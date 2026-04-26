"""Tests for core/config.py — category normalization and display."""

from core.config import normalize_category, get_category_display


class TestNormalizeCategory:
    def test_identity_known(self):
        assert normalize_category("ai_ml") == "ai_ml"
        assert normalize_category("ai_tools") == "ai_tools"

    def test_legacy_mapping(self):
        assert normalize_category("ai_research") == "ai_ml"
        assert normalize_category("安全") == "wechat_security"
        assert normalize_category("开发") == "wechat_dev"

    def test_skills_mapping(self):
        assert normalize_category("AI/ML") == "ai_ml"
        assert normalize_category("综合科技") == "tech_general"

    def test_unknown_passthrough(self):
        assert normalize_category("unknown_cat") == "unknown_cat"
        assert normalize_category("") == ""


class TestGetCategoryDisplay:
    def test_known_category(self):
        result = get_category_display("ai_ml")
        assert "AI" in result

    def test_known_category_tech(self):
        result = get_category_display("hacker_news")
        assert "Hacker News" in result

    def test_unknown_returns_id(self):
        assert get_category_display("nonexistent_xyz") == "nonexistent_xyz"
