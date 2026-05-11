# 播客音频生成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two-person conversational podcast MP3 generation to both tech and podcast daily reports.

**Architecture:** After Markdown report generation, extract key content (TL;DR + themes), call LLM to generate a two-person dialogue script (JSON), then use edge-tts to synthesize audio per speaker and pydub to stitch segments into a single MP3 file.

**Tech Stack:** edge-tts (TTS), pydub (audio stitching, depends on ffmpeg), OpenAI-compatible LLM API (dialogue script generation)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `config/prompts/podcast_script.py` | Prompt template for LLM dialogue script generation |
| `core/podcast_generator.py` | Content extraction, LLM script generation, TTS synthesis, audio assembly |
| `tests/test_podcast_generator.py` | Tests for content extraction, script parsing, and audio generation |
| `pyproject.toml` | Add `edge-tts`, `pydub` dependencies |
| `main.py` | Add `--podcast-only` CLI argument |
| `config/prompts/__init__.py` | Re-export new prompt constant |

---

### Task 1: Add dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add edge-tts and pydub to dependencies**

In `pyproject.toml`, add `edge-tts` and `pydub` to the `dependencies` list:

```toml
dependencies = [
    "feedparser>=6.0.10",
    "openai>=1.40.0",
    "python-dotenv>=1.0.1",
    "beautifulsoup4>=4.12.3",
    "scikit-learn>=1.3.0",
    "edge-tts>=6.1.0",
    "pydub>=0.25.1",
]
```

- [ ] **Step 2: Install new dependencies**

Run: `uv sync`
Expected: Dependencies installed successfully.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add edge-tts and pydub dependencies for podcast audio generation"
```

---

### Task 2: Create podcast script prompt template

**Files:**
- Create: `config/prompts/podcast_script.py`
- Modify: `config/prompts/__init__.py`

- [ ] **Step 1: Write the prompt template**

Create `config/prompts/podcast_script.py`:

```python
"""Prompt template for generating two-person podcast dialogue scripts."""

PODCAST_SCRIPT_PROMPT_ZH = """你是一位经验丰富的播客制作人，擅长把枯燥的新闻摘要变成生动有趣的对话。

## 任务

根据以下{report_type}的摘要内容，生成一段双人对话脚本。两位主持人以轻松闲聊的方式讨论今天的重要内容。

## 角色设定

- 主持人A（小云）：男性，活泼开朗，喜欢用生动的比喻，善于提问和引导话题
- 主持人B（小晓）：女性，理性温和，擅长补充背景知识和深度分析

## 日报内容

{report_content}

## 要求

1. 风格轻松自然，像朋友聊天，口语化表达
2. 总字数控制在2000-3500字（对应5-10分钟音频）
3. 包含开场白、各主题讨论、结束语
4. 提炼要点并加入观点，不要逐条念新闻标题
5. 纯文字，不含markdown格式、括号注释、舞台指示或表情符号
6. 两位主持人交替发言，每人每次发言不超过200字

## 输出格式

严格只输出一个JSON数组，不要输出Markdown、解释、代码块或额外文字：
[{{"speaker": "A", "text": "..."}}, {{"speaker": "B", "text": "..."}}]

speaker 只能是 "A" 或 "B"。数组长度不超过60个元素。"""
```

- [ ] **Step 2: Add re-export in `config/prompts/__init__.py`**

Append this line at the end of `config/prompts/__init__.py`:

```python
from .podcast_script import PODCAST_SCRIPT_PROMPT_ZH
```

- [ ] **Step 3: Verify import works**

Run: `uv run python -c "from config.prompts.podcast_script import PODCAST_SCRIPT_PROMPT_ZH; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add config/prompts/podcast_script.py config/prompts/__init__.py
git commit -m "feat: add podcast dialogue script prompt template"
```

---

### Task 3: Create podcast_generator.py — content extraction

**Files:**
- Create: `core/podcast_generator.py`
- Create: `tests/test_podcast_generator.py`

- [ ] **Step 1: Write failing tests for content extraction**

Create `tests/test_podcast_generator.py`:

```python
"""Tests for podcast audio generation."""

import pytest
from core.podcast_generator import extract_report_content


