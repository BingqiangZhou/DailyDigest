"""
AI deep analysis report generator.
Produces the Part I (AI deep digest) section of the unified report.
"""

import os
import re

from .article import Article, format_article_item
from .config import AI_DEEP_ANALYSIS_PROMPT_ZH, AI_DEEP_ANALYSIS_PROMPT_EN
from .logging_config import get_logger

logger = get_logger("ai_report")


def _select_articles_for_analysis(articles: list[Article], max_count: int) -> list[Article]:
    """Select articles for deep analysis, prioritizing must_read > noteworthy > brief.

    Returns up to max_count articles, preserving the most editorially important ones.
    """
    if len(articles) <= max_count:
        return articles

    must_read = [a for a in articles if a.extra.get("editorial_tier") == "must_read"]
    noteworthy = [a for a in articles if a.extra.get("editorial_tier") == "noteworthy"]
    brief = [a for a in articles if a.extra.get("editorial_tier") == "brief"]
    unclassified = [a for a in articles if not a.extra.get("editorial_tier")]

    # Sort each tier by news value score descending
    for tier_list in (must_read, noteworthy, brief, unclassified):
        tier_list.sort(key=lambda a: a.extra.get("news_value_score", 0), reverse=True)

    selected = []
    for tier in (must_read, noteworthy, unclassified, brief):
        remaining = max_count - len(selected)
        if remaining <= 0:
            break
        selected.extend(tier[:remaining])

    return selected


def _format_articles_for_deep_analysis(articles: list[Article],
                                        cluster_map: dict = None) -> str:
    """Format articles for the deep analysis prompt.

    When cluster_map is provided, articles belonging to the same topic
    cluster are annotated with [CLUSTER: N篇关于"theme"] markers.

    When editorial tier data is present, articles are grouped into
    tiered sections (must_read / noteworthy / brief) so the LLM
    receives explicit editorial hierarchy signals.
    """
    has_editorial = any(a.extra.get("editorial_tier") for a in articles)

    if has_editorial:
        return _format_articles_tiered(articles, cluster_map)

    return _format_articles_flat(articles, cluster_map)


def _format_articles_flat(articles: list[Article], cluster_map: dict = None) -> str:
    """Format articles as a flat numbered list (original behavior)."""
    lines = []
    for i, article in enumerate(articles, 1):
        cluster_info = (cluster_map or {}).get(article.url)
        if cluster_info and cluster_info.get("cluster_size", 1) > 1:
            lines.append(f"[CLUSTER: {cluster_info['cluster_size']}篇关于\"{cluster_info['theme']}\"]")

        item_lines = format_article_item(article, i, desc_limit=300, include_source_type=True)
        lines.extend(item_lines)

        # Cap full text per article to control prompt size
        tier = article.extra.get("editorial_tier", "noteworthy")
        if tier == "must_read":
            full_limit = 2000
        elif tier == "noteworthy":
            full_limit = 800
        else:
            full_limit = 200
        full = (article.full_text or "")[:full_limit]
        if full:
            lines.append(f"   正文片段: {full}")
        lines.append("")
    return "\n".join(lines)


def _format_articles_tiered(articles: list[Article], cluster_map: dict = None) -> str:
    """Format articles grouped by editorial tier for the LLM.

    Tiered formatting to control prompt size:
    - must_read: full detail (title + desc + full_text up to 2000 chars)
    - noteworthy: title + description only (no full text)
    - brief: title + source only, as compact list
    """
    must_read = [a for a in articles if a.extra.get("editorial_tier") == "must_read"]
    noteworthy = [a for a in articles if a.extra.get("editorial_tier") == "noteworthy"]
    brief = [a for a in articles if a.extra.get("editorial_tier") == "brief"]

    sections = []
    idx = 1

    if must_read:
        sections.append("=== ⭐ 必读 (Must Read) — 最重要的 AI 动态 ===")
        for article in must_read:
            sections.extend(_format_single_article(article, idx, cluster_map))
            idx += 1
        sections.append("")

    if noteworthy:
        sections.append("=== 📰 值得关注 (Noteworthy) — 重要更新与研究 ===")
        for article in noteworthy:
            lines = []
            cluster_info = (cluster_map or {}).get(article.url)
            if cluster_info and cluster_info.get("cluster_size", 1) > 1:
                lines.append(f"[CLUSTER: {cluster_info['cluster_size']}篇关于\"{cluster_info['theme']}\"]")
            lines.extend(format_article_item(article, idx, desc_limit=300, include_source_type=True))
            lines.append("")
            sections.extend(lines)
            idx += 1
        sections.append("")

    if brief:
        sections.append("=== 📋 简讯 (Brief) — 常规更新（仅列表，无需详细分析）===")
        for article in brief:
            engagement = ""
            if article.hn_points and article.hn_points > 0:
                engagement = f" (🔥 {article.hn_points})"
            sections.append(f"{idx}. [{article.title}]({article.url}) — {article.source}{engagement}")
            idx += 1
        sections.append("")

    return "\n".join(sections)


