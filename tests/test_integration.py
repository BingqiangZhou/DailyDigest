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
            with patch("core.narrative_renderer.get_llm_client", return_value=MagicMock()), \
                 patch("core.narrative_renderer.NarrativeRenderer.render_briefing", return_value={
                     "highlights": ["OpenAI folds Codex into GPT-5.5"],
                     "theme_summaries": {"theme-模型与平台": "GPT-5.5 is here."},
                     "trends": ["Model vendors are converging around coding workflows."],
                 }):
                report = build_unified_report(
                    [ai_article], [non_ai_article], now, "zh",
                    cluster_map={},
                )

        assert "GPT-5.5" in report
        assert "## 📌 今日亮点" in report
        assert "## 🧭 新内容" in report
        assert "## 📝 科技简讯" in report

    def test_highlights_section_present_in_two_part_report(self):
        """Highlights and briefing sections appear in the unified report."""
        ai = _make_article("Big AI news", tier="must_read", score=0.9, hn_points=200)
        non_ai = _make_article("Tech news", category="tech_general")
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        with _mock_llm_env():
            with patch("core.narrative_renderer.get_llm_client", return_value=MagicMock()), \
                 patch("core.narrative_renderer.NarrativeRenderer.render_briefing", return_value={
                     "highlights": ["Big AI news moves into production"],
                     "theme_summaries": {"theme-模型与平台": "Theme summary"},
                 }):
                report = build_unified_report(
                    [ai], [non_ai], now, "zh",
                    cluster_map={},
                )

        assert "## 📌 今日亮点" in report
        assert "Big AI news moves into production" in report
        assert "## 🧭 新内容" in report

    def test_no_highlights_when_no_tiered_articles(self):
        """Fallback highlights still render even when tiers are missing."""
        ai = _make_article("No tier article")
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        with _mock_llm_env():
            with patch("core.narrative_renderer.get_llm_client", return_value=MagicMock()), \
                 patch("core.narrative_renderer.NarrativeRenderer.render_briefing", return_value={}):
                report = build_unified_report(
                    [ai], [], now, "zh",
                    cluster_map={},
                )

        assert "## 📌 今日亮点" in report

    def test_engagement_data_flows_to_llm(self):
        """HN engagement data flows into the batched briefing prompts."""
        ai = _make_article("Hot post", tier="must_read", score=0.9, hn_points=500)
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        captured_prompt = []

        def capture_prompt(_client, prompt, profile_name, max_retries=2, optional=False):
            captured_prompt.append(prompt)
            if profile_name == "trends":
                return '["trend"]'
            if "JSON 数组" in prompt or "JSON array" in prompt:
                return '[{"index": 1, "summary": "Hot post summary"}]'
            return "- Hot post carries heavy HN engagement"

        with _mock_llm_env():
            with patch("core.narrative_renderer.get_llm_client", return_value=MagicMock()), \
                 patch("core.narrative_renderer.chat_with_profile", side_effect=capture_prompt):
                build_unified_report([ai], [], now, "zh", cluster_map={})

        assert captured_prompt, "LLM was never called"
        assert any("500" in prompt for prompt in captured_prompt)

    def test_english_report_routing(self):
        """English report renders the briefing skeleton in English."""
        ai = _make_article("English AI news", tier="must_read", score=0.9)
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        with _mock_llm_env():
            with patch("core.narrative_renderer.get_llm_client", return_value=MagicMock()), \
                 patch("core.narrative_renderer.NarrativeRenderer.render_briefing", return_value={
                     "highlights": ["English AI news ships today"],
                 }):
                report = build_unified_report(
                    [ai], [], now, "en",
                    cluster_map={},
                )

        assert "## 📌 Highlights" in report
        assert "## 🧭 New Developments" in report

    def test_report_falls_back_when_optional_llm_is_degraded(self):
        """When optional LLM embellishment is degraded, the fallback briefing still renders."""
        ai = _make_article("Fallback AI news", tier="must_read", score=0.9)
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        llm.reset_llm_runtime_state()
        llm._runtime_state.degraded_mode = True
        try:
            with _mock_llm_env():
                report = build_unified_report([ai], [], now, "zh", cluster_map={})
        finally:
            llm.reset_llm_runtime_state()

        assert "## 📌 今日亮点" in report
        assert "Fallback AI news" in report
        assert "## 🧭 新内容" in report


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
                [ai], [], now, "zh",
                cluster_map={},
            )

        assert "## 🧭 新内容" in report
        assert "Template article" in report
