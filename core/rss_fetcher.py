"""
RSS 抓取模块
支持两种后端：
  1. feedparser 后端（GitHub Actions 模式，依赖 feedparser）
  2. stdlib 后端（Skill 模式，纯 Python 标准库，支持 ETag 缓存和并发）
"""

import json
import os
import re
import ssl
import time
import random
import hashlib
import urllib.request as urllib_request
import urllib.error as urllib_error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from collections import defaultdict, OrderedDict
from urllib.parse import urlparse, parse_qs, urlunparse

from .config import (
    normalize_category, get_category_display,
    CATEGORIES, CATEGORY_ORDER, LEGACY_CATEGORY_MAP,
)


# ============================================================
# HTML 解析（零依赖）
# ============================================================

class _HTMLStripper(HTMLParser):
    """HTML to plain text parser."""
    BLOCK_TAGS = frozenset({
        'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'blockquote', 'tr', 'ul', 'ol', 'table', 'hr', 'section', 'article'
    })
    SKIP_TAGS = frozenset({'style', 'script'})

    def __init__(self):
        super().__init__()
        self._pieces = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS and self._pieces and self._pieces[-1] != '\n':
            self._pieces.append('\n')

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BLOCK_TAGS:
            self._pieces.append('\n')

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._pieces.append(data)

    def handle_entityref(self, name):
        if self._skip_depth > 0:
            return
        entities = {
            'amp': '&', 'lt': '<', 'gt': '>', 'nbsp': '', 'quot': '"',
            'apos': "'", 'mdash': '—', 'ndash': '–', 'bull': '•'
        }
        self._pieces.append(entities.get(name, f'&{name};'))

    def handle_charref(self, name):
        if self._skip_depth > 0:
            return
        try:
            if name.startswith('x') or name.startswith('X'):
                char = chr(int(name[1:], 16))
            else:
                char = chr(int(name))
            self._pieces.append(char)
        except (ValueError, OverflowError):
            self._pieces.append(f'&#{name};')

    def get_text(self):
        text = ''.join(self._pieces)
        text = text.replace('\xa0', ' ')
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()


def strip_html(html_text):
    """将 HTML 转为纯文本（零依赖）"""
    if not html_text:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(html_text)
    return stripper.get_text()


def strip_html_with_bs4(html_text):
    """将 HTML 转为纯文本（使用 BeautifulSoup，需要安装）"""
    if not html_text:
        return ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        if len(text) > 2000:
            text = text[:2000] + "..."
        return text
    except ImportError:
        return strip_html(html_text)


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
# HTTP 抓取（支持 ETag 缓存）
# ============================================================

