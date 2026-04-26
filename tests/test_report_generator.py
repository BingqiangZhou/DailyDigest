"""Tests for core/report_generator.py — table rendering and utilities."""

import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from core.article import Article
from core.report_generator import _escape_pipe, _render_hn_table, generate_tech_report, _select_non_ai_articles
from core.report_builder import _merge_llm_summaries, build_unified_report, _generate_importance_reason


def _no_api_key():
    """Context manager that ensures API_KEY is unset."""
    env = dict(os.environ)
    env.pop("API_KEY", None)
    return patch.dict(os.environ, env, clear=True)


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


class TestMergeLlmSummaries:
    def test_merges_summary_into_tiered(self):
        editorial = {
            "ai_ml": {
                "name": "AI/ML",
                "articles": [],
                "tiered": {"must_read": [], "noteworthy": [], "brief": []},
                "article_count": 5,
            }
        }
        llm = {
            "ai_ml": {
                "name": "AI/ML",
                "summary": "GPT-5.5 released with improvements",
                "article_count": 5,
                "articles": [],
            }
        }
        result = _merge_llm_summaries(editorial, llm)
        assert result["ai_ml"]["tiered"]["category_summary"] == "GPT-5.5 released with improvements"

    def test_no_llm_summary_no_crash(self):
        editorial = {
            "ai_ml": {
                "name": "AI/ML",
                "articles": [],
                "tiered": {"must_read": [], "noteworthy": [], "brief": []},
                "article_count": 5,
            }
        }
        result = _merge_llm_summaries(editorial, {})
        assert "category_summary" not in result["ai_ml"]["tiered"]

    def test_preserves_editorial_tier_data(self):
        editorial = {
            "ai_ml": {
                "name": "AI/ML",
                "articles": ["a1", "a2"],
                "tiered": {"must_read": [{"index": 1, "summary": "important"}], "noteworthy": [], "brief": []},
                "article_count": 2,
            }
        }
        llm = {
            "ai_ml": {"name": "AI/ML", "summary": "LLM summary", "article_count": 2, "articles": []}
        }
        result = _merge_llm_summaries(editorial, llm)
        assert result["ai_ml"]["tiered"]["must_read"] == [{"index": 1, "summary": "important"}]
        assert result["ai_ml"]["tiered"]["category_summary"] == "LLM summary"


class TestTrendInsightsInReport:
    def _make_article(self, title="Test", category="ai_ml", tier="noteworthy"):
        return Article(
            title=title, url=f"https://test/{title}",
            source="TestSource", category=category,
            published="2026-04-27T12:00:00",
            extra={"editorial_tier": tier, "news_value_score": 0.5},
        )

    def test_trend_insights_rendered_in_api_mode(self):
        articles = [self._make_article()]
        category_results = {
            "ai_ml": {
                "name": "AI/ML",
                "articles": articles,
                "tiered": {"must_read": [], "noteworthy": [], "brief": []},
                "article_count": 1,
            }
        }
        report = generate_tech_report(
            articles,
            category_results=category_results,
            stats={"total_articles": 1, "categories": 1},
            report_language="zh",
            trend_insights="**多模态融合**: 各大厂商加速多模态模型迭代。",
        )
        assert "📊 趋势洞察" in report
        assert "多模态融合" in report

    def test_trend_insights_en_rendered(self):
        articles = [self._make_article()]
        category_results = {
            "ai_ml": {
                "name": "AI/ML",
                "articles": articles,
                "tiered": {"must_read": [], "noteworthy": [], "brief": []},
                "article_count": 1,
            }
        }
        report = generate_tech_report(
            articles,
            category_results=category_results,
            stats={"total_articles": 1, "categories": 1},
            report_language="en",
            trend_insights="**Multi-modal convergence**: Vendors accelerate multi-modal models.",
        )
        assert "📊 Trend Insights" in report
        assert "Multi-modal convergence" in report

    def test_no_trend_insights_no_section(self):
        articles = [self._make_article()]
        category_results = {
            "ai_ml": {
                "name": "AI/ML",
                "articles": articles,
                "tiered": {"must_read": [], "noteworthy": [], "brief": []},
                "article_count": 1,
            }
        }
        report = generate_tech_report(
            articles,
            category_results=category_results,
            stats={"total_articles": 1, "categories": 1},
            report_language="zh",
        )
        assert "📊 趋势洞察" not in report


