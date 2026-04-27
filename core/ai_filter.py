"""
AI content filter module.
Splits articles into AI-relevant and non-AI sets using
category matching, AI API classification, or keyword fallback.
Also provides feed-level noise filtering for high-noise sources.
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

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
from .llm import get_llm_client, chat_with_profile

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
    results = []
    for article in articles:
        text = f"{article.title} {article.description or ''}"
        if _ai_keyword_match(text):
            results.append(article)
    return results


def _classify_batch(client, batch, batch_idx, total_batches, language):
    """Classify a single batch of articles. Returns list of AI-relevant articles."""
    logger.info(f"[AI Filter] 🤖 batch {batch_idx + 1}/{total_batches} ({len(batch)} articles)...")
    articles_text = "\n\n".join(
        _article_to_filter_item(i, a) for i, a in enumerate(batch, start=1)
    )
    prompt_template = AI_FILTER_PROMPT_ZH if language == "zh" else AI_FILTER_PROMPT_EN
    prompt = prompt_template.format(articles=articles_text)

    response = chat_with_profile(client, prompt, "classify")
    if not response:
        logger.warning(f"[AI Filter] ⚠️ batch {batch_idx + 1} API failed, using keyword fallback")
        return _keyword_filter(batch)

    try:
        classifications = parse_llm_json(response)
        results = []
        for i, article in enumerate(batch, start=1):
            if classifications.get(str(i), False):
                results.append(article)
        ai_count = sum(1 for v in classifications.values() if v)
        logger.info(f"[AI Filter] ✅ batch {batch_idx + 1}: {ai_count} AI articles")
        return results
    except (ValueError, json.JSONDecodeError):
        logger.warning(f"[AI Filter] ⚠️ batch {batch_idx + 1} JSON parse failed, using keyword fallback")
        return _keyword_filter(batch)


def _api_filter(articles: list[Article], batch_size: int = 50) -> list[Article]:
    """AI API-based batch classification for AI relevance (concurrent batches)."""
    client = get_llm_client()
    language = os.environ.get("REPORT_LANGUAGE", "zh")

    batches = []
    for i in range(0, len(articles), batch_size):
        batches.append(articles[i:i + batch_size])
    total_batches = len(batches)

    results = []
    max_workers = min(3, total_batches)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_classify_batch, client, batch, idx, total_batches, language): idx
            for idx, batch in enumerate(batches)
        }
        for future in as_completed(futures):
            results.extend(future.result())

    return results


def _is_obvious_non_ai(article: Article) -> bool:
    """Return True if the article is clearly NOT about AI based on title/description.

    This is a negative gate applied BEFORE any positive keyword matching.
    Catches wildlife, sports, politics, lifestyle, etc. that slip through
    feed-level filters.
    """
    text = f"{article.title} {article.description or ''}".lower()

    # Strong negative patterns — if any match, article is almost certainly not AI
    negative_patterns = [
        # Wildlife / nature / environment
        r"\b(wildfires?|wetland|orangutan|wildlife|bird watching|river pollution|endangered species|deforestation|changing skies)\b",
        # Natural disasters (not tech-related)
        r"\b(earthquake|tsunami|volcano|hurricane|tornado|flood damage)\b",
        # Sports
        r"\b(nfl|nba|mlb|nhl|soccer match|football game|basketball game|olympics? medal|tennis tournament|baseball game|world cup)\b",
        # Pure entertainment (not AI-generated content)
        r"\b(box office|album release|concert tour|red carpet|tv series finale|celebrity gossip|movie (review|trailer)|tv show recap)\b",
        # Lifestyle / health (not AI health applications)
        r"\b(exercise routine|diet plan|workout|fitness tip|yoga pose|sleep better|weight loss|meditation guide|skincare routine|makeup tutorial)\b",
        # Crime / violence (not AI safety/ethics)
        r"\b(shooting|gunman|stabbing|homicide|armed robbery|murder suspect)\b",
        # Pure food / travel (not AI food tech)
        r"\b(recipe|restaurant review|travel guide|hotel review|tourist attraction)\b",
        # Celebrities / gossip / fashion
        r"\b(celebrity|gossip|red carpet|dating rumor|divorce|wedding dress|fashion week|lipstick|perfume review|nail art)\b",
        # Random non-tech science / nature curiosities
        r"\b(meteors?|aurora borealis|solar eclipse|dinosaur fossil|archaeology|supercomputer.*auction|royalty.*auction|your snaps)\b",
        # Finance without tech angle
        r"\b(stock (pick|tip)|dividend|earnings per share|hedge fund strategy)\b",
        # Chinese off-topic patterns
        r"(野火|湿地|野生动物|濒危物种|河流污染|环境污染|减肥计划|瑜伽体式|冥想指南|睡眠卫生|口红测评|护肤步骤|美妆教程|香水推荐|综艺节目|明星八卦|电视剧|演唱会|股票推荐|基金定投|期货交易)",
    ]
    for pattern in negative_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _ai_keyword_match(text: str) -> bool:
    """Check if text contains AI-related keywords with appropriate matching.

    Short/ambiguous keywords (like 'AI', 'GPT', 'RAG') use word boundary
    matching with ASCII flag to handle mixed CJK/Latin text correctly.
    Longer keywords use substring matching.
    """
    all_keywords = AI_KEYWORDS_ZH + AI_KEYWORDS_EN
    text_lower = text.lower()

    for kw in all_keywords:
        kw_lower = kw.lower()
        # Short keywords (<=6 chars) require word boundaries to avoid
        # false positives: "agent" shouldn't match "real estate agent".
        # Use re.ASCII so \b only matches at ASCII/Latin boundaries,
        # correctly handling mixed CJK/Latin text like "Claude降智".
        if len(kw) <= 6:
            if re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower, re.ASCII):
                return True
        else:
            if kw_lower in text_lower:
                return True
    return False


def _is_likely_ai_article(article: Article) -> bool:
    """Quick heuristic check: does this article look AI-related at all?

    Applied as a safety net even for direct-category articles to filter
    obvious false positives (e.g. wildlife articles, lifestyle posts).
    """
    # Negative gate first — if it's obviously not AI, skip keyword matching
    if _is_obvious_non_ai(article):
        return False

    text = f"{article.title} {article.description or ''}"
    # Check if any AI keyword appears in title or description (word-boundary safe)
    if _ai_keyword_match(text):
        return True

    # Check for common AI-adjacent terms (specific enough to avoid false positives)
    ai_adjacent = [
        "artificial intelligence", "machine learning", "deep learning",
        "neural network", "gpt-", "claude ", "gemini ", "transformer",
        "chatbot", "openai", "anthropic", "deepmind", "diffusion model",
        "copilot", "ai alignment", "reinforcement learning",
        "人工智能", "大模型", "大语言模型", "深度学习", "机器学习",
        "智能体", "ai agent", "chatgpt", "神经网络",
        "foundation model", "language model", "generative ai",
    ]
    text_lower = text.lower()
    return any(term in text_lower for term in ai_adjacent)


def _hard_ai_relevance_check(article: Article) -> bool:
    """Hard gate: articles with zero AI keyword signal are never AI-related.

    This is the final safety net against fundamental misclassification.
    No amount of clustering, authority, or API classification should
    override a complete absence of AI relevance in the article text.
    """
    # Negative gate: obvious non-AI content is always rejected
    if _is_obvious_non_ai(article):
        return False

    # First check: does it pass the keyword/adjacent term test?
    if _is_likely_ai_article(article):
        return True

    # Check full_text for AI keywords as a secondary signal
    full_text = article.full_text or ""
    if full_text:
        if _ai_keyword_match(full_text):
            return True

    return False


def filter_ai_articles(articles: list[Article]) -> tuple[list[Article], list[Article]]:
    """Split articles into (ai_articles, non_ai_articles).

    Articles in AI_DIGEST_DIRECT_CATEGORIES get a lightweight relevance
    check before inclusion. All other articles are classified by AI API
    (with keyword fallback).
    """
    ai_direct = []
    to_classify = []

    for article in articles:
        if article.category in AI_DIGEST_DIRECT_CATEGORIES:
            # Safety filter: even direct-category articles must look AI-related
            if _is_likely_ai_article(article):
                ai_direct.append(article)
            else:
                to_classify.append(article)
        else:
            to_classify.append(article)

    logger.info(f"[AI Filter] 📋 {len(ai_direct)} direct AI articles, {len(to_classify)} to classify")

    if not to_classify:
        return ai_direct, []

    if os.environ.get("API_KEY"):
        ai_classified = _api_filter(to_classify)
    else:
        ai_classified = _keyword_filter(to_classify)

    ai_urls = {a.url for a in ai_classified}
    ai_articles = ai_direct + ai_classified

    # Hard relevance gate: filter out articles with zero AI signal
    # This catches false positives from the API classifier (e.g. wildlife articles)
    pre_gate = len(ai_articles)
    ai_articles = [a for a in ai_articles if _hard_ai_relevance_check(a)]
    gated = pre_gate - len(ai_articles)
    if gated > 0:
        logger.info(f"[AI Filter] 🚫 Hard relevance gate removed {gated} articles with zero AI signal")

    gated_urls = {a.url for a in ai_direct + ai_classified} - {a.url for a in ai_articles}
    non_ai_articles = [a for a in to_classify if a.url not in ai_urls or a.url in gated_urls]

    logger.info(f"[AI Filter] ✅ result: {len(ai_articles)} AI articles, {len(non_ai_articles)} non-AI articles")
    return ai_articles, non_ai_articles


# Feed-level noise keywords for high-noise sources
# These are topics that frequently appear in AI feeds but are NOT AI-related
NOISE_PATTERNS = [
    # Lifestyle / personal posts
    r"\b(exercise|hate exercise|learned to not hate|diet|fitness|sleep|meditation)\b",
    # Entertainment / gaming (not AI-in-gaming)
    r"\b(movie|film|album|concert|fantasy|dnd|dungeons?)\b",
    # Pure politics / social (not AI policy)
    r"\b(election|voting|campaign|partisan|tribal affiliation)\b",
    # Non-AI science / nature / environment
    r"\b(wildfires?|wetland|orangutan|wildlife|meteor|skydiving|species|river pollution|flood|drought|climate change protest|supercomputer|auction|royalty|fossil|dinosaur|archaeology)\b",
    # Random BBC-style general interest ("watch:", "your snaps", etc.)
    r"\b(your snaps|watch:|braved|new bridge|split community|17th century)\b",
]

# Broader noise patterns for tech_only feeds — filters political, lifestyle, sports content
# that routinely leaks into general-purpose feeds (CNBC, BBC, Axios, etc.)
TECH_NOISE_PATTERNS = [
    # Hard politics (keep tech policy like "AI regulation")
    r"\b(election|voting|campaign|partisan|senator|congressman|governor voted|ballot|primary|trump|biden|white house correspondents|press dinner|whcd)\b",
    # Breaking news / crime (shootings, arrests, trials)
    r"\b(shooting|gunman|shot and killed|arrested|indicted|trial\b|convicted|murder|homicide)\b",
    # Sports
    r"\b(nfl|nba|mlb|nhl|soccer|football|basketball|baseball|olympics?|championship game)\b",
    # Lifestyle / food / fashion
    r"\b(recipe|cooking|fashion|style tips|restaurant review|travel guide|workout routine)\b",
    # Pure entertainment (keep tech entertainment like streaming platforms)
    r"\b(celebrity|red carpet|award show|box office|tv series|episode recap|season finale)\b",
    # Non-tech science (wildlife, geology, astronomy not related to tech)
    r"\b(wildfire|wetland|orangutan|wildlife|bird watching|volcano|earthquake|tsunami)\b",
    # Finance/stock news without tech angle
    r"\b(stock (pick|tip|portfolio)|dividend|earnings (per share|season)|hedge fund|ipo\b)\b",
    # Non-tech consumer (home buying, car deals without tech angle)
    r"\b(home sale|real estate|bay area home|mortgage rate)\b",
]


def _matches_any_pattern(text: str, patterns: list) -> bool:
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def apply_feed_noise_filter(articles: list[Article]) -> list[Article]:
    """Filter out off-topic content from feeds with noise_filter flag.

    Supports two modes:
      - "ai_only": Only keep articles that look AI-related (noise check + AI keywords)
      - "tech_only": Keep tech content, filter political/entertainment/lifestyle noise

    Reads the noise_filter flag from feed metadata stored in article.extra.
    """
    filtered = []
    ai_only_removed = 0
    tech_only_removed = 0

    for article in articles:
        feed_meta = article.extra.get("_feed_meta", {})
        noise_mode = feed_meta.get("noise_filter", "") or feed_meta.get("_noise_filter", "")

        if not noise_mode:
            filtered.append(article)
            continue

        text = f"{article.title} {article.description or ''}".lower()

        if noise_mode == "ai_only":
            is_noise = _matches_any_pattern(text, NOISE_PATTERNS)
            if is_noise and not _is_likely_ai_article(article):
                ai_only_removed += 1
                continue

        elif noise_mode == "tech_only":
            is_noise = _matches_any_pattern(text, TECH_NOISE_PATTERNS)
            if is_noise:
                tech_only_removed += 1
                continue

        filtered.append(article)

    removed = ai_only_removed + tech_only_removed
    if removed > 0:
        parts = []
        if ai_only_removed:
            parts.append(f"{ai_only_removed} non-AI")
        if tech_only_removed:
            parts.append(f"{tech_only_removed} non-tech")
        logger.info(f"[Feed Filter] 🧹 Removed {' and '.join(parts)} articles from filtered feeds")

    return filtered


def _extract_domain(url: str) -> str:
    """Extract domain from URL for trust tier lookup."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _ai_keyword_count(text: str) -> int:
    """Count how many AI/tech keywords appear in text."""
    text_lower = text.lower()
    count = 0
    all_keywords = AI_KEYWORDS_ZH + AI_KEYWORDS_EN
    for kw in all_keywords:
        kw_lower = kw.lower()
        if len(kw) <= 6:
            if re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower, re.ASCII):
                count += 1
        else:
            if kw_lower in text_lower:
                count += 1
    return count