def create_ssl_context():
    """创建 SSL 上下文，支持降级"""
    try:
        return ssl.create_default_context()
    except Exception:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def fetch_url(url, cache=None, timeout=12):
    """抓取 URL，支持 ETag/If-Modified-Since 缓存"""
    headers = {"User-Agent": "DailyDigest/1.0"}
    cached = cache.get(url, {}) if cache else {}

    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
        headers["If-Modified-Since"] = cached["last_modified"]

    req = urllib_request.Request(url, headers=headers)
    new_cache = {}

    try:
        ctx = create_ssl_context()
        with urllib_request.urlopen(req, context=ctx, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            etag = resp.headers.get("ETag")
            last_mod = resp.headers.get("Last-Modified")
            if etag:
                new_cache["etag"] = etag
            if last_mod:
                new_cache["last_modified"] = last_mod
            return body, resp.status, new_cache
    except urllib_error.HTTPError as e:
        if e.code == 304:
            return None, 304, cached
        return None, e.code, {}
    except Exception:
        # SSL 降级重试
        try:
            relaxed = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            relaxed.check_hostname = False
            relaxed.verify_mode = ssl.CERT_NONE
            with urllib_request.urlopen(req, context=relaxed, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                etag = resp.headers.get("ETag")
                last_mod = resp.headers.get("Last-Modified")
                if etag:
                    new_cache["etag"] = etag
                if last_mod:
                    new_cache["last_modified"] = last_mod
                return body, resp.status, new_cache
        except urllib_error.HTTPError as e:
            if e.code == 304:
                return None, 304, cached
            return None, e.code, {}
        except Exception:
            return None, -1, {}


def fetch_url_with_retry(url, cache=None, timeout=12, max_retries=2):
    """带重试的 URL 抓取"""
    for attempt in range(max_retries + 1):
        body, status, new_cache = fetch_url(url, cache=cache, timeout=timeout)
        if body is not None or status == 304:
            return body, status, new_cache
        if attempt < max_retries:
            delay = (attempt + 1) * 2 + random.uniform(0, 1)
            time.sleep(delay)
    return None, -1, {}


# ============================================================
# RSS 解析（stdlib）
# ============================================================

def parse_rss_items(xml_text):
    """解析 RSS XML，返回条目列表"""
    items = []
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

def fetch_feeds_stdlib(feed_list, hours=24, workers=20, cache=None):
    """使用标准库并发抓取 RSS 源（Skill 模式）

    Args:
        feed_list: list of dict, 每个包含 name, url, category, language
        hours: 时间范围（小时）
        workers: 并发数
        cache: HTTP 缓存 dict

    Returns:
        (updates, stats, new_cache)
        - updates: list of article dicts
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
        body, status, feed_cache = fetch_url_with_retry(url, cache=new_cache.get(url, {}))
        if body is None:
            if status == 304:
                return feed_info, [], "not_modified", feed_cache
            return feed_info, [], f"HTTP {status}", feed_cache

        rss_items = parse_rss_items(body)
        articles = []
        for item in rss_items:
            pub_date_raw = item.get("pub_date_raw", "")
            pub_date = parse_rss_date(pub_date_raw)
            if pub_date and pub_date > cutoff_time:
                desc_html = item.get("content_encoded") or item.get("description") or ""
                full_text = strip_html(desc_html)
                summary_text = full_text[:2000] + ("..." if len(full_text) > 2000 else "")
                articles.append({
                    "title": item.get("title", "(no title)"),
                    "url": item.get("link", ""),
                    "published": pub_date.strftime("%Y-%m-%d %H:%M"),
                    "published_raw": pub_date_raw,
                    "description": summary_text,
                    "full_text": full_text,
                    "source_name": feed_info.get("name", "Unknown"),
                    "source_category": normalize_category(feed_info.get("category", "")),
                    "language": feed_info.get("language", "en"),
                    "hn_points": item.get("hn_points"),
                    "hn_comments": item.get("hn_comments"),
                    # 传递 feed_info 中的自定义元数据
                    "_feed_meta": {
                        k: v for k, v in feed_info.items()
                        if k.startswith("_") and k != "_feed_meta"
                    },
                })
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
                print(f"  [{completed}/{total}] ❌ {feed_info.get('name', '?')}: {error}", flush=True)
            else:
                stats["success_count"] += 1
                if articles:
                    print(f"  [{completed}/{total}] ✅ {feed_info.get('name', '?')}: {len(articles)} 新文章", flush=True)
                else:
                    print(f"  [{completed}/{total}] ⏭️  {feed_info.get('name', '?')}: 无更新", flush=True)
            if articles:
                all_articles.extend(articles)

    # 跨源去重：URL 标准化 + 标题相似度（使用词索引优化）
    deduped = []
    seen_urls = set()
    # 词级倒排索引：word -> set of indices in deduped
    word_index = {}
    _WORD_SPLIT = re.compile(r'[\s\-_:,;|/\\]+')

    for article in all_articles:
        norm_url = normalize_url(article.get("url", ""))
        if norm_url in seen_urls:
            continue
        # 标题相似度检查（通过倒排索引快速定位候选）
        title = article.get("title", "")
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
                    if title_similarity(title, deduped[idx].get("title", "")) > 0.85:
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
        print("[RSS] feedparser 未安装，回退到 stdlib 模式")
        updates, stats, _ = fetch_feeds_stdlib(feed_list, hours=hours)
        # 转换为 articles_by_category 格式
        articles_by_category = defaultdict(list)
        for article in updates:
            cat = article.get("source_category", "tech_general")
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

                article = {
                    "title": title,
                    "url": link,
                    "description": summary,
                    "author": author,
                    "published": pub_str,
                    "source_name": name,
                    "source_category": category,
                    "language": language,
                    "priority": priority,
                }
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
                print(f"  [{completed}/{total}] ❌ {name}: {error}", flush=True)
            else:
                stats["success"] += 1
                if articles:
                    all_articles[category].extend(articles)
                    stats["total_articles"] += len(articles)
                    print(f"  [{completed}/{total}] ✅ {name}: {len(articles)} 篇", flush=True)
                else:
                    print(f"  [{completed}/{total}] ⏭️  {name}: 无更新", flush=True)

    return dict(all_articles), stats
