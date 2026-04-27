"""
RSS 抓取模块
使用 feedparser 后端抓取 RSS 源（依赖 feedparser 库）
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from urllib.parse import urlparse, parse_qs, urlunparse

from .article import Article
from .config import normalize_category
from .http import fetch_url_with_retry, error_label
from .html_utils import strip_html_with_bs4
from .logging_config import get_logger

logger = get_logger("rss")


# ============================================================
# 日期解析 — canonical implementation in date_utils.py
# ============================================================

from .date_utils import is_within_time  # noqa: E402


# is_within_time re-exported from date_utils above


# ============================================================
# URL 标准化与去重
# ============================================================

_TRACKING_PARAMS = re.compile(
    r'^(utm_[a-z]+|ref|source|fbclid|gclid|mc_eid|campaign|medium|content|term)$',
    re.IGNORECASE
)


def normalize_url(url):
    """标准化 URL：移除追踪参数和 fragment"""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=True)
            clean_params = {k: v for k, v in params.items() if not _TRACKING_PARAMS.match(k)}
            if clean_params:
                parts = []
                for k, vs in sorted(clean_params.items()):
                    for v in vs:
                        parts.append(f"{k}={v}")
                query = "&".join(parts)
            else:
                query = ""
        else:
            query = ""
        path = parsed.path.rstrip("/")
        return urlunparse((parsed.scheme, parsed.netloc.lower(), path, parsed.params, query, ""))
    except Exception:
        return url.lower().strip()


def title_similarity(t1, t2):
    """计算两个标题的 Jaccard 相似度"""
    if not t1 or not t2:
        return 0.0
    pattern = re.compile(r'[\s\-_:,;|/\\]+')
    words1 = set(pattern.split(t1.lower().strip()))
    words2 = set(pattern.split(t2.lower().strip()))
    words1.discard("")
    words2.discard("")
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)


def fetch_feeds_feedparser(feed_list, hours=48, max_per_feed=10):
    """使用 feedparser 抓取 RSS 源（GitHub Actions 模式）

    Args:
        feed_list: list of dict, 每个包含 name, url, category, language, priority
        hours: 时间范围（小时）
        max_per_feed: 每个源最大文章数

    Returns:
        (articles_by_category, stats)
    """
    import feedparser

    all_articles = defaultdict(list)
    from .feed_health import batch_health as _batch_health_fp
    stats = {
        "total_feeds": len(feed_list),
        "success": 0,
        "failed": 0,
        "total_articles": 0,
    }

    def _parse_single_feed(feed):
        """解析单个 feed，返回 (name, category, language, priority, articles, error)"""
        name = feed.get("name", "Unknown")
        category = normalize_category(feed.get("category", "tech_general"))
        language = feed.get("language", "en")
        priority = feed.get("priority", 3)
        url = feed["url"]

        max_count = {
            1: max_per_feed,
            2: max(1, int(max_per_feed * 0.7)),
            3: max(1, int(max_per_feed * 0.5)),
        }.get(priority, max_per_feed)

        # Check feed health — skip consistently failing feeds
        from .feed_health import is_healthy, record_success, record_failure
        if not is_healthy(url):
            return name, category, language, priority, [], "skipped_unhealthy"

        try:
            # Use HTTP client with timeout + retry, then parse the response body
            from .http import fetch_url_with_retry, error_label
            body, status, _ = fetch_url_with_retry(url, headers={
                "User-Agent": "DailyDigest/1.0"
            })
            if body is None:
                label = error_label(status)
                logger.warning(f"  {name}: fetch failed ({label})")
                record_failure(url, label)
                return name, category, language, priority, [], label
            d = feedparser.parse(body)
            if not d.entries:
                # Check for parse-level errors (bozo bit)
                if d.get("bozo") and d.get("bozo_exception"):
                    return name, category, language, priority, [], str(d.bozo_exception)[:200]
                return name, category, language, priority, [], None

            articles = []
            count = 0
            for entry in d.entries:
                if count >= max_count:
                    break

                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if not is_within_time(published, hours):
                    continue

                title = entry.get("title", "").strip()
                link = entry.get("link", "")
                if not title or not link:
                    continue

                summary_html = entry.get("summary", "") or entry.get("description", "")
                summary = strip_html_with_bs4(summary_html)
                author = entry.get("author", "") or entry.get("dc_creator", "")
                pub_str = entry.get("published", "") or entry.get("updated", "")

                article = Article(
                    title=title,
                    url=link,
                    source=name,
                    category=category,
                    description=summary,
                    published=pub_str,
                    language=language,
                    extra={
                        "author": author,
                        "priority": priority,
                        "_feed_meta": {
                            k: v for k, v in feed.items()
                            if k.startswith("_") and k != "_feed_meta"
                        },
                    },
                )
                articles.append(article)
                count += 1

            record_success(url)
            return name, category, language, priority, articles, None
        except Exception as e:
            record_failure(url, str(e))
            return name, category, language, priority, [], str(e)

    # 并发抓取
    workers = min(20, max(5, len(feed_list) // 10))
    completed = 0
    total = len(feed_list)
    with _batch_health_fp():
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_parse_single_feed, feed): feed for feed in feed_list}
            for future in as_completed(futures):
                name, category, language, priority, articles, error = future.result()
                completed += 1
                if error == "skipped_unhealthy":
                    stats["skipped"] = stats.get("skipped", 0) + 1
                    logger.info(f"  [{completed}/{total}] ⏭️  {name}: 跳过(连续失败)")
                elif error:
                    stats["failed"] += 1
                    logger.error(f"  [{completed}/{total}] ❌ {name}: {error}")
                else:
                    stats["success"] += 1
                    if articles:
                        all_articles[category].extend(articles)
                        stats["total_articles"] += len(articles)
                        logger.info(f"  [{completed}/{total}] ✅ {name}: {len(articles)} 篇")
                    else:
                        logger.info(f"  [{completed}/{total}] ⏭️  {name}: 无更新")

    return dict(all_articles), stats
