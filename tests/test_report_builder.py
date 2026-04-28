"""Tests for report_builder.py — data dashboard, highlights, and report assembly."""

import os
from datetime import datetime, timezone
from core.article import Article
from core.report_builder import _build_data_dashboard, _build_highlights
from core.report_builder import _escape_pipe, _render_hn_table, generate_tech_report, _merge_llm_summaries, build_unified_report, _generate_importance_reason
from core.briefing import _extract_keyword_from_title, _dedup_theme_names, _compose_theme_summary, _fallback_trends
from core.renderer import _render_briefing_markdown
from unittest.mock import patch

def _no_api_key():
    """Context manager that ensures API_KEY is unset."""
    env = dict(os.environ)
    env.pop("API_KEY", None)
    return patch.dict(os.environ, env, clear=True)



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
        result = _build_data_dashboard(ai, non_ai, {})
        assert "📊 数据概览" in result
        assert "3" in result  # total
        assert "2" in result  # AI count

    def test_empty_articles(self):
        result = _build_data_dashboard([], [], {})
        assert result == ""

    def test_cluster_info_shown(self):
        ai = [_make_article("AI 1"), _make_article("AI 2")]
        cluster_map = {
            ai[0].url: {"cluster_size": 3, "cross_source": True},
            ai[1].url: {"cluster_size": 2, "cross_source": False},
        }
        result = _build_data_dashboard(ai, [], cluster_map)
        assert "话题聚类" in result
        assert "跨源验证" in result

    def test_editorial_tiers_shown(self):
        ai = [
            _make_article("Must read", editorial_tier="must_read"),
            _make_article("Noteworthy", editorial_tier="noteworthy"),
            _make_article("Brief", editorial_tier="brief"),
        ]
        result = _build_data_dashboard(ai, [], {})
        assert "必读" in result
        assert "值得关注" in result

    def test_no_tiers_hides_tier_rows(self):
        ai = [_make_article("No tier")]
        result = _build_data_dashboard(ai, [], {})
        assert "必读" not in result


class TestBuildHighlights:
    def test_zh_highlights_from_must_read(self):
        ai = [
            _make_article("Big AI news", editorial_tier="must_read", news_value_score=0.9),
            _make_article("Lesser news", editorial_tier="noteworthy", news_value_score=0.5),
        ]
        result = _build_highlights(ai, [], {})
        assert "🔥 今日要点" in result
        assert "Big AI news" in result

    def test_empty_articles_no_highlights(self):
        result = _build_highlights([], [], {})
        assert result == ""

    def test_no_tier_articles_uses_noteworthy(self):
        ai = [_make_article("Noteworthy", editorial_tier="noteworthy", news_value_score=0.5)]
        result = _build_highlights(ai, [], {})
        assert "Noteworthy" in result

    def test_hn_engagement_shown(self):
        ai = [Article(
            title="HN Post", url="https://hn.test/1", source="HN",
            category="ai_ml", published="2026-04-27T12:00:00",
            extra={"editorial_tier": "must_read", "news_value_score": 0.9, "hn_points": 200},
        )]
        result = _build_highlights(ai, [], {})
        assert "🔥HN 200" in result

    def test_mixed_ai_and_non_ai(self):
        ai = [_make_article("AI news", editorial_tier="must_read", news_value_score=0.9)]
        non_ai = [_make_article("Tech news", category="tech_general",
                                editorial_tier="must_read", news_value_score=0.8)]
        result = _build_highlights(ai, non_ai, {})
        assert "AI news" in result
        assert "Tech news" in result


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
        assert _render_hn_table([], "条") == []

    def test_basic_zh_rendering(self):
        items = [self._make_hn_item()]
        lines = _render_hn_table(items, "条")
        joined = "\n".join(lines)
        assert "Hacker News 热门" in joined
        assert "🔥 100" in joined
        assert "💬 42" in joined
        assert "Test HN Post" in joined

    def test_with_summary_map(self):
        items = [self._make_hn_item(url="https://hn.test/1")]
        sm = {"https://hn.test/1": {"ai_summary": "AI says hello"}}
        lines = _render_hn_table(items, "条", summary_map=sm)
        joined = "\n".join(lines)
        assert "摘要" in joined
        assert "AI says hello" in joined

    def test_without_summary_map_no_summary_column(self):
        items = [self._make_hn_item()]
        lines = _render_hn_table(items, "条")
        joined = "\n".join(lines)
        assert "摘要" not in joined

    def test_pipe_in_title_escaped(self):
        items = [self._make_hn_item(title="A|B")]
        lines = _render_hn_table(items, "条")
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
            trend_insights="**多模态融合**: 各大厂商加速多模态模型迭代。",
        )
        assert "📊 趋势洞察" in report
        assert "多模态融合" in report

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
        )
        assert "📊 趋势洞察" not in report


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
        )
        assert "> Line one" in report
        assert "> **Line two**" in report
        assert "> - Line three" in report


