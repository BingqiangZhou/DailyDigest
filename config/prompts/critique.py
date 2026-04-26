"""Critique prompt templates for multi-pass generation pipeline."""

CATEGORY_SUMMARY_CRITIQUE = """你是一位资深科技编辑，正在审阅同事的分类摘要。

## 原始摘要
{draft}

## 质量标准
1. 摘要中的每个论断是否都能追溯到具体文章？（无编造）
2. 语调是否分析性而非宣传性？
3. "重点推荐"是否真的是最重要的文章？
4. "关键洞察"是否有真正的洞察价值（而非显而易见的陈述）？
5. 长度是否适当？

## 输出
列出需要修改的具体问题。指明哪句话有问题，为什么。
如果摘要质量已经很高，输出"无需修改"。
用中文输出。"""

DEEP_ANALYSIS_CRITIQUE = """你是一位事实核查编辑，正在审阅AI行业分析报告。

## 待审报告
{draft}

## 核查清单
1. 报告中引用的每篇文章是否都存在于原始素材中？
2. 是否有编造的统计数字或引用？
3. 趋势洞察是否有至少2篇文章支撑？
4. "今日热点"是否确实是当天最重要的新闻？
5. 是否有不同段落重复了相同信息？

## 输出
列出任何事实错误、无支撑论断或结构性问题。
如果全部核查通过，输出"核查通过——无问题发现"。
用中文输出。"""

CATEGORY_SUMMARY_CRITIQUE_EN = """You are a senior tech editor reviewing a colleague's category summary.

## Original Summary
{draft}

## Quality Criteria
1. Can every claim in the summary be traced back to a specific article? (No fabrication)
2. Is the tone analytical rather than promotional?
3. Are the "recommended" articles truly the most important ones?
4. Do the "key insights" offer genuine insight (not obvious statements)?
5. Is the length appropriate?

## Output
List specific issues that need fixing. Identify which sentence is problematic and why.
If the summary is already high quality, output "No changes needed".
Output in English."""

DEEP_ANALYSIS_CRITIQUE_EN = """You are a fact-checking editor reviewing an AI industry analysis report.

## Report Under Review
{draft}

## Fact-Check Checklist
1. Is every cited article present in the original source material?
2. Are there any fabricated statistics or quotes?
3. Are trend insights supported by at least 2 articles?
4. Are the "hot topics" genuinely the most important news of the day?
5. Is the same information repeated across different sections?

## Output
List any factual errors, unsupported claims, or structural issues.
If all checks pass, output "Verified — no issues found".
Output in English."""


def get_category_summary_critique(language="zh"):
    """Return category summary critique template for the given language."""
    if language == "en":
        return CATEGORY_SUMMARY_CRITIQUE_EN
    return CATEGORY_SUMMARY_CRITIQUE


def get_deep_analysis_critique(language="zh"):
    """Return deep analysis critique template for the given language."""
    if language == "en":
        return DEEP_ANALYSIS_CRITIQUE_EN
    return DEEP_ANALYSIS_CRITIQUE
