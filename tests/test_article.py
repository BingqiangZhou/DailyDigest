"""Tests for core/article.py — Article dataclass and properties."""

from core.article import Article


class TestArticleConstruction:
    def test_minimal_construction(self):
        a = Article(title="T", url="http://x", source="S", category="ai_ml", published="2026-01-01")
        assert a.title == "T"
        assert a.url == "http://x"
        assert a.source == "S"
        assert a.category == "ai_ml"
        assert a.published == "2026-01-01"
        assert a.description == ""
        assert a.full_text == ""
        assert a.language == "en"
        assert a.extra == {}

    def test_full_construction(self):
        a = Article(
            title="T", url="http://x", source="S", category="ai_ml",
            published="2026-01-01", description="D", full_text="FT",
            language="zh", extra={"hn_points": 10},
        )
        assert a.description == "D"
        assert a.full_text == "FT"
        assert a.language == "zh"
        assert a.extra["hn_points"] == 10

    def test_default_extra_is_independent(self):
        a1 = Article(title="T1", url="http://1", source="S", category="ai_ml", published="2026-01-01")
        a2 = Article(title="T2", url="http://2", source="S", category="ai_ml", published="2026-01-01")
        a1.extra["key"] = "val"
        assert "key" not in a2.extra


class TestArticleProperties:
    def test_hn_points_present(self):
        a = Article(title="T", url="http://x", source="S", category="ai_ml",
                    published="2026-01-01", extra={"hn_points": 42})
        assert a.hn_points == 42

    def test_hn_points_absent(self):
        a = Article(title="T", url="http://x", source="S", category="ai_ml", published="2026-01-01")
        assert a.hn_points is None

    def test_hn_comments_present(self):
        a = Article(title="T", url="http://x", source="S", category="ai_ml",
                    published="2026-01-01", extra={"hn_comments": 5})
        assert a.hn_comments == 5

    def test_hn_comments_absent(self):
        a = Article(title="T", url="http://x", source="S", category="ai_ml", published="2026-01-01")
        assert a.hn_comments is None

    def test_priority_present(self):
        a = Article(title="T", url="http://x", source="S", category="ai_ml",
                    published="2026-01-01", extra={"priority": 1})
        assert a.priority == 1

    def test_priority_default(self):
        a = Article(title="T", url="http://x", source="S", category="ai_ml", published="2026-01-01")
        assert a.priority == 3

    def test_rank_present(self):
        a = Article(title="T", url="http://x", source="S", category="ai_ml",
                    published="2026-01-01", extra={"rank": 5})
        assert a.rank == 5

    def test_rank_default(self):
        a = Article(title="T", url="http://x", source="S", category="ai_ml", published="2026-01-01")
        assert a.rank == 0
