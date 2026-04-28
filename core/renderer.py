"""Markdown rendering for DailyDigest reports.

Handles formatting for both API and Skill mode: tiered category sections,
HN tables, highlights, briefing markdown, and heading manipulation.
"""

import re
from datetime import datetime, timezone

from .logging_config import get_logger
from .briefing import _is_language_compatible, _clean_description_for_display
from .config import REPORT_MAX_THEMES, REPORT_ARTICLES_PER_THEME, REPORT_BRIEF_ITEMS_CAP, REPORT_HIGHLIGHTS_COUNT

logger = get_logger("renderer")


# ---------------------------------------------------------------------------
# Heading utilities
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Table row helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# HN table rendering
# ---------------------------------------------------------------------------

def _render_hn_table(hn_items, count_unit, summary_map=None):
    """Render a Hacker News trending section as Markdown table.

    Args:
        hn_items: list of Article objects with hn_points/hn_comments in extra
        count_unit: unit word for count (e.g. "条")
        summary_map: optional dict mapping url -> {"ai_summary": str}; if
                     provided, a Summary column is added.

    Returns:
        list[str] of Markdown lines (empty list if no items).
    """
    if not hn_items:
        return []

    summary_map = summary_map or {}
    has_summary = any(summary_map.get(item.url, {}).get("ai_summary") for item in hn_items)

    hn_label = "Hacker News 热门"
    lines = [f"### {hn_label} ({len(hn_items)} {count_unit})", ""]

    if has_summary:
        lines.append("| # | 文章 | 热度 | 摘要 |")
        lines.append("|---:|------|------|------|")
    else:
        lines.append("| # | 文章 | 热度 |")
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


# ---------------------------------------------------------------------------
# Highlights and tiered category rendering
# ---------------------------------------------------------------------------

def _render_today_highlights(category_results):
    """Render the 🔥 今日重点 section from top must_reads across categories."""
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
    lines.append("### 🔥 今日重点")
    lines.append("")

    for i, (article, summary) in enumerate(top, 1):
        title = article.title.replace("|", "\\|").replace("\n", " ")
        url = article.url.replace("|", "\\|")
        source = article.source.replace("|", "\\|")
        lines.append(f"#### ⭐ [{title}]({url})")
        lines.append("")
        lines.append(f"> **来源**: *{source}* | **为什么重要**: {summary}")
        lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _render_tiered_category(name, articles, tiered):
    """Render a single category with three visual tiers: must_read / noteworthy / brief."""
    if not articles:
        return ""

    lines = []
    count = len(articles)
    count_unit = "篇"
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
        lines.append("#### ⭐ 必读")
        lines.append("")
        for i, (article, summary) in enumerate(must_reads, 1):
            title = article.title.replace("|", "\\|").replace("\n", " ")
            url = article.url.replace("|", "\\|")
            source = article.source.replace("|", "\\|")
            lines.append(f"**{i}. [{title}]({url})**")
            lines.append("")
            if summary:
                lines.append(f"> **来源**: *{source}* | **为什么重要**: {summary}")
            else:
                lines.append(f"> **来源**: *{source}*")
            lines.append("")

    # 📰 Noteworthy section
    if noteworthies:
        lines.append("#### 📰 值得关注")
        lines.append("")
        lines.append("| # | 文章 | 来源 | 要点 |")
        lines.append("|---|---------|--------|-----------|")
        for i, (article, summary) in enumerate(noteworthies, 1):
            title_cell = f"[**{_escape_pipe(article.title)}**]({_escape_pipe(article.url)})"
            source_cell = f"*{_escape_pipe(article.source)}*"
            lines.append(f"| {i} | {title_cell} | {source_cell} | {_escape_pipe(summary)} |")
        lines.append("")

    # 📋 Brief section (collapsed)
    if briefs:
        lines.append("<details>")
        lines.append(f"<summary>📋 简讯 ({len(briefs)} {count_unit})</summary>")
        lines.append("")
        lines.append("| # | 文章 | 来源 |")
        lines.append("|---|---------|--------|")
        for i, article in enumerate(briefs, 1):
            lines.append(_article_table_row(i, article.title, article.url, article.source))
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Briefing merge and rendering
# ---------------------------------------------------------------------------

