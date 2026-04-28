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
from urllib.parse import urlparse

from .logging_config import get_logger
from .workspace import (
    ensure_pipeline_dirs,
    save_workspace_updates, load_workspace_data, merge_batch_summaries,
)
from .report_builder import (
    build_merged_report, build_unified_report, build_unified_wechat_report,
    build_category_results_from_summaries, classify_from_summaries,
)

logger = get_logger("pipeline")


class PipelineTimer:
    """Track elapsed time across pipeline steps with an overall budget."""

    def __init__(self, budget_seconds=2400):
        self.budget = budget_seconds
        self.start = time.time()

    def elapsed(self):
        return time.time() - self.start

    def remaining(self):
        return max(0, self.budget - self.elapsed())

    def can_proceed(self, estimated_seconds=60):
        return self.remaining() > estimated_seconds


def _canary_check(url, timeout=10):
    """Quick health check for a shared service URL. Returns True if reachable."""
    from .http import fetch_url_with_retry
    try:
        body, status, _ = fetch_url_with_retry(url, timeout=timeout, max_retries=0)
        return body is not None or status == 304
    except Exception:
        return False


def _group_feeds_by_shared_domain(feed_list, threshold=20):
    """Split feeds into shared-domain and unique-domain groups.

    Returns (shared, unique) where shared contains feeds whose domain
    appears >= threshold times across the entire feed_list.
    """
    domain_counts = {}
    for f in feed_list:
        try:
            domain = urlparse(f["url"]).netloc
        except Exception:
            domain = "unknown"
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    shared_domains = {d for d, c in domain_counts.items() if c >= threshold}
    shared = [f for f in feed_list if urlparse(f["url"]).netloc in shared_domains]
    unique = [f for f in feed_list if urlparse(f["url"]).netloc not in shared_domains]
    return shared, unique, shared_domains


def _log_no_api_key(source_type, path):
    """Log the 'no API key' hint for Skill mode."""
    logger.info(f"💡 no API_KEY, raw data saved to {path}")
    logger.info("   Run sub-agent summaries, then:")
    logger.info(f"   python main.py --source {source_type} --finalize")


def _merge_run_stats(stats_list):
    """Merge per-source metadata into top-level digest stats."""
    merged = {
        "source_count": 0,
        "candidate_count": 0,
        "after_dedup": 0,
        "after_editorial": 0,
        "included_count": 0,
        "generated_at": None,
        "run_id": None,
    }
    for stats in stats_list:
        if not stats:
            continue
        for key in ("source_count", "candidate_count", "after_dedup", "after_editorial", "included_count"):
            merged[key] += int(stats.get(key, 0) or 0)
        merged["generated_at"] = merged["generated_at"] or stats.get("generated_at")
        merged["run_id"] = merged["run_id"] or stats.get("run_id")
    return merged