class TestSelectNonAiArticles:
    def _make_article(self, title, tier=None, score=0.0):
        extra = {"news_value_score": score}
        if tier:
            extra["editorial_tier"] = tier
        return Article(
            title=title, url=f"https://test/{title}",
            source="TestSource", category="tech_general",
            published="2026-04-27T12:00:00",
            extra=extra,
        )

    def test_under_limit_returns_all(self):
        articles = [self._make_article(f"a{i}", "noteworthy", 0.5) for i in range(5)]
        result = _select_non_ai_articles(articles, 10)
        assert len(result) == 5

    def test_must_read_always_kept(self):
        must_reads = [self._make_article(f"must{i}", "must_read", 0.9) for i in range(8)]
        briefs = [self._make_article(f"brief{i}", "brief", 0.2) for i in range(50)]
        result = _select_non_ai_articles(must_reads + briefs, 10)
        assert len(result) == 10
        assert all(a.extra.get("editorial_tier") == "must_read" for a in result[:8])

    def test_noteworthy_prioritized_over_brief(self):
        noteworthies = [self._make_article(f"note{i}", "noteworthy", 0.5) for i in range(10)]
        briefs = [self._make_article(f"brief{i}", "brief", 0.2) for i in range(10)]
        result = _select_non_ai_articles(noteworthies + briefs, 12)
        assert len(result) == 12
        note_count = sum(1 for a in result if a.extra.get("editorial_tier") == "noteworthy")
        assert note_count == 10  # All noteworthy kept before brief

    def test_sorted_by_score_within_tier(self):
        articles = [
            self._make_article("low", "noteworthy", 0.3),
            self._make_article("high", "noteworthy", 0.8),
            self._make_article("mid", "noteworthy", 0.5),
        ]
        result = _select_non_ai_articles(articles, 2)
        assert result[0].title == "high"
        assert result[1].title == "mid"


class TestMultilineBlockquote:
    def _make_article(self, title="Test", category="ai_ml", tier="noteworthy"):
        return Article(
            title=title, url=f"https://test/{title}",
            source="TestSource", category=category,
            published="2026-04-27T12:00:00",
            extra={"editorial_tier": tier, "news_value_score": 0.5},
        )

    def test_multiline_category_summary_properly_quoted(self):
        articles = [self._make_article()]
        category_results = {
            "ai_ml": {
                "name": "AI/ML",
                "articles": articles,
                "tiered": {
                    "must_read": [], "noteworthy": [], "brief": [],
                    "category_summary": "Line one\n**Line two**\n- Line three",
                },
                "article_count": 1,
            }
        }
        report = generate_tech_report(
            articles,
            category_results=category_results,
            stats={"total_articles": 1, "categories": 1},
            report_language="zh",
        )
        assert "> Line one" in report
        assert "> **Line two**" in report
        assert "> - Line three" in report