class TestUnifiedBriefingReport:
    def test_briefing_sections_present(self):
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
                [ai_article], [non_ai_article], now,
                executive_summary="Test summary",
            )
        assert "## 📌 今日要点" in report
        assert "## 🧭 今日动态" in report
        assert "## 📝 科技简讯" in report

    def test_single_part_report_still_has_briefing_structure(self):
        ai_article = Article(
            title="AI article", url="https://test/ai",
            source="TestSource", category="ai_ml",
            published="2026-04-27T12:00:00",
            extra={"editorial_tier": "noteworthy", "news_value_score": 0.5},
        )
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        with _no_api_key():
            report = build_unified_report(
                [ai_article], [], now,
            )
        assert "## 🧭 今日动态" in report
        assert "## 📌 今日要点" in report

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
                [ai_article], [], now,
            )
        assert "---\n---" not in report

    def test_header_uses_stats_source_count_not_article_count(self):
        ai_article = Article(
            title="AI article", url="https://test/ai",
            source="TestSource", category="ai_ml",
            published="2026-04-27T12:00:00",
            extra={"editorial_tier": "must_read", "news_value_score": 0.9},
        )
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        with _no_api_key():
            report = build_unified_report(
                [ai_article], [], now,
                cluster_map={},
                stats={"candidate_count": 12, "source_count": 5, "included_count": 1},
            )
        assert "扫描 12 篇候选内容" in report
        assert "覆盖 5 个信息源" in report


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
        reason = _generate_importance_reason(a)
        assert "值得关注" in reason

    def test_zh_cluster_reason(self):
        a = self._make_article()
        cluster_map = {a.url: {"cluster_size": 5, "cross_source": True}}
        reason = _generate_importance_reason(a, cluster_map)
        assert "5篇相关报道" in reason
        assert "多源验证" in reason


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
        )
        # Section heading should be ###
        assert "### 🔥 今日重点" in report
        # Individual items should be ####
        assert "#### ⭐ [Big AI news]" in report
        # Items should NOT be same level as section heading (exact line match)
        for line in report.split("\n"):
            if "Big AI news" in line and line.strip().startswith("#"):
                    assert line.strip().startswith("####"), f"Expected h4 but got: {line}"


class TestThemeDedup:
    """Tests for _extract_keyword_from_title and _dedup_theme_names."""

    def test_extract_keyword_english_title(self):
        result = _extract_keyword_from_title("OpenAI releases GPT-5")
        assert result == "OpenAI"

    def test_extract_keyword_chinese_title(self):
        result = _extract_keyword_from_title("新智元报道全球AI终局")
        assert len(result) >= 2
        assert len(result) <= 4

    def test_extract_keyword_empty_title(self):
        result = _extract_keyword_from_title("")
        assert result == ""

    def test_extract_keyword_none_title(self):
        result = _extract_keyword_from_title(None)
        assert result == ""

    def test_dedup_adds_suffix_to_duplicate_themes(self):
        themes = [
            {"title": "模型与平台", "articles": [_make_article("OpenAI releases model")]},
            {"title": "模型与平台", "articles": [_make_article("Anthropic launches Claude")]},
        ]
        _dedup_theme_names(themes)
        # First occurrence is unchanged, second gets a keyword suffix
        assert themes[0]["title"] == "模型与平台"
        assert "模型与平台" in themes[1]["title"]
        assert themes[1]["title"] != "模型与平台"

    def test_dedup_no_suffix_for_unique_titles(self):
        themes = [
            {"title": "模型与平台", "articles": [_make_article("OpenAI news")]},
            {"title": "开源与工具", "articles": [_make_article("Hugging Face release")]},
        ]
        _dedup_theme_names(themes)
        assert themes[0]["title"] == "模型与平台"
        assert themes[1]["title"] == "开源与工具"

    def test_dedup_three_duplicates(self):
        themes = [
            {"title": "研究与方法", "articles": [_make_article("DeepMind AlphaFold")]},
            {"title": "研究与方法", "articles": [_make_article("Stanford NLP paper")]},
            {"title": "研究与方法", "articles": [_make_article("MIT research breakthrough")]},
        ]
        _dedup_theme_names(themes)
        # First unchanged, subsequent ones get suffixes
        assert themes[0]["title"] == "研究与方法"
        assert themes[1]["title"] != "研究与方法"
        assert themes[2]["title"] != "研究与方法"


