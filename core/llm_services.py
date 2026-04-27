"""Consolidated LLM services for DailyDigest."""

import os
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import get_category_display, CATEGORY_ORDER, WECHAT_STRUCTURE_PROMPT_ZH
from config.prompts.summarizer import (
    CATEGORY_SUMMARY_PROMPT_ZH,
    EXECUTIVE_SUMMARY_PROMPT_ZH,
    TREND_INSIGHTS_PROMPT_ZH,
    PODCAST_BATCH_PROMPT, WECHAT_BATCH_PROMPT,
    TLDR_PROMPT_ZH,
)
from .llm_utils import parse_llm_json, sanitize_generated_text
from .llm import (
    get_llm_client as _get_client,
    get_model as _get_model,
    chat_with_profile as _chat_with_profile,
    generate_with_critique,
    limit_llm_workers,
    should_skip_optional_llm,
)
from .llm import get_llm_client, chat_with_profile  # re-export for test patching
from .article import format_article_item
from .logging_config import get_logger

logger = get_logger("llm_services")


# ---------------------------------------------------------------------------
# Functions from ai_summarizer.py
# ---------------------------------------------------------------------------

def _format_articles_for_prompt(articles, max_per_category=15):
    """将文章列表格式化为 prompt 输入"""
    if not articles:
        return "无新文章"

    sorted_articles = sorted(articles, key=lambda a: a.priority)
    lines = []
    for i, article in enumerate(sorted_articles[:max_per_category], 1):
        item_lines = format_article_item(article, i, desc_limit=200)
        lines.extend(item_lines)
        transcript = article.extra.get("transcript", "")
        if transcript:
            lines.append(f"   视频字幕: {transcript[:500]}")
        lines.append("")

    return "\n".join(lines)


def summarize_category(client, category, articles):
    """对单个分类的文章生成 AI 摘要"""
    if not articles:
        return None

    category_name = get_category_display(category)
    articles_text = _format_articles_for_prompt(articles)

    prompt = CATEGORY_SUMMARY_PROMPT_ZH.format(
        category_name=category_name, articles_text=articles_text,
    )

    summary = _chat_with_profile(client, prompt, "summarize")
    if summary is None:
        logger.error(f"[AI] ❌ 生成 {category_name} 摘要失败")
    return summary


def generate_executive_summary(client, category_summaries, total_stats):
    """生成整体执行摘要"""
    if not category_summaries:
        return ""

    prompt = EXECUTIVE_SUMMARY_PROMPT_ZH.format(
        total_articles=total_stats.get('total_articles', 0),
        categories=total_stats.get('categories', 0),
        category_summaries=json.dumps(category_summaries, ensure_ascii=False, indent=2),
    )

    from config.prompts.critique import get_category_summary_critique
    critique_template = get_category_summary_critique("zh")
    summary = generate_with_critique(client, prompt, "summarize", critique_template, language="zh")
    if summary is None:
        logger.error("[AI] ❌ 生成执行摘要失败")
        return ""
    return summary


def generate_trend_insights(client, category_summaries, total_stats):
    """Generate cross-category trend insights from category summaries."""
    if not category_summaries or should_skip_optional_llm():
        return ""

    summaries_text = "\n".join(
        f"### {name}\n{summary}" for name, summary in category_summaries.items()
    )

    prompt = TREND_INSIGHTS_PROMPT_ZH.format(
        total_articles=total_stats.get('total_articles', 0),
        categories=total_stats.get('categories', 0),
        category_summaries=summaries_text,
    )

    result = _chat_with_profile(client, prompt, "trends", optional=True)
    if result is None:
        logger.warning("[AI] ⚠️ 趋势洞察生成失败")
        return ""

    result = sanitize_generated_text(result)
    if not result or not result.strip():
        return ""

    return result