def _merge_llm_briefing(briefing_data, llm_briefing):
    """Overlay LLM-generated highlights and theme summaries onto briefing_data."""
    if not llm_briefing:
        return briefing_data

    merged = dict(briefing_data)
    if llm_briefing.get("highlights"):
        filtered_highlights = [
            item.strip() for item in llm_briefing["highlights"]
            if isinstance(item, str) and _is_language_compatible(item)
        ]
        if filtered_highlights:
            merged["highlights"] = filtered_highlights

    theme_summaries = llm_briefing.get("theme_summaries", {})
    if theme_summaries:
        themes = []
        for idx, theme in enumerate(briefing_data.get("themes", []), 1):
            summary = theme_summaries.get(theme.get("id")) or theme_summaries.get(str(idx))
            if summary and _is_language_compatible(summary):
                updated = dict(theme)
                updated["summary"] = summary
                themes.append(updated)
            else:
                themes.append(theme)
        merged["themes"] = themes

    if llm_briefing.get("trends"):
        filtered_trends = [
            item.strip() for item in llm_briefing["trends"]
            if isinstance(item, str) and _is_language_compatible(item)
        ]
        if filtered_trends:
            merged["trends"] = filtered_trends
    return merged


def _render_briefing_markdown(briefing_data, now):
    """Render briefing_data into the default markdown daily digest."""
    date_str = now.strftime("%Y-%m-%d")
    stats = briefing_data.get("stats", {})
    highlights = briefing_data.get("highlights", [])[:REPORT_HIGHLIGHTS_COUNT]
    themes = briefing_data.get("themes", [])[:REPORT_MAX_THEMES]
    featured_tech = briefing_data.get("featured_tech", [])
    brief_items = briefing_data.get("brief_items", [])[:REPORT_BRIEF_ITEMS_CAP]
    trends = briefing_data.get("trends", [])

    lines = [
        f"# 📰 DailyDigest — {date_str}",
        "",
        f"> 扫描 {stats.get('candidate_count', 0)} 篇候选内容 · 覆盖 {stats.get('source_count', 0)} 个信息源 · 纳入 {stats.get('included_count', 0)} 篇",
        "",
        "---",
        "",
    ]
    tldr = briefing_data.get("tldr", "")
    if tldr:
        lines.extend(["## 🎯 今日速览", ""])
        lines.append(tldr)
        lines.extend(["", "---", ""])
    if highlights:
        lines.extend(["## 📌 今日要点", ""])
        for item in highlights:
            lines.append(f"- {item}")
        lines.extend(["", "---", ""])

    if themes:
        lines.extend(["## 🧭 今日动态", ""])
        numerals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                     "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"]
        for idx, theme in enumerate(themes, 1):
            prefix = numerals[idx - 1] if idx - 1 < len(numerals) else str(idx)
            title = theme.get("title", "")
            size_info = ""
            cluster_size = theme.get("cluster_size") or len(theme.get("articles", []))
            source_count = theme.get("source_count", 0)
            if cluster_size > 1:
                parts = [f"{cluster_size} 篇"]
                if source_count > 1:
                    parts.append(f"来自 {source_count} 个来源")
                size_info = f"  ({' · '.join(parts)})"
            cross_tag = " 🔥" if theme.get("cross_source") else ""
            lines.append(f"### {prefix}、{title}{cross_tag}{size_info}")
            lines.append("")
            lines.append(theme.get("summary", ""))
            lines.append("")
            articles = theme.get("articles", [])[:REPORT_ARTICLES_PER_THEME]
            if articles:
                link_parts = []
                for i, article in enumerate(articles):
                    source = f"（{article.source}）" if article.source else ""
                    heat = ""
                    if article.hn_points:
                        heat = f" · HN {article.hn_points}"
                    link_parts.append(f"[{article.title}]({article.url}){source}{heat}")
                links_str = "、".join(link_parts)
                lines.append(f"> 📎 相关：{links_str}")
            lines.append("")
            if idx < len(themes):
                lines.extend(["---", ""])

    notable_singletons = briefing_data.get("notable_singletons", [])
    if notable_singletons:
        lines.extend(["## 👀 值得关注", ""])
        for article in notable_singletons:
            source = f" — *{article.source}*" if article.source else ""
            desc = _clean_description_for_display(article.description, max_len=80)
            lines.append(f"- **[{article.title}]({article.url})**{source}")
            if desc and _is_language_compatible(desc):
                lines.append(f"  > {desc}")
            lines.append("")
        lines.extend(["---", ""])

    if featured_tech:
        lines.extend(["## ⭐ 重点科技新闻", ""])
        for article in featured_tech:
            source = f" — *{article.source}*" if article.source else ""
            desc = _clean_description_for_display(article.description, max_len=100)
            lines.append(f"- **[{article.title}]({article.url})**{source}")
            if desc and _is_language_compatible(desc):
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
    ])
    if "theme_count" in stats:
        lines.extend([
            f"| 主题分组 | {stats['theme_count']} |",
            f"| 独立条目 | {len(brief_items)} |",
        ])
    else:
        lines.extend([
            f"| AI 主题 | {stats.get('ai_count', 0)} |",
            f"| 科技简讯 | {len(brief_items)} |",
        ])
    lines.extend([
        f"| 信息源数量 | {stats.get('source_count', 0)} |",
    ])
    if stats.get("cluster_count"):
        lines.append(f"| 主题聚类 | {stats.get('cluster_count', 0)} |")
        lines.append(f"| 跨源话题 | {stats.get('cross_source_count', 0)} |")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Podcast briefing rendering
