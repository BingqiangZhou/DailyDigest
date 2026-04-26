"""Tests for the editorial pipeline module."""

import pytest
from datetime import datetime, timezone
from core.article import Article
from core.editorial import (
    compute_article_authority,
    compute_article_novelty,
    compute_news_value,
    assign_editorial_tier,
    allocate_depth,
    run_editorial_pipeline,
)


def _make_article(title="Test", url="https://example.com/1",
                  source="TestSource", category="ai_ml",
                  priority=3, hn_points=None, description=""):
    """Helper to create an Article with common defaults."""
    extra = {"priority": priority}
    if hn_points is not None:
        extra["hn_points"] = hn_points
    return Article(
        title=title,
        url=url,
        source=source,
        category=category,
        published="2026-04-27T08:00:00",
        description=description,
        language="en",
        extra=extra,
    )


class TestComputeArticleAuthority:
    def test_tier1_openai(self):
        a = _make_article(source="OpenAI Blog")
        assert compute_article_authority(a) == 1.0

    def test_tier1_anthropic(self):
        a = _make_article(source="Anthropic")
        assert compute_article_authority(a) == 1.0

    def test_tier2_techcrunch(self):
        a = _make_article(source="TechCrunch")
        assert compute_article_authority(a) == 0.7

    def test_tier2_36kr(self):
        a = _make_article(source="36kr", url="https://36kr.com/article/1")
        assert compute_article_authority(a) == 0.7

    def test_priority1_fallback(self):
        a = _make_article(source="Unknown Source", priority=1)
        assert compute_article_authority(a) == 0.8

    def test_priority2_fallback(self):
        a = _make_article(source="Unknown Source", priority=2)
        assert compute_article_authority(a) == 0.6

    def test_priority3_default(self):
        a = _make_article(source="Unknown Source", priority=3)
        assert compute_article_authority(a) == 0.4


class TestComputeArticleNovelty:
    def test_no_cluster_info(self):
        a = _make_article()
        assert compute_article_novelty(a, {}) == 0.5

    def test_singleton_cluster(self):
        a = _make_article()
        cluster_map = {a.url: {"cluster_size": 1, "cross_source": False}}
        assert compute_article_novelty(a, cluster_map) == 0.5

    def test_multi_article_cluster(self):
        a = _make_article(source="OpenAI Blog")
        cluster_map = {a.url: {"cluster_size": 3, "cross_source": True}}
        novelty = compute_article_novelty(a, cluster_map)
        assert 0.0 < novelty <= 1.0


class TestComputeNewsValue:
    def test_minimal_article(self):
        a = _make_article()
        result = compute_news_value(a, {})
        assert "composite" in result
        assert 0.0 <= result["composite"] <= 1.0
        assert "authority" in result
        assert "cross_source" in result
        assert "cluster_heat" in result
        assert "signal" in result
        assert "novelty" in result

    def test_high_value_article(self):
        a = _make_article(
            title="OpenAI announces GPT-5 breakthrough",
            source="OpenAI Blog",
            priority=1,
            description="OpenAI release breakthrough first",
        )
        cluster_map = {a.url: {"cluster_size": 5, "cross_source": True}}
        result = compute_news_value(a, cluster_map)
        assert result["composite"] >= 0.6

    def test_low_value_article(self):
        a = _make_article(
            source="Unknown Source",
            priority=3,
            description="routine update",
        )
        result = compute_news_value(a, {})
        assert result["composite"] < 0.5

    def test_signal_keywords(self):
        a = _make_article(title="Breakthrough in AI release")
        result = compute_news_value(a, {})
        assert result["signal"] > 0


class TestAssignEditorialTier:
    def test_must_read(self):
        a = _make_article()
        a.extra["news_value_score"] = 0.80
        assert assign_editorial_tier(a) == "must_read"

    def test_noteworthy(self):
        a = _make_article()
        a.extra["news_value_score"] = 0.50
        assert assign_editorial_tier(a) == "noteworthy"

    def test_brief(self):
        a = _make_article()
        a.extra["news_value_score"] = 0.20
        assert assign_editorial_tier(a) == "brief"

    def test_hn_promotion(self):
        a = _make_article(hn_points=300)
        a.extra["news_value_score"] = 0.35
        assert assign_editorial_tier(a) == "noteworthy"

    def test_priority1_promotion(self):
        a = _make_article(priority=1)
        a.extra["news_value_score"] = 0.35
        assert assign_editorial_tier(a) == "noteworthy"


class TestAllocateDepth:
    def test_must_read_gets_deep(self):
        a = _make_article()
        a.extra["editorial_tier"] = "must_read"
        assert allocate_depth(a) == "deep_analysis"

    def test_noteworthy_gets_summary(self):
        a = _make_article()
        a.extra["editorial_tier"] = "noteworthy"
        assert allocate_depth(a) == "summary_only"

    def test_brief_gets_headline(self):
        a = _make_article()
        a.extra["editorial_tier"] = "brief"
        assert allocate_depth(a) == "headline_only"


class TestRunEditorialPipeline:
    def test_basic_pipeline(self):
        articles = [
            _make_article(title="OpenAI GPT-5", source="OpenAI Blog", priority=1,
                          description="breakthrough release"),
            _make_article(title="Routine update", source="Random Blog", priority=3,
                          description="minor fix"),
        ]
        cluster_map = {
            articles[0].url: {"cluster_size": 3, "cross_source": True},
            articles[1].url: {"cluster_size": 1, "cross_source": False},
        }
        approved, stats = run_editorial_pipeline(articles, cluster_map)

        assert len(approved) <= len(articles)
        assert "must_read" in stats
        assert "noteworthy" in stats
        assert "brief" in stats

        for a in approved:
            assert "news_value_score" in a.extra
            assert "editorial_tier" in a.extra
            assert "depth" in a.extra

    def test_all_articles_approved_with_low_threshold(self):
        a = _make_article()
        approved, stats = run_editorial_pipeline([a], {})
        # Single low-value article might be filtered
        assert stats["total_input"] == 1

    def test_empty_input(self):
        approved, stats = run_editorial_pipeline([], {})
        assert approved == []
        assert stats["approved"] == 0
