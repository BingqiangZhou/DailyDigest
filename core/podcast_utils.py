"""
播客工具模块
包含播客监控的专有逻辑：
  - 小宇宙 episode URL 解析
  - 播客 RSS 抓取（基于 core.rss_fetcher 的封装）
  - 播客报告生成
"""

import json
import re
import ssl
import time
import random
import urllib.request
import urllib.error
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from .config import CONFIG_DIR, OUTPUT_DIR, WORKSPACE_DIR, ensure_dirs


TIMEOUT = 15
MAX_RETRIES = 2


# ============================================================
# 小宇宙 URL 解析
# ============================================================

def _create_ssl_context(relaxed=False):
    if relaxed:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def _fetch_url(url, max_retries=MAX_RETRIES):
    """获取 URL 内容（带 SSL 降级重试）"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html, */*'
    }
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            use_relaxed = attempt > 0 and last_error and 'SSL' in str(last_error)
            ctx = _create_ssl_context(relaxed=use_relaxed)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as response:
                content = response.read()
                for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                    try:
                        return content.decode(encoding)
                    except (UnicodeDecodeError, LookupError):
                        continue
                return content.decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code >= 500 and attempt < max_retries:
                time.sleep((2 ** attempt) + random.uniform(0, 1))
                continue
            raise
        except (urllib.error.URLError, OSError) as e:
            last_error = e
            if attempt < max_retries:
                time.sleep((2 ** attempt) + random.uniform(0, 1))
                continue
            raise
    raise last_error


def _parse_xiaoyuzhou_episodes(html_content):
    """从小宇宙页面解析出最近单集列表"""
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html_content, re.DOTALL
    )
    if not match:
        return []
    try:
        page_data = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return []
    try:
        episodes = page_data['props']['pageProps']['podcast']['episodes']
    except (KeyError, TypeError):
        return []

    return [(ep.get('title', '').strip(), ep.get('eid', '').strip())
            for ep in episodes if ep.get('title') and ep.get('eid')]


def _normalize_title(title):
    """归一化标题用于匹配"""
    title = title.strip().replace('\u3000', ' ')
    return re.sub(r'\s+', ' ', title)


def _match_episode(target_title, episodes):
    """从单集列表中匹配标题，返回 eid 或 None"""
    target = _normalize_title(target_title)
    for title, eid in episodes:
        if _normalize_title(title) == target:
            return eid
    for title, eid in episodes:
        nt = _normalize_title(title)
        if target in nt or nt in target:
            return eid
    return None


def resolve_xiaoyuzhou_urls(updates, podcasts_data=None):
    """解析非小宇宙的单集链接为小宇宙单集链接

    Args:
        updates: list of update dicts（来自 check_updates）
        podcasts_data: dict, 播客配置数据（包含 podcasts 数组）
            如果为 None，从 config/podcast_feeds.json 读取

    Returns:
        修改后的 updates 列表（原地修改）
    """
    if podcasts_data is None:
        config_path = CONFIG_DIR / "podcast_feeds.json"
        with open(config_path, "r", encoding="utf-8") as f:
            podcasts_data = json.load(f)

    podcasts_map = {
        p['name']: p.get('xiaoyuzhou_url', '')
        for p in podcasts_data.get('podcasts', [])
        if p.get('xiaoyuzhou_url')
    }

    # 找出需要解析的更新
    needs_resolve = []
    for i, update in enumerate(updates):
        url = update.get('episode_url', '')
        if 'xiaoyuzhoufm.com/episode/' not in url:
            needs_resolve.append(i)

    if not needs_resolve:
        print(f'[Podcast] 所有 {len(updates)} 个更新已有小宇宙链接')
        return updates

    print(f'[Podcast] {len(needs_resolve)}/{len(updates)} 个需要解析小宇宙链接')

    # 按播客名分组
    podcast_indices = defaultdict(list)
    for idx in needs_resolve:
        name = updates[idx]['podcast_name']
        podcast_indices[name].append(idx)

    resolved = 0
    failed = 0
    for podcast_name, indices in podcast_indices.items():
        xyz_url = podcasts_map.get(podcast_name)
        if not xyz_url:
            failed += len(indices)
            continue
        try:
            html = _fetch_url(xyz_url)
            episodes = _parse_xiaoyuzhou_episodes(html)
            if not episodes:
                failed += len(indices)
                continue
            for idx in indices:
                eid = _match_episode(updates[idx]['episode_title'], episodes)
                if eid:
                    updates[idx]['episode_url'] = f'https://www.xiaoyuzhoufm.com/episode/{eid}'
                    resolved += 1
                else:
                    failed += 1
            time.sleep(random.uniform(0.2, 0.5))
        except Exception:
            failed += len(indices)

    # 清理 utm 后缀
    for update in updates:
        url = update.get('episode_url', '')
        if 'xiaoyuzhoufm.com/episode/' in url and '?utm_source=' in url:
            update['episode_url'] = url.split('?utm_source=')[0]

    print(f'[Podcast] URL 解析完成: 成功 {resolved}, 失败 {failed}')
    return updates


# ============================================================
# 播客报告生成
# ============================================================

def generate_podcast_report(updates_data, ai_summaries=None):
    """生成播客日报 Markdown 报告

    Args:
        updates_data: dict with 'metadata' and 'updates'
        ai_summaries: dict, {episode_url: summary} 或 None

    Returns:
        str: Markdown 报告
    """
    metadata = updates_data.get('metadata', {})
    updates = updates_data.get('updates', [])
    ai_summaries = ai_summaries or {}

    now = datetime.now(timezone.utc)
    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M')

    lines = [
        f'# 播客更新汇总 - {date_str} {time_str}',
        '',
        f'> 共检查 {metadata.get("checked_count", 0)} 个播客，'
        f'时间范围 {metadata.get("hours", 24)} 小时，'
        f'发现 {metadata.get("update_count", len(updates))} 个更新',
        '',
        '---',
        ''
    ]

    for i, update in enumerate(updates, 1):
        podcast_name = update.get('podcast_name', '未知')
        rank = update.get('rank', 0)
        title = update.get('episode_title', '未知标题')
        url = update.get('episode_url', '')
        pub_date = update.get('pub_date', '')
        shownotes = update.get('shownotes', '')

        # AI 摘要优先，fallback 到截断
        summary = ai_summaries.get(url) or ai_summaries.get(title)
        if not summary:
            text = shownotes[:500] if shownotes else '内容暂无'
            summary = text[:150] + ('...' if len(text) > 150 else '')

        # 清理 URL
        display_url = url.split('?utm_source=')[0] if url else ''
        podcast_url = update.get('podcast_url', display_url)

        rank_display = f" — 排名 #{rank}" if rank > 0 else ""
        lines.append(f'- 🎙️ [{podcast_name}]({podcast_url}){rank_display} — [{title}]({display_url})')
        lines.append(f'  > {summary}')

    lines.append('')

    return '\n'.join(lines)
