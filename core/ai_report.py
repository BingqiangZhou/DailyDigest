"""
AI deep analysis report generator.
Produces the Part I (AI deep digest) section of the unified report.
"""

import os

from .article import Article
from .config import AI_DEEP_ANALYSIS_PROMPT_ZH, AI_DEEP_ANALYSIS_PROMPT_EN


def _format_articles_for_deep_analysis(articles: list[Article]) -> str:
    """Format articles for the deep analysis prompt."""
    lines = []
    for i, article in enumerate(articles, 1):
        lang_tag = "🇨🇳" if article.language == "zh" else "🇺🇸"
        lines.append(f"{i}. [{lang_tag}] {article.title}")
        lines.append(f"   来源: {article.source}")
        lines.append(f"   链接: {article.url}")
        desc = (article.description or "")[:300]
        if desc:
            lines.append(f"   摘要: {desc}")
        full = (article.full_text or "")[:500]
        if full:
            lines.append(f"   正文片段: {full}")
        source_type = article.category
        if source_type:
            lines.append(f"   来源类型: {source_type}")
        lines.append("")
    return "\n".join(lines)


def generate_ai_report(ai_articles: list[Article], language: str = "zh") -> str:
    """Generate Part I: AI deep analysis section.

    Uses the AI API to produce a deep analysis with hot topics,
    trend insights, and detailed coverage tables.

    Args:
        ai_articles: list of AI-relevant Article objects
        language: "zh" or "en"

    Returns:
        Markdown string for the AI deep analysis section
    """
    if not ai_articles:
        return ""

    language = language or os.environ.get("REPORT_LANGUAGE", "zh")

    # If no API_KEY, generate a simple listing as fallback
    if not os.environ.get("API_KEY"):
        return _generate_ai_listing_fallback(ai_articles, language)

    from .ai_summarizer import _get_client, _chat_completion

    client = _get_client()
    articles_text = _format_articles_for_deep_analysis(ai_articles)

    prompt_template = AI_DEEP_ANALYSIS_PROMPT_ZH if language == "zh" else AI_DEEP_ANALYSIS_PROMPT_EN
    prompt = prompt_template.format(articles=articles_text)

    print(f"[AI Report] 🤖 Generating deep analysis for {len(ai_articles)} AI articles...")
    response = _chat_completion(client, prompt, max_tokens=6000)

    if not response:
        print("[AI Report] ⚠️ Deep analysis failed, using listing fallback")
        return _generate_ai_listing_fallback(ai_articles, language)

    print("[AI Report] ✅ Deep analysis generated")
    return response.strip()


def _generate_ai_listing_fallback(ai_articles: list[Article], language: str) -> str:
    """Simple fallback listing when AI API is unavailable."""
    lines = []

    if language == "zh":
        lines.append("### 🤖 AI 相关文章")
        lines.append("")
        lines.append("| # | 文章 | 来源 | 分类 |")
        lines.append("|---:|------|------|------|")
    else:
        lines.append("### 🤖 AI-Related Articles")
        lines.append("")
        lines.append("| # | Article | Source | Category |")
        lines.append("|---:|------|------|------|")

    for i, article in enumerate(ai_articles, 1):
        title = article.title.replace("|", "\\|").replace("\n", " ")
        url = article.url.replace("|", "\\|")
        source = article.source.replace("|", "\\|")
        cat = article.category.replace("|", "\\|")
        lines.append(f"| {i} | [**{title}**]({url}) | *{source}* | {cat} |")

    lines.append("")
    return "\n".join(lines)


def build_ai_section(ai_articles: list[Article], language: str = "zh") -> str:
    """Build the complete Part I: AI Deep Digest section.

    Wraps the deep analysis in a part header with article count.

    Args:
        ai_articles: list of AI-relevant Article objects
        language: "zh" or "en"

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

    deep_analysis = generate_ai_report(ai_articles, language)

    return f"{header}\n\n{deep_analysis}"
