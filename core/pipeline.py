"""
Pipeline orchestration for DailyDigest.

Provides per-source run functions and finalize logic used by the CLI
entry point in main.py. Report building and workspace I/O are in
report_builder.py and workspace.py respectively.
"""

import json
import os
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from .logging_config import get_logger
from .workspace import (
    load_http_cache, save_http_cache, ensure_pipeline_dirs,
    save_workspace_updates, load_workspace_data, merge_batch_summaries,
)
from .report_builder import (
    build_merged_report, build_unified_report, build_unified_wechat_report,
    build_category_results_from_summaries, classify_from_summaries,
)

logger = get_logger("pipeline")


def _log_no_api_key(source_type, path):
    """Log the 'no API key' hint for Skill mode."""
    logger.info(f"💡 no API_KEY, raw data saved to {path}")
    logger.info("   Run sub-agent summaries, then:")
    logger.info(f"   python main.py --source {source_type} --finalize")


# ---------------------------------------------------------------------------
# Unified report builder (workspace → report)
# ---------------------------------------------------------------------------

def try_build_unified_report(source, now, language="zh", output_format="markdown"):
    """Attempt to build a unified two-part report from workspace article data.

    Uses API-based AI filter when API_KEY is set, or sub-agent summary data
    for Skill mode classification when no API_KEY.
    """
    from .article import Article

    all_articles = []
    summaries_by_source = {}
    for src in ("tech", "podcast", "wechat"):
        if source in (src, "all") or (source == "tech" and src == "wechat"):
            data = load_workspace_data(src)
            if data:
                for item in data.get("updates", []):
                    try:
                        all_articles.append(Article(**item))
                    except TypeError:
                        continue
                summaries_by_source[src] = merge_batch_summaries(src)

    if not all_articles:
        return None

    api_key = os.environ.get("API_KEY")
    merged_summaries = {}
    for s in summaries_by_source.values():
        merged_summaries.update(s)

    if api_key:
        from .ai_filter import filter_ai_articles
        logger.info(f"\n🤖 Building unified AI + non-AI report from {len(all_articles)} articles...")
        ai_articles, non_ai_articles = filter_ai_articles(all_articles)
    else:
        if not merged_summaries:
            return None
        logger.info(f"\n🤖 Building unified report from {len(all_articles)} articles (Skill mode)...")
        ai_articles, non_ai_articles = classify_from_summaries(all_articles, merged_summaries)

    if not ai_articles and not non_ai_articles:
        return None

    # Generate topic clusters for AI articles
    cluster_map = {}
    try:
        from .topic_cluster import cluster_articles, get_cluster_map
        topic_clusters = cluster_articles(ai_articles)
        cluster_map = get_cluster_map(topic_clusters)
    except Exception as e:
        logger.warning(f"⚠️ Clustering failed (non-fatal): {e}")

    # Full-text enrichment (optional)
    if api_key and os.environ.get("ENRICH_FULL_TEXT"):
        try:
            from .enrich import enrich_tech_articles
            ai_articles, _ = enrich_tech_articles(ai_articles, cluster_map=cluster_map)
        except Exception as e:
            logger.warning(f"⚠️ Enrichment failed (non-fatal): {e}")

    if output_format == "wechat":
        return build_unified_wechat_report(
            ai_articles, non_ai_articles, now, language,
            summary_map=merged_summaries if not api_key else None,
            cluster_map=cluster_map,
        )

    return build_unified_report(
        ai_articles, non_ai_articles, now, language,
        quality_scores=None,
        summary_map=merged_summaries if not api_key else None,
        cluster_map=cluster_map,
    )


# ---------------------------------------------------------------------------
# Finalize helpers
# ---------------------------------------------------------------------------

