# AI-Focused Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the daily digest into a unified two-part report — Part I is an AI deep analysis section with editorial commentary, trend insights, and sub-domain coverage; Part II retains the existing format for non-AI tech news.

**Architecture:** After the existing pipeline collects and processes all articles, a new filtering module (`ai_filter.py`) splits them into AI-relevant and non-AI sets. A new report module (`ai_report.py`) generates the deep analysis Part I using specialized prompts. The existing report generator handles Part II. `pipeline.py` orchestrates both parts into a single merged output.

**Tech Stack:** Python 3.11 stdlib + openai + existing codebase infrastructure (Article dataclass, ai_summarizer._chat_completion, concurrent.futures)

---

### Task 1: Add AI digest configuration constants to `core/config.py`

**Files:**
- Modify: `core/config.py` (append after existing constants)

- [ ] **Step 1: Add AI categories, keyword list, and prompt templates**

Append the following to `core/config.py` after the `ensure_dirs` function:

```python
# ============================================================
# AI Digest Configuration
# ============================================================

# Categories whose articles are directly included in the AI digest
AI_DIGEST_DIRECT_CATEGORIES = {"ai_ml", "ai_tools"}

# Fallback AI keywords for keyword-based filtering when API is unavailable
AI_KEYWORDS_ZH = [
    "人工智能", "AI", "大模型", "LLM", "机器学习", "深度学习",
    "神经网络", "GPT", "Claude", "Gemini", "大语言模型", "Transformer",
    "AGI", "AIGC", "生成式", "智能体", "Agent", "RAG", "微调",
    "训练", "推理", "开源模型", "闭源模型", "多模态", "文生图",
    "文生视频", "语音识别", "NLP", "计算机视觉", "强化学习",
    "芯片", "GPU", "TPU", "算力", "AI芯片", "英伟达", "NVIDIA",
]
AI_KEYWORDS_EN = [
    "AI", "artificial intelligence", "LLM", "GPT", "Claude", "Gemini",
    "machine learning", "deep learning", "neural network", "transformer",
    "AGI", "AIGC", "generative", "agent", "RAG", "fine-tun",
    "inference", "open-source model", "multimodal", "text-to-image",
    "text-to-video", "NLP", "computer vision", "reinforcement learning",
    "GPU", "TPU", "AI chip", "NVIDIA", "deepseek", "anthropic",
    "openai", "google ai", "meta ai", "copilot", "chatbot",
]

# Prompt for batch AI-relevance classification
AI_FILTER_PROMPT_ZH = """你是一位AI领域内容分类专家。请判断以下每篇文章是否与AI/机器学习/大模型/AI应用/AI芯片/AI工具等主题直接相关。

相关标准（宽松）：
- 直接讨论AI技术、模型、算法、训练、推理
- AI产品、工具、应用、平台
- AI公司动态（OpenAI、Anthropic、Google DeepMind、Meta AI等）
- AI芯片、算力基础设施
- AI政策、监管、伦理
- 使用AI技术的产品更新

不相关：
- 纯硬件产品发布（非AI芯片）
- 一般软件开发新闻（无AI成分）
- 纯商业/金融新闻

## 文章列表

{articles}

## 输出格式

严格按JSON输出，不要输出其他内容：
{{"id_1": true, "id_2": false, ...}}

其中 key 为文章编号，value 为布尔值（true=AI相关，false=不相关）。"""

AI_FILTER_PROMPT_EN = """You are an AI domain content classifier. Determine whether each article below is directly related to AI/machine learning/LLMs/AI applications/AI chips/AI tools.

Relevance criteria (lenient):
- Direct discussion of AI technology, models, algorithms, training, inference
- AI products, tools, applications, platforms
- AI company news (OpenAI, Anthropic, Google DeepMind, Meta AI, etc.)
- AI chips, compute infrastructure
- AI policy, regulation, ethics
- Product updates that use AI technology

Not relevant:
- Pure hardware product launches (non-AI chips)
- General software development news (no AI component)
- Pure business/finance news

## Article List

{articles}

## Output Format

Strict JSON output, nothing else:
{{"id_1": true, "id_2": false, ...}}

Key is the article number, value is boolean (true=AI-related, false=not related)."""

# Prompt for AI deep analysis report
AI_DEEP_ANALYSIS_PROMPT_ZH = """你是一位资深AI行业分析师。请基于以下AI相关文章，生成一份深度分析报告。

你需要扮演"AI领域分析师"角色，提供专业的编辑视角和趋势洞察。

## AI相关文章

{articles}

## 报告要求

请按以下结构输出Markdown格式报告：

### 🔥 今日热点
选取2-3条最重要的AI新闻，每条包含：
- **标题和来源链接**
- 1-2句"为什么重要"的编辑评论

### 📊 趋势洞察
从所有文章中归纳2-3条跨文章的趋势模式，例如：
- 多家公司发布同类产品
- 某项技术从研究走向应用
- 行业格局变化
每条趋势附上支撑论据（引用具体文章）。

### 📰 详细报道

按以下子领域分类，每个领域列出相关文章表格（标题+链接 | 来源 | 核心要点）：

#### 基础模型与研究
（模型发布、研究论文、训练技术等）

#### AI工具与应用
（AI产品、工具、应用场景等）

#### AI硬件与基础设施
（AI芯片、算力、数据中心等）

#### 行业动态与观点
（公司动态、投融资、政策、观点评论等）

如果某个子领域没有相关文章，跳过该子领域。

### 🎙️ AI播客精选
（如果有播客内容被标记为AI相关，列出单集标题+播客名+摘要）

### 📱 AI微信精选
（如果有微信公众号内容被标记为AI相关，列出文章标题+公众号名+摘要）

## 注意事项
- 核心要点应该是提炼的洞察，而非简单转述
- 保持专业但易懂的语调
- 不要编造文章中没有的信息
- 使用中文输出"""

AI_DEEP_ANALYSIS_PROMPT_EN = """You are a senior AI industry analyst. Based on the following AI-related articles, generate a deep analysis report.

You should play the role of an "AI domain analyst", providing professional editorial perspective and trend insights.

## AI-Related Articles

{articles}

## Report Requirements

Output a Markdown report with the following structure:

### 🔥 Hot Topics
Select 2-3 most important AI news items, each with:
- **Title and source link**
- 1-2 sentences of editorial commentary on why it matters

### 📊 Trend Insights
Identify 2-3 cross-article trend patterns from all articles, such as:
- Multiple companies releasing similar products
- A technology moving from research to application
- Industry landscape shifts
Each trend should cite supporting articles.

### 📰 Detailed Coverage

Group articles by the following sub-domains, each with a table (title+link | source | key insight):

#### Foundation Models & Research
(Model releases, research papers, training techniques, etc.)

#### AI Tools & Applications
(AI products, tools, use cases, etc.)

#### AI Hardware & Infrastructure
(AI chips, compute, data centers, etc.)

#### Industry News & Opinions
(Company updates, funding, policy, opinion pieces, etc.)

Skip any sub-domain with no relevant articles.

### 🎙️ AI Podcast Highlights
(If any podcast content is flagged as AI-related, list episode title + podcast name + summary)

### 📱 AI WeChat Highlights
(If any WeChat content is flagged as AI-related, list article title + account name + summary)

## Notes
- Key insights should be extracted observations, not simple restatements
- Professional yet accessible tone
- Do not fabricate information not present in the articles
- Output in English"""
```

