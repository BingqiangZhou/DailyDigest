# Prompt Engineering Techniques for Content Generation

Reference guide for the DailyDigest AI content generation pipeline.

## 1. Chain-of-Thought (CoT) Prompting

**When to use:** Classification, summarization, deep analysis — any task requiring structured reasoning.

**Pattern:**
```
Step 1: Read all the input data
Step 2: Identify key patterns/themes
Step 3: Apply criteria to each item
Step 4: Generate output following the required format
Self-check: Verify [specific quality criteria]
```

**Applied in DailyDigest:**
- AI filter: Step 1 identify topic -> Step 2 apply criteria -> Step 3 output JSON -> verify no false positives
- Deep analysis: Step 1 identify themes -> Step 2 cluster articles -> Step 3 write structured report -> verify citations
- TL;DR: Step 1 rank by significance -> Step 2 write concise bullets -> verify accuracy

## 2. Prompt Chaining

**When to use:** Complex multi-step tasks that exceed a single prompt's capacity.

**Pattern:** Output of step N becomes input of step N+1.
```
Step 1 (Classify): articles -> {ai: bool}
Step 2 (Cluster): ai_articles -> topic_groups
Step 3 (Summarize): topic_groups + articles -> structured_summary
Step 4 (Critique): summary + criteria -> issues
Step 5 (Refine): summary + issues -> improved_summary
```

**Applied in DailyDigest:**
- `filter_ai_articles()` -> `cluster_articles()` -> `generate_ai_report()` -> `generate_with_critique()`
- Each step enriches context for the next.

## 3. Self-Verification / Self-Critique

**When to use:** High-stakes output where accuracy matters (deep analysis, executive summary).

**Pattern:** After generating output, ask the model to verify it.
```
## Self-check
Before finalizing, verify:
1. Every cited article exists in the source list
2. No fabricated statistics or quotes
3. Trend claims are supported by at least 2 articles
4. No redundant information across sections
```

**Applied in DailyDigest:**
- Deep analysis prompt includes inline self-check
- `generate_with_critique()` performs explicit draft-critique-refine cycle for key sections

## 4. Generated Knowledge Prompting

**When to use:** When the model needs to "warm up" before generating structured output.

**Pattern:** Ask the model to identify key information first, then use it.
```
Before writing the report:
1. Identify the 3-5 most significant AI developments in these articles
2. Note which articles support each development
3. Then write the structured report using this analysis
```

**Applied in DailyDigest:**
- Deep analysis prompt starts with "identify top themes" before requesting the full report structure.

## 5. Task-Specific Parameter Tuning

Different generation tasks need different creativity/precision tradeoffs.

| Task Profile | Temperature | top_p | max_tokens | Rationale |
|---|---|---|---|---|
| `classify` | 0.1 | 0.9 | 2000 | Max precision — binary true/false decisions |
| `topic_cluster` | 0.2 | 0.9 | 2000 | High precision — grouping requires consistency |
| `tldr` | 0.3 | 0.9 | 500 | Concise — needs focus, not creativity |
| `critique` | 0.3 | 0.9 | 1000 | Precise — identify specific issues |
| `summarize` | 0.5 | 0.9 | 4000 | Balanced — factual but fluid |
| `deep_analysis` | 0.7 | 0.9 | 6000 | Creative — trend insight requires reasoning |

## 6. Structured Output Enforcement

**Pattern:** Specify exact JSON schema + say "output ONLY valid JSON, nothing else."

**Applied in DailyDigest:**
- AI filter: `{"id_1": true, "id_2": false}`
- Podcast summary: `{"url1": "summary1"}`
- WeChat summary: `{"summaries": [{"article_url": "...", "ai_summary": "..."}]}`
- Topic cluster: `{"cluster_id": "...", "theme": "...", "articles": [...]}`

**Tip:** Wrap JSON extraction with `strip()` + markdown fence removal for robustness.

## 7. Editorial Voice Anchoring

**Pattern:** Define persona + banned terms + approved patterns before content instructions.

**Applied in DailyDigest:**
- See `knowledge/editorial-voice.md` for the full persona definition
- Every prompt includes a one-line voice anchor: "You are a senior AI industry analyst writing for senior engineers and technology leaders."

## 8. RAG-like Context Grounding

**Pattern:** Always include the actual source material in the prompt. Never ask the model to "recall" facts.

**Applied in DailyDigest:**
- All prompts include the full article list with titles, sources, URLs, descriptions, and full-text snippets
- Deep analysis prompt includes up to 500 chars of full text per article
- Topic clustering enriches analysis with cross-article context
