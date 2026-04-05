"""
Pipeline orchestration for Daily Digest.

Provides cache I/O, report building, finalization, and per-source run
functions used by the CLI entry point in main.py.
"""

import json
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# HTTP cache helpers
# ---------------------------------------------------------------------------

def load_http_cache(name):
    """Load an HTTP cache dict from workspace/{name}.

    Returns (cache_dict, cache_path).
    """
    from .config import WORKSPACE_DIR
    cache_path = WORKSPACE_DIR / name
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f), cache_path
        except (json.JSONDecodeError, ValueError):
            print(f"[Cache] cache file corrupted, ignoring: {cache_path}")
    return {}, cache_path


def save_http_cache(cache_path, cache):
    """Save *cache* dict to *cache_path* atomically."""
    try:
        tmp_path = cache_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        tmp_path.replace(cache_path)
    except Exception as e:
        print(f"[Cache] cache save failed: {e}")


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------

def _demote_headings(lines, levels):
    """Add # prefix to heading lines to demote them by the given number of levels.

    Also normalizes # heading (h1) to h3 within demoted content, since AI-generated
    text may contain raw h1 headings that should not appear at the top level.
    """
    result = []
    for line in lines:
        match = re.match(r'^(#{1,6})\s', line)
        if match:
            hashes = match.group(1)
            new_level = min(len(hashes) + levels, 6)
            result.append('#' * new_level + line[len(hashes):])
        else:
            result.append(line)
    return result


def _make_anchor(heading_text):
    """Generate a GitHub-compatible anchor from heading text."""
    # Remove emojis and special unicode symbols
    text = re.sub(r'[\U00010000-\U0010ffff]', '', heading_text)
    # Remove punctuation except hyphens, underscores, and CJK
    text = re.sub(r'[^\w\u4e00-\u9fff\s-]', '', text)
    # Collapse whitespace into hyphens and lowercase
    text = re.sub(r'[\s]+', '-', text).strip().lower()
    return text


def strip_section_header_footer(content: str, demote_headings: int = 0) -> str:
    """Strip title/header lines and footer lines from a report section.

    Args:
        content: Markdown section content
        demote_headings: number of # levels to add (e.g. 2 turns # into ###)
    """
    lines = content.split("\n")
    # Skip header: only skip lines BEFORE the first --- separator.
    # This strips the # title, > metadata, empty lines, and the --- itself,
    # but preserves ## category headings that come after ---.
    start = 0
    found_first_sep = False
    for i, line in enumerate(lines):
        if line.strip() == "---":
            start = i + 1
            found_first_sep = True
            break
        # Part of the header (title, metadata, empty lines)
        start = i + 1
    # If no separator found, fall back to skipping # title + metadata
    if not found_first_sep:
        start = 0
        while start < len(lines) and (
            lines[start].startswith("# ")  # only top-level headings
            or lines[start].strip() == ""
            or lines[start].startswith(">")
        ):
            start += 1
    # Skip footer: empty lines, timestamp lines, separators
    end = len(lines)
    while end > start and (
        lines[end - 1].strip() == ""
        or "生成时间" in lines[end - 1]
        or "Generated" in lines[end - 1]
        or lines[end - 1].strip() == "---"
        or (lines[end - 1].strip().startswith("*") and "UTC" in lines[end - 1])
    ):
        end -= 1

    result_lines = lines[start:end]
    if demote_headings > 0:
        result_lines = _demote_headings(result_lines, demote_headings)

    return "\n".join(result_lines).strip()


