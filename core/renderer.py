"""Markdown rendering for DailyDigest reports.

Handles formatting for both API and Skill mode: tiered category sections,
HN tables, highlights, briefing markdown, and heading manipulation.
"""

import re
from datetime import datetime, timezone

from .logging_config import get_logger
from .briefing import _is_language_compatible, _clean_description_for_display

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


# ---------------------------------------------------------------------------
# Highlights and tiered category rendering
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Briefing merge and rendering
# ---------------------------------------------------------------------------

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
