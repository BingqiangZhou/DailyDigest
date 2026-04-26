"""
RSS 抓取模块
支持两种后端：
  1. feedparser 后端（GitHub Actions 模式，依赖 feedparser）
  2. stdlib 后端（Skill 模式，纯 Python 标准库，支持 ETag 缓存和并发）
"""

import json
import os
import re
import time
import hashlib
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from collections import defaultdict, OrderedDict
from urllib.parse import urlparse, parse_qs, urlunparse

from .article import Article
from .config import (
    normalize_category, get_category_display,
    CATEGORIES, CATEGORY_ORDER, LEGACY_CATEGORY_MAP,
)
from .http import create_ssl_context, fetch_url, fetch_url_with_retry, error_label
from .html_utils import strip_html, strip_html_with_bs4
from .logging_config import get_logger

logger = get_logger("rss")


# ============================================================
# 日期解析
# ============================================================

_RSS_DATE_FORMATS = [
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S GMT",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
]


def parse_rss_date(date_str):
    """解析 RSS 日期字符串为 datetime 对象"""
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in _RSS_DATE_FORMATS:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    # 尝试去掉时区后缀
    for suffix in (" +0000", " -0000", " UTC", " GMT"):
        if date_str.endswith(suffix):
            trimmed = date_str[: -len(suffix)]
            for fmt in _RSS_DATE_FORMATS:
                try:
                    dt = datetime.strptime(trimmed, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except ValueError:
                    continue
    return None


def is_within_time(published_time, hours_back):
    """检查文章是否在时间范围内"""
    if not published_time:
        return True
    try:
        if isinstance(published_time, time.struct_time):
            pub_dt = datetime(*published_time[:6], tzinfo=timezone.utc)
        elif isinstance(published_time, datetime):
            pub_dt = published_time
        else:
            pub_dt = parse_rss_date(str(published_time))
        if pub_dt is None:
            return True
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        return pub_dt >= cutoff
    except (ValueError, TypeError):
        return True


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


# ============================================================
# RSS 解析（stdlib）
# ============================================================

def parse_rss_items(xml_text):
    """解析 RSS XML，返回条目列表"""
    items = []
    # 预处理常见 HTML 实体（ET.fromstring 不支持）
    xml_text = re.sub(r'&nbsp;', ' ', xml_text)
    xml_text = re.sub(r'&mdash;', '—', xml_text)
    xml_text = re.sub(r'&ndash;', '–', xml_text)
    xml_text = re.sub(r'&copy;', '©', xml_text)
    xml_text = re.sub(r'&reg;', '®', xml_text)
    xml_text = re.sub(r'&laquo;', '«', xml_text)
    xml_text = re.sub(r'&raquo;', '»', xml_text)
    xml_text = re.sub(r'&hellip;', '…', xml_text)
    xml_text = re.sub(r'&ldquo;', '"', xml_text)
    xml_text = re.sub(r'&rdquo;', '"', xml_text)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    # RSS 2.0
    for item in root.iter("item"):
        entry = {}
        title_el = item.find("title")
        if title_el is not None and title_el.text:
            entry["title"] = title_el.text.strip()
        link_el = item.find("link")
        if link_el is not None and link_el.text:
            entry["link"] = link_el.text.strip()
        pub_el = item.find("pubDate")
        if pub_el is not None and pub_el.text:
            entry["pub_date_raw"] = pub_el.text.strip()
        desc_el = item.find("description")
        if desc_el is not None and desc_el.text:
            entry["description"] = desc_el.text.strip()
        # content:encoded
        for child in item:
            tag = child.tag
            if tag.endswith("}encoded") or tag == "content:encoded":
                if child.text:
                    entry["content_encoded"] = child.text.strip()
                    break
        # HN 特殊字段
        desc_text = entry.get("description", "")
        if desc_text:
            m_points = re.search(r'Points:\s*(\d+)', desc_text)
            if m_points:
                entry["hn_points"] = int(m_points.group(1))
            m_comments = re.search(r'# Comments:\s*(\d+)', desc_text)
            if m_comments:
                entry["hn_comments"] = int(m_comments.group(1))
        items.append(entry)

    # Atom feeds
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry_elem in root.findall(".//atom:entry", ns):
        item = {}
        title_el = entry_elem.find("atom:title", ns)
        if title_el is not None and title_el.text:
            item["title"] = title_el.text.strip()
        link_el = entry_elem.find("atom:link", ns)
        if link_el is not None:
            item["link"] = link_el.get("href", "").strip()
        # 优先选择 rel="alternate" 的链接
        for link_el in entry_elem.findall("atom:link", ns):
            rel = link_el.get("rel", "")
            href = link_el.get("href", "").strip()
            if href and (rel == "alternate" or (not rel and not item.get("link"))):
                item["link"] = href
        published_el = entry_elem.find("atom:published", ns)
        if published_el is not None and published_el.text:
            item["pub_date_raw"] = published_el.text.strip()
        elif entry_elem.find("atom:updated", ns) is not None:
            updated_el = entry_elem.find("atom:updated", ns)
            if updated_el.text:
                item["pub_date_raw"] = updated_el.text.strip()
        summary_el = entry_elem.find("atom:summary", ns)
        if summary_el is not None and summary_el.text:
            item["description"] = summary_el.text.strip()
        content_el = entry_elem.find("atom:content", ns)
        if content_el is not None and content_el.text:
            item["content_encoded"] = content_el.text.strip()
        items.append(item)

    return items


# ============================================================
# 统一抓取接口
# ============================================================

def fetch_feeds_stdlib(feed_list, hours=24, workers=20, cache=None,
                       timeout=None, max_per_source=30):
    """使用标准库并发抓取 RSS 源（Skill 模式）

    Args:
        feed_list: list of dict, 每个包含 name, url, category, language
        hours: 时间范围（小时）
        workers: 并发数
        cache: HTTP 缓存 dict
        timeout: 单次请求超时秒数（None 使用默认值）
        max_per_source: 每个源最大文章数（防止 ArXiv 等高产源淹没报告）

    Returns:
        (updates, stats, new_cache)
        - updates: list of Article objects
        - stats: dict with metadata
        - new_cache: updated HTTP cache
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    new_cache = dict(cache) if cache else {}
    all_articles = []
    stats = {
        "checked_count": len(feed_list),
        "success_count": 0,
        "error_count": 0,
        "not_modified_count": 0,
        "update_count": 0,
        "hours": hours,
        "check_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }

    def check_single_feed(feed_info):
        url = feed_info["url"]
        body, status, feed_cache = fetch_url_with_retry(url, cache=new_cache.get(url, {}),
                                                         timeout=timeout)
        if body is None:
            if status == 304:
                return feed_info, [], "not_modified", feed_cache
            return feed_info, [], f"HTTP {status}", feed_cache

        rss_items = parse_rss_items(body)
        articles = []
        for item in rss_items:
            if len(articles) >= feed_info.get("max_articles", max_per_source):
                break
            pub_date_raw = item.get("pub_date_raw", "")
            pub_date = parse_rss_date(pub_date_raw)
            if pub_date and pub_date > cutoff_time:
                desc_html = item.get("content_encoded") or item.get("description") or ""
                full_text = strip_html(desc_html)
                summary_text = full_text[:2000] + ("..." if len(full_text) > 2000 else "")
                articles.append(Article(
                    title=item.get("title", "(no title)"),
                    url=item.get("link", ""),
                    published=pub_date.strftime("%Y-%m-%d %H:%M"),
                    source=feed_info.get("name", "Unknown"),
                    category=normalize_category(feed_info.get("category", "")),
                    description=summary_text,
                    full_text=full_text,
                    language=feed_info.get("language", "en"),
                    extra={
                        "published_raw": pub_date_raw,
                        "hn_points": item.get("hn_points"),
                        "hn_comments": item.get("hn_comments"),
                        "priority": feed_info.get("priority", 3),
                        "_feed_meta": {
                            k: v for k, v in feed_info.items()
                            if k.startswith("_") and k != "_feed_meta"
                        },
                    },
                ))
        return feed_info, articles, None, feed_cache

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(check_single_feed, feed): feed for feed in feed_list}
        completed = 0
        total = len(futures)
        for future in as_completed(futures):
            feed_info, articles, error, feed_cache = future.result()
            completed += 1
            if feed_cache:
                new_cache[feed_info["url"]] = feed_cache
            if error == "not_modified":
                stats["not_modified_count"] += 1
            elif error:
                stats["error_count"] += 1
                label = error_label(int(error.split()[-1])) if error.startswith("HTTP -") else error
                logger.error(f"  [{completed}/{total}] ❌ {feed_info.get('name', '?')}: {label}")
            else:
                stats["success_count"] += 1
                if articles:
                    logger.info(f"  [{completed}/{total}] ✅ {feed_info.get('name', '?')}: {len(articles)} 新文章")
                else:
                    logger.info(f"  [{completed}/{total}] ⏭️  {feed_info.get('name', '?')}: 无更新")
            if articles:
                all_articles.extend(articles)

    # 跨源去重：URL 标准化 + 标题相似度（使用词索引优化）
    deduped = []
    seen_urls = set()
    # 词级倒排索引：word -> set of indices in deduped
    word_index = {}
    _WORD_SPLIT = re.compile(r'[\s\-_:,;|/\\]+')

    for article in all_articles:
        raw_url = article.url
        if not raw_url:
            # 空 URL 的文章跳过 URL 去重，直接保留
            deduped.append(article)
            continue
        norm_url = normalize_url(raw_url)
        if norm_url in seen_urls:
            continue
        # 标题相似度检查（通过倒排索引快速定位候选）
        title = article.title
        is_dup = False
        if title:
            words = set(_WORD_SPLIT.split(title.lower().strip()))
            words.discard("")
            if words:
                # 找出共享至少一个词的候选文章
                candidate_indices = set()
                for w in words:
                    if w in word_index:
                        candidate_indices.update(word_index[w])
                # 只对候选文章计算 Jaccard 相似度
                for idx in candidate_indices:
                    if title_similarity(title, deduped[idx].title) > 0.85:
                        is_dup = True
                        break
                if not is_dup:
                    # 将此文章的词加入索引
                    for w in words:
                        word_index.setdefault(w, set()).add(len(deduped))
        if not is_dup:
            seen_urls.add(norm_url)
            deduped.append(article)

    stats["update_count"] = len(deduped)
    return deduped, stats, new_cache


def fetch_feeds_feedparser(feed_list, hours=48, max_per_feed=10):
    """使用 feedparser 抓取 RSS 源（GitHub Actions 模式）

    Args:
        feed_list: list of dict, 每个包含 name, url, category, language, priority
        hours: 时间范围（小时）
        max_per_feed: 每个源最大文章数

    Returns:
        (articles_by_category, stats)
    """
    try:
        import feedparser
    except ImportError:
        logger.warning("[RSS] feedparser 未安装，回退到 stdlib 模式")
        updates, stats, _ = fetch_feeds_stdlib(feed_list, hours=hours)
        # 转换为 articles_by_category 格式
        articles_by_category = defaultdict(list)
        for article in updates:
            cat = article.category
            articles_by_category[cat].append(article)
        return dict(articles_by_category), stats

    all_articles = defaultdict(list)
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

        try:
            d = feedparser.parse(url, request_headers={
                "User-Agent": "DailyDigest/1.0"
            })
            if not d.entries:
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
                    },
                )
                articles.append(article)
                count += 1

            return name, category, language, priority, articles, None
        except Exception as e:
            return name, category, language, priority, [], str(e)

    # 并发抓取
    workers = min(20, max(5, len(feed_list) // 10))
    completed = 0
    total = len(feed_list)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_parse_single_feed, feed): feed for feed in feed_list}
        for future in as_completed(futures):
            name, category, language, priority, articles, error = future.result()
            completed += 1
            if error:
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
