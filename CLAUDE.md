# DailyDigest — AI Tech Daily Report Generator

Aggregates RSS/WeChat/Podcast feeds into a curated daily AI tech digest, inspired by linux.do community reports.

## Architecture

### Tech Report Pipeline (run_tech_unified)
```
RSS/WeChat feeds
  → Fetch (concurrent, with health tracking + circuit breaker)
  → Dedup (URL normalization + Jaccard title similarity)
  → Topic cluster (Jaccard similarity, agglomerative merge)
  → Editorial pipeline (6-factor news value scoring → tier assignment)
  → AI/non-AI split
  → AI path: LLM deep analysis (draft → critique → refine)
  → Non-AI path: template renderer (capped at 30 articles)
  → Unified two-part report (Part I: AI, Part II: Tech)
  → TL;DR generation
```

### Podcast Report Pipeline (run_podcast)
```
1000 podcasts (podcast_feeds.json)
  → Cleanup (dedup entries >30d + feed health entries >30d)
  → All 1000 podcasts have RSS URLs (862 direct + 138 with xiaoyuzhou pages resolved via RSS)
  → Canary check feed.xyzfm.space (warn on failure, individual feeds tracked by health system)
  → RSS fetch (domain-level rate limiting: shared domains 3 workers, unique domains 20 workers)
  → Xiaoyuzhou HTML scrape (for podcasts without RSS)
  → Dedup (URL hash)
  → Scoring:
      API mode:  LLM batch scoring (25/batch, 3 concurrent, score≤2 filtered)
      Skill mode: heuristic clustering → editorial 6-factor scoring
  → Resolve xiaoyuzhou URLs (match RSS articles to xiaoyuzhou episode pages via __NEXT_DATA__)
  → Report generation:
      API mode:  embedding clustering → LLM theme interpretation → podcast briefing
      Skill mode: simple Markdown table
```

## Dual-Mode Operation

- **API mode** (`API_KEY` set): LLM generates narrative analysis, embedding clustering for themes, critique/refine loop, full-text enrichment for must-read articles
- **Skill mode** (no `API_KEY`): Template renderer with tiered sections, no LLM calls

## Key Thresholds (configurable via env vars)

| Threshold | Default | Env Var |
|-----------|---------|---------|
| Must Read | 0.70 | `EDITORIAL_TIER_MUST_READ` |
| Noteworthy | 0.40 | `EDITORIAL_TIER_NOTEWORTHY` |
| Filter out | 0.25 | `EDITORIAL_NEWS_VALUE_THRESHOLD` |
| HN promote | 200 points | `EDITORIAL_HN_PROMOTE_THRESHOLD` |
| LLM score filter | 2 | `LLM_SCORE_FILTER_THRESHOLD` |
| LLM concurrency | 3 | `LLM_MAX_CONCURRENCY` |
| Embedding cache max | 10000 | `EMBEDDING_CACHE_MAX` |
| Embedding distance | 0.35 | `EMBEDDING_DISTANCE_THRESHOLD` |

## Authority Scoring

76 domains in `AUTHORITY_DOMAINS` (core/config.py) with 4 tiers:
- 1.0: AI labs (OpenAI, Anthropic, DeepSeek, Mistral, xAI), research (Stanford, MIT, Nature, arXiv), landmark blogs (Karpathy, Lilian Weng, Simon Willison)
- 0.85: Premium analysis (Stratechery, Economist, Quanta, FT)
- 0.7: Established tech media + engineering blogs (TechCrunch, Cloudflare, Stripe, Hugging Face)
- 0.55: Aggregators/product sites (HN, AppleInsider, MacRumors, IT之家)

Sources not in AUTHORITY_DOMAINS fall back to feed priority (P1→0.8, P2→0.6, P3→0.4).

## Feed Health

`core/feed_health.py` tracks per-URL consecutive failures in `workspace/feed_health.json`. After 5 failures, feeds are skipped (saves ~20s timeout per dead feed). Unhealthy feeds retry once per 24h. Auto-cleanup removes entries >30 days old.

## RSS Rate Limiting

`core/rss_fetcher.py` splits feeds by domain: domains with ≥20 feeds (shared domains like `feed.xyzfm.space`) use 3 concurrent workers; unique domains use up to 20 workers. This avoids overwhelming a single CDN.

## Embedding Cache

`core/embedding_cache.json` caches text embeddings keyed by SHA-256(title+description). Capped at 10000 entries (`EMBEDDING_CACHE_MAX`); oldest entries evicted on cleanup.

## Feed Reliability Risks

- `wechat2rss.xlab.app`: single service for all 393 WeChat feeds
- `feed.xyzfm.space`: single service for 530 of 1000 podcast feeds

## Running Tests

```bash
uv run pytest tests/ -v
```

## Language

Reports are generated in Chinese (zh).
