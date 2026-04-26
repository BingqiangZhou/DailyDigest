"""
LLM client singleton and communication layer.
Provides a lazy-initialised OpenAI-compatible client with task-specific profiles.
"""

import os
import time

from .logging_config import get_logger

logger = get_logger("llm")

# Default model configuration
DEFAULT_MODEL = "nvidia/llama-3.1-nemotron-70b-instruct"
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Task-specific LLM parameter profiles
TASK_PROFILES = {
    "classify": {"temperature": 0.1, "top_p": 0.9, "max_tokens": 2000},
    "topic_cluster": {"temperature": 0.2, "top_p": 0.9, "max_tokens": 2000},
    "tldr": {"temperature": 0.3, "top_p": 0.9, "max_tokens": 500},
    "critique": {"temperature": 0.3, "top_p": 0.9, "max_tokens": 1000},
    "summarize": {"temperature": 0.5, "top_p": 0.9, "max_tokens": 4000},
    "deep_analysis": {"temperature": 0.7, "top_p": 0.9, "max_tokens": 6000},
    "wechat_structure": {"temperature": 0.5, "top_p": 0.9, "max_tokens": 4000},
}

# Module-level singleton
_client = None


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
        base_url = os.environ.get("BASE_URL") or DEFAULT_BASE_URL
        _client = OpenAI(api_key=api_key, base_url=base_url, timeout=120, max_retries=2)
    return _client


def get_model():
    """Get the configured model name."""
    return os.environ.get("MODEL") or DEFAULT_MODEL


def chat_completion(client, prompt, max_tokens=4000, max_retries=3,
                    temperature=None, top_p=None):
    """Call OpenAI-compatible API with retry logic."""
    model = get_model()
    _temperature = temperature if temperature is not None else 0.7
    _top_p = top_p if top_p is not None else 0.9
    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=_temperature,
                top_p=_top_p,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_error = e
            logger.warning(f"[AI] ⚠️ API 调用失败 (attempt {attempt + 1}/{max_retries}, model={model}): {e}")
            if attempt < max_retries - 1:
                wait = min(2 ** attempt * 2, 30)
                logger.warning(f"[AI]   等待 {wait} 秒后重试...")
                time.sleep(wait)
    logger.error(f"[AI] ❌ API 调用最终失败 (model={model}): {last_error}")
    return None


def chat_with_profile(client, prompt, profile_name, max_retries=3):
    """Call chat_completion with task-specific parameters from TASK_PROFILES."""
    profile = TASK_PROFILES.get(profile_name, TASK_PROFILES["summarize"])
    return chat_completion(client, prompt,
                           max_tokens=profile["max_tokens"],
                           temperature=profile["temperature"],
                           top_p=profile["top_p"],
                           max_retries=max_retries)


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
    """Generate content, then self-critique and refine (draft-critique-refine).

    Three-pass pipeline:
    1. Generate initial draft
    2. Critique the draft against quality criteria
    3. Refine based on critique

    Skipped if SKIP_CRITIQUE env var is set.

    Returns the refined output, or the draft if critique/refine fails.
    """
    # Pass 1: Draft
    draft = chat_with_profile(client, prompt, profile_name)
    if not draft:
        return None

    # Skip critique if env var is set or critique template is empty
    if os.environ.get("SKIP_CRITIQUE") or not critique_template:
        return draft

    # Pass 2: Critique
    critique_prompt = critique_template.format(draft=draft)
    critique = chat_with_profile(client, critique_prompt, "critique")
    if not critique:
        return draft

    # If critique says no changes needed, return draft as-is
    if _is_no_change_response(critique):
        return draft

    # Pass 3: Refine based on critique
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

    refined = chat_with_profile(client, refine_prompt, profile_name)
    return refined if refined else draft
