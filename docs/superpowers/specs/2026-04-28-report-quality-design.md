---
title: Report Quality Deep-Dive Design
date: 2026-04-28
status: approved
---

# Report Quality Deep-Dive: Prompt-Based Analysis Enhancement

## Problem

Daily digest reports (e.g., 2026-04-27, 2026-04-28) read like link lists rather than analytical briefings:

1. **Duplicate theme titles** — multiple consecutive sections named "模型与平台"
2. **Empty summaries** — boilerplate like "今日该主题有多篇相关更新" carries zero information
3. **Raw English highlights** — no LLM synthesis, just original article titles
4. **Low analysis depth** — 1 sentence per theme vs 3-4 links; ~1:4 analysis-to-link ratio
5. **Mechanical trends** — source distribution counts, not real trend observations

Root cause: fallback heuristics produce minimal text, and LLM prompts are too conservative (short output limits, shallow instructions).

## Approach

Prompt-based deepening (Approach A): redesign LLM prompts, adjust task profiles, and make targeted rendering changes. Minimal structural refactoring — focus on content quality.

## Design

### 1. Theme Title Generation (new LLM call)

**File**: `core/llm_services.py` — new function `generate_theme_titles(themes)`

- Called for clustered themes (cluster_size > 1) only
- Receives top 3 article titles per cluster
- Outputs a 4-8 character Chinese title per theme
- Profile: `brief_summary` (temp 0.3, max_tokens 200)
- Fallback: current `_clean_theme_title` behavior, but with dedup suffix when same fallback name appears multiple times

**Dedup logic** (in `core/briefing.py` `_build_theme_groups`):
- Track how many times each fallback name appears
- When a duplicate is detected, append a keyword from the lead article's title (first significant noun phrase, ≤6 chars)
- Example: "模型与平台" → "模型与平台 · OpenAI 动态"

### 2. TL;DR Opening Paragraph (new section)

**File**: `core/llm_services.py` — new function `generate_tldr(themes)`

- Placed between stats line and "今日要点" in the rendered output
- Receives all theme titles + top 2 article titles per theme
- Outputs 60-120 char Chinese paragraph answering "what happened in AI today"
- Profile: `brief_summary` (max_tokens 300)
- Fallback: empty string (section not rendered)

**Renderer change** (`core/renderer.py` `_render_briefing_markdown`):
- New section `## 🎯 今日速览` rendered when `briefing_data["tldr"]` is non-empty
- Rendered as a plain paragraph, not a bullet list

### 3. Highlights Rewrite

**File**: `core/llm_services.py` — redesign `generate_briefing_highlights(themes)`

Current prompt: "只写事实，不写分析过程" → produces shallow rephrasing.

New prompt:
- Request synthesized Chinese bullet points, each 15-30 chars
- Format: `【领域】要点描述`
- Instruction: "用中文改写，综合多篇信息，不要照搬英文标题"
- Input: per theme, 3 article titles + source + HN points
- Same profile: `summarize` (max_tokens 4000)

### 4. Theme Summaries Deepening

**File**: `core/llm_services.py` — redesign `generate_theme_summaries(themes)`

Current: "120-220 字的简报综述" → too short, often superficial.

Changes:
- New prompt structure per theme: "1) 这件事是什么 2) 为什么值得关注 3) 可能的影响或后续方向"
- Output length: 200-350 chars per theme
- Explicitly request "综合多篇文章信息，不要逐篇复述"
- Summary field in JSON output supports multi-line (bullet points + paragraph)

**Profile change**: `summarize` max_tokens from 4000 → 6000

**Fallback improvement** (`core/briefing.py` `_compose_theme_summary`):
- Extract first sentence from each article description (not full description)
- Join with " · " instead of "；"
- When no descriptions available, list article keywords: "涉及：xxx、yyy、zzz" instead of boilerplate

### 5. Trends Upgrade

