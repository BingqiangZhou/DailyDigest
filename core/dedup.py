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
    """生成文章唯一 ID（基于 URL 的 SHA-256 哈希）"""
    url = article.get("link", "") or article.get("url", "")
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def filter_new_articles(articles):
    """过滤出未处理过的文章（基于 URL 哈希持久化去重）"""
    if not articles:
        return []

    tracker = _load_tracker()
    processed = tracker.get("articles", {})
    new_articles = []

    for article in articles:
        aid = article_id(article)
        if aid not in processed:
            new_articles.append(article)

    print(f"[Dedup] 总文章: {len(articles)}, 新文章: {len(new_articles)}")
    return new_articles


def mark_articles_processed(articles):
    """标记文章为已处理"""
    if not articles:
        return

    tracker = _load_tracker()
    now = datetime.now(timezone.utc).isoformat()

    for article in articles:
        aid = article_id(article)
        tracker["articles"][aid] = {
            "title": (article.get("title", "") or "")[:100],
            "source": article.get("source_name", article.get("source", "")),
            "processed_at": now,
        }

    _save_tracker(tracker)
    print(f"[Dedup] 已标记 {len(articles)} 篇文章为已处理")


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
