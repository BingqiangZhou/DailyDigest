"""Tests for core/llm.py — runtime config, retries, critique degradation."""

import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core import llm
from core.llm import _is_no_change_response, TASK_PROFILES


class FakeAPIError(Exception):
    def __init__(self, message, status_code=None, headers=None):
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(
            status_code=status_code,
            headers=headers or {},
        )


def _fake_response(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


def _fake_client(side_effect):
    create = MagicMock(side_effect=side_effect)
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        )
    )


@pytest.fixture(autouse=True)
def _reset_runtime():
    llm.reset_llm_runtime_state()
    yield
    llm.reset_llm_runtime_state()


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
        expected = {
            "classify", "topic_cluster", "tldr", "critique", "summarize",
            "deep_analysis", "wechat_structure",
            "narrative", "brief_summary", "trends",
        }
        assert set(TASK_PROFILES.keys()) == expected


class TestRetryBehavior:
    def test_retryable_429_retries_and_succeeds(self):
        client = _fake_client([
            FakeAPIError("rate limit", status_code=429, headers={"Retry-After": "0"}),
            _fake_response("ok"),
        ])

        with patch.dict(os.environ, {"LLM_MAX_RETRIES": "3"}, clear=False), \
             patch("core.llm.time.sleep") as sleep_mock:
            result = llm.chat_completion(client, "prompt", profile_name="summarize")

        summary = llm.get_llm_runtime_summary()
        assert result == "ok"
        assert client.chat.completions.create.call_count == 2
        assert summary["retries"] == 1
        assert summary["successes"] == 1
        assert summary["rate_limit_errors"] == 1
        sleep_mock.assert_called_once()

    def test_non_retryable_400_fails_without_retry(self):
        client = _fake_client([FakeAPIError("bad request", status_code=400)])

        with patch.dict(os.environ, {"LLM_MAX_RETRIES": "4"}, clear=False), \
             patch("core.llm.time.sleep") as sleep_mock:
            result = llm.chat_completion(client, "prompt", profile_name="summarize")

        summary = llm.get_llm_runtime_summary()
        assert result is None
        assert client.chat.completions.create.call_count == 1
        assert summary["retries"] == 0
        assert summary["final_failures"] == 1
        sleep_mock.assert_not_called()

    def test_context_overflow_is_non_retryable(self):
        client = _fake_client([FakeAPIError("maximum context length exceeded", status_code=400)])

        with patch("core.llm.time.sleep") as sleep_mock:
            result = llm.chat_completion(client, "prompt", profile_name="summarize")

        assert result is None
        assert client.chat.completions.create.call_count == 1
        sleep_mock.assert_not_called()


class TestCritiqueDegradation:
    def test_generate_with_critique_returns_draft_when_draft_needed_retry(self):
        client = _fake_client([
            FakeAPIError("rate limit", status_code=429, headers={"Retry-After": "0"}),
            _fake_response("draft body"),
        ])

        with patch.dict(os.environ, {"LLM_MAX_RETRIES": "3"}, clear=False), \
             patch("core.llm.time.sleep"):
            result = llm.generate_with_critique(
                client,
                "prompt",
                "summarize",
                "review:\n{draft}",
                language="zh",
            )

        assert result == "draft body"
        assert client.chat.completions.create.call_count == 2

    def test_generate_with_critique_falls_back_to_draft_after_degradation(self):
        client = _fake_client([
            _fake_response("draft body"),
            FakeAPIError("too many requests", status_code=429),
        ])

        with patch.dict(os.environ, {
            "LLM_MAX_RETRIES": "1",
            "LLM_DEGRADE_AFTER_FAILURES": "1",
        }, clear=False):
            result = llm.generate_with_critique(
                client,
                "prompt",
                "summarize",
                "review:\n{draft}",
                language="zh",
            )

        summary = llm.get_llm_runtime_summary()
        assert result == "draft body"
        assert summary["degraded_mode"] is True
        assert client.chat.completions.create.call_count == 2

    def test_optional_calls_short_circuit_once_degraded(self):
        failing = _fake_client([FakeAPIError("gateway timeout", status_code=503)])

        with patch.dict(os.environ, {
            "LLM_MAX_RETRIES": "1",
            "LLM_DEGRADE_AFTER_FAILURES": "1",
        }, clear=False):
            assert llm.chat_completion(failing, "prompt", profile_name="summarize") is None

            skipped = _fake_client([_fake_response("should not happen")])
            result = llm.chat_with_profile(skipped, "prompt", "trends", optional=True)

        assert result is None
        assert skipped.chat.completions.create.call_count == 0


class TestConcurrencyGate:
    def test_global_semaphore_caps_concurrency(self):
        active = 0
        max_active = 0
        lock = threading.Lock()
        results = []

        def create(**_kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return _fake_response("ok")

        client = _fake_client(create)

        with patch.dict(os.environ, {"LLM_MAX_CONCURRENCY": "2"}, clear=False):
            threads = [
                threading.Thread(
                    target=lambda: results.append(
                        llm.chat_with_profile(client, "prompt", "summarize")
                    )
                )
                for _ in range(5)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        assert results == ["ok"] * 5
        assert max_active <= 2
