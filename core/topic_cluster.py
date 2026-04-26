"""
Topic clustering module for DailyDigest.

Groups related articles into topic clusters using keyword-based
agglomerative clustering, and assigns importance scores.
"""

import re
from collections import Counter

from .article import Article
from .config import AI_KEYWORDS_ZH, AI_KEYWORDS_EN
from .logging_config import get_logger

logger = get_logger("cluster")

# Stop words to filter during keyword extraction
STOP_WORDS = {
    "en": {"the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or",
           "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
           "do", "does", "did", "will", "would", "could", "should", "may", "might",
           "this", "that", "these", "those", "it", "its", "with", "from", "by",
           "as", "but", "not", "no", "so", "if", "than", "too", "very", "can",
           "about", "up", "out", "how", "what", "which", "who", "when", "where",
           "why", "all", "each", "every", "both", "few", "more", "most", "other",
           "some", "such", "only", "own", "same", "just", "also", "new", "like",
           "into", "over", "after", "before", "between", "through", "during"},
    "zh": {"的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都",
           "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会",
           "着", "没有", "看", "好", "自己", "这", "他", "她", "它", "们",
           "那", "些", "个", "为", "与", "对", "中", "等", "能", "将", "被",
           "从", "让", "把", "给", "做", "用", "比", "更", "已", "或"},
}

# High-signal keywords that boost importance score
HIGH_SIGNAL_KEYWORDS = {
    "release", "launch", "announce", "breakthrough", "first", "record",
    "acquire", "acquisition", "merger", "benchmark", "SOTA", "open-source",
    "regulation", "ban", "restrict", "发布", "推出", "首次", "突破", "收购",
    "合并", "开源", "禁止", "限制", "监管",
}

# Source authority weight mapping (domain -> tier weight)
_AUTHORITY_DOMAINS = {
    # Tier 1 (weight 1.0)
    "openai.com": 1.0, "anthropic.com": 1.0, "ai.google": 1.0,
    "deepmind.google": 1.0, "ai.meta.com": 1.0, "arxiv.org": 1.0,
    "blog.google": 1.0, "research.google": 1.0,
    # Tier 2 (weight 0.7)
    "techcrunch.com": 0.7, "theverge.com": 0.7, "wired.com": 0.7,
    "arstechnica.com": 0.7, "github.com": 0.7, "huggingface.co": 0.7,
    "venturebeat.com": 0.7, "theinformation.com": 0.7,
    # Tier 2 Chinese
    "36kr.com": 0.7, "jiqizhixin.com": 0.7, "机器之心": 0.7,
    "量子位": 0.7, "infoq.cn": 0.7,
}

# Regex for CJK characters (including extensions)
_CJK_RE = re.compile(r'[一-鿿㐀-䶿\U00020000-\U0002a6df]')


def _tokenize(text: str) -> list[str]:
    """Tokenize text: extract words and CJK bigrams for better Chinese matching."""
    tokens = []
    # Extract alphanumeric words
    alpha_tokens = re.findall(r'[a-zA-Z0-9]+', text.lower())
    tokens.extend(t for t in alpha_tokens if len(t) > 1)

    # Extract CJK characters and generate bigrams for Chinese text
    cjk_chars = _CJK_RE.findall(text)
    if len(cjk_chars) >= 2:
        bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
        tokens.extend(bigrams)
    # Also add individual CJK chars as fallback (for short text)
    tokens.extend(cjk_chars)

    return tokens


def extract_keywords(title: str, description: str = "") -> list[str]:
    """Extract significant keywords from article title + description.

    Uses AI keyword lists as signals and filters stop words.
    """
    text = f"{title} {description}"
    tokens = _tokenize(text)

    all_ai_keywords = set(kw.lower() for kw in AI_KEYWORDS_ZH + AI_KEYWORDS_EN)
    all_stop = STOP_WORDS["en"] | STOP_WORDS["zh"]

    # Score tokens: AI keywords get priority, others by frequency
    significant = []
    for token in tokens:
        if token in all_stop:
            continue
        if token in all_ai_keywords or len(token) >= 2:
            significant.append(token)

    # Deduplicate while preserving order, limit to top keywords
    seen = set()
    result = []
    for kw in significant:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
        if len(result) >= 15:
            break

    return result


