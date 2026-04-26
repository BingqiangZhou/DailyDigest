"""
Full-text enrichment for tech articles.

Fetches the original article URL and extracts main content for
high-importance articles only, controlled by ENRICH_FULL_TEXT env var.
"""

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .article import Article
from .html_utils import strip_html
from .http import fetch_url_with_retry
from .logging_config import get_logger

logger = get_logger("enrich")

# Domains known to block automated requests or require login
_SKIP_DOMAINS = {
    "twitter.com", "x.com", "reddit.com", "youtube.com",
    "facebook.com", "linkedin.com", "instagram.com",
    "paywalled.", "subscribe.", "login.", "accounts.",
}

# HTML patterns that indicate main content areas (for extraction quality)
_ARTICLE_PATTERNS = [
    re.compile(r'<article[^>]*>(.*?)</article>', re.DOTALL | re.IGNORECASE),
    re.compile(r'<div[^>]*class="[^"]*(?:post-content|article-body|entry-content|'
               r'td-post-content|article-text|story-body|post-body|content-body|'
               r'article__body|main-content|post-entry)[^"]*"[^>]*>(.*?)</div>',
               re.DOTALL | re.IGNORECASE),
    re.compile(r'<main[^>]*>(.*?)</main>', re.DOTALL | re.IGNORECASE),
]


def _should_skip_url(url: str) -> bool:
    """Check if a URL should be skipped (social media, known blockers)."""
    lower = url.lower()
    return any(domain in lower for domain in _SKIP_DOMAINS)


def _extract_article_content(html: str) -> str:
    """Extract main article content from HTML.

    Tries to find article/content-specific divs first,
    falls back to stripping all HTML.
    """
    if not html:
        return ""

    # Try structured extraction first
    for pattern in _ARTICLE_PATTERNS:
        match = pattern.search(html)
        if match:
            content = match.group(1)
            text = strip_html(content)
            if len(text) > 200:
                return text

    # Fallback: strip all HTML, take the longest text block
    full_text = strip_html(html)
    if len(full_text) > 500:
        return full_text

    return ""


def _select_articles_for_enrichment(articles: list[Article],
                                     cluster_map: dict = None,
                                     max_articles: int = 50,
                                     min_cluster_score: float = 0.4) -> list[Article]:
    """Select high-importance articles for full-text enrichment.

    Selection criteria (OR):
    - In a multi-article cluster with cross-source corroboration
    - Cluster importance score above threshold
    - Has less than 500 chars of existing content
    """
    candidates = []

    for article in articles:
        # Skip if already has substantial content
        if len(article.full_text or "") >= 500:
            continue
        # Skip unsuitable URLs
        if _should_skip_url(article.url):
            continue

        # Check cluster-based importance
        cluster_info = (cluster_map or {}).get(article.url, {})
        cluster_score = cluster_info.get("score", 0)
        cluster_size = cluster_info.get("cluster_size", 1)
        cross_source = cluster_info.get("cross_source", False)

        # Select if: high cluster score OR in a cross-source cluster
        is_important = (
            cluster_score >= min_cluster_score
            or (cluster_size >= 2 and cross_source)
        )

        if is_important:
            candidates.append(article)

    # Sort by cluster score descending, limit count
    candidates.sort(
        key=lambda a: (cluster_map or {}).get(a.url, {}).get("score", 0),
        reverse=True,
    )
    return candidates[:max_articles]


def enrich_tech_articles(articles: list[Article],
                          cluster_map: dict = None,
                          max_articles: int = 50,
                          max_workers: int = 5,
                          delay: float = 0.5) -> tuple[list[Article], dict]:
    """Enrich high-importance tech articles with full text from source URLs.

    Only enriches articles identified as important by topic clustering.
    Controlled by ENRICH_FULL_TEXT environment variable.

    Args:
        articles: list of Article objects to potentially enrich
        cluster_map: dict url -> {score, cluster_size, cross_source, ...}
        max_articles: maximum number of articles to enrich
        max_workers: concurrent fetch threads
        delay: minimum delay between requests to same domain

    Returns:
        (articles, stats) — articles are modified in-place, stats dict with counts
    """
    import time as _time

    candidates = _select_articles_for_enrichment(
        articles, cluster_map, max_articles
    )

    if not candidates:
        return articles, {"enriched": 0, "skipped": 0, "failed": 0}

    logger.info(f"[Enrich] 📖 Fetching full text for {len(candidates)} high-importance articles...")

    enriched = 0
    failed = 0
    _rate_lock = threading.Lock()
    _domain_timestamps: dict[str, float] = {}

    def _rate_limit(domain):
        """Thread-safe per-domain rate limiting."""
        if delay <= 0:
            return
        with _rate_lock:
            last = _domain_timestamps.get(domain, 0.0)
            elapsed = _time.time() - last
            wait = max(0, delay - elapsed)
            _domain_timestamps[domain] = _time.time() + wait
        if wait > 0:
            _time.sleep(wait)

    def _fetch_one(article):
        url = article.url

        # Per-domain rate limiting
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
        except Exception:
            domain = ""
        _rate_limit(domain)

        try:
            body, status, _ = fetch_url_with_retry(
                url,
                headers={
                    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/125.0.0.0 Safari/537.36"),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
                },
                timeout=15,
                max_retries=1,
            )
            if body is None:
                return article, False

            content = _extract_article_content(body)
            if content and len(content) > len(article.full_text or ""):
                article.full_text = content[:8000]  # Cap to avoid excessive token usage
                article.extra["content_source"] = "enriched"
                return article, True
            return article, False
        except Exception:
            return article, False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, art): art for art in candidates}
        for future in as_completed(futures):
            art, success = future.result()
            if success:
                enriched += 1
            else:
                failed += 1

    stats = {
        "enriched": enriched,
        "skipped": len(articles) - len(candidates),
        "failed": failed,
    }
    logger.info(f"[Enrich] ✅ Enriched {enriched}/{len(candidates)} articles "
                f"({failed} failed, {stats['skipped']} skipped)")
    return articles, stats
