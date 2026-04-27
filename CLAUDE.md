# DailyDigest — AI Tech Daily Report Generator

Aggregates RSS/WeChat/Podcast feeds into a curated daily AI tech digest, inspired by linux.do community reports.

## Architecture

```
RSS/WeChat/Podcast feeds
  → Fetch (concurrent, with health tracking + circuit breaker)
  → Dedup (URL normalization + Jaccard title similarity)
  → Noise filter (negative gate → keyword match → hard relevance check)
  → Topic cluster (Jaccard similarity, agglomerative merge)
  → Editorial pipeline (5-factor news value scoring → tier assignment)
  → AI/non-AI split
  → AI path: LLM deep analysis (draft → critique → refine)
  → Non-AI path: template renderer (capped at 30 articles)
  → Unified two-part report (Part I: AI, Part II: Tech)
  → TL;DR generation
```

## Dual-Mode Operation

- **API mode** (`API_KEY` set): LLM generates narrative analysis, critique/refine loop, full-text enrichment for must-read articles
- **Skill mode** (no `API_KEY`): Template renderer with tiered sections, no LLM calls

## Key Thresholds (configurable via env vars)

| Threshold | Default | Env Var |
|-----------|---------|---------|
| Must Read | 0.70 | `EDITORIAL_TIER_MUST_READ` |
| Noteworthy | 0.40 | `EDITORIAL_TIER_NOTEWORTHY` |
| Filter out | 0.25 | `EDITORIAL_NEWS_VALUE_THRESHOLD` |
| HN promote | 200 points | `EDITORIAL_HN_PROMOTE_THRESHOLD` |

## Authority Scoring

76 domains in `AUTHORITY_DOMAINS` (core/topic_cluster.py) with 4 tiers:
- 1.0: AI labs (OpenAI, Anthropic, DeepSeek, Mistral, xAI), research (Stanford, MIT, Nature, arXiv), landmark blogs (Karpathy, Lilian Weng, Simon Willison)
- 0.85: Premium analysis (Stratechery, Economist, Quanta, FT)
- 0.7: Established tech media + engineering blogs (TechCrunch, Cloudflare, Stripe, Hugging Face)
- 0.55: Aggregators/product sites (HN, AppleInsider, MacRumors, IT之家)

Sources not in AUTHORITY_DOMAINS fall back to feed priority (P1→0.8, P2→0.6, P3→0.4).

## Feed Health

`core/feed_health.py` tracks per-URL consecutive failures in `workspace/feed_health.json`. After 5 failures, feeds are skipped (saves ~20s timeout per dead feed). Unhealthy feeds retry once per 24h. Auto-cleanup removes entries >30 days old.

## Feed Reliability Risks

- `wechat2rss.xlab.app`: single service for all 393 WeChat feeds
- `feed.xyzfm.space`: single service for 514 of 1000 podcast feeds
- 138 podcasts have no RSS URL (scrape-only via xiaoyuzhou)

## Running Tests

```bash
uv run pytest tests/ -v
```

## Language

Reports are generated in Chinese (zh).
