"""
LLM response parsing utilities.
Shared helpers for extracting structured data from LLM outputs.
"""

import json
import re


_REASONING_MARKERS = [
    "<think>", "</think>",
    "the user wants", "the user said", "the user gave",
    "the user is", "the user has",
    "we need to", "i need to", "let me ", "let's ",
    "we must ", "must keep", "must avoid",
    "continue with", "add mention",
    "first sentence:", "second sentence:", "third sentence:",
    "paragraph:", "draft:",
    "that's about", "that covers",
    "potential top items",
    "we'll write each character",
    "count characters",
]


def _looks_like_reasoning_line(line: str) -> bool:
    """Heuristic check for leaked model reasoning lines."""
    s = line.strip()
    if not s:
        return False

    lower = s.lower()
    if any(marker in lower for marker in _REASONING_MARKERS):
        return True
    if re.search(r'"\S+"\d', s) and s.count('"') >= 2:
        return True
    if re.match(r'^\d+\.\s+(If|Keep|Don\'t|No|Must|Retain|The|We|Avoid)\b', s):
        return True
    if re.match(r'^(Step|Rule)\s+\d+[:.]', s, re.IGNORECASE):
        return True
    if re.match(r'^(Potential|Continue|Add)\b', s, re.IGNORECASE):
        return True
    return False


def _strip_thinking_output(text: str) -> str:
    """Remove leaked chain-of-thought reasoning from model output.

    Some models (e.g. minimax-m2.7) include their internal reasoning
    process in the response. This function detects and strips it.

    Strategy: find the longest contiguous block of Chinese-dominant text,
    which is the actual narrative output. Model thinking is almost always
    in English (meta-commentary about the task, rule listings, character
    counting, etc.).
    """
    # Strip paired reasoning blocks first.
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Strip /* ... */ blocks
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)

    lines = text.split('\n')

    # Score each line: higher = more likely actual content
    def _content_score(line):
        s = line.strip()
        if not s:
            return -1  # blank
        # Count Chinese chars
        cjk = sum(1 for c in s if '一' <= c <= '鿿')
        total = len(s)
        if total == 0:
            return -1
        cjk_ratio = cjk / total

        # Strongly Chinese lines are content
        if cjk >= 15 and cjk_ratio > 0.4:
            return 10
        # Moderate Chinese
        if cjk >= 5 and cjk_ratio > 0.3:
            return 5
        # Lines with list markers and Chinese
        if re.match(r'^[-*]\s+', s) and cjk >= 3:
            return 8

        # Known thinking patterns — negative score
        if _looks_like_reasoning_line(s):
            return -10

        # Character counting patterns like "研"1 "究"2
        if re.search(r'"\S"\d', s) and s.count('"') >= 4:
            return -10
        # Numbered rule lists (e.g. "3. If multiple sources...")
        if re.match(r'^\d+\.\s+(If|Keep|Don\'t|No|Must|Retain|The|We|Avoid)', s):
            return -10

        # Pure English short lines are likely thinking
        if cjk == 0 and total < 100:
            return -5

        return 0

    scores = [_content_score(line) for line in lines]

    # Find the best contiguous block of content lines
    # A "block" starts when score >= 0 and ends at a negative score
    best_start = 0
    best_end = 0
    best_total = 0
    current_start = None
    current_total = 0

    for i, score in enumerate(scores):
        if score >= 0:
            if current_start is None:
                current_start = i
            current_total += score
        else:
            if current_start is not None and current_total > best_total:
                best_total = current_total
                best_start = current_start
                best_end = i
            current_start = None
            current_total = 0

    # Handle block extending to end
    if current_start is not None and current_total > best_total:
        best_total = current_total
        best_start = current_start
        best_end = len(lines)

    # If no good block found, return original
    if best_total <= 0:
        return text.strip()

    result = '\n'.join(lines[best_start:best_end])
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


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
    cleaned = _strip_thinking_output(cleaned)
    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Fallback: extract first JSON object or array from the text
    for start_char, end_char in [('[', ']'), ('{', '}')]:
        start = cleaned.find(start_char)
        if start >= 0:
            end = cleaned.rfind(end_char)
            if end > start:
                try:
                    return json.loads(cleaned[start:end + 1])
                except json.JSONDecodeError:
                    pass
    raise ValueError(f"Failed to parse LLM JSON response from: {cleaned[:200]}")


def sanitize_generated_text(text: str) -> str:
    """Normalize LLM output before rendering or persistence."""
    return _strip_thinking_output(strip_code_fences(text)).strip()


def contains_reasoning_artifacts(text: str) -> bool:
    """Check whether a string still appears to contain leaked reasoning."""
    lower = text.lower()
    if "<think>" in lower or "</think>" in lower:
        return True
    return any(marker in lower for marker in _REASONING_MARKERS if marker not in {"</think>"})


def sanitize_report_markdown(content: str) -> str:
    """Remove leaked reasoning lines from a rendered markdown report."""
    cleaned = content.replace("<think>", "").replace("</think>", "")
    lines = []
    for line in cleaned.splitlines():
        if _looks_like_reasoning_line(line):
            continue
        lines.append(line)

    cleaned = "\n".join(lines)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    cleaned = re.sub(r'(\n---\n\s*){2,}', '\n---\n', cleaned)
    return cleaned.strip() + ("\n" if cleaned.strip() else "")