- [ ] **Step 2: Commit**

```bash
git add core/config.py
git commit -m "feat: add AI digest configuration constants and prompt templates"
```

---

### Task 2: Create `core/ai_filter.py` — AI content filtering module

**Files:**
- Create: `core/ai_filter.py`

- [ ] **Step 1: Write the module with both API-based and keyword-based filtering**

Create `core/ai_filter.py`:

```python
"""
AI content filter module.
Splits articles into AI-relevant and non-AI sets using
category matching, AI API classification, or keyword fallback.
"""

import json
import os

from .article import Article
from .config import (
    AI_DIGEST_DIRECT_CATEGORIES,
    AI_KEYWORDS_ZH,
    AI_KEYWORDS_EN,
    AI_FILTER_PROMPT_ZH,
    AI_FILTER_PROMPT_EN,
)


def _article_to_filter_item(index: int, article: Article) -> str:
    """Format a single article for the filter prompt."""
    parts = [f"[{index}] {article.title}"]
    if article.source:
        parts.append(f"    来源: {article.source}")
    desc = (article.description or "")[:200]
    if desc:
        parts.append(f"    摘要: {desc}")
    return "\n".join(parts)


def _keyword_filter(articles: list[Article]) -> list[Article]:
    """Fallback keyword-based AI relevance filter."""
    all_keywords = AI_KEYWORDS_ZH + AI_KEYWORDS_EN
    results = []
    for article in articles:
        text = f"{article.title} {article.description or ''}".lower()
        if any(kw.lower() in text for kw in all_keywords):
            results.append(article)
    return results


def _api_filter(articles: list[Article], batch_size: int = 50) -> list[Article]:
    """AI API-based batch classification for AI relevance."""
    from .ai_summarizer import _get_client, _chat_completion

    client = _get_client()
    language = os.environ.get("REPORT_LANGUAGE", "zh")

    results = []
    total_batches = (len(articles) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        start = batch_idx * batch_size
        batch = articles[start:start + batch_size]
        print(f"[AI Filter] 🤖 batch {batch_idx + 1}/{total_batches} ({len(batch)} articles)...")

        articles_text = "\n\n".join(
            _article_to_filter_item(i, a) for i, a in enumerate(batch, start=1)
        )
        prompt_template = AI_FILTER_PROMPT_ZH if language == "zh" else AI_FILTER_PROMPT_EN
        prompt = prompt_template.format(articles=articles_text)

        response = _chat_completion(client, prompt, max_tokens=2000)
        if not response:
            print(f"[AI Filter] ⚠️ batch {batch_idx + 1} API failed, using keyword fallback")
            results.extend(_keyword_filter(batch))
            continue

        try:
            json_str = response.strip()
            if json_str.startswith("```"):
                json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            classifications = json.loads(json_str)
            for i, article in enumerate(batch, start=1):
                if classifications.get(str(i), False):
                    results.append(article)
            print(f"[AI Filter] ✅ batch {batch_idx + 1}: {sum(1 for v in classifications.values() if v)} AI articles")
        except (json.JSONDecodeError, ValueError):
            print(f"[AI Filter] ⚠️ batch {batch_idx + 1} JSON parse failed, using keyword fallback")
            results.extend(_keyword_filter(batch))

    return results


