"""
LLM response parsing utilities.
Shared helpers for extracting structured data from LLM outputs.
"""

import json
import re

# Patterns indicating model chain-of-thought leaked into output.
# Some models (e.g. minimax-m2.7) include their reasoning process.
_THINKING_LINE_PATTERNS = [
    r'^The user (wants?|said|gave|provided)',
    r'^We need to ',
    r'^I need to ',
    r'^I\'ll ',
    r'^Let me ',
    r'^Let\'s (count|draft|calculate|target|aim)',
    r'^Now (count|let\'s|we)',
    r'^Count (characters|each|approximate)',
    r'^Draft:',
    r'^First sentence:',
    r'^Second sentence:',
    r'^Third sentence:',
    r'^Paragraph:',
    r'^We must not ',
    r'^Must (keep|avoid|be|follow)',
    r'^The (requirement|instruction|rule) is',
    r'^So we need',
    r'^That\'s? (about|the|covers)',
    r'^\d+\. (First|Second|Third) sentence',
    r'^"[^"]*"\d+\s*"[^"]*"\d+',  # Character counting like "研"1 "究"2
]


def _strip_thinking_output(text: str) -> str:
    """Remove leaked chain-of-thought reasoning from model output.

    Some models include their internal reasoning process (planning,
    counting, drafting) in the response text. This strips those lines
    while preserving the actual content.
    """
    # Strip /* ... */ blocks (common in minimax models)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)

    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            continue
        # Check against known thinking patterns
        is_thinking = False
        for pattern in _THINKING_LINE_PATTERNS:
            if re.match(pattern, stripped, re.IGNORECASE):
                is_thinking = True
                break
        if not is_thinking:
            cleaned.append(line)

    result = '\n'.join(cleaned)
    # Collapse multiple blank lines
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def strip_code_fences(response: str) -> str:
    """Remove markdown code fences and leaked thinking from LLM response."""
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
    text = text.strip()
    text = _strip_thinking_output(text)
    return text


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
