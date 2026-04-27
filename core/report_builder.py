from collections import OrderedDict
"""
Report building utilities for DailyDigest.

Handles section cleanup, Markdown manipulation, merged and unified
report construction, and category/tier conversion from sub-agent summaries.
Also provides the new magazine-style report builder.
"""

import os
import re
from datetime import datetime, timezone

from .logging_config import get_logger
from .config import OUTPUT_DIR, CATEGORY_ORDER, get_category_display, normalize_category
from .llm_utils import contains_reasoning_artifacts, sanitize_report_markdown
from .llm_services import render_briefing as _render_briefing

logger = get_logger("report_builder")

_THEME_ORDER = [
    "模型与平台",
    "研究与方法",
    "开源与工具",
    "硬件与基础设施",
    "产品与应用",
    "评测与实战",
    "行业与商业",
]


def demote_headings(lines, levels):
    """Add # prefix to heading lines to demote them by the given number of levels.

    Also normalizes # heading (h1) to h3 within demoted content, since AI-generated
    text may contain raw h1 headings that should not appear at the top level.
    """
    result = []
    for line in lines:
        match = re.match(r'^(#{1,6})\s', line)
        if match:
            hashes = match.group(1)
            new_level = min(len(hashes) + levels, 6)
            result.append('#' * new_level + line[len(hashes):])
        else:
            result.append(line)
    return result


