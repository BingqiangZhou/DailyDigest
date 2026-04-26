"""
Report building utilities for DailyDigest.

Handles section cleanup, Markdown manipulation, merged and unified
report construction, and category/tier conversion from sub-agent summaries.
"""

import os
import re

from .logging_config import get_logger

logger = get_logger("report_builder")


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


def build_unified_report(ai_articles, non_ai_articles, now, language="zh", quality_scores=None,
                         summary_map=None, cluster_map=None):
    """Build a two-part unified report: AI deep analysis + non-AI tech news."""
    from .ai_report import build_ai_section
    from .report_generator import build_non_ai_section, generate_tech_report

    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    ai_count = len(ai_articles)
    non_ai_count = len(non_ai_articles)
    total = ai_count + non_ai_count

    if language == "zh":
        header = f"# 📰 Daily Digest — {date_str}\n\n"
        header += f"> 🤖 AI 深度分析 {ai_count} 篇 · 💻 科技动态 {non_ai_count} 条 · 共 {total} 篇\n\n"
        header += f"> ⏰ 生成时间 {time_str} UTC\n"
    else:
        header = f"# 📰 Daily Digest — {date_str}\n\n"
        header += f"> 🤖 AI Deep Analysis {ai_count} articles · 💻 Tech Updates {non_ai_count} items · Total {total}\n\n"
        header += f"> ⏰ Generated at {time_str} UTC\n"

    header += "\n---\n\n"

    has_tiers = False
    if summary_map:
        has_tiers = any(
            isinstance(v, dict) and "tier" in v
            for v in summary_map.values()
        )
    elif any(a.extra.get("editorial_tier") for a in ai_articles):
        has_tiers = True
        category_results = build_category_results_from_editorial(ai_articles, cluster_map)

    if has_tiers:
        category_results = build_category_results_from_summaries(ai_articles, summary_map)
        ai_section_body = generate_tech_report(
            ai_articles,
            category_results=category_results,
            stats={"total_articles": ai_count, "categories": len(category_results)},
            report_language=language,
        )
        part_label = "AI 深度日报" if language == "zh" else "AI Deep Digest"
        ai_section = f"# Part I: 🤖 {part_label} ({ai_count} {'篇' if language == 'zh' else 'articles'})\n\n{ai_section_body}"
    else:
        ai_section = build_ai_section(ai_articles, language, summary_map=summary_map,
                                      cluster_map=cluster_map)

    non_ai_section = build_non_ai_section(non_ai_articles, language)

    parts = []
    if ai_section:
        parts.append(ai_section)
    if non_ai_section:
        parts.append(non_ai_section)

    if not parts:
        return ""

    return header + "\n\n---\n\n".join(parts)


def build_unified_wechat_report(ai_articles, non_ai_articles, now, language="zh",
                                 summary_map=None, cluster_map=None,
                                 category_results=None):
    """Build a WeChat Official Account Markdown article."""
    from .wechat_article import generate_wechat_article

    if not category_results and summary_map:
        category_results = build_category_results_from_summaries(ai_articles, summary_map)

    ai_structure = None
    if os.environ.get("API_KEY") and ai_articles:
        try:
            from .ai_summarizer import generate_wechat_structure
            ai_structure = generate_wechat_structure(ai_articles, language)
        except Exception as e:
            logger.warning(f"[WeChat] ⚠️ AI结构生成失败，使用分类回退: {e}")

    return generate_wechat_article(
        ai_articles=ai_articles,
        non_ai_articles=non_ai_articles,
        now=now,
        language=language,
        category_results=category_results,
        summary_map=summary_map,
        cluster_map=cluster_map,
        ai_structure=ai_structure,
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


def build_category_results_from_editorial(ai_articles, cluster_map=None):
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
                reason = _generate_importance_reason(article, cluster_map)
                must_read.append({"index": i, "summary": reason})
            elif tier == "brief":
                brief.append(i)
            else:
                reason = _generate_importance_reason(article, cluster_map)
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


def _generate_importance_reason(article, cluster_map=None):
    """Generate a brief importance reason from article metadata."""
    parts = []
    cluster_info = (cluster_map or {}).get(article.url, {})
    cluster_size = cluster_info.get("cluster_size", 1)
    cross_source = cluster_info.get("cross_source", False)
    score = article.extra.get("news_value_score", 0)

    if cluster_size >= 3:
        parts.append(f"{cluster_size}篇相关报道")
    if cross_source:
        parts.append("多源验证")
    if article.priority == 1:
        parts.append("权威来源")
    if article.hn_points and article.hn_points >= 100:
        parts.append(f"HN {article.hn_points}赞")

    if not parts:
        if score >= 0.6:
            parts.append("高新闻价值")
        else:
            parts.append(article.description[:80] if article.description else "值得关注")

    return "，".join(parts)


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