class TestUnifiedReportToc:
    def test_toc_present_in_two_part_report(self):
        ai_article = Article(
            title="AI article", url="https://test/ai",
            source="TestSource", category="ai_ml",
            published="2026-04-27T12:00:00",
            extra={"editorial_tier": "noteworthy", "news_value_score": 0.5},
        )
        non_ai_article = Article(
            title="Non-AI article", url="https://test/nonai",
            source="TestSource", category="tech_general",
            published="2026-04-27T12:00:00",
            extra={},
        )
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        with _no_api_key():
            report = build_unified_report(
                [ai_article], [non_ai_article], now, "zh",
                llm_category_results={"ai_ml": {"name": "AI/ML", "summary": "Test", "articles": [], "article_count": 1}},
                executive_summary="Test summary",
            )
        assert "📑 快速导航" in report
        assert "AI 深度日报" in report
        assert "科技动态" in report
        assert "🔥 今日亮点" in report

    def test_no_toc_when_only_one_part(self):
        ai_article = Article(
            title="AI article", url="https://test/ai",
            source="TestSource", category="ai_ml",
            published="2026-04-27T12:00:00",
            extra={"editorial_tier": "noteworthy", "news_value_score": 0.5},
        )
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        with _no_api_key():
            report = build_unified_report(
                [ai_article], [], now, "zh",
                llm_category_results={"ai_ml": {"name": "AI/ML", "summary": "Test", "articles": [], "article_count": 1}},
            )
        assert "📑" not in report
        # Highlights should still appear for articles with tier data
        assert "🔥 今日亮点" in report

    def test_no_double_separator_in_single_part_report(self):
        ai_article = Article(
            title="AI article", url="https://test/ai",
            source="TestSource", category="ai_ml",
            published="2026-04-27T12:00:00",
            extra={"editorial_tier": "noteworthy", "news_value_score": 0.5},
        )
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        with _no_api_key():
            report = build_unified_report(
                [ai_article], [], now, "zh",
                llm_category_results={"ai_ml": {"name": "AI/ML", "summary": "Test", "articles": [], "article_count": 1}},
            )
        assert "---\n---" not in report


class TestImportanceReason:
    def _make_article(self, title="Test", priority=1, hn_points=0, extra=None):
        e = extra or {}
        if priority:
            e["priority"] = priority
        if hn_points:
            e["hn_points"] = hn_points
        return Article(
            title=title, url="https://test/" + title,
            source="TestSource", category="ai_ml",
            published="2026-04-27T12:00:00",
            extra=e,
        )

    def test_zh_reason_uses_description(self):
        a = self._make_article(priority=1)
        reason = _generate_importance_reason(a, language="zh")
        assert "值得关注" in reason

    def test_en_reason_uses_description(self):
        a = self._make_article(priority=1)
        reason = _generate_importance_reason(a, language="en")
        assert "noteworthy" in reason

    def test_zh_cluster_reason(self):
        a = self._make_article()
        cluster_map = {a.url: {"cluster_size": 5, "cross_source": True}}
        reason = _generate_importance_reason(a, cluster_map, "zh")
        assert "5篇相关报道" in reason
        assert "多源验证" in reason

    def test_en_cluster_reason(self):
        a = self._make_article()
        cluster_map = {a.url: {"cluster_size": 5, "cross_source": True}}
        reason = _generate_importance_reason(a, cluster_map, "en")
        assert "5 related reports" in reason
        assert "cross-source" in reason

    def test_en_fallback_worth_reading(self):
        a = self._make_article(priority=0, extra={"news_value_score": 0.1})
        reason = _generate_importance_reason(a, language="en")
        assert "noteworthy" in reason


class TestHighlightHeadingLevels:
    def test_highlights_items_are_h4(self):
        articles = [
            Article(
                title="Big AI news", url="https://test/1",
                source="TestSource", category="ai_ml",
                published="2026-04-27T12:00:00",
                extra={"editorial_tier": "must_read", "news_value_score": 0.9},
            ),
        ]
        category_results = {
            "ai_ml": {
                "name": "AI/ML",
                "articles": articles,
                "tiered": {
                    "must_read": [{"index": 1, "summary": "Big release"}],
                    "noteworthy": [], "brief": [],
                },
                "article_count": 1,
            }
        }
        report = generate_tech_report(
            articles,
            category_results=category_results,
            stats={"total_articles": 1, "categories": 1},
            report_language="zh",
        )
        # Section heading should be ###
        assert "### 🔥 今日重点" in report
        # Individual items should be ####
        assert "#### ⭐ [Big AI news]" in report
        # Items should NOT be same level as section heading (exact line match)
        for line in report.split("\n"):
            if "Big AI news" in line and line.strip().startswith("#"):
                    assert line.strip().startswith("####"), f"Expected h4 but got: {line}"
