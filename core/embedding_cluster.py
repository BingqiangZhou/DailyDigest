"""
Embedding-based semantic clustering for topic discovery.

Replaces LLM-based grouping with a two-stage approach:
  1. Embed all articles via API, cluster with AgglomerativeClustering
  2. LLM interprets each cluster (title, summary, importance) separately

This ensures zero topic omission — every article is seen by the clustering algorithm.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from .config import (
    WORKSPACE_DIR, EMBEDDING_CACHE_ENABLED,
    EMBEDDING_DISTANCE_THRESHOLD, EMBEDDING_MIN_CLUSTER_SIZE,
    EMBEDDING_MAX_CLUSTERS, REPORT_ARTICLES_PER_THEME,
)
from .logging_config import get_logger

logger = get_logger("embedding_cluster")

_CACHE_PATH = WORKSPACE_DIR / "embedding_cache.json"
_CACHE_MAX_ENTRIES = int(os.environ.get("EMBEDDING_CACHE_MAX", "10000"))


def _cleanup_cache(cache):
    """Evict oldest entries when cache exceeds max size."""
    if len(cache) <= _CACHE_MAX_ENTRIES:
        return cache
    items = list(cache.items())
    return dict(items[-_CACHE_MAX_ENTRIES:])


def _text_fingerprint(title, description=""):
    text = f"{title}|||{description or ''}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_cache():
    if not EMBEDDING_CACHE_ENABLED or not _CACHE_PATH.exists():
        return {}
    try:
        with open(_CACHE_PATH, "r") as f:
            return _cleanup_cache(json.load(f))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache):
    if not EMBEDDING_CACHE_ENABLED:
        return
    cache = _cleanup_cache(cache)
    try:
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_PATH, "w") as f:
            json.dump(cache, f)
    except OSError as e:
        logger.warning(f"Failed to save embedding cache: {e}")


def embed_articles(articles):
    """Embed all articles using the configured Embedding API.

    Uses per-article caching to avoid re-computing embeddings for
    articles whose title+description hasn't changed.

    Returns:
        numpy.ndarray of shape (n_articles, embedding_dim)
    """
    from .llm import get_llm_client, embed_texts

    if not articles:
        return np.array([])

    cache = _load_cache()
    fingerprints = []
    texts = []
    uncached_indices = []

    for i, article in enumerate(articles):
        fp = _text_fingerprint(article.title, article.description)
        fingerprints.append(fp)
        text = f"{article.title or ''} {article.description or ''}".strip()
        text = text[:512]
        texts.append(text)
        if fp not in cache:
            uncached_indices.append(i)

    # Compute embeddings for uncached articles
    if uncached_indices:
        uncached_texts = [texts[i] for i in uncached_indices]
        logger.info(f"[Embed] Computing {len(uncached_texts)} new embeddings "
                     f"({len(articles) - len(uncached_indices)} cached)...")

        client = get_llm_client()
        vectors = embed_texts(client, uncached_texts)

        if len(vectors) != len(uncached_indices):
            logger.error(f"[Embed] Expected {len(uncached_indices)} vectors, got {len(vectors)}")
            # Fall back: embed everything fresh
            vectors = embed_texts(get_llm_client(), texts)
            if not vectors:
                raise RuntimeError("Embedding API failed completely")
            uncached_indices = list(range(len(articles)))

        for idx, vec in zip(uncached_indices, vectors):
            cache[fingerprints[idx]] = vec

        _save_cache(cache)
    else:
        logger.info(f"[Embed] All {len(articles)} embeddings found in cache")

    # Assemble in order
    dim = None
    for vec in cache.values():
        dim = len(vec)
        break

    if dim is None:
        raise RuntimeError("No embeddings available")

    embeddings = np.zeros((len(articles), dim))
    for i, fp in enumerate(fingerprints):
        vec = cache.get(fp)
        if vec:
            embeddings[i] = vec

    return embeddings


def cluster_by_embedding(articles, embeddings,
                         distance_threshold=None,
                         min_cluster_size=None,
                         max_clusters=None):
    """Cluster articles by embedding cosine distance.

    Returns:
        list[dict] — sorted clusters, each with:
          - id: int cluster label
          - articles: list[Article] sorted by score desc
          - score: max news_value_score in cluster
          - cross_source: bool — whether cluster spans 3+ sources
          - source_count: int — distinct sources
          - size: int — number of articles
          - is_singleton: bool — cluster has fewer than min_cluster_size articles
    """
    if len(articles) < 2:
        return [_make_singleton(0, articles)]

    distance_threshold = distance_threshold or EMBEDDING_DISTANCE_THRESHOLD
    min_cluster_size = min_cluster_size or EMBEDDING_MIN_CLUSTER_SIZE
    max_clusters = max_clusters or EMBEDDING_MAX_CLUSTERS

    clustering = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=distance_threshold,
    )
    labels = clustering.fit_predict(embeddings)
    n_clusters = len(set(labels))

    logger.info(f"[Cluster] Agglomerative produced {n_clusters} clusters "
                f"(threshold={distance_threshold}, articles={len(articles)})")

    # If too many clusters, re-cluster with a tighter threshold
    if n_clusters > max_clusters:
        tighter = distance_threshold * 0.8
        logger.info(f"[Cluster] Too many clusters ({n_clusters}), re-clustering with threshold={tighter:.3f}")
        clustering = AgglomerativeClustering(
            n_clusters=max_clusters,
            metric="cosine",
            linkage="average",
        )
        labels = clustering.fit_predict(embeddings)
        n_clusters = len(set(labels))

    # Group articles by cluster label
    cluster_groups = {}
    for i, label in enumerate(labels):
        cluster_groups.setdefault(int(label), []).append(i)

    # Build cluster dicts
    clusters = []
    for cluster_id, indices in cluster_groups.items():
        cluster_articles = [articles[i] for i in indices]
        cluster_articles.sort(
            key=lambda a: a.extra.get("news_value_score", 0),
            reverse=True,
        )
        sources = {a.source for a in cluster_articles if a.source}
        score = max(a.extra.get("news_value_score", 0) for a in cluster_articles)
        is_singleton = len(cluster_articles) < min_cluster_size

        clusters.append({
            "id": cluster_id,
            "articles": cluster_articles[:REPORT_ARTICLES_PER_THEME * 3],
            "score": score,
            "cross_source": len(sources) >= 3,
            "source_count": len(sources),
            "size": len(cluster_articles),
            "is_singleton": is_singleton,
        })

    # Sort: non-singletons first, then by score desc
    clusters.sort(key=lambda c: (c["is_singleton"], -c["score"], -c["size"]))
    return clusters


def _make_singleton(cluster_id, articles):
    sources = {a.source for a in articles if a.source}
    score = max((a.extra.get("news_value_score", 0) for a in articles), default=0)
    return {
        "id": cluster_id,
        "articles": articles,
        "score": score,
        "cross_source": len(sources) >= 3,
        "source_count": len(sources),
        "size": len(articles),
        "is_singleton": True,
    }


def get_cluster_leftovers(clusters, articles):
    """Return articles that belong to singleton clusters or are in excess of display limits."""
    used = set()
    for c in clusters:
        if not c["is_singleton"]:
            for a in c["articles"][:REPORT_ARTICLES_PER_THEME]:
                used.add(id(a))

    leftovers = []
    for c in clusters:
        if c["is_singleton"]:
            leftovers.extend(c["articles"])
        else:
            for a in c["articles"][REPORT_ARTICLES_PER_THEME:]:
                leftovers.append(a)

    leftovers.sort(key=lambda a: a.extra.get("news_value_score", 0), reverse=True)
    return leftovers