**File**: `core/llm_services.py` — redesign `generate_briefing_trends(themes, brief_items)`

Current: max_tokens=400, context = only titles → mechanical output.

Changes:
- New prompt: each trend in format `【趋势方向】观察描述（支撑证据）`
- Each trend 50-100 chars with specific evidence citations
- Instruction: "寻找跨主题的关联，而非罗列单个主题"
- Context: theme titles + 2-3 sentence summaries (from theme summary or fallback) + top brief_items

**Profile change**: `trends` max_tokens from 400 → 1500

**Fallback improvement** (`core/briefing.py` `_fallback_trends`):
- Replace source distribution listing with "今日热词" extracted from article titles
- Cross-source trend uses actual cross-source theme titles (not duplicated fallback names)

### 6. Rendering Layer Changes

**File**: `core/renderer.py` `_render_briefing_markdown`

**Report section order** changes from:
```
要点 → 动态 → 重点科技 → 科技简讯 → 趋势 → 数据
```
to:
```
今日速览 (new TL;DR) → 要点 → 动态 → 重点科技 → 科技简讯 → 趋势 → 数据
```

**Theme rendering** changes from:
```markdown
### 一、模型与平台
[1-sentence summary]
**参考：**
- [link1] — *source*
- [link2] — *source*
```
to:
```markdown
### 一、[LLM-generated title]
[overview paragraph, 2-3 sentences]

- key point 1
- key point 2

> 📎 相关：[article1](url)（source）、[article2](url)（source）
```

Key visual changes:
- Links moved to blockquote `> 📎 相关：`, visually de-emphasized
- Bullet points (insights) come before links
- Multiple links on one line separated by "、" to save vertical space

### 7. Orchestration Changes

**File**: `core/report_builder.py` `build_unified_report`

New call chain in `render_briefing_v2` (replaces `render_briefing`):

```
build_briefing_data()
  → generate_theme_titles(themes)    # new, must run first
  → generate_tldr(themes)            # new
  → generate_briefing_highlights(themes)
  → generate_theme_summaries(themes)  # uses generated titles
  → generate_briefing_trends(themes, brief_items)
  → merge results into briefing_data
  → render markdown
```

All LLM calls in one function, ordered by dependency (titles before summaries since summaries use titles as context).

### 8. Task Profile Changes

**File**: `core/llm.py` `TASK_PROFILES`

| Profile | Current max_tokens | New max_tokens |
|---------|-------------------|----------------|
| `summarize` | 4000 | 6000 |
| `trends` | 400 | 1500 |

No other profile changes.

### Cost Estimate

| Call | Est. tokens (in+out) | Profile |
|------|---------------------|---------|
| theme_titles | ~700 | brief_summary |
| tldr | ~1100 | brief_summary |
| highlights | ~2000 | summarize |
| theme_summaries | ~6000 | summarize |
| trends | ~2500 | trends |
| **Total** | **~12300** | — |

Current: ~5000 tokens. Increase: ~2.4x for significantly better quality.

## Files Changed

| File | Change |
|------|--------|
| `core/llm.py` | Update TASK_PROFILES: summarize→6000, trends→1500 |
| `core/llm_services.py` | Add `generate_theme_titles`, `generate_tldr`; rewrite `generate_briefing_highlights`, `generate_theme_summaries`, `generate_briefing_trends`; create `render_briefing_v2` |
| `core/briefing.py` | Fix `_build_theme_groups` dedup suffix; improve `_compose_theme_summary` fallback; improve `_fallback_trends` |
| `core/renderer.py` | Update `_render_briefing_markdown` for new section order and theme rendering format |
| `core/report_builder.py` | Update `build_unified_report` to call `render_briefing_v2` |
| `tests/test_integration.py` | Update any tests affected by output format changes |

## Out of Scope

- Structural report redesign (section architecture changes beyond rendering)
- Non-API mode (Skill mode) improvements
- Feed fetching or editorial pipeline changes
- WeChat article format changes
