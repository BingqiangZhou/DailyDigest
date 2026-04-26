"""
Feed health tracker — persist fetch results across runs to skip consistently failing feeds.

Stores per-URL health data in workspace/feed_health.json:
  - consecutive_failures: count of consecutive fetch failures
  - last_success: ISO timestamp of last successful fetch
  - last_error: error message from most recent failure

A feed is "skipped" after SKIP_THRESHOLD consecutive failures (default 5).
Success resets the counter.
"""

import json
import time
from pathlib import Path

from .config import WORKSPACE_DIR
from .logging_config import get_logger

logger = get_logger("feed_health")

HEALTH_FILE = WORKSPACE_DIR / "feed_health.json"
SKIP_THRESHOLD = 5
CLEANUP_MAX_AGE_DAYS = 30


def _load():
    if HEALTH_FILE.exists():
        try:
            with open(HEALTH_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save(data):
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    with open(HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_healthy(url):
    """Return True if the feed should be fetched, False if it should be skipped."""
    data = _load()
    entry = data.get(url)
    if not entry:
        return True
    failures = entry.get("consecutive_failures", 0)
    if failures < SKIP_THRESHOLD:
        return True
    # Even unhealthy feeds get retried periodically (once per day)
    last = entry.get("last_failure_time", 0)
    if time.time() - last > 86400:
        logger.info(f"[Health] {url}: retrying unhealthy feed (last failure >24h ago)")
        return True
    return False


def record_success(url):
    """Record a successful fetch, resetting the failure counter."""
    data = _load()
    prev = data.get(url, {})
    data[url] = {
        "consecutive_failures": 0,
        "last_success": time.time(),
        "last_failure_time": prev.get("last_failure_time"),
        "last_error": prev.get("last_error"),
    }
    _save(data)


def record_failure(url, error=""):
    """Record a failed fetch, incrementing the failure counter."""
    data = _load()
    entry = data.get(url, {})
    failures = entry.get("consecutive_failures", 0) + 1
    data[url] = {
        "consecutive_failures": failures,
        "last_failure_time": time.time(),
        "last_error": str(error)[:200],
        "last_success": entry.get("last_success"),
    }
    if failures == SKIP_THRESHOLD:
        logger.warning(f"[Health] {url}: marked as unhealthy ({failures} consecutive failures)")
    _save(data)


def cleanup():
    """Remove entries older than CLEANUP_MAX_AGE_DAYS with no recent activity."""
    data = _load()
    cutoff = time.time() - (CLEANUP_MAX_AGE_DAYS * 86400)
    to_remove = []
    for url, entry in data.items():
        last_success = entry.get("last_success") or 0
        last_failure = entry.get("last_failure_time") or 0
        last_activity = max(last_success, last_failure)
        if last_activity < cutoff:
            to_remove.append(url)
    for url in to_remove:
        del data[url]
    if to_remove:
        _save(data)
        logger.info(f"[Health] Cleaned up {len(to_remove)} stale entries")
    return len(to_remove)


def get_stats():
    """Return summary stats for logging."""
    data = _load()
    healthy = sum(1 for e in data.values() if e.get("consecutive_failures", 0) < SKIP_THRESHOLD)
    unhealthy = sum(1 for e in data.values() if e.get("consecutive_failures", 0) >= SKIP_THRESHOLD)
    return {"tracked": len(data), "healthy": healthy, "unhealthy": unhealthy}
