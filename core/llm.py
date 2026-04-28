"""
LLM client singleton and communication layer.
Provides a lazy-initialised OpenAI-compatible client with task-specific profiles.
"""

import atexit
import os
import random
import threading
import time

from .logging_config import get_logger
from .llm_utils import sanitize_generated_text

logger = get_logger("llm")

# Default provider configuration
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/llama-3.1-nemotron-70b-instruct"
_INCOMPATIBLE_NVIDIA_PREFIXES = (
    "openai/",
    "anthropic/",
    "claude",
    "gpt-",
)

# Task-specific LLM parameter profiles
TASK_PROFILES = {
    "classify": {"temperature": 0.1, "top_p": 0.9, "max_tokens": 2000},
    "topic_cluster": {"temperature": 0.2, "top_p": 0.9, "max_tokens": 2000},
    "tldr": {"temperature": 0.3, "top_p": 0.9, "max_tokens": 500},
    "critique": {"temperature": 0.3, "top_p": 0.9, "max_tokens": 1000},
    "summarize": {"temperature": 0.5, "top_p": 0.9, "max_tokens": 6000},
    "deep_analysis": {"temperature": 0.7, "top_p": 0.9, "max_tokens": 6000},
    "wechat_structure": {"temperature": 0.5, "top_p": 0.9, "max_tokens": 4000},
    "narrative": {"temperature": 0.6, "top_p": 0.9, "max_tokens": 800},
    "brief_summary": {"temperature": 0.3, "top_p": 0.9, "max_tokens": 200},
    "trends": {"temperature": 0.5, "top_p": 0.9, "max_tokens": 1500},
}

# Module-level singleton
_client = None
_config_logged = False
_semaphore = None
_semaphore_size = None


class _LlmRuntimeState:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.total_requests = 0
        self.total_attempts = 0
        self.successes = 0
        self.retries = 0
        self.final_failures = 0
        self.rate_limit_errors = 0
        self.total_latency_seconds = 0.0
        self.total_retryable_failures = 0
        self.consecutive_retryable_failures = 0
        self.degraded_mode = False

    def begin_request(self):
        with self.lock:
            self.total_requests += 1

    def record_attempt(self):
        with self.lock:
            self.total_attempts += 1

    def record_success(self, latency_seconds):
        with self.lock:
            self.successes += 1
            self.total_latency_seconds += latency_seconds
            self.consecutive_retryable_failures = 0

    def _note_retryable_failure_locked(self, rate_limited):
        threshold = get_degrade_after_failures()
        if rate_limited:
            self.rate_limit_errors += 1
        self.total_retryable_failures += 1
        self.consecutive_retryable_failures += 1
        if self.total_retryable_failures >= threshold:
            self.degraded_mode = True

    def record_retryable_error(self, rate_limited):
        with self.lock:
            self.retries += 1
            self._note_retryable_failure_locked(rate_limited)

    def record_failure(self, retryable, rate_limited, latency_seconds):
        with self.lock:
            self.final_failures += 1
            self.total_latency_seconds += latency_seconds
            if retryable:
                self._note_retryable_failure_locked(rate_limited)
            else:
                self.consecutive_retryable_failures = 0

    def snapshot(self):
        with self.lock:
            avg_latency = (
                self.total_latency_seconds / self.total_attempts
                if self.total_attempts else 0.0
            )
            return {
                "total_requests": self.total_requests,
                "total_attempts": self.total_attempts,
                "successes": self.successes,
                "retries": self.retries,
                "final_failures": self.final_failures,
                "rate_limit_errors": self.rate_limit_errors,
                "avg_latency_seconds": avg_latency,
                "degraded_mode": self.degraded_mode,
                "total_retryable_failures": self.total_retryable_failures,
                "consecutive_retryable_failures": self.consecutive_retryable_failures,
            }


_runtime_state = _LlmRuntimeState()


def reset_llm_runtime_state():
    """Reset singleton client and runtime counters (useful for tests)."""
    global _client, _config_logged, _semaphore, _semaphore_size
    _client = None
    _config_logged = False
    _semaphore = None
    _semaphore_size = None
    _runtime_state.reset()


def get_llm_timeout_seconds():
    return int(os.environ.get("LLM_TIMEOUT_SECONDS", "180"))


def get_llm_max_retries():
    return int(os.environ.get("LLM_MAX_RETRIES", "4"))


def get_llm_max_concurrency():
    return max(1, int(os.environ.get("LLM_MAX_CONCURRENCY", "1")))


def get_llm_retry_base_seconds():
    return max(1.0, float(os.environ.get("LLM_RETRY_BASE_SECONDS", "2")))


def get_llm_retry_max_seconds():
    return max(1.0, float(os.environ.get("LLM_RETRY_MAX_SECONDS", "30")))


