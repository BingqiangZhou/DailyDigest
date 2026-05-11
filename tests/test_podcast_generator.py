"""Tests for podcast audio generation."""

import pytest
from core.podcast_generator import extract_report_content


class TestExtractReportContent:
    """Tests for extracting key content from Markdown reports."""

    def test_extracts_tldr(self):
        md = (
            "# 📰 DailyDigest — 2026-04-29\n\n"
            "> 扫描 554 篇\n\n---\n\n"
            "## 📌 今日要点\n\n"
            "- GPT-5.5 is OpenAI's most capable\n"
            "- OpenAI lands on AWS\n\n---\n\n"
            "## 📝 科技简讯\n\n- item1\n"
        )
        result = extract_report_content(md, "tech")
        assert "GPT-5.5 is OpenAI's most capable" in result

    def test_extracts_theme_summaries(self):
        md = (
            "# 📰 DailyDigest — 2026-04-29\n\n"
            "> scan info\n\n---\n\n"
            "## 🧭 今日动态\n\n"
            "### 一、AI巨头生态重塑 🔥  (88 篇)\n\n"
            "本周AI领域迎来深度生态重塑。核心动态有三。\n\n"
            "---\n\n"
            "### 二、安全危机 🔥  (55 篇)\n\n"
            "安全领域重大突破。\n\n"
            "---\n"
        )
        result = extract_report_content(md, "tech")
        assert "AI巨头生态重塑" in result
        assert "安全危机" in result

    def test_truncates_long_content(self):
        md = "## 📌 今日要点\n\n" + "\n".join(f"- item {i}" for i in range(500))
        result = extract_report_content(md, "tech")
        assert len(result) <= 4500

    def test_podcast_report_extracts_highlights(self):
        md = (
            "# 🎙️ AI 播客日报 — 2026-04-29\n\n"
            "> scan info\n\n---\n\n"
            "## 今日要点\n\n"
            "- 任鑫：AI 转型没戏\n"
            "- 数据标注员的困境\n\n"
            "---\n\n"
            "## 值得关注的单集\n\n"
            "- 🎧 episode1\n"
            "- 🎧 episode2\n"
        )
        result = extract_report_content(md, "podcast")
        assert "任鑫：AI 转型没戏" in result
        assert "数据标注员的困境" in result

    def test_empty_report(self):
        result = extract_report_content("", "tech")
        assert result == ""

    def test_report_type_label(self):
        md = "## 📌 今日要点\n\n- test item"
        result = extract_report_content(md, "tech")
        assert result.startswith("科技日报")
        result2 = extract_report_content(md, "podcast")
        assert result2.startswith("播客日报")