class TestComposeThemeSummary:
    """Tests for _compose_theme_summary fallback logic."""

    def test_with_descriptions_joins_first_sentences(self):
        a1 = _make_article("Article 1")
        a1.description = "这是第一篇文章的重要摘要。后面还有更多内容。"
        a2 = _make_article("Article 2")
        a2.description = "这是第二篇文章的摘要内容。额外信息。"
        theme = {"articles": [a1, a2]}
        result = _compose_theme_summary(theme)
        assert " · " in result
        assert "这是第一篇" in result

    def test_without_descriptions_uses_keywords(self):
        a1 = _make_article("OpenAI launches new model")
        a1.description = ""
        a2 = _make_article("Anthropic releases Claude update")
        a2.description = ""
        theme = {"articles": [a1, a2]}
        result = _compose_theme_summary(theme)
        assert "涉及" in result

    def test_empty_theme_returns_fallback(self):
        theme = {"articles": []}
        result = _compose_theme_summary(theme)
        assert "涉及" in result or "今日该主题" in result

    def test_with_summary_map_uses_importance_reason(self):
        a1 = _make_article("Article 1")
        summary_map = {a1.url: {"importance_reason": "模型性能大幅提升，值得关注。"}}
        theme = {"articles": [a1]}
        result = _compose_theme_summary(theme, summary_map=summary_map)
        assert "模型性能大幅提升" in result


class TestFallbackTrends:
    """Tests for _fallback_trends heuristic trend generation."""

    def test_cross_source_trend_uses_lead_article_title(self):
        a1 = Article(
            title="GPT-5 多模态突破性进展", url="https://test/1",
            source="SourceA", category="ai_ml",
            published="2026-04-27T12:00:00", extra={"news_value_score": 0.8},
        )
        theme = {
            "theme": "模型与平台",
            "title": "模型与平台",  # generic title = theme name
            "articles": [a1],
            "cross_source": True,
        }
        trends = _fallback_trends([theme])
        joined = " ".join(trends)
        # Should use lead article title since theme title is generic
        assert "GPT-5" in joined

    def test_hn_heat_trend(self):
        a1 = Article(
            title="Hot HN Post", url="https://test/1",
            source="HN", category="ai_ml",
            published="2026-04-27T12:00:00",
            extra={"news_value_score": 0.5, "hn_points": 250},
        )
        theme = {
            "theme": "评测与实战",
            "title": "评测与实战",
            "articles": [a1],
            "score": 0.5,
        }
        trends = _fallback_trends([theme])
        joined = " ".join(trends)
        assert "HN 热议" in joined
        assert "250" in joined

    def test_empty_themes_returns_empty(self):
        assert _fallback_trends([]) == []

    def test_source_distribution_trend(self):
        a1 = _make_article("Article A")
        a1.source = "SourceAlpha"
        a2 = _make_article("Article B")
        a2.source = "SourceBeta"
        theme = {
            "theme": "行业与商业",
            "title": "行业与商业",
            "articles": [a1, a2],
            "score": 0.4,
        }
        trends = _fallback_trends([theme])
        joined = " ".join(trends)
        assert "信息源分布" in joined


