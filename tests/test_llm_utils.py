"""Tests for JSON-from-LLM parsing utilities (core/llm_utils.py).

These tests define the contract before the implementation is extracted.
After Phase 2, they import from core.llm_utils directly.
"""

import json
import pytest


# We'll import from the new module once it exists.
# For now, define the expected interface via a pytest import hook.
def _get_parse_fn():
    """Get parse_llm_json from the module (exists after Phase 2)."""
    from core.llm_utils import parse_llm_json
    return parse_llm_json


def _get_strip_fn():
    """Get strip_code_fences from the module (exists after Phase 2)."""
    from core.llm_utils import strip_code_fences
    return strip_code_fences


class TestParseLlmJson:
    def test_clean_json(self):
        parse = _get_parse_fn()
        result = parse('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_whitespace(self):
        parse = _get_parse_fn()
        result = parse('  \n{"key": "val"}\n  ')
        assert result == {"key": "val"}

    def test_json_in_code_fence(self):
        parse = _get_parse_fn()
        raw = '```json\n{"key": "val"}\n```'
        result = parse(raw)
        assert result == {"key": "val"}

    def test_json_in_plain_fence(self):
        parse = _get_parse_fn()
        raw = '```\n{"key": "val"}\n```'
        result = parse(raw)
        assert result == {"key": "val"}

    def test_json_with_preamble(self):
        parse = _get_parse_fn()
        raw = 'Here is the result:\n```json\n{"x": 1}\n```'
        result = parse(raw)
        assert result == {"x": 1}

    def test_invalid_json_raises(self):
        parse = _get_parse_fn()
        with pytest.raises(ValueError):
            parse("not json at all")

    def test_complex_nested_json(self):
        parse = _get_parse_fn()
        data = {"summaries": [{"url": "http://x", "ai_summary": "test"}], "count": 1}
        raw = f"```json\n{json.dumps(data, ensure_ascii=False)}\n```"
        result = parse(raw)
        assert result == data


class TestStripCodeFences:
    def test_no_fence(self):
        strip = _get_strip_fn()
        assert strip("hello world") == "hello world"

    def test_plain_fence(self):
        strip = _get_strip_fn()
        assert strip("```\nhello\n```") == "hello"

    def test_json_fence(self):
        strip = _get_strip_fn()
        assert strip("```json\nhello\n```") == "hello"

    def test_leading_whitespace(self):
        strip = _get_strip_fn()
        assert strip("  ```\nhello\n```  ") == "hello"


class TestStripThinkingOutput:
    def _get_fn(self):
        from core.llm_utils import _strip_thinking_output
        return _strip_thinking_output

    def test_strips_english_thinking_before_chinese(self):
        strip = self._get_fn()
        raw = (
            "The user wants a summary for CTO readers.\n"
            "We need to pick 3-5 items.\n"
            "研究团队发布新模型，在多个基准上取得突破性进展。\n"
            "该成果将推动行业技术迭代。"
        )
        result = strip(raw)
        assert "研究团队发布新模型" in result
        assert "The user wants" not in result

    def test_strips_c_style_thinking(self):
        strip = self._get_fn()
        raw = "/* thinking process */\n这是实际输出内容，包含中文技术分析。"
        result = strip(raw)
        assert "这是实际输出内容" in result

    def test_preserves_clean_chinese_content(self):
        strip = self._get_fn()
        raw = "这是一段正常的中文内容，包含技术分析和行业洞察。内容长度足够长。"
        assert strip(raw) == raw

    def test_strips_character_counting_lines(self):
        strip = self._get_fn()
        raw = (
            '"研"1 "究"2 "人"3 "员"4\n'
            "研究团队发布新论文，展示了突破性的实验结果和分析。"
        )
        result = strip(raw)
        assert "研究团队发布新论文" in result
        assert '"研"1' not in result

    def test_real_minimax_output(self):
        strip = self._get_fn()
        raw = (
            "The user wants a news narrative in Chinese, 200-300 characters.\n"
            "3. If multiple sources, combine.\n"
            "Continue with implications: results show significant improvement.\n"
            "研究团队在arXiv发布新论文，采用强化学习激励视觉语言模型。"
            "实验基于Qwen3-VL-2B模型，在包含数学和科学的评测集上准确率提升3.33%。\n"
            '"研"1 "究"2\n'
            "Count characters: 250 total."
        )
        result = strip(raw)
        assert "研究团队在arXiv发布新论文" in result
        assert "The user wants" not in result
        assert "Count characters" not in result

    def test_sanitizes_report_markdown_reasoning_lines(self):
        from core.llm_utils import sanitize_report_markdown

        raw = (
            "# DailyDigest\n\n"
            "## 📌 TL;DR\n\n"
            "> <think>The user wants a TL;DR summary.\n"
            "> - OpenAI folds Codex into GPT-5.5\n\n"
            "## 🧭 今日动态\n\n"
            "<think>Count characters: 220\n"
            "### 一、模型与平台\n\n"
            "真实内容保留。\n"
        )
        result = sanitize_report_markdown(raw)
        assert "<think>" not in result
        assert "The user wants" not in result
        assert "Count characters" not in result
        assert "真实内容保留" in result
