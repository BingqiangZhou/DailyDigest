"""Tests for podcast audio generation."""

import json
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch
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


class TestGenerateDialogueScript:
    """Tests for LLM-based dialogue script generation."""

    @pytest.fixture(autouse=True)
    def _patch_llm(self):
        with patch("core.llm.chat_with_profile") as mock:
            self.mock_chat = mock
            yield

    def test_parses_valid_json_response(self):
        from core.podcast_generator import generate_dialogue_script

        self.mock_chat.return_value = '[{"speaker": "A", "text": "大家好"}, {"speaker": "B", "text": "欢迎收听"}]'
        result = generate_dialogue_script(MagicMock(), "科技日报摘要", "tech")
        assert len(result) == 2
        assert result[0]["speaker"] == "A"
        assert result[0]["text"] == "大家好"

    def test_handles_code_fence_wrapping(self):
        from core.podcast_generator import generate_dialogue_script

        self.mock_chat.return_value = '```json\n[{"speaker": "A", "text": "hello"}]\n```'
        result = generate_dialogue_script(MagicMock(), "test content", "tech")
        assert len(result) == 1
        assert result[0]["speaker"] == "A"

    def test_returns_empty_on_llm_failure(self):
        from core.podcast_generator import generate_dialogue_script

        self.mock_chat.side_effect = Exception("API error")
        result = generate_dialogue_script(MagicMock(), "test", "tech")
        assert result == []

    def test_validates_speaker_values(self):
        from core.podcast_generator import generate_dialogue_script

        self.mock_chat.return_value = '[{"speaker": "A", "text": "ok"}, {"speaker": "C", "text": "bad"}, {"speaker": "B", "text": "good"}]'
        result = generate_dialogue_script(MagicMock(), "test", "tech")
        speakers = [d["speaker"] for d in result]
        assert "C" not in speakers
        assert len(result) == 2

    def test_truncates_long_script(self):
        from core.podcast_generator import generate_dialogue_script

        lines = [{"speaker": "A", "text": f"line {i}"} for i in range(100)]
        self.mock_chat.return_value = json.dumps(lines)
        result = generate_dialogue_script(MagicMock(), "test", "tech")
        assert len(result) <= 60


class TestSynthesizeAudio:
    """Tests for TTS synthesis and audio assembly."""

    def test_synthesize_dialogue_returns_bytes(self):
        from core.podcast_generator import synthesize_audio

        script = [
            {"speaker": "A", "text": "大家好，欢迎收听今天的节目。"},
            {"speaker": "B", "text": "今天我们聊聊最新的AI动态。"},
        ]
        result = synthesize_audio(script)
        assert result is not None
        assert len(result) > 0
        # MP3 files start with ID3 or 0xff sync byte
        assert result[:3] == b"ID3" or (result[0] & 0xFF) == 0xFF

    def test_synthesize_empty_script_returns_none(self):
        from core.podcast_generator import synthesize_audio

        result = synthesize_audio([])
        assert result is None

    def test_synthesize_single_speaker(self):
        from core.podcast_generator import synthesize_audio

        script = [{"speaker": "A", "text": "这是一段测试音频。"}]
        result = synthesize_audio(script)
        assert result is not None
        assert len(result) > 0


class TestGeneratePodcastAudio:
    """Tests for the top-level generate_podcast_audio orchestrator."""

    def test_generates_mp3_from_report_file(self, tmp_path):
        from core.podcast_generator import generate_podcast_audio

        report = tmp_path / "2026-04-29.md"
        report.write_text(
            "# 📰 DailyDigest — 2026-04-29\n\n"
            "> scan info\n\n---\n\n"
            "## 📌 今日要点\n\n"
            "- GPT-5.5 is great\n"
            "- OpenAI on AWS\n\n---\n"
        )

        output_dir = tmp_path / "audio"
        output_dir.mkdir()

        with patch("core.podcast_generator.generate_dialogue_script") as mock_script, \
             patch("core.podcast_generator.synthesize_audio") as mock_audio, \
             patch("core.llm.get_llm_client") as mock_client:
            mock_client.return_value = MagicMock()
            mock_script.return_value = [
                {"speaker": "A", "text": "大家好"},
                {"speaker": "B", "text": "欢迎收听"},
            ]
            mock_audio.return_value = b"fake_mp3_data"

            result = generate_podcast_audio(
                str(report), "tech", "2026-04-29", output_dir=str(output_dir)
            )

        assert result is not None
        assert Path(result).exists()
        assert Path(result).name == "2026-04-29_tech.mp3"

    def test_returns_none_on_missing_report(self):
        from core.podcast_generator import generate_podcast_audio

        result = generate_podcast_audio(
            "/nonexistent/report.md", "tech", "2026-04-29"
        )
        assert result is None

    def test_returns_none_on_empty_script(self, tmp_path):
        from core.podcast_generator import generate_podcast_audio

        report = tmp_path / "2026-04-29.md"
        report.write_text("# 📰 DailyDigest\n\n## 📌 今日要点\n\n- item\n")

        with patch("core.podcast_generator.generate_dialogue_script") as mock_script, \
             patch("core.llm.get_llm_client") as mock_client:
            mock_client.return_value = MagicMock()
            mock_script.return_value = []
            result = generate_podcast_audio(
                str(report), "tech", "2026-04-29", output_dir=str(tmp_path)
            )

        assert result is None