def _format_single_article(article: Article, index: int, cluster_map: dict = None) -> list[str]:
    """Format a single article with cluster annotation and full text."""
    lines = []
    cluster_info = (cluster_map or {}).get(article.url)
    if cluster_info and cluster_info.get("cluster_size", 1) > 1:
        lines.append(f"[CLUSTER: {cluster_info['cluster_size']}篇关于\"{cluster_info['theme']}\"]")

    item_lines = format_article_item(article, index, desc_limit=300, include_source_type=True)
    lines.extend(item_lines)

    full_text_len = len(article.full_text or "")
    full_limit = 2000 if full_text_len > 500 else 500
    full = (article.full_text or "")[:full_limit]
    if full:
        lines.append(f"   正文片段: {full}")
    lines.append("")
    return lines


def generate_ai_report(ai_articles: list[Article], language: str = "zh",
                       summary_map: dict = None, cluster_map: dict = None) -> str:
    """Generate Part I: AI deep analysis section.

    Uses the AI API to produce a deep analysis with hot topics,
    trend insights, and detailed coverage tables.

    Args:
        ai_articles: list of AI-relevant Article objects
        language: "zh" or "en"
        summary_map: optional dict url -> {ai_summary, ...} for Skill mode enrichment
        cluster_map: optional dict url -> {cluster_id, theme, score, ...} from topic_cluster

    Returns:
        Markdown string for the AI deep analysis section
    """
    if not ai_articles:
        return ""

    language = language or os.environ.get("REPORT_LANGUAGE", "zh")

    # If no API_KEY, generate a simple listing as fallback
    if not os.environ.get("API_KEY"):
        return _generate_ai_listing_fallback(ai_articles, language, summary_map=summary_map)

    from .llm import get_llm_client, chat_with_profile, generate_with_critique

    client = get_llm_client()

    # Cap articles sent to the LLM to prevent prompt overflow / timeout.
    # Prioritize by editorial tier: must_read > noteworthy > brief.
    max_articles = int(os.environ.get("DEEP_ANALYSIS_MAX_ARTICLES", "50"))
    analysis_articles = _select_articles_for_analysis(ai_articles, max_articles)

    articles_text = _format_articles_for_deep_analysis(analysis_articles, cluster_map=cluster_map)

    prompt_template = AI_DEEP_ANALYSIS_PROMPT_ZH if language == "zh" else AI_DEEP_ANALYSIS_PROMPT_EN
    prompt = prompt_template.format(articles=articles_text)

    logger.info(f"[AI Report] 🤖 Generating deep analysis for {len(analysis_articles)} AI articles "
                f"(from {len(ai_articles)} total, cap={max_articles})...")
    # Use multi-pass critique for the deep analysis (most prominent section)
    from config.prompts.critique import get_deep_analysis_critique
    critique_template = get_deep_analysis_critique(language)
    response = generate_with_critique(client, prompt, "deep_analysis", critique_template, language=language)

    if not response:
        logger.warning("[AI Report] ⚠️ Deep analysis failed, using listing fallback")
        return _generate_ai_listing_fallback(ai_articles, language, summary_map=summary_map)

    logger.info("[AI Report] ✅ Deep analysis generated")
    return response.strip()