def filter_ai_articles(articles: list[Article]) -> tuple[list[Article], list[Article]]:
    """Split articles into (ai_articles, non_ai_articles).

    Articles in AI_DIGEST_DIRECT_CATEGORIES are always included in ai_articles.
    All other articles are classified by AI API (with keyword fallback).

    Args:
        articles: all processed articles from all sources

    Returns:
        tuple of (ai_relevant_articles, non_ai_articles)
    """
    ai_direct = []
    to_classify = []

    for article in articles:
        if article.category in AI_DIGEST_DIRECT_CATEGORIES:
            ai_direct.append(article)
        else:
            to_classify.append(article)

    print(f"[AI Filter] 📋 {len(ai_direct)} direct AI articles, {len(to_classify)} to classify")

    if not to_classify:
        return ai_direct, []

    # Use API classification if API_KEY is available, else keyword fallback
    if os.environ.get("API_KEY"):
        ai_classified = _api_filter(to_classify)
    else:
        ai_classified = _keyword_filter(to_classify)

    # Split classified articles
    ai_urls = {a.url for a in ai_classified}
    ai_articles = ai_direct + ai_classified
    non_ai_articles = [a for a in to_classify if a.url not in ai_urls]

    print(f"[AI Filter] ✅ result: {len(ai_articles)} AI articles, {len(non_ai_articles)} non-AI articles")
    return ai_articles, non_ai_articles
```

- [ ] **Step 2: Commit**

```bash
git add core/ai_filter.py
git commit -m "feat: add AI content filtering module with API + keyword fallback"
```

---

### Task 3: Create `core/ai_report.py` — AI deep analysis report generator

**Files:**
- Create: `core/ai_report.py`

- [ ] **Step 1: Write the AI deep analysis report generator**

Create `core/ai_report.py`:

```python
"""
AI deep analysis report generator.
Produces the Part I (AI deep digest) section of the unified report.
"""

import json
import os
from datetime import datetime, timezone

from .article import Article
from .config import AI_DEEP_ANALYSIS_PROMPT_ZH, AI_DEEP_ANALYSIS_PROMPT_EN