def get_degrade_after_failures():
    return max(1, int(os.environ.get("LLM_DEGRADE_AFTER_FAILURES", "3")))


def limit_llm_workers(requested):
    """Cap local worker pools against the shared LLM concurrency budget."""
    return max(1, min(requested, get_llm_max_concurrency()))


def _detect_provider(base_url):
    base = (base_url or "").lower()
    if "nvidia.com" in base:
        return "nvidia"
    if "openrouter" in base:
        return "openrouter"
    if "deepseek" in base:
        return "deepseek"
    if "siliconflow" in base:
        return "siliconflow"
    if "localhost" in base or "127.0.0.1" in base or "ollama" in base:
        return "local"
    return "custom"


def _is_obviously_incompatible_nvidia_model(model):
    lowered = (model or "").strip().lower()
    if not lowered:
        return True
    # Check the full string and the basename after '/' (e.g. "z-ai/glm4.7" -> "glm4.7")
    parts_to_check = [lowered]
    if "/" in lowered:
        parts_to_check.append(lowered.rsplit("/", 1)[-1])
    return any(
        part.startswith(prefix)
        for part in parts_to_check
        for prefix in _INCOMPATIBLE_NVIDIA_PREFIXES
    )


def get_llm_runtime_config():
    """Resolve runtime configuration with provider-aware model defaults."""
    base_url = os.environ.get("BASE_URL") or DEFAULT_BASE_URL
    provider = _detect_provider(base_url)
    raw_model = (os.environ.get("MODEL") or "").strip()
    model = raw_model or DEFAULT_MODEL
    model_warning = None

    if provider == "nvidia" and _is_obviously_incompatible_nvidia_model(raw_model):
        if raw_model:
            model_warning = (
                f"[AI] ⚠️ MODEL={raw_model} 与 NVIDIA BASE_URL 不匹配，"
                f"自动切换到 {DEFAULT_MODEL}"
            )
        else:
            model_warning = (
                f"[AI] ⚠️ NVIDIA BASE_URL 未指定 MODEL，自动切换到 {DEFAULT_MODEL}"
            )
        model = DEFAULT_MODEL

    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "timeout_seconds": get_llm_timeout_seconds(),
        "max_retries": get_llm_max_retries(),
        "max_concurrency": get_llm_max_concurrency(),
        "retry_base_seconds": get_llm_retry_base_seconds(),
        "retry_max_seconds": get_llm_retry_max_seconds(),
        "degrade_after_failures": get_degrade_after_failures(),
        "critique_enabled": not bool(os.environ.get("SKIP_CRITIQUE")),
        "model_warning": model_warning,
    }


def _log_runtime_config_once():
    global _config_logged
    if _config_logged:
        return
    config = get_llm_runtime_config()
    if config["model_warning"]:
        logger.warning(config["model_warning"])
    logger.info(
        "[AI] provider=%s model=%s timeout=%ss max_concurrency=%s critique=%s",
        config["provider"],
        config["model"],
        config["timeout_seconds"],
        config["max_concurrency"],
        "on" if config["critique_enabled"] else "off",
    )
    _config_logged = True


def get_model():
    """Get the configured model name after provider-aware validation."""
    return get_llm_runtime_config()["model"]


def _get_semaphore():
    global _semaphore, _semaphore_size
    size = get_llm_max_concurrency()
    if _semaphore is None or _semaphore_size != size:
        _semaphore = threading.BoundedSemaphore(size)
        _semaphore_size = size
    return _semaphore


def should_skip_optional_llm():
    """Whether optional LLM embellishments should be skipped for this run."""
    return _runtime_state.snapshot()["degraded_mode"]


def get_llm_runtime_summary():
    return _runtime_state.snapshot()


def log_llm_runtime_summary():
    summary = get_llm_runtime_summary()
    if not summary["total_requests"]:
        return
    logger.info(
        "[AI] summary requests=%s attempts=%s success=%s retries=%s failures=%s "
        "rate_limits=%s avg_latency=%.2fs degraded=%s",
        summary["total_requests"],
        summary["total_attempts"],
        summary["successes"],
        summary["retries"],
        summary["final_failures"],
        summary["rate_limit_errors"],
        summary["avg_latency_seconds"],
        "yes" if summary["degraded_mode"] else "no",
    )


atexit.register(log_llm_runtime_summary)


def get_llm_client():
    """Get or create the singleton OpenAI-compatible client.

    Raises ValueError if API_KEY env var is not set.
    """
    global _client
    if _client is None:
        from openai import OpenAI

        api_key = os.environ.get("API_KEY")
        if not api_key:
            raise ValueError("API_KEY environment variable is required")

        config = get_llm_runtime_config()
        _log_runtime_config_once()
        _client = OpenAI(
            api_key=api_key,
            base_url=config["base_url"],
            timeout=config["timeout_seconds"],
            max_retries=0,
        )
    return _client


