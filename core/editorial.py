"""
Editorial pipeline module for DailyDigest.

Implements a magazine-style editorial workflow:
  1. Source assessment (authority scoring)
  2. News value scoring (5-factor composite)
  3. Tier assignment (must_read / noteworthy / brief)
  4. Depth allocation (deep_analysis / summary_only / headline_only)
  5. Newsworthiness filter

All scoring is heuristic (zero LLM calls).
"""

from .article import Article
from .config import (
    EDITORIAL_ENABLED,
    EDITORIAL_NEWS_VALUE_THRESHOLD,
    EDITORIAL_TIER_MUST_READ,
    EDITORIAL_TIER_NOTEWORTHY,
    EDITORIAL_HN_PROMOTE_THRESHOLD,
)
from .logging_config import get_logger
from .topic_cluster import AUTHORITY_DOMAINS, HIGH_SIGNAL_KEYWORDS

logger = get_logger("editorial")


def compute_article_authority(article: Article) -> float:
    """Score a single article's source authority (0.0 - 1.0).

    Uses AUTHORITY_DOMAINS for known domains, falls back to feed priority.
    Matches against both source name and article URL.
    """
    source = article.source or ""
    source_lower = source.lower()

    # Build match text from source name + URL
    url_lower = (article.url or "").lower()
    match_text = f"{source_lower} {url_lower}"

    # Check known authority domains (match against source name and URL)
    for domain, weight in AUTHORITY_DOMAINS.items():
        # Direct domain match against URL or source text
        if domain in match_text:
            return weight
        # Brand name matching for domains (e.g. "openai.com" -> "openai")
        # Only match brands >= 4 chars to avoid false positives like "blog"
        if "." in domain:
            brand = domain.split(".")[0]
            if len(brand) >= 4 and brand in source_lower:
                return weight
        else:
            # Non-domain keys (e.g. "机器之心") match directly against source
            if domain in source:
                return weight

    # Fallback: use feed priority (1=highest, 3=default)
    priority = article.priority
    if priority == 1:
        return 0.8
    elif priority == 2:
        return 0.6
    return 0.4


def compute_article_novelty(article: Article, cluster_map: dict) -> float:
    """Score an article's novelty within its cluster (0.0 - 1.0).

    First report in a cluster = highest novelty. Subsequent reports decay.
    Singletons get moderate novelty (uncertain).
    """
    cluster_info = cluster_map.get(article.url)
    if not cluster_info:
        return 0.5

    cluster_size = cluster_info.get("cluster_size", 1)
    if cluster_size <= 1:
        return 0.5

    # Check if this is the earliest article in the cluster
    # by comparing URLs in the cluster (first published = highest novelty)
    # Since we don't have direct cluster membership here, use a proxy:
    # articles from higher-authority sources get a novelty bonus
    authority = compute_article_authority(article)
    # Decay based on cluster size: more coverage = less novel per article
    return min(1.0 / (cluster_size ** 0.5) + authority * 0.3, 1.0)


def compute_news_value(article: Article, cluster_map: dict) -> dict:
    """Compute 5-factor news value score for a single article.

    Returns dict with individual factor scores and composite.
    Weights match knowledge/content-strategy.md rubric.
    """
    cluster_info = cluster_map.get(article.url, {})
    cluster_size = cluster_info.get("cluster_size", 1)
    cross_source = cluster_info.get("cross_source", False)

    # Factor 1: Source authority (weight 0.25)
    authority = compute_article_authority(article)
    authority_weighted = authority * 0.25

    # Factor 2: Cross-source corroboration (weight 0.25)
    cross_score = 0.25 if cross_source else 0.05

    # Factor 3: Cluster heat / size (weight 0.20)
    size_score = min(cluster_size / 5.0, 1.0) * 0.20

    # Factor 4: Keyword signal strength (weight 0.15)
    text = f"{article.title} {article.description or ''}".lower()
    has_signal = any(kw in text for kw in HIGH_SIGNAL_KEYWORDS)
    signal_score = 0.15 if has_signal else 0.0

    # Factor 5: Novelty (weight 0.15)
    novelty = compute_article_novelty(article, cluster_map)
    novelty_weighted = novelty * 0.15

    composite = authority_weighted + cross_score + size_score + signal_score + novelty_weighted
    composite = min(round(composite, 3), 1.0)

    return {
        "authority": round(authority, 3),
        "cross_source": round(cross_score, 3),
        "cluster_heat": round(size_score, 3),
        "signal": round(signal_score, 3),
        "novelty": round(novelty_weighted, 3),
        "composite": composite,
    }