def build_merged_report(sections, now, language="zh"):
    """Merge multiple sections into a single report with header and TOC.

    Each source section is cleaned (header/footer stripped, headings demoted
    by 3 levels so AI-generated ## becomes #### and ### becomes #####)
    and placed under a ## section title.  The TOC links to
    those top-level ## headings only.
    """
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    # Extract section names from each section's first heading
    section_names = []
    for section in sections:
        for line in section.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                section_names.append(stripped.lstrip("#").strip())
                break

    if language == "zh":
        header = f"# \U0001F4F0 Daily Digest — {date_str}\n\n"
        header += f"> \U0001F4E1 {' · '.join(section_names)}\n\n"
        header += f"> \U0001F550 生成时间 {time_str} UTC\n"
    else:
        header = f"# \U0001F4F0 Daily Digest — {date_str}\n\n"
        header += f"> \U0001F4E1 {' · '.join(section_names)}\n\n"
        header += f"> \U0001F550 Generated at {time_str} UTC\n"

    header += "\n---\n\n"

    # Build cleaned sections: each gets a ## section title + demoted body
    cleaned_sections = []
    all_headings = []
    for i, section in enumerate(sections):
        name = section_names[i] if i < len(section_names) else f"Section {i+1}"
        cleaned = strip_section_header_footer(section, demote_headings=3)
        if not cleaned:
            continue

        # Add ## section heading
        section_heading = f"## {name}"
        anchor = _make_anchor(name)
        all_headings.append((name, anchor))

        cleaned_sections.append(f"{section_heading}\n\n{cleaned}")

    # Build TOC from top-level ## section headings only
    toc_label = "## \U0001F4D1 目录" if language == "zh" else "## \U0001F4D1 Table of Contents"
    toc_lines = [toc_label, ""]
    for heading_text, anchor in all_headings:
        toc_lines.append(f"- [{heading_text}](#{anchor})")
    toc = "\n".join(toc_lines) + "\n"

    merged = header + toc + "\n---\n\n" + "\n\n---\n\n".join(cleaned_sections)
    # Collapse consecutive --- separators (with optional whitespace between)
    merged = re.sub(r'(\n---\n\s*){2,}', '\n---\n', merged)
    return merged


def build_unified_report(ai_articles, non_ai_articles, now, language="zh"):
    """Build a two-part unified report: AI deep analysis + non-AI tech news.

    Args:
        ai_articles: list of Article objects (AI-relevant)
        non_ai_articles: list of Article objects (non-AI)
        now: datetime with timezone
        language: "zh" or "en"

    Returns:
        Markdown string of the complete unified report
    """
    from .ai_report import build_ai_section
    from .report_generator import build_non_ai_section

    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    ai_count = len(ai_articles)
    non_ai_count = len(non_ai_articles)
    total = ai_count + non_ai_count

    if language == "zh":
        header = f"# 📰 Daily Digest — {date_str}\n\n"
        header += f"> 🤖 AI 深度分析 {ai_count} 篇 · 💻 科技动态 {non_ai_count} 条 · 共 {total} 篇\n\n"
        header += f"> ⏰ 生成时间 {time_str} UTC\n"
    else:
        header = f"# 📰 Daily Digest — {date_str}\n\n"
        header += f"> 🤖 AI Deep Analysis {ai_count} articles · 💻 Tech Updates {non_ai_count} items · Total {total}\n\n"
        header += f"> ⏰ Generated at {time_str} UTC\n"

    header += "\n---\n\n"

    # Part I: AI Deep Digest
    ai_section = build_ai_section(ai_articles, language)

    # Part II: Non-AI Tech Updates
    non_ai_section = build_non_ai_section(non_ai_articles, language)

    # Combine parts
    parts = []
    if ai_section:
        parts.append(ai_section)
    if non_ai_section:
        parts.append(non_ai_section)

    if not parts:
        return ""

    return header + "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Finalize helpers
# ---------------------------------------------------------------------------

def _load_workspace_data(source_type):
    """Load {source_type}_updates.json from workspace.  Returns dict or None."""
    from .config import WORKSPACE_DIR
    path = WORKSPACE_DIR / f"{source_type}_updates.json"
    if not path.exists():
        print(f"\u26a0\ufe0f workspace/{source_type}_updates.json not found; run fetch first.")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _merge_batch_summaries(source_type):
    """Glob {source_type}_summary_batch_*.json and merge into a single dict."""
    from .config import WORKSPACE_DIR
    summary_map = {}
    for p in sorted(WORKSPACE_DIR.glob(f"{source_type}_summary_batch_*.json")):
        with open(p, "r", encoding="utf-8") as f:
            batch = json.load(f)
        if source_type == "podcast":
            for url, summary in batch.items():
                summary_map[url] = summary
        else:
            items = batch.get("summaries", [])
            for item in items:
                url = item.get("url") or item.get("article_url", "")
                if url:
                    summary_map[url] = item if source_type == "tech" else item.get("ai_summary", "")
    return summary_map


