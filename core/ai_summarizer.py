"""
AI 摘要生成模块（OpenAI API 后端）
用于 GitHub Actions 模式，通过 OpenAI 兼容 API 生成分类摘要和执行摘要。
"""

import os
import json
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import get_category_display, CATEGORY_ORDER


# 默认模型配置
DEFAULT_MODEL = "nvidia/llama-3.1-nemotron-70b-instruct"
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"


def _get_client():
    """初始化 OpenAI 兼容 API 客户端"""
    from openai import OpenAI

    api_key = os.environ.get("API_KEY")
    if not api_key:
        raise ValueError("请设置环境变量 API_KEY")

    base_url = os.environ.get("BASE_URL") or DEFAULT_BASE_URL
    return OpenAI(api_key=api_key, base_url=base_url, timeout=120, max_retries=2)


def _get_model():
    """获取模型名称"""
    return os.environ.get("MODEL") or DEFAULT_MODEL


def _chat_completion(client, prompt, max_tokens=4000, max_retries=3):
    """调用 OpenAI 兼容 API（带重试）"""
    model = _get_model()
    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.7,
                top_p=0.9,
                timeout=60,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_error = e
            print(f"[AI] ⚠️ API 调用失败 (attempt {attempt + 1}/{max_retries}, model={model}): {e}")
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 5
                print(f"[AI]   等待 {wait} 秒后重试...")
                time.sleep(wait)
    print(f"[AI] ❌ API 调用最终失败 (model={model}): {last_error}")
    return None


def _format_articles_for_prompt(articles, max_per_category=15):
    """将文章列表格式化为 prompt 输入"""
    if not articles:
        return "无新文章"

    sorted_articles = sorted(articles, key=lambda a: a.get("priority", 3))
    lines = []
    for i, article in enumerate(sorted_articles[:max_per_category], 1):
        title = article.get("title", "无标题")
        source = article.get("source_name", article.get("source", "未知来源"))
        summary = article.get("description", article.get("summary", ""))[:200]
        link = article.get("url", article.get("link", ""))
        lang_tag = "🇨🇳" if article.get("language") == "zh" else "🇺🇸"
        transcript = article.get("transcript", "")

        lines.append(f"{i}. [{lang_tag}] {title}")
        lines.append(f"   来源: {source}")
        if summary:
            lines.append(f"   摘要: {summary}")
        lines.append(f"   链接: {link}")
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
        prompt = f"""你是一位专业的科技新闻编辑。请对以下「{category_name}」分类的最新文章进行汇总分析。

## 文章列表

{articles_text}

## 要求

1. **整体概述**（2-3句话）：概括该分类过去24-48小时的重要趋势和热点
2. **重点文章推荐**（3-5篇）：挑选最重要/最有趣的文章，每篇用1-2句话说明为什么值得关注
3. **关键洞察**（1-2条）：从这些文章中提炼出的关键趋势或洞察

请用中文输出，保持专业但易懂的语调。使用 Markdown 格式。不要编造文章中没有的信息。"""
    else:
        prompt = f"""You are a professional tech news editor. Please summarize and analyze the following articles in the "{category_name}" category.

## Article List

{articles_text}

## Requirements

1. **Overview** (2-3 sentences): Summarize the key trends and highlights from the past 24-48 hours
2. **Top Picks** (3-5 articles): Select the most important/interesting articles, explain why each is worth attention in 1-2 sentences
3. **Key Insights** (1-2 insights): Extract key trends or insights from these articles

Please write in English, professional yet accessible tone. Use Markdown format. Do not fabricate information not present in the articles."""

    summary = _chat_completion(client, prompt, max_tokens=4000)
    if summary is None:
        print(f"[AI] ❌ 生成 {category_name} 摘要失败")
    return summary


