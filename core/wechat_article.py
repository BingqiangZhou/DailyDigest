"""
公众号文章 Markdown 渲染模块

参考 linux.do 日报格式：今日亮点（要点列表）+ 新内容（编号主题，每主题含综合摘要 + 参考文章）。
"""

import re
from collections import OrderedDict
from datetime import datetime, timezone

from .config import (
    CATEGORY_ORDER, get_category_display, normalize_category,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_wechat_article(
    ai_articles,
    non_ai_articles,
    now,
    language="zh",
    category_results=None,
    executive_summary="",
    summary_map=None,
    cluster_map=None,
    ai_structure=None,
):
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    ai_count = len(ai_articles) if ai_articles else 0
    non_ai_count = len(non_ai_articles) if non_ai_articles else 0
    total = ai_count + non_ai_count

    # Render
    lines = []
    lines.append(_render_header(date_str, time_str, ai_count, non_ai_count, total, language))
    lines.append("")

    if ai_structure:
        # AI-generated structure path
        highlights = ai_structure.get("highlights", [])
        themes = ai_structure.get("themes", [])

        if highlights:
            lines.append(_render_ai_highlights(highlights, language))
            lines.append("")

        if themes:
            lines.append(_render_ai_themes(themes, language))
            lines.append("")
    else:
        # Fallback: category-based structure
        all_items = _collect_items(category_results, summary_map, ai_articles)
        highlights = [it for it in all_items if it["tier"] == "must_read"]
        themes = _group_into_themes(all_items, language)

        if highlights:
            lines.append(_render_highlights(highlights, language))
            lines.append("")

        if themes:
            lines.append(_render_themes(themes, language))
            lines.append("")

    if non_ai_articles:
        lines.append(_render_tech_brief(non_ai_articles, language))
        lines.append("")

    lines.append(_render_footer(date_str, time_str, language))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _collect_items(category_results, summary_map, ai_articles):
    """Flatten all articles into a list of dicts with tier/summary metadata."""
    items = []
    seen = set()

    if category_results:
        for cat, data in category_results.items():
            articles = data.get("articles", [])
            tiered = data.get("tiered", {})
            cat_name = data.get("name", "")

            must_idx = {it["index"]: it.get("summary", "") for it in tiered.get("must_read", [])}
            note_idx = {it["index"]: it.get("summary", "") for it in tiered.get("noteworthy", [])}
            brief_idx = set(tiered.get("brief", []))

            for i, a in enumerate(articles, 1):
                if a.url in seen:
                    continue
                seen.add(a.url)

                if i in must_idx:
                    tier, summary = "must_read", must_idx[i]
                elif i in note_idx:
                    tier, summary = "noteworthy", note_idx[i]
                elif i in brief_idx:
                    tier, summary = "brief", ""
                else:
                    tier, summary = "noteworthy", ""

                items.append({
                    "article": a,
                    "tier": tier,
                    "summary": summary,
                    "category": cat,
                    "cat_name": cat_name,
                })

    # Fallback: use summary_map directly
    if not items and summary_map:
        for a in ai_articles:
            if a.url in seen:
                continue
            seen.add(a.url)
            info = summary_map.get(a.url, {})
            if isinstance(info, dict):
                tier = info.get("tier", "noteworthy")
                summary = info.get("importance_reason", "") or info.get("ai_summary", "")
            else:
                tier, summary = "noteworthy", str(info)
            items.append({
                "article": a,
                "tier": tier,
                "summary": summary,
                "category": normalize_category(a.category),
                "cat_name": get_category_display(normalize_category(a.category)),
            })

    # Last fallback: no summaries at all
    if not items:
        for a in (ai_articles or []):
            items.append({
                "article": a,
                "tier": "noteworthy",
                "summary": a.description or "",
                "category": normalize_category(a.category),
                "cat_name": get_category_display(normalize_category(a.category)),
            })

    return items


# ---------------------------------------------------------------------------
# Theme grouping
# ---------------------------------------------------------------------------

def _group_into_themes(items, language):
    """Group items into 3-6 broad themes by category proximity.

    Each theme: {title, summary, refs: [article, ...]}
    """
    # Separate must_reads and others
    must_reads = [it for it in items if it["tier"] == "must_read"]
    others = [it for it in items if it["tier"] != "must_read"]

    # Group must_reads by category
    cat_groups = OrderedDict()
    for it in must_reads:
        cat_groups.setdefault(it["category"], []).append(it)

    # Also group others by category
    cat_others = OrderedDict()
    for it in others:
        cat_others.setdefault(it["category"], []).append(it)

    # Merge small adjacent groups to avoid too many themes
    themes = []
    used_cats = set()

    for cat, group in cat_groups.items():
        if cat in used_cats:
            continue
        used_cats.add(cat)

        # Collect all articles for this theme (must_reads + others in same cat)
        theme_articles = [it["article"] for it in group]
        theme_summaries = [it["summary"] for it in group if it["summary"]]

        # Add noteworthy/brief from same category
        for oit in cat_others.get(cat, []):
            theme_articles.append(oit["article"])
            if oit["summary"]:
                theme_summaries.append(oit["summary"])

        # Compose theme title from category name
        cat_name = group[0]["cat_name"] or get_category_display(cat)
        # Clean emoji from cat_name for the theme title
        title = re.sub(r'^[\U0001F300-\U0010FFFF\s]+', '', cat_name).strip()

        # Compose summary paragraph: join individual summaries
        summary = _compose_summary(theme_summaries)

        themes.append({
            "title": title,
            "summary": summary,
            "refs": theme_articles,
        })

    # Add noteworthy-only categories as themes
    for cat, group in cat_others.items():
        if cat in used_cats:
            continue
        articles = [it["article"] for it in group]
        summaries = [it["summary"] for it in group if it["summary"]]
        if not articles:
            continue

        cat_name = group[0]["cat_name"] or get_category_display(cat)
        title = re.sub(r'^[\U0001F300-\U0010FFFF\s]+', '', cat_name).strip()
        summary = _compose_summary(summaries)

        themes.append({
            "title": title,
            "summary": summary,
            "refs": articles,
        })

    # If we have too many themes (>6), merge the smallest ones
    if len(themes) > 6:
        themes = _merge_small_themes(themes)

    return themes


def _compose_summary(summaries):
    """Join multiple individual summaries into one paragraph."""
    # Deduplicate and clean
    seen = set()
    parts = []
    for s in summaries:
        s = s.strip()
        # Remove "为什么重要：" prefix
        s = re.sub(r'^为什么重要[：:]\s*', '', s)
        s = re.sub(r'^Why it matters:\s*', '', s)
        if s and s not in seen:
            seen.add(s)
            parts.append(s)
    return "\n\n".join(parts) if parts else ""


def _merge_small_themes(themes):
    """Merge themes with few refs into a catch-all."""
    main = []
    catchall_refs = []
    catchall_summaries = []

    for t in themes:
        if len(t["refs"]) >= 2 or t["summary"]:
            main.append(t)
        else:
            catchall_refs.extend(t["refs"])
            if t["summary"]:
                catchall_summaries.append(t["summary"])

    if catchall_refs:
        main.append({
            "title": "其他 AI 动态",
            "summary": _compose_summary(catchall_summaries),
            "refs": catchall_refs,
        })

    return main


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_header(date_str, time_str, ai_count, non_ai_count, total, language):
    if language == "zh":
        return "\n".join([
            "# DailyDigest 人工智能技术日报",
            "",
            f"> {date_str} · 共 {total} 篇 · AI 自动生成",
            "",
            "---",
        ])
    return "\n".join([
        "# DailyDigest AI Technology Daily",
        "",
        f"> {date_str} · {total} articles · AI generated",
        "",
        "---",
    ])


def _render_highlight_list(items, language):
    """Render '今日要点' as a bullet list of strings."""
    label = "今日要点" if language == "zh" else "Highlights"
    lines = [f"## {label}", ""]
    for item in items:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _render_highlights(highlights, language):
    """Render highlights from category-based items (extracts article titles)."""
    items = [it["article"].title for it in highlights]
    return _render_highlight_list(items, language)


def _render_ai_highlights(highlights, language):
    """Render AI-generated highlights (already strings)."""
    return _render_highlight_list(highlights, language)


def _render_theme_list(themes, language, articles_key="refs"):
    """Render numbered theme sections: N. Title / summary / article refs."""
    label = "深度解读" if language == "zh" else "In Depth"
    lines = [f"## {label}", ""]

    for i, theme in enumerate(themes, 1):
        title = theme.get("title", "")
        summary = theme.get("summary", "")
        articles = theme.get(articles_key, [])

        lines.append(f"### {i}. {title}")
        lines.append("")

        if summary:
            lines.append(summary)
            lines.append("")

        if articles:
            ref_label = "参考" if language == "zh" else "Refs"
            lines.append(f"**{ref_label}：**")
            lines.append("")
            for a in articles:
                source = f" — *{a.source}*" if a.source else ""
                lines.append(f"- [{a.title}]({a.url}){source}")
            lines.append("")

        if i < len(themes):
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def _render_themes(themes, language):
    """Render themes with 'refs' key (fallback category-based path)."""
    return _render_theme_list(themes, language, articles_key="refs")


def _render_ai_themes(themes, language):
    """Render themes with 'articles' key (AI-generated path)."""
    return _render_theme_list(themes, language, articles_key="articles")


def _render_tech_brief(non_ai_articles, language):
    """Compact section for non-AI tech news."""
    label = "科技动态" if language == "zh" else "Tech Updates"
    lines = [f"## {label}", ""]

    for a in non_ai_articles[:20]:
        source = f" — *{a.source}*" if a.source else ""
        lines.append(f"- [{a.title}]({a.url}){source}")

    remaining = len(non_ai_articles) - 20
    if remaining > 0:
        lines.append(f"- ……等 {remaining} 条")

    lines.append("")
    return "\n".join(lines)


def _render_footer(date_str, time_str, language):
    if language == "zh":
        return "---\n\n*DailyDigest 人工智能技术日报 · AI 自动生成 · 每日更新*\n"
    return "---\n\n*DailyDigest AI Technology Daily · AI generated · Daily updates*\n"