def summarize_all_categories(articles_by_category, max_workers=None):
    """对所有分类生成 AI 摘要（OpenAI API 模式，并发控制）

    Args:
        articles_by_category: {category: [articles]}
        max_workers: 最大并发 API 调用数（默认跟随全局 LLM 并发门）
    """
    client = _get_client()
    model = _get_model()
    max_workers = limit_llm_workers(max_workers or 5)
    logger.info(f"[AI] 🎯 使用 OpenAI 兼容 API | 模型: {model} | 并发: {max_workers}")

    results = {}
    category_summaries_for_exec = {}

    # 筛选有文章的分类，按 CATEGORY_ORDER 排序
    categories_to_process = [
        cat for cat in sorted(
            articles_by_category.keys(),
            key=lambda c: CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else 99,
        )
        if articles_by_category[cat]
    ]

    total = len(categories_to_process)
    logger.info(f"[AI] 📋 共 {total} 个分类需要生成摘要")

    # 并发生成分类摘要
    def _summarize_one(category):
        articles = articles_by_category[category]
        category_name = get_category_display(category)
        logger.info(f"[AI] 🤖 开始「{category_name}」({len(articles)} 篇)...")
        summary = summarize_category(client, category, articles)
        return category, category_name, articles, summary

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_summarize_one, cat): cat for cat in categories_to_process}
        for future in as_completed(futures):
            category, category_name, articles, summary = future.result()
            completed += 1
            if summary:
                results[category] = {
                    "name": category_name,
                    "summary": summary,
                    "article_count": len(articles),
                    "articles": articles[:15],
                }
                category_summaries_for_exec[category_name] = summary[:500]
                logger.info(f"[AI] ✅ [{completed}/{total}] 「{category_name}」完成")
            else:
                logger.error(f"[AI] ❌ [{completed}/{total}] 「{category_name}」失败")

    # 生成执行摘要（必须在所有分类完成后）
    total_stats = {
        "total_articles": sum(len(a) for a in articles_by_category.values()),
        "categories": len(results),
    }

    if results:
        logger.info(f"[AI] 🤖 正在生成执行摘要...")
        executive_summary = generate_executive_summary(
            client, category_summaries_for_exec, total_stats
        )
    else:
        logger.warning(f"[AI] ⚠️ 所有分类摘要均失败，跳过执行摘要")
        executive_summary = ""

    logger.info(f"[AI] ✅ 完成! 共生成 {len(results)}/{total} 个分类摘要\n")
    return results, executive_summary


def _generic_batch_summarize(updates, source_name, count_unit, format_item, build_prompt,
                             parse_response, batch_size=6, max_workers=None):
    """Generic batch summarization with concurrent API calls.

    Args:
        updates: list of Article objects
        source_name: display name for log messages (e.g. "播客", "微信")
        count_unit: counter word for logs (e.g. "集", "篇")
        format_item: callable(article, index) -> list[str] lines for prompt
        build_prompt: callable(joined_lines) -> str full LLM prompt
        parse_response: callable(parsed_dict) -> dict {url: summary}
        batch_size: items per batch
        max_workers: max concurrent API calls

    Returns:
        dict: {url: summary}
    """
    client = _get_client()
    model = _get_model()
    max_workers = limit_llm_workers(max_workers or 5)
    logger.info(f"[AI] 🎯 使用 OpenAI 兼容 API | 模型: {model} | 并发: {max_workers}")

    batches = []
    for i in range(0, len(updates), batch_size):
        batches.append((i // batch_size, updates[i:i + batch_size]))
    total_batches = len(batches)
    logger.info(f"[AI] 📋 共 {len(updates)} {count_unit}，分 {total_batches} 批")

    def _process_batch(batch_info):
        batch_num, batch = batch_info
        logger.info(f"[AI] 🤖 {source_name} batch {batch_num + 1}/{total_batches} ({len(batch)} {count_unit})...")

        lines = []
        for j, item in enumerate(batch, 1):
            lines.extend(format_item(item, j))
            lines.append("")

        prompt = build_prompt("\n".join(lines))

        response = _chat_with_profile(client, prompt, "summarize")
        batch_summaries = {}
        if response:
            try:
                parsed = parse_llm_json(response)
                batch_summaries = parse_response(parsed)
                logger.info(f"[AI] ✅ batch {batch_num + 1}: {len(batch_summaries)} 条摘要")
            except (ValueError, json.JSONDecodeError):
                logger.warning(f"[AI] ⚠️ batch {batch_num + 1}: JSON 解析失败")
                logger.debug(f"[AI] 📄 原始响应: {response[:300]}")
        return batch_summaries

    ai_summaries = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process_batch, b): b[0] for b in batches}
        for future in as_completed(futures):
            ai_summaries.update(future.result())

    logger.info(f"[AI] ✅ {source_name}摘要完成! 共 {len(ai_summaries)} 条\n")
    return ai_summaries


