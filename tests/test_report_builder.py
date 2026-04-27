"""Tests for report_builder.py — data dashboard, highlights, and report assembly."""

from datetime import datetime, timezone
from core.article import Article
from core.report_builder import _build_data_dashboard, _build_highlights


def _make_article(title="Test", source="TestSource", category="ai_ml",
                  editorial_tier=None, news_value_score=None):
    extra = {}
    if editorial_tier:
        extra["editorial_tier"] = editorial_tier
    if news_value_score is not None:
        extra["news_value_score"] = news_value_score
    return Article(
        title=title,
        url=f"https://example.com/{title}",
        source=source,
        category=category,
        published="2026-04-27T08:00:00",
        description="test description",
        extra=extra,
    )


class TestBuildDataDashboard:
    def test_zh_basic_dashboard(self):
        ai = [_make_article("AI article 1"), _make_article("AI article 2")]
        non_ai = [_make_article("Tech article", category="tech_general")]
        result = _build_data_dashboard(ai, non_ai, {}, language="zh")
        assert "📊 数据概览" in result
        assert "3" in result  # total
        assert "2" in result  # AI count

    def test_en_basic_dashboard(self):
        ai = [_make_article("AI article")]
        non_ai = [_make_article("Tech article", category="tech_general")]
        result = _build_data_dashboard(ai, non_ai, {}, language="en")
        assert "📊 Data Overview" in result
        assert "2" in result  # total

    def test_empty_articles(self):
        result = _build_data_dashboard([], [], {})
        assert result == ""

    def test_cluster_info_shown(self):
        ai = [_make_article("AI 1"), _make_article("AI 2")]
        cluster_map = {
            ai[0].url: {"cluster_size": 3, "cross_source": True},
            ai[1].url: {"cluster_size": 2, "cross_source": False},
        }
        result = _build_data_dashboard(ai, [], cluster_map, "zh")
        assert "话题聚类" in result
        assert "跨源验证" in result

    def test_editorial_tiers_shown(self):
        ai = [
            _make_article("Must read", editorial_tier="must_read"),
            _make_article("Noteworthy", editorial_tier="noteworthy"),
            _make_article("Brief", editorial_tier="brief"),
        ]
        result = _build_data_dashboard(ai, [], {}, "zh")
        assert "必读" in result
        assert "值得关注" in result

    def test_no_tiers_hides_tier_rows(self):
        ai = [_make_article("No tier")]
        result = _build_data_dashboard(ai, [], {}, "zh")
        assert "必读" not in result


class TestBuildHighlights:
    def test_zh_highlights_from_must_read(self):
        ai = [
            _make_article("Big AI news", editorial_tier="must_read", news_value_score=0.9),
            _make_article("Lesser news", editorial_tier="noteworthy", news_value_score=0.5),
        ]
        result = _build_highlights(ai, [], {}, "zh")
        assert "🔥 今日要点" in result
        assert "Big AI news" in result

    def test_en_highlights(self):
        ai = [_make_article("Must read", editorial_tier="must_read", news_value_score=0.9)]
        result = _build_highlights(ai, [], {}, "en")
        assert "🔥 Today's Highlights" in result
        assert "Must read" in result

    def test_empty_articles_no_highlights(self):
        result = _build_highlights([], [], {}, "zh")
        assert result == ""

    def test_no_tier_articles_uses_noteworthy(self):
        ai = [_make_article("Noteworthy", editorial_tier="noteworthy", news_value_score=0.5)]
        result = _build_highlights(ai, [], {}, "zh")
        assert "Noteworthy" in result

    def test_hn_engagement_shown(self):
        ai = [Article(
            title="HN Post", url="https://hn.test/1", source="HN",
            category="ai_ml", published="2026-04-27T12:00:00",
            extra={"editorial_tier": "must_read", "news_value_score": 0.9, "hn_points": 200},
        )]
        result = _build_highlights(ai, [], {}, "zh")
        assert "🔥HN 200" in result

    def test_mixed_ai_and_non_ai(self):
        ai = [_make_article("AI news", editorial_tier="must_read", news_value_score=0.9)]
        non_ai = [_make_article("Tech news", category="tech_general",
                                editorial_tier="must_read", news_value_score=0.8)]
        result = _build_highlights(ai, non_ai, {}, "zh")
        assert "AI news" in result
        assert "Tech news" in result