def _build_run_metadata(run_id, source_count, candidate_count, after_dedup, after_editorial,
                        included_count, extra=None):
    metadata = {
        "run_id": run_id,
        "source_count": source_count,
        "candidate_count": candidate_count,
        "after_dedup": after_dedup,
        "after_editorial": after_editorial,
        "included_count": included_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        metadata.update(extra)
    return metadata


# ---------------------------------------------------------------------------
# Unified report builder (workspace → report)
# ---------------------------------------------------------------------------

def try_build_unified_report(source, now, output_format="markdown"):
    """Attempt to build a unified report from workspace article data.

    Uses LLM scoring + theme grouping when API_KEY is set, or sub-agent
    summary data for Skill mode when no API_KEY.
    """
    from .article import Article

    all_articles = []
    summaries_by_source = {}
    source_stats = []
    for src in ("tech", "podcast", "wechat"):
        if source in (src, "all") or (source == "tech" and src == "wechat"):
            data = load_workspace_data(src)
            if data:
                for item in data.get("updates", []):
                    try:
                        all_articles.append(Article(**item))
                    except TypeError:
                        continue
                metadata = data.get("metadata", {})
                summaries_by_source[src] = merge_batch_summaries(
                    src,
                    run_id=metadata.get("run_id"),
                    generated_at=metadata.get("generated_at"),
                )
                source_stats.append(metadata)

    if not all_articles:
        return None

    api_key = os.environ.get("API_KEY")
    merged_summaries = {}
    for s in summaries_by_source.values():
        merged_summaries.update(s)

    if api_key:
        logger.info(f"\n🤖 Building unified report from {len(all_articles)} articles (LLM pipeline)...")
        from .llm_classify import score_and_filter_articles

        all_articles, score_stats = score_and_filter_articles(all_articles)

        if not all_articles:
            return None

        from .config import EMBEDDING_CLUSTERING_ENABLED
        if EMBEDDING_CLUSTERING_ENABLED:
            from .embedding_cluster import embed_articles, cluster_by_embedding, get_cluster_leftovers
            from .llm_classify import interpret_themes_with_llm
            try:
                embeddings = embed_articles(all_articles)
                clusters = cluster_by_embedding(all_articles, embeddings)
                llm_themes, singletons = interpret_themes_with_llm(clusters)
                leftovers = get_cluster_leftovers(clusters, all_articles)
            except RuntimeError as e:
                logger.warning(f"⚠️ Embedding clustering failed, falling back to LLM grouping: {e}")
                from .llm_classify import group_articles_by_theme
                llm_themes, leftovers = group_articles_by_theme(all_articles)
                singletons = []
        else:
            from .llm_classify import group_articles_by_theme
            llm_themes, leftovers = group_articles_by_theme(all_articles)
            singletons = []

        merged_stats = _merge_run_stats(source_stats)
        merged_stats.update(score_stats)

        if output_format == "wechat":
            return build_unified_wechat_report(
                all_articles, now=now,
                llm_themes=llm_themes, llm_leftovers=leftovers,
                stats=merged_stats,
            )

        return build_unified_report(
            all_articles, now=now,
            llm_themes=llm_themes, llm_leftovers=leftovers,
            embedding_singletons=singletons,
            stats=merged_stats,
        )

    # Skill mode: use sub-agent summaries for classification
    if not merged_summaries:
        return None
    logger.info(f"\n🤖 Building unified report from {len(all_articles)} articles (Skill mode)...")
    ai_articles, non_ai_articles = classify_from_summaries(all_articles, merged_summaries)

    if not ai_articles and not non_ai_articles:
        return None

    # Skill mode: heuristic clustering + editorial
    cluster_map = {}
    try:
        from .topic_cluster import cluster_articles, get_cluster_map
        topic_clusters = cluster_articles(ai_articles)
        cluster_map = get_cluster_map(topic_clusters)
    except Exception as e:
        logger.warning(f"⚠️ Clustering failed (non-fatal): {e}")

    if not any(a.extra.get("editorial_tier") for a in ai_articles):
        try:
            from .editorial import run_editorial_pipeline
            from .config import EDITORIAL_ENABLED
            if EDITORIAL_ENABLED:
                ai_articles, _ = run_editorial_pipeline(ai_articles, cluster_map)
        except Exception as e:
            import traceback
            logger.warning(f"⚠️ Editorial pipeline in finalize failed: {e}")
            logger.debug(traceback.format_exc())

    if output_format == "wechat":
        return build_unified_wechat_report(
            ai_articles, non_ai_articles, now,
            summary_map=merged_summaries if not api_key else None,
            cluster_map=cluster_map,
            stats=_merge_run_stats(source_stats),
        )

    return build_unified_report(
        ai_articles, non_ai_articles, now,
        summary_map=merged_summaries if not api_key else None,
        cluster_map=cluster_map,
        stats=_merge_run_stats(source_stats),
    )


# ---------------------------------------------------------------------------
# Finalize helpers
# ---------------------------------------------------------------------------

def _generate_source_report(source_type, data, summaries):
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

        from .report_builder import generate_tech_report

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
            )
        else:
            report = generate_tech_report(updates, summaries, trend_insight_skill=trend_insight, stats=metadata)

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


def _finalize_source(source_type):
    """Unified finalizer for a single source type.  Returns report string or None."""
    data = load_workspace_data(source_type)
    if data is None:
        return None
    metadata = data.get("metadata", {})
    summaries = merge_batch_summaries(
        source_type,
        run_id=metadata.get("run_id"),
        generated_at=metadata.get("generated_at"),
    )
    return _generate_source_report(source_type, data, summaries)


