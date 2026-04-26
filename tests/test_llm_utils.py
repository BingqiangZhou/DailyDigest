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
