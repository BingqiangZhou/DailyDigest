"""
AI content filter module.
Splits articles into AI-relevant and non-AI sets using
category matching, AI API classification, or keyword fallback.
"""

import json
import os

from .article import Article
from .llm_utils import parse_llm_json
from .config import (
    AI_DIGEST_DIRECT_CATEGORIES,
    AI_KEYWORDS_ZH,
    AI_KEYWORDS_EN,
    AI_FILTER_PROMPT_ZH,
    AI_FILTER_PROMPT_EN,
)
from .logging_config import get_logger

logger = get_logger("ai_filter")


def _article_to_filter_item(index: int, article: Article) -> str:
    """Format a single article for the filter prompt."""
    parts = [f"[{index}] {article.title}"]
    if article.source:
        parts.append(f"    来源: {article.source}")
    desc = (article.description or "")[:200]
    if desc:
        parts.append(f"    摘要: {desc}")
    return "\n".join(parts)


def _keyword_filter(articles: list[Article]) -> list[Article]:
    """Fallback keyword-based AI relevance filter."""
    all_keywords = AI_KEYWORDS_ZH + AI_KEYWORDS_EN
    results = []
    for article in articles:
        text = f"{article.title} {article.description or ''}".lower()
        if any(kw.lower() in text for kw in all_keywords):
            results.append(article)
    return results


def _api_filter(articles: list[Article], batch_size: int = 50) -> list[Article]:
    """AI API-based batch classification for AI relevance."""
    from .llm import get_llm_client, chat_with_profile

    client = get_llm_client()
    language = os.environ.get("REPORT_LANGUAGE", "zh")

    results = []
    total_batches = (len(articles) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        start = batch_idx * batch_size
        batch = articles[start:start + batch_size]
        logger.info(f"[AI Filter] 🤖 batch {batch_idx + 1}/{total_batches} ({len(batch)} articles)...")

        articles_text = "\n\n".join(
            _article_to_filter_item(i, a) for i, a in enumerate(batch, start=1)
        )
        prompt_template = AI_FILTER_PROMPT_ZH if language == "zh" else AI_FILTER_PROMPT_EN
        prompt = prompt_template.format(articles=articles_text)

        response = chat_with_profile(client, prompt, "classify")
        if not response:
            logger.warning(f"[AI Filter] ⚠️ batch {batch_idx + 1} API failed, using keyword fallback")
            results.extend(_keyword_filter(batch))
            continue

        try:
            classifications = parse_llm_json(response)
            for i, article in enumerate(batch, start=1):
                if classifications.get(str(i), False):
                    results.append(article)
            ai_count = sum(1 for v in classifications.values() if v)
            logger.info(f"[AI Filter] ✅ batch {batch_idx + 1}: {ai_count} AI articles")
        except (ValueError, json.JSONDecodeError):
            logger.warning(f"[AI Filter] ⚠️ batch {batch_idx + 1} JSON parse failed, using keyword fallback")
            results.extend(_keyword_filter(batch))

    return results


def filter_ai_articles(articles: list[Article]) -> tuple[list[Article], list[Article]]:
    """Split articles into (ai_articles, non_ai_articles).

    Articles in AI_DIGEST_DIRECT_CATEGORIES are always included in ai_articles.
    All other articles are classified by AI API (with keyword fallback).

    Args:
        articles: all processed articles from all sources

    Returns:
        tuple of (ai_relevant_articles, non_ai_articles)
    """
    ai_direct = []
    to_classify = []

    for article in articles:
        if article.category in AI_DIGEST_DIRECT_CATEGORIES:
            ai_direct.append(article)
        else:
            to_classify.append(article)

    logger.info(f"[AI Filter] 📋 {len(ai_direct)} direct AI articles, {len(to_classify)} to classify")

    if not to_classify:
        return ai_direct, []

    # Use API classification if API_KEY is available, else keyword fallback
    if os.environ.get("API_KEY"):
        ai_classified = _api_filter(to_classify)
    else:
        ai_classified = _keyword_filter(to_classify)

    # Split classified articles
    ai_urls = {a.url for a in ai_classified}
    ai_articles = ai_direct + ai_classified
    non_ai_articles = [a for a in to_classify if a.url not in ai_urls]

    logger.info(f"[AI Filter] ✅ result: {len(ai_articles)} AI articles, {len(non_ai_articles)} non-AI articles")
    return ai_articles, non_ai_articles
