"""
Workspace and cache I/O utilities for DailyDigest.

Handles workspace directory management and workspace data
loading/saving for the pipeline.
"""

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .logging_config import get_logger

logger = get_logger("workspace")


def ensure_pipeline_dirs():
    """Create output and workspace dirs if needed."""
    from .config import ensure_dirs, OUTPUT_DIR, WORKSPACE_DIR
    ensure_dirs(OUTPUT_DIR, WORKSPACE_DIR)


def save_workspace_updates(source_type, updates, metadata=None):
    """Save articles to workspace/{source_type}_updates.json (atomic write)."""
    from .config import WORKSPACE_DIR
    path = WORKSPACE_DIR / f"{source_type}_updates.json"
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(metadata or {})
    payload.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    tmp_path = path.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"metadata": payload, "updates": [asdict(a) for a in updates]}, f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
    except Exception as e:
        logger.error(f"[Workspace] save failed: {e}")
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    return path


def load_workspace_data(source_type):
    """Load {source_type}_updates.json from workspace.  Returns dict or None."""
    from .config import WORKSPACE_DIR
    path = WORKSPACE_DIR / f"{source_type}_updates.json"
    if not path.exists():
        logger.warning(f"⚠️ workspace/{source_type}_updates.json not found; run fetch first.")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _summary_batch_paths(source_type, run_id=None, generated_at=None):
    """Return summary batch files for a specific run, falling back cautiously."""
    from .config import WORKSPACE_DIR
    candidates = []

    if run_id:
        patterns = [
            f"{source_type}_summary_batch_{run_id}_*.json",
            f"{source_type}_summary_batch_{run_id}.json",
        ]
        for pattern in patterns:
            candidates.extend(sorted(WORKSPACE_DIR.glob(pattern)))
        if candidates:
            return candidates

    generated_date = ""
    if generated_at:
        try:
            generated_date = datetime.fromisoformat(generated_at.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            generated_date = ""

    legacy = sorted(WORKSPACE_DIR.glob(f"{source_type}_summary_batch_*.json"))
    if not legacy:
        return []
    if not generated_date:
        return legacy

    dated = []
    for path in legacy:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        if modified == generated_date:
            dated.append(path)
    return dated or legacy


def merge_batch_summaries(source_type, run_id=None, generated_at=None):
    """Merge summary batch files for a single run into a dict."""
    from .config import WORKSPACE_DIR
    summary_map = {}
    for p in _summary_batch_paths(source_type, run_id=run_id, generated_at=generated_at):
        with open(p, "r", encoding="utf-8") as f:
            batch = json.load(f)
        if source_type == "podcast":
            for url, summary in batch.items():
                summary_map[url] = summary
        else:
            items = batch.get("summaries", [])
            for item in items:
                url = item.get("url") or item.get("article_url", "")
                if url:
                    summary_map[url] = item if source_type == "tech" else item.get("ai_summary", "")
    return summary_map