def _generate_ai_listing_fallback(ai_articles: list[Article], language: str,
                                   summary_map: dict = None) -> str:
    """Structured fallback listing when AI API is unavailable.

    Produces a curated, grouped report even without LLM:
      - Highlights: top 5 articles by authority/signal
      - Grouped by category with descriptions as summaries
      - Collapsed overflow section for remaining articles
    """
    has_tiers = any(a.extra.get("editorial_tier") for a in ai_articles)
    if has_tiers:
        return _generate_ai_listing_tiered(ai_articles, language, summary_map)

    from .config import get_category_display, normalize_category
    from .topic_cluster import cluster_articles, get_cluster_map

    lines = []

    # Try topic clustering for better grouping (non-LLM, heuristic)
    try:
        topic_clusters = cluster_articles(ai_articles)
        cluster_map = get_cluster_map(topic_clusters)
    except Exception:
        cluster_map = {}

    # Rank articles by authority and description richness for highlights
    def _article_rank(a):
        score = 0
        if a.priority == 1:
            score += 3
        elif a.priority == 2:
            score += 1
        if a.description and len(a.description) > 50:
            score += 2
        if a.extra.get("hn_points") and a.extra["hn_points"] >= 100:
            score += 2
        if cluster_map.get(a.url, {}).get("cluster_size", 1) > 1:
            score += 1
        return score

    sorted_articles = sorted(ai_articles, key=_article_rank, reverse=True)

    # Highlights: top 5 most important articles
    highlights = sorted_articles[:5]
    if highlights:
        if language == "zh":
            lines.append("### 🔥 今日亮点")
        else:
            lines.append("### 🔥 Today's Highlights")
        lines.append("")
        for i, article in enumerate(highlights, 1):
            title = article.title.replace("|", "\\|").replace("\n", " ")
            url = article.url.replace("|", "\\|")
            source = article.source.replace("|", "\\|")
            lines.append(f"**{i}. [{title}]({url})**")
            lines.append(f"> *{source}*")
            desc = (article.description or "")[:200]
            if desc:
                desc = re.sub(r'<[^>]+>', '', desc)
                lines.append(f"> {desc}")
            lines.append("")

    # Group remaining articles by category
    remaining = sorted_articles[5:]
    cat_groups = {}
    for article in remaining:
        cat = normalize_category(article.category)
        cat_groups.setdefault(cat, []).append(article)

    for cat in cat_groups:
        articles_in_cat = cat_groups[cat]
        cat_display = get_category_display(cat)
        count = len(articles_in_cat)
        count_unit = "篇" if language == "zh" else "articles"

        if language == "zh":
            lines.append(f"### {cat_display} ({count} {count_unit})")
        else:
            lines.append(f"### {cat_display} ({count} {count_unit})")
        lines.append("")

        has_desc = any(a.description for a in articles_in_cat[:20])
        if has_desc:
            summary_header = "摘要" if language == "zh" else "Summary"
            if language == "zh":
                lines.append(f"| # | 文章 | 来源 | {summary_header} |")
            else:
                lines.append(f"| # | Article | Source | {summary_header} |")
            lines.append("|---:|------|------|------|")
            for i, article in enumerate(articles_in_cat[:20], 1):
                title = article.title.replace("|", "\\|").replace("\n", " ")
                url = article.url.replace("|", "\\|")
                source = article.source.replace("|", "\\|")
                desc = ""
                if article.description:
                    desc = re.sub(r'<[^>]+>', '', article.description.strip())[:150]
                # Check for AI summary from summary_map
                if summary_map and url in summary_map:
                    info = summary_map[url]
                    if isinstance(info, dict) and info.get("ai_summary"):
                        desc = info["ai_summary"].replace("|", "\\|").replace("\n", " ")[:150]
                lines.append(f"| {i} | [**{title}**]({url}) | *{source}* | {desc} |")
        else:
            if language == "zh":
                lines.append(f"| # | 文章 | 来源 |")
            else:
                lines.append(f"| # | Article | Source |")
            lines.append("|---:|------|------|")
            for i, article in enumerate(articles_in_cat[:20], 1):
                title = article.title.replace("|", "\\|").replace("\n", " ")
                url = article.url.replace("|", "\\|")
                source = article.source.replace("|", "\\|")
                lines.append(f"| {i} | [**{title}**]({url}) | *{source}* |")

        # Collapsed overflow
        overflow = articles_in_cat[20:]
        if overflow:
            lines.append("")
            lines.append("<details>")
            overflow_label = "更多" if language == "zh" else "More"
            lines.append(f"<summary>📋 {overflow_label} ({len(overflow)} {count_unit})</summary>")
            lines.append("")
            for i, article in enumerate(overflow, 21):
                title = article.title.replace("|", "\\|").replace("\n", " ")
                url = article.url.replace("|", "\\|")
                source = article.source.replace("|", "\\|")
                lines.append(f"- [{title}]({url}) — *{source}*")
            lines.append("")
            lines.append("</details>")

        lines.append("")

    return "\n".join(lines)


