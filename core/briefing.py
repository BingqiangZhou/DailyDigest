"""Briefing data model: theme grouping, highlight selection, and stats."""
import re
from .logging_config import get_logger
from .config import normalize_category, REPORT_MAX_THEMES, REPORT_ARTICLES_PER_THEME, REPORT_BRIEF_ITEMS_CAP

logger = get_logger("briefing")

_THEME_ORDER = [
    "模型与平台",
    "研究与方法",
    "开源与工具",
    "硬件与基础设施",
    "产品与应用",
    "评测与实战",
    "行业与商业",
]


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


def _clean_theme_title(text, fallback):
    cleaned = re.sub(r'\s+', ' ', (text or "")).strip()
    cleaned = cleaned.strip("-:;,| ")
    cleaned = cleaned[:72] if cleaned else ""
    if cleaned and not _is_language_compatible(cleaned):
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


def _is_language_compatible(text):
    """Check whether text contains meaningful CJK presence for Chinese reports."""
    if not text:
        return False

    cleaned = text.strip()
    if not cleaned:
        return False

    cjk_count = len(re.findall(r'[一-鿿]', cleaned))
    latin_count = len(re.findall(r'[A-Za-z]', cleaned))

    if cjk_count == 0:
        return False
    if latin_count == 0:
        return True

    return cjk_count >= 4 or cjk_count >= latin_count


def _extract_keyword_from_title(title, max_en=6, max_zh=4):
    """Extract a short distinguishing keyword from an article title.

    For titles containing English: extract the first capitalized word or acronym.
    For Chinese titles: extract the first 2-4 character meaningful phrase.
    Returns empty string if nothing useful is found.
    """
    if not title:
        return ""
    # Try English first: first capitalized word or acronym
    en_match = re.search(r'\b([A-Z][A-Za-z0-9\-\.]{1,' + str(max_en - 1) + r'})\b', title)
    if en_match:
        return en_match.group(1)[:max_en]
    # Chinese: first meaningful phrase (2-4 CJK chars, skip common particles)
    zh_match = re.search(r'[一-鿿]{2,' + str(max_zh) + r'}', title)
    if zh_match:
        return zh_match.group(0)[:max_zh]
    return ""


def _compose_theme_summary(theme, summary_map=None):
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
            summary = _clean_description_for_display(article.description, max_len=150)
        if not summary:
            continue
        summary = summary.replace("\n", " ").strip()
        # Extract only the first sentence
        summary = re.split(r'[。.!！]', summary)[0].strip()
        if not summary:
            continue
        if not _is_language_compatible(summary):
            continue
        if summary and summary not in seen:
            seen.add(summary)
            parts.append(summary[:150])
        if len(parts) >= 3:
            break

    if parts:
        joined = " · ".join(parts)
        # Cap total summary length to keep themes scannable
        if len(joined) > 300:
            joined = joined[:300].rstrip() + "..."
        return joined

    # No descriptions available — build a keyword summary from article titles
    keywords = []
    for article in theme.get("articles", []):
        kw = _extract_keyword_from_title(article.title or "")
        if kw and kw not in keywords:
            keywords.append(kw)
        if len(keywords) >= 4:
            break
    if keywords:
        return "涉及：" + "、".join(keywords)

    return "今日该主题有多篇相关更新，需结合参考条目快速浏览。"


