"""
Narrative renderer for DailyDigest.

Generates LLM-driven content for headline stories, noteworthy summaries,
and trend analysis. Falls back to template text on LLM failure.
"""

import json
import os
import re

from .logging_config import get_logger
from .llm import get_llm_client, chat_with_profile, should_skip_optional_llm
from .llm_utils import (
    parse_llm_json,
    sanitize_generated_text,
)

logger = get_logger("narrative_renderer")


class NarrativeRenderer:
    """LLM-powered narrative generator for reports."""

    def __init__(self, language=None):
        self.client = get_llm_client()
        self.language = language or os.environ.get("REPORT_LANGUAGE", "zh")
        self._success = 0
        self._failure = 0

    def render_briefing(self, briefing_data) -> dict:
        """Generate batched highlights, theme summaries, and trends for briefing_data."""
        themes = briefing_data.get("themes", [])
        if not themes or should_skip_optional_llm():
            return {}

        results = {
            "highlights": self._generate_briefing_highlights(themes),
            "theme_summaries": self._generate_theme_summaries(themes),
        }
        trends = self._generate_briefing_trends(themes, briefing_data.get("brief_items", []))
        if trends:
            results["trends"] = trends
        return results

    def _generate_briefing_highlights(self, themes) -> list[str]:
        lines = []
        for idx, theme in enumerate(themes[:6], 1):
            refs = []
            for article in theme.get("articles", [])[:3]:
                heat = f", HN {article.hn_points}" if article.hn_points else ""
                refs.append(f"- {article.title} ({article.source}{heat})")
            lines.append(f"## Theme {idx}: {theme.get('title', '')}")
            lines.extend(refs)
            lines.append("")

        if self.language == "zh":
            prompt = (
                "你是一位科技日报编辑。基于下面的主题材料，输出 4-6 条“今日要点”。\n"
                "要求：每条一行，以 '- ' 开头；只写事实，不写分析过程；不要输出 JSON；"
                "不要出现 <think>、规则说明、字符计数。\n\n"
                + "\n".join(lines)
            )
        else:
            prompt = (
                "Write 4-6 one-line daily highlights from the material below.\n"
                "Return only bullet lines starting with '- '. Do not explain your process.\n\n"
                + "\n".join(lines)
            )

        try:
            response = chat_with_profile(self.client, prompt, "summarize", optional=True)
            cleaned = sanitize_generated_text(response or "")
            return [line.lstrip("- ").strip() for line in cleaned.splitlines() if line.strip().startswith("- ")]
        except Exception as e:
            logger.warning(f"  ⚠️ Briefing highlights: failed ({e})")
            return []

    def _generate_theme_summaries(self, themes) -> dict[str, str]:
        payload = []
        for idx, theme in enumerate(themes[:8], 1):
            refs = []
            for article in theme.get("articles", [])[:4]:
                content = re.sub(r'<[^>]+>', '', (article.description or article.full_text or "")).strip()
                refs.append({
                    "title": article.title,
                    "source": article.source,
                    "hn_points": article.hn_points,
                    "summary": content[:220],
                })
            payload.append({
                "index": idx,
                "theme_id": theme.get("id"),
                "title": theme.get("title", ""),
                "refs": refs,
            })

        if self.language == "zh":
            prompt = (
                "你是一位 AI 技术日报编辑。请基于以下主题材料，为每个主题写一段 120-220 字的简报综述。"
                "输出 JSON 数组，每项包含 index 和 summary。只输出 JSON。不要输出思考过程。\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
            )
        else:
            prompt = (
                "Write one concise briefing summary for each theme below. "
                "Return a JSON array with fields index and summary only.\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
            )

        try:
            response = chat_with_profile(self.client, prompt, "summarize", optional=True)
            parsed = parse_llm_json(response or "[]")
            results = {}
            if isinstance(parsed, list):
                for item in parsed:
                    idx = item.get("index")
                    summary = sanitize_generated_text(item.get("summary", "")) if isinstance(item, dict) else ""
                    if idx and summary:
                        results[str(idx)] = summary
                        if 1 <= idx <= len(themes):
                            results[themes[idx - 1].get("id")] = summary
            return results
        except Exception as e:
            logger.warning(f"  ⚠️ Briefing theme summaries: failed ({e})")
            return {}

    def _generate_briefing_trends(self, themes, brief_items) -> list[str]:
        theme_lines = []
        for theme in themes[:6]:
            article_titles = ", ".join(a.title for a in theme.get("articles", [])[:3])
            theme_lines.append(f"- {theme.get('title', '')}: {article_titles}")
        for article in brief_items[:6]:
            theme_lines.append(f"- Brief: {article.title}")

        if self.language == "zh":
            prompt = (
                "基于以下日报材料，总结 1-3 条趋势观察。输出 JSON 数组，每项是一个字符串。只输出 JSON。\n\n"
                + "\n".join(theme_lines)
            )
        else:
            prompt = (
                "Summarize 1-3 trend notes from the material below. Return a JSON array of strings only.\n\n"
                + "\n".join(theme_lines)
            )

        try:
            response = chat_with_profile(self.client, prompt, "trends", optional=True)
            parsed = parse_llm_json(response or "[]")
            if isinstance(parsed, list):
                return [sanitize_generated_text(str(item)) for item in parsed if str(item).strip()]
            return []
        except Exception as e:
            logger.warning(f"  ⚠️ Briefing trends: failed ({e})")
            return []