# ---------------------------------------------------------------------------

def _render_podcast_episode_table(articles):
    """Render the full-update table for podcast episodes."""
    if not articles:
        return []

    lines = [
        "## 全部更新",
        "",
        "| # | 节目 | 播客 | 排名 | 摘要 |",
        "|---:|------|------|------|------|",
    ]
    for i, article in enumerate(articles, 1):
        title_cell = f"[**{_escape_pipe(article.title)}**]({_escape_pipe(article.url)})"
        source_cell = f"*{_escape_pipe(article.source)}*"
        desc = _clean_description_for_display(article.description, max_len=60)
        summary_cell = _escape_pipe(desc) if desc else ""
        rank_val = article.extra.get("rank", 0)
        rank_str = f"#{rank_val}" if rank_val > 0 else "-"
        lines.append(f"| {i} | {title_cell} | {source_cell} | {rank_str} | {summary_cell} |")
    lines.append("")
    return lines


def _render_podcast_briefing_markdown(briefing_data, now):
    """Render briefing_data into a podcast-specific markdown daily digest."""
    date_str = now.strftime("%Y-%m-%d")
    stats = briefing_data.get("stats", {})
    highlights = briefing_data.get("highlights", [])[:REPORT_HIGHLIGHTS_COUNT]
    themes = briefing_data.get("themes", [])[:REPORT_MAX_THEMES]
    brief_items = briefing_data.get("brief_items", [])[:REPORT_BRIEF_ITEMS_CAP]
    notable_singletons = briefing_data.get("notable_singletons", [])

    lines = [
        f"# 🎙️ AI 播客日报 — {date_str}",
        "",
        f"> 扫描 {stats.get('source_count', 0)} 个播客 · "
        f"共 {stats.get('candidate_count', 0)} 条更新 · "
        f"筛选后 {stats.get('included_count', 0)} 条",
        "",
        "---",
        "",
    ]

    tldr = briefing_data.get("tldr", "")
    if tldr:
        lines.extend(["## TL;DR", ""])
        lines.append(tldr)
        lines.extend(["", "---", ""])

    if highlights:
        lines.extend(["## 今日要点", ""])
        for item in highlights:
            lines.append(f"- {item}")
        lines.extend(["", "---", ""])

    if themes:
        lines.extend(["## 今日热点主题", ""])
        for idx, theme in enumerate(themes, 1):
            title = theme.get("title", "")
            lines.append(f"### 主题 {idx}: {title}")
            lines.append("")
            theme_articles = theme.get("articles", [])
            for article in theme_articles:
                source = f" — {article.source}" if article.source else ""
                lines.append(f"- 🎧 [{article.title}]({article.url}){source}")
                desc = _clean_description_for_display(article.description, max_len=100)
                if desc and _is_language_compatible(desc):
                    lines.append(f"  > {desc}")
                lines.append("")
            summary = theme.get("summary", "")
            if summary:
                lines.append(f"> {summary}")
                lines.append("")
            lines.extend(["---", ""])

    if notable_singletons:
        lines.extend(["## 值得关注的单集", ""])
        for article in notable_singletons:
            source = f" — {article.source}" if article.source else ""
            desc = _clean_description_for_display(article.description, max_len=80)
            lines.append(f"- 🎧 [{article.title}]({article.url}){source}")
            if desc and _is_language_compatible(desc):
                lines.append(f"  > {desc}")
            lines.append("")
        lines.extend(["---", ""])

    # Build full-update table from all articles
    all_articles = []
    seen_urls = set()
    for theme in themes:
        for article in theme.get("articles", []):
            if article.url not in seen_urls:
                all_articles.append(article)
                seen_urls.add(article.url)
    for article in notable_singletons:
        if article.url not in seen_urls:
            all_articles.append(article)
            seen_urls.add(article.url)
    for article in brief_items:
        if article.url not in seen_urls:
            all_articles.append(article)
            seen_urls.add(article.url)

    table_lines = _render_podcast_episode_table(all_articles)
    lines.extend(table_lines)

    return "\n".join(lines).strip()