def _format_articles_for_deep_analysis(articles: list[Article]) -> str:
    """Format articles for the deep analysis prompt."""
    lines = []
    for i, article in enumerate(articles, 1):
        lang_tag = "🇨🇳" if article.language == "zh" else "🇺🇸"
        lines.append(f"{i}. [{lang_tag}] {article.title}")
        lines.append(f"   来源: {article.source}")
        lines.append(f"   链接: {article.url}")
        desc = (article.description or "")[:300]
        if desc:
            lines.append(f"   摘要: {desc}")
        full = (article.full_text or "")[:500]
        if full:
            lines.append(f"   正文片段: {full}")
        source_type = article.category
        if source_type:
            lines.append(f"   来源类型: {source_type}")
        lines.append("")
    return "\n".join(lines)


def _extract_section(report: str, heading: str) -> str:
    """Extract a section from markdown by heading name."""
    lines = report.split("\n")
    capture = False
    section_lines = []
    for line in lines:
        if line.strip().startswith("#") and heading in line:
            capture = True
            section_lines.append(line)
            continue
        if capture:
            if line.strip().startswith("#") and heading not in line:
                break
            section_lines.append(line)
    return "\n".join(section_lines).strip()


def generate_ai_report(ai_articles: list[Article], language: str = "zh") -> str:
    """Generate Part I: AI deep analysis section.

    Uses the AI API to produce a deep analysis with hot topics,
    trend insights, and detailed coverage tables.

    Args:
        ai_articles: list of AI-relevant Article objects
        language: "zh" or "en"

    Returns:
        Markdown string for the AI deep analysis section
    """
    if not ai_articles:
        return ""

    language = language or os.environ.get("REPORT_LANGUAGE", "zh")

    # If no API_KEY, generate a simple listing as fallback
    if not os.environ.get("API_KEY"):
        return _generate_ai_listing_fallback(ai_articles, language)

    from .ai_summarizer import _get_client, _chat_completion

    client = _get_client()
    articles_text = _format_articles_for_deep_analysis(ai_articles)

    prompt_template = AI_DEEP_ANALYSIS_PROMPT_ZH if language == "zh" else AI_DEEP_ANALYSIS_PROMPT_EN
    prompt = prompt_template.format(articles=articles_text)

    print(f"[AI Report] 🤖 Generating deep analysis for {len(ai_articles)} AI articles...")
    response = _chat_completion(client, prompt, max_tokens=6000)

    if not response:
        print("[AI Report] ⚠️ Deep analysis failed, using listing fallback")
        return _generate_ai_listing_fallback(ai_articles, language)

    print("[AI Report] ✅ Deep analysis generated")
    return response.strip()


def _generate_ai_listing_fallback(ai_articles: list[Article], language: str) -> str:
    """Simple fallback listing when AI API is unavailable."""
    lines = []

    if language == "zh":
        lines.append("### 🤖 AI 相关文章")
        lines.append("")
        lines.append("| # | 文章 | 来源 | 分类 |")
        lines.append("|---:|------|------|------|")
    else:
        lines.append("### 🤖 AI-Related Articles")
        lines.append("")
        lines.append("| # | Article | Source | Category |")
        lines.append("|---:|------|------|------|")

    for i, article in enumerate(ai_articles, 1):
        title = article.title.replace("|", "\\|").replace("\n", " ")
        url = article.url.replace("|", "\\|")
        source = article.source.replace("|", "\\|")
        cat = article.category.replace("|", "\\|")
        lines.append(f"| {i} | [**{title}**]({url}) | *{source}* | {cat} |")

    lines.append("")
    return "\n".join(lines)


def build_ai_section(ai_articles: list[Article], language: str = "zh") -> str:
    """Build the complete Part I: AI Deep Digest section.

    Wraps the deep analysis in a part header with article count.

    Args:
        ai_articles: list of AI-relevant Article objects
        language: "zh" or "en"

    Returns:
        Complete Part I markdown string
    """
    if not ai_articles:
        return ""

    count = len(ai_articles)

    if language == "zh":
        header = f"# Part I: 🤖 AI 深度日报 ({count} 篇)"
    else:
        header = f"# Part I: 🤖 AI Deep Digest ({count} articles)"

    deep_analysis = generate_ai_report(ai_articles, language)

    return f"{header}\n\n{deep_analysis}"
