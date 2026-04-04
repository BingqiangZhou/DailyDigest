"""
Markdown 报告生成模块
统一输出 Markdown 格式的日报报告。
支持科技日报、播客日报、微信日报三种类型。
"""

import json
import os
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    OUTPUT_DIR, CATEGORY_ORDER, get_category_display,
    normalize_category,
)


def generate_tech_report(updates, summary_map=None, trend_insight=None,
                         stats=None, report_language="zh"):
    """生成科技日报 Markdown 报告

    Args:
        updates: list of article dicts（来自 rss_fetcher）
        summary_map: dict, url -> {ai_summary, category}（来自 AI 摘要）
        trend_insight: dict with "trend_insight" key
        stats: dict with metadata
        report_language: "zh" or "en"

    Returns:
        str: Markdown 报告内容
    """
    summary_map = summary_map or {}
    now = datetime.now(timezone.utc)
    report_date = now.strftime("%Y-%m-%d")
    report_time = now.strftime("%Y-%m-%d %H:%M")

    lines = []

    # 头部
    if report_language == "zh":
        lines.append(f"# AI 科技日报 - {report_date}")
        lines.append("")
        checked = (stats or {}).get("checked_count", (stats or {}).get("total_feeds", 0))
        hours = (stats or {}).get("hours", 24)
        update_count = len(updates)
        lines.append(f"> 共检查 {checked} 个信息源，时间范围 {hours} 小时，发现 {update_count} 条更新")
    else:
        lines.append(f"# AI Tech Daily - {report_date}")
        lines.append("")
        checked = (stats or {}).get("checked_count", (stats or {}).get("total_feeds", 0))
        hours = (stats or {}).get("hours", 24)
        update_count = len(updates)
        lines.append(f"> Checked {checked} sources, {hours}h window, found {update_count} updates")

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
        source_cat = normalize_category(update.get("source_category", ""))
        if source_cat == "hacker_news":
            hn_items.append(update)
            continue

        # 检查 AI 是否重新分类
        url = update.get("url", "")
        ai_info = summary_map.get(url, {})
        ai_cat = ai_info.get("category", "")
        final_cat = normalize_category(ai_cat) if ai_cat else source_cat
        if final_cat not in groups:
            final_cat = "其他"
        groups[final_cat].append(update)

    # 输出各分类
    article_index = 0
    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue

        cat_display = get_category_display(cat)
        lines.append(f"## {cat_display} ({len(cat_updates)} " + ("条" if report_language == "zh" else "items") + ")")
        lines.append("")

        for update in cat_updates:
            article_index += 1
            title = update.get("title", "(no title)")
            url = update.get("url", "")
            source_name = update.get("source_name", "Unknown")
            pub_date = update.get("published", "")
            description = update.get("description", "")

            ai_info = summary_map.get(url, {})
            ai_summary = ai_info.get("ai_summary", "")

            lines.append(f"### {article_index}. {title}")
            lines.append("")
            lines.append(f"**{'来源' if report_language == 'zh' else 'Source'}**: {source_name} | **{'发布时间' if report_language == 'zh' else 'Published'}**: {pub_date}")
            lines.append("")
            if url:
                lines.append(f"**{'链接' if report_language == 'zh' else 'Link'}**: {url}")
                lines.append("")
            if ai_summary:
                lines.append(f"**AI {'摘要' if report_language == 'zh' else 'Summary'}**: {ai_summary}")
                lines.append("")
            elif description:
                fallback = description[:200] + ("..." if len(description) > 200 else "")
                lines.append(f"**{'摘要' if report_language == 'zh' else 'Summary'}**: {fallback}")
                lines.append("")
            lines.append("---")
            lines.append("")

    # Hacker News
    if hn_items:
        hn_label = "Hacker News 热门" if report_language == "zh" else "Hacker News Trending"
        lines.append(f"## {hn_label} ({len(hn_items)} " + ("条" if report_language == "zh" else "items") + ")")
        lines.append("")

        for item in hn_items:
            article_index += 1
            title = item.get("title", "(no title)")
            url = item.get("url", "")
            points = item.get("hn_points")
            comments = item.get("hn_comments")

            ai_info = summary_map.get(url, {})
            ai_summary = ai_info.get("ai_summary", "")

            stats_parts = []
            if points is not None:
                stats_parts.append(f"points: {points}")
            if comments is not None:
                stats_parts.append(f"comments: {comments}")
            stats_str = ", ".join(stats_parts)

            lines.append(f"### {article_index}. {title}")
            lines.append("")
            if stats_str:
                lines.append(f"**{'热度' if report_language == 'zh' else 'Stats'}**: {stats_str}")
                lines.append("")
            if url:
                lines.append(f"**{'链接' if report_language == 'zh' else 'Link'}**: {url}")
                lines.append("")
            if ai_summary:
                lines.append(f"**AI {'摘要' if report_language == 'zh' else 'Summary'}**: {ai_summary}")
                lines.append("")
            lines.append("---")
            lines.append("")

    # 页脚
    footer_prefix = "报告生成时间" if report_language == "zh" else "Report generated at"
    lines.append(f"*{footer_prefix}: {report_time} UTC*")

    return "\n".join(lines)


def generate_category_report(category_results, executive_summary, stats,
                             report_language="zh"):
    """从 AI 摘要结果生成 Markdown 报告（GitHub Actions 模式）

    Args:
        category_results: dict, category -> {name, summary, article_count, articles}
        executive_summary: str, 执行摘要
        stats: dict, 统计信息
        report_language: "zh" or "en"

    Returns:
        str: Markdown 报告内容
    """
    now = datetime.now(timezone.utc)
    report_date = now.strftime("%Y-%m-%d")
    report_time = now.strftime("%Y-%m-%d %H:%M")

    lines = []

    # 头部
    if report_language == "zh":
        lines.append(f"# AI 科技日报 - {report_date}")
        lines.append("")
        total_articles = stats.get("total_articles", 0)
        total_categories = len(category_results)
        lines.append(f"> 📰 {total_articles} 篇文章 | 📁 {total_categories} 个分类 | 🤖 AI 智能摘要")
    else:
        lines.append(f"# AI Tech Daily - {report_date}")
        lines.append("")
        total_articles = stats.get("total_articles", 0)
        total_categories = len(category_results)
        lines.append(f"> 📰 {total_articles} articles | 📁 {total_categories} categories | 🤖 AI-powered")

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

        lines.append(f"## {name} ({data.get('article_count', 0)} " + ("篇" if report_language == "zh" else "articles") + ")")
        lines.append("")
        lines.append(summary)
        lines.append("")
        lines.append("---")
        lines.append("")

        # 文章列表
        for article in articles:
            title = article.get("title", "")
            link = article.get("url", article.get("link", ""))
            source = article.get("source_name", article.get("source", ""))
            lang = article.get("language", "en")
            lang_tag = "🇨🇳" if lang == "zh" else "🇺🇸"

            lines.append(f"- [{lang_tag}] [{title}]({link}) — *{source}*")

        lines.append("")

    # 页脚
    footer = "报告生成时间" if report_language == "zh" else "Generated at"
    lines.append(f"*{footer}: {report_time} UTC*")

    return "\n".join(lines)


def save_report(content, filename, output_dir=None):
    """保存报告到文件

    Args:
        content: str, Markdown 内容
        filename: str, 文件名（如 tech-daily_14-30.md）
        output_dir: Path, 输出目录

    Returns:
        Path: 保存的文件路径
    """
    output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[Report] ✅ 报告已保存: {filepath}")
    return filepath
