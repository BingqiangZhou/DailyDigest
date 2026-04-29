"""Shared test fixtures for DailyDigest tests."""

import pytest
from datetime import datetime, timezone
from core.article import Article


@pytest.fixture
def sample_article():
    """A minimal Article for testing."""
    return Article(
        title="Test Article",
        url="https://example.com/1",
        source="TestSource",
        category="ai_ml",
        published="2026-04-06T08:00:00",
        description="A test article description",
    )


@pytest.fixture
def sample_article_zh():
    """A Chinese-language Article."""
    return Article(
        title="测试文章",
        url="https://example.com/zh/1",
        source="测试来源",
        category="ai_ml",
        published="2026-04-06T08:00:00",
        description="测试描述",
    )


@pytest.fixture
def sample_article_hn():
    """A Hacker News Article with points/comments."""
    return Article(
        title="HN: Show HN: Cool Project",
        url="https://news.ycombinator.com/item?id=12345",
        source="Hacker News",
        category="hacker_news",
        published="2026-04-06T12:00:00",
        description="A cool project",
        extra={"hn_points": 42, "hn_comments": 7, "priority": 1},
    )


@pytest.fixture
def sample_articles(sample_article, sample_article_zh, sample_article_hn):
    """A list of diverse sample articles."""
    return [sample_article, sample_article_zh, sample_article_hn]


@pytest.fixture
def now_utc():
    """Fixed UTC datetime for deterministic tests."""
    return datetime(2026, 4, 6, 5, 30, tzinfo=timezone.utc)


def make_article(title="Test Article", url=None, source="TestSource", category="ai_ml",
                 published="2026-04-27T12:00:00", description="", language=None,
                 priority=None, hn_points=None, tier=None, score=None, extra=None):
    """Flexible Article factory for tests. Extra kwargs override auto-set fields."""
    _extra = extra or {}
    if priority is not None:
        _extra.setdefault("priority", priority)
    if hn_points is not None:
        _extra.setdefault("hn_points", hn_points)
    if tier is not None:
        _extra.setdefault("editorial_tier", tier)
    if score is not None:
        _extra.setdefault("news_value_score", score)
    kwargs = dict(
        title=title,
        url=url or f"https://test/{title}",
        source=source,
        category=category,
        published=published,
        description=description or f"Description of {title}",
        extra=_extra,
    )
    if language:
        kwargs["language"] = language
    return Article(**kwargs)
