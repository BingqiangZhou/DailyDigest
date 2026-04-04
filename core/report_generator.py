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
        # 头部
        if report_language == "zh":
            lines.append(f"# AI 科技日报 - {report_date}")
            lines.append("")
            total_articles = (stats or {}).get("total_articles", 0)
            total_categories = len(category_results)
            lines.append(f"> 📰 {total_articles} 篇文章 | 📁 {total_categories} 个分类 | 🤖 AI 智能摘要")
        else:
            lines.append(f"# AI Tech Daily - {report_date}")
            lines.append("")
            total_articles = (stats or {}).get("total_articles", 0)
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
                title = article.title
                link = article.url
                source = article.source
                lang = article.language
                lang_tag = "🇨🇳" if lang == "zh" else "🇺🇸"

                lines.append(f"- [{lang_tag}] [{title}]({link}) — *{source}*")

            lines.append("")

        # 页脚
        footer = "报告生成时间" if report_language == "zh" else "Generated at"
        lines.append(f"*{footer}: {report_time} UTC*")

    else:
        # ---- Skill 模式：按 article 列表渲染 ----
        summary_map = summary_map or {}

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
            lines.append(f"## {cat_display} ({len(cat_updates)} " + ("条" if report_language == "zh" else "items") + ")")
            lines.append("")

            for update in cat_updates:
                title = update.title
                url = update.url
                source_name = update.source
                description = update.description

                ai_info = summary_map.get(url, {})
                ai_summary = ai_info.get("ai_summary", "")

                # 紧凑两行格式
                lines.append(f"- [{title}]({url}) — *{source_name}*")
                if ai_summary:
                    lines.append(f"  > {ai_summary}")
                elif description:
                    clean_desc = re.sub(r'<[^>]+>', '', description.strip())
                    if len(clean_desc) > 150:
                        clean_desc = clean_desc[:150] + "..."
                    lines.append(f"  > {clean_desc}")

            lines.append("")

        # Hacker News
        if hn_items:
            hn_label = "Hacker News 热门" if report_language == "zh" else "Hacker News Trending"
            lines.append(f"## {hn_label} ({len(hn_items)} " + ("条" if report_language == "zh" else "items") + ")")
            lines.append("")

            for item in hn_items:
                title = item.title
                url = item.url
                points = item.hn_points
                comments = item.hn_comments

                ai_info = summary_map.get(url, {})
                ai_summary = ai_info.get("ai_summary", "")

                stats_parts = []
                if points is not None:
                    stats_parts.append(f"🔥 {points}")
                if comments is not None:
                    stats_parts.append(f"💬 {comments}")
                stats_str = " | ".join(stats_parts)

                lines.append(f"- [{title}]({url})" + (f" — *{stats_str}*" if stats_str else ""))
                if ai_summary:
                    lines.append(f"  > {ai_summary}")

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

    tldr_label = "## 📌 TL;DR（太长不看）" if language == "zh" else "## 📌 TL;DR (Too Long; Didn't Read)"
    tldr_block = [tldr_label, "", tldr, "", "---", ""]

    new_lines = lines[:insert_idx] + tldr_block + lines[insert_idx:]
    return "\n".join(new_lines)
