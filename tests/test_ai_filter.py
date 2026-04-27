"""Tests for core/ai_filter.py — relevance filter and feed noise filter."""

from core.article import Article
from core.ai_filter import (
    _is_likely_ai_article,
    _hard_ai_relevance_check,
    _is_obvious_non_ai,
    _parse_classification_response,
    _classify_batch,
    apply_feed_noise_filter,
)


def _make_article(title="Test", description="", extra=None):
    return Article(
        title=title,
        url="https://example.com/test",
        source="TestSource",
        category="ai_ml",
        published="2026-04-27T08:00:00",
        description=description,
        extra=extra or {},
    )


class TestIsLikelyAiArticle:
    def test_ai_title_passes(self):
        a = _make_article(title="OpenAI announces GPT-5")
        assert _is_likely_ai_article(a) is True

    def test_ai_description_passes(self):
        a = _make_article(description="A new machine learning model for image recognition")
        assert _is_likely_ai_article(a) is True

    def test_chinese_ai_title_passes(self):
        a = _make_article(title="大模型推理能力新突破")
        assert _is_likely_ai_article(a) is True

    def test_wildlife_article_rejected(self):
        a = _make_article(title="Wildlife arriving at newly created wetland")
        assert _is_likely_ai_article(a) is False

    def test_exercise_post_rejected(self):
        a = _make_article(title="Did anyone here used to hate exercise?")
        assert _is_likely_ai_article(a) is False

    def test_ai_policy_article_passes(self):
        a = _make_article(title="EU proposes new AI regulation framework")
        assert _is_likely_ai_article(a) is True


class TestHardAiRelevanceCheck:
    def test_ai_title_passes(self):
        a = _make_article(title="OpenAI announces GPT-5")
        assert _hard_ai_relevance_check(a) is True

    def test_wildlife_rejected(self):
        a = _make_article(title="Wildlife arriving at newly created wetland")
        assert _hard_ai_relevance_check(a) is False

    def test_exercise_rejected(self):
        a = _make_article(title="Did anyone here used to hate exercise?")
        assert _hard_ai_relevance_check(a) is False

    def test_wildfire_rejected(self):
        a = _make_article(title="Series of wildfires across Scotland")
        assert _hard_ai_relevance_check(a) is False

    def test_orangutan_rejected(self):
        a = _make_article(title="How one orangutan braved new bridge")
        assert _hard_ai_relevance_check(a) is False

    def test_ai_keyword_in_full_text_passes(self):
        a = _make_article(
            title="New research breakthrough",
            description="",
        )
        a.full_text = "This paper introduces a novel transformer architecture for AI."
        assert _hard_ai_relevance_check(a) is True

    def test_no_text_no_ai_rejected(self):
        a = _make_article(title="Random title", description="", extra={})
        a.full_text = ""
        assert _hard_ai_relevance_check(a) is False


class TestApplyFeedNoiseFilter:
    def test_no_filter_flag_passes_all(self):
        articles = [
            _make_article(title="Wildlife at wetland", extra={"_feed_meta": {}}),
            _make_article(title="AI breakthrough", extra={"_feed_meta": {}}),
        ]
        result = apply_feed_noise_filter(articles)
        assert len(result) == 2

    def test_ai_only_filter_removes_noise(self):
        articles = [
            _make_article(
                title="Wildlife arriving at newly created wetland",
                extra={"_feed_meta": {"noise_filter": "ai_only"}},
            ),
            _make_article(
                title="GPT-5 shows improved reasoning",
                extra={"_feed_meta": {"noise_filter": "ai_only"}},
            ),
            _make_article(
                title="Exercise and fitness tips",
                extra={"_feed_meta": {"noise_filter": "ai_only"}},
            ),
        ]
        result = apply_feed_noise_filter(articles)
        # Only the AI-related article passes both noise filter and relevance check
        assert len(result) == 1
        assert result[0].title == "GPT-5 shows improved reasoning"

    def test_ai_content_passes_noise_filter(self):
        articles = [
            _make_article(
                title="AI alignment research progress",
                extra={"_feed_meta": {"noise_filter": "ai_only"}},
            ),
        ]
        result = apply_feed_noise_filter(articles)
        assert len(result) == 1

    def test_underscore_noise_filter_key_works(self):
        """Verify _noise_filter (as set by feedparser path) is recognized."""
        articles = [
            _make_article(
                title="Wildlife arriving at newly created wetland",
                extra={"_feed_meta": {"_noise_filter": "ai_only"}},
            ),
            _make_article(
                title="GPT-5 shows improved reasoning",
                extra={"_feed_meta": {"_noise_filter": "ai_only"}},
            ),
        ]
        result = apply_feed_noise_filter(articles)
        assert len(result) == 1
        assert result[0].title == "GPT-5 shows improved reasoning"


class TestClassifierResponseParsing:
    def test_accepts_numeric_string_keys(self):
        response = '{"1": true, "2": false}'
        parsed = _parse_classification_response(response, 2)
        assert parsed == {1: True, 2: False}

    def test_accepts_id_prefixed_keys(self):
        response = '{"id_1": true, "id_2": false}'
        parsed = _parse_classification_response(response, 2)
        assert parsed == {1: True, 2: False}

    def test_salvages_non_json_lines(self):
        response = "1: true\n2: false\n3: 相关"
        parsed = _parse_classification_response(response, 3)
        assert parsed == {1: True, 2: False, 3: True}


