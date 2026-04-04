"""
文章去重模块
支持两种去重策略：
  1. URL SHA-256 哈希去重（跨运行持久化）
  2. URL 标准化 + Jaccard 标题相似度去重（单次运行内）
"""

import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .config import WORKSPACE_DIR


TRACKER_FILE = WORKSPACE_DIR / "processed_articles.json"


def _load_tracker():
    """加载追踪记录"""
    if TRACKER_FILE.exists():
        try:
            with open(TRACKER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"articles": {}}
    return {"articles": {}}


def _save_tracker(tracker):
    """保存追踪记录"""
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRACKER_FILE, "w", encoding="utf-8") as f:
        json.dump(tracker, f, ensure_ascii=False, indent=2)


def article_id(article):
    """生成文章唯一 ID（基于标准化 URL 的 SHA-256 哈希）"""
    raw_url = article.get("link", "") or article.get("url", "")
    # 标准化 URL：去除追踪参数、统一大小写等
    url = _normalize_url_for_dedup(raw_url)
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _normalize_url_for_dedup(url):
    """简化版 URL 标准化（用于去重，避免循环导入 rss_fetcher）"""
    from urllib.parse import urlparse, parse_qs, urlencode
    parsed = urlparse(url)
    # 去除常见追踪参数
    tracking_params = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
                       "ref", "source", "from", "fbclid", "gclid"}
    params = {k: v for k, v in parse_qs(parsed.query).items() if k.lower() not in tracking_params}
    query = urlencode(params, doseq=True) if params else ""
    # 统一路径（去尾斜杠）
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}?{query}" if query else f"{parsed.scheme}://{parsed.netloc}{path}"


def filter_and_mark(articles):
    """过滤新文章并一次性标记为已处理（合并 filter + mark，减少 IO）"""
    if not articles:
        return []

    tracker = _load_tracker()
    processed = tracker.get("articles", {})
    new_articles = []
    now = datetime.now(timezone.utc).isoformat()

    for article in articles:
        aid = article_id(article)
        if aid not in processed:
            new_articles.append(article)
            processed[aid] = {
                "title": (article.get("title", "") or "")[:100],
                "source": article.get("source_name", article.get("source", "")),
                "processed_at": now,
            }

    tracker["articles"] = processed
    _save_tracker(tracker)
    print(f"[Dedup] 总文章: {len(articles)}, 新文章: {len(new_articles)}, 已标记")
    return new_articles


def cleanup_old_entries(days=30):
    """清理过期的追踪记录"""
    tracker = _load_tracker()
    articles = tracker.get("articles", {})
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)

    to_remove = []
    for aid, info in articles.items():
        try:
            processed_time = datetime.fromisoformat(info["processed_at"]).timestamp()
            if processed_time < cutoff:
                to_remove.append(aid)
        except (KeyError, ValueError):
            to_remove.append(aid)

    for aid in to_remove:
        del articles[aid]

    if to_remove:
        _save_tracker(tracker)
        print(f"[Dedup] 清理了 {len(to_remove)} 条过期记录")

    return len(to_remove)
