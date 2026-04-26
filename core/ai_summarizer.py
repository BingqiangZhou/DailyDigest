"""
AI 摘要生成模块（OpenAI API 后端）
用于 GitHub Actions 模式，通过 OpenAI 兼容 API 生成分类摘要和执行摘要。
"""

import os
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import get_category_display, CATEGORY_ORDER, WECHAT_STRUCTURE_PROMPT_ZH
from config.prompts.summarizer import (
    CATEGORY_SUMMARY_PROMPT_ZH, CATEGORY_SUMMARY_PROMPT_EN,
    EXECUTIVE_SUMMARY_PROMPT_ZH, EXECUTIVE_SUMMARY_PROMPT_EN,
    PODCAST_BATCH_PROMPT, WECHAT_BATCH_PROMPT,
    TLDR_PROMPT_ZH, TLDR_PROMPT_EN,
)
from .llm_utils import parse_llm_json, strip_code_fences
from .llm import (
    get_llm_client as _get_client,
    get_model as _get_model,
    chat_completion as _chat_completion,
    chat_with_profile as _chat_with_profile,
    generate_with_critique,
    TASK_PROFILES,
)
from .article import format_article_item
from .logging_config import get_logger

logger = get_logger("ai_summarizer")


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


def summarize_category(client, category, articles, report_language="zh"):
    """对单个分类的文章生成 AI 摘要"""
    if not articles:
        return None

    category_name = get_category_display(category)
    articles_text = _format_articles_for_prompt(articles)

    if report_language == "zh":
        prompt = CATEGORY_SUMMARY_PROMPT_ZH.format(
            category_name=category_name, articles_text=articles_text,
        )
    else:
        prompt = CATEGORY_SUMMARY_PROMPT_EN.format(
            category_name=category_name, articles_text=articles_text,
        )

    summary = _chat_with_profile(client, prompt, "summarize")
    if summary is None:
        logger.error(f"[AI] ❌ 生成 {category_name} 摘要失败")
    return summary


def generate_executive_summary(client, category_summaries, total_stats, report_language="zh"):
    """生成整体执行摘要"""
    if not category_summaries:
        return ""

    if report_language == "zh":
        prompt = EXECUTIVE_SUMMARY_PROMPT_ZH.format(
            total_articles=total_stats.get('total_articles', 0),
            categories=total_stats.get('categories', 0),
            category_summaries=json.dumps(category_summaries, ensure_ascii=False, indent=2),
        )
    else:
        prompt = EXECUTIVE_SUMMARY_PROMPT_EN.format(
            total_articles=total_stats.get('total_articles', 0),
            categories=total_stats.get('categories', 0),
            category_summaries=json.dumps(category_summaries, ensure_ascii=False, indent=2),
        )

    from .config import CATEGORY_SUMMARY_CRITIQUE
    summary = generate_with_critique(client, prompt, "summarize", CATEGORY_SUMMARY_CRITIQUE)
    if summary is None:
        logger.error("[AI] ❌ 生成执行摘要失败")
        return ""
    return summary


def summarize_all_categories(articles_by_category, report_language="zh", max_workers=5):
    """对所有分类生成 AI 摘要（OpenAI API 模式，并发控制）

    Args:
        articles_by_category: {category: [articles]}
        report_language: 报告语言
        max_workers: 最大并发 API 调用数（默认 3，避免 rate limit）
    """
    client = _get_client()
    model = _get_model()
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
        summary = summarize_category(client, category, articles, report_language)
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
                category_summaries_for_exec[category_name] = summary[:200]
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
            client, category_summaries_for_exec, total_stats, report_language
        )
    else:
        logger.warning(f"[AI] ⚠️ 所有分类摘要均失败，跳过执行摘要")
        executive_summary = ""

    logger.info(f"[AI] ✅ 完成! 共生成 {len(results)}/{total} 个分类摘要\n")
    return results, executive_summary


def _generic_batch_summarize(updates, source_name, count_unit, format_item, build_prompt,
                             parse_response, batch_size=10, max_workers=5):
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


def summarize_podcast_batch(updates, batch_size=10, max_workers=5):
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


def summarize_wechat_batch(updates, batch_size=10, max_workers=5):
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


def generate_wechat_structure(ai_articles, language="zh"):
    """Generate AI-powered structure for WeChat article: highlights + themed summaries.

    Args:
        ai_articles: list of Article objects (AI-relevant, already curated)
        language: "zh" or "en"

    Returns:
        dict with keys:
          highlights: list of str (one-line bullet items)
          themes: list of {title, summary, articles: [Article]}
        Returns None on failure.
    """
    if not os.environ.get("API_KEY"):
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
    response = _chat_with_profile(client, prompt, "wechat_structure")

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


def generate_tldr(report_content, report_type="tech", language="zh"):
    """根据完整报告生成 TL;DR（太长不看版）

    Args:
        report_content: str, 完整的 Markdown 报告
        report_type: str, 报告类型（tech/podcast/wechat）
        language: str, 语言（zh/en）

    Returns:
        str: TL;DR 文本，失败返回空字符串
    """
    if not os.environ.get("API_KEY"):
        return ""

    try:
        client = _get_client()
    except ValueError:
        return ""

    type_names = {
        "tech": "科技日报" if language == "zh" else "Tech Daily",
        "podcast": "播客日报" if language == "zh" else "Podcast Daily",
        "wechat": "微信日报" if language == "zh" else "WeChat Daily",
        "digest": "每日摘要" if language == "zh" else "Daily Digest",
    }
    type_name = type_names.get(report_type, report_type)

    # 截取报告内容（避免 token 超限）
    content = report_content[:8000]

    if language == "zh":
        prompt = TLDR_PROMPT_ZH.format(type_name=type_name, content=content)
    else:
        prompt = TLDR_PROMPT_EN.format(type_name=type_name, content=content)

    logger.info(f"[AI] 🤖 正在生成 TL;DR ({type_name})...")
    response = _chat_with_profile(client, prompt, "tldr")
    if response:
        tldr = strip_code_fences(response)
        logger.info(f"[AI] ✅ TL;DR 生成完成")
        return tldr
    else:
        logger.warning(f"[AI] ⚠️ TL;DR 生成失败")
        return ""