class TestExtractReportContent:
    """Tests for extracting key content from Markdown reports."""

    def test_extracts_tldr(self):
        md = (
            "# 📰 DailyDigest — 2026-04-29\n\n"
            "> 扫描 554 篇\n\n---\n\n"
            "## 📌 今日要点\n\n"
            "- GPT-5.5 is OpenAI's most capable\n"
            "- OpenAI lands on AWS\n\n---\n\n"
            "## 📝 科技简讯\n\n- item1\n"
        )
        result = extract_report_content(md, "tech")
        assert "GPT-5.5 is OpenAI's most capable" in result

    def test_extracts_theme_summaries(self):
        md = (
            "# 📰 DailyDigest — 2026-04-29\n\n"
            "> scan info\n\n---\n\n"
            "## 🧭 今日动态\n\n"
            "### 一、AI巨头生态重塑 🔥  (88 篇)\n\n"
            "本周AI领域迎来深度生态重塑。核心动态有三。\n\n"
            "---\n\n"
            "### 二、安全危机 🔥  (55 篇)\n\n"
            "安全领域重大突破。\n\n"
            "---\n"
        )
        result = extract_report_content(md, "tech")
        assert "AI巨头生态重塑" in result
        assert "安全危机" in result

    def test_truncates_long_content(self):
        md = "## 📌 今日要点\n\n" + "\n".join(f"- item {i}" for i in range(500))
        result = extract_report_content(md, "tech")
        assert len(result) <= 4500

    def test_podcast_report_extracts_highlights(self):
        md = (
            "# 🎙️ AI 播客日报 — 2026-04-29\n\n"
            "> scan info\n\n---\n\n"
            "## 今日要点\n\n"
            "- 任鑫：AI 转型没戏\n"
            "- 数据标注员的困境\n\n"
            "---\n\n"
            "## 值得关注的单集\n\n"
            "- 🎧 episode1\n"
            "- 🎧 episode2\n"
        )
        result = extract_report_content(md, "podcast")
        assert "任鑫：AI 转型没戏" in result
        assert "数据标注员的困境" in result

    def test_empty_report(self):
        result = extract_report_content("", "tech")
        assert result == ""

    def test_report_type_label(self):
        md = "## 📌 今日要点\n\n- test item"
        result = extract_report_content(md, "tech")
        assert result.startswith("科技日报")
        result2 = extract_report_content(md, "podcast")
        assert result2.startswith("播客日报")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_podcast_generator.py::TestExtractReportContent -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `extract_report_content`**

Create `core/podcast_generator.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_podcast_generator.py::TestExtractReportContent -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/podcast_generator.py tests/test_podcast_generator.py
git commit -m "feat: add report content extraction for podcast generation"
```

---

### Task 4: Add LLM dialogue script generation

**Files:**
- Modify: `core/podcast_generator.py`
- Modify: `tests/test_podcast_generator.py`

- [ ] **Step 1: Write failing tests for script generation**

Add to `tests/test_podcast_generator.py`:

```python
import json
from unittest.mock import MagicMock, patch


class TestGenerateDialogueScript:
    """Tests for LLM-based dialogue script generation."""

    def test_parses_valid_json_response(self):
        from core.podcast_generator import generate_dialogue_script

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='[{"speaker": "A", "text": "大家好"}, {"speaker": "B", "text": "欢迎收听"}]'
            ))]
        )
        result = generate_dialogue_script(mock_client, "科技日报摘要", "tech")
        assert len(result) == 2
        assert result[0]["speaker"] == "A"
        assert result[0]["text"] == "大家好"

    def test_handles_code_fence_wrapping(self):
        from core.podcast_generator import generate_dialogue_script

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='```json\n[{"speaker": "A", "text": "hello"}]\n```'
            ))]
        )
        result = generate_dialogue_script(mock_client, "test content", "tech")
        assert len(result) == 1
        assert result[0]["speaker"] == "A"

    def test_returns_empty_on_llm_failure(self):
        from core.podcast_generator import generate_dialogue_script

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")
        result = generate_dialogue_script(mock_client, "test", "tech")
        assert result == []

    def test_validates_speaker_values(self):
        from core.podcast_generator import generate_dialogue_script

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='[{"speaker": "A", "text": "ok"}, {"speaker": "C", "text": "bad"}, {"speaker": "B", "text": "good"}]'
            ))]
        )
        result = generate_dialogue_script(mock_client, "test", "tech")
        # Speaker "C" should be filtered out
        speakers = [d["speaker"] for d in result]
        assert "C" not in speakers
        assert len(result) == 2

    def test_truncates_long_script(self):
        from core.podcast_generator import generate_dialogue_script

        mock_client = MagicMock()
        lines = [{"speaker": "A", "text": f"line {i}"} for i in range(100)]
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content=json.dumps(lines)
            ))]
        )
        result = generate_dialogue_script(mock_client, "test", "tech")
        assert len(result) <= 60
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_podcast_generator.py::TestGenerateDialogueScript -v`
Expected: FAIL (function not defined)