def make_anchor(heading_text):
    """Generate a GitHub-compatible anchor from heading text."""
    text = re.sub(r'[\U00010000-\U0010ffff]', '', heading_text)
    text = re.sub(r'[^\w一-鿿\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text).strip().lower()
    return text


def strip_section_header_footer(content: str, demote_heading_levels: int = 0) -> str:
    """Strip title/header lines and footer lines from a report section.

    Args:
        content: Markdown section content
        demote_heading_levels: number of # levels to add (e.g. 2 turns # into ###)
    """
    lines = content.split("\n")
    start = 0
    found_first_sep = False
    for i, line in enumerate(lines):
        if line.strip() == "---":
            start = i + 1
            found_first_sep = True
            break
        start = i + 1
    if not found_first_sep:
        start = 0
        while start < len(lines) and (
            lines[start].startswith("# ")
            or lines[start].strip() == ""
            or lines[start].startswith(">")
        ):
            start += 1
    end = len(lines)
    while end > start and (
        lines[end - 1].strip() == ""
        or "生成时间" in lines[end - 1]
        or "Generated" in lines[end - 1]
        or lines[end - 1].strip() == "---"
        or (lines[end - 1].strip().startswith("*") and "UTC" in lines[end - 1])
    ):
        end -= 1

    result_lines = lines[start:end]
    if demote_heading_levels > 0:
        result_lines = demote_headings(result_lines, demote_heading_levels)

    return "\n".join(result_lines).strip()


def build_merged_report(sections, now, language="zh"):
    """Merge multiple sections into a single report with header and TOC."""
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    section_names = []
    for section in sections:
        for line in section.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                section_names.append(stripped.lstrip("#").strip())
                break

    if language == "zh":
        header = f"# 📰 Daily Digest — {date_str}\n\n"
        header += f"> 📡 {' · '.join(section_names)}\n\n"
        header += f"> 🕐 生成时间 {time_str} UTC\n"
    else:
        header = f"# 📰 Daily Digest — {date_str}\n\n"
        header += f"> 📡 {' · '.join(section_names)}\n\n"
        header += f"> 🕐 Generated at {time_str} UTC\n"

    header += "\n---\n\n"

    cleaned_sections = []
    all_headings = []
    for i, section in enumerate(sections):
        name = section_names[i] if i < len(section_names) else f"Section {i+1}"
        cleaned = strip_section_header_footer(section, demote_heading_levels=3)
        if not cleaned:
            continue

        section_heading = f"## {name}"
        anchor = make_anchor(name)
        all_headings.append((name, anchor))

        cleaned_sections.append(f"{section_heading}\n\n{cleaned}")

    toc_label = "## 📑 目录" if language == "zh" else "## 📑 Table of Contents"
    toc_lines = [toc_label, ""]
    for heading_text, anchor in all_headings:
        toc_lines.append(f"- [{heading_text}](#{anchor})")
    toc = "\n".join(toc_lines) + "\n"

    merged = header + toc + "\n---\n\n" + "\n\n---\n\n".join(cleaned_sections)
    merged = re.sub(r'(\n---\n\s*){2,}', '\n---\n', merged)
    return merged


def _merge_llm_summaries(editorial_results, llm_results):
    """Merge LLM-generated category summaries into editorial-tiered category results.

    Adds the LLM summary as 'category_summary' in each category's tiered dict,
    which is rendered as a blockquote by _render_tiered_category().
    """
    merged = {}
    for cat, data in editorial_results.items():
        merged_data = dict(data)
        tiered = dict(data.get("tiered", {}))
        # Find matching LLM summary
        llm_data = llm_results.get(cat)
        if llm_data and llm_data.get("summary"):
            tiered["category_summary"] = llm_data["summary"]
        merged_data["tiered"] = tiered
        merged[cat] = merged_data
    return merged


def _build_highlights(ai_articles, non_ai_articles, cluster_map, language="zh"):
    """Build a highlights section from top must-read articles.

    Picks the top 3-5 most important articles across both AI and non-AI,
    based on editorial tier and news value score.
    """
    all_articles = list(ai_articles) + list(non_ai_articles)
    if not all_articles:
        return ""

    # Sort by editorial importance: must_read first, then by news_value_score
    must_read = [a for a in all_articles if a.extra.get("editorial_tier") == "must_read"]
    noteworthy = [a for a in all_articles if a.extra.get("editorial_tier") == "noteworthy"]

    # Sort each tier by score descending
    must_read.sort(key=lambda a: a.extra.get("news_value_score", 0), reverse=True)
    noteworthy.sort(key=lambda a: a.extra.get("news_value_score", 0), reverse=True)

    # Take up to 5 from must_read, fill remainder from noteworthy
    highlights = must_read[:5]
    if len(highlights) < 5:
        highlights.extend(noteworthy[:5 - len(highlights)])

    if not highlights:
        return ""

    if language == "zh":
        section = "### 🔥 今日要点\n\n"
        for i, a in enumerate(highlights, 1):
            title = a.title.replace("|", "\\|").replace("\n", " ")
            url = a.url.replace("|", "\\|")
            source = a.source.replace("|", "\\|")
            desc = _clean_description_for_display(a.description, max_len=100)
            if desc and not _is_language_compatible(desc, language):
                desc = ""
            engagement = ""
            if a.hn_points and a.hn_points >= 50:
                engagement = f" (🔥HN {a.hn_points})"
            section += f"**{i}. [{title}]({url})** — *{source}*{engagement}\n"
            if desc:
                section += f"> {desc}\n"
            section += "\n"
    else:
        section = "### 🔥 Today's Highlights\n\n"
        for i, a in enumerate(highlights, 1):
            title = a.title.replace("|", "\\|").replace("\n", " ")
            url = a.url.replace("|", "\\|")
            source = a.source.replace("|", "\\|")
            desc = _clean_description_for_display(a.description, max_len=100)
            engagement = ""
            if a.hn_points and a.hn_points >= 50:
                engagement = f" (🔥HN {a.hn_points})"
            section += f"**{i}. [{title}]({url})** — *{source}*{engagement}\n"
            if desc:
                section += f"> {desc}\n"
            section += "\n"

    section += "---\n\n"
    return section


def _build_data_dashboard(ai_articles, non_ai_articles, cluster_map, language="zh"):
    """Build a data overview dashboard showing scan statistics.

    Produces a compact table with total articles, AI vs non-AI split,
    topic clusters, and source counts — similar to linux.do's data overview.
    """
    cluster_map = cluster_map or {}
    total_ai = len(ai_articles)
    total_non_ai = len(non_ai_articles)
    total = total_ai + total_non_ai

    if total == 0:
        return ""

    # Count multi-article clusters
    multi_clusters = sum(
        1 for c in cluster_map.values()
        if isinstance(c, dict) and c.get("cluster_size", 1) > 1
    )
    cross_source_clusters = sum(
        1 for c in cluster_map.values()
        if isinstance(c, dict) and c.get("cross_source", False)
    )

    # Count unique sources
    all_sources = set()
    for a in ai_articles + non_ai_articles:
        if a.source:
            all_sources.add(a.source)

    # Count editorial tiers (if available)
    must_read = sum(1 for a in ai_articles if a.extra.get("editorial_tier") == "must_read")
    noteworthy = sum(1 for a in ai_articles if a.extra.get("editorial_tier") == "noteworthy")

    if language == "zh":
        dash = "## 📊 数据概览\n\n"
        dash += f"| 指标 | 数值 |\n"
        dash += f"|------|------|\n"
        dash += f"| 扫描文章总数 | {total} |\n"
        dash += f"| 🤖 AI 相关 | {total_ai} ({total_ai * 100 // max(total, 1)}%) |\n"
        dash += f"| 💻 科技动态 | {total_non_ai} ({total_non_ai * 100 // max(total, 1)}%) |\n"
        dash += f"| 信息源数量 | {len(all_sources)} |\n"
        if cluster_map:
            dash += f"| 话题聚类 | {multi_clusters} 个多源话题 |\n"
            dash += f"| 跨源验证 | {cross_source_clusters} 个话题 |\n"
        if must_read or noteworthy:
            dash += f"| 🔴 必读 | {must_read} |\n"
            dash += f"| 🟡 值得关注 | {noteworthy} |\n"
    else:
        dash = "## 📊 Data Overview\n\n"
        dash += f"| Metric | Value |\n"
        dash += f"|--------|-------|\n"
        dash += f"| Total articles scanned | {total} |\n"
        dash += f"| 🤖 AI-related | {total_ai} ({total_ai * 100 // max(total, 1)}%) |\n"
        dash += f"| 💻 Tech updates | {total_non_ai} ({total_non_ai * 100 // max(total, 1)}%) |\n"
        dash += f"| Sources covered | {len(all_sources)} |\n"
        if cluster_map:
            dash += f"| Topic clusters | {multi_clusters} multi-source topics |\n"
            dash += f"| Cross-verified | {cross_source_clusters} topics |\n"
        if must_read or noteworthy:
            dash += f"| 🔴 Must read | {must_read} |\n"
            dash += f"| 🟡 Noteworthy | {noteworthy} |\n"

    dash += "\n---\n\n"
    return dash


def _article_rank(article, cluster_map=None):
    """Sort higher-signal articles first for briefing selection."""
    cluster_info = (cluster_map or {}).get(article.url, {})
    tier_order = {"must_read": 3, "noteworthy": 2, "brief": 1}
    return (
        tier_order.get(article.extra.get("editorial_tier"), 0),
        article.extra.get("news_value_score", 0),
        cluster_info.get("cluster_size", 1),
        1 if cluster_info.get("cross_source") else 0,
        article.hn_points or 0,
    )


def _assign_briefing_theme(article):
    """Map an article to a human-readable briefing theme."""
    from .config import normalize_category

    category = normalize_category(article.category)
    title = (article.title or "").lower()

    if category in {"ai_ml"}:
        if any(kw in title for kw in ("paper", "research", "study", "arxiv", "论文", "研究")):
            return "研究与方法"
        return "模型与平台"
    if category in {"ai_tools", "open_source", "wechat_dev"}:
        return "开源与工具"
    if category in {"chips_hardware", "cloud"}:
        return "硬件与基础设施"
    if category in {"tech_product", "wechat_user"}:
        return "产品与应用"
    if category in {"hacker_news", "podcast"}:
        return "评测与实战"
    if category in {"tech_general", "general_news", "wechat_other", "wechat_security"}:
        if any(kw in title for kw in ("benchmark", "test", "review", "评测", "实测", "横评")):
            return "评测与实战"
        return "行业与商业"
    return "行业与商业"


def _theme_sort_key(theme):
    theme_order = {name: idx for idx, name in enumerate(_THEME_ORDER)}
    return (
        -theme.get("score", 0),
        theme_order.get(theme.get("theme", ""), 99),
        theme.get("title", ""),
    )


def _clean_theme_title(text, fallback, language="zh"):
    cleaned = re.sub(r'\s+', ' ', (text or "")).strip()
    cleaned = cleaned.strip("-:;,| ")
    cleaned = cleaned[:72] if cleaned else ""
    if cleaned and language == "zh" and not _is_language_compatible(cleaned, "zh"):
        return fallback
    return cleaned or fallback


_BOILERPLATE_PATTERNS = [
    re.compile(r'（本文作者为[^）]*）\s*', re.MULTILINE),
    re.compile(r'^\s*文\s*[|｜]\s*[^，。\n]{0,30}\n?', re.MULTILINE),
    re.compile(r'文\s*[|｜]\s*[^，。\n]{0,30}，\s*作者\s*[|｜]\s*[^，。\n]{0,30}\n?'),
    re.compile(r'编辑\s*[|｜]\s*[^，。\n]{0,30}\n?', re.MULTILINE),
    re.compile(r'作者\s*[|｜]\s*[^，。\n]{0,30}\n?', re.MULTILINE),
]


def _clean_description_for_display(description, max_len=180):
    """Strip byline boilerplate and HTML from article description for display."""
    if not description:
        return ""
    text = re.sub(r'<[^>]+>', '', description).strip()
    for pattern in _BOILERPLATE_PATTERNS:
        text = pattern.sub('', text)
    text = re.sub(r'\n{2,}', '\n', text).strip()
    # Remove leading whitespace/newlines left after boilerplate removal
    text = text.lstrip('\n\r\t ')
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "..."
    return text


def _is_language_compatible(text, language):
    """Check whether text is compatible with the target report language.

    For zh reports, prefer content with meaningful CJK presence.
    For non-zh reports, keep original behavior.
    """
    if not text:
        return False

    cleaned = text.strip()
    if not cleaned:
        return False

    if language != "zh":
        return True

    cjk_count = len(re.findall(r'[一-鿿]', cleaned))
    latin_count = len(re.findall(r'[A-Za-z]', cleaned))

    if cjk_count == 0:
        return False
    if latin_count == 0:
        return True

    return cjk_count >= 4 or cjk_count >= latin_count


def _compose_theme_summary(theme, summary_map=None, language="zh"):
    """Fallback theme summary from article descriptions and summary_map."""
    parts = []
    seen = set()
    for article in theme.get("articles", []):
        info = (summary_map or {}).get(article.url, {})
        summary = ""
        if isinstance(info, dict):
            summary = info.get("importance_reason") or info.get("ai_summary") or ""
        elif isinstance(info, str):
            summary = info
        if not summary:
            summary = _clean_description_for_display(article.description, max_len=120)
        if not summary:
            continue
        summary = summary.replace("\n", " ").strip()
        if not _is_language_compatible(summary, language):
            continue
        if summary and summary not in seen:
            seen.add(summary)
            parts.append(summary[:120])
        if len(parts) >= 3:
            break

    if parts:
        joiner = "；" if language == "zh" else " "
        joined = joiner.join(parts)
        # Cap total summary length to keep themes scannable
        if len(joined) > 300:
            joined = joined[:300].rstrip() + "..."
        return joined

    if language == "zh":
        return "今日该主题有多篇相关更新，需结合参考条目快速浏览。"
    return "This theme collected multiple relevant updates for quick scanning."


def _fallback_trends(themes, language="zh", brief_items=None):
    """Generate heuristic trend bullets from theme + brief data.

    Goes beyond simple “N articles” counts by surfacing source distribution,
    HN heat signals, and cross-source convergence.
    """
    if not themes:
        return []

    trends = []
    all_articles = [a for t in themes for a in t.get("articles", [])]
    all_articles += list(brief_items or [])

    # 1. Source distribution insight
    sources = {}
    for a in all_articles:
        if a.source:
            sources[a.source] = sources.get(a.source, 0) + 1
    if sources:
        top_sources = sorted(sources.items(), key=lambda x: -x[1])[:3]
        top_str = "、".join(f"{s}({n})" for s, n in top_sources)
        if language == "zh":
            trends.append(f"今日信息源分布：{top_str} 等 {len(sources)} 个来源贡献了内容。")
        else:
            top_str_en = ", ".join(f"{s} ({n})" for s, n in top_sources)
            trends.append(f"Source distribution: {top_str_en} among {len(sources)} sources.")

    # 2. Cross-source convergence
    cross_themes = [t for t in themes if t.get("cross_source")]
    if cross_themes:
        names = "、".join(t.get("title", "") for t in cross_themes[:2])
        if language == "zh":
            trends.append(f"多源交叉验证：{names}，值得关注后续发展。")
        else:
            trends.append(f"Cross-source convergence: {', '.join(t.get('title', '') for t in cross_themes[:2])}.")

    # 3. HN heat signal
    hn_hot = [a for a in all_articles if (a.hn_points or 0) >= 100]
    if hn_hot:
        top_hn = max(hn_hot, key=lambda a: a.hn_points or 0)
        if language == "zh":
            trends.append(f"HN 热议：「{top_hn.title}」获 {top_hn.hn_points} 赞。")
        else:
            trends.append(f"HN trending: \"{top_hn.title}\" with {top_hn.hn_points} points.")

    return trends[:3]


def _fallback_highlights(ai_articles, non_ai_articles, cluster_map=None, language="zh"):
    """Build 4-6 concise highlight lines when no LLM highlights are available.

    Uses article titles (always clean) rather than descriptions
    (which often contain byline boilerplate).
    """
    highlights = []
    selected = sorted(ai_articles + non_ai_articles, key=lambda a: _article_rank(a, cluster_map), reverse=True)[:6]
    for article in selected:
        cluster_info = (cluster_map or {}).get(article.url, {})
        # Prefer title — descriptions often have byline noise
        line = article.title
        if cluster_info.get("cross_source"):
            line += "（多源交叉验证）" if language == "zh" else " (cross-source)"
        highlights.append(line)
    return highlights


def _select_brief_items(non_ai_articles, max_count=20):
    """Select compact tech brief items, preferring higher editorial weight.

    Filters out articles from non-tech categories (e.g. general_news,
    podcast) to keep the brief section focused on technology.
    """
    if not non_ai_articles:
        return []

    from .config import normalize_category
    _TECH_CATEGORIES = {
        "ai_ml", "ai_tools", "tech_general", "tech_product",
        "chips_hardware", "cloud", "open_source", "cybersecurity",
        "hacker_news", "wechat_dev",
    }
    filtered = [a for a in non_ai_articles
                if normalize_category(a.category) in _TECH_CATEGORIES]
    if not filtered:
        filtered = non_ai_articles  # fallback: keep all if nothing passes

    must_read = [a for a in filtered if a.extra.get("editorial_tier") == "must_read"]
    noteworthy = [a for a in filtered if a.extra.get("editorial_tier") == "noteworthy"]
    brief = [a for a in filtered if a.extra.get("editorial_tier") == "brief"]
    unclassified = [a for a in filtered if not a.extra.get("editorial_tier")]

    for bucket in (must_read, noteworthy, brief, unclassified):
        bucket.sort(key=lambda a: a.extra.get("news_value_score", 0), reverse=True)

    selected = []
    for bucket in (must_read, noteworthy, unclassified, brief):
        remaining = max_count - len(selected)
        if remaining <= 0:
            break
        selected.extend(bucket[:remaining])
    return selected


def _build_theme_groups(ai_articles, cluster_map=None, language="zh"):
    """Group AI articles into cluster-first theme sections."""
    cluster_map = cluster_map or {}
    articles_by_url = {article.url: article for article in ai_articles}
    cluster_groups = {}
    for article in ai_articles:
        info = cluster_map.get(article.url, {})
        cluster_id = info.get("cluster_id")
        if cluster_id and info.get("cluster_size", 1) > 1:
            cluster_groups.setdefault(cluster_id, []).append(article)

    themes = []
    used_urls = set()
    for cluster_id, members in cluster_groups.items():
        members.sort(key=lambda a: _article_rank(a, cluster_map), reverse=True)
        lead = members[0]
        info = cluster_map.get(lead.url, {})
        theme_fallback = _assign_briefing_theme(lead)
        title = _clean_theme_title(lead.title, theme_fallback, language=language)
        # Double-check: ensure Chinese reports never show English-only titles
        if language == "zh" and not _is_language_compatible(title, "zh"):
            title = theme_fallback
        themes.append({
            "id": cluster_id,
            "theme": theme_fallback,
            "title": title,
            "articles": members[:4],
            "score": max(a.extra.get("news_value_score", 0) for a in members),
            "cluster_theme": info.get("theme", ""),
            "cross_source": info.get("cross_source", False),
        })
        used_urls.update(a.url for a in members)

    leftovers = {}
    for article in sorted(ai_articles, key=lambda a: _article_rank(a, cluster_map), reverse=True):
        if article.url in used_urls:
            continue
        theme = _assign_briefing_theme(article)
        leftovers.setdefault(theme, []).append(article)

    for theme_name, members in leftovers.items():
        if not members:
            continue
        themes.append({
            "id": f"theme-{theme_name}",
            "theme": theme_name,
            "title": theme_name,
            "articles": members[:4],
            "score": max(a.extra.get("news_value_score", 0) for a in members),
            "cluster_theme": "",
            "cross_source": False,
        })

    themes.sort(key=_theme_sort_key)
    return themes[:8]


def _combine_briefing_stats(ai_articles, non_ai_articles, stats=None, cluster_map=None):
    """Normalize top-level report statistics."""
    stats = dict(stats or {})
    total_included = len(ai_articles) + len(non_ai_articles)
    stats.setdefault("included_count", total_included)
    stats.setdefault("after_editorial", total_included)
    stats.setdefault("after_dedup", total_included)
    stats.setdefault("candidate_count", total_included)
    stats.setdefault("source_count", len({a.source for a in ai_articles + non_ai_articles if a.source}))
    if cluster_map:
        stats.setdefault("cluster_count", len({info.get("cluster_id") for info in cluster_map.values() if info.get("cluster_id")}))
        stats.setdefault("cross_source_count", len({info.get("cluster_id") for info in cluster_map.values() if info.get("cross_source")}))
    else:
        stats.setdefault("cluster_count", 0)
        stats.setdefault("cross_source_count", 0)
    stats["ai_count"] = len(ai_articles)
    stats["non_ai_count"] = len(non_ai_articles)
    return stats


def _select_featured_tech(non_ai_articles, max_count=5):
    """Select non-AI must_read articles for a featured tech news section."""
    must_read = [a for a in non_ai_articles if a.extra.get("editorial_tier") == "must_read"]
    must_read.sort(key=lambda a: a.extra.get("news_value_score", 0), reverse=True)
    return must_read[:max_count]


def build_briefing_data(ai_articles, non_ai_articles, cluster_map=None, summary_map=None,
                        stats=None, language="zh"):
    """Build the neutral briefing-data contract shared by markdown and wechat."""
    cluster_map = cluster_map or {}
    themes = _build_theme_groups(ai_articles, cluster_map=cluster_map, language=language)
    for theme in themes:
        theme["summary"] = _compose_theme_summary(theme, summary_map=summary_map, language=language)
    brief_items = _select_brief_items(non_ai_articles, 20)
    return {
        "highlights": _fallback_highlights(ai_articles, non_ai_articles, cluster_map=cluster_map, language=language),
        "themes": themes[:8],
        "featured_tech": _select_featured_tech(non_ai_articles),
        "brief_items": brief_items,
        "stats": _combine_briefing_stats(ai_articles, non_ai_articles, stats=stats, cluster_map=cluster_map),
        "trends": _fallback_trends(themes[:8], language=language, brief_items=brief_items),
    }


def _merge_llm_briefing(briefing_data, llm_briefing, language="zh"):
    """Overlay LLM-generated highlights and theme summaries onto briefing_data."""
    if not llm_briefing:
        return briefing_data

    merged = dict(briefing_data)
    if llm_briefing.get("highlights"):
        filtered_highlights = [
            item.strip() for item in llm_briefing["highlights"]
            if isinstance(item, str) and _is_language_compatible(item, language)
        ]
        if filtered_highlights:
            merged["highlights"] = filtered_highlights

    theme_summaries = llm_briefing.get("theme_summaries", {})
    if theme_summaries:
        themes = []
        for idx, theme in enumerate(briefing_data.get("themes", []), 1):
            summary = theme_summaries.get(theme.get("id")) or theme_summaries.get(str(idx))
            if summary and _is_language_compatible(summary, language):
                updated = dict(theme)
                updated["summary"] = summary
                themes.append(updated)
            else:
                themes.append(theme)
        merged["themes"] = themes

    if llm_briefing.get("trends"):
        filtered_trends = [
            item.strip() for item in llm_briefing["trends"]
            if isinstance(item, str) and _is_language_compatible(item, language)
        ]
        if filtered_trends:
            merged["trends"] = filtered_trends
    return merged


def _render_briefing_markdown(briefing_data, now, language="zh"):
    """Render briefing_data into the default markdown daily digest."""
    date_str = now.strftime("%Y-%m-%d")
    stats = briefing_data.get("stats", {})
    highlights = briefing_data.get("highlights", [])[:6]
    themes = briefing_data.get("themes", [])[:8]
    featured_tech = briefing_data.get("featured_tech", [])
    brief_items = briefing_data.get("brief_items", [])[:20]
    trends = briefing_data.get("trends", [])

    if language == "zh":
        lines = [
            f"# 📰 DailyDigest — {date_str}",
            "",
            f"> 扫描 {stats.get('candidate_count', 0)} 篇候选内容 · 覆盖 {stats.get('source_count', 0)} 个信息源 · 纳入 {stats.get('included_count', 0)} 篇",
            "",
            "---",
            "",
        ]
        if highlights:
            lines.extend(["## 📌 今日要点", ""])
            for item in highlights:
                lines.append(f"- {item}")
            lines.extend(["", "---", ""])

        if themes:
            lines.extend(["## 🧭 今日动态", ""])
            numerals = ["一", "二", "三", "四", "五", "六", "七", "八"]
            for idx, theme in enumerate(themes, 1):
                prefix = numerals[idx - 1] if idx - 1 < len(numerals) else str(idx)
                lines.append(f"### {prefix}、{theme.get('title', '')}")
                lines.append("")
                lines.append(theme.get("summary", ""))
                lines.append("")
                lines.append("**参考：**")
                lines.append("")
                for article in theme.get("articles", [])[:4]:
                    source = f" — *{article.source}*" if article.source else ""
                    heat = ""
                    if article.hn_points:
                        heat = f" · HN {article.hn_points}"
                    lines.append(f"- [{article.title}]({article.url}){source}{heat}")
                lines.append("")
                if idx < len(themes):
                    lines.extend(["---", ""])

        if featured_tech:
            lines.extend(["## ⭐ 重点科技新闻", ""])
            for article in featured_tech:
                source = f" — *{article.source}*" if article.source else ""
                desc = _clean_description_for_display(article.description, max_len=100)
                lines.append(f"- **[{article.title}]({article.url})**{source}")
                if desc and _is_language_compatible(desc, language):
                    lines.append(f"  > {desc}")
                lines.append("")
            lines.extend(["---", ""])

        if brief_items:
            lines.extend(["## 📝 科技简讯", ""])
            for article in brief_items:
                source = f" — *{article.source}*" if article.source else ""
                lines.append(f"- [{article.title}]({article.url}){source}")
            lines.extend(["", "---", ""])

        if trends:
            lines.extend(["## 📈 趋势观察", ""])
            for idx, trend in enumerate(trends, 1):
                lines.append(f"{idx}. {trend}")
            lines.extend(["", "---", ""])

        lines.extend([
            "## 📊 数据概览",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 候选内容 | {stats.get('candidate_count', 0)} |",
            f"| 去重后 | {stats.get('after_dedup', 0)} |",
            f"| 纳入日报 | {stats.get('included_count', 0)} |",
            f"| AI 主题 | {stats.get('ai_count', 0)} |",
            f"| 科技简讯 | {len(brief_items)} |",
            f"| 信息源数量 | {stats.get('source_count', 0)} |",
        ])
        if stats.get("cluster_count"):
            lines.append(f"| 主题聚类 | {stats.get('cluster_count', 0)} |")
            lines.append(f"| 跨源话题 | {stats.get('cross_source_count', 0)} |")
        return "\n".join(lines).strip()

    lines = [
        f"# 📰 DailyDigest — {date_str}",
        "",
        f"> Scanned {stats.get('candidate_count', 0)} candidate items · Covered {stats.get('source_count', 0)} sources · Included {stats.get('included_count', 0)} items",
        "",
        "---",
        "",
    ]
    if highlights:
        lines.extend(["## 📌 Highlights", ""])
        for item in highlights:
            lines.append(f"- {item}")
        lines.extend(["", "---", ""])
    if themes:
        lines.extend(["## 🧭 New Developments", ""])
        for idx, theme in enumerate(themes, 1):
            lines.append(f"### {idx}. {theme.get('title', '')}")
            lines.append("")
            lines.append(theme.get("summary", ""))
            lines.append("")
            lines.append("**References:**")
            lines.append("")
            for article in theme.get("articles", [])[:4]:
                source = f" — *{article.source}*" if article.source else ""
                lines.append(f"- [{article.title}]({article.url}){source}")
            lines.append("")
            if idx < len(themes):
                lines.extend(["---", ""])
    if featured_tech:
        lines.extend(["## ⭐ Featured Tech News", ""])
        for article in featured_tech:
            source = f" — *{article.source}*" if article.source else ""
            desc = _clean_description_for_display(article.description, max_len=100)
            lines.append(f"- **[{article.title}]({article.url})**{source}")
            if desc:
                lines.append(f"  > {desc}")
            lines.append("")
        lines.extend(["---", ""])
    if brief_items:
        lines.extend(["## 📝 Tech Briefs", ""])
        for article in brief_items:
            source = f" — *{article.source}*" if article.source else ""
            lines.append(f"- [{article.title}]({article.url}){source}")
        lines.extend(["", "---", ""])
    if trends:
        lines.extend(["## 📈 Trend Notes", ""])
        for idx, trend in enumerate(trends, 1):
            lines.append(f"{idx}. {trend}")
        lines.extend(["", "---", ""])
    lines.extend([
        "## 📊 Data Overview",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Candidate items | {stats.get('candidate_count', 0)} |",
        f"| After dedup | {stats.get('after_dedup', 0)} |",
        f"| Included | {stats.get('included_count', 0)} |",
        f"| AI themes | {stats.get('ai_count', 0)} |",
        f"| Tech briefs | {len(brief_items)} |",
        f"| Sources covered | {stats.get('source_count', 0)} |",
    ])
    if stats.get("cluster_count"):
        lines.append(f"| Topic clusters | {stats.get('cluster_count', 0)} |")
        lines.append(f"| Cross-source topics | {stats.get('cross_source_count', 0)} |")
    return "\n".join(lines).strip()


def build_unified_report(ai_articles, non_ai_articles, now, language="zh",
                         summary_map=None, cluster_map=None,
                         executive_summary="", trend_insights="",
                         stats=None, llm_briefing=None):
    """Build the default markdown daily digest in briefing format."""
    briefing_data = build_briefing_data(
        ai_articles,
        non_ai_articles,
        cluster_map=cluster_map,
        summary_map=summary_map,
        stats=stats,
        language=language,
    )

    if executive_summary:
        highlights = [line.lstrip("- ").strip() for line in executive_summary.splitlines() if line.strip()]
        if highlights:
            briefing_data["highlights"] = highlights[:6]
    if trend_insights:
        briefing_data["trends"] = [line.strip() for line in trend_insights.splitlines() if line.strip()]

    if os.environ.get("API_KEY") and ai_articles and llm_briefing is None:
        try:
            llm_briefing = _render_briefing(briefing_data, language=language)
        except Exception as e:
            logger.warning(f"⚠️ Briefing narrative generation failed (non-fatal): {e}")

    briefing_data = _merge_llm_briefing(briefing_data, llm_briefing, language=language)
    return _render_briefing_markdown(briefing_data, now, language=language)


def build_unified_wechat_report(ai_articles, non_ai_articles, now, language="zh",
                                 summary_map=None, cluster_map=None,
                                 category_results=None, stats=None, llm_briefing=None):
    """Build a WeChat Official Account Markdown article."""
    from .wechat_article import generate_wechat_article

    briefing_data = build_briefing_data(
        ai_articles,
        non_ai_articles,
        cluster_map=cluster_map,
        summary_map=summary_map,
        stats=stats,
        language=language,
    )

    if os.environ.get("API_KEY") and ai_articles and llm_briefing is None:
        try:
            llm_briefing = _render_briefing(briefing_data, language=language)
        except Exception as e:
            logger.warning(f"[WeChat] ⚠️ Briefing narrative generation failed, using fallback: {e}")

    return generate_wechat_article(
        ai_articles=ai_articles,
        non_ai_articles=non_ai_articles,
        now=now,
        language=language,
        category_results=category_results,
        summary_map=summary_map,
        cluster_map=cluster_map,
        briefing_data=_merge_llm_briefing(briefing_data, llm_briefing, language=language),
    )


def build_category_results_from_summaries(updates, summary_map):
    """Convert flat sub-agent summary_map into category_results for tiered rendering."""
    from .config import normalize_category, get_category_display, CATEGORIES

    cat_articles = {}
    cat_display_names = {}

    valid_cats = set(CATEGORIES.keys())

    for article in updates:
        info = summary_map.get(article.url, {})
        ai_cat = info.get("category", "")
        if ai_cat:
            final_cat = normalize_category(ai_cat)
            if final_cat == ai_cat and ai_cat not in valid_cats:
                final_cat = ai_cat
                cat_display_names.setdefault(final_cat, ai_cat)
            else:
                cat_display_names.setdefault(final_cat, get_category_display(final_cat))
        else:
            final_cat = normalize_category(article.category)
            cat_display_names.setdefault(final_cat, get_category_display(final_cat))

        cat_articles.setdefault(final_cat, []).append(article)

    category_results = {}
    for cat, articles in cat_articles.items():
        must_read = []
        noteworthy = []
        brief = []
        for i, article in enumerate(articles, 1):
            info = summary_map.get(article.url, {})
            tier = info.get("tier", "noteworthy")
            reason = info.get("importance_reason", "")
            if tier == "must_read":
                must_read.append({"index": i, "summary": reason or info.get("ai_summary", "")})
            elif tier == "brief":
                brief.append(i)
            else:
                noteworthy.append({"index": i, "summary": reason or info.get("ai_summary", "")})

        tiered = {
            "must_read": must_read,
            "noteworthy": noteworthy,
            "brief": brief,
        }
        category_results[cat] = {
            "name": cat_display_names.get(cat, cat),
            "articles": articles,
            "tiered": tiered,
            "article_count": len(articles),
        }

    return category_results


def build_category_results_from_editorial(ai_articles, cluster_map=None, language="zh"):
    """Build category_results from editorial tier data on articles.

    Used by API mode when editorial pipeline has annotated articles
    with editorial_tier (must_read/noteworthy/brief) in article.extra.
    Produces the same structure as build_category_results_from_summaries
    so the existing _render_tiered_category renderer can be reused.
    """
    from .config import normalize_category, get_category_display, CATEGORIES

    cat_articles = {}
    cat_display_names = {}

    valid_cats = set(CATEGORIES.keys())

    for article in ai_articles:
        final_cat = normalize_category(article.category)
        cat_display_names.setdefault(final_cat, get_category_display(final_cat))
        cat_articles.setdefault(final_cat, []).append(article)

    category_results = {}
    for cat, articles in cat_articles.items():
        # Sort by editorial score descending within each category
        articles.sort(key=lambda a: a.extra.get("news_value_score", 0), reverse=True)

        must_read = []
        noteworthy = []
        brief = []
        for i, article in enumerate(articles, 1):
            tier = article.extra.get("editorial_tier", "noteworthy")
            if tier == "must_read":
                reason = _generate_importance_reason(article, cluster_map, language)
                must_read.append({"index": i, "summary": reason})
            elif tier == "brief":
                brief.append(i)
            else:
                reason = _generate_importance_reason(article, cluster_map, language)
                noteworthy.append({"index": i, "summary": reason})

        tiered = {
            "must_read": must_read,
            "noteworthy": noteworthy,
            "brief": brief,
        }
        category_results[cat] = {
            "name": cat_display_names.get(cat, cat),
            "articles": articles,
            "tiered": tiered,
            "article_count": len(articles),
        }

    return category_results


def _generate_importance_reason(article, cluster_map=None, language="zh"):
    """Generate a brief importance reason from article metadata."""
    parts = []
    cluster_info = (cluster_map or {}).get(article.url, {})
    cluster_size = cluster_info.get("cluster_size", 1)
    cross_source = cluster_info.get("cross_source", False)

    if language == "zh":
        if cluster_size >= 3:
            parts.append(f"{cluster_size}篇相关报道")
        if cross_source:
            parts.append("多源验证")
        if article.hn_points and article.hn_points >= 100:
            parts.append(f"HN {article.hn_points}赞")
        if not parts:
            # Use article description as the primary importance signal
            if article.description:
                desc = _clean_description_for_display(article.description, max_len=80)
                if desc:
                    parts.append(desc)
            if not parts:
                parts.append("值得关注")
        return "，".join(parts)
    else:
        if cluster_size >= 3:
            parts.append(f"{cluster_size} related reports")
        if cross_source:
            parts.append("cross-source verification")
        if article.hn_points and article.hn_points >= 100:
            parts.append(f"HN {article.hn_points} pts")
        if not parts:
            if article.description:
                desc = _clean_description_for_display(article.description, max_len=80)
                if desc:
                    parts.append(desc)
        if not parts:
            parts.append("noteworthy")
        return ", ".join(parts)


def classify_from_summaries(updates, summary_map):
    """Classify articles as AI vs non-AI using sub-agent category data.

    Uses config.py's AI keyword lists instead of a hardcoded duplicate.
    """
    from .config import normalize_category, AI_KEYWORDS_ZH, AI_KEYWORDS_EN

    ai_cats = {"ai_ml", "ai_tools"}
    # Build keyword set from config (single source of truth)
    ai_keywords = tuple(set(kw.lower() for kw in AI_KEYWORDS_ZH + AI_KEYWORDS_EN
                            if len(kw) <= 10))  # Short keywords only for substring match

    ai_articles = []
    non_ai_articles = []
    for article in updates:
        info = summary_map.get(article.url, {})
        ai_cat = info.get("category", "")
        if ai_cat:
            final_cat = normalize_category(ai_cat)
            if final_cat in ai_cats:
                ai_articles.append(article)
            elif any(kw in ai_cat.lower() for kw in ai_keywords):
                ai_articles.append(article)
            else:
                non_ai_articles.append(article)
        else:
            final_cat = normalize_category(article.category)
            if final_cat in ai_cats:
                ai_articles.append(article)
            else:
                non_ai_articles.append(article)
    return ai_articles, non_ai_articles



def _escape_pipe(text):
    """Escape pipe characters for use in Markdown tables."""
    return text.replace("|", "\\|").replace("\n", " ")


def _article_table_row(index, title, url, source, summary=""):
    """Build a single article row for a Markdown table."""
    title_cell = f"[**{_escape_pipe(title)}**]({_escape_pipe(url)})"
    source_cell = f"*{_escape_pipe(source)}*"
    if summary:
        summary_cell = _escape_pipe(summary)
        return f"| {index} | {title_cell} | {source_cell} | {summary_cell} |"
    return f"| {index} | {title_cell} | {source_cell} |"


def _render_hn_table(hn_items, report_language, count_unit, summary_map=None):
    """Render a Hacker News trending section as Markdown table.

    Args:
        hn_items: list of Article objects with hn_points/hn_comments in extra
        report_language: "zh" or "en"
        count_unit: unit word for count (e.g. "条" or "items")
        summary_map: optional dict mapping url -> {"ai_summary": str}; if
                     provided, a Summary column is added.

    Returns:
        list[str] of Markdown lines (empty list if no items).
    """
    if not hn_items:
        return []

    summary_map = summary_map or {}
    has_summary = any(summary_map.get(item.url, {}).get("ai_summary") for item in hn_items)

    hn_label = "Hacker News 热门" if report_language == "zh" else "Hacker News Trending"
    lines = [f"### {hn_label} ({len(hn_items)} {count_unit})", ""]

    if has_summary:
        lines.append(f"| # | {'文章' if report_language == 'zh' else 'Article'} | {'热度' if report_language == 'zh' else 'Stats'} | {'摘要' if report_language == 'zh' else 'Summary'} |")
        lines.append("|---:|------|------|------|")
    else:
        lines.append(f"| # | {'文章' if report_language == 'zh' else 'Article'} | {'热度' if report_language == 'zh' else 'Stats'} |")
        lines.append("|---:|------|------|")

    for i, item in enumerate(hn_items, 1):
        stats_parts = []
        if item.hn_points is not None:
            stats_parts.append(f"🔥 {item.hn_points}")
        if item.hn_comments is not None:
            stats_parts.append(f"💬 {item.hn_comments}")
        stats_str = " · ".join(stats_parts)

        title_cell = f"[**{_escape_pipe(item.title)}**]({_escape_pipe(item.url)})"

        if has_summary:
            ai_summary = summary_map.get(item.url, {}).get("ai_summary", "")
            summary_cell = _escape_pipe(ai_summary) if ai_summary else ""
            lines.append(f"| {i} | {title_cell} | {_escape_pipe(stats_str)} | {summary_cell} |")
        else:
            lines.append(f"| {i} | {title_cell} | {_escape_pipe(stats_str)} |")

    lines.append("")
    return lines


def _select_non_ai_articles(articles: list, max_count: int) -> list:
    """Select up to max_count non-AI articles, prioritizing editorial tiers.

    Must-read articles are always kept. Remaining slots are filled by
    noteworthy (sorted by score), then brief (sorted by score).
    """
    must_read = [a for a in articles if a.extra.get("editorial_tier") == "must_read"]
    noteworthy = [a for a in articles if a.extra.get("editorial_tier") == "noteworthy"]
    brief = [a for a in articles if a.extra.get("editorial_tier") == "brief"]
    unclassified = [a for a in articles if not a.extra.get("editorial_tier")]

    for tier_list in (noteworthy, brief, unclassified):
        tier_list.sort(key=lambda a: a.extra.get("news_value_score", 0), reverse=True)

    selected = list(must_read)
    for tier in (noteworthy, unclassified, brief):
        remaining = max_count - len(selected)
        if remaining <= 0:
            break
        selected.extend(tier[:remaining])
    return selected


def _render_today_highlights(category_results, report_language):
    """Render the 🔥 Today's Highlights section from top must_reads across categories."""
    highlights = []
    for category, data in category_results.items():
        tiered = data.get("tiered", {})
        articles = data.get("articles", [])
        for item in tiered.get("must_read", []):
            idx = item.get("index", 0)
            if 1 <= idx <= len(articles):
                highlights.append((articles[idx - 1], item.get("summary", "")))

    if not highlights:
        return ""

    top = highlights[:5]
    lines = []
    label = "🔥 今日重点" if report_language == "zh" else "🔥 Today's Highlights"
    lines.append(f"### {label}")
    lines.append("")

    for i, (article, summary) in enumerate(top, 1):
        title = article.title.replace("|", "\\|").replace("\n", " ")
        url = article.url.replace("|", "\\|")
        source = article.source.replace("|", "\\|")
        source_label = "来源" if report_language == "zh" else "Source"
        why_label = "为什么重要" if report_language == "zh" else "Why it matters"
        lines.append(f"#### ⭐ [{title}]({url})")
        lines.append("")
        lines.append(f"> **{source_label}**: *{source}* | **{why_label}**: {summary}")
        lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _render_tiered_category(name, articles, tiered, report_language):
    """Render a single category with three visual tiers: must_read / noteworthy / brief."""
    if not articles:
        return ""

    lines = []
    count = len(articles)
    count_unit = "篇" if report_language == "zh" else "articles"
    lines.append(f"### {name} ({count} {count_unit})")
    lines.append("")

    # Category summary (blockquote with proper multiline support)
    cat_summary = tiered.get("category_summary", "") if tiered else ""
    if cat_summary:
        for summary_line in cat_summary.split("\n"):
            lines.append(f"> {summary_line}" if summary_line.strip() else ">")
        lines.append("")

    # Partition articles into tiers
    must_reads = []
    noteworthies = []
    briefs = []

    if tiered:
        must_indices = {item.get("index", 0): item.get("summary", "")
                        for item in tiered.get("must_read", [])}
        note_indices = {item.get("index", 0): item.get("summary", "")
                        for item in tiered.get("noteworthy", [])}
        brief_indices = set(tiered.get("brief", []))

        for i, article in enumerate(articles, 1):
            if i in must_indices:
                must_reads.append((article, must_indices[i]))
            elif i in note_indices:
                noteworthies.append((article, note_indices[i]))
            elif i in brief_indices:
                briefs.append(article)
            else:
                noteworthies.append((article, ""))
    else:
        noteworthies = [(a, "") for a in articles]

    # ⭐ Must Read section
    if must_reads:
        label = "⭐ 必读" if report_language == "zh" else "⭐ Must Read"
        lines.append(f"#### {label}")
        lines.append("")
        for i, (article, summary) in enumerate(must_reads, 1):
            title = article.title.replace("|", "\\|").replace("\n", " ")
            url = article.url.replace("|", "\\|")
            source = article.source.replace("|", "\\|")
            source_label = "来源" if report_language == "zh" else "Source"
            why_label = "为什么重要" if report_language == "zh" else "Why it matters"
            lines.append(f"**{i}. [{title}]({url})**")
            lines.append("")
            if summary:
                lines.append(f"> **{source_label}**: *{source}* | **{why_label}**: {summary}")
            else:
                lines.append(f"> **{source_label}**: *{source}*")
            lines.append("")

    # 📰 Noteworthy section
    if noteworthies:
        label = "📰 值得关注" if report_language == "zh" else "📰 Noteworthy"
        lines.append(f"#### {label}")
        lines.append("")
        article_header = "文章" if report_language == "zh" else "Article"
        source_header = "来源" if report_language == "zh" else "Source"
        point_header = "要点" if report_language == "zh" else "Key Point"
        lines.append(f"| # | {article_header} | {source_header} | {point_header} |")
        lines.append("|---|---------|--------|-----------|")
        for i, (article, summary) in enumerate(noteworthies, 1):
            title_cell = f"[**{_escape_pipe(article.title)}**]({_escape_pipe(article.url)})"
            source_cell = f"*{_escape_pipe(article.source)}*"
            lines.append(f"| {i} | {title_cell} | {source_cell} | {_escape_pipe(summary)} |")
        lines.append("")

    # 📋 Brief section (collapsed)
    if briefs:
        brief_label = "简讯" if report_language == "zh" else "Brief"
        lines.append("<details>")
        lines.append(f"<summary>📋 {brief_label} ({len(briefs)} {count_unit})</summary>")
        lines.append("")
        article_header = "文章" if report_language == "zh" else "Article"
        source_header = "来源" if report_language == "zh" else "Source"
        lines.append(f"| # | {article_header} | {source_header} |")
        lines.append("|---|---------|--------|")
        for i, article in enumerate(briefs, 1):
            lines.append(_article_table_row(i, article.title, article.url, article.source))
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines)


