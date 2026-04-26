"""Tests for the feed health tracker."""

import json
import time
import pytest
from unittest.mock import patch

from core.feed_health import (
    is_healthy, record_success, record_failure, cleanup, get_stats,
    HEALTH_FILE, SKIP_THRESHOLD,
)


@pytest.fixture
def fresh_health(tmp_path, monkeypatch):
    """Provide a clean health file for each test."""
    health_file = tmp_path / "feed_health.json"
    monkeypatch.setattr("core.feed_health.HEALTH_FILE", health_file)
    # Clear any cached data
    return health_file


class TestIsHealthy:
    def test_unknown_feed_is_healthy(self, fresh_health):
        assert is_healthy("https://unknown.example.com/feed") is True

    def test_few_failures_still_healthy(self, fresh_health):
        url = "https://example.com/feed"
        for _ in range(SKIP_THRESHOLD - 1):
            record_failure(url, "timeout")
        assert is_healthy(url) is True

    def test_threshold_failures_unhealthy(self, fresh_health):
        url = "https://example.com/feed"
        for _ in range(SKIP_THRESHOLD):
            record_failure(url, "timeout")
        assert is_healthy(url) is False

    def test_success_resets_to_healthy(self, fresh_health):
        url = "https://example.com/feed"
        for _ in range(SKIP_THRESHOLD):
            record_failure(url, "timeout")
        assert is_healthy(url) is False
        record_success(url)
        assert is_healthy(url) is True

    def test_unhealthy_retried_after_24h(self, fresh_health):
        url = "https://example.com/feed"
        for _ in range(SKIP_THRESHOLD):
            record_failure(url, "timeout")
        assert is_healthy(url) is False
        # Simulate 25 hours passing
        with patch("core.feed_health.time") as mock_time:
            mock_time.time.return_value = time.time() + 90000
            assert is_healthy(url) is True


class TestRecordSuccess:
    def test_resets_failure_count(self, fresh_health):
        url = "https://example.com/feed"
        record_failure(url, "error")
        record_failure(url, "error")
        record_success(url)
        data = json.loads(fresh_health.read_text())
        assert data[url]["consecutive_failures"] == 0

    def test_stores_timestamp(self, fresh_health):
        url = "https://example.com/feed"
        record_success(url)
        data = json.loads(fresh_health.read_text())
        assert "last_success" in data[url]


class TestRecordFailure:
    def test_increments_counter(self, fresh_health):
        url = "https://example.com/feed"
        record_failure(url, "error1")
        record_failure(url, "error2")
        data = json.loads(fresh_health.read_text())
        assert data[url]["consecutive_failures"] == 2
        assert "error2" in data[url]["last_error"]

    def test_persists_across_calls(self, fresh_health):
        url = "https://example.com/feed"
        record_failure(url, "first error")
        # Read back from file
        data = json.loads(fresh_health.read_text())
        assert url in data
        assert data[url]["consecutive_failures"] == 1


class TestCleanup:
    def test_removes_stale_entries(self, fresh_health):
        url = "https://stale.example.com/feed"
        record_failure(url, "old error")
        # Simulate 31 days passing
        with patch("core.feed_health.time") as mock_time:
            mock_time.time.return_value = time.time() + 31 * 86400
            removed = cleanup()
        assert removed == 1
        assert is_healthy(url) is True  # Gone from tracker, treated as healthy

    def test_keeps_recent_entries(self, fresh_health):
        url = "https://fresh.example.com/feed"
        record_success(url)
        removed = cleanup()
        assert removed == 0
        assert is_healthy(url) is True


class TestGetStats:
    def test_empty_tracker(self, fresh_health):
        stats = get_stats()
        assert stats["tracked"] == 0
        assert stats["healthy"] == 0

    def test_mixed_health(self, fresh_health):
        healthy_url = "https://ok.example.com/feed"
        failing_url = "https://bad.example.com/feed"
        record_success(healthy_url)
        for _ in range(SKIP_THRESHOLD):
            record_failure(failing_url, "timeout")
        stats = get_stats()
        assert stats["tracked"] == 2
        assert stats["healthy"] == 1
        assert stats["unhealthy"] == 1
