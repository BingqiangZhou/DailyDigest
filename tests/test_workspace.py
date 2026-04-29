"""Tests for workspace metadata and run-scoped summary merging."""

import json

from core.workspace import merge_batch_summaries, save_workspace_updates
from .conftest import make_article as _make_article


class TestWorkspaceSummaries:
    def test_save_workspace_updates_adds_generated_at(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.config.WORKSPACE_DIR", tmp_path)
        save_workspace_updates("tech", [_make_article()], {"run_id": "run-1"})
        payload = json.loads((tmp_path / "tech_updates.json").read_text(encoding="utf-8"))
        assert payload["metadata"]["run_id"] == "run-1"
        assert payload["metadata"]["generated_at"]

    def test_merge_batch_summaries_prefers_current_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.config.WORKSPACE_DIR", tmp_path)

        current = {
            "summaries": [
                {
                    "url": "https://example.com/current",
                    "ai_summary": "current summary",
                    "tier": "must_read",
                }
            ]
        }
        old = {
            "summaries": [
                {
                    "url": "https://example.com/old",
                    "ai_summary": "old summary",
                    "tier": "noteworthy",
                }
            ]
        }

        (tmp_path / "tech_summary_batch_run-new_0.json").write_text(
            json.dumps(current, ensure_ascii=False),
            encoding="utf-8",
        )
        (tmp_path / "tech_summary_batch_run-old_0.json").write_text(
            json.dumps(old, ensure_ascii=False),
            encoding="utf-8",
        )

        merged = merge_batch_summaries("tech", run_id="run-new", generated_at="2026-04-27T12:00:00+00:00")
        assert "https://example.com/current" in merged
        assert "https://example.com/old" not in merged