def _fallback_trends(themes, brief_items=None):
    """Generate heuristic trend bullets from theme + brief data.

    Goes beyond simple "N articles" counts by surfacing source distribution,
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
        trends.append(f"今日信息源分布：{top_str} 等 {len(sources)} 个来源贡献了内容。")

    # 2. Cross-source convergence
    cross_themes = [t for t in themes if t.get("cross_source")]
    if cross_themes:
        names = []
        for t in cross_themes[:2]:
            title = t.get("title", "")
            # If the title is the same as the fallback theme name, use lead article title
            if title == t.get("theme", "") or not _is_language_compatible(title):
                articles = t.get("articles", [])
                if articles:
                    title = articles[0].title or title
            names.append(title)
        names_str = "、".join(names)
        trends.append(f"多源交叉验证：{names_str}，多个独立来源均报道此话题。")

    # 3. HN heat signal
    hn_hot = [a for a in all_articles if (a.hn_points or 0) >= 100]
    if hn_hot:
        top_hn = max(hn_hot, key=lambda a: a.hn_points or 0)
        trends.append(f"HN 热议：「{top_hn.title}」获 {top_hn.hn_points} 赞。")

    return trends[:3]


def _fallback_highlights(ai_articles, non_ai_articles, cluster_map=None):
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
            line += "（多源交叉验证）"
        highlights.append(line)
    return highlights


def _select_brief_items(non_ai_articles, max_count=20):
    """Select compact tech brief items, preferring higher editorial weight.

    Filters out articles from non-tech categories (e.g. general_news,
    podcast) to keep the brief section focused on technology.
    """
    if not non_ai_articles:
        return []

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


def _dedup_theme_names(themes):
    """Add keyword suffixes to duplicate theme titles for disambiguation.

    Modifies themes in-place. When multiple themes share the same title,
    appends a distinguishing keyword from the lead article's title.
    """
    # Count occurrences of each title
    title_counts = {}
    for t in themes:
        title = t.get("title", "")
        title_counts[title] = title_counts.get(title, 0) + 1

    # Only process duplicates
    seen = {}
    for t in themes:
        title = t.get("title", "")
        if title_counts.get(title, 0) <= 1:
            continue
        # Track occurrence index per title
        idx = seen.get(title, 0)
        seen[title] = idx + 1
        # Skip the first occurrence — only suffix subsequent ones
        if idx == 0:
            continue
        articles = t.get("articles", [])
        if articles:
            lead_title = articles[0].title or ""
            kw = _extract_keyword_from_title(lead_title)
            if kw:
                t["title"] = f"{title} · {kw}"


def _build_theme_groups(ai_articles, cluster_map=None):
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
        title = _clean_theme_title(lead.title, theme_fallback)
        # Double-check: ensure Chinese reports never show English-only titles
        if not _is_language_compatible(title):
            title = theme_fallback
        themes.append({
            "id": cluster_id,
            "theme": theme_fallback,
            "title": title,
            "articles": members[:REPORT_ARTICLES_PER_THEME],
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
        # Sort members so the lead article is the highest-ranked one
        members.sort(key=lambda a: _article_rank(a, cluster_map), reverse=True)
        themes.append({
            "id": f"theme-{theme_name}",
            "theme": theme_name,
            "title": theme_name,
            "articles": members[:REPORT_ARTICLES_PER_THEME],
            "score": max(a.extra.get("news_value_score", 0) for a in members),
            "cluster_theme": "",
            "cross_source": False,
        })

    # Dedup pass: add keyword suffix when multiple themes share the same name
    _dedup_theme_names(themes)

    themes.sort(key=_theme_sort_key)
    return themes[:REPORT_MAX_THEMES]


def _combine_briefing_stats(ai_articles, non_ai_articles, stats=None, cluster_map=None,
                            llm_themes=None):
    """Normalize top-level report statistics."""
    stats = dict(stats or {})
    total_included = len(ai_articles) + len(non_ai_articles)
    stats.setdefault("included_count", total_included)
    stats.setdefault("after_editorial", total_included)
    stats.setdefault("after_dedup", total_included)
    stats.setdefault("candidate_count", total_included)
    stats.setdefault("source_count", len({a.source for a in ai_articles + non_ai_articles if a.source}))
    if llm_themes is not None:
        stats["theme_count"] = len(llm_themes)
    else:
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


def _convert_llm_themes(llm_themes, summary_map=None):
    """Convert LLM-generated themes to briefing theme format."""
    themes = []
    for i, t in enumerate(llm_themes):
        theme = {
            "id": f"llm-theme-{i}",
            "theme": t["title"],
            "title": t["title"],
            "articles": t["articles"][:REPORT_ARTICLES_PER_THEME],
            "score": t["score"],
            "cluster_theme": "",
            "cross_source": t.get("cross_source", False),
            "summary": t.get("summary", ""),
            "source_count": t.get("source_count", 0),
            "cluster_size": t.get("cluster_size", len(t.get("articles", []))),
        }
        if not theme["summary"]:
            theme["summary"] = _compose_theme_summary(theme, summary_map=summary_map)
        themes.append(theme)
    return themes


def build_briefing_data(ai_articles, non_ai_articles=None, cluster_map=None, summary_map=None,
                        stats=None, llm_themes=None, llm_leftovers=None,
                        embedding_singletons=None):
    """Build the neutral briefing-data contract shared by markdown and wechat.

    Supports two modes:
    - Legacy: ai_articles + non_ai_articles + cluster_map (heuristic themes)
    - LLM: ai_articles (all) + llm_themes + llm_leftovers (LLM-generated themes)
    - Embedding: same as LLM, with embedding_singletons for "值得关注" section
    """
    if llm_themes is not None:
        # LLM pipeline mode — unified articles, no AI/non-AI split
        non_ai = non_ai_articles or []
        all_articles = list(ai_articles) + list(non_ai)
        themes = _convert_llm_themes(llm_themes, summary_map=summary_map)
        brief_items = list(llm_leftovers or [])[:REPORT_BRIEF_ITEMS_CAP]

        # Notable singletons: high-score articles not in any multi-article theme
        notable_singletons = []
        if embedding_singletons:
            notable_singletons = [
                a for a in embedding_singletons
                if a.extra.get("news_value_score", 0) >= 7
            ][:8]

        return {
            "highlights": _fallback_highlights(all_articles, [], cluster_map=None),
            "themes": themes[:REPORT_MAX_THEMES],
            "featured_tech": [],
            "brief_items": brief_items,
            "notable_singletons": notable_singletons,
            "stats": _combine_briefing_stats(
                all_articles, [], stats=stats, cluster_map=None, llm_themes=llm_themes,
            ),
            "trends": _fallback_trends(themes[:REPORT_MAX_THEMES], brief_items=brief_items),
        }

    # Backward-compatible heuristic mode
    non_ai = non_ai_articles or []
    cluster_map = cluster_map or {}
    themes = _build_theme_groups(ai_articles, cluster_map=cluster_map)
    for theme in themes:
        theme["summary"] = _compose_theme_summary(theme, summary_map=summary_map)
    brief_items = _select_brief_items(non_ai, REPORT_BRIEF_ITEMS_CAP)
    return {
        "highlights": _fallback_highlights(ai_articles, non_ai, cluster_map=cluster_map),
        "themes": themes[:REPORT_MAX_THEMES],
        "featured_tech": _select_featured_tech(non_ai),
        "brief_items": brief_items,
        "notable_singletons": [],
        "stats": _combine_briefing_stats(ai_articles, non_ai, stats=stats, cluster_map=cluster_map),
        "trends": _fallback_trends(themes[:REPORT_MAX_THEMES], brief_items=brief_items),
    }


def _build_highlights(ai_articles, non_ai_articles, cluster_map):
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

    section = "### 🔥 今日要点\n\n"
    for i, a in enumerate(highlights, 1):
        title = a.title.replace("|", "\\|").replace("\n", " ")
        url = a.url.replace("|", "\\|")
        source = a.source.replace("|", "\\|")
        desc = _clean_description_for_display(a.description, max_len=100)
        if desc and not _is_language_compatible(desc):
            desc = ""
        engagement = ""
        if a.hn_points and a.hn_points >= 50:
            engagement = f" (🔥HN {a.hn_points})"
        section += f"**{i}. [{title}]({url})** — *{source}*{engagement}\n"
        if desc:
            section += f"> {desc}\n"
        section += "\n"

    section += "---\n\n"
    return section


def _build_data_dashboard(ai_articles, non_ai_articles, cluster_map):
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

    dash += "\n---\n\n"
    return dash
