"""Integration tests for the unified report pipeline with mocked LLM.

These tests exercise the LLM routing path (API_KEY set) which the regular
report tests don't cover — they run without API_KEY and only test the
template renderer.
"""

import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from core.article import Article
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
        """When API_KEY is available and articles have tiers, use LLM deep analysis."""
        ai_article = _make_article("GPT-5.5 released", tier="must_read", score=0.9)
        non_ai_article = _make_article("Linux 6.10 released", category="tech_general")
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        mock_response = "### 🔥 今日亮点\n\n**1. GPT-5.5 released**\nAnalysis here.\n\n### 📰 详细报道\n\n#### 基础模型与研究\n\n**1. GPT-5.5**\nGPT-5.5 is here.\n- [GPT-5.5 released](https://test/GPT-5.5 released) — TestSource\n"

        with _mock_llm_env():
            with patch("core.llm.get_llm_client") as mock_get_client, \
                 patch("core.llm.generate_with_critique", return_value=mock_response):
                mock_get_client.return_value = MagicMock()
                report = build_unified_report(
                    [ai_article], [non_ai_article], now, "zh",
                    cluster_map={},
                )

        assert "GPT-5.5" in report
        assert "Part I: 🤖 AI 深度日报" in report
        assert "Part II: 💻 科技动态" in report

    def test_highlights_section_present_in_two_part_report(self):
        """Highlights section appears between dashboard and TOC."""
        ai = _make_article("Big AI news", tier="must_read", score=0.9, hn_points=200)
        non_ai = _make_article("Tech news", category="tech_general")
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        with _mock_llm_env():
            with patch("core.llm.get_llm_client") as mock_get_client, \
                 patch("core.llm.generate_with_critique", return_value="AI analysis output"):
                mock_get_client.return_value = MagicMock()
                report = build_unified_report(
                    [ai], [non_ai], now, "zh",
                    cluster_map={},
                )

        assert "🔥 今日亮点" in report
        assert "Big AI news" in report
        assert "🔥HN 200" in report
        assert "📑 快速导航" in report

    def test_no_highlights_when_no_tiered_articles(self):
        """No highlights section when articles lack editorial tiers."""
        ai = _make_article("No tier article")
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        with _mock_llm_env():
            with patch("core.llm.get_llm_client") as mock_get_client, \
                 patch("core.llm.generate_with_critique", return_value="Analysis"):
                mock_get_client.return_value = MagicMock()
                report = build_unified_report(
                    [ai], [], now, "zh",
                    cluster_map={},
                )

        assert "🔥 今日亮点" not in report

    def test_engagement_data_flows_to_llm(self):
        """HN engagement data is included in the formatted articles sent to LLM."""
        ai = _make_article("Hot post", tier="must_read", score=0.9, hn_points=500)
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        captured_prompt = []

        def capture_prompt(*args, **kwargs):
            # args: client, prompt, profile_name, critique_template
            # kwargs: language
            if len(args) >= 2:
                captured_prompt.append(args[1])
            return "Mock LLM response"

        with _mock_llm_env():
            with patch("core.llm.get_llm_client") as mock_get_client, \
                 patch("core.llm.generate_with_critique", side_effect=capture_prompt):
                mock_get_client.return_value = MagicMock()
                build_unified_report([ai], [], now, "zh", cluster_map={})

        assert captured_prompt, "LLM was never called"
        prompt = captured_prompt[0]
        assert "500" in prompt

    def test_english_report_routing(self):
        """English report uses English LLM path."""
        ai = _make_article("English AI news", tier="must_read", score=0.9)
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        with _mock_llm_env():
            with patch("core.llm.get_llm_client") as mock_get_client, \
                 patch("core.llm.generate_with_critique", return_value="English analysis"):
                mock_get_client.return_value = MagicMock()
                report = build_unified_report(
                    [ai], [], now, "en",
                    cluster_map={},
                )

        assert "Part I: 🤖 AI Deep Digest" in report


class TestSkillModeWithoutAPIKey:
    """Tests that verify the template renderer path when no API_KEY."""

    def test_tiered_articles_use_template_without_api_key(self):
        """When no API_KEY, tiered articles use template renderer."""
        ai = _make_article("Template article", tier="must_read", score=0.9)
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)

        env = dict(os.environ)
        env.pop("API_KEY", None)

        with patch.dict(os.environ, env, clear=True):
            report = build_unified_report(
                [ai], [], now, "zh",
                cluster_map={},
            )

        assert "Part I: 🤖 AI 深度日报" in report
        assert "Template article" in report
