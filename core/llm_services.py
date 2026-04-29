"""Consolidated LLM services for DailyDigest."""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .llm_utils import parse_llm_json, sanitize_generated_text
from .briefing import _is_language_compatible
from .llm import (
    get_llm_client,
    chat_with_profile,
    should_skip_optional_llm,
)
from .logging_config import get_logger

logger = get_logger("llm_services")


def generate_theme_titles(themes):
    """Generate concise Chinese titles for clustered themes."""
    client = get_llm_client()
    if not client or should_skip_optional_llm():
        return {}

    payload = []
    for idx, theme in enumerate(themes, 1):
        articles = theme.get("articles", [])
        if not articles:
            continue
        theme_id = theme.get("id", "")
        if not theme_id or not theme_id.startswith("cluster-"):
            continue
        titles = [a.title for a in articles[:3]]
        payload.append({
            "index": idx,
            "current_title": theme.get("title", ""),
            "article_titles": titles,
        })

    if not payload:
        return {}

    prompt = (
        "你是一位科技日报编辑。请为以下每个新闻主题生成一个 4-8 字的中文标题。\n"
        "要求：准确概括主题核心内容；不要使用「模型与平台」等泛泛的标题；输出 JSON 数组，每项包含 index 和 title。\n"
        "只输出 JSON，不要输出思考过程。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    try:
        response = chat_with_profile(client, prompt, "brief_summary", optional=True)
        parsed = parse_llm_json(response or "[]")
        results = {}
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    idx = item.get("index")
                    title = sanitize_generated_text(item.get("title", ""))
                    if idx and title and _is_language_compatible(title):
                        results[str(idx)] = title
        return results
    except Exception as e:
        logger.warning(f"  ⚠️ Theme titles: failed ({e})")
        return {}


def generate_tldr(themes):
    """Generate a 2-3 sentence TL;DR overview of the day's AI developments."""
    client = get_llm_client()
    if not client or should_skip_optional_llm():
        return ""

    lines = []
    for theme in themes[:6]:
        title = theme.get("title", "")
        articles = theme.get("articles", [])[:2]
        article_titles = "、".join(a.title for a in articles)
        lines.append(f"- {title}: {article_titles}")

    if not lines:
        return ""

    prompt = (
        "你是一位 AI 科技日报编辑。基于下面的主题材料，用 60-120 字写一段中文概述，"
        "回答「今天 AI 领域发生了什么大事」。突出最重要的 1-2 条主线。"
        "只输出概述段落，不要加标题、编号或额外说明。\n\n"
        + "\n".join(lines)
    )

    try:
        response = chat_with_profile(client, prompt, "brief_summary", optional=True)
        if response:
            tldr = sanitize_generated_text(response).strip()
            if tldr and _is_language_compatible(tldr):
                return tldr
        return ""
    except Exception as e:
        logger.warning(f"  ⚠️ TL;DR: failed ({e})")
        return ""


def generate_briefing_highlights(themes):
    """Generate synthesized Chinese daily highlights from themed article material."""
    client = get_llm_client()

    lines = []
    for idx, theme in enumerate(themes[:6], 1):
        refs = []
        for article in theme.get("articles", [])[:3]:
            heat = f", HN {article.hn_points}" if article.hn_points else ""
            refs.append(f"- {article.title} ({article.source}{heat})")
        lines.append(f"## Theme {idx}: {theme.get('title', '')}")
        lines.extend(refs)
        lines.append("")

    prompt = (
        '你是一位 AI 科技日报编辑。基于下面的主题材料，输出 4-6 条"今日要点"。\n'
        "要求：\n"
        "- 每条一行，以 '- ' 开头\n"
        "- 格式：【领域】要点描述（15-30字）\n"
        "- 用中文综合改写，不要照搬英文标题\n"
        "- 综合多篇信息，提炼核心要点\n"
        "- 不要输出 JSON、思考过程、字符计数或规则说明\n\n"
        + "\n".join(lines)
    )

    try:
        response = chat_with_profile(client, prompt, "summarize", optional=True)
        cleaned = sanitize_generated_text(response or "")
        return [line.lstrip("- ").strip() for line in cleaned.splitlines() if line.strip().startswith("- ")]
    except Exception as e:
        logger.warning(f"  ⚠️ Briefing highlights: failed ({e})")
        return []


def generate_theme_summaries(themes):
    """Generate in-depth briefing summaries for each theme."""
    client = get_llm_client()

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

    prompt = (
        "你是一位 AI 技术日报编辑。请基于以下主题材料，为每个主题写一段综述（200-350字）。\n"
        "要求：\n"
        "- 包含三部分：1) 这件事是什么 2) 为什么值得关注 3) 可能的影响或后续方向\n"
        "- 综合多篇文章信息，不要逐篇复述\n"
        "- 可以在段落末尾加 1-2 个要点（'- ' 格式的列表项）\n"
        "- 输出 JSON 数组，每项包含 index 和 summary（summary 可以是多行文本）\n"
        "- 只输出 JSON\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    try:
        response = chat_with_profile(client, prompt, "summarize", optional=True)
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


def generate_briefing_trends(themes, brief_items):
    """Generate evidence-based trend observations from daily briefing material."""
    client = get_llm_client()

    theme_lines = []
    for theme in themes[:6]:
        title = theme.get("title", "")
        summary = theme.get("summary", "")
        summary_brief = summary[:100] + "..." if len(summary) > 100 else summary
        article_titles = ", ".join(a.title for a in theme.get("articles", [])[:3])
        theme_lines.append(f"- {title}: {summary_brief or article_titles}")
    for article in (brief_items or [])[:6]:
        theme_lines.append(f"- Brief: {article.title} ({article.source})")

    prompt = (
        "基于以下日报材料，总结 1-3 条跨主题的趋势观察。\n"
        "要求：\n"
        "- 每条格式：【趋势方向】观察描述（支撑证据）\n"
        "- 每条 50-100 字，需要引用具体来源作为证据\n"
        "- 寻找跨主题的关联，而非罗列单个主题\n"
        "- 输出 JSON 数组，每项是一个字符串\n"
        "- 只输出 JSON\n\n"
        + "\n".join(theme_lines)
    )

    try:
        response = chat_with_profile(client, prompt, "trends", optional=True)
        parsed = parse_llm_json(response or "[]")
        if isinstance(parsed, list):
            return [sanitize_generated_text(str(item)) for item in parsed if str(item).strip()]
        return []
    except Exception as e:
        logger.warning(f"  ⚠️ Briefing trends: failed ({e})")
        return []


def render_briefing_v2(briefing_data):
    """Enhanced briefing generation with theme titles, TL;DR, and deeper analysis."""
    themes = briefing_data.get("themes", [])
    if not themes or should_skip_optional_llm():
        return {}

    results = {}

    titles = generate_theme_titles(themes)
    if titles:
        results["theme_titles"] = titles

    tldr = generate_tldr(themes)
    if tldr:
        results["tldr"] = tldr

    highlights = generate_briefing_highlights(themes)
    if highlights:
        results["highlights"] = highlights

    summaries = generate_theme_summaries(themes)
    if summaries:
        results["theme_summaries"] = summaries

    trends = generate_briefing_trends(themes, briefing_data.get("brief_items", []))
    if trends:
        results["trends"] = trends

    return results
