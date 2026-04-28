# Split Podcast and Tech Reports — Design Spec

**Date:** 2026-04-28
**Status:** Approved

## Problem

When running `--source all` or `--finalize` with `source=all`, podcast episodes and tech news articles are merged into a single report file. The user wants two completely independent daily reports.

Additionally, the current podcast report is a simple Markdown table with no theme grouping. The user wants it upgraded to a theme-grouped briefing format similar to the tech report, but tailored for audio content.

## Design

### 1. Output Routing Split

**`main.py` — `--source all` mode:**

Currently, `main.py` collects report sections from each runner and calls `build_merged_report()` to combine them into one file saved to `tech/`. Change to:

- Run tech and podcast pipelines as before (they are already architecturally separate)
- Save each report to its own output directory independently
- Tech+WeChat → `daily-digests/tech/YYYY-MM-DD.md`
- Podcast → `daily-digests/podcast/YYYY-MM-DD.md`
- Remove the `build_merged_report()` call for `--source all`
- Print both file paths on completion

**`core/pipeline.py` — `finalize_reports()`:**

Currently, `try_build_unified_report()` loads workspace data from all sources (tech, podcast, wechat) and merges them into one LLM pipeline. Change to:

- When `source == "all"`, call separate finalize paths for tech and podcast
- Tech path: existing `try_build_unified_report()` but exclude podcast workspace data
- Podcast path: new `try_build_podcast_report()` function
- Each saves to its own output directory

**`try_build_unified_report()` modification:**

The function currently iterates over `("tech", "podcast", "wechat")` and loads workspace data from all three. Change the source filtering so that podcast data is never loaded in this function. Podcast gets its own `try_build_podcast_report()`.

### 2. Podcast Theme-Grouped Report

**New function `build_podcast_briefing_report()` in `core/podcast_utils.py`:**

Uses the same clustering infrastructure as tech but with podcast-specific rendering.

**Report structure:**

```
# 🎙️ AI 播客日报 — YYYY-MM-DD

> 扫描 X 个播客 · Yh 窗口 · 发现 Z 个更新

## TL;DR
(LLM-generated daily podcast highlights)

## 今日热点主题

### 主题 1: xxx
- 🎧 [单集标题](url) — 播客名 · #排名
  > 单集摘要/要点
- 🎧 [单集标题](url) — 播客名 · #排名

### 主题 2: xxx
...

## 值得关注的单集
- 🎧 [单集标题](url) — 播客名
  > 简短摘要

## 全部更新
| # | 节目 | 播客 | 排名 | 分类 | 摘要 |
```

**Differences from tech report:**

- No Data Dashboard section
- No Trends section
- No AI/non-AI split (all content is podcast recommendations)
- Uses 🎧 emoji for episode entries
- Retains the existing table format as a "全部更新" (all updates) appendix section
- Theme interpretation uses podcast-specific prompts

**Implementation:**

- `core/podcast_utils.py`: Add `build_podcast_briefing_report()`
- `core/renderer.py`: Add `_render_podcast_briefing_markdown()`
- Both reuse existing `embedding_cluster` and `briefing` infrastructure
- Theme interpretation via `llm_classify.interpret_themes_with_llm()` with podcast-specific prompt

**Pipeline integration in `run_podcast()`:**

When `API_KEY` is set, after scoring and clustering:
1. Embed articles and cluster (same as tech)
2. Interpret themes with podcast-specific prompt
3. Build podcast briefing report (new function)
4. Save to `podcast/` directory

When no `API_KEY`, keep current simple table format (no change to Skill mode).

### 3. Podcast-Specific LLM Prompts

**New file `config/prompts/podcast_theme.py`:**

Theme interpretation prompt focused on:
- Discussion content and viewpoints (not news events)
- Guest expertise and conversation depth
- Practical takeaways for listeners
- Chinese output (consistent with existing reports)

**Scoring criteria for podcast episodes:**

Podcast scoring uses different dimensions than tech news:
- Content depth (unique insights vs. surface-level discussion)
- Timeliness (is the topic currently trending)
- Audience breadth (AI practitioners vs. general tech listeners)

The existing `score_and_filter_articles()` can be reused with a podcast-specific system prompt that adjusts the scoring criteria.

**TL;DR and highlights generation:**

Add podcast-specific TL;DR generation in `llm_services.py`, similar to the existing tech TL;DR but focused on audio content highlights.

### 4. `try_build_podcast_report()` Function

New function in `core/pipeline.py` for finalize mode:

1. Load podcast workspace data from `workspace/podcast_updates.json`
2. If `API_KEY` set: run embedding clustering + LLM theme interpretation + build podcast briefing report
3. If no `API_KEY`: use sub-agent summaries (existing Skill mode)
4. Save report to `daily-digests/podcast/YYYY-MM-DD.md`

## Files Changed

| File | Change | Lines (est.) |
|------|--------|-------------|
| `main.py` | Route `--source all` to separate save calls | ~30 |
| `core/pipeline.py` | Split finalize, add `try_build_podcast_report()` | ~60 |
| `core/podcast_utils.py` | Add `build_podcast_briefing_report()` | ~100 |
| `core/renderer.py` | Add `_render_podcast_briefing_markdown()` | ~80 |
| `config/prompts/podcast_theme.py` | New file, podcast theme interpretation prompt | ~50 |
| `core/llm_services.py` | Podcast TL;DR/highlights adaptation | ~20 |

## Non-Goals

- Custom podcast template for Skill mode (only API mode gets the upgrade)
- Separate feed health tracking for podcast (already separate)
- Changes to `--source tech`, `--source podcast`, or `--source wechat` individual modes
- Modifying the podcast fetch/dedup pipeline
