"""Tests for core/report_generator.py — table rendering and utilities."""

from core.article import Article
from core.report_generator import _escape_pipe, _render_hn_table


class TestEscapePipe:
    def test_pipe_escaped(self):
        assert _escape_pipe("a|b") == r"a\|b"

    def test_no_pipe(self):
        assert _escape_pipe("hello") == "hello"

    def test_multiple_pipes(self):
        assert _escape_pipe("a|b|c") == r"a\|b\|c"

    def test_newline_replaced(self):
        assert _escape_pipe("a\nb") == "a b"

    def test_pipe_and_newline(self):
        assert _escape_pipe("a|\nb") == r"a\| b"

    def test_empty_string(self):
        assert _escape_pipe("") == ""


class TestRenderHnTable:
    def _make_hn_item(self, title="Test HN Post", url="https://news.ycombinator.com/item?id=1",
                       source="HN", points=100, comments=42):
        return Article(
            title=title, url=url, source=source, category="hacker_news",
            published="2026-04-06T12:00:00",
            extra={"hn_points": points, "hn_comments": comments},
        )

    def test_empty_list_returns_empty(self):
        assert _render_hn_table([], "zh", "条") == []

    def test_basic_zh_rendering(self):
        items = [self._make_hn_item()]
        lines = _render_hn_table(items, "zh", "条")
        joined = "\n".join(lines)
        assert "Hacker News 热门" in joined
        assert "🔥 100" in joined
        assert "💬 42" in joined
        assert "Test HN Post" in joined

    def test_basic_en_rendering(self):
        items = [self._make_hn_item()]
        lines = _render_hn_table(items, "en", "items")
        joined = "\n".join(lines)
        assert "Hacker News Trending" in joined

    def test_with_summary_map(self):
        items = [self._make_hn_item(url="https://hn.test/1")]
        sm = {"https://hn.test/1": {"ai_summary": "AI says hello"}}
        lines = _render_hn_table(items, "zh", "条", summary_map=sm)
        joined = "\n".join(lines)
        assert "摘要" in joined
        assert "AI says hello" in joined

    def test_without_summary_map_no_summary_column(self):
        items = [self._make_hn_item()]
        lines = _render_hn_table(items, "zh", "条")
        joined = "\n".join(lines)
        assert "摘要" not in joined

    def test_pipe_in_title_escaped(self):
        items = [self._make_hn_item(title="A|B")]
        lines = _render_hn_table(items, "zh", "条")
        joined = "\n".join(lines)
        assert r"A\|B" in joined