def assign_editorial_tier(article: Article) -> str:
    """Assign editorial tier based on news value score.

    Returns "must_read", "noteworthy", or "brief".
    Applies promotion rules for high HN engagement and priority-1 sources.
    """
    score = article.extra.get("news_value_score", 0.0)
    tier = "brief"

    if score >= EDITORIAL_TIER_MUST_READ:
        tier = "must_read"
    elif score >= EDITORIAL_TIER_NOTEWORTHY:
        tier = "noteworthy"

    # Promotion: HN points > threshold bumps up one tier
    if tier != "must_read":
        hn_points = article.hn_points or 0
        if hn_points >= EDITORIAL_HN_PROMOTE_THRESHOLD:
            tier = _promote_tier(tier)

    # Promotion: priority-1 source bumps up one tier
    if tier != "must_read" and article.priority == 1:
        tier = _promote_tier(tier)

    return tier


def allocate_depth(article: Article) -> str:
    """Allocate processing depth based on editorial tier.

    Returns "deep_analysis", "summary_only", or "headline_only".
    """
    tier = article.extra.get("editorial_tier", "brief")
    depth_map = {
        "must_read": "deep_analysis",
        "noteworthy": "summary_only",
        "brief": "headline_only",
    }
    return depth_map.get(tier, "headline_only")


def run_editorial_pipeline(
    articles: list[Article],
    cluster_map: dict,
) -> tuple[list[Article], dict]:
    """Run the full editorial pipeline on a list of articles.

    Steps:
      1. Compute news value score for each article
      2. Assign editorial tier
      3. Allocate depth
      4. Filter out low-value articles

    Returns:
        (approved_articles, stats)
        stats contains counts for logging.
    """
    if not EDITORIAL_ENABLED:
        logger.info("📝 Editorial pipeline disabled (EDITORIAL_ENABLED=false)")
        return articles, {"disabled": True}

    threshold = EDITORIAL_NEWS_VALUE_THRESHOLD

    approved = []
    tier_counts = {"must_read": 0, "noteworthy": 0, "brief": 0}
    filtered_count = 0

    for article in articles:
        # Step 1-3: Score, tier, depth
        factors = compute_news_value(article, cluster_map)
        article.extra["news_value_score"] = factors["composite"]
        article.extra["editorial_factors"] = factors

        tier = assign_editorial_tier(article)
        article.extra["editorial_tier"] = tier

        depth = allocate_depth(article)
        article.extra["depth"] = depth

        # Step 4: Newsworthiness filter
        if factors["composite"] < threshold:
            filtered_count += 1
            continue

        approved.append(article)
        tier_counts[tier] += 1

    stats = {
        "total_input": len(articles),
        "approved": len(approved),
        "filtered": filtered_count,
        **tier_counts,
    }

    logger.info(
        f"📝 Editorial: {len(approved)}/{len(articles)} approved "
        f"(must_read={tier_counts['must_read']}, "
        f"noteworthy={tier_counts['noteworthy']}, "
        f"brief={tier_counts['brief']}, "
        f"filtered={filtered_count})"
    )

    return approved, stats


def _promote_tier(tier: str) -> str:
    """Promote an editorial tier by one level."""
    promotions = {"brief": "noteworthy", "noteworthy": "must_read"}
    return promotions.get(tier, tier)
