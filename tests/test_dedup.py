"""Tests for core/dedup.py — URL normalization, article ID, filter and mark."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch
from core.article import Article
from core.dedup import (
    article_id,
    filter_and_mark,
    cleanup_old_entries,
    _normalize_url_for_dedup,
)


class TestArticleId:
    def test_deterministic(self):
        a = Article(title="T", url="http://example.com/1", source="S",
                    category="ai_ml", published="2026-01-01")
        assert article_id(a) == article_id(a)

    def test_different_urls_different_ids(self):
        a1 = Article(title="T", url="http://example.com/1", source="S",
                     category="ai_ml", published="2026-01-01")
        a2 = Article(title="T", url="http://example.com/2", source="S",
                     category="ai_ml", published="2026-01-01")
        assert article_id(a1) != article_id(a2)


class TestNormalizeUrlForDedup:
    def test_strips_utm_params(self):
        url = "http://example.com/article?utm_source=twitter&id=1"
        result = _normalize_url_for_dedup(url)
        assert "utm_source" not in result
        assert "id=1" in result

    def test_removes_trailing_slash(self):
        assert _normalize_url_for_dedup("http://example.com/path/") == \
               _normalize_url_for_dedup("http://example.com/path")

    def test_removes_fragment(self):
        url = "http://example.com/article#section"
        result = _normalize_url_for_dedup(url)
        assert "#" not in result


class TestFilterAndMark:
    def _make_tracker(self, tmp_path):
        """Return a patcher for TRACKER_FILE pointing to tmp_path."""
        return patch("core.dedup.TRACKER_FILE", tmp_path / "processed_articles.json")

    def test_new_articles_pass_through(self, tmp_path):
        articles = [
            Article(title=f"Article {i}", url=f"http://example.com/{i}",
                    source="S", category="ai_ml", published="2026-01-01")
            for i in range(5)
        ]
        with self._make_tracker(tmp_path):
            result = filter_and_mark(articles)
        assert len(result) == 5

    def test_same_url_deduped_within_batch(self, tmp_path):
        """Two articles with the same URL: only 1 passes through in one batch."""
        articles = [
            Article(title="A", url="http://example.com/1",
                    source="S", category="ai_ml", published="2026-01-01"),
            Article(title="B", url="http://example.com/1",
                    source="S", category="ai_ml", published="2026-01-01"),
        ]
        with self._make_tracker(tmp_path):
            result = filter_and_mark(articles)
        assert len(result) == 1

    def test_duplicates_filtered_across_batches(self, tmp_path):
        articles = [
            Article(title="A", url="http://example.com/1",
                    source="S", category="ai_ml", published="2026-01-01"),
        ]
        with self._make_tracker(tmp_path):
            first = filter_and_mark(articles)
            second = filter_and_mark(articles)
        assert len(first) == 1
        assert len(second) == 0

    def test_empty_list(self, tmp_path):
        with self._make_tracker(tmp_path):
            result = filter_and_mark([])
        assert result == []


class TestCleanupOldEntries:
    def _make_tracker(self, tmp_path):
        return patch("core.dedup.TRACKER_FILE", tmp_path / "processed_articles.json")

    def test_cleanup_removes_old_entries(self, tmp_path):
        """Write a tracker with an old entry, cleanup should remove it."""
        old_ts = "2020-01-01T00:00:00+00:00"
        tracker_data = {
            "articles": {
                "abc123": {"title": "Old", "source": "S", "processed_at": old_ts},
            }
        }
        tracker_file = tmp_path / "processed_articles.json"
        tracker_file.write_text(json.dumps(tracker_data), encoding="utf-8")

        with self._make_tracker(tmp_path):
            cleanup_old_entries(days=30)

        # After cleanup, the tracker should be empty
        remaining = json.loads(tracker_file.read_text(encoding="utf-8"))
        assert len(remaining.get("articles", {})) == 0
