# AI-Focused Digest Design

**Date**: 2026-04-05
**Status**: Draft

## Summary

Transform the daily digest report into a two-part unified document: Part I is an AI-focused deep analysis section with editorial commentary and trend insights; Part II retains the existing format for non-AI tech news. Content is sourced from all four channels (RSS, podcasts, WeChat, YouTube) with AI-relevance filtering applied to podcasts, WeChat, and YouTube.

## Requirements

- One report file per day (`daily-digests/YYYY-MM-DD.md`), same as today
- Part I: AI deep analysis with hot topics, trend insights, and detailed coverage by AI sub-domain
- Part II: Non-AI tech news in the current table format
- AI content sourced from: `ai_ml` + `ai_tools` tech categories (direct inclusion), plus AI-relevant articles from podcasts, WeChat, YouTube, and other tech categories (via AI classification)
- Generated automatically alongside the existing daily pipeline
- No CLI changes required; runs as part of the normal `--source all` flow

## Design

### 1. AI Content Filtering (`core/ai_filter.py`)

**New module** providing `filter_ai_articles(articles: list[Article]) -> list[Article]`.

**Filtering logic**:
- Articles in `ai_ml` and `ai_tools` categories are directly included (no AI call needed)
- Articles from podcasts, WeChat, YouTube, and other tech categories are batch-classified by AI
- Classification prompt: given article title + summary, determine if directly related to AI/ML/LLM/AI applications/AI chips. Return `{id: boolean}`.
- Batch size: 50 articles per API call to minimize overhead
- Threshold: lenient — prefer inclusion over exclusion

**Function signature**:
```python
def filter_ai_articles(articles: list[Article], ai_client) -> list[Article]:
    """Filter articles relevant to AI from a mixed list."""
```

### 2. AI Deep Analysis Report (`core/ai_report.py`)

**New module** providing `generate_ai_report(ai_articles: list[Article], ai_client, language: str) -> str`.

Generates the Part I markdown string with:
- TL;DR (3-5 sentence core AI summary)
- Hot Topics (2-3 key stories with editorial commentary on why they matter)
- Trend Insights (cross-article pattern recognition)
- Detailed Coverage tables grouped by AI sub-domain:
  - Foundation Models & Research
  - AI Tools & Applications
  - AI Hardware & Infrastructure
  - Industry News & Opinions
- AI Podcast Highlights (from podcast sources)
- AI WeChat Highlights (from WeChat sources)

**AI prompts**:
- Uses a "AI industry analyst" persona for the deep analysis
- Requests insight extraction (not simple summarization) for each article
- Trend insights prompt explicitly asks for cross-article patterns

### 3. Report Structure

The unified report has two top-level parts:

```markdown
# Daily Digest — YYYY-MM-DD

## TL;DR
(global summary covering both AI and non-AI)

---

# Part I: AI Deep Digest

## Hot Topics
## Trend Insights
## Detailed Coverage
### Foundation Models & Research
### AI Tools & Applications
### AI Hardware & Infrastructure
### Industry News & Opinions
## AI Podcast Highlights
## AI WeChat Highlights

---

# Part II: Tech Updates

## [existing tech category tables]
## Hacker News Highlights
```

### 4. Pipeline Integration

**Modified file**: `core/pipeline.py`

At the end of the finalize step, after the standard report is assembled:
1. Collect all processed Article objects from tech, podcast, and wechat sources
2. Call `filter_ai_articles()` to split into AI-relevant and non-AI sets
3. Call `generate_ai_report()` for the AI part (deep analysis)
4. Generate the non-AI part using existing report generation logic
5. Merge into the two-part unified report

**Modified file**: `core/report_generator.py`

The report builder needs to support the two-part structure. The existing `build_tech_report()` handles the non-AI portion. A new `build_ai_section()` calls into `ai_report.py`.

### 5. Configuration (`core/config.py`)

Add:
- `AI_DIGEST_CATEGORIES`: list of categories for direct inclusion (`["ai_ml", "ai_tools"]`)
- AI filter prompt template
- AI deep analysis prompt templates (hot topics, trends, detailed coverage)

### 6. Files Changed

| File | Action | Purpose |
|------|--------|---------|
| `core/ai_filter.py` | New | AI-relevance classification of articles |
| `core/ai_report.py` | New | Deep analysis report generation for AI content |
| `core/pipeline.py` | Modify | Integrate AI filtering + dual-part report at finalize |
| `core/report_generator.py` | Modify | Support two-part report structure |
| `core/config.py` | Modify | Add AI digest config constants and prompt templates |
| `core/ai_summarizer.py` | Modify | Add batch classification prompt type |

### 7. Error Handling

- If AI filtering API fails, fall back to keyword-based filtering (predefined AI terms list)
- If deep analysis generation fails, output AI articles in the existing summary format with a note
- The non-AI Part II always generates regardless of Part I status

### 8. GitHub Actions

No workflow changes needed. The AI report file is generated as part of the normal pipeline and will be committed alongside the existing report. If the output filename stays as `YYYY-MM-DD.md`, the workflow handles it automatically.

## Out of Scope

- Separate AI digest email/notification
- Historical AI topic tracking across days
- User-configurable topic filters (beyond AI)
- Changes to data source configuration (feed lists)
