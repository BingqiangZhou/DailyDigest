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
        language="en",
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
        language="zh",
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
        language="en",
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
