"""
微信公众号工具模块
包含微信监控的专有逻辑：
  - Feed 列表获取（从 Wechat2RSS GitHub 仓库）
  - 文章全文获取（从 mp.weixin.qq.com）
  - 微信报告生成
"""

import json
import re
import time
import random
from pathlib import Path
from datetime import datetime, timezone
from collections import OrderedDict

from .config import CONFIG_DIR, WORKSPACE_DIR, get_category_display
from .http import fetch_url_with_retry
from .html_utils import strip_html
from .logging_config import get_logger

logger = get_logger("wechat")


CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 天
SOURCE_URL = "https://raw.githubusercontent.com/ttttmr/Wechat2RSS/master/list/all.md"

FEED_LINK_PATTERN = re.compile(r'^(~~)?\[([^\]]+)\]\(([^)]+)\)(~~)?$')
HEADING_PATTERN = re.compile(r'^##\s+(.+)$')

WECHAT_CATEGORY_ORDER = ["wechat_security", "wechat_dev", "wechat_other", "wechat_user"]


# ============================================================
# Feed 列表获取
# ============================================================

def _parse_feed_list(markdown_text):
    """解析 Markdown Feed 列表"""
    feeds = []
    current_category = "未分类"
    index = 0

    for line in markdown_text.splitlines():
        line = line.strip()
        heading_match = HEADING_PATTERN.match(line)
        if heading_match:
            current_category = heading_match.group(1).strip()
            continue

        link_match = FEED_LINK_PATTERN.match(line)
        if link_match:
            struck_before = link_match.group(1) is not None
            name = link_match.group(2).strip()
            url = link_match.group(3).strip()
            struck_after = link_match.group(4) is not None
            active = not (struck_before or struck_after)

            feeds.append({
                "index": index, "name": name, "url": url,
                "category": current_category, "active": active,
            })
            index += 1

    return feeds


def fetch_wechat_feed_list(output_path=None, cache_path=None, force=False):
    """获取并解析 Wechat2RSS Feed 列表

    Args:
        output_path: 输出 JSON 路径（默认 config/wechat_feeds.json）
        cache_path: 缓存元数据路径
        force: 强制刷新

    Returns:
        dict: 包含 metadata 和 feeds 的数据
    """
    if output_path is None:
        output_path = CONFIG_DIR / "wechat_feeds.json"
    else:
        output_path = Path(output_path)

    if cache_path is None:
        cache_path = WORKSPACE_DIR / ".wechat_feed_list_cache.json"
    else:
        cache_path = Path(cache_path)

    # 检查缓存
    if not force and output_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            cached_time = datetime.fromisoformat(cache_data["fetch_time"]).timestamp()
            if (time.time() - cached_time) < CACHE_TTL_SECONDS:
                logger.info(f'[WeChat] Feed 列表缓存有效，跳过获取')
                with open(output_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            pass

    # 获取
    logger.info(f'[WeChat] 正在获取 Feed 列表...')
    try:
        body, status, _ = fetch_url_with_retry(SOURCE_URL, headers={"User-Agent": "WechatRSSMonitor/1.0"}, timeout=30)
        if body is None:
            raise ConnectionError(f"Failed to fetch {SOURCE_URL}")
        markdown_text = body
    except Exception as e:
        logger.error(f'[WeChat] ❌ 获取失败: {e}')
        if output_path.exists():
            with open(output_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"metadata": {}, "feeds": []}

    feeds = _parse_feed_list(markdown_text)
    fetch_time = datetime.now(timezone.utc).isoformat()

    # 统计
    category_stats = {}
    active_count = 0
    for feed in feeds:
        cat = feed["category"]
        category_stats[cat] = category_stats.get(cat, 0) + 1
        if feed["active"]:
            active_count += 1

    result = {
        "metadata": {
            "source": SOURCE_URL,
            "fetch_time": fetch_time,
            "total_count": len(feeds),
            "active_count": active_count,
            "discontinued_count": len(feeds) - active_count,
            "categories": category_stats,
        },
        "feeds": feeds,
    }

    # 保存
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"fetch_time": fetch_time}, f, ensure_ascii=False, indent=2)

    logger.info(f'[WeChat] ✅ Feed 列表更新: {len(feeds)} 个 ({active_count} 活跃)')
    return result


# ============================================================
# 文章全文获取
# ============================================================

