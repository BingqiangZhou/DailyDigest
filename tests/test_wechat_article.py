"""Tests for core/wechat_article.py — rendering functions."""

import pytest
from datetime import datetime, timezone
from core.article import Article
from core.wechat_article import (
    generate_wechat_article,
    _render_header,
    _render_highlights,
    _render_ai_highlights,
    _render_themes,
    _render_ai_themes,
    _render_tech_brief,
)


class TestRenderHeader:
    def test_zh_header(self, now_utc):
        result = _render_header("2026-04-06", "05:30", 10, 2, 12, "zh")
        assert "DailyDigest 人工智能技术日报" in result
        assert "2026-04-06" in result
        assert "共 12 篇" in result

    def test_en_header(self, now_utc):
        result = _render_header("2026-04-06", "05:30", 10, 2, 12, "en")
        assert "DailyDigest AI Technology Daily" in result
        assert "12 articles" in result


class TestRenderHighlights:
    def test_renders_article_titles(self, sample_article, sample_article_zh):
        highlights = [
            {"article": sample_article, "tier": "must_read", "summary": "", "category": "ai_ml", "cat_name": "AI"},
            {"article": sample_article_zh, "tier": "must_read", "summary": "", "category": "ai_ml", "cat_name": "AI"},
        ]
        result = _render_highlights(highlights, "zh")
        assert "今日要点" in result
        assert "- Test Article" in result
        assert "- 测试文章" in result

    def test_empty_highlights(self):
        # The caller checks for empty, but let's verify it handles empty gracefully
        result = _render_highlights([], "zh")
        assert "今日要点" in result


class TestRenderAiHighlights:
    def test_renders_string_items(self):
        highlights = ["AI自主攻破安全系统", "研究揭示模型间知识传递效率"]
        result = _render_ai_highlights(highlights, "zh")
        assert "今日要点" in result
        assert "- AI自主攻破安全系统" in result
        assert "- 研究揭示模型间知识传递效率" in result


class TestRenderThemes:
    def test_renders_themes_with_refs(self, sample_article):
        themes = [{
            "title": "AI安全",
            "summary": "AI安全面临新挑战",
            "refs": [sample_article],
        }]
        result = _render_themes(themes, "zh")
        assert "### 1. AI安全" in result
        assert "AI安全面临新挑战" in result
        assert "参考" in result
        assert "[Test Article](https://example.com/1)" in result


class TestRenderAiThemes:
    def test_renders_ai_themes(self, sample_article):
        themes = [{
            "title": "AI安全",
            "summary": "AI自主攻破高安全系统意味着威胁升级",
            "articles": [sample_article],
        }]
        result = _render_ai_themes(themes, "zh")
        assert "### 1. AI安全" in result
        assert "AI自主攻破高安全系统意味着威胁升级" in result
        assert "参考" in result
        assert "[Test Article](https://example.com/1)" in result

    def test_multiple_themes_with_separator(self, sample_article, sample_article_zh):
        themes = [
            {"title": "AI安全", "summary": "摘要1", "articles": [sample_article]},
            {"title": "AI应用", "summary": "摘要2", "articles": [sample_article_zh]},
        ]
        result = _render_ai_themes(themes, "zh")
        assert "### 1. AI安全" in result
        assert "### 2. AI应用" in result
        assert "---" in result


class TestRenderTechBrief:
    def test_renders_non_ai_articles(self, sample_article_hn):
        result = _render_tech_brief([sample_article_hn], "zh")
        assert "科技动态" in result
        assert "[HN: Show HN: Cool Project]" in result

    def test_limits_to_20(self):
        articles = [
            Article(title=f"Article {i}", url=f"http://x/{i}", source="S",
                    category="tech_general", published="2026-01-01")
            for i in range(25)
        ]
        result = _render_tech_brief(articles, "zh")
        assert "等 5 条" in result


class TestGenerateWechatArticle:
    def test_full_article_with_ai_structure(self, sample_article, now_utc):
        ai_structure = {
            "highlights": ["要点1：AI安全突破", "要点2：LLM推理优化"],
            "themes": [{
                "title": "AI安全",
                "summary": "安全形势严峻",
                "articles": [sample_article],
            }],
        }
        result = generate_wechat_article(
            ai_articles=[sample_article],
            non_ai_articles=[],
            now=now_utc,
            language="zh",
            ai_structure=ai_structure,
        )
        assert "DailyDigest" in result
        assert "要点1：AI安全突破" in result
        assert "### 1. AI安全" in result
        assert "安全形势严峻" in result

    def test_fallback_without_ai_structure(self, sample_article, now_utc):
        result = generate_wechat_article(
            ai_articles=[sample_article],
            non_ai_articles=[],
            now=now_utc,
            language="zh",
        )
        assert "DailyDigest" in result

    def test_includes_non_ai_section(self, sample_article, sample_article_hn, now_utc):
        result = generate_wechat_article(
            ai_articles=[sample_article],
            non_ai_articles=[sample_article_hn],
            now=now_utc,
            language="zh",
        )
        assert "科技动态" in result