def _generate_source_report(source_type, data, summaries, language):
    """Dispatch to the correct report generator and return the markdown string."""
    from .article import Article

    updates = [Article(**u) for u in data.get("updates", [])]
    metadata = data.get("metadata", {})

    if source_type == "tech":
        from .config import WORKSPACE_DIR
        trend_path = WORKSPACE_DIR / "tech_trend_insight.json"
        trend_insight = None
        if trend_path.exists():
            with open(trend_path, "r", encoding="utf-8") as f:
                trend_insight = json.load(f)

        has_tiers = any(
            isinstance(v, dict) and "tier" in v
            for v in (summaries.values() if isinstance(summaries, dict) else [])
        )

        from .report_generator import generate_tech_report

        if has_tiers:
            category_results = build_category_results_from_summaries(updates, summaries)
            report_stats = {
                "total_articles": len(updates),
                "categories": len(category_results),
            }
            report = generate_tech_report(
                updates,
                category_results=category_results,
                stats=report_stats,
                report_language=language,
            )
        else:
            report = generate_tech_report(updates, summaries, trend_insight, stats=metadata, report_language=language)

        logger.info(f"✅ tech report generated ({len(updates)} articles)")
        return report

    if source_type == "podcast":
        from .podcast_utils import generate_podcast_report
        report = generate_podcast_report(updates, summaries, metadata=metadata)
        logger.info(f"✅ podcast report generated ({len(summaries)} summaries)")
        return report

    if source_type == "wechat":
        from .wechat_utils import generate_wechat_report
        report = generate_wechat_report(updates, summaries, metadata=metadata)
        logger.info(f"✅ wechat report generated ({len(summaries)} summaries)")
        return report

    raise ValueError(f"Unknown source_type: {source_type}")


def _finalize_source(source_type, language="zh"):
    """Unified finalizer for a single source type.  Returns report string or None."""
    data = load_workspace_data(source_type)
    if data is None:
        return None
    summaries = merge_batch_summaries(source_type)
    return _generate_source_report(source_type, data, summaries, language)


