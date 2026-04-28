"""Integration tests for the unified report pipeline with mocked LLM.

These tests exercise the LLM routing path (API_KEY set) which the regular
report tests don't cover — they run without API_KEY and only test the
template renderer.
"""

import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from core.article import Article
from core import llm
from core.report_builder import build_unified_report


def _make_article(title, category="ai_ml", tier=None, score=0.5, hn_points=0):
    extra = {"news_value_score": score}
    if tier:
        extra["editorial_tier"] = tier
    if hn_points:
        extra["hn_points"] = hn_points
    return Article(
        title=title,
        url=f"https://test/{title}",
        source="TestSource",
        category=category,
        published="2026-04-27T12:00:00",
        description=f"Description of {title}",
        extra=extra,
    )


def _mock_llm_env():
    """Context manager that sets API_KEY and mocks LLM client."""
    # Return a tuple for nested with statements
    return patch.dict(os.environ, {"API_KEY": "test-key"})


class TestLLMRoutingWithAPIKey:
    """Tests that verify the LLM path is taken when API_KEY is set."""

    def test_tiered_articles_use_llm_path_when_api_key_set(self):
        """When API_KEY is available, the unified briefing path still renders correctly."""
        ai_article = _make_article("GPT-5.5 released", tier="must_read", score=0.9)
        non_ai_article = _make_article("Linux 6.10 released", category="tech_general")
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        with _mock_llm_env():
            with patch("core.llm_services.get_llm_client", return_value=MagicMock()), \
                 patch("core.llm_services.render_briefing", return_value={
                     "highlights": ["OpenAI folds Codex into GPT-5.5"],
                     "theme_summaries": {"theme-模型与平台": "GPT-5.5 is here."},
                     "trends": ["Model vendors are converging around coding workflows."],
                 }):
                report = build_unified_report(
                    [ai_article], [non_ai_article], now,
                    cluster_map={},
                )

        assert "GPT-5.5" in report
        assert "## 📌 今日要点" in report
        assert "## 🧭 今日动态" in report
        assert "## 📝 科技简讯" in report

    def test_highlights_section_present_in_two_part_report(self):
        """Highlights section renders and falls back when zh mode receives English LLM bullets."""
        ai = _make_article("Big AI news", tier="must_read", score=0.9, hn_points=200)
        non_ai = _make_article("Tech news", category="tech_general")
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        with _mock_llm_env():
            with patch("core.llm_services.get_llm_client", return_value=MagicMock()), \
                 patch("core.llm_services.render_briefing", return_value={
                     "highlights": ["Big AI news moves into production"],
                     "theme_summaries": {"theme-模型与平台": "Theme summary"},
                 }):
                report = build_unified_report(
                    [ai], [non_ai], now,
                    cluster_map={},
                )

        assert "## 📌 今日要点" in report
        assert "Big AI news moves into production" not in report
        assert "Big AI news" in report
        assert "## 🧭 今日动态" in report

    def test_no_highlights_when_no_tiered_articles(self):
        """Fallback highlights still render even when tiers are missing."""
        ai = _make_article("No tier article")
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        with _mock_llm_env():
            with patch("core.llm_services.get_llm_client", return_value=MagicMock()), \
                 patch("core.llm_services.render_briefing", return_value={}):
                report = build_unified_report(
                    [ai], [], now,
                    cluster_map={},
                )

        assert "## 📌 今日要点" in report

    def test_engagement_data_flows_to_llm(self):
        """HN engagement data flows into the batched briefing prompts."""
        llm.reset_llm_runtime_state()
        ai = _make_article("Hot post", tier="must_read", score=0.9, hn_points=500)
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        captured_themes = []

        def capture_briefing(briefing_data):
            themes = briefing_data.get("themes", [])
            for t in themes:
                for article in t.get("articles", []):
                    captured_themes.append(article)
            return {
                "highlights": ["Hot post carries heavy HN engagement"],
                "theme_summaries": {t.get("id"): "Summary" for t in themes[:1]} or {"1": "Summary"},
                "trends": ["trend"],
            }

        with _mock_llm_env():
            with patch("core.report_builder._render_briefing_v2", side_effect=capture_briefing):
                report = build_unified_report([ai], [], now, cluster_map={})

        assert captured_themes, "No articles reached the briefing"
        assert any(a.extra.get("hn_points") == 500 for a in captured_themes), \
            "HN engagement data did not flow to briefing"

    def test_report_falls_back_when_optional_llm_is_degraded(self):
        """When optional LLM embellishment is degraded, the fallback briefing still renders."""
        ai = _make_article("Fallback AI news", tier="must_read", score=0.9)
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        llm.reset_llm_runtime_state()
        llm._runtime_state.degraded_mode = True
        try:
            with _mock_llm_env():
                report = build_unified_report([ai], [], now, cluster_map={})
        finally:
            llm.reset_llm_runtime_state()

        assert "## 📌 今日要点" in report
        assert "Fallback AI news" in report
        assert "## 🧭 今日动态" in report


class TestSkillModeWithoutAPIKey:
    """Tests that verify the template renderer path when no API_KEY."""

    def test_tiered_articles_use_template_without_api_key(self):
        """When no API_KEY, the template briefing renderer is used."""
        ai = _make_article("Template article", tier="must_read", score=0.9)
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        env = dict(os.environ)
        env.pop("API_KEY", None)

        with patch.dict(os.environ, env, clear=True):
            report = build_unified_report(
                [ai], [], now,
                cluster_map={},
            )

        assert "## 🧭 今日动态" in report
        assert "Template article" in report


