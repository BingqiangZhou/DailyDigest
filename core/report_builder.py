"""
Report building utilities for DailyDigest.

Handles report assembly, category/tier conversion, merged and unified
report construction, and file I/O.  Briefing data model lives in
briefing.py; markdown rendering lives in renderer.py.
"""

import os
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from .logging_config import get_logger
from .config import OUTPUT_DIR, CATEGORY_ORDER, get_category_display, normalize_category, has_api_key
from .llm_utils import contains_reasoning_artifacts, sanitize_report_markdown
from .llm_services import render_briefing_v2 as _render_briefing_v2
from .briefing import (
    build_briefing_data,
    _build_highlights,
    _build_data_dashboard,
    _clean_description_for_display,
)
from .renderer import (
    demote_headings,
    make_anchor,
    strip_section_header_footer,
    _escape_pipe,
    _article_table_row,
    _render_hn_table,
    _render_today_highlights,
    _render_tiered_category,
    _merge_llm_briefing,
    _render_briefing_markdown,
)

logger = get_logger("report_builder")


# ---------------------------------------------------------------------------
# Backward-compatible re-exports
# ---------------------------------------------------------------------------

__all__ = [
    "build_briefing_data",
    "_build_highlights",
    "_build_data_dashboard",
    "demote_headings",
    "make_anchor",
    "strip_section_header_footer",
    "_escape_pipe",
    "_article_table_row",
    "_render_hn_table",
    "_render_today_highlights",
    "_render_tiered_category",
    "_merge_llm_briefing",
    "_render_briefing_markdown",
]


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_merged_report(sections, now):
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

    header = f"# 📰 Daily Digest — {date_str}\n\n"
    header += f"> 📡 {' · '.join(section_names)}\n\n"
    header += f"> 🕐 生成时间 {time_str} UTC\n"
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

    toc_lines = ["## 📑 目录", ""]
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


def build_unified_report(ai_articles, non_ai_articles=None, now=None,
                         summary_map=None, cluster_map=None,
                         executive_summary="", trend_insights="",
                         stats=None, llm_briefing=None,
                         llm_themes=None, llm_leftovers=None,
                         embedding_singletons=None):
    """Build the default markdown daily digest in briefing format.

    Supports two modes:
    - Legacy: ai_articles + non_ai_articles + cluster_map (heuristic themes)
    - LLM: ai_articles (all) + llm_themes + llm_leftovers (LLM-generated themes)
    """
    if non_ai_articles is None:
        non_ai_articles = []
    briefing_data = build_briefing_data(
        ai_articles,
        non_ai_articles,
        cluster_map=cluster_map,
        summary_map=summary_map,
        stats=stats,
        llm_themes=llm_themes,
        llm_leftovers=llm_leftovers,
        embedding_singletons=embedding_singletons,
    )

    if executive_summary:
        highlights = [line.lstrip("- ").strip() for line in executive_summary.splitlines() if line.strip()]
        if highlights:
            briefing_data["highlights"] = highlights[:6]
    if trend_insights:
        briefing_data["trends"] = [line.strip() for line in trend_insights.splitlines() if line.strip()]

    if has_api_key() and ai_articles and llm_briefing is None:
        try:
            llm_briefing = _render_briefing_v2(briefing_data)
        except Exception as e:
            logger.warning(f"⚠️ Briefing narrative generation failed (non-fatal): {e}")

    briefing_data = _merge_llm_briefing(briefing_data, llm_briefing)

    # Apply theme titles from LLM
    theme_titles = (llm_briefing or {}).get("theme_titles", {})
    if theme_titles:
        for idx, theme in enumerate(briefing_data.get("themes", []), 1):
            title = theme_titles.get(str(idx))
            if title:
                theme["title"] = title

    # Apply TL;DR
    tldr = (llm_briefing or {}).get("tldr", "")
    if tldr:
        briefing_data["tldr"] = tldr

    return _render_briefing_markdown(briefing_data, now)



# ---------------------------------------------------------------------------
# Category / tier conversion
# ---------------------------------------------------------------------------

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

    if cluster_size >= 3:
        parts.append(f"{cluster_size}篇相关报道")
    if cross_source:
        parts.append("多源验证")
    if article.hn_points and article.hn_points >= 100:
        parts.append(f"HN {article.hn_points}赞")
    if not parts:
        if article.description:
            desc = _clean_description_for_display(article.description, max_len=80)
            if desc:
                parts.append(desc)
    if not parts:
        parts.append("值得关注")
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


# ---------------------------------------------------------------------------
# Legacy tech report (two modes)
# ---------------------------------------------------------------------------

def generate_tech_report(updates, summary_map=None, trend_insight_skill=None,
                         executive_summary=None, category_results=None,
                         stats=None, trend_insights=None):
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

        lines.append(f"> 📰 {total_articles} 篇文章 · {total_categories} 个分类 · 🤖 AI 智能摘要")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 执行摘要
        if executive_summary:
            lines.append("### 📋 今日要闻")
            lines.append("")
            lines.append(executive_summary)
            lines.append("")
            lines.append("---")
            lines.append("")

        # 趋势洞察 (API mode)
        if trend_insights and trend_insights.strip() and len(trend_insights.strip()) > 10:
            lines.append("### 📊 趋势洞察")
            lines.append("")
            lines.append(trend_insights)
            lines.append("")
            lines.append("---")
            lines.append("")

        # 🔥 Today's Highlights (cross-category must-reads)
        highlights = _render_today_highlights(category_results)
        if highlights:
            lines.append(highlights)

        # 各分类 (tiered rendering)
        for category, data in category_results.items():
            name = data.get("name", get_category_display(category))
            articles = data.get("articles", [])
            tiered = data.get("tiered")
            count = data.get("article_count", len(articles))

            section = _render_tiered_category(name, articles, tiered)
            if section:
                lines.append(section)
                lines.append("---")
                lines.append("")

        # 页脚
        lines.append(f"*报告生成时间: {report_time} UTC*")

    else:
        # ---- Skill 模式：按 article 列表渲染 ----
        summary_map = summary_map or {}
        checked = (stats or {}).get("checked_count", (stats or {}).get("total_feeds", 0))
        hours = (stats or {}).get("hours", 24)
        update_count = len(updates)

        lines.append(f"# AI 科技日报 — {report_date}")
        lines.append("")
        lines.append(f"> 共检查 {checked} 个信息源 · {hours}h 窗口 · 发现 {update_count} 条更新")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 趋势洞察 (Skill mode)
        if trend_insight_skill:
            insight_text = trend_insight_skill.get("trend_insight", "")
            if insight_text:
                lines.append("## 今日趋势洞察")
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
        for cat, cat_updates in groups.items():
            if not cat_updates:
                continue

            cat_display = get_category_display(cat)
            lines.append(f"## {cat_display} ({len(cat_updates)} 条)")
            lines.append("")

            # 表格格式
            has_summary = any(summary_map.get(u.url, {}).get("ai_summary", "") or u.description for u in cat_updates)
            if has_summary:
                lines.append("| # | 文章 | 来源 | 摘要 |")
                lines.append("|---:|------|------|------|")
            else:
                lines.append("| # | 文章 | 来源 |")
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
            lines.extend(_render_hn_table(hn_items, "条", summary_map))

        # 页脚
        lines.append(f"*报告生成时间: {report_time} UTC*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def save_report(content, filename, output_dir=None, report_type="tech"):
    """保存报告到文件

    Args:
        content: str, Markdown 内容
        filename: str, 文件名（如 tech-daily_14-30.md）
        output_dir: Path, 输出目录
        report_type: str, 报告类型（tech/podcast）

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