def finalize_reports(source, language="zh", output_format="markdown"):
    """--finalize mode: read sub-agent summaries from workspace/ and build final reports."""
    from .config import OUTPUT_DIR
    from .report_generator import save_report

    now = datetime.now(timezone.utc)

    sections = []
    for src in ("tech", "podcast", "wechat"):
        if source in (src, "all") or (source == "tech" and src == "wechat"):
            report = _finalize_source(src, language)
            if report:
                sections.append(report)

    if not sections:
        logger.warning("⚠️ no reports to generate.")
        return

    unified = try_build_unified_report(source, now, language, output_format=output_format)

    if unified:
        merged = unified
    else:
        merged = build_merged_report(sections, now, language)

    is_wechat = output_format == "wechat"
    ext = "wechat-" + now.strftime('%Y-%m-%d') + ".md" if is_wechat else now.strftime('%Y-%m-%d') + ".md"
    filepath = save_report(merged, ext, OUTPUT_DIR,
                           report_type="digest", language=language,
                           skip_tldr=is_wechat)

    logger.info("\n" + "=" * 60)
    logger.info(f"✅ Finalize done! report: {filepath}")
    logger.info("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Shared helpers for unified pipeline
# ---------------------------------------------------------------------------

def _fetch_wechat_articles(hours=24, limit=None):
    """Fetch, dedup, and enrich WeChat articles."""
    from .wechat_utils import fetch_wechat_feed_list, enrich_wechat_articles
    from .rss_fetcher import fetch_feeds_stdlib
    from .dedup import filter_and_mark

    api_key = os.environ.get("API_KEY")

    logger.info("\n📱 Fetching WeChat feed list...")
    feed_data = fetch_wechat_feed_list()
    feeds = [f for f in feed_data.get("feeds", []) if f.get("active")]
    if limit:
        feeds = feeds[:limit]
        logger.info(f"   (limit mode: first {limit} accounts)")
    feed_list = [
        {"name": f["name"], "url": f["url"], "category": f.get("category", "其他"),
         "language": "zh", "_wechat_meta": {"index": f.get("index", 0)}}
        for f in feeds
    ]

    logger.info("📡 Checking WeChat updates...")
    cache, cache_path = load_http_cache(".wechat_http_cache.json")
    raw_updates, stats, new_cache = fetch_feeds_stdlib(
        feed_list, hours=hours, workers=10, cache=cache
    )
    save_http_cache(cache_path, new_cache)

    raw_updates = filter_and_mark(raw_updates)
    if not raw_updates:
        logger.info("ℹ️ No WeChat updates.")
        return [], stats

    logger.info(f"✅ {len(raw_updates)} WeChat updates")

    if api_key:
        logger.info("📖 Enriching WeChat articles with full text...")
        raw_updates = enrich_wechat_articles(raw_updates)

    return raw_updates, stats


# ---------------------------------------------------------------------------
# Per-source pipeline runners
# ---------------------------------------------------------------------------

def run_tech_unified(hours=48, language="zh", limit=None):
    """Unified tech+wechat pipeline."""
    from .config import load_feed_config, OUTPUT_DIR, WORKSPACE_DIR, normalize_category
    from .dedup import filter_and_mark, cleanup_old_entries
    from .ai_filter import filter_ai_articles

    t_start = time.time()
    ensure_pipeline_dirs()
    api_key = os.environ.get("API_KEY")

    # Step 1+2: Fetch tech RSS and WeChat in parallel
    logger.info("\n📡 Step 1/5: Fetching tech RSS + WeChat in parallel...")
    config = load_feed_config("tech")
    feed_list = [
        {"name": f["name"], "url": f["url"], "category": c["name"],
         "language": f.get("language", "en"), "priority": f.get("priority", 3),
         **({"max_articles": f["max_articles"]} if "max_articles" in f else {})}
        for c in config.get("categories", [])
        for f in c.get("feeds", [])
    ]
    if limit:
        feed_list = feed_list[:limit]
        logger.info(f"   (limit mode: first {limit} sources)")
    settings = config.get("settings", {})

    def _fetch_tech():
        if api_key:
            from .rss_fetcher import fetch_feeds_feedparser
            articles_by_category, tech_stats = fetch_feeds_feedparser(
                feed_list, hours=settings.get("hours_back", hours),
                max_per_feed=settings.get("max_articles_per_feed", 10)
            )
            return [a for arts in articles_by_category.values() for a in arts], tech_stats
        else:
            from .rss_fetcher import fetch_feeds_stdlib
            cache, cache_path = load_http_cache(".http_cache.json")
            updates, stats, new_cache = fetch_feeds_stdlib(
                feed_list, hours=hours, workers=20, cache=cache,
                timeout=settings.get("timeout_seconds"),
                max_per_source=settings.get("max_per_source", 30),
            )
            save_http_cache(cache_path, new_cache)
            return updates, stats

    wechat_hours = min(hours, 25)

    t1 = time.time()
    tech_articles, tech_stats = [], {}
    wechat_articles, wechat_stats = [], {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        tech_future = pool.submit(_fetch_tech)
        wechat_future = pool.submit(_fetch_wechat_articles, wechat_hours, limit)
        tech_articles, tech_stats = tech_future.result()
        wechat_articles, wechat_stats = wechat_future.result()
    logger.info(f"⏱️ RSS fetch completed in {time.time() - t1:.1f}s "
                f"(tech: {len(tech_articles)}, wechat: {len(wechat_articles)})")

    if not tech_articles:
        logger.warning("⚠️ No tech articles fetched.")

    # Step 3: Merge + dedup
    all_articles = tech_articles + wechat_articles
    if not all_articles:
        logger.warning("⚠️ No articles from any source.")
        return None

    t2 = time.time()
    logger.info(f"\n🔍 Step 3/5: Dedup ({len(tech_articles)} tech + {len(wechat_articles)} wechat)...")
    cleanup_old_entries(days=30)
    new_articles = filter_and_mark(all_articles)
    if not new_articles:
        logger.warning("⚠️ All articles already processed.")
        return None
    logger.info(f"✅ {len(new_articles)} new articles total ({time.time() - t2:.1f}s)")

    def _is_wechat_article(a):
        return a.category.startswith("wechat_") or "mp.weixin.qq.com" in a.url

    tech_new = [a for a in new_articles if not _is_wechat_article(a)]
    wechat_new = [a for a in new_articles if _is_wechat_article(a)]

    save_workspace_updates("tech", tech_new, tech_stats)
    if wechat_new:
        save_workspace_updates("wechat", wechat_new, wechat_stats)

    # Step 4: Cluster + classify + summarize
    cluster_map = {}
    t3 = time.time()
    try:
        logger.info("🔍 Step 4/6: Clustering topics...")
        from .topic_cluster import cluster_articles, get_cluster_map
        topic_clusters = cluster_articles(new_articles)
        cluster_map = get_cluster_map(topic_clusters)
        clustered = sum(1 for c in topic_clusters if c["size"] > 1)
        logger.info(f"✅ {len(topic_clusters)} topic clusters ({clustered} multi-article) ({time.time() - t3:.1f}s)")
    except Exception as e:
        logger.warning(f"⚠️ Topic clustering failed (non-fatal): {e}")

    # Step 4.5: Editorial pipeline — scoring, tiering, depth allocation, filtering
    try:
        from .editorial import run_editorial_pipeline
        new_articles, editorial_stats = run_editorial_pipeline(new_articles, cluster_map)
    except Exception as e:
        logger.warning(f"⚠️ Editorial pipeline failed (non-fatal): {e}")

    if api_key and os.environ.get("ENRICH_FULL_TEXT"):
        try:
            t_enrich = time.time()
            logger.info("📖 Enriching high-importance articles...")
            from .enrich import enrich_tech_articles
            new_articles, _ = enrich_tech_articles(new_articles, cluster_map=cluster_map)
            logger.info(f"⏱️ Enrichment completed in {time.time() - t_enrich:.1f}s")
        except Exception as e:
            logger.warning(f"⚠️ Full-text enrichment failed (non-fatal): {e}")

    if not api_key:
        logger.info("💡 no API_KEY, raw data saved to workspace/")
        logger.info("   Run sub-agent summaries, then:")
        logger.info("   python main.py --source tech --finalize")
        return None

    # AI / non-AI classification + summarization
    t4 = time.time()
    logger.info("🤖 Step 6/6: AI classification + summarization...")
    ai_articles, non_ai_articles = filter_ai_articles(new_articles)

    ai_by_category = {}
    for a in ai_articles:
        cat = normalize_category(a.category)
        ai_by_category.setdefault(cat, []).append(a)

    if ai_by_category:
        from .ai_summarizer import summarize_all_categories
        category_results, executive_summary = summarize_all_categories(
            ai_by_category, language
        )
    else:
        category_results, executive_summary = {}, ""
    logger.info(f"⏱️ AI pipeline completed in {time.time() - t4:.1f}s")

    now = datetime.now(timezone.utc)
    report = build_unified_report(
        ai_articles, non_ai_articles, now, language,
        quality_scores=None,
        summary_map=None,
        cluster_map=cluster_map,
    )

    combined_stats = {
        "total_articles": len(new_articles),
        "tech": len(tech_new),
        "wechat": len(wechat_new),
    }
    logger.info(f"⏱️ Total pipeline time: {time.time() - t_start:.1f}s")
    return report, combined_stats


def run_podcast(hours=24, limit=None):
    """Podcast pipeline.  Returns (report_str, stats_dict) or None."""
    from .config import CONFIG_DIR
    from .rss_fetcher import fetch_feeds_stdlib
    from .podcast_utils import resolve_xiaoyuzhou_urls, generate_podcast_report
    from .dedup import filter_and_mark

    t_start = time.time()
    ensure_pipeline_dirs()
    api_key = os.environ.get("API_KEY")

    logger.info("\n🎙️ Step 1/3: Checking podcast updates...")
    with open(CONFIG_DIR / "podcast_feeds.json", "r", encoding="utf-8") as f:
        pdata = json.load(f)

    podcasts = pdata.get("podcasts", [])[:pdata.get("settings", {}).get("count", 1000)]

    # Filter to tech-related categories if configured
    psettings = pdata.get("settings", {})
    tech_cats = set(psettings.get("tech_categories", []))
    if psettings.get("filter_tech_only") and tech_cats:
        before = len(podcasts)
        podcasts = [p for p in podcasts if p.get("category", "") in tech_cats]
        logger.info(f"   ({before} total -> {len(podcasts)} tech-related podcasts)")
    if limit:
        podcasts = podcasts[:limit]
        logger.info(f"   (limit mode: first {limit} podcasts)")
    feed_list = [
        {"name": p["name"], "url": p["url"], "category": "podcast", "language": "zh",
         "_podcast_meta": {"rank": p.get("rank", 0), "xiaoyuzhou_url": p.get("xiaoyuzhou_url", "")}}
        for p in podcasts if p.get("url")
    ]

    t1 = time.time()
    cache, cache_path = load_http_cache(".podcast_http_cache.json")
    raw_updates, stats, new_cache = fetch_feeds_stdlib(feed_list, hours=hours, workers=30, cache=cache)
    save_http_cache(cache_path, new_cache)
    logger.info(f"⏱️ Podcast RSS fetch completed in {time.time() - t1:.1f}s")

    raw_updates = filter_and_mark(raw_updates)
    if not raw_updates:
        logger.warning("⚠️ no podcast updates.")
        return None

    for u in raw_updates:
        meta = u.extra.get("_feed_meta", {}).get("_podcast_meta", {})
        u.extra["rank"] = meta.get("rank", 0)
        u.extra["xiaoyuzhou_url"] = meta.get("xiaoyuzhou_url", "")

    logger.info(f"✅ {len(raw_updates)} podcast updates")

    t2 = time.time()
    logger.info("🔗 Step 2/3: Resolving xiaoyuzhou URLs...")
    updates = resolve_xiaoyuzhou_urls(raw_updates)
    logger.info(f"⏱️ URL resolution completed in {time.time() - t2:.1f}s")

    updates_path = save_workspace_updates("podcast", updates, stats)

    if api_key:
        logger.info("📄 Step 3/3: AI summaries + report...")
        from .ai_summarizer import summarize_podcast_batch
        ai_summaries = summarize_podcast_batch(updates)
        report = generate_podcast_report(updates, ai_summaries, metadata=stats)
    else:
        logger.info("📄 Step 3/3: Preliminary report (no AI summaries)...")
        report = generate_podcast_report(updates, metadata=stats)
        _log_no_api_key("podcast", updates_path)

    logger.info(f"⏱️ Total podcast pipeline time: {time.time() - t_start:.1f}s")
    return report, {"total_episodes": len(updates)}


def run_wechat(hours=24, limit=None):
    """WeChat pipeline.  Returns (report_str, stats_dict) or None."""
    from .wechat_utils import fetch_wechat_feed_list, generate_wechat_report, enrich_wechat_articles
    from .rss_fetcher import fetch_feeds_stdlib
    from .dedup import filter_and_mark

    ensure_pipeline_dirs()
    api_key = os.environ.get("API_KEY")

    logger.info("\n📱 Step 1/3: Fetching WeChat feed list...")
    feed_data = fetch_wechat_feed_list()
    feeds = [f for f in feed_data.get("feeds", []) if f.get("active")]
    if limit:
        feeds = feeds[:limit]
        logger.info(f"   (limit mode: first {limit} accounts)")
    feed_list = [
        {"name": f["name"], "url": f["url"], "category": f.get("category", "其他"), "language": "zh",
         "_wechat_meta": {"index": f.get("index", 0)}}
        for f in feeds
    ]

    logger.info("📡 Step 2/3: Checking WeChat updates...")
    cache, cache_path = load_http_cache(".wechat_http_cache.json")
    raw_updates, stats, new_cache = fetch_feeds_stdlib(feed_list, hours=hours, workers=10, cache=cache)
    save_http_cache(cache_path, new_cache)

    raw_updates = filter_and_mark(raw_updates)
    if not raw_updates:
        logger.warning("⚠️ no WeChat updates.")
        return None

    updates = raw_updates
    logger.info(f"✅ {len(updates)} WeChat updates")

    if api_key:
        logger.info("📖 Enriching WeChat articles with full text...")
        updates = enrich_wechat_articles(updates)

    updates_path = save_workspace_updates("wechat", updates, stats)

    if api_key:
        logger.info("📄 Step 3/3: AI summaries + report...")
        from .ai_summarizer import summarize_wechat_batch
        ai_summaries = summarize_wechat_batch(updates)
        report = generate_wechat_report(updates, ai_summaries, metadata=stats)
    else:
        logger.info("📄 Step 3/3: Preliminary report (no AI summaries)...")
        report = generate_wechat_report(updates, metadata=stats)
        _log_no_api_key("wechat", updates_path)

    return report, {"total_articles": len(updates)}