class TestClassifyBatchFallback:
    def test_partial_parse_uses_keyword_fallback_only_for_missing_items(self):
        batch = [
            _make_article(title="OpenAI announces GPT-5"),
            _make_article(title="General software release"),
            _make_article(title="Anthropic launches new AI agent"),
        ]

        class _Client:
            pass

        response = '{"1": true, "2": false}'
        from unittest.mock import patch
        with patch("core.ai_filter.chat_with_profile", return_value=response):
            result = _classify_batch(_Client(), batch, batch_idx=0, total_batches=1)

        titles = [article.title for article in result]
        assert "OpenAI announces GPT-5" in titles
        assert "Anthropic launches new AI agent" in titles
        assert "General software release" not in titles


class TestTechOnlyNoiseFilter:
    def test_political_news_filtered(self):
        articles = [
            _make_article(
                title="Trump fires the entire National Science Board",
                extra={"_feed_meta": {"noise_filter": "tech_only"}},
            ),
            _make_article(
                title="Apple announces new M5 chip with AI acceleration",
                extra={"_feed_meta": {"noise_filter": "tech_only"}},
            ),
        ]
        result = apply_feed_noise_filter(articles)
        assert len(result) == 1
        assert "Apple" in result[0].title

    def test_shooting_crime_filtered(self):
        articles = [
            _make_article(
                title="Shooting at White House dinner injures three",
                extra={"_feed_meta": {"noise_filter": "tech_only"}},
            ),
            _make_article(
                title="OpenAI launches new API for function calling",
                extra={"_feed_meta": {"noise_filter": "tech_only"}},
            ),
        ]
        result = apply_feed_noise_filter(articles)
        assert len(result) == 1
        assert "OpenAI" in result[0].title

    def test_stock_picks_filtered(self):
        articles = [
            _make_article(
                title="Top Wall Street analysts pick these 3 dividend stocks",
                extra={"_feed_meta": {"noise_filter": "tech_only"}},
            ),
            _make_article(
                title="NVIDIA stock rises on strong AI chip demand",
                extra={"_feed_meta": {"noise_filter": "tech_only"}},
            ),
        ]
        result = apply_feed_noise_filter(articles)
        assert len(result) == 1
        assert "NVIDIA" in result[0].title

    def test_celebrity_entertainment_filtered(self):
        articles = [
            _make_article(
                title="Celebrity red carpet fashion at the Oscars",
                extra={"_feed_meta": {"noise_filter": "tech_only"}},
            ),
        ]
        result = apply_feed_noise_filter(articles)
        assert len(result) == 0

    def test_tech_content_passes(self):
        articles = [
            _make_article(
                title="New robotic control software avoids jamming their joints",
                extra={"_feed_meta": {"noise_filter": "tech_only"}},
            ),
            _make_article(
                title="I tried 7 voice typing apps on Windows",
                extra={"_feed_meta": {"noise_filter": "tech_only"}},
            ),
        ]
        result = apply_feed_noise_filter(articles)
        assert len(result) == 2

    def test_no_filter_passes_all(self):
        articles = [
            _make_article(title="Some random article", extra={"_feed_meta": {}}),
        ]
        result = apply_feed_noise_filter(articles)
        assert len(result) == 1


class TestObviousNonAi:
    """Test the negative gate that catches obviously non-AI content."""

    def test_bbc_river_pollution(self):
        a = _make_article(title="We're living in a shed because of river pollution")
        assert _is_obvious_non_ai(a) is True
        assert _is_likely_ai_article(a) is False

    def test_bbc_orangutan_bridge(self):
        a = _make_article(title="Watch: How one orangutan braved new bridge to unite his split community")
        assert _is_obvious_non_ai(a) is True
        assert _is_likely_ai_article(a) is False

    def test_bbc_changing_skies(self):
        a = _make_article(title="Your snaps of changing skies from meteors to rays")
        assert _is_obvious_non_ai(a) is True

    def test_bbc_supercomputer(self):
        a = _make_article(title="A 17th Century 'supercomputer' once owned by Indian royalty heads for auction")
        assert _is_obvious_non_ai(a) is True

    def test_home_buying(self):
        a = _make_article(title="To buy this Bay Area home, you'll need Anthropic equity")
        assert _is_obvious_non_ai(a) is False  # This IS AI-related despite "home"

    def test_agent_not_false_positive(self):
        """Bare 'agent' should NOT match — only specific AI compounds."""
        a = _make_article(title="Real estate agent reports market trends")
        assert _is_likely_ai_article(a) is False

    def test_ai_agent_passes(self):
        """'AI agent' should match."""
        a = _make_article(title="New AI agent framework released by Anthropic")
        assert _is_likely_ai_article(a) is True

    def test_claude_mixed_cjk_latin(self):
        """Claude keyword should match in mixed CJK/Latin text."""
        a = _make_article(title="Claude终于认了！降智坐实")
        assert _is_likely_ai_article(a) is True

    def test_gpt_mixed_cjk_latin(self):
        """GPT keyword should match in mixed CJK/Latin text."""
        a = _make_article(title="OpenAI发布GPT-5.5新模型")
        assert _is_likely_ai_article(a) is True

    def test_pure_sports_rejected(self):
        a = _make_article(title="NFL championship game draws record viewership")
        assert _is_obvious_non_ai(a) is True

    def test_stock_tip_rejected(self):
        a = _make_article(title="Top stock picks for reliable dividend income")
        assert _is_obvious_non_ai(a) is True