def generate_executive_summary(client, category_summaries, total_stats, report_language="zh"):
    """生成整体执行摘要"""
    if not category_summaries:
        return ""

    if report_language == "zh":
        prompt = f"""你是一位资深科技媒体主编。基于以下各分类的摘要，生成一份简洁的"今日要闻"执行摘要。

## 今日数据
- 总文章数: {total_stats.get('total_articles', 0)}
- 涉及分类: {total_stats.get('categories', 0)}
- 数据时间范围: 过去 48 小时

## 各分类摘要

{json.dumps(category_summaries, ensure_ascii=False, indent=2)}

## 要求
- 3-5句话概括今日最值得关注的科技/AI 动态
- 突出最重要的 1-2 个事件或趋势
- 使用中文，简洁有力的新闻语调
- 不要超过 200 字"""
    else:
        prompt = f"""You are a senior tech media editor-in-chief. Based on the category summaries below, generate a concise "Today's Highlights" executive summary.

## Today's Data
- Total articles: {total_stats.get('total_articles', 0)}
- Categories covered: {total_stats.get('categories', 0)}
- Time range: Past 48 hours

## Category Summaries

{json.dumps(category_summaries, ensure_ascii=False, indent=2)}

## Requirements
- 3-5 sentences summarizing the most noteworthy tech/AI developments today
- Highlight the 1-2 most important events or trends
- Use English, concise and impactful news tone
- Do not exceed 200 words"""

    summary = _chat_completion(client, prompt, max_tokens=1000)
    if summary is None:
        print("[AI] ❌ 生成执行摘要失败")
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
    print(f"[AI] 🎯 使用 OpenAI 兼容 API | 模型: {model} | 并发: {max_workers}")

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
    print(f"[AI] 📋 共 {total} 个分类需要生成摘要")

    # 并发生成分类摘要
    def _summarize_one(category):
        articles = articles_by_category[category]
        category_name = get_category_display(category)
        print(f"[AI] 🤖 开始「{category_name}」({len(articles)} 篇)...")
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
                print(f"[AI] ✅ [{completed}/{total}] 「{category_name}」完成")
            else:
                print(f"[AI] ❌ [{completed}/{total}] 「{category_name}」失败")

    # 生成执行摘要（必须在所有分类完成后）
    total_stats = {
        "total_articles": sum(len(a) for a in articles_by_category.values()),
        "categories": len(results),
    }

    if results:
        print(f"[AI] 🤖 正在生成执行摘要...")
        executive_summary = generate_executive_summary(
            client, category_summaries_for_exec, total_stats, report_language
        )
    else:
        print(f"[AI] ⚠️ 所有分类摘要均失败，跳过执行摘要")
        executive_summary = ""

    print(f"[AI] ✅ 完成! 共生成 {len(results)}/{total} 个分类摘要\n")
    return results, executive_summary


def summarize_podcast_batch(updates, batch_size=10, max_workers=5):
    """对播客更新批量生成 AI 摘要（OpenAI API 模式，并发控制）

    Args:
        updates: list of update dicts（来自 run_podcast）
        batch_size: 每批处理数量
        max_workers: 最大并发 API 调用数

    Returns:
        dict: {episode_url: summary}
    """
    client = _get_client()
    model = _get_model()
    print(f"[AI] 🎯 使用 OpenAI 兼容 API | 模型: {model} | 并发: {max_workers}")

    # 分批
    batches = []
    for i in range(0, len(updates), batch_size):
        batches.append((i // batch_size, updates[i:i + batch_size]))
    total_batches = len(batches)
    print(f"[AI] 📋 共 {len(updates)} 集，分 {total_batches} 批")

    def _summarize_batch(batch_info):
        batch_num, batch = batch_info
        print(f"[AI] 🤖 播客 batch {batch_num + 1}/{total_batches} ({len(batch)} 集)...")

        lines = []
        for j, ep in enumerate(batch, 1):
            lines.append(f"{j}. 播客: {ep.get('podcast_name', '未知')}")
            lines.append(f"   单集: {ep.get('episode_title', '未知')}")
            shownotes = ep.get("shownotes", "")[:300]
            if shownotes:
                lines.append(f"   节目简介: {shownotes}")
            lines.append("")

        prompt = f"""你是一位播客内容编辑。请对以下播客单集各写一句中文摘要（30-50字）。
过滤掉广告和推广内容。如果内容不足，输出"内容暂无"。

## 单集列表

{chr(10).join(lines)}

## 输出格式

请严格按以下 JSON 格式输出，不要输出其他内容：
{{"url1": "摘要1", "url2": "摘要2", ...}}

其中 url 为每个单集的 episode_url。"""

        response = _chat_completion(client, prompt, max_tokens=2000)
        batch_summaries = {}
        if response:
            try:
                json_str = response.strip()
                if json_str.startswith("```"):
                    json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                parsed = json.loads(json_str)
                if isinstance(parsed, dict):
                    batch_summaries.update(parsed)
                    print(f"[AI] ✅ batch {batch_num + 1}: {len(parsed)} 条摘要")
            except (json.JSONDecodeError, ValueError):
                print(f"[AI] ⚠️ batch {batch_num + 1}: JSON 解析失败")
        return batch_summaries

    ai_summaries = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_summarize_batch, b): b[0] for b in batches}
        for future in as_completed(futures):
            batch_summaries = future.result()
            completed += 1
            ai_summaries.update(batch_summaries)

    print(f"[AI] ✅ 播客摘要完成! 共 {len(ai_summaries)} 条\n")
    return ai_summaries


