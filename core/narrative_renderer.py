"""
Narrative renderer for DailyDigest magazine-style reports.

Generates LLM-driven content for headline stories, noteworthy summaries,
and trend analysis. Falls back to template text on LLM failure.
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .article import Article
from .logging_config import get_logger
from .llm import get_llm_client, chat_with_profile, limit_llm_workers, should_skip_optional_llm
from .llm_utils import (
    parse_llm_json,
    strip_code_fences,
    sanitize_generated_text,
)

logger = get_logger("narrative_renderer")


class NarrativeRenderer:
    """LLM-powered narrative generator for magazine-style reports."""

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

    def render_headlines(self, headlines) -> list[str]:
        """Generate narrative for each headline story in parallel.

        Returns list of narrative strings (same order as headlines).
        Falls back to empty string on failure (template mode in report_builder).
        """
        from config.prompts.narrative import (
            HEADLINE_NARRATIVE_PROMPT_ZH,
            HEADLINE_NARRATIVE_PROMPT_EN,
        )

        if not headlines:
            return []

        template = (HEADLINE_NARRATIVE_PROMPT_ZH if self.language == "zh"
                    else HEADLINE_NARRATIVE_PROMPT_EN)

        def _generate_one(idx_and_h):
            idx, h = idx_and_h
            try:
                article = h.main
                title = article.title
                source = article.source or ""
                content = article.full_text or article.description or ""
                content = content[:2000] if content else "(no content available)"

                related_parts = []
                for r in h.related[:4]:
                    related_parts.append(f"- [{r.title}]({r.url}) ({r.source or 'Unknown'})")
                related = "\n".join(related_parts) if related_parts else "(无相关报道)"

                prompt = template.format(
                    title=title, source=source, content=content, related=related
                )

                response = chat_with_profile(self.client, prompt, "narrative", optional=True)
                if response:
                    narrative = sanitize_generated_text(response)
                    self._success += 1
                    logger.info(f"  📝 Headline {idx + 1}/{len(headlines)}: narrative generated ({len(narrative)} chars)")
                    return idx, narrative
                else:
                    self._failure += 1
                    logger.warning(f"  ⚠️ Headline {idx + 1}/{len(headlines)}: LLM returned empty, using template")
                    return idx, ""
            except Exception as e:
                self._failure += 1
                logger.warning(f"  ⚠️ Headline {idx + 1}/{len(headlines)}: failed ({e}), using template")
                return idx, ""

        # Parallel execution with ThreadPoolExecutor
        results = [""] * len(headlines)
        max_workers = min(len(headlines), limit_llm_workers(5))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_generate_one, (i, h)): i
                for i, h in enumerate(headlines)
            }
            for future in as_completed(futures):
                idx, narrative = future.result()
                results[idx] = narrative

        return results

    def summarize_noteworthy(self, noteworthy: dict[str, list[Article]]) -> dict[str, str]:
        """Generate summaries for noteworthy articles missing RSS summaries.

        Returns dict mapping article URL to generated summary.
        """
        from config.prompts.narrative import (
            BATCH_SUMMARY_PROMPT_ZH,
            BATCH_SUMMARY_PROMPT_EN,
        )

        # Collect articles needing summaries
        need_summary: list[tuple[int, Article]] = []
        for theme, articles in noteworthy.items():
            for article in articles:
                has_summary = bool(article.description and len(article.description.strip()) > 20)
                if not has_summary:
                    need_summary.append((len(need_summary), article))

        if not need_summary:
            logger.info("  📋 All noteworthy articles have RSS summaries, skipping LLM")
            return {}

        logger.info(f"  📋 Generating summaries for {len(need_summary)} articles without RSS summaries")

        summaries: dict[str, str] = {}
        batch_size = 10
        template = (BATCH_SUMMARY_PROMPT_ZH if self.language == "zh"
                    else BATCH_SUMMARY_PROMPT_EN)

        for batch_start in range(0, len(need_summary), batch_size):
            batch = need_summary[batch_start:batch_start + batch_size]
            try:
                articles_json = json.dumps([
                    {
                        "index": idx,
                        "title": a.title,
                        "source": a.source or "",
                        "description": (a.description or "")[:200],
                    }
                    for idx, a in batch
                ], ensure_ascii=False, indent=2)

                prompt = template.format(articles_json=articles_json)
                response = chat_with_profile(self.client, prompt, "brief_summary", optional=True)

                if response:
                    parsed = parse_llm_json(response)
                    if isinstance(parsed, list):
                        for item in parsed:
                            idx = item.get("index")
                            summary = item.get("summary", "")
                            if idx is not None and summary:
                                # Find article by batch index
                                for orig_idx, article in batch:
                                    if orig_idx == idx:
                                        summaries[article.url] = summary
                                        break
                        self._success += 1
                    elif isinstance(parsed, dict):
                        for key, item in parsed.items():
                            idx = item.get("index") if isinstance(item, dict) else int(key)
                            summary = item.get("summary", "") if isinstance(item, dict) else str(item)
                            if summary:
                                for orig_idx, article in batch:
                                    if orig_idx == idx:
                                        summaries[article.url] = summary
                                        break
                        self._success += 1
                else:
                    self._failure += 1
                    logger.warning(f"  ⚠️ Summary batch {batch_start // batch_size + 1}: LLM returned empty")
            except Exception as e:
                self._failure += 1
                logger.warning(f"  ⚠️ Summary batch {batch_start // batch_size + 1}: failed ({e})")

        logger.info(f"  📋 Generated {len(summaries)} summaries")
        return summaries

    def generate_trends(self, headlines, noteworthy) -> list[str]:
        """Generate 1-3 trend insights from the day's news."""
        from config.prompts.narrative import (
            TREND_ANALYSIS_PROMPT_ZH,
            TREND_ANALYSIS_PROMPT_EN,
        )

        # Build summary texts for prompt
        hl_parts = []
        for h in headlines[:8]:
            desc = re.sub(r'<[^>]+>', '', (h.main.description or "")[:100]).strip()
            hl_parts.append(f"- {h.main.title}: {desc}" if desc else f"- {h.main.title}")
        headlines_summary = "\n".join(hl_parts)

        nw_parts = []
        for theme, articles in noteworthy.items():
            titles = [a.title for a in articles[:5]]
            nw_parts.append(f"**{theme}**: {', '.join(titles)}")
        noteworthy_summary = "\n".join(nw_parts)

        template = (TREND_ANALYSIS_PROMPT_ZH if self.language == "zh"
                    else TREND_ANALYSIS_PROMPT_EN)
        prompt = template.format(
            headlines_summary=headlines_summary,
            noteworthy_summary=noteworthy_summary,
        )

        try:
            response = chat_with_profile(self.client, prompt, "trends", optional=True)
            if response:
                parsed = parse_llm_json(response)
                trends = []
                if isinstance(parsed, list):
                    for item in parsed:
                        trend = item.get("trend", "") if isinstance(item, dict) else str(item)
                        if trend:
                            trends.append(trend)
                elif isinstance(parsed, dict):
                    for key, item in parsed.items():
                        trend = item.get("trend", "") if isinstance(item, dict) else str(item)
                        if trend:
                            trends.append(trend)

                if trends:
                    self._success += 1
                    logger.info(f"  📈 Generated {len(trends)} trend insights")
                    return trends

            self._failure += 1
            logger.warning("  ⚠️ Trend analysis: no valid trends generated")
            return []
        except Exception as e:
            self._failure += 1
            logger.warning(f"  ⚠️ Trend analysis: failed ({e})")
            return []

    def render_full(self, story_group) -> dict:
        """Run all rendering steps and return results dict.

        Returns:
            {
                "headline_narratives": list[str],
                "noteworthy_summaries": dict[str, str],
                "trends": list[str],
            }
        """
        logger.info("🎨 Narrative renderer: generating LLM content...")

        headline_narratives = self.render_headlines(story_group.headlines)
        noteworthy_summaries = self.summarize_noteworthy(story_group.noteworthy)
        trends = self.generate_trends(story_group.headlines, story_group.noteworthy)

        logger.info(
            f"🎨 Narrative renderer done: "
            f"{self._success} successes, {self._failure} failures"
        )

        return {
            "headline_narratives": headline_narratives,
            "noteworthy_summaries": noteworthy_summaries,
            "trends": trends,
        }
