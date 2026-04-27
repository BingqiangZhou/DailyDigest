"""
Markdown 报告生成模块
统一输出 Markdown 格式的日报报告。
支持科技日报、播客日报、微信日报三种类型。
"""

import json
import os
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    OUTPUT_DIR, CATEGORY_ORDER, get_category_display,
    normalize_category,
)
from .logging_config import get_logger
from .llm_utils import contains_reasoning_artifacts, sanitize_report_markdown

logger = get_logger("report")


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

    content = sanitize_report_markdown(content)
    if contains_reasoning_artifacts(content):
        logger.warning("[Report] ⚠️ reasoning artifacts detected after sanitization; applying fallback cleanup")
        content = sanitize_report_markdown(content)

    output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"[Report] ✅ 报告已保存: {filepath}")
    return filepath


def _clean_description(desc: str, max_len: int = 150) -> str:
    """Clean article description for display.

    Strips HTML tags and removes HN RSS metadata noise
    (Article URL, Comments URL, Points, # Comments lines).
    """
    if not desc:
        return ""
    clean = re.sub(r'<[^>]+>', '', desc.strip())
    # Remove HN RSS metadata lines
    clean = re.sub(r'^Article URL:.*$', '', clean, flags=re.MULTILINE)
    clean = re.sub(r'^Comments URL:.*$', '', clean, flags=re.MULTILINE)
    clean = re.sub(r'^Points:.*$', '', clean, flags=re.MULTILINE)
    clean = re.sub(r'^# Comments.*$', '', clean, flags=re.MULTILINE)
    clean = re.sub(r'\n{3,}', '\n\n', clean.strip())
    clean = clean.strip()
    if len(clean) > max_len:
        clean = clean[:max_len].rstrip() + "..."
    return clean


def build_non_ai_section(non_ai_articles, report_language="zh"):
    """Build Part II: non-AI tech news section.

    Reuses the existing table-based report format for articles
    that are not AI-related. When editorial tier data is present,
    must_read articles are rendered prominently at the top.

    Args:
        non_ai_articles: list of Article objects (non-AI)
        report_language: "zh" or "en"

    Returns:
        Markdown string for Part II
    """
    if not non_ai_articles:
        return ""

    # Cap articles to avoid overwhelming the reader.
    # Prioritize: must_read > noteworthy > brief, then by news_value_score.
    original_count = len(non_ai_articles)
    max_articles = 30
    if len(non_ai_articles) > max_articles:
        non_ai_articles = _select_non_ai_articles(non_ai_articles, max_articles)

    total_count = len(non_ai_articles)

    if report_language == "zh":
        count_label = f"{total_count} 条" if total_count == original_count else f"Top {total_count}/{original_count} 条"
        lines = [f"## Part II: 💻 科技动态 ({count_label})"]
    else:
        count_label = f"{total_count} items" if total_count == original_count else f"Top {total_count}/{original_count} items"
        lines = [f"## Part II: 💻 Tech Updates ({count_label})"]

    lines.append("")

    # Category breakdown summary
    cat_counts = {}
    for a in non_ai_articles:
        cat = normalize_category(a.category)
        if cat == "hacker_news":
            cat = "hacker_news"
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    cat_summary = " · ".join(f"{get_category_display(c)} {n}" for c, n in sorted(cat_counts.items(), key=lambda x: -x[1])[:4])
    if cat_summary:
        lines.append(f"> {cat_summary}")
        lines.append("")

    lines.append("---")
    lines.append("")

    # Check if any articles have editorial tier data
    has_editorial = any(a.extra.get("editorial_tier") for a in non_ai_articles)

    # Prominently render must_read non-AI articles at the top
    if has_editorial:
        must_read_articles = [a for a in non_ai_articles
                              if a.extra.get("editorial_tier") == "must_read"
                              and normalize_category(a.category) != "hacker_news"]
        if must_read_articles:
            if report_language == "zh":
                lines.append("#### ⭐ 重点科技新闻")
                lines.append("")
                for a in must_read_articles:
                    desc = _clean_description(a.description or "")
                    reason = a.extra.get("editorial_factors", {})
                    reason_text = ""
                    # Try to generate a brief importance hint
                    if reason.get("cross_source", 0) > 0.1:
                        reason_text = " [多源验证]"
                    lines.append(f"- **[{_escape_pipe(a.title)}]({_escape_pipe(a.url)})** — {a.source}{reason_text}")
                    if desc:
                        lines.append(f"  > {desc}")
                    lines.append("")
            else:
                lines.append("#### ⭐ Top Tech News")
                lines.append("")
                for a in must_read_articles:
                    desc = _clean_description(a.description or "")
                    lines.append(f"- **[{_escape_pipe(a.title)}]({_escape_pipe(a.url)})** — {a.source}")
                    if desc:
                        lines.append(f"  > {desc}")
                    lines.append("")

    # Group by category using same logic as Skill-mode report
    groups = OrderedDict()
    for cat in CATEGORY_ORDER:
        if cat not in ("ai_ml", "ai_tools"):  # Skip AI categories in Part II
            groups[cat] = []
    groups["其他"] = []

    hn_items = []
    for update in non_ai_articles:
        source_cat = normalize_category(update.category)
        if source_cat == "hacker_news":
            hn_items.append(update)
            continue
        if source_cat in ("ai_ml", "ai_tools"):
            continue  # Safety check
        # Skip must_read articles already rendered above
        if has_editorial and update.extra.get("editorial_tier") == "must_read":
            continue
        if source_cat not in groups:
            source_cat = "其他"
        groups[source_cat].append(update)

    # Output category tables (sorted by news_value_score within each category)
    count_unit = "条" if report_language == "zh" else "items"
    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue

        cat_updates.sort(key=lambda a: a.extra.get("news_value_score", 0), reverse=True)

        cat_display = get_category_display(cat)
        lines.append(f"### {cat_display} ({len(cat_updates)} {count_unit})")
        lines.append("")

        has_desc = any(u.description for u in cat_updates)
        if has_desc:
            summary_header = "摘要" if report_language == "zh" else "Summary"
            lines.append(f"| # | {'文章' if report_language == 'zh' else 'Article'} | {'来源' if report_language == 'zh' else 'Source'} | {summary_header} |")
            lines.append("|---:|------|------|------|")
        else:
            lines.append(f"| # | {'文章' if report_language == 'zh' else 'Article'} | {'来源' if report_language == 'zh' else 'Source'} |")
            lines.append("|---:|------|------|")

        for i, update in enumerate(cat_updates, 1):
            summary_text = _clean_description(update.description or "")
            lines.append(_article_table_row(i, update.title, update.url, update.source, summary_text))

        lines.append("")

    # Hacker News
    if hn_items:
        lines.extend(_render_hn_table(hn_items, report_language, count_unit))

    return "\n".join(lines)
