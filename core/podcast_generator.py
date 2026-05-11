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


def generate_dialogue_script(client, content: str, report_type: str) -> list[dict]:
    """Call LLM to generate a two-person dialogue script from report content.

    Returns a list of {"speaker": "A"|"B", "text": "..."} dicts.
    Returns empty list on failure.
    """
    from .llm_utils import parse_llm_json

    type_label = "科技日报" if report_type == "tech" else "播客日报"

    from config.prompts.podcast_script import PODCAST_SCRIPT_PROMPT_ZH
    prompt = PODCAST_SCRIPT_PROMPT_ZH.format(
        report_type=type_label,
        report_content=content,
    )

    try:
        from .llm import chat_with_profile
        response = chat_with_profile(client, prompt, profile_name="narrative")
    except Exception as e:
        logger.warning("LLM script generation failed: %s", e)
        return []

    if not response:
        logger.warning("LLM returned empty response for podcast script")
        return []

    try:
        parsed = parse_llm_json(response)
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning("Failed to parse dialogue script JSON: %s", e)
        return []

    if not isinstance(parsed, list):
        logger.warning("Expected JSON array, got %s", type(parsed).__name__)
        return []

    # Validate and filter entries
    valid = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        speaker = entry.get("speaker", "")
        text = entry.get("text", "")
        if speaker in ("A", "B") and text.strip():
            valid.append({"speaker": speaker, "text": text.strip()})

    if len(valid) > MAX_SCRIPT_LINES:
        valid = valid[:MAX_SCRIPT_LINES]

    return valid


def synthesize_audio(script: list[dict]) -> bytes | None:
    """Synthesize dialogue script into a single MP3 using edge-tts and pydub.

    Returns MP3 bytes, or None on failure.
    """
    if not script:
        return None

    try:
        import edge_tts
        from pydub import AudioSegment
    except ImportError as e:
        logger.warning("Missing dependency for audio synthesis: %s", e)
        return None

    segments: list = []
    pause = AudioSegment.silent(duration=PAUSE_MS)

    async def _synthesize_line(speaker: str, text: str):
        voice = VOICES.get(speaker)
        if not voice:
            return None
        communicate = edge_tts.Communicate(text, voice)
        buf = io.BytesIO()
        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            buf.seek(0)
            return AudioSegment.from_mp3(buf)
        except Exception as e:
            logger.warning("TTS synthesis failed for '%s...': %s", text[:30], e)
            return None

    async def _synthesize_all():
        for i, entry in enumerate(script):
            seg = await _synthesize_line(entry["speaker"], entry["text"])
            if seg is None:
                # Retry once
                logger.warning("Retrying TTS line %d", i)
                seg = await _synthesize_line(entry["speaker"], entry["text"])
            if seg is not None:
                if segments:
                    segments.append(pause)
                segments.append(seg)

    try:
        asyncio.run(_synthesize_all())
    except Exception as e:
        logger.warning("Audio synthesis failed: %s", e)
        return None

    if not segments:
        return None

    combined = segments[0]
    for seg in segments[1:]:
        combined += seg

    buf = io.BytesIO()
    combined.export(buf, format="mp3", bitrate=MP3_BITRATE)
    return buf.getvalue()