class TestFinalizePath:
    """Tests for the --finalize / try_build_unified_report path."""

    def test_finalizes_tech_source_from_workspace(self):
        """try_build_unified_report loads workspace data and produces a report."""
        from core.pipeline import try_build_unified_report
        from core.workspace import save_workspace_updates

        ai = _make_article("AI breakthrough", tier="must_read", score=0.9)
        non_ai = _make_article("Cloud update", category="cloud", tier="noteworthy", score=0.5)
        metadata = {
            "run_id": "test",
            "generated_at": "2026-04-27T12:00:00Z",
            "source_count": 1,
            "candidate_count": 2,
            "after_dedup": 2,
            "after_editorial": 2,
            "included_count": 2,
        }
        save_workspace_updates("tech", [ai, non_ai], metadata)

        try:
            with _mock_llm_env():
                with patch("core.report_builder._render_briefing_v2", return_value={
                    "highlights": ["AI breakthrough reported"],
                    "theme_summaries": {},
                }):
                    now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
                    report = try_build_unified_report("tech", now)

            assert report is not None
            assert "AI breakthrough" in report
            assert "## 📌 今日要点" in report
        finally:
            # Cleanup workspace file
            from core.config import WORKSPACE_DIR
            path = WORKSPACE_DIR / "tech_updates.json"
            if path.exists():
                path.unlink()

    def test_finalize_returns_none_when_no_workspace(self):
        """try_build_unified_report returns None when no workspace data exists."""
        from core.pipeline import try_build_unified_report
        from core.config import WORKSPACE_DIR

        # Ensure no workspace file
        for src in ("tech", "podcast", "wechat"):
            path = WORKSPACE_DIR / f"{src}_updates.json"
            if path.exists():
                path.unlink()

        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        result = try_build_unified_report("podcast", now)
        assert result is None

    def test_finalizes_with_mixed_sources(self):
        """try_build_unified_report merges tech + podcast workspace data."""
        from core.pipeline import try_build_unified_report
        from core.workspace import save_workspace_updates

        tech_article = _make_article("LLM release", tier="must_read", score=0.85)
        podcast_article = _make_article("AI podcast ep", category="podcast", tier="noteworthy", score=0.6)

        tech_meta = {"run_id": "test", "generated_at": "2026-04-27T12:00:00Z",
                     "source_count": 1, "candidate_count": 1, "after_dedup": 1,
                     "after_editorial": 1, "included_count": 1}
        podcast_meta = dict(tech_meta)

        save_workspace_updates("tech", [tech_article], tech_meta)
        save_workspace_updates("podcast", [podcast_article], podcast_meta)

        try:
            with _mock_llm_env():
                with patch("core.report_builder._render_briefing_v2", return_value={
                    "highlights": ["LLM released"],
                    "theme_summaries": {},
                }):
                    now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
                    report = try_build_unified_report("all", now)

            assert report is not None
            assert "LLM release" in report
        finally:
            from core.config import WORKSPACE_DIR
            for src in ("tech", "podcast", "wechat"):
                path = WORKSPACE_DIR / f"{src}_updates.json"
                if path.exists():
                    path.unlink()


class TestRenderBriefingV2:
    """Tests that build_unified_report correctly applies v2 fields from render_briefing_v2."""

    def test_tldr_section_appears_from_v2(self):
        """When render_briefing_v2 returns tldr, the report contains the TL;DR section."""
        ai = _make_article("GPT-5.5 released", tier="must_read", score=0.9)
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        v2_response = {
            "tldr": "今日AI领域重点关注：OpenAI发布GPT-5.5，性能大幅提升。",
            "highlights": ["GPT-5.5 released with major improvements"],
            "theme_summaries": {},
        }

        with _mock_llm_env():
            with patch("core.report_builder._render_briefing_v2", return_value=v2_response):
                report = build_unified_report([ai], [], now, cluster_map={})

        assert "## 🎯 今日速览" in report
        assert "今日AI领域重点关注" in report

    def test_theme_titles_applied_from_v2(self):
        """When render_briefing_v2 returns theme_titles, they override the rendered theme names."""
        ai = _make_article("Claude 5 released", tier="must_read", score=0.9)
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        v2_response = {
            "tldr": "速览内容",
            "theme_titles": {"1": "大模型竞赛白热化"},
        }

        with _mock_llm_env():
            with patch("core.report_builder._render_briefing_v2", return_value=v2_response):
                report = build_unified_report([ai], [], now, cluster_map={})

        assert "大模型竞赛白热化" in report

    def test_v2_highlights_override_template_highlights(self):
        """When render_briefing_v2 returns highlights, they replace the template-generated ones."""
        ai = _make_article("AI article", tier="must_read", score=0.9)
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        v2_response = {
            "highlights": ["LLM领域出现重大突破，多家厂商发布新模型"],
        }

        with _mock_llm_env():
            with patch("core.report_builder._render_briefing_v2", return_value=v2_response):
                report = build_unified_report([ai], [], now, cluster_map={})

        assert "LLM领域出现重大突破" in report