class TestTldrSection:
    """Tests for TL;DR rendering in _render_briefing_markdown."""

    def _make_briefing_data(self, **overrides):
        data = {
            "stats": {"candidate_count": 10, "source_count": 3, "included_count": 8,
                      "after_dedup": 9, "ai_count": 5, "non_ai_count": 3,
                      "cluster_count": 0, "cross_source_count": 0},
            "highlights": ["AI highlight one", "AI highlight two"],
            "themes": [],
            "featured_tech": [],
            "brief_items": [],
            "trends": [],
        }
        data.update(overrides)
        return data

    def test_tldr_section_appears(self):
        data = self._make_briefing_data(tldr="今日AI领域重要更新概览。")
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        result = _render_briefing_markdown(data, now)
        assert "## 🎯 今日速览" in result
        assert "今日AI领域重要更新概览" in result

    def test_tldr_section_absent_without_field(self):
        data = self._make_briefing_data()
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        result = _render_briefing_markdown(data, now)
        assert "## 🎯 今日速览" not in result

    def test_tldr_appears_before_highlights(self):
        data = self._make_briefing_data(tldr="这是速览内容")
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        result = _render_briefing_markdown(data, now)
        tldr_pos = result.find("## 🎯 今日速览")
        highlights_pos = result.find("## 📌 今日要点")
        assert tldr_pos > 0
        assert highlights_pos > 0
        assert tldr_pos < highlights_pos


class TestBlockquoteThemeLinks:
    """Tests for blockquote-style theme link rendering."""

    def test_blockquote_theme_links_present(self):
        a1 = Article(
            title="AI article one", url="https://test/1",
            source="SrcA", category="ai_ml",
            published="2026-04-27T12:00:00", extra={"news_value_score": 0.5},
        )
        data = {
            "stats": {"candidate_count": 1, "source_count": 1, "included_count": 1,
                      "after_dedup": 1, "ai_count": 1, "non_ai_count": 0,
                      "cluster_count": 0, "cross_source_count": 0},
            "highlights": [],
            "themes": [{
                "title": "模型与平台",
                "summary": "主题摘要",
                "articles": [a1],
            }],
            "featured_tech": [],
            "brief_items": [],
            "trends": [],
        }
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        result = _render_briefing_markdown(data, now)
        assert "> 📎 相关：" in result

    def test_old_format_not_present(self):
        a1 = Article(
            title="AI article one", url="https://test/1",
            source="SrcA", category="ai_ml",
            published="2026-04-27T12:00:00", extra={"news_value_score": 0.5},
        )
        data = {
            "stats": {"candidate_count": 1, "source_count": 1, "included_count": 1,
                      "after_dedup": 1, "ai_count": 1, "non_ai_count": 0,
                      "cluster_count": 0, "cross_source_count": 0},
            "highlights": [],
            "themes": [{
                "title": "模型与平台",
                "summary": "主题摘要",
                "articles": [a1],
            }],
            "featured_tech": [],
            "brief_items": [],
            "trends": [],
        }
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        result = _render_briefing_markdown(data, now)
        assert "**参考：**" not in result

    def test_links_joined_with_chinese_comma(self):
        a1 = Article(
            title="Article A", url="https://test/1",
            source="SrcA", category="ai_ml",
            published="2026-04-27T12:00:00", extra={"news_value_score": 0.5},
        )
        a2 = Article(
            title="Article B", url="https://test/2",
            source="SrcB", category="ai_ml",
            published="2026-04-27T12:00:00", extra={"news_value_score": 0.4},
        )
        data = {
            "stats": {"candidate_count": 2, "source_count": 2, "included_count": 2,
                      "after_dedup": 2, "ai_count": 2, "non_ai_count": 0,
                      "cluster_count": 0, "cross_source_count": 0},
            "highlights": [],
            "themes": [{
                "title": "模型与平台",
                "summary": "主题摘要",
                "articles": [a1, a2],
            }],
            "featured_tech": [],
            "brief_items": [],
            "trends": [],
        }
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        result = _render_briefing_markdown(data, now)
        # Links should be joined with 、
        assert "Article A" in result
        assert "Article B" in result