def _generate_ai_listing_tiered(ai_articles: list[Article], language: str,
                                 summary_map: dict = None) -> str:
    """Render AI articles with editorial tier structure (no LLM needed)."""
    must_read = [a for a in ai_articles if a.extra.get("editorial_tier") == "must_read"]
    noteworthy = [a for a in ai_articles if a.extra.get("editorial_tier") == "noteworthy"]
    brief = [a for a in ai_articles if a.extra.get("editorial_tier") == "brief"]
    # Articles without tier get treated as noteworthy
    unclassified = [a for a in ai_articles if not a.extra.get("editorial_tier")]

    lines = []

    # Must Read section — prominent display
    if must_read:
        label = "⭐ 必读" if language == "zh" else "⭐ Must Read"
        lines.append(f"### {label}")
        lines.append("")
        for i, article in enumerate(must_read, 1):
            title = article.title.replace("|", "\\|").replace("\n", " ")
            url = article.url.replace("|", "\\|")
            source = article.source.replace("|", "\\|")
            score = article.extra.get("news_value_score", 0)
            cluster = article.extra.get("editorial_factors", {})
            reason_parts = []
            if cluster.get("cross_source", 0) > 0.1:
                reason_parts.append("多源验证" if language == "zh" else "multi-source")
            if article.hn_points and article.hn_points >= 100:
                reason_parts.append(f"HN {article.hn_points}")
            reason = " · ".join(reason_parts) if reason_parts else ""

            lines.append(f"**{i}. [{title}]({url})**")
            lines.append(f"> *{source}*{' | ' + reason if reason else ''}")
            desc = (article.description or "")[:200]
            if desc:
                desc = re.sub(r'<[^>]+>', '', desc)
                lines.append(f"> {desc}")
            lines.append("")

    # Noteworthy section — table format
    noteworthy_articles = noteworthy + unclassified
    if noteworthy_articles:
        label = "📰 值得关注" if language == "zh" else "📰 Noteworthy"
        lines.append(f"### {label} ({len(noteworthy_articles)})")
        lines.append("")
        if language == "zh":
            lines.append("| # | 文章 | 来源 | 摘要 |")
            lines.append("|---:|------|------|------|")
        else:
            lines.append("| # | Article | Source | Summary |")
            lines.append("|---:|------|------|------|")
        for i, article in enumerate(noteworthy_articles, 1):
            title = article.title.replace("|", "\\|").replace("\n", " ")
            url = article.url.replace("|", "\\|")
            source = article.source.replace("|", "\\|")
            desc = ""
            if article.description:
                import re
                desc = re.sub(r'<[^>]+>', '', article.description.strip())[:120]
            lines.append(f"| {i} | [**{title}**]({url}) | *{source}* | {desc} |")
        lines.append("")

    # Brief section — collapsed
    if brief:
        label = "简讯" if language == "zh" else "Brief"
        lines.append("<details>")
        lines.append(f"<summary>📋 {label} ({len(brief)})</summary>")
        lines.append("")
        if language == "zh":
            lines.append("| # | 文章 | 来源 |")
            lines.append("|---:|------|------|")
        else:
            lines.append("| # | Article | Source |")
            lines.append("|---:|------|------|")
        for i, article in enumerate(brief, 1):
            title = article.title.replace("|", "\\|").replace("\n", " ")
            url = article.url.replace("|", "\\|")
            source = article.source.replace("|", "\\|")
            lines.append(f"| {i} | [{title}]({url}) | *{source}* |")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines)


def build_ai_section(ai_articles: list[Article], language: str = "zh",
                     summary_map: dict = None, cluster_map: dict = None) -> str:
    """Build the complete Part I: AI Deep Digest section.

    Wraps the deep analysis in a part header with article count.

    Args:
        ai_articles: list of AI-relevant Article objects
        language: "zh" or "en"
        summary_map: optional dict url -> {ai_summary, ...} for Skill mode enrichment
        cluster_map: optional dict url -> {cluster_id, theme, ...} from topic_cluster

    Returns:
        Complete Part I markdown string
    """
    if not ai_articles:
        return ""

    count = len(ai_articles)

    if language == "zh":
        header = f"## Part I: 🤖 AI 深度日报 ({count} 篇)"
    else:
        header = f"## Part I: 🤖 AI Deep Digest ({count} articles)"

    deep_analysis = generate_ai_report(ai_articles, language, summary_map=summary_map,
                                       cluster_map=cluster_map)

    return f"{header}\n\n{deep_analysis}"
