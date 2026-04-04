"""
微信公众号工具模块
包含微信监控的专有逻辑：
  - Feed 列表获取（从 Wechat2RSS GitHub 仓库）
  - 文章全文获取（从 mp.weixin.qq.com）
  - 微信报告生成
"""

import json
import re
import ssl
import time
import random
import urllib.request
import urllib.error
from html.parser import HTMLParser
from pathlib import Path
from datetime import datetime, timezone
from collections import OrderedDict

from .config import CONFIG_DIR, WORKSPACE_DIR, get_category_display


CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 天
SOURCE_URL = "https://raw.githubusercontent.com/ttttmr/Wechat2RSS/master/list/all.md"

FEED_LINK_PATTERN = re.compile(r'^(~~)?\[([^\]]+)\]\(([^)]+)\)(~~)?$')
HEADING_PATTERN = re.compile(r'^##\s+(.+)$')

WECHAT_CATEGORY_ORDER = ["wechat_security", "wechat_dev", "wechat_other", "wechat_user"]


# ============================================================
# Feed 列表获取
# ============================================================

def _create_ssl_context():
    try:
        return ssl.create_default_context()
    except Exception:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def _fetch_url(url, timeout=30):
    """获取 URL 内容（带 SSL 降级）"""
    import urllib.error
    ctx = _create_ssl_context()
    req = urllib.request.Request(url, headers={"User-Agent": "WechatRSSMonitor/1.0"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except Exception as first_err:
        # 只在 SSL 相关错误时才降级
        err_str = str(first_err).lower()
        is_ssl_error = any(kw in err_str for kw in ["ssl", "certificate", "cert", "hostname"])
        if not is_ssl_error:
            raise first_err
        try:
            relaxed = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            relaxed.check_hostname = False
            relaxed.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, context=relaxed, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except Exception:
            raise first_err


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
                print(f'[WeChat] Feed 列表缓存有效，跳过获取')
                with open(output_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            pass

    # 获取
    print(f'[WeChat] 正在获取 Feed 列表...')
    try:
        markdown_text = _fetch_url(SOURCE_URL)
    except Exception as e:
        print(f'[WeChat] ❌ 获取失败: {e}')
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

    print(f'[WeChat] ✅ Feed 列表更新: {len(feeds)} 个 ({active_count} 活跃)')
    return result


# ============================================================
# 文章全文获取
# ============================================================

class _TextExtractor(HTMLParser):
    """从 HTML 提取可见文本"""
    SKIP_TAGS = {"script", "style", "noscript"}

    def __init__(self):
        super().__init__()
        self._pieces = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._pieces.append(data)

    def get_text(self):
        return re.sub(r"\s+", " ", "".join(self._pieces)).strip()


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
            extractor = _TextExtractor()
            extractor.feed(html[start:end])
            text = extractor.get_text()
            return text if len(text) > 100 else None

    return None


def enrich_wechat_articles(updates_data, min_length=500, max_articles=0, delay=2.0):
    """补充获取微信文章全文

    Args:
        updates_data: dict with 'updates' list
        min_length: 已有 full_text 超过此长度则跳过
        max_articles: 最大获取数（0=全部）
        delay: 请求间隔秒数

    Returns:
        修改后的 updates_data
    """
    updates = updates_data.get("updates", [])
    if not updates:
        return updates_data

    fetched = 0
    skipped = 0
    failed = 0

    # 筛选需要获取的文章
    to_fetch = []
    for i, update in enumerate(updates):
        existing_text = update.get("full_text", "")
        article_url = update.get("article_url", "")
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
        article_url = update.get("article_url", "")
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            req = urllib.request.Request(article_url, headers=headers)
            ctx = _create_ssl_context()
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            full_text = _extract_wechat_content(html)
            if full_text and len(full_text) > len(update.get("full_text", "")):
                update["full_text"] = full_text
                update["content_source"] = "mp.weixin.qq.com"
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

    print(f'[WeChat] 文章补充: 获取 {fetched}, 跳过 {skipped}, 失败 {failed}')
    return updates_data


# ============================================================
# 微信报告生成
# ============================================================

def generate_wechat_report(updates_data, ai_summaries=None):
    """生成微信公众号日报 Markdown 报告

    Args:
        updates_data: dict with 'metadata' and 'updates'
        ai_summaries: dict, {article_url: ai_summary} 或 None

    Returns:
        str: Markdown 报告
    """
    metadata = updates_data.get('metadata', {})
    updates = updates_data.get('updates', [])
    ai_summaries = ai_summaries or {}

    now = datetime.now(timezone.utc)
    report_time = now.strftime('%Y-%m-%d %H:%M')

    lines = [
        f'# 微信公众号更新汇总 - {report_time}',
        '',
        f'> 共检查 {metadata.get("checked_count", 0)} 个公众号，'
        f'时间范围 {metadata.get("hours", 24)} 小时，'
        f'发现 {metadata.get("update_count", len(updates))} 条更新',
        '',
        '---',
        ''
    ]

    # 按分类分组
    groups = OrderedDict()
    for cat in WECHAT_CATEGORY_ORDER:
        groups[cat] = []
    for update in updates:
        cat = update.get('category', '其他')
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

        for update in cat_updates:
            article_index += 1
            account_name = update.get('account_name', 'Unknown')
            article_title = update.get('article_title', '(no title)')
            article_url = update.get('article_url', '')
            pub_date = update.get('pub_date', '')
            summary_text = update.get('summary_text', '')

            ai_summary = ai_summaries.get(article_url, '')

            lines.append(f'- 📱 [{account_name}] — [{article_title}]({article_url})')
            if ai_summary:
                lines.append(f'  > {ai_summary}')
            elif summary_text:
                fallback = summary_text[:150] + ('...' if len(summary_text) > 150 else '')
                lines.append(f'  > {fallback}')

        lines.append('')

    lines.append(f'*报告生成时间: {report_time} UTC*')
    return '\n'.join(lines)