```

- [ ] **Step 2: Commit**

```bash
git add core/ai_report.py
git commit -m "feat: add AI deep analysis report generator module"
```

---

### Task 4: Modify `core/report_generator.py` — add non-AI section builder

**Files:**
- Modify: `core/report_generator.py`

- [ ] **Step 1: Add `build_non_ai_section` function**

Add the following function at the end of `core/report_generator.py` (before the final blank line):

```python
def build_non_ai_section(non_ai_articles, report_language="zh"):
    """Build Part II: non-AI tech news section.

    Reuses the existing table-based report format for articles
    that are not AI-related.

    Args:
        non_ai_articles: list of Article objects (non-AI)
        report_language: "zh" or "en"

    Returns:
        Markdown string for Part II
    """
    if not non_ai_articles:
        return ""

    from collections import OrderedDict
    from .config import CATEGORY_ORDER, get_category_display, normalize_category

    count = len(non_ai_articles)

    if report_language == "zh":
        lines = [f"# Part II: 💻 科技动态 ({count} 条)"]
    else:
        lines = [f"# Part II: 💻 Tech Updates ({count} items)"]

    lines.append("")

    # Group by category using same logic as Skill-mode report
    groups = OrderedDict()
    for cat in CATEGORY_ORDER:
        if cat not in ("ai_ml", "ai_tools"):  # Skip AI categories in Part II
            groups[cat] = []
    groups["其他"] = []

    hn_items = []
    for update in non_ai_articles:
        source_cat = normalize_category(update.category)
        if source_cat == "hacker_news":
            hn_items.append(update)
            continue
        if source_cat in ("ai_ml", "ai_tools"):
            continue  # Should not happen, but safety check
        if source_cat not in groups:
            source_cat = "其他"
        groups[source_cat].append(update)

    # Output category tables
    count_unit = "条" if report_language == "zh" else "items"
    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue

        cat_display = get_category_display(cat)
        lines.append(f"## {cat_display} ({len(cat_updates)} {count_unit})")
        lines.append("")

        # Check if any article has a description
        has_desc = any(u.description for u in cat_updates)
        if has_desc:
            summary_header = "摘要" if report_language == "zh" else "Summary"
            lines.append(f"| # | {'文章' if report_language == 'zh' else 'Article'} | {'来源' if report_language == 'zh' else 'Source'} | {summary_header} |")
            lines.append("|---:|------|------|------|")
        else:
            lines.append(f"| # | {'文章' if report_language == 'zh' else 'Article'} | {'来源' if report_language == 'zh' else 'Source'} |")
            lines.append("|---:|------|------|")

        for i, update in enumerate(cat_updates, 1):
            summary_text = ""
            if update.description:
                clean_desc = re.sub(r'<[^>]+>', '', update.description.strip())
                if len(clean_desc) > 150:
                    clean_desc = clean_desc[:150] + "..."
                summary_text = clean_desc
            lines.append(_article_table_row(i, update.title, update.url, update.source, summary_text))

        lines.append("")

    # Hacker News
    if hn_items:
        hn_label = "Hacker News 热门" if report_language == "zh" else "Hacker News Trending"
        lines.append(f"## {hn_label} ({len(hn_items)} {count_unit})")
        lines.append("")

        lines.append(f"| # | {'文章' if report_language == 'zh' else 'Article'} | {'热度' if report_language == 'zh' else 'Stats'} |")
        lines.append("|---:|------|------|")

        for i, item in enumerate(hn_items, 1):
            stats_parts = []
            if item.hn_points is not None:
                stats_parts.append(f"🔥 {item.hn_points}")
            if item.hn_comments is not None:
                stats_parts.append(f"💬 {item.hn_comments}")
            stats_str = " · ".join(stats_parts)

            title_cell = f"[**{_escape_pipe(item.title)}**]({_escape_pipe(item.url)})"
            lines.append(f"| {i} | {title_cell} | {_escape_pipe(stats_str)} |")

        lines.append("")

    return "\n".join(lines)
