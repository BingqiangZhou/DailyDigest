"""
Workspace and cache I/O utilities for DailyDigest.

Handles HTTP cache persistence, workspace directory management,
and workspace data loading/saving for the pipeline.
"""

import json
from dataclasses import asdict
from pathlib import Path

from .logging_config import get_logger

logger = get_logger("workspace")


def load_http_cache(name):
    """Load an HTTP cache dict from workspace/{name}.

    Returns (cache_dict, cache_path).
    """
    from .config import WORKSPACE_DIR
    cache_path = WORKSPACE_DIR / name
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f), cache_path
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"[Cache] cache file corrupted, ignoring: {cache_path}")
    return {}, cache_path


def save_http_cache(cache_path, cache):
    """Save *cache* dict to *cache_path* atomically."""
    try:
        tmp_path = cache_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        tmp_path.replace(cache_path)
    except Exception as e:
        logger.error(f"[Cache] cache save failed: {e}")


def ensure_pipeline_dirs():
    """Create output and workspace dirs if needed."""
    from .config import ensure_dirs, OUTPUT_DIR, WORKSPACE_DIR
    ensure_dirs(OUTPUT_DIR, WORKSPACE_DIR)


def save_workspace_updates(source_type, updates, metadata=None):
    """Save articles to workspace/{source_type}_updates.json."""
    from .config import WORKSPACE_DIR
    path = WORKSPACE_DIR / f"{source_type}_updates.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"metadata": metadata or {}, "updates": [asdict(a) for a in updates]}, f, ensure_ascii=False, indent=2)
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


def merge_batch_summaries(source_type):
    """Glob {source_type}_summary_batch_*.json and merge into a single dict."""
    from .config import WORKSPACE_DIR
    summary_map = {}
    for p in sorted(WORKSPACE_DIR.glob(f"{source_type}_summary_batch_*.json")):
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