- [ ] **Step 3: Implement `generate_dialogue_script`**

Add to `core/podcast_generator.py`:

```python
def generate_dialogue_script(client, content: str, report_type: str) -> list[dict]:
    """Call LLM to generate a two-person dialogue script from report content.

    Returns a list of {"speaker": "A"|"B", "text": "..."} dicts.
    Returns empty list on failure.
    """
    from .llm_utils import parse_llm_json

    type_label = "科技日报" if report_type == "tech" else "播客日报"

    # Import prompt directly to avoid circular imports
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_podcast_generator.py::TestGenerateDialogueScript -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/podcast_generator.py tests/test_podcast_generator.py
git commit -m "feat: add LLM dialogue script generation for podcast"
```

---

### Task 5: Add TTS synthesis and audio assembly

**Files:**
- Modify: `core/podcast_generator.py`
- Modify: `tests/test_podcast_generator.py`

- [ ] **Step 1: Write failing tests for audio synthesis**

Add to `tests/test_podcast_generator.py`:

```python
class TestSynthesizeAudio:
    """Tests for TTS synthesis and audio assembly."""

    def test_synthesize_dialogue_returns_bytes(self):
        from core.podcast_generator import synthesize_audio

        script = [
            {"speaker": "A", "text": "大家好，欢迎收听今天的节目。"},
            {"speaker": "B", "text": "今天我们聊聊最新的AI动态。"},
        ]
        result = synthesize_audio(script)
        assert result is not None
        assert len(result) > 0
        # MP3 files start with ID3 or 0xff sync byte
        assert result[:3] == b"ID3" or (result[0] & 0xFF) == 0xFF

    def test_synthesize_empty_script_returns_none(self):
        from core.podcast_generator import synthesize_audio

        result = synthesize_audio([])
        assert result is None

    def test_synthesize_single_speaker(self):
        from core.podcast_generator import synthesize_audio

        script = [{"speaker": "A", "text": "这是一段测试音频。"}]
        result = synthesize_audio(script)
        assert result is not None
        assert len(result) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_podcast_generator.py::TestSynthesizeAudio -v`
Expected: FAIL (function not defined)

- [ ] **Step 3: Implement `synthesize_audio`**

Add to `core/podcast_generator.py`:

```python
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

    segments = []
    pause = AudioSegment.silent(duration=PAUSE_MS)

    async def _synthesize_line(speaker: str, text: str) -> AudioSegment | None:
        voice = VOICES.get(speaker)
        if not voice:
            return None
        communicate = edge_tts.Communicate(text, voice)
        buf = io.BytesIO()
        try:
            await communicate.save(buf)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_podcast_generator.py::TestSynthesizeAudio -v`
Expected: All PASS (requires network for edge-tts)

- [ ] **Step 5: Commit**

```bash
git add core/podcast_generator.py tests/test_podcast_generator.py
git commit -m "feat: add TTS synthesis and audio assembly for podcast"
```

---

### Task 6: Add top-level `generate_podcast_audio` orchestrator

**Files:**
- Modify: `core/podcast_generator.py`
- Modify: `tests/test_podcast_generator.py`

- [ ] **Step 1: Write failing test for the orchestrator**

Add to `tests/test_podcast_generator.py`:

```python
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestGeneratePodcastAudio:
    """Tests for the top-level generate_podcast_audio orchestrator."""

    def test_generates_mp3_from_report_file(self, tmp_path):
        from core.podcast_generator import generate_podcast_audio

        # Create a fake report file
        report = tmp_path / "2026-04-29.md"
        report.write_text(
            "# 📰 DailyDigest — 2026-04-29\n\n"
            "> scan info\n\n---\n\n"
            "## 📌 今日要点\n\n"
            "- GPT-5.5 is great\n"
            "- OpenAI on AWS\n\n---\n"
        )

        output_dir = tmp_path / "audio"
        output_dir.mkdir()

        with patch("core.podcast_generator.generate_dialogue_script") as mock_script, \
             patch("core.podcast_generator.synthesize_audio") as mock_audio:
            mock_script.return_value = [
                {"speaker": "A", "text": "大家好"},
                {"speaker": "B", "text": "欢迎收听"},
            ]
            mock_audio.return_value = b"fake_mp3_data"

            result = generate_podcast_audio(
                str(report), "tech", "2026-04-29", output_dir=str(output_dir)
            )

        assert result is not None
        assert Path(result).exists()
        assert Path(result).name == "2026-04-29_tech.mp3"

    def test_returns_none_on_missing_report(self):
        from core.podcast_generator import generate_podcast_audio

        result = generate_podcast_audio(
            "/nonexistent/report.md", "tech", "2026-04-29"
        )
        assert result is None

    def test_returns_none_on_empty_script(self, tmp_path):
        from core.podcast_generator import generate_podcast_audio

        report = tmp_path / "2026-04-29.md"
        report.write_text("# 📰 DailyDigest\n\n## 📌 今日要点\n\n- item\n")

        with patch("core.podcast_generator.generate_dialogue_script") as mock_script:
            mock_script.return_value = []
            result = generate_podcast_audio(
                str(report), "tech", "2026-04-29", output_dir=str(tmp_path)
            )

        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_podcast_generator.py::TestGeneratePodcastAudio -v`
Expected: FAIL (function signature mismatch or not defined)

- [ ] **Step 3: Implement `generate_podcast_audio`**

Add to `core/podcast_generator.py`:

```python
def generate_podcast_audio(
    report_path: str,
    report_type: str,
    date: str,
    output_dir: str | None = None,
) -> str | None:
    """Generate a podcast MP3 from a Markdown report.

    Args:
        report_path: Path to the Markdown report file.
        report_type: "tech" or "podcast".
        date: Date string in YYYY-MM-DD format.
        output_dir: Override output directory. Defaults to daily-digests/podcast_audio/.

    Returns:
        Path to the generated MP3 file, or None on failure.
    """
    try:
        report_file = Path(report_path)
        if not report_file.exists():
            logger.warning("Report file not found: %s", report_path)
            return None

        markdown = report_file.read_text(encoding="utf-8")
        content = extract_report_content(markdown, report_type)
        if not content:
            logger.warning("No content extracted from %s", report_path)
            return None

        from .llm import get_llm_client
        client = get_llm_client()

        script = generate_dialogue_script(client, content, report_type)
        if not script:
            logger.warning("Failed to generate dialogue script for %s", report_path)
            return None

        audio_bytes = synthesize_audio(script)
        if not audio_bytes:
            logger.warning("Failed to synthesize audio for %s", report_path)
            return None

        # Determine output path
        if output_dir:
            out_dir = Path(output_dir)
        else:
            from .config import OUTPUT_DIR
            out_dir = OUTPUT_DIR / "podcast_audio"
        out_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{date}_{report_type}.mp3"
        out_path = out_dir / filename
        out_path.write_bytes(audio_bytes)

        logger.info("Podcast audio saved: %s (%d bytes)", out_path, len(audio_bytes))
        return str(out_path)

    except Exception as e:
        logger.warning("Podcast generation failed for %s: %s", report_path, e)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_podcast_generator.py::TestGeneratePodcastAudio -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/podcast_generator.py tests/test_podcast_generator.py
git commit -m "feat: add generate_podcast_audio orchestrator"
```

---

### Task 7: Integrate into pipeline

**Files:**
- Modify: `core/pipeline.py`

- [ ] **Step 1: Add podcast generation to `run_tech_unified`**

In `core/pipeline.py`, at the end of `run_tech_unified()` (just before the final `return` on line 626), add:

```python
    # Generate podcast audio (API mode only)
    if api_key:
        try:
            from .podcast_generator import generate_podcast_audio
            from .config import TECH_OUTPUT_DIR
            report_file = TECH_OUTPUT_DIR / f"{date_str}.md"
            date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            mp3_path = generate_podcast_audio(str(report_file), "tech", date_str)
            if mp3_path:
                logger.info("🎙️ Tech podcast audio: %s", mp3_path)
        except Exception as e:
            logger.warning("Tech podcast generation failed: %s", e)
```

Note: The `date_str` variable should reuse the one already computed in the function. Check how the date string is constructed in the existing code (it may use `now` or a different variable). Use that same variable.

- [ ] **Step 2: Add podcast generation to `run_podcast`**

At the end of `run_podcast()` (just before the final `return` on line 751), add the same pattern:

```python
    # Generate podcast audio (API mode only)
    if api_key:
        try:
            from .podcast_generator import generate_podcast_audio
            from .config import PODCAST_OUTPUT_DIR
            report_file = PODCAST_OUTPUT_DIR / f"{date_str}.md"
            date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            mp3_path = generate_podcast_audio(str(report_file), "podcast", date_str)
            if mp3_path:
                logger.info("🎙️ Podcast report audio: %s", mp3_path)
        except Exception as e:
            logger.warning("Podcast report audio generation failed: %s", e)
```

Same note about `date_str` — reuse whatever variable is already available in the function scope.

- [ ] **Step 3: Verify imports work**

Run: `uv run python -c "from core.pipeline import run_tech_unified, run_podcast; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add core/pipeline.py
git commit -m "feat: integrate podcast audio generation into report pipelines"
```

---

### Task 8: Add `--podcast-only` CLI argument

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add `--podcast-only` argument to argparse**

In `main.py`, after the `--limit` argument (around line 82), add:

```python
    parser.add_argument("--podcast-only", choices=["tech", "podcast", "all"],
                        default=None,
                        help="generate podcast audio from existing reports (skip pipeline)")
```

- [ ] **Step 2: Handle `--podcast-only` in main()**

In `main.py`, just after the `--finalize` handling block (after `return` on line 94), add:

```python
    # --podcast-only: generate audio from existing reports
    if args.podcast_only:
        print("\n" + "=" * 60)
        print(f"\U0001f3a4 Podcast Audio Generation")
        print(f"⏰ {start_time.strftime('%Y-%m-%d %H:%M UTC')} | source: {args.podcast_only}")
        print("=" * 60)

        from core.podcast_generator import generate_podcast_audio
        from core.config import TECH_OUTPUT_DIR, PODCAST_OUTPUT_DIR
        date_str = start_time.strftime('%Y-%m-%d')

        sources = ["tech", "podcast"] if args.podcast_only == "all" else [args.podcast_only]
        for src in sources:
            if src == "tech":
                report_file = TECH_OUTPUT_DIR / f"{date_str}.md"
            else:
                report_file = PODCAST_OUTPUT_DIR / f"{date_str}.md"

            if not report_file.exists():
                print(f"  ⚠️ {src} report not found: {report_file}")
                continue

            result = generate_podcast_audio(str(report_file), src, date_str)
            if result:
                print(f"  ✅ {src}: {result}")
            else:
                print(f"  ❌ {src}: generation failed")

        return
```

- [ ] **Step 3: Verify CLI help**

Run: `uv run python main.py --help`
Expected: Help text includes `--podcast-only` option.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add --podcast-only CLI argument for standalone podcast generation"
```

---

### Task 9: End-to-end smoke test

**Files:**
- No file changes

- [ ] **Step 1: Verify existing tests still pass**

Run: `uv run pytest tests/ -v --timeout=60`
Expected: All existing tests PASS.

- [ ] **Step 2: Verify new tests pass**

Run: `uv run pytest tests/test_podcast_generator.py -v`
Expected: All PASS.

- [ ] **Step 3: Test --podcast-only with a real report**

Pick the most recent tech report and run:

```bash
uv run python main.py --podcast-only tech
```

Expected: MP3 file generated at `daily-digests/podcast_audio/YYYY-MM-DD_tech.mp3`.

- [ ] **Step 4: Verify MP3 file is valid**

```bash
ls -la daily-digests/podcast_audio/
```

Expected: MP3 file exists, size 2-5 MB for a full report.

- [ ] **Step 5: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: address issues found during smoke test"
```