```

- [ ] **Step 2: Commit**

```bash
git add core/report_generator.py
git commit -m "feat: add non-AI section builder for Part II of unified report"
```

---

### Task 5: Modify `core/pipeline.py` — integrate two-part report generation

**Files:**
- Modify: `core/pipeline.py`

- [ ] **Step 1: Add `build_unified_report` function**

Add the following function to `core/pipeline.py`, after the `build_merged_report` function (around line 184):

```python
def build_unified_report(ai_articles, non_ai_articles, now, language="zh"):
    """Build a two-part unified report: AI deep analysis + non-AI tech news.

    Args:
        ai_articles: list of Article objects (AI-relevant)
        non_ai_articles: list of Article objects (non-AI)
        now: datetime with timezone
        language: "zh" or "en"

    Returns:
        Markdown string of the complete unified report
    """
    from .ai_report import build_ai_section
    from .report_generator import build_non_ai_section

    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    ai_count = len(ai_articles)
    non_ai_count = len(non_ai_articles)
    total = ai_count + non_ai_count

    if language == "zh":
        header = f"# 📰 Daily Digest — {date_str}\n\n"
        header += f"> 🤖 AI 深度分析 {ai_count} 篇 · 💻 科技动态 {non_ai_count} 条 · 共 {total} 篇\n\n"
        header += f"> ⏰ 生成时间 {time_str} UTC\n"
    else:
        header = f"# 📰 Daily Digest — {date_str}\n\n"
        header += f"> 🤖 AI Deep Analysis {ai_count} articles · 💻 Tech Updates {non_ai_count} items · Total {total}\n\n"
        header += f"> ⏰ Generated at {time_str} UTC\n"

    header += "\n---\n\n"

    # Part I: AI Deep Digest
    ai_section = build_ai_section(ai_articles, language)

    # Part II: Non-AI Tech Updates
    non_ai_section = build_non_ai_section(non_ai_articles, language)

    # Combine parts
    parts = []
    if ai_section:
        parts.append(ai_section)
    if non_ai_section:
        parts.append(non_ai_section)

    if not parts:
        return ""

    return header + "\n\n---\n\n".join(parts)
```

- [ ] **Step 2: Modify the main `main.py` flow to use unified report when all sources are processed**

In `main.py`, after the merged report is built (around line 119-121), add AI filtering and rebuild as unified report. Modify the section from line 115 to line 122:

Replace:
```python
    if not sections:
        print("\n⚠️ no updates, nothing to report.")
        return

    from core.config import OUTPUT_DIR
    from core.report_generator import save_report

    now = datetime.now(timezone.utc)
    merged = build_merged_report(sections, now, language)
    filepath = save_report(merged, f"{now.strftime('%Y-%m-%d')}.md", OUTPUT_DIR,
                           report_type="digest", language=language)
```

With:
```python
    if not sections:
        print("\n⚠️ no updates, nothing to report.")
        return

    from core.config import OUTPUT_DIR
    from core.report_generator import save_report

    now = datetime.now(timezone.utc)

    # Try to build unified two-part report (AI deep + non-AI)
    unified = _try_build_unified_report(sections, now, language)
    if unified:
        report_content = unified
    else:
        report_content = build_merged_report(sections, now, language)

    filepath = save_report(report_content, f"{now.strftime('%Y-%m-%d')}.md", OUTPUT_DIR,
                           report_type="digest", language=language)