def _generate_source_report(source_type, data, summaries, language):
    """Dispatch to the correct report generator and return the markdown string."""
    from .article import Article

    updates = [Article(**u) for u in data.get("updates", [])]
    metadata = data.get("metadata", {})

    if source_type == "tech":
        # Also load trend insight if present
        from .config import WORKSPACE_DIR
        trend_path = WORKSPACE_DIR / "tech_trend_insight.json"
        trend_insight = None
        if trend_path.exists():
            with open(trend_path, "r", encoding="utf-8") as f:
                trend_insight = json.load(f)
        from .report_generator import generate_tech_report
        report = generate_tech_report(updates, summaries, trend_insight, metadata, language)
        print(f"\u2705 tech report generated ({len(updates)} articles)")
        return report

    if source_type == "podcast":
        from .podcast_utils import generate_podcast_report
        report = generate_podcast_report(updates, summaries, metadata=metadata)
        print(f"\u2705 podcast report generated ({len(summaries)} summaries)")
        return report

    if source_type == "wechat":
        from .wechat_utils import generate_wechat_report
        report = generate_wechat_report(updates, summaries, metadata=metadata)
        print(f"\u2705 wechat report generated ({len(summaries)} summaries)")
        return report

    raise ValueError(f"Unknown source_type: {source_type}")


def _finalize_source(source_type, language="zh"):
    """Unified finalizer for a single source type.  Returns report string or None."""
    data = _load_workspace_data(source_type)
    if data is None:
        return None
    summaries = _merge_batch_summaries(source_type)
    return _generate_source_report(source_type, data, summaries, language)


def finalize_reports(source, language="zh"):
    """--finalize mode: read sub-agent summaries from workspace/ and build final reports."""
    from .config import OUTPUT_DIR
    from .report_generator import save_report

    now = datetime.now(timezone.utc)

    sections = []
    for src in ("tech", "podcast", "wechat"):
        if source in (src, "all"):
            report = _finalize_source(src, language)
            if report:
                sections.append(report)

    if not sections:
        print("\u26a0\ufe0f no reports to generate.")
        return

    # Try unified two-part report
    unified = None
    if os.environ.get("API_KEY"):
        from .article import Article
        from .ai_filter import filter_ai_articles

        all_articles = []
        for src in ("tech", "podcast", "wechat"):
            if source in (src, "all"):
                data = _load_workspace_data(src)
                if data:
                    for item in data.get("updates", []):
                        try:
                            all_articles.append(Article(**item))
                        except Exception:
                            continue

        if all_articles:
            print(f"\n\U0001F916 Building unified AI + non-AI report from {len(all_articles)} articles...")
            ai_articles, non_ai_articles = filter_ai_articles(all_articles)
            unified = build_unified_report(ai_articles, non_ai_articles, now, language)

    if unified:
        merged = unified
    else:
        merged = build_merged_report(sections, now, language)

    filepath = save_report(merged, f"{now.strftime('%Y-%m-%d')}.md", OUTPUT_DIR,
                           report_type="digest", language=language)

    print("\n" + "=" * 60)
    print(f"\u2705 Finalize done! report: {filepath}")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Per-source pipeline runners
# ---------------------------------------------------------------------------