def summarize_wechat_batch(updates, batch_size=10, max_workers=5):
    """对微信公众号更新批量生成 AI 摘要（OpenAI API 模式，并发控制）

    Args:
        updates: list of update dicts（来自 run_wechat）
        batch_size: 每批处理数量
        max_workers: 最大并发 API 调用数

    Returns:
        dict: {article_url: ai_summary}
    """
    client = _get_client()
    model = _get_model()
    print(f"[AI] 🎯 使用 OpenAI 兼容 API | 模型: {model} | 并发: {max_workers}")

    # 分批
    batches = []
    for i in range(0, len(updates), batch_size):
        batches.append((i // batch_size, updates[i:i + batch_size]))
    total_batches = len(batches)
    print(f"[AI] 📋 共 {len(updates)} 篇，分 {total_batches} 批")

    def _summarize_batch(batch_info):
        batch_num, batch = batch_info
        print(f"[AI] 🤖 微信 batch {batch_num + 1}/{total_batches} ({len(batch)} 篇)...")

        lines = []
        for j, art in enumerate(batch, 1):
            lines.append(f"{j}. 公众号: {art.get('account_name', '未知')}")
            lines.append(f"   文章: {art.get('article_title', '未知')}")
            summary = art.get("summary_text", "")[:200]
            if summary:
                lines.append(f"   摘要: {summary}")
            lines.append("")

        prompt = f"""你是一位微信公众号内容编辑。请对以下文章各写一句中文摘要（不超过100字）。

## 文章列表

{chr(10).join(lines)}

## 输出格式

请严格按以下 JSON 格式输出，不要输出其他内容：
{{"summaries": [{{"article_url": "url1", "ai_summary": "摘要1"}}, ...]}}

其中 article_url 为每篇文章的 article_url。"""

        response = _chat_completion(client, prompt, max_tokens=2000)
        batch_summaries = {}
        if response:
            try:
                json_str = response.strip()
                if json_str.startswith("```"):
                    json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                parsed = json.loads(json_str)
                summaries_list = parsed.get("summaries", [])
                for item in summaries_list:
                    url = item.get("article_url", "")
                    summary = item.get("ai_summary", "")
                    if url and summary:
                        batch_summaries[url] = summary
                print(f"[AI] ✅ batch {batch_num + 1}: {len(summaries_list)} 条摘要")
            except (json.JSONDecodeError, ValueError):
                print(f"[AI] ⚠️ batch {batch_num + 1}: JSON 解析失败")
        return batch_summaries

    ai_summaries = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_summarize_batch, b): b[0] for b in batches}
        for future in as_completed(futures):
            batch_summaries = future.result()
            completed += 1
            ai_summaries.update(batch_summaries)

    print(f"[AI] ✅ 微信摘要完成! 共 {len(ai_summaries)} 条\n")
    return ai_summaries


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
    }
    type_name = type_names.get(report_type, report_type)

    # 截取报告内容（避免 token 超限）
    content = report_content[:8000]

    if language == "zh":
        prompt = f"""你是一位资深编辑。请为以下{type_name}写一个"太长不看"(TL;DR)版本。

要求：
1. 用 3-5 个要点概括最重要的内容
2. 每个要点一行，以 "- " 开头
3. 总字数不超过 200 字
4. 语言简洁有力，适合快速浏览
5. 不要编造报告中没有的信息

## 原始报告

{content}

## 输出格式

直接输出要点列表，不要输出其他内容。"""
    else:
        prompt = f"""You are a senior editor. Write a "Too Long; Didn't Read" (TL;DR) version of the following {type_name}.

Requirements:
1. 3-5 bullet points covering the most important content
2. Each point starts with "- "
3. Total under 200 words
4. Concise and punchy, suitable for quick scanning
5. Do not fabricate information not in the report

## Original Report

{content}

## Output Format

Output only the bullet points, nothing else."""

    print(f"[AI] 🤖 正在生成 TL;DR ({type_name})...")
    response = _chat_completion(client, prompt, max_tokens=500)
    if response:
        # 清理可能的 markdown 包裹
        tldr = response.strip()
        if tldr.startswith("```"):
            tldr = tldr.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        print(f"[AI] ✅ TL;DR 生成完成")
        return tldr
    else:
        print(f"[AI] ⚠️ TL;DR 生成失败")
        return ""