def _extract_status_code(exc):
    for attr in ("status_code", "http_status"):
        status = getattr(exc, attr, None)
        if isinstance(status, int):
            return status
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    return None


def _extract_headers(exc):
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    return headers or {}


def _extract_retry_after_seconds(exc):
    headers = _extract_headers(exc)
    retry_after = None
    if hasattr(headers, "get"):
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after is None:
        return None
    try:
        return max(0.0, float(retry_after))
    except (TypeError, ValueError):
        return None


def _classify_llm_error(exc):
    """Return metadata describing whether an exception should be retried."""
    status_code = _extract_status_code(exc)
    message = str(exc).lower()
    exc_name = exc.__class__.__name__.lower()

    if status_code == 429 or "rate limit" in message or "too many requests" in message:
        return {"retryable": True, "status_code": status_code or 429, "reason": "rate_limit"}

    if status_code in {400, 401, 403, 404}:
        return {"retryable": False, "status_code": status_code, "reason": "client_error"}

    if status_code == 422 or "unprocessable" in message or "validation" in message:
        return {"retryable": False, "status_code": status_code or 422, "reason": "invalid_request"}

    if "context length" in message or "maximum context" in message or "too many tokens" in message:
        return {"retryable": False, "status_code": status_code or 400, "reason": "context_overflow"}

    if "model" in message and ("not found" in message or "does not exist" in message):
        return {"retryable": False, "status_code": status_code or 404, "reason": "model_not_found"}

    if status_code and status_code >= 500:
        return {"retryable": True, "status_code": status_code, "reason": "server_error"}

    if any(token in exc_name for token in ("timeout", "connection", "ratelimit", "server")):
        return {"retryable": True, "status_code": status_code, "reason": exc_name}

    if any(token in message for token in ("timeout", "timed out", "connection reset", "temporarily unavailable", "gateway")):
        return {"retryable": True, "status_code": status_code, "reason": "transport_error"}

    return {"retryable": False, "status_code": status_code, "reason": exc_name or "unknown_error"}


def _compute_retry_wait(exc, attempt_index):
    retry_after = _extract_retry_after_seconds(exc)
    if retry_after is not None:
        return min(retry_after, get_llm_retry_max_seconds())

    base = get_llm_retry_base_seconds()
    max_wait = get_llm_retry_max_seconds()
    wait = min(base * (2 ** max(attempt_index - 1, 0)), max_wait)
    jitter = random.uniform(0, min(1.0, wait / 2))
    return min(wait + jitter, max_wait)


def _request_content(response):
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    return getattr(message, "content", "") or ""


def _chat_completion_request(client, prompt, max_tokens=4000, max_retries=None,
                             temperature=None, top_p=None, profile_name="summarize",
                             optional=False):
    """Call the OpenAI-compatible API and return content plus diagnostics."""
    if optional and should_skip_optional_llm():
        logger.info("[AI] skip optional profile=%s degraded=yes", profile_name)
        return {"content": None, "retries_used": 0, "skipped": True}

    config = get_llm_runtime_config()
    model = config["model"]
    attempts = max(1, max_retries if max_retries is not None else config["max_retries"])
    _temperature = temperature if temperature is not None else 0.7
    _top_p = top_p if top_p is not None else 0.9
    retries_used = 0
    _runtime_state.begin_request()

    for attempt in range(1, attempts + 1):
        _runtime_state.record_attempt()
        started = time.monotonic()
        try:
            with _get_semaphore():
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=_temperature,
                    top_p=_top_p,
                )
            latency = time.monotonic() - started
            content = _request_content(response)
            _runtime_state.record_success(latency)
            logger.info(
                "[AI] profile=%s attempt=%s/%s status=success latency=%.2fs prompt_chars=%s response_chars=%s degraded=%s",
                profile_name,
                attempt,
                attempts,
                latency,
                len(prompt or ""),
                len(content or ""),
                "yes" if should_skip_optional_llm() else "no",
            )
            return {"content": content, "retries_used": retries_used, "skipped": False}
        except Exception as exc:
            latency = time.monotonic() - started
            error = _classify_llm_error(exc)
            retryable = error["retryable"]
            status_code = error["status_code"]
            rate_limited = error["reason"] == "rate_limit"
            last_attempt = attempt >= attempts
            if retryable and not last_attempt:
                _runtime_state.record_retryable_error(rate_limited)
                retries_used += 1
                wait = _compute_retry_wait(exc, attempt)
                logger.warning(
                    "[AI] profile=%s attempt=%s/%s status=retryable_error code=%s type=%s latency=%.2fs wait=%.2fs degraded=%s err=%s",
                    profile_name,
                    attempt,
                    attempts,
                    status_code,
                    error["reason"],
                    latency,
                    wait,
                    "yes" if should_skip_optional_llm() else "no",
                    exc,
                )
                time.sleep(wait)
                continue

            _runtime_state.record_failure(retryable, rate_limited, latency)
            logger.error(
                "[AI] profile=%s attempt=%s/%s status=final_error code=%s type=%s latency=%.2fs degraded=%s err=%s",
                profile_name,
                attempt,
                attempts,
                status_code,
                error["reason"],
                latency,
                "yes" if should_skip_optional_llm() else "no",
                exc,
            )
            return {"content": None, "retries_used": retries_used, "skipped": False}

    return {"content": None, "retries_used": retries_used, "skipped": False}