def run_tech(hours=48, language="zh", limit=None):
    """Tech news pipeline.  Returns (report_str, stats_dict) or None."""
    from .config import load_feed_config, ensure_dirs, OUTPUT_DIR, WORKSPACE_DIR
    from .dedup import filter_and_mark, cleanup_old_entries

    ensure_dirs(OUTPUT_DIR, WORKSPACE_DIR)

    # Step 1: Fetch RSS
    print("\n\U0001F4E1 Step 1/4: Fetching tech RSS...")
    config = load_feed_config("tech")
    feed_list = [
        {"name": f["name"], "url": f["url"], "category": c["name"],
         "language": f.get("language", "en"), "priority": f.get("priority", 3)}
        for c in config.get("categories", [])
        for f in c.get("feeds", [])
    ]
    if limit:
        feed_list = feed_list[:limit]
        print(f"   (limit mode: first {limit} sources)")
    settings = config.get("settings", {})

    api_key = os.environ.get("API_KEY")
    if api_key:
        from .rss_fetcher import fetch_feeds_feedparser
        articles_by_category, fetch_stats = fetch_feeds_feedparser(
            feed_list, hours=settings.get("hours_back", hours),
            max_per_feed=settings.get("max_articles_per_feed", 10)
        )
    else:
        from .rss_fetcher import fetch_feeds_stdlib
        cache, cache_path = load_http_cache(".http_cache.json")
        updates, stats, new_cache = fetch_feeds_stdlib(feed_list, hours=hours, workers=20, cache=cache)
        save_http_cache(cache_path, new_cache)
        articles_by_category = {}
        for u in updates:
            cat = u.category
            articles_by_category.setdefault(cat, []).append(u)
        fetch_stats = stats

    all_articles = [a for arts in articles_by_category.values() for a in arts]
    if not all_articles:
        print("\u26a0\ufe0f no articles fetched.")
        return None

    # Step 2: Dedup
    print("\U0001F50D Step 2/4: Dedup...")
    cleanup_old_entries(days=30)
    new_articles = filter_and_mark(all_articles)
    if not new_articles:
        print("\u26a0\ufe0f all articles already processed.")
        return None

    new_by_category = {}
    for a in new_articles:
        cat = a.category
        new_by_category.setdefault(cat, []).append(a)
    print(f"\u2705 {len(new_articles)} new articles")

    # Step 3: AI summaries
    print("\U0001F916 Step 3/4: AI summaries...")
    if api_key:
        from .ai_summarizer import summarize_all_categories
        category_results, executive_summary = summarize_all_categories(new_by_category, language)
        if not category_results:
            print("\u26a0\ufe0f AI summary failed.")
            return None
        from .report_generator import generate_tech_report
        report_stats = {"total_articles": len(new_articles), "categories": len(category_results)}
        report = generate_tech_report(
            new_articles,
            category_results=category_results,
            executive_summary=executive_summary,
            stats=report_stats,
            report_language=language,
        )
    else:
        from .report_generator import generate_tech_report
        updates_path = WORKSPACE_DIR / "tech_updates.json"
        with open(updates_path, "w", encoding="utf-8") as f:
            json.dump({"metadata": fetch_stats, "updates": [asdict(a) for a in new_articles]}, f, ensure_ascii=False, indent=2)
        print(f"\U0001F4A1 no API_KEY, raw data saved to {updates_path}")
        print("   Run sub-agent summaries, then:")
        print(f"   python main.py --source tech --finalize")
        return None

    # Step 4: Report
    print("\U0001F4C4 Step 4/4: Generating report...")
    return report, {"total_articles": len(new_articles), "categories": len(new_by_category)}


def run_podcast(hours=24, limit=None):
    """Podcast pipeline.  Returns (report_str, stats_dict) or None."""
    from .config import CONFIG_DIR, ensure_dirs, OUTPUT_DIR, WORKSPACE_DIR
    from .rss_fetcher import fetch_feeds_stdlib
    from .podcast_utils import resolve_xiaoyuzhou_urls, generate_podcast_report
    from .dedup import filter_and_mark

    ensure_dirs(OUTPUT_DIR, WORKSPACE_DIR)
    api_key = os.environ.get("API_KEY")

    print("\n\U0001F399\ufe0f Step 1/3: Checking podcast updates...")
    with open(CONFIG_DIR / "podcast_feeds.json", "r", encoding="utf-8") as f:
        pdata = json.load(f)

    podcasts = pdata.get("podcasts", [])[:pdata.get("settings", {}).get("count", 1000)]
    if limit:
        podcasts = podcasts[:limit]
        print(f"   (limit mode: first {limit} podcasts)")
    feed_list = [
        {"name": p["name"], "url": p["url"], "category": "podcast", "language": "zh",
         "_podcast_meta": {"rank": p.get("rank", 0), "xiaoyuzhou_url": p.get("xiaoyuzhou_url", "")}}
        for p in podcasts if p.get("url")
    ]

    cache, cache_path = load_http_cache(".podcast_http_cache.json")
    raw_updates, stats, new_cache = fetch_feeds_stdlib(feed_list, hours=hours, workers=30, cache=cache)
    save_http_cache(cache_path, new_cache)

    raw_updates = filter_and_mark(raw_updates)
    if not raw_updates:
        print("\u26a0\ufe0f no podcast updates.")
        return None

    for u in raw_updates:
        meta = u.extra.get("_feed_meta", {}).get("_podcast_meta", {})
        u.extra["rank"] = meta.get("rank", 0)
        u.extra["xiaoyuzhou_url"] = meta.get("xiaoyuzhou_url", "")

    print(f"\u2705 {len(raw_updates)} podcast updates")

    print("\U0001F517 Step 2/3: Resolving xiaoyuzhou URLs...")
    updates = resolve_xiaoyuzhou_urls(raw_updates)

    updates_path = WORKSPACE_DIR / "podcast_updates.json"
    with open(updates_path, "w", encoding="utf-8") as f:
        json.dump({"metadata": stats, "updates": [asdict(u) for u in updates]}, f, ensure_ascii=False, indent=2)

    if api_key:
        print("\U0001F4C4 Step 3/3: AI summaries + report...")
        from .ai_summarizer import summarize_podcast_batch
        ai_summaries = summarize_podcast_batch(updates)
        report = generate_podcast_report(updates, ai_summaries, metadata=stats)
    else:
        print("\U0001F4C4 Step 3/3: Preliminary report (no AI summaries)...")
        report = generate_podcast_report(updates, metadata=stats)
        print(f"\U0001F4A1 no API_KEY, raw data saved to {updates_path}")
        print("   Run sub-agent summaries, then:")
        print(f"   python main.py --source podcast --finalize")

    return report, {"total_episodes": len(updates)}