def _extract_wechat_content(html):
    """从微信文章 HTML 提取正文"""
    if not html:
        return None

    for marker in ['id="js_content"', 'class="rich_media_content"']:
        idx = html.find(marker)
        if idx >= 0:
            start = html.find(">", idx) + 1
            if start <= 0:
                continue
            end = len(html)
            for em in ['id="js_pc_close_btn"', 'class="rich_media_tool"', 'id="js_tags"']:
                eidx = html.find(em, start)
                if eidx > start:
                    end = min(end, eidx)
            text = strip_html(html[start:end])
            return text if len(text) > 100 else None

    return None


def enrich_wechat_articles(updates, min_length=500, max_articles=0, delay=2.0):
    """补充获取微信文章全文

    Args:
        updates: list of Article objects
        min_length: 已有 full_text 超过此长度则跳过
        max_articles: 最大获取数（0=全部）
        delay: 请求间隔秒数

    Returns:
        修改后的 updates list（原地修改 Article 对象）
    """
    if not updates:
        return updates

    fetched = 0
    skipped = 0
    failed = 0

    # 筛选需要获取的文章
    to_fetch = []
    for i, update in enumerate(updates):
        existing_text = update.full_text or ""
        article_url = update.url
        if len(existing_text) >= min_length:
            skipped += 1
            continue
        if not article_url or "mp.weixin.qq.com" not in article_url:
            skipped += 1
            continue
        if max_articles > 0 and len(to_fetch) >= max_articles:
            break
        to_fetch.append((i, update))

    def _fetch_one(item):
        idx, update = item
        article_url = update.url
        try:
            body, status, _ = fetch_url_with_retry(
                article_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                timeout=30,
            )
            if body is None:
                return False
            html = body
            full_text = _extract_wechat_content(html)
            if full_text and len(full_text) > len(update.full_text or ""):
                update.full_text = full_text
                update.extra["content_source"] = "mp.weixin.qq.com"
                return True
            return False
        except Exception:
            return False

    if to_fetch:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(_fetch_one, item): item for item in to_fetch}
            for future in as_completed(futures):
                if future.result():
                    fetched += 1
                else:
                    failed += 1
                time.sleep(delay / 3)  # 分散总延迟

    logger.info(f'[WeChat] 文章补充: 获取 {fetched}, 跳过 {skipped}, 失败 {failed}')
    return updates


# ============================================================
# 微信报告生成
# ============================================================

def generate_wechat_report(updates, ai_summaries=None, metadata=None):
    """生成微信公众号日报 Markdown 报告

    Args:
        updates: list of Article objects
        ai_summaries: dict, {article_url: ai_summary} 或 None
        metadata: dict, optional metadata for report header

    Returns:
        str: Markdown 报告
    """
    metadata = metadata or {}
    ai_summaries = ai_summaries or {}

    now = datetime.now(timezone.utc)
    report_time = now.strftime('%Y-%m-%d %H:%M')

    lines = [
        f'# 微信公众号更新汇总 — {report_time}',
        '',
        f'> 📱 共检查 {metadata.get("checked_count", 0)} 个公众号'
        f' · {metadata.get("hours", 24)}h 窗口'
        f' · 发现 {metadata.get("update_count", len(updates))} 条更新',
        '',
        '---',
        ''
    ]

    # 按分类分组
    groups = OrderedDict()
    for cat in WECHAT_CATEGORY_ORDER:
        groups[cat] = []
    for update in updates:
        cat = update.category
        if cat not in groups:
            groups[cat] = []
        groups[cat].append(update)

    article_index = 0
    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue

        cat_display = get_category_display(cat)
        lines.append(f'## {cat_display} ({len(cat_updates)} 条)')
        lines.append('')

        # 表格格式
        lines.append('| # | 文章 | 公众号 | 摘要 |')
        lines.append('|---:|------|--------|------|')

        for update in cat_updates:
            article_index += 1
            account_name = update.source
            article_title = update.title
            article_url = update.url
            summary_text = update.description

            ai_summary = ai_summaries.get(article_url, '')

            title_cell = f"[**{article_title}**]({article_url})".replace("|", "\\|")
            account_cell = f"*{account_name}*".replace("|", "\\|")

            if ai_summary:
                summary_cell = ai_summary.replace("|", "\\|").replace("\n", " ")
            elif summary_text:
                fallback = summary_text[:150] + ('...' if len(summary_text) > 150 else '')
                summary_cell = fallback.replace("|", "\\|").replace("\n", " ")
            else:
                summary_cell = ""

            lines.append(f"| {article_index} | {title_cell} | {account_cell} | {summary_cell} |")

        lines.append('')

    lines.append(f'*报告生成时间: {report_time} UTC*')
    return '\n'.join(lines)
