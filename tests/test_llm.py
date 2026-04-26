"""Tests for core/llm.py — no-change detection, critique helpers."""

import pytest
from core.llm import _is_no_change_response, TASK_PROFILES


class TestNoChangeDetection:
    def test_chinese_no_change(self):
        assert _is_no_change_response("这份摘要无需修改")

    def test_chinese_verified(self):
        assert _is_no_change_response("核查通过——无问题发现")

    def test_english_no_changes(self):
        assert _is_no_change_response("No changes needed")

    def test_english_verified(self):
        assert _is_no_change_response("Verified — no issues found")

    def test_english_looks_good(self):
        assert _is_no_change_response("Looks good to me")

    def test_english_no_revision(self):
        assert _is_no_change_response("No revision necessary")

    def test_case_insensitive(self):
        assert _is_no_change_response("NO CHANGES NEEDED")
        assert _is_no_change_response("verified completely")

    def test_actual_critique_not_matched(self):
        assert not _is_no_change_response("Fix the third paragraph, it contains a factual error")

    def test_mixed_content(self):
        assert not _is_no_change_response("The summary is good but paragraph 2 needs work")

    def test_empty_string(self):
        assert not _is_no_change_response("")


class TestTaskProfiles:
    def test_all_profiles_have_required_keys(self):
        for name, profile in TASK_PROFILES.items():
            assert "temperature" in profile, f"Missing temperature in {name}"
            assert "top_p" in profile, f"Missing top_p in {name}"
            assert "max_tokens" in profile, f"Missing max_tokens in {name}"

    def test_classify_is_low_temperature(self):
        assert TASK_PROFILES["classify"]["temperature"] <= 0.2

    def test_deep_analysis_is_high_temperature(self):
        assert TASK_PROFILES["deep_analysis"]["temperature"] >= 0.5

    def test_tldr_is_low_max_tokens(self):
        assert TASK_PROFILES["tldr"]["max_tokens"] <= 1000

    def test_expected_profiles_exist(self):
        expected = {"classify", "topic_cluster", "tldr", "critique", "summarize", "deep_analysis", "wechat_structure"}
        assert set(TASK_PROFILES.keys()) == expected