def run_wechat(hours=24, limit=None):
    """WeChat pipeline.  Returns (report_str, stats_dict) or None."""
    from .config import ensure_dirs, OUTPUT_DIR, WORKSPACE_DIR
    from .wechat_utils import fetch_wechat_feed_list, generate_wechat_report, enrich_wechat_articles
    from .rss_fetcher import fetch_feeds_stdlib
    from .dedup import filter_and_mark

    ensure_dirs(OUTPUT_DIR, WORKSPACE_DIR)
    api_key = os.environ.get("API_KEY")

    print("\n\U0001F4F1 Step 1/3: Fetching WeChat feed list...")
    feed_data = fetch_wechat_feed_list()
    feeds = [f for f in feed_data.get("feeds", []) if f.get("active")]
    if limit:
        feeds = feeds[:limit]
        print(f"   (limit mode: first {limit} accounts)")
    feed_list = [
        {"name": f["name"], "url": f["url"], "category": f.get("category", "其他"), "language": "zh",
         "_wechat_meta": {"index": f.get("index", 0)}}
        for f in feeds
    ]

    print("\U0001F4E1 Step 2/3: Checking WeChat updates...")
    cache, cache_path = load_http_cache(".wechat_http_cache.json")
    raw_updates, stats, new_cache = fetch_feeds_stdlib(feed_list, hours=hours, workers=10, cache=cache)
    save_http_cache(cache_path, new_cache)

    raw_updates = filter_and_mark(raw_updates)
    if not raw_updates:
        print("\u26a0\ufe0f no WeChat updates.")
        return None

    updates = raw_updates
    print(f"\u2705 {len(updates)} WeChat updates")

    if api_key:
        print("\U0001F4D6 Enriching WeChat articles with full text...")
        updates = enrich_wechat_articles(updates)

    updates_path = WORKSPACE_DIR / "wechat_updates.json"
    with open(updates_path, "w", encoding="utf-8") as f:
        json.dump({"metadata": stats, "updates": [asdict(a) for a in updates]}, f, ensure_ascii=False, indent=2)

    if api_key:
        print("\U0001F4C4 Step 3/3: AI summaries + report...")
        from .ai_summarizer import summarize_wechat_batch
        ai_summaries = summarize_wechat_batch(updates)
        report = generate_wechat_report(updates, ai_summaries, metadata=stats)
    else:
        print("\U0001F4C4 Step 3/3: Preliminary report (no AI summaries)...")
        report = generate_wechat_report(updates, metadata=stats)
        print(f"\U0001F4A1 no API_KEY, raw data saved to {updates_path}")
        print("   Run sub-agent summaries, then:")
        print(f"   python main.py --source wechat --finalize")

    return report, {"total_articles": len(updates)}
