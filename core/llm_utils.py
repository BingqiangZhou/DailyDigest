"""
LLM response parsing utilities.
Shared helpers for extracting structured data from LLM outputs.
"""

import json
import re


def strip_code_fences(response: str) -> str:
    """Remove markdown code fences from LLM response."""
    text = response.strip()
    # Find first ``` and extract content after it
    match = re.search(r'```(?:\w*)\n?', text)
    if match:
        start = match.end()
        # Find closing ```
        rest = text[start:]
        close = rest.rfind("```")
        if close > 0:
            text = rest[:close]
        else:
            text = rest
    return text.strip()


def parse_llm_json(response: str) -> dict:
    """Parse JSON from an LLM response, stripping code fences if present.

    Returns:
        dict: Parsed JSON object.

    Raises:
        ValueError: If the response cannot be parsed as JSON.
    """
    cleaned = strip_code_fences(response)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse LLM JSON response: {e}") from e
