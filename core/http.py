"""
HTTP utility module
Shared SSL context creation, URL fetching with ETag cache, and retry logic.

Error status codes:
    -1  Unknown / unclassified error
    -2  Timeout (socket.timeout or urllib timeout)
    -3  DNS resolution failure
    -4  Connection refused / reset
    -5  SSL error (even after relaxed retry)
"""

import os
import ssl
import socket
import time
import random
import urllib.request as urllib_request
import urllib.error as urllib_error

from .logging_config import get_logger

logger = get_logger("http")

# Configurable timeout via environment variable (default 20 seconds)
DEFAULT_TIMEOUT = int(os.environ.get("RSS_TIMEOUT", "20"))


def _classify_error(exc):
    """Classify a network exception into a specific error status code."""
    err_str = str(exc).lower()
    exc_type = type(exc).__name__.lower()

    # Timeout detection
    if isinstance(exc, socket.timeout) or "timed out" in err_str or "timeout" in exc_type:
        return -2
    if "timeout" in err_str:
        return -2

    # DNS resolution failure
    if "getaddrinfo" in err_str or "name or service not known" in err_str or "nodename" in err_str:
        return -3
    if "name resolution" in err_str or "temporary failure in name resolution" in err_str:
        return -3

    # Connection refused / reset
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, BrokenPipeError)):
        return -4
    if "connection refused" in err_str or "connection reset" in err_str:
        return -4
    if "broken pipe" in err_str:
        return -4

    # SSL errors (will be retried with relaxed context)
    if any(kw in err_str for kw in ["ssl", "certificate", "cert", "hostname"]):
        return -5

    return -1


def create_ssl_context(relaxed=False):
    """Create an SSL context.

    Args:
        relaxed: If True, skip certificate verification (for fallback).

    Returns:
        ssl.SSLContext configured for HTTPS requests.
    """
    if relaxed:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        return ssl.create_default_context()
    except Exception:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def fetch_url(url, headers=None, cache=None, timeout=None):
    """Fetch a URL with ETag/If-Modified-Since cache support.

    Args:
        url: URL to fetch.
        headers: Optional dict of HTTP headers (default: {"User-Agent": "DailyDigest/1.0"}).
        cache: Optional dict with cached "etag" / "last_modified" for this URL.
        timeout: Request timeout in seconds (default: RSS_TIMEOUT env var or 20).

    Returns:
        (body, status, new_cache) — body is str or None, status is int, new_cache is dict.
    """
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    if headers is None:
        headers = {"User-Agent": "DailyDigest/1.0"}
    else:
        headers = dict(headers)

    cached = cache.get(url, {}) if cache else {}

    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
        headers["If-Modified-Since"] = cached["last_modified"]

    req = urllib_request.Request(url, headers=headers)

    def _read_response(resp):
        """Read response body and extract cache headers."""
        body = resp.read().decode("utf-8", errors="replace")
        new_cache = {}
        etag = resp.headers.get("ETag")
        last_mod = resp.headers.get("Last-Modified")
        if etag:
            new_cache["etag"] = etag
        if last_mod:
            new_cache["last_modified"] = last_mod
        return body, resp.status, new_cache

    try:
        ctx = create_ssl_context()
        with urllib_request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return _read_response(resp)
    except urllib_error.HTTPError as e:
        if e.code == 304:
            return None, 304, cached
        return None, e.code, {}
    except Exception as e:
        status = _classify_error(e)
        logger.debug(f"[HTTP] {url}: {status} ({type(e).__name__}: {e})")
        # Only retry with relaxed SSL on SSL-related errors
        if status == -5:
            try:
                relaxed = create_ssl_context(relaxed=True)
                with urllib_request.urlopen(req, context=relaxed, timeout=timeout) as resp:
                    return _read_response(resp)
            except urllib_error.HTTPError as e:
                if e.code == 304:
                    return None, 304, cached
                return None, e.code, {}
            except Exception:
                return None, -5, {}
        return None, status, {}


def fetch_url_with_retry(url, headers=None, cache=None, timeout=None, max_retries=2):
    """Fetch a URL with exponential-backoff retries.

    Args:
        url: URL to fetch.
        headers: Optional dict of HTTP headers.
        cache: Optional dict with cached "etag" / "last_modified" for this URL.
        timeout: Request timeout in seconds (default: RSS_TIMEOUT env var or 20).
        max_retries: Maximum number of retries after the first attempt.

    Returns:
        (body, status, new_cache) — same as fetch_url.
    """
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    last_status = -1
    for attempt in range(max_retries + 1):
        body, status, new_cache = fetch_url(url, headers=headers, cache=cache, timeout=timeout)
        if body is not None or status == 304:
            return body, status, new_cache
        last_status = status
        if attempt < max_retries:
            delay = min(2 ** attempt * 2, 30) + random.uniform(0, 1)
            time.sleep(delay)
    return None, last_status, {}


# Human-readable error labels for logging
ERROR_LABELS = {
    -1: "未知错误",
    -2: "超时",
    -3: "DNS解析失败",
    -4: "连接被拒绝/重置",
    -5: "SSL错误",
}


def error_label(status):
    """Return a human-readable error label for a negative status code."""
    return ERROR_LABELS.get(status, f"HTTP {status}")