def generate_tech_report(updates, summary_map=None, trend_insight_skill=None,
                         executive_summary=None, category_results=None,
                         stats=None, report_language="zh", trend_insights=None):
    """生成科技日报 Markdown 报告

    支持两种模式:
    - Skill 模式 (category_results=None): 按 article 列表渲染分类报告
    - API 模式 (category_results provided): 按 AI 摘要结果渲染分类报告

    Args:
        updates: list of Article objects（来自 rss_fetcher）
        summary_map: dict, url -> {ai_summary, category}（来自 AI 摘要，Skill 模式）
        trend_insight_skill: dict with "trend_insight" key (Skill mode)
        executive_summary: str, 执行摘要（API 模式）
        category_results: dict, category -> {name, summary, article_count, articles}（API 模式）
        stats: dict with metadata
        report_language: "zh" or "en"

    Returns:
        str: Markdown 报告内容
    """
    now = datetime.now(timezone.utc)
    report_date = now.strftime("%Y-%m-%d")
    report_time = now.strftime("%Y-%m-%d %H:%M")

    lines = []

    if category_results:
        # ---- API 模式：基于分层摘要渲染 ----
        total_articles = (stats or {}).get("total_articles", 0)
        total_categories = len(category_results)

        if report_language == "zh":
            lines.append(f"> 📰 {total_articles} 篇文章 · {total_categories} 个分类 · 🤖 AI 智能摘要")
        else:
            lines.append(f"> 📰 {total_articles} articles · {total_categories} categories · 🤖 AI-powered")

        lines.append("")
        lines.append("---")
        lines.append("")

        # 执行摘要
        if executive_summary:
            exec_label = "📋 今日要闻" if report_language == "zh" else "📋 Today's Highlights"
            lines.append(f"### {exec_label}")
            lines.append("")
            lines.append(executive_summary)
            lines.append("")
            lines.append("---")
            lines.append("")

        # 趋势洞察 (API mode)
        if trend_insights and trend_insights.strip() and len(trend_insights.strip()) > 10:
            trend_label = "📊 趋势洞察" if report_language == "zh" else "📊 Trend Insights"
            lines.append(f"### {trend_label}")
            lines.append("")
            lines.append(trend_insights)
            lines.append("")
            lines.append("---")
            lines.append("")

        # 🔥 Today's Highlights (cross-category must-reads)
        highlights = _render_today_highlights(category_results, report_language)
        if highlights:
            lines.append(highlights)

        # 各分类 (tiered rendering)
        for category, data in category_results.items():
            name = data.get("name", get_category_display(category))
            articles = data.get("articles", [])
            tiered = data.get("tiered")
            count = data.get("article_count", len(articles))

            section = _render_tiered_category(name, articles, tiered, report_language)
            if section:
                lines.append(section)
                lines.append("---")
                lines.append("")

        # 页脚
        footer = "报告生成时间" if report_language == "zh" else "Generated at"
        lines.append(f"*{footer}: {report_time} UTC*")

    else:
        # ---- Skill 模式：按 article 列表渲染 ----
        summary_map = summary_map or {}
        checked = (stats or {}).get("checked_count", (stats or {}).get("total_feeds", 0))
        hours = (stats or {}).get("hours", 24)
        update_count = len(updates)

        if report_language == "zh":
            lines.append(f"# AI 科技日报 — {report_date}")
            lines.append("")
            lines.append(f"> 共检查 {checked} 个信息源 · {hours}h 窗口 · 发现 {update_count} 条更新")
        else:
            lines.append(f"# AI Tech Daily — {report_date}")
            lines.append("")
            lines.append(f"> Checked {checked} sources · {hours}h window · found {update_count} updates")

        lines.append("")
        lines.append("---")
        lines.append("")

        # 趋势洞察 (Skill mode)
        if trend_insight_skill:
            insight_text = trend_insight_skill.get("trend_insight", "")
            if insight_text:
                lines.append("## " + ("今日趋势洞察" if report_language == "zh" else "Today's Trend Insights"))
                lines.append("")
                lines.append(insight_text)
                lines.append("")
                lines.append("---")
                lines.append("")

        # 按分类分组
        groups = OrderedDict()
        for cat in CATEGORY_ORDER:
            groups[cat] = []
        groups["其他"] = []

        hn_items = []
        for update in updates:
            source_cat = normalize_category(update.category)
            if source_cat == "hacker_news":
                hn_items.append(update)
                continue

            # 检查 AI 是否重新分类
            url = update.url
            ai_info = summary_map.get(url, {})
            ai_cat = ai_info.get("category", "")
            final_cat = normalize_category(ai_cat) if ai_cat else source_cat
            if final_cat not in groups:
                final_cat = "其他"
            groups[final_cat].append(update)

        # 输出各分类
        count_unit = "条" if report_language == "zh" else "items"
        for cat, cat_updates in groups.items():
            if not cat_updates:
                continue

            cat_display = get_category_display(cat)
            lines.append(f"## {cat_display} ({len(cat_updates)} {count_unit})")
            lines.append("")

            # 表格格式
            has_summary = any(summary_map.get(u.url, {}).get("ai_summary", "") or u.description for u in cat_updates)
            if has_summary:
                summary_header = "摘要" if report_language == "zh" else "Summary"
                lines.append(f"| # | {'文章' if report_language == 'zh' else 'Article'} | {'来源' if report_language == 'zh' else 'Source'} | {summary_header} |")
                lines.append("|---:|------|------|------|")
            else:
                lines.append(f"| # | {'文章' if report_language == 'zh' else 'Article'} | {'来源' if report_language == 'zh' else 'Source'} |")
                lines.append("|---:|------|------|")

            for i, update in enumerate(cat_updates, 1):
                ai_info = summary_map.get(update.url, {})
                ai_summary = ai_info.get("ai_summary", "")
                summary_text = ""
                if ai_summary:
                    summary_text = ai_summary
                elif update.description:
                    clean_desc = re.sub(r'<[^>]+>', '', update.description.strip())
                    if len(clean_desc) > 150:
                        clean_desc = clean_desc[:150] + "..."
                    summary_text = clean_desc
                lines.append(_article_table_row(i, update.title, update.url, update.source, summary_text))

            lines.append("")

        # Hacker News
        if hn_items:
            lines.extend(_render_hn_table(hn_items, report_language, count_unit, summary_map))

        # 页脚
        footer_prefix = "报告生成时间" if report_language == "zh" else "Report generated at"
        lines.append(f"*{footer_prefix}: {report_time} UTC*")

    return "\n".join(lines)


def save_report(content, filename, output_dir=None, report_type="tech", language="zh",
                skip_tldr=False):
    """保存报告到文件

    Args:
        content: str, Markdown 内容
        filename: str, 文件名（如 tech-daily_14-30.md）
        output_dir: Path, 输出目录
        report_type: str, 报告类型（tech/podcast/wechat）
        language: str, 语言（zh/en）
        skip_tldr: bool, 已废弃，保留兼容

    Returns:
        Path: 保存的文件路径
    """
    content = sanitize_report_markdown(content)
    if contains_reasoning_artifacts(content):
        logger.warning("[Report] ⚠️ reasoning artifacts detected after sanitization; applying fallback cleanup")
        content = sanitize_report_markdown(content)

    output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / filename
    tmp_path = filepath.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        tmp_path.replace(filepath)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    logger.info(f"[Report] ✅ 报告已保存: {filepath}")
    return filepath