def summarize_podcast_batch(updates, batch_size=6, max_workers=None):
    """对播客更新批量生成 AI 摘要（OpenAI API 模式，并发控制）

    Args:
        updates: list of Article objects
        batch_size: 每批处理数量
        max_workers: 最大并发 API 调用数

    Returns:
        dict: {episode_url: summary}
    """

    def format_item(ep, j):
        lines = [f"{j}. 播客: {ep.source}", f"   单集: {ep.title}", f"   链接: {ep.url}"]
        shownotes = (ep.full_text or ep.description)[:300]
        if shownotes:
            lines.append(f"   节目简介: {shownotes}")
        return lines

    def build_prompt(joined_lines):
        return PODCAST_BATCH_PROMPT.format(joined_lines=joined_lines)

    def parse_response(parsed):
        return parsed if isinstance(parsed, dict) else {}

    return _generic_batch_summarize(
        updates, "播客", "集", format_item, build_prompt, parse_response,
        batch_size=batch_size, max_workers=max_workers,
    )


def summarize_wechat_batch(updates, batch_size=6, max_workers=None):
    """对微信公众号更新批量生成 AI 摘要（OpenAI API 模式，并发控制）

    Args:
        updates: list of Article objects（来自 run_wechat）
        batch_size: 每批处理数量
        max_workers: 最大并发 API 调用数

    Returns:
        dict: {article_url: ai_summary}
    """

    def format_item(art, j):
        lines = [f"{j}. 公众号: {art.source}", f"   文章: {art.title}", f"   链接: {art.url}"]
        summary = (art.description or "")[:200]
        if summary:
            lines.append(f"   摘要: {summary}")
        return lines

    def build_prompt(joined_lines):
        return WECHAT_BATCH_PROMPT.format(joined_lines=joined_lines)

    def parse_response(parsed):
        result = {}
        for item in parsed.get("summaries", []):
            url = item.get("article_url", "")
            summary = item.get("ai_summary", "")
            if url and summary:
                result[url] = summary
        return result

    return _generic_batch_summarize(
        updates, "微信", "篇", format_item, build_prompt, parse_response,
        batch_size=batch_size, max_workers=max_workers,
    )


def generate_wechat_structure(ai_articles):
    """Generate AI-powered structure for WeChat article: highlights + themed summaries.

    Args:
        ai_articles: list of Article objects (AI-relevant, already curated)

    Returns:
        dict with keys:
          highlights: list of str (one-line bullet items)
          themes: list of {title, summary, articles: [Article]}
        Returns None on failure.
    """
    if not os.environ.get("API_KEY") or should_skip_optional_llm():
        return None

    client = _get_client()

    # Format articles as numbered list
    lines = []
    for i, a in enumerate(ai_articles, 1):
        source = f"来源：{a.source}" if a.source else ""
        summary = (a.description or "")[:200]
        lines.append(f"{i}. {a.title}")
        if source:
            lines.append(f"   {source}")
        if summary:
            lines.append(f"   摘要：{summary}")
        lines.append(f"   链接：{a.url}")
        lines.append("")

    articles_text = "\n".join(lines)

    prompt = WECHAT_STRUCTURE_PROMPT_ZH.format(articles=articles_text)

    logger.info(f"[AI] 🤖 正在生成公众号文章结构（{len(ai_articles)} 篇文章）...")
    response = _chat_with_profile(client, prompt, "wechat_structure", optional=True)

    if not response:
        logger.error("[AI] ❌ 公众号文章结构生成失败")
        return None

    # Parse JSON response
    try:
        parsed = parse_llm_json(response)

        highlights = parsed.get("highlights", [])
        raw_themes = parsed.get("themes", [])

        # Resolve ref indices to actual Article objects
        themes = []
        for t in raw_themes:
            ref_indices = t.get("refs", [])
            theme_articles = []
            for idx in ref_indices:
                if 1 <= idx <= len(ai_articles):
                    theme_articles.append(ai_articles[idx - 1])
            if theme_articles:
                themes.append({
                    "title": t.get("title", ""),
                    "summary": t.get("summary", ""),
                    "articles": theme_articles,
                })

        if not highlights and not themes:
            logger.warning("[AI] ⚠️ AI返回的结构为空")
            return None

        logger.info(f"[AI] ✅ 公众号文章结构生成完成：{len(highlights)} 条要点，{len(themes)} 个主题")
        return {
            "highlights": highlights,
            "themes": themes,
        }

    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"[AI] ❌ 公众号文章结构JSON解析失败: {e}")
        logger.debug(f"[AI] 📄 原始响应: {response[:500]}")
        return None


