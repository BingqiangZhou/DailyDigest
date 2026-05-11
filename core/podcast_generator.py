"""Podcast audio generation — converts Markdown reports to two-person conversational MP3."""

import asyncio
import io
import json
import logging
import re
from pathlib import Path

from .logging_config import get_logger

logger = get_logger("podcast_generator")

# edge-tts voice assignments
VOICES = {
    "A": "zh-CN-YunxiNeural",
    "B": "zh-CN-XiaoxiaoNeural",
}

# Audio settings
PAUSE_MS = 300       # milliseconds of silence between dialogue lines
MP3_BITRATE = "64k"  # bitrate for spoken audio

# Content limits
MAX_CONTENT_CHARS = 4000  # max chars sent to LLM
MAX_SCRIPT_LINES = 60


def extract_report_content(markdown: str, report_type: str) -> str:
    """Extract key content from a Markdown report for podcast script generation.

    Returns a plain-text summary suitable for LLM consumption.
    """
    if not markdown.strip():
        return ""

    type_label = "科技日报" if report_type == "tech" else "播客日报"
    sections = [f"{type_label}内容摘要："]

    # Extract key points (📌 今日要点 / 今日要点)
    key_points = _extract_section(markdown, r"^##\s+📌?\s*今日要点")
    if key_points:
        sections.append("【今日要点】")
        sections.append(key_points)

    # Extract theme summaries (🧭 今日动态 sections with ###)
    themes = _extract_themes(markdown)
    if themes:
        sections.append("【主题概要】")
        sections.append(themes)

    # Extract notable items (值得关注 / 值得关注的单集)
    notable = _extract_section(markdown, r"^##\s+👀?\s*值得关")
    if notable:
        sections.append("【值得关注】")
        sections.append(notable)

    result = "\n".join(sections)
    if len(result) > MAX_CONTENT_CHARS:
        result = result[:MAX_CONTENT_CHARS] + "\n...(内容已截断)"
    return result


def _extract_section(md: str, heading_pattern: str) -> str:
    """Extract content under a heading until the next ## heading or ---."""
    match = re.search(heading_pattern, md, re.MULTILINE)
    if not match:
        return ""
    start = match.end()
    # Find next ## heading or --- separator
    rest = md[start:]
    end_match = re.search(r"\n(?:##|---)", rest)
    if end_match:
        content = rest[:end_match.start()]
    else:
        content = rest
    # Clean up: strip markdown links, keep text
    content = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", content)
    content = re.sub(r"^[•\-\*]\s*", "- ", content, flags=re.MULTILINE)
    return content.strip()


def _extract_themes(md: str) -> str:
    """Extract ### theme headings and their summary paragraphs."""
    # Find the 今日动态 section
    dynamic_match = re.search(r"^##\s+🧭\s*今日动态", md, re.MULTILINE)
    if not dynamic_match:
        return ""
    rest = md[dynamic_match.end():]
    # Stop at next ## heading that is not ###
    end_match = re.search(r"\n##\s+(?!#)", rest)
    if end_match:
        rest = rest[:end_match.start()]

    themes = []
    for m in re.finditer(
        r"###\s+[一二三四五六七八九十]+[、．.]\s*(.+?)(?:\s+🔥?\s*)?\(\d+\s*篇.*?\)\n\n(.+?)(?=\n\n>|\n\n###|\Z)",
        rest, re.DOTALL
    ):
        title = m.group(1).strip()
        summary = m.group(2).strip()
        # Clean markdown links from summary
        summary = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", summary)
        # Truncate long summaries
        if len(summary) > 200:
            summary = summary[:200] + "..."
        themes.append(f"- {title}：{summary}")

    return "\n".join(themes)
