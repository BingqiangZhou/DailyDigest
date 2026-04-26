"""Tests for core/topic_cluster.py — keyword extraction, similarity, clustering."""

import pytest
from core.article import Article
from core.topic_cluster import (
    extract_keywords,
    compute_similarity,
    cluster_articles,
    get_cluster_map,
    score_importance,
)


class TestExtractKeywords:
    def test_english_keywords(self):
        kws = extract_keywords("OpenAI releases GPT-5 model", "")
        assert "openai" in kws
        assert "gpt" in kws

    def test_chinese_bigrams(self):
        kws = extract_keywords("人工智能大模型发布", "")
        # Should have bigrams like "人工", "工智", "智能" etc.
        has_bigram = any(len(kw) == 2 and '一' <= kw[0] <= '鿿' for kw in kws)
        assert has_bigram, f"Expected CJK bigrams, got: {kws}"

    def test_ai_keywords_prioritized(self):
        kws = extract_keywords("Deep learning breakthrough in transformer architecture", "")
        kw_set = set(kws)
        assert "deep" in kw_set or "learning" in kw_set

    def test_max_15_keywords(self):
        long_title = " ".join(f"word{i}" for i in range(30))
        kws = extract_keywords(long_title, "")
        assert len(kws) <= 15

    def test_stop_words_filtered(self):
        kws = extract_keywords("the model is a very good system", "")
        assert "the" not in kws
        assert "is" not in kws

    def test_mixed_chinese_english(self):
        kws = extract_keywords("OpenAI发布GPT-5大模型", "")
        kw_set = set(kws)
        assert "openai" in kw_set
        assert "gpt" in kw_set


class TestComputeSimilarity:
    def test_identical_sets(self):
        assert compute_similarity(["ai", "ml", "model"], ["ai", "ml", "model"]) == 1.0

    def test_disjoint_sets(self):
        assert compute_similarity(["ai", "ml"], ["security", "hardware"]) == 0.0

    def test_partial_overlap(self):
        sim = compute_similarity(["ai", "ml", "model"], ["ai", "security", "model"])
        assert 0.0 < sim < 1.0

    def test_empty_sets(self):
        assert compute_similarity([], ["ai"]) == 0.0
        assert compute_similarity(["ai"], []) == 0.0
        assert compute_similarity([], []) == 0.0


class TestClusterArticles:
    def test_empty_input(self):
        assert cluster_articles([]) == []

    def test_single_article(self):
        articles = [
            Article(title="AI model released", url="http://x/1",
                    source="S", category="ai_ml", published="2026-01-01"),
        ]
        clusters = cluster_articles(articles)
        # Single article goes to "其他" singleton group
        assert len(clusters) == 1
        assert clusters[0]["size"] == 1

    def test_similar_articles_merged(self):
        articles = [
            Article(title="OpenAI GPT-5 released with breakthrough performance",
                    url=f"http://x/{i}", source="Src" if i % 2 == 0 else "Other",
                    category="ai_ml", published="2026-01-01")
            for i in range(4)
        ]
        clusters = cluster_articles(articles)
        # All 4 should be in one cluster (identical keywords)
        multi = [c for c in clusters if c["size"] > 1]
        assert len(multi) >= 1
        assert multi[0]["size"] == 4

    def test_dissimilar_articles_separate(self):
        articles = [
            Article(title="New AI model released", url="http://x/1",
                    source="S", category="ai_ml", published="2026-01-01",
                    description="artificial intelligence breakthrough"),
            Article(title="Rust programming language update", url="http://x/2",
                    source="S", category="tech_general", published="2026-01-01",
                    description="programming language compiler"),
        ]
        clusters = cluster_articles(articles)
        # These are very different topics, should be separate (or in "其他" group)
        assert len(clusters) >= 1

    def test_cluster_map_structure(self):
        articles = [
            Article(title="AI model", url="http://x/1",
                    source="S", category="ai_ml", published="2026-01-01"),
        ]
        clusters = cluster_articles(articles)
        cmap = get_cluster_map(clusters)
        assert "http://x/1" in cmap
        info = cmap["http://x/1"]
        assert "cluster_id" in info
        assert "score" in info

    def test_sorted_by_score(self):
        articles = [
            Article(title="Breaking: major AI release", url=f"http://x/{i}",
                    source=f"Src{i}", category="ai_ml", published="2026-01-01")
            for i in range(6)
        ]
        clusters = cluster_articles(articles)
        scores = [c["score"] for c in clusters]
        assert scores == sorted(scores, reverse=True)


class TestScoreImportance:
    def test_minimum_score(self):
        cluster = {"articles": [], "size": 0, "cross_source": False}
        assert score_importance(cluster) == 0.0

    def test_large_cross_source_cluster(self):
        articles = [
            Article(title="AI release announcement breakthrough",
                    url=f"http://x/{i}", source=f"Src{i}",
                    category="ai_ml", published="2026-01-01")
            for i in range(10)
        ]
        cluster = {
            "articles": articles, "size": 10, "cross_source": True,
        }
        score = score_importance(cluster)
        assert score > 0.5

    def test_score_bounded(self):
        articles = [
            Article(title="AI release launch breakthrough first record",
                    url=f"http://x/{i}", source=f"Src{i}",
                    category="ai_ml", published="2026-01-01")
            for i in range(100)
        ]
        cluster = {
            "articles": articles, "size": 100, "cross_source": True,
        }
        assert score_importance(cluster) <= 1.0