def chat_completion(client, prompt, max_tokens=4000, max_retries=None,
                    temperature=None, top_p=None, profile_name="summarize",
                    optional=False):
    """Call OpenAI-compatible API with unified retry, limit, and logging policy."""
    result = _chat_completion_request(
        client,
        prompt,
        max_tokens=max_tokens,
        max_retries=max_retries,
        temperature=temperature,
        top_p=top_p,
        profile_name=profile_name,
        optional=optional,
    )
    return result["content"]


def chat_with_profile(client, prompt, profile_name, max_retries=None, optional=False):
    """Call chat_completion with task-specific parameters from TASK_PROFILES."""
    profile = TASK_PROFILES.get(profile_name, TASK_PROFILES["summarize"])
    return chat_completion(
        client,
        prompt,
        max_tokens=profile["max_tokens"],
        temperature=profile["temperature"],
        top_p=profile["top_p"],
        max_retries=max_retries,
        profile_name=profile_name,
        optional=optional,
    )


# Phrases indicating the critique found no issues (both languages)
_NO_CHANGE_PHRASES = [
    "无需修改", "核查通过", "无问题发现",
    "no changes needed", "verified", "no issues found",
    "looks good", "no revision", "no corrections",
    "quality is high", "no problems",
]


def _is_no_change_response(critique_text):
    """Check if a critique response indicates no changes are needed."""
    lower = critique_text.lower()
    return any(phrase in lower for phrase in _NO_CHANGE_PHRASES)


def generate_with_critique(client, prompt, profile_name, critique_template, language="zh"):
    """Generate content, then critique/refine only while the provider is healthy."""
    profile = TASK_PROFILES.get(profile_name, TASK_PROFILES["summarize"])

    draft_result = _chat_completion_request(
        client,
        prompt,
        max_tokens=profile["max_tokens"],
        temperature=profile["temperature"],
        top_p=profile["top_p"],
        profile_name=profile_name,
        optional=False,
    )
    draft = draft_result["content"]
    if not draft:
        return None

    draft = sanitize_generated_text(draft)
    if not draft.strip():
        return None

    if (
        os.environ.get("SKIP_CRITIQUE")
        or not critique_template
        or should_skip_optional_llm()
        or draft_result["retries_used"] > 0
    ):
        return draft

    critique_prompt = critique_template.format(draft=draft)
    critique_result = _chat_completion_request(
        client,
        critique_prompt,
        max_tokens=TASK_PROFILES["critique"]["max_tokens"],
        temperature=TASK_PROFILES["critique"]["temperature"],
        top_p=TASK_PROFILES["critique"]["top_p"],
        profile_name="critique",
        optional=True,
    )
    critique = critique_result["content"]
    if not critique or critique_result["retries_used"] > 0 or should_skip_optional_llm():
        return draft

    critique = sanitize_generated_text(critique)
    if _is_no_change_response(critique):
        return draft

    if language == "en":
        refine_prompt = f"""Refine the following report draft based on the review feedback.

## Original Draft
{draft}

## Review Feedback
{critique}

## Instructions
Address all reasonable revision suggestions from the review. Keep the good parts, fix the problematic parts.
Output only the improved version, nothing else."""
    else:
        refine_prompt = f"""基于以下审阅意见，改进这份报告草稿。

## 原始草稿
{draft}

## 审阅意见
{critique}

## 指示
处理审阅意见中所有合理的修改建议。保留草稿中好的部分，修正有问题的部分。
只输出改进后的版本，不要输出其他内容。"""

    refine_result = _chat_completion_request(
        client,
        refine_prompt,
        max_tokens=profile["max_tokens"],
        temperature=profile["temperature"],
        top_p=profile["top_p"],
        profile_name=profile_name,
        optional=True,
    )
    refined = refine_result["content"]
    if not refined:
        return draft
    return sanitize_generated_text(refined)