def finalize_reports(source, output_format="markdown"):
    """--finalize mode: read sub-agent summaries from workspace/ and build final reports.

    Tries the unified briefing path first (preferred). Falls back to
    per-source report merging only when the unified builder returns None.
    """
    from .config import TECH_OUTPUT_DIR, PODCAST_OUTPUT_DIR
    from .report_builder import save_report

    now = datetime.now(timezone.utc)

    # For podcast source, finalize directly to podcast directory
    if source == "podcast":
        report = _finalize_source("podcast")
        if not report:
            logger.warning("⚠️ no podcast reports to generate.")
            return
        is_wechat = output_format == "wechat"
        ext = "wechat-" + now.strftime('%Y-%m-%d') + ".md" if is_wechat else now.strftime('%Y-%m-%d') + ".md"
        filepath = save_report(report, ext, PODCAST_OUTPUT_DIR,
                               report_type="digest", skip_tldr=is_wechat)
        logger.info("\n" + "=" * 60)
        logger.info(f"✅ Finalize done! report: {filepath}")
        logger.info("=" * 60 + "\n")
        return

    # Tech / wechat / all sources: route to tech directory
    merged = try_build_unified_report(source, now, output_format=output_format)

    if not merged:
        sections = []
        for src in ("tech", "podcast", "wechat"):
            if source in (src, "all") or (source == "tech" and src == "wechat"):
                report = _finalize_source(src)
                if report:
                    sections.append(report)

        if not sections:
            logger.warning("⚠️ no reports to generate.")
            return

        merged = build_merged_report(sections, now)

    is_wechat = output_format == "wechat"
    ext = "wechat-" + now.strftime('%Y-%m-%d') + ".md" if is_wechat else now.strftime('%Y-%m-%d') + ".md"
    filepath = save_report(merged, ext, TECH_OUTPUT_DIR,
                           report_type="digest",
                           skip_tldr=is_wechat)

    logger.info("\n" + "=" * 60)
    logger.info(f"✅ Finalize done! report: {filepath}")
    logger.info("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Per-source pipeline runners
# ---------------------------------------------------------------------------

def run_tech_unified(hours=48, limit=None):
    """Unified tech+wechat pipeline."""
    from .config import load_feed_config, WORKSPACE_DIR, normalize_category
    from .dedup import filter_and_mark, cleanup_old_entries

    t_start = time.time()
    timer = PipelineTimer(budget_seconds=int(os.environ.get("PIPELINE_BUDGET_SECONDS", "2400")))
    ensure_pipeline_dirs()
    api_key = os.environ.get("API_KEY")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Cleanup stale feed health and dedup entries
    from .feed_health import cleanup as health_cleanup
    health_cleanup()
    cleanup_old_entries()

    # Step 1: Fetch tech RSS and WeChat in parallel
    logger.info("\n📡 Step 1/5: Fetching tech RSS + WeChat in parallel...")
    config = load_feed_config("tech")
    feed_list = [
        {"name": f["name"], "url": f["url"], "category": c["name"],
         "language": f.get("language", "en"), "priority": f.get("priority", 3),
         **({"max_articles": f["max_articles"]} if "max_articles" in f else {}),
         **({"_noise_filter": f["noise_filter"]} if "noise_filter" in f else {})}
        for c in config.get("categories", [])
        for f in c.get("feeds", [])
    ]
    if limit:
        feed_list = feed_list[:limit]
        logger.info(f"   (limit mode: first {limit} sources)")
    settings = config.get("settings", {})

    def _fetch_tech():
        from .rss_fetcher import fetch_feeds_feedparser
        articles_by_category, tech_stats = fetch_feeds_feedparser(
            feed_list, hours=settings.get("hours_back", hours),
            max_per_feed=settings.get("max_articles_per_feed", 10)
        )
        return [a for arts in articles_by_category.values() for a in arts], tech_stats

    wechat_hours = min(hours, 25)

    # Canary check: verify wechat2rss service is reachable before batch fetch
    wechat_ok = True
    if not limit:
        wechat_ok = _canary_check("https://wechat2rss.xlab.app/", timeout=10)
        if not wechat_ok:
            logger.warning("⚠️ wechat2rss.xlab.app unreachable, skipping WeChat fetch")

    t1 = time.time()
    from .wechat_utils import fetch_wechat_articles
    tech_articles, tech_stats = [], {}
    wechat_articles, wechat_stats = [], {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        tech_future = pool.submit(_fetch_tech)
        wechat_future = (
            pool.submit(fetch_wechat_articles, wechat_hours, limit)
            if wechat_ok else None
        )
        tech_articles, tech_stats = tech_future.result()
        if wechat_future:
            wechat_articles, wechat_stats = wechat_future.result()
    logger.info(f"⏱️ RSS fetch completed in {time.time() - t1:.1f}s "
                f"(tech: {len(tech_articles)}, wechat: {len(wechat_articles)})")

    if not tech_articles:
        logger.warning("⚠️ No tech articles fetched.")

    # Step 2: Merge + dedup
    all_articles = tech_articles + wechat_articles
    candidate_count = len(all_articles)
    if not all_articles:
        logger.warning("⚠️ No articles from any source.")
        return None

    t2 = time.time()
    logger.info(f"\n🔍 Step 2/5: Dedup ({len(tech_articles)} tech + {len(wechat_articles)} wechat)...")
    new_articles = filter_and_mark(all_articles)
    if not new_articles:
        logger.warning("⚠️ All articles already processed.")
        return None
    after_dedup_total = len(new_articles)
    logger.info(f"✅ {len(new_articles)} new articles total ({time.time() - t2:.1f}s)")

    def _is_wechat_article(a):
        return a.category.startswith("wechat_") or "mp.weixin.qq.com" in a.url

    # Step 3: LLM scoring & filtering
    pre_scoring_articles = list(new_articles)

    logger.info("🤖 Step 3/5: LLM scoring & filtering...")
    from .llm_classify import score_and_filter_articles
    new_articles, score_stats = score_and_filter_articles(new_articles)
    logger.info(f"📊 LLM scoring: {score_stats['surviving']}/{score_stats['total']} surviving")

    # Step 4: Save workspace data (with tier/score data preserved)
    logger.info("💾 Step 4/5: Saving workspace data...")
    tech_pre_scoring = [a for a in pre_scoring_articles if not _is_wechat_article(a)]
    wechat_pre_scoring = [a for a in pre_scoring_articles if _is_wechat_article(a)]
    tech_new = [a for a in new_articles if not _is_wechat_article(a)]
    wechat_new = [a for a in new_articles if _is_wechat_article(a)]
    tech_metadata = _build_run_metadata(
        run_id,
        source_count=len(feed_list),
        candidate_count=len(tech_articles),
        after_dedup=len(tech_pre_scoring),
        after_editorial=len(tech_new),
        included_count=len(tech_new),
        extra=tech_stats,
    )
    save_workspace_updates("tech", tech_new, tech_metadata)
    if wechat_new:
        wechat_metadata = _build_run_metadata(
            run_id,
            source_count=wechat_stats.get("source_count", 0),
            candidate_count=len(wechat_articles),
            after_dedup=len(wechat_pre_scoring),
            after_editorial=len(wechat_new),
            included_count=len(wechat_new),
            extra=wechat_stats,
        )
        save_workspace_updates("wechat", wechat_new, wechat_metadata)

    if not api_key:
        logger.info("💡 no API_KEY, raw data saved to workspace/")
        logger.info("   Run sub-agent summaries, then:")
        logger.info("   python main.py --source tech --finalize")
        return None

    t4 = time.time()

    from .config import EMBEDDING_CLUSTERING_ENABLED
    if EMBEDDING_CLUSTERING_ENABLED:
        logger.info("🤖 Step 5/5: Embedding clustering + LLM interpretation + unified briefing...")
        from .embedding_cluster import embed_articles, cluster_by_embedding, get_cluster_leftovers
        from .llm_classify import interpret_themes_with_llm
        from .report_builder import build_unified_report

        try:
            embeddings = embed_articles(new_articles)
            clusters = cluster_by_embedding(new_articles, embeddings)
            llm_themes, singletons = interpret_themes_with_llm(clusters)
            leftovers = get_cluster_leftovers(clusters, new_articles)
        except RuntimeError as e:
            logger.warning(f"⚠️ Embedding clustering failed, falling back to LLM grouping: {e}")
            from .llm_classify import group_articles_by_theme
            llm_themes, leftovers = group_articles_by_theme(new_articles)
            singletons = []
    else:
        logger.info("🤖 Step 5/5: LLM theme grouping + unified briefing...")
        from .llm_classify import group_articles_by_theme
        from .report_builder import build_unified_report

        llm_themes, leftovers = group_articles_by_theme(new_articles)
        singletons = []
    now = datetime.now(timezone.utc)
    report = build_unified_report(
        new_articles,
        now=now,
        llm_themes=llm_themes,
        llm_leftovers=leftovers,
        embedding_singletons=singletons,
        stats={
            "run_id": run_id,
            "source_count": len(feed_list) + wechat_stats.get("source_count", 0),
            "candidate_count": candidate_count,
            "after_dedup": after_dedup_total,
            "included_count": len(new_articles),
        },
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
    from .rss_fetcher import fetch_feeds_feedparser
    from .podcast_utils import resolve_xiaoyuzhou_urls, generate_podcast_report
    from .dedup import filter_and_mark

    t_start = time.time()
    ensure_pipeline_dirs()
    api_key = os.environ.get("API_KEY")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    logger.info("\n🎙️ Step 1/5: Checking podcast updates...")
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

    # Canary check: verify feed.xyzfm.space is reachable (serves majority of podcast feeds)
    xyzfm_feeds = [f for f in feed_list if "feed.xyzfm.space" in f["url"]]
    if xyzfm_feeds and not limit and not _canary_check(xyzfm_feeds[0]["url"], timeout=10):
        logger.warning(f"⚠️ feed.xyzfm.space unreachable, removing {len(xyzfm_feeds)} feeds")
        feed_list = [f for f in feed_list if "feed.xyzfm.space" not in f["url"]]

    if not feed_list:
        logger.warning("⚠️ No podcast feeds available after health check.")
        return None

    t1 = time.time()
    articles_by_category, stats = fetch_feeds_feedparser(feed_list, hours=hours, max_per_feed=10)
    raw_updates = [a for arts in articles_by_category.values() for a in arts]
    logger.info(f"⏱️ Podcast RSS fetch completed in {time.time() - t1:.1f}s")

    candidate_count = len(raw_updates)
    raw_updates = filter_and_mark(raw_updates)
    if not raw_updates:
        logger.warning("⚠️ no podcast updates.")
        return None

    for u in raw_updates:
        meta = u.extra.get("_feed_meta", {}).get("_podcast_meta", {})
        u.extra["rank"] = meta.get("rank", 0)
        u.extra["xiaoyuzhou_url"] = meta.get("xiaoyuzhou_url", "")

    # Scoring: LLM when API_KEY is set, heuristic otherwise
    if api_key:
        logger.info("🤖 Step 2/5: LLM scoring & filtering...")
        from .llm_classify import score_and_filter_articles
        raw_updates, score_stats = score_and_filter_articles(raw_updates)
        logger.info(f"📊 LLM scoring: {score_stats['surviving']}/{score_stats['total']} surviving")
    else:
        logger.info("📊 Step 2/5: Heuristic scoring...")
        try:
            from .topic_cluster import cluster_articles, get_cluster_map
            topic_clusters = cluster_articles(raw_updates)
            cluster_map = get_cluster_map(topic_clusters)
        except Exception as e:
            logger.warning(f"⚠️ Podcast clustering failed (non-fatal): {e}")

        try:
            from .editorial import run_editorial_pipeline
            from .config import EDITORIAL_ENABLED
            if EDITORIAL_ENABLED:
                raw_updates, _ = run_editorial_pipeline(raw_updates, cluster_map)
        except Exception as e:
            logger.warning(f"⚠️ Podcast editorial pipeline failed (non-fatal): {e}")

    logger.info(f"✅ {len(raw_updates)} podcast updates")

    t2 = time.time()
    logger.info("🔗 Step 3/5: Resolving xiaoyuzhou URLs...")
    updates = resolve_xiaoyuzhou_urls(raw_updates)
    logger.info(f"⏱️ URL resolution completed in {time.time() - t2:.1f}s")

    podcast_metadata = _build_run_metadata(
        run_id,
        source_count=len(feed_list),
        candidate_count=candidate_count,
        after_dedup=len(raw_updates),
        after_editorial=len(updates),
        included_count=len(updates),
        extra=stats,
    )
    updates_path = save_workspace_updates("podcast", updates, podcast_metadata)

    if api_key:
        logger.info("📄 Step 4/5: AI summaries + report...")
        from .llm_services import summarize_podcast_batch
        ai_summaries = summarize_podcast_batch(updates)
        report = generate_podcast_report(updates, ai_summaries, metadata=stats)
    else:
        logger.info("📄 Step 4/5: Preliminary report (no AI summaries)...")
        report = generate_podcast_report(updates, metadata=stats)
        _log_no_api_key("podcast", updates_path)

    logger.info(f"⏱️ Total podcast pipeline time: {time.time() - t_start:.1f}s")
    return report, {"total_episodes": len(updates)}


def run_wechat(hours=24, limit=None):
    """WeChat pipeline.  Returns (report_str, stats_dict) or None."""
    from .wechat_utils import fetch_wechat_feed_list, generate_wechat_report, enrich_wechat_articles
    from .rss_fetcher import fetch_feeds_feedparser
    from .dedup import filter_and_mark

    ensure_pipeline_dirs()
    api_key = os.environ.get("API_KEY")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    logger.info("\n📱 Step 1/5: Fetching WeChat feed list...")
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

    # Canary check: verify wechat2rss service is reachable
    if feed_list and not limit:
        if not _canary_check(feed_list[0]["url"], timeout=10):
            logger.warning(f"⚠️ wechat2rss.xlab.app unreachable, aborting WeChat pipeline")
            return None

    logger.info("📡 Step 2/5: Checking WeChat updates...")
    articles_by_category, stats = fetch_feeds_feedparser(feed_list, hours=hours, max_per_feed=10)
    raw_updates = [a for arts in articles_by_category.values() for a in arts]
    stats["source_count"] = len(feed_list)

    candidate_count = len(raw_updates)
    raw_updates = filter_and_mark(raw_updates)
    if not raw_updates:
        logger.warning("⚠️ no WeChat updates.")
        return None

    updates = raw_updates
    logger.info(f"✅ {len(updates)} WeChat updates")

    # Scoring: LLM when API_KEY is set, heuristic otherwise
    if api_key:
        logger.info("🤖 Step 3/5: LLM scoring & filtering...")
        from .llm_classify import score_and_filter_articles
        updates, score_stats = score_and_filter_articles(updates)
        logger.info(f"📊 LLM scoring: {score_stats['surviving']}/{score_stats['total']} surviving")
    else:
        logger.info("📊 Step 3/5: Heuristic scoring...")
        try:
            from .topic_cluster import cluster_articles, get_cluster_map
            topic_clusters = cluster_articles(updates)
            cluster_map = get_cluster_map(topic_clusters)
        except Exception as e:
            logger.warning(f"⚠️ WeChat clustering failed (non-fatal): {e}")

        try:
            from .editorial import run_editorial_pipeline
            from .config import EDITORIAL_ENABLED
            if EDITORIAL_ENABLED:
                updates, _ = run_editorial_pipeline(updates, cluster_map)
        except Exception as e:
            logger.warning(f"⚠️ WeChat editorial pipeline failed (non-fatal): {e}")

    if api_key:
        logger.info("📖 Step 4/5: Enriching WeChat articles...")
        updates = enrich_wechat_articles(updates)

    after_editorial = len(updates)
    wechat_metadata = _build_run_metadata(
        run_id,
        source_count=len(feed_list),
        candidate_count=candidate_count,
        after_dedup=len(raw_updates),
        after_editorial=after_editorial,
        included_count=after_editorial,
        extra=stats,
    )
    updates_path = save_workspace_updates("wechat", updates, wechat_metadata)

    if api_key:
        logger.info("📄 Step 5/5: AI summaries + report...")
        from .llm_services import summarize_wechat_batch
        ai_summaries = summarize_wechat_batch(updates)
        report = generate_wechat_report(updates, ai_summaries, metadata=stats)
    else:
        logger.info("📄 Step 5/5: Preliminary report (no AI summaries)...")
        report = generate_wechat_report(updates, metadata=stats)
        _log_no_api_key("wechat", updates_path)

    return report, {"total_articles": len(updates)}
