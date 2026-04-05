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


def generate_tech_report(updates, summary_map=None, trend_insight=None,
                         executive_summary=None, category_results=None,
                         stats=None, report_language="zh"):
    """生成科技日报 Markdown 报告

    支持两种模式:
    - Skill 模式 (category_results=None): 按 article 列表渲染分类报告
    - API 模式 (category_results provided): 按 AI 摘要结果渲染分类报告

    Args:
        updates: list of Article objects（来自 rss_fetcher）
        summary_map: dict, url -> {ai_summary, category}（来自 AI 摘要，Skill 模式）
        trend_insight: dict with "trend_insight" key（Skill 模式）
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
        # ---- API 模式：基于 AI 分类摘要渲染 ----
        total_articles = (stats or {}).get("total_articles", 0)
        total_categories = len(category_results)

        if report_language == "zh":
            lines.append(f"# AI 科技日报 — {report_date}")
            lines.append("")
            lines.append(f"> 📰 {total_articles} 篇文章 · {total_categories} 个分类 · 🤖 AI 智能摘要")
        else:
            lines.append(f"# AI Tech Daily — {report_date}")
            lines.append("")
            lines.append(f"> 📰 {total_articles} articles · {total_categories} categories · 🤖 AI-powered")

        lines.append("")
        lines.append("---")
        lines.append("")

        # 执行摘要
        if executive_summary:
            exec_label = "📋 今日要闻" if report_language == "zh" else "📋 Today's Highlights"
            lines.append(f"## {exec_label}")
            lines.append("")
            lines.append(executive_summary)
            lines.append("")
            lines.append("---")
            lines.append("")

        # 各分类
        for category, data in category_results.items():
            name = data.get("name", get_category_display(category))
            summary = data.get("summary", "")
            articles = data.get("articles", [])
            count = data.get("article_count", 0)
            count_unit = "篇" if report_language == "zh" else "articles"

            lines.append(f"## {name} ({count} {count_unit})")
            lines.append("")

            # AI 洞察一行摘要
            first_para = summary.strip().split("\n\n")[0].strip() if summary else ""
            if first_para:
                insight_label = "**AI 洞察**" if report_language == "zh" else "**AI Insight**"
                lines.append(f"> {insight_label}: {first_para}")
                lines.append("")

            # Full AI analysis in collapsible details block
            if summary and len(summary.strip()) > len(first_para):
                analysis_label = "📊 详细分析 — 点击展开" if report_language == "zh" else "📊 Detailed Analysis — Click to expand"
                lines.append("<details>")
                lines.append(f"<summary><strong>{analysis_label}</strong></summary>")
                lines.append("")
                lines.append(summary)
                lines.append("")
                lines.append("</details>")
                lines.append("")

            lines.append("---")
            lines.append("")

            # 文章列表 — 表格格式
            articles_label = "文章列表" if report_language == "zh" else "Articles"
            lines.append(f"### {articles_label}")
            lines.append("")
            lines.append(f"| # | {'文章' if report_language == 'zh' else 'Article'} | {'来源' if report_language == 'zh' else 'Source'} |")
            lines.append("|---:|------|------|")
            for i, article in enumerate(articles, 1):
                lines.append(_article_table_row(i, article.title, article.url, article.source))

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

        # 趋势洞察
        if trend_insight:
            insight_text = trend_insight.get("trend_insight", "")
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
            hn_label = "Hacker News 热门" if report_language == "zh" else "Hacker News Trending"
            lines.append(f"## {hn_label} ({len(hn_items)} {count_unit})")
            lines.append("")

            lines.append(f"| # | {'文章' if report_language == 'zh' else 'Article'} | {'热度' if report_language == 'zh' else 'Stats'} | {'摘要' if report_language == 'zh' else 'Summary'} |")
            lines.append("|---:|------|------|------|")

            for i, item in enumerate(hn_items, 1):
                stats_parts = []
                if item.hn_points is not None:
                    stats_parts.append(f"🔥 {item.hn_points}")
                if item.hn_comments is not None:
                    stats_parts.append(f"💬 {item.hn_comments}")
                stats_str = " · ".join(stats_parts)

                ai_info = summary_map.get(item.url, {})
                ai_summary = ai_info.get("ai_summary", "")

                title_cell = f"[**{_escape_pipe(item.title)}**]({_escape_pipe(item.url)})"
                summary_cell = _escape_pipe(ai_summary) if ai_summary else ""
                lines.append(f"| {i} | {title_cell} | {_escape_pipe(stats_str)} | {summary_cell} |")

            lines.append("")

        # 页脚
        footer_prefix = "报告生成时间" if report_language == "zh" else "Report generated at"
        lines.append(f"*{footer_prefix}: {report_time} UTC*")

    return "\n".join(lines)


def save_report(content, filename, output_dir=None, report_type="tech", language="zh"):
    """保存报告到文件（自动生成 TL;DR 并插入头部）

    Args:
        content: str, Markdown 内容
        filename: str, 文件名（如 tech-daily_14-30.md）
        output_dir: Path, 输出目录
        report_type: str, 报告类型（tech/podcast/wechat），用于 TL;DR 生成
        language: str, 语言（zh/en）

    Returns:
        Path: 保存的文件路径
    """
    # 尝试生成 TL;DR
    tldr = _generate_tldr_if_needed(content, report_type, language)
    if tldr:
        content = _insert_tldr(content, tldr, language)

    output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[Report] ✅ 报告已保存: {filepath}")
    return filepath


def _generate_tldr_if_needed(content, report_type, language):
    """如果环境允许，调用 AI 生成 TL;DR"""
    if not os.environ.get("API_KEY"):
        return ""
    try:
        from .ai_summarizer import generate_tldr
        return generate_tldr(content, report_type, language)
    except Exception as e:
        print(f"[Report] ⚠️ TL;DR 生成失败: {e}")
        return ""


def _insert_tldr(content, tldr, language):
    """将 TL;DR 插入报告头部（标题之后、正文之前）"""
    lines = content.split("\n")
    # 找到第一个 ## 或 --- 的位置，在它之前插入 TL;DR
    insert_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") or stripped == "---":
            insert_idx = i
            break
        if stripped.startswith("> "):
            insert_idx = i + 1
    # Safety: never insert before the # title line
    if insert_idx == 0:
        insert_idx = 1

    tldr_label = "## 📌 TL;DR"
    # Wrap TL;DR bullets as blockquote callout
    tldr_lines = tldr.strip().split("\n")
    tldr_blockquote = "\n".join(f"> {line}" for line in tldr_lines)
    tldr_block = [tldr_label, "", tldr_blockquote, "", "---", ""]

    new_lines = lines[:insert_idx] + tldr_block + lines[insert_idx:]
    return "\n".join(new_lines)


def build_non_ai_section(non_ai_articles, report_language="zh"):
    """Build Part II: non-AI tech news section.

    Reuses the existing table-based report format for articles
    that are not AI-related.

    Args:
        non_ai_articles: list of Article objects (non-AI)
        report_language: "zh" or "en"

    Returns:
        Markdown string for Part II
    """
    if not non_ai_articles:
        return ""

    count = len(non_ai_articles)

    if report_language == "zh":
        lines = [f"# Part II: 💻 科技动态 ({count} 条)"]
    else:
        lines = [f"# Part II: 💻 Tech Updates ({count} items)"]

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
        if source_cat not in groups:
            source_cat = "其他"
        groups[source_cat].append(update)

    # Output category tables
    count_unit = "条" if report_language == "zh" else "items"
    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue

        cat_display = get_category_display(cat)
        lines.append(f"## {cat_display} ({len(cat_updates)} {count_unit})")
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
            summary_text = ""
            if update.description:
                clean_desc = re.sub(r'<[^>]+>', '', update.description.strip())
                if len(clean_desc) > 150:
                    clean_desc = clean_desc[:150] + "..."
                summary_text = clean_desc
            lines.append(_article_table_row(i, update.title, update.url, update.source, summary_text))

        lines.append("")

    # Hacker News
    if hn_items:
        hn_label = "Hacker News 热门" if report_language == "zh" else "Hacker News Trending"
        lines.append(f"## {hn_label} ({len(hn_items)} {count_unit})")
        lines.append("")

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
            lines.append(f"| {i} | {title_cell} | {_escape_pipe(stats_str)} |")

        lines.append("")

    return "\n".join(lines)