```

- [ ] **Step 3: Add `_try_build_unified_report` helper to `main.py`**

Add this function to `main.py` before `main()`:

```python
def _try_build_unified_report(sections, now, language):
    """Attempt to build a unified two-part report from section data.

    Returns None if API_KEY is not set (falls back to merged report).
    """
    if not os.environ.get("API_KEY"):
        return None

    from core.article import Article
    from core.ai_filter import filter_ai_articles
    from core.pipeline import build_unified_report

    # Collect all articles from workspace data files
    all_articles = []
    from core.config import WORKSPACE_DIR
    for source_type in ("tech", "podcast", "wechat"):
        data_path = WORKSPACE_DIR / f"{source_type}_updates.json"
        if data_path.exists():
            import json as _json
            with open(data_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            for item in data.get("updates", []):
                try:
                    all_articles.append(Article(**item))
                except Exception:
                    continue

    if not all_articles:
        return None

    print(f"\n🤖 Building unified AI + non-AI report from {len(all_articles)} articles...")
    ai_articles, non_ai_articles = filter_ai_articles(all_articles)

    if not ai_articles and not non_ai_articles:
        return None

    return build_unified_report(ai_articles, non_ai_articles, now, language)
```

- [ ] **Step 4: Add `import os` to `main.py` if not already present**

`os` is already imported in `main.py` (line 4), so no change needed.

- [ ] **Step 5: Commit**

```bash
git add core/pipeline.py main.py
git commit -m "feat: integrate two-part unified report into pipeline"
```

---

### Task 6: Modify `core/pipeline.py` finalize flow for two-part report

**Files:**
- Modify: `core/pipeline.py` (the `finalize_reports` function)

- [ ] **Step 1: Update `finalize_reports` to generate unified report**

Replace the `finalize_reports` function (lines 265-289) with:

```python
def finalize_reports(source, language="zh"):
    """--finalize mode: read sub-agent summaries from workspace/ and build final reports."""
    from .config import OUTPUT_DIR
    from .report_generator import save_report

    now = datetime.now(timezone.utc)

    sections = []
    for src in ("tech", "podcast", "wechat"):
        if source in (src, "all"):
            report = _finalize_source(src, language)
            if report:
                sections.append(report)

    if not sections:
        print("⚠️ no reports to generate.")
        return

    # Try unified two-part report
    unified = None
    if os.environ.get("API_KEY"):
        from .article import Article
        from .ai_filter import filter_ai_articles

        all_articles = []
        for src in ("tech", "podcast", "wechat"):
            if source in (src, "all"):
                data = _load_workspace_data(src)
                if data:
                    for item in data.get("updates", []):
                        try:
                            all_articles.append(Article(**item))
                        except Exception:
                            continue

        if all_articles:
            print(f"\n🤖 Building unified AI + non-AI report from {len(all_articles)} articles...")
            ai_articles, non_ai_articles = filter_ai_articles(all_articles)
            unified = build_unified_report(ai_articles, non_ai_articles, now, language)

    if unified:
        merged = unified
    else:
        merged = build_merged_report(sections, now, language)

    filepath = save_report(merged, f"{now.strftime('%Y-%m-%d')}.md", OUTPUT_DIR,
                           report_type="digest", language=language)

    print("\n" + "=" * 60)
    print(f"✅ Finalize done! report: {filepath}")
    print("=" * 60 + "\n")
```

- [ ] **Step 2: Commit**

```bash
git add core/pipeline.py
git commit -m "feat: update finalize_reports to support unified two-part report"
```

---

### Task 7: Integration test — dry run with `--limit`

**Files:**
- No new files

- [ ] **Step 1: Run a limited test to verify the pipeline doesn't crash**

```bash
cd E:/Projects/AI/DailyDigest
python main.py --source tech --limit 3 --hours 48
```

Expected: Pipeline runs without errors. If `API_KEY` is set, should see `[AI Filter]` log lines and a unified two-part report. If not set, falls back to existing merged report behavior.

- [ ] **Step 2: Verify the output file exists and has expected structure**

```bash
ls -la daily-digests/ | head -5
```

Check that the latest report file exists and contains either "Part I" + "Part II" headings (API mode) or the existing merged format (no API mode).

- [ ] **Step 3: If issues found, fix and commit**

Fix any issues discovered during testing and commit with descriptive message.

---

## Self-Review

**1. Spec coverage:**
- AI content filtering with direct categories + AI classification → Task 2
- Deep analysis report (hot topics, trend insights, sub-domain coverage) → Task 3
- Non-AI section builder → Task 4
- Pipeline integration (both main flow and finalize flow) → Tasks 5, 6
- Configuration constants and prompts → Task 1
- Error handling (keyword fallback, listing fallback) → Tasks 2, 3
- No workflow changes needed → covered (output file name unchanged)

**2. Placeholder scan:** No TBD, TODO, or vague steps found. All code is complete.

**3. Type consistency:**
- `filter_ai_articles` returns `tuple[list[Article], list[Article]]` — consistent across Tasks 2, 5, 6
- `build_ai_section` takes `(list[Article], str)` → returns `str` — consistent
- `build_non_ai_section` takes `(list, str)` → returns `str` — consistent
- `build_unified_report` takes `(list[Article], list[Article], datetime, str)` → returns `str` — consistent
- All functions use `Article` from `core.article` — consistent
