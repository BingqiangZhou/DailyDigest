"""
AI deep analysis report generator.
Produces the Part I (AI deep digest) section of the unified report.
"""

import os

from .article import Article, format_article_item
from .config import AI_DEEP_ANALYSIS_PROMPT_ZH, AI_DEEP_ANALYSIS_PROMPT_EN
from .logging_config import get_logger

logger = get_logger("ai_report")


def _format_articles_for_deep_analysis(articles: list[Article],
                                        cluster_map: dict = None) -> str:
    """Format articles for the deep analysis prompt.

    When cluster_map is provided, articles belonging to the same topic
    cluster are annotated with [CLUSTER: N篇关于"theme"] markers.
    """
    lines = []
    for i, article in enumerate(articles, 1):
        cluster_info = (cluster_map or {}).get(article.url)
        if cluster_info and cluster_info.get("cluster_size", 1) > 1:
            lines.append(f"[CLUSTER: {cluster_info['cluster_size']}篇关于\"{cluster_info['theme']}\"]")

        item_lines = format_article_item(article, i, desc_limit=300, include_source_type=True)
        lines.extend(item_lines)

        full_text_len = len(article.full_text or "")
        full_limit = 2000 if full_text_len > 500 else 500
        full = (article.full_text or "")[:full_limit]
        if full:
            lines.append(f"   正文片段: {full}")
        lines.append("")
    return "\n".join(lines)


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
    articles_text = _format_articles_for_deep_analysis(ai_articles, cluster_map=cluster_map)

    prompt_template = AI_DEEP_ANALYSIS_PROMPT_ZH if language == "zh" else AI_DEEP_ANALYSIS_PROMPT_EN
    prompt = prompt_template.format(articles=articles_text)

    logger.info(f"[AI Report] 🤖 Generating deep analysis for {len(ai_articles)} AI articles...")
    # Use multi-pass critique for the deep analysis (most prominent section)
    from .config import DEEP_ANALYSIS_CRITIQUE
    response = generate_with_critique(client, prompt, "deep_analysis", DEEP_ANALYSIS_CRITIQUE)

    if not response:
        logger.warning("[AI Report] ⚠️ Deep analysis failed, using listing fallback")
        return _generate_ai_listing_fallback(ai_articles, language, summary_map=summary_map)

    logger.info("[AI Report] ✅ Deep analysis generated")
    return response.strip()


def _generate_ai_listing_fallback(ai_articles: list[Article], language: str,
                                   summary_map: dict = None) -> str:
    """Simple fallback listing when AI API is unavailable.

    Enriches the table with sub-agent summaries when available.
    """
    lines = []

    if language == "zh":
        lines.append("### 🤖 AI 相关文章")
        lines.append("")
        lines.append("| # | 文章 | 来源 | 分类 | 摘要 |")
        lines.append("|---:|------|------|------|------|")
    else:
        lines.append("### 🤖 AI-Related Articles")
        lines.append("")
        lines.append("| # | Article | Source | Category | Summary |")
        lines.append("|---:|------|------|------|------|")

    for i, article in enumerate(ai_articles, 1):
        title = article.title.replace("|", "\\|").replace("\n", " ")
        url = article.url.replace("|", "\\|")
        source = article.source.replace("|", "\\|")
        cat = article.category.replace("|", "\\|")
        summary = ""
        if summary_map and url in summary_map:
            info = summary_map[url]
            if isinstance(info, dict):
                summary = info.get("ai_summary", "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {i} | [**{title}**]({url}) | *{source}* | {cat} | {summary} |")

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
        header = f"# Part I: 🤖 AI 深度日报 ({count} 篇)"
    else:
        header = f"# Part I: 🤖 AI Deep Digest ({count} articles)"

    deep_analysis = generate_ai_report(ai_articles, language, summary_map=summary_map,
                                       cluster_map=cluster_map)

    return f"{header}\n\n{deep_analysis}"