def compute_similarity(kw1: list[str], kw2: list[str]) -> float:
    """Jaccard similarity between two keyword sets."""
    if not kw1 or not kw2:
        return 0.0
    set1, set2 = set(kw1), set(kw2)
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def cluster_articles(articles: list[Article], similarity_threshold: float = 0.25,
                     max_clusters: int = 50) -> list[dict]:
    """Group articles into topic clusters using keyword-based agglomerative clustering.

    Args:
        articles: list of Article objects
        similarity_threshold: minimum Jaccard similarity to merge clusters
        max_clusters: maximum number of non-singleton clusters to produce

    Returns:
        list of cluster dicts with keys: cluster_id, theme, articles, size,
        cross_source, score
    """
    if not articles:
        return []

    # Extract keywords for each article
    article_keywords = {}
    for article in articles:
        article_keywords[article.url] = extract_keywords(article.title, article.description or "")

    # Initialize: each article is its own cluster
    clusters: dict[int, list[str]] = {i: [a.url] for i, a in enumerate(articles)}
    url_to_article = {a.url: a for a in articles}

    # Precompute pairwise similarities for efficiency
    urls = list(url_to_article.keys())
    pair_sim = {}
    for i in range(len(urls)):
        for j in range(i + 1, len(urls)):
            sim = compute_similarity(
                article_keywords.get(urls[i], []),
                article_keywords.get(urls[j], []),
            )
            if sim > similarity_threshold:
                pair_sim[(urls[i], urls[j])] = sim

    # Greedy agglomerative clustering using precomputed similarities
    url_to_cluster = {a.url: i for i, a in enumerate(articles)}

    # Sort candidate pairs by similarity (descending) for greedy merging
    sorted_pairs = sorted(pair_sim.items(), key=lambda x: -x[1])

    for (url_i, url_j), sim in sorted_pairs:
        ci, cj = url_to_cluster.get(url_i), url_to_cluster.get(url_j)
        if ci is None or cj is None or ci == cj:
            continue
        if len(clusters) <= max_clusters:
            # Merge smaller cluster into larger
            if len(clusters.get(ci, [])) < len(clusters.get(cj, [])):
                ci, cj = cj, ci
            for url in clusters.get(cj, []):
                url_to_cluster[url] = ci
            clusters.setdefault(ci, []).extend(clusters.pop(cj, []))

    # Build cluster output with scoring
    result = []
    singleton_urls = []  # Collect singletons for "其他" grouping

    for idx, (cluster_id, urls) in enumerate(clusters.items()):
        cluster_articles_list = [url_to_article[u] for u in urls if u in url_to_article]
        if not cluster_articles_list:
            continue

        # Singleton handling: collect for later grouping
        if len(cluster_articles_list) < 2:
            singleton_urls.extend(urls)
            continue

        # Derive theme from shared keywords
        all_kw_sets = [set(article_keywords.get(u, [])) for u in urls]
        if all_kw_sets:
            shared = all_kw_sets[0]
            for s in all_kw_sets[1:]:
                shared = shared & s
            if not shared:
                counter = Counter(kw for kws in all_kw_sets for kw in kws)
                shared = set(kw for kw, _ in counter.most_common(3))
            theme = ", ".join(list(shared)[:5])
        else:
            theme = cluster_articles_list[0].title[:50]

        sources = {a.source for a in cluster_articles_list}

        cluster_data = {
            "cluster_id": f"c{idx}",
            "theme": theme,
            "articles": cluster_articles_list,
            "size": len(cluster_articles_list),
            "cross_source": len(sources) > 1,
            "score": 0.0,
        }
        cluster_data["score"] = score_importance(cluster_data)
        result.append(cluster_data)

    # Group singletons into a single "其他" cluster
    if singleton_urls:
        singleton_articles = [url_to_article[u] for u in singleton_urls if u in url_to_article]
        if singleton_articles:
            # Derive theme from most common keywords
            all_kw = Counter()
            for u in singleton_urls:
                for kw in article_keywords.get(u, []):
                    all_kw[kw] += 1
            theme = ", ".join(kw for kw, _ in all_kw.most_common(3)) if all_kw else "其他"

            cluster_data = {
                "cluster_id": f"c_other",
                "theme": theme,
                "articles": singleton_articles,
                "size": len(singleton_articles),
                "cross_source": len({a.source for a in singleton_articles}) > 1,
                "score": 0.0,
            }
            cluster_data["score"] = score_importance(cluster_data)
            result.append(cluster_data)

    # Sort by score descending
    result.sort(key=lambda c: c["score"], reverse=True)

    multi = sum(1 for c in result if c["size"] > 1)
    logger.debug(f"Clustering: {len(articles)} articles -> {len(result)} clusters ({multi} multi-article)")
    return result


def score_importance(cluster: dict) -> float:
    """Score a cluster's importance (0.0 - 1.0).

    Factors: cluster size, cross-source corroboration, source authority,
    keyword signal strength.
    """
    score = 0.0
    articles = cluster.get("articles", [])
    if not articles:
        return score

    # Factor 1: Cluster size (0-0.25) — larger = more important
    size_score = min(cluster.get("size", 1) / 5.0, 1.0) * 0.25

    # Factor 2: Cross-source corroboration (0-0.25)
    cross_score = 0.25 if cluster.get("cross_source", False) else 0.05

    # Factor 3: Source authority (0-0.25) — average authority of sources
    authority_scores = []
    for article in articles:
        source = article.source or ""
        domain_auth = 0.4  # default tier 3
        for domain, weight in _AUTHORITY_DOMAINS.items():
            if domain in source.lower() or domain in source:
                domain_auth = weight
                break
        authority_scores.append(domain_auth)
    avg_authority = sum(authority_scores) / len(authority_scores) if authority_scores else 0.4
    auth_score = avg_authority * 0.25

    # Factor 4: Keyword signal strength (0-0.25)
    high_signal_count = 0
    for article in articles:
        text = f"{article.title} {article.description or ''}".lower()
        if any(kw in text for kw in HIGH_SIGNAL_KEYWORDS):
            high_signal_count += 1
    signal_ratio = high_signal_count / len(articles) if articles else 0
    signal_score = signal_ratio * 0.25

    score = size_score + cross_score + auth_score + signal_score
    return min(round(score, 3), 1.0)


def get_cluster_map(clusters: list[dict]) -> dict[str, dict]:
    """Build a url -> cluster_info lookup for enrichment.

    Returns:
        dict mapping article URL to {"cluster_id": str, "theme": str, "score": float}
    """
    cluster_map = {}
    for cluster in clusters:
        info = {
            "cluster_id": cluster["cluster_id"],
            "theme": cluster["theme"],
            "score": cluster["score"],
            "cluster_size": cluster["size"],
            "cross_source": cluster["cross_source"],
        }
        for article in cluster["articles"]:
            cluster_map[article.url] = info
    return cluster_map
