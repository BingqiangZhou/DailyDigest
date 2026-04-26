# Content Strategy Reference

## Topic Clustering Approach

### Algorithm: Agglomerative Keyword Clustering

1. **Keyword Extraction:** For each article, extract significant terms from title + description
   - Use existing AI keyword lists (`AI_KEYWORDS_ZH`, `AI_KEYWORDS_EN`) as signal words
   - Tokenize title by splitting on whitespace and punctuation
   - Filter stop words (the, a, an, of, in, 的, 了, 是, etc.)
   - Keep terms that appear in keywords list OR appear multiple times across articles

2. **Pairwise Similarity:** Compute Jaccard similarity between each pair of articles
   - `similarity(A, B) = |keywords(A) ∩ keywords(B)| / |keywords(A) ∪ keywords(B)|`
   - Reuse the Jaccard implementation pattern from `core/dedup.py`

3. **Clustering:** Group articles with similarity > threshold (default 0.3)
   - Simple greedy agglomerative: start with each article as its own cluster
   - Merge clusters if any pair of articles across clusters exceeds threshold
   - Stop when no more merges possible

4. **Cluster Theme Generation:** Derive theme from shared keywords
   - Take intersection of keywords across all articles in cluster
   - Use top 3-5 shared keywords as the theme description

### Output Format
```json
{
  "cluster_id": "c1",
  "theme": "Claude, Anthropic, security",
  "articles": ["url1", "url2", "url3"],
  "size": 3,
  "cross_source": true,
  "score": 0.85
}
```

## Importance Scoring Rubric

Each article and cluster receives an importance score (0.0 - 1.0).

### Factors and Weights

| Factor | Weight | Description |
|---|---|---|
| Source Authority | 0.25 | Known authoritative source (official blogs, tier-1 tech media) vs aggregator |
| Cross-Source Corroboration | 0.25 | Multiple independent sources reporting the same event = higher importance |
| Cluster Size | 0.20 | Larger clusters = more attention on the topic = more important |
| Keyword Signal Strength | 0.15 | Matches high-signal keywords (new model release, breakthrough, acquisition) |
| Novelty | 0.15 | First report of a new development vs. follow-up analysis |

### Source Authority Tiers

**Tier 1 (weight 1.0):** Official company blogs (OpenAI, Anthropic, Google AI, Meta AI), top-tier tech media (Ars Technica, MIT Tech Review), major research publications

**Tier 2 (weight 0.7):** Established tech media (TechCrunch, The Verge, Wired), well-known industry analysts, major open-source project announcements

**Tier 3 (weight 0.4):** General news sites, tech blogs, aggregator sites, Reddit/HN discussions

**Tier 4 (weight 0.2):** Unknown or low-authority sources, social media posts

### High-Signal Keywords (boost importance)

- New model release names (GPT-5, Claude 4, Gemini 3, etc.)
- "breakthrough", "first", "record", "largest"
- Company names in M&A context ("acquires", "acquisition", "merger")
- "benchmark", "SOTA", "state-of-the-art" (with evidence)
- "open-source" + model name
- "regulation", "ban", "restrict" (policy impact)

## Trend Detection Patterns

### What Constitutes a Trend

A trend is identified when **3+ articles from 2+ different sources** discuss the same topic within a **48-hour window**.

### Trend Categories

1. **Technology Convergence:** Multiple companies/teams independently pursue the same technical approach
   - Signal: Similar technical terms across different sources
   - Example: "Three companies released competing RAG frameworks this week"

2. **Technology Transfer:** A technology moves from research to production
   - Signal: Academic paper → product announcement → industry adoption
   - Example: "The attention mechanism variant from [paper] now appears in [product]"

3. **Industry Realignment:** Mergers, acquisitions, partnerships, strategy shifts
   - Signal: Company A + Company B mentioned together across sources
   - Example: "Cloud providers are converging on the same AI infrastructure strategy"

4. **Regulatory Response:** Government actions affecting the AI industry
   - Signal: Policy/regulation keywords from multiple jurisdictions
   - Example: "EU and US simultaneously propose AI transparency requirements"

5. **Ecosystem Shift:** Developer tools, frameworks, or platforms gaining or losing traction
   - Signal: GitHub stars, npm downloads, or developer sentiment shift
   - Example: "LangChain alternatives are gaining momentum in the developer community"

### Trend Scoring

For each detected trend, score it:
- **Impact radius:** How many developers/companies affected? (1-5)
- **Time horizon:** Is this immediate (this week), short-term (this quarter), or long-term? (1-3)
- **Confidence:** How well-supported by source articles? (number of articles / 3, capped at 1.0)

## Human-in-the-Loop Checkpoints

### Flag for Manual Review

Automatically flag these situations for human verification:
1. **Fabricated URLs:** Any cited URL not in the original article list
2. **Too-good-to-be-true claims:** Breakthrough announcements from unknown sources
3. **Contradictory information:** Multiple sources making opposing claims about the same event
4. **Sensitive topics:** AI regulation, ethics, bias incidents — require careful framing
5. **Company-specific claims:** "Company X is shutting down Y" — verify before publishing

### Quality Gates

| Gate | Check | Action |
|---|---|---|
| Classification | Spot-check 10% of AI/non-AI classifications | Fix misclassifications |
| Summary Accuracy | Compare top 3 summaries against source articles | Flag hallucinations |
| Trend Validity | Verify each trend has 2+ supporting articles | Remove unsupported trends |
| Link Integrity | Verify all URLs are valid and reachable | Remove broken links |
