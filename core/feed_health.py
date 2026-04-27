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
import threading
import time
from pathlib import Path

from .config import WORKSPACE_DIR
from .logging_config import get_logger

logger = get_logger("feed_health")

HEALTH_FILE = WORKSPACE_DIR / "feed_health.json"
SKIP_THRESHOLD = 5
CLEANUP_MAX_AGE_DAYS = 30
TEMP_RETRY_SECONDS = 86400
PERMANENT_RETRY_SECONDS = 7 * 86400
PERMANENT_HTTP_CODES = {401, 403, 404, 410}

_cache_lock = threading.Lock()
_cache_data = None
_cache_dirty = False
_cache_active = False


def _is_permanent_error(error_text):
    """Return True when an error likely indicates a permanently invalid feed URL."""
    if not error_text:
        return False
    text = str(error_text)
    for code in PERMANENT_HTTP_CODES:
        if f"HTTP {code}" in text:
            return True
    return False


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


class batch_health:
    """Context manager for batching feed health operations.

    Loads data once on enter, buffers all changes in memory,
    saves once on exit. Thread-safe via a module-level lock.

    Usage:
        with batch_health():
            for url in feed_urls:
                if is_healthy(url):
                    ...
                    record_success(url)
    """

    def __enter__(self):
        global _cache_data, _cache_dirty, _cache_active
        with _cache_lock:
            _cache_data = _load()
            _cache_dirty = False
            _cache_active = True
        return self

    def __exit__(self, *args):
        global _cache_data, _cache_dirty, _cache_active
        with _cache_lock:
            if _cache_dirty and _cache_data is not None:
                _save(_cache_data)
            _cache_data = None
            _cache_dirty = False
            _cache_active = False


def _get_data():
    """Return cached data if in batch mode, otherwise load from disk."""
    if _cache_active and _cache_data is not None:
        return _cache_data
    return _load()


def _set_data(data):
    """Update cached data (batch mode) or write to disk immediately."""
    global _cache_data, _cache_dirty
    if _cache_active:
        _cache_data = data
        _cache_dirty = True
    else:
        _save(data)


def is_healthy(url):
    """Return True if the feed should be fetched, False if it should be skipped."""
    data = _get_data()
    entry = data.get(url)
    if not entry:
        return True
    failures = entry.get("consecutive_failures", 0)
    if failures < SKIP_THRESHOLD:
        return True
    # Even unhealthy feeds get retried periodically.
    retry_after_seconds = int(entry.get("retry_after_seconds", TEMP_RETRY_SECONDS))
    last = entry.get("last_failure_time", 0)
    if time.time() - last > retry_after_seconds:
        hours = retry_after_seconds // 3600
        logger.info(f"[Health] {url}: retrying unhealthy feed (last failure >{hours}h ago)")
        return True
    return False


def record_success(url):
    """Record a successful fetch, resetting the failure counter."""
    with _cache_lock:
        data = _get_data()
        prev = data.get(url, {})
        data[url] = {
            "consecutive_failures": 0,
            "last_success": time.time(),
            "last_failure_time": prev.get("last_failure_time"),
            "last_error": prev.get("last_error"),
            "retry_after_seconds": TEMP_RETRY_SECONDS,
        }
        _set_data(data)


def record_failure(url, error="", permanent=None):
    """Record a failed fetch, incrementing the failure counter."""
    with _cache_lock:
        data = _get_data()
        entry = data.get(url, {})
        is_permanent = _is_permanent_error(error) if permanent is None else bool(permanent)

        failures = entry.get("consecutive_failures", 0) + 1
        if is_permanent:
            failures = max(failures, SKIP_THRESHOLD)

        retry_after_seconds = PERMANENT_RETRY_SECONDS if is_permanent else TEMP_RETRY_SECONDS

        data[url] = {
            "consecutive_failures": failures,
            "last_failure_time": time.time(),
            "last_error": str(error)[:200],
            "last_success": entry.get("last_success"),
            "retry_after_seconds": retry_after_seconds,
        }
        if failures >= SKIP_THRESHOLD:
            reason = "permanent" if is_permanent else "temporary"
            retry_hours = retry_after_seconds // 3600
            logger.warning(
                f"[Health] {url}: marked as unhealthy ({reason}, failures={failures}, retry={retry_hours}h)"
            )
        _set_data(data)


def cleanup():
    """Remove entries older than CLEANUP_MAX_AGE_DAYS with no recent activity."""
    data = _get_data()
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
        _set_data(data)
        logger.info(f"[Health] Cleaned up {len(to_remove)} stale entries")
    return len(to_remove)


def get_stats():
    """Return summary stats for logging."""
    data = _get_data()
    healthy = sum(1 for e in data.values() if e.get("consecutive_failures", 0) < SKIP_THRESHOLD)
    unhealthy = sum(1 for e in data.values() if e.get("consecutive_failures", 0) >= SKIP_THRESHOLD)
    return {"tracked": len(data), "healthy": healthy, "unhealthy": unhealthy}