def apply_trust_tier_filter(
    articles: list[Article],
    authority_domains: dict | None = None,
) -> tuple[list[Article], int]:
    """Filter articles by source trust tier.

    Tier 1-2 (score >= 0.85): high trust, pass through
    Tier 3 (score >= 0.7): require at least 1 AI keyword
    Tier 4 (score < 0.7) and unknown: require 2+ AI keywords

    Returns:
        (filtered_articles, filtered_count)
    """
    from .topic_cluster import AUTHORITY_DOMAINS as _AUTHORITY_DOMAINS
    authority_domains = authority_domains or _AUTHORITY_DOMAINS

    filtered = []
    filtered_count = 0

    for article in articles:
        domain = _extract_domain(article.url)
        tier_score = 0.0
        for auth_domain, score in authority_domains.items():
            if domain == auth_domain or domain.endswith("." + auth_domain):
                tier_score = score
                break

        if tier_score >= 0.85:
            # High trust (OpenAI, Anthropic, arXiv, etc.)
            filtered.append(article)
        elif tier_score >= 0.7:
            # Medium trust: need at least 1 AI keyword
            text = f"{article.title} {article.description or ''}"
            if _ai_keyword_match(text):
                filtered.append(article)
            else:
                filtered_count += 1
        else:
            # Low trust or unknown: need 2+ AI keywords
            text = f"{article.title} {article.description or ''}"
            if _ai_keyword_count(text) >= 2:
                filtered.append(article)
            else:
                filtered_count += 1

    if filtered_count > 0:
        logger.info(f"[Trust Filter] 🛡️ Filtered {filtered_count} low-signal articles from lower-trust sources")

    return filtered, filtered_count