def generate_tldr(report_content, report_type="tech"):
    """根据完整报告生成 TL;DR（太长不看版）

    Args:
        report_content: str, 完整的 Markdown 报告
        report_type: str, 报告类型（tech/podcast/wechat）

    Returns:
        str: TL;DR 文本，失败返回空字符串
    """
    if not os.environ.get("API_KEY"):
        return ""

    try:
        client = _get_client()
    except ValueError:
        return ""

    type_names = {"tech": "科技日报", "podcast": "播客日报", "wechat": "微信日报", "digest": "每日摘要"}
    type_name = type_names.get(report_type, report_type)

    # 截取报告内容（避免 token 超限，15000 chars ≈ 5k tokens）
    content = report_content[:15000]

    prompt = TLDR_PROMPT_ZH.format(type_name=type_name, content=content)

    logger.info(f"[AI] 🤖 正在生成 TL;DR ({type_name})...")
    response = _chat_with_profile(client, prompt, "tldr")
    if response:
        tldr = sanitize_generated_text(response)
        logger.info(f"[AI] ✅ TL;DR 生成完成")
        return tldr
    else:
        logger.warning(f"[AI] ⚠️ TL;DR 生成失败")
        return ""


# ---------------------------------------------------------------------------
# Flattened functions from narrative_renderer.py (NarrativeRenderer class)
# ---------------------------------------------------------------------------

def render_briefing(briefing_data):
    """Generate batched highlights, theme summaries, and trends for briefing_data."""
    themes = briefing_data.get("themes", [])
    if not themes or should_skip_optional_llm():
        return {}

    results = {
        "highlights": generate_briefing_highlights(themes),
        "theme_summaries": generate_theme_summaries(themes),
    }
    trends = generate_briefing_trends(themes, briefing_data.get("brief_items", []))
    if trends:
        results["trends"] = trends
    return results


def generate_briefing_highlights(themes):
    """Generate one-line daily highlights from themed article material."""
    client = _get_client()

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
        "你是一位科技日报编辑。基于下面的主题材料，输出 4-6 条“今日要点”。\n"
        "要求：每条一行，以 '- ' 开头；只写事实，不写分析过程；不要输出 JSON；"
        "不要出现 思考和规则说明、字符计数。\n\n"
        + "\n".join(lines)
    )

    try:
        response = _chat_with_profile(client, prompt, "summarize", optional=True)
        cleaned = sanitize_generated_text(response or "")
        return [line.lstrip("- ").strip() for line in cleaned.splitlines() if line.strip().startswith("- ")]
    except Exception as e:
        logger.warning(f"  ⚠️ Briefing highlights: failed ({e})")
        return []


def generate_theme_summaries(themes):
    """Generate concise briefing summary for each theme."""
    client = _get_client()

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
        "你是一位 AI 技术日报编辑。请基于以下主题材料，为每个主题写一段 120-220 字的简报综述。"
        "输出 JSON 数组，每项包含 index 和 summary。只输出 JSON。不要输出思考过程。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    try:
        response = _chat_with_profile(client, prompt, "summarize", optional=True)
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
    """Generate 1-3 trend observations from daily briefing material."""
    client = _get_client()

    theme_lines = []
    for theme in themes[:6]:
        article_titles = ", ".join(a.title for a in theme.get("articles", [])[:3])
        theme_lines.append(f"- {theme.get('title', '')}: {article_titles}")
    for article in brief_items[:6]:
        theme_lines.append(f"- Brief: {article.title}")

    prompt = (
        "基于以下日报材料，总结 1-3 条趋势观察。输出 JSON 数组，每项是一个字符串。只输出 JSON。\n\n"
        + "\n".join(theme_lines)
    )

    try:
        response = _chat_with_profile(client, prompt, "trends", optional=True)
        parsed = parse_llm_json(response or "[]")
        if isinstance(parsed, list):
            return [sanitize_generated_text(str(item)) for item in parsed if str(item).strip()]
        return []
    except Exception as e:
        logger.warning(f"  ⚠️ Briefing trends: failed ({e})")
        return []
