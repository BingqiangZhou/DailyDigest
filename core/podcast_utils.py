"""
播客工具模块
包含播客监控的专有逻辑：
  - 小宇宙 episode URL 解析
  - 播客 RSS 抓取（基于 core.rss_fetcher 的封装）
  - 播客报告生成
"""

import json
import re
import time
import random
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from .config import CONFIG_DIR, OUTPUT_DIR, WORKSPACE_DIR, ensure_dirs
from .http import fetch_url_with_retry
from .logging_config import get_logger

logger = get_logger("podcast")


# ============================================================
# 小宇宙 URL 解析
# ============================================================


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
        updates: list of Article objects
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
    for i, article in enumerate(updates):
        url = article.url
        if 'xiaoyuzhoufm.com/episode/' not in url:
            needs_resolve.append(i)

    if not needs_resolve:
        logger.info(f'[Podcast] 所有 {len(updates)} 个更新已有小宇宙链接')
        return updates

    logger.info(f'[Podcast] {len(needs_resolve)}/{len(updates)} 个需要解析小宇宙链接')

    # 按播客名分组
    podcast_indices = defaultdict(list)
    for idx in needs_resolve:
        name = updates[idx].source
        podcast_indices[name].append(idx)

    resolved = 0
    failed = 0

    def _resolve_one(podcast_name, indices):
        xyz_url = podcasts_map.get(podcast_name)
        if not xyz_url:
            return 0, len(indices)
        try:
            body, status, _ = fetch_url_with_retry(
                xyz_url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'Accept': 'text/html, */*'},
                timeout=15,
            )
            if body is None:
                return 0, len(indices)
            episodes = _parse_xiaoyuzhou_episodes(body)
            if not episodes:
                return 0, len(indices)
            res, fail = 0, 0
            for idx in indices:
                eid = _match_episode(updates[idx].title, episodes)
                if eid:
                    updates[idx].url = f'https://www.xiaoyuzhoufm.com/episode/{eid}'
                    res += 1
                else:
                    fail += 1
            return res, fail
        except Exception as e:
            logger.warning(f"[Podcast] ⚠️ 解析 {podcast_name} 失败: {e}")
            return 0, len(indices)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    items = list(podcast_indices.items())
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_resolve_one, name, idxs): name for name, idxs in items}
        for future in as_completed(futures):
            res, fail = future.result()
            resolved += res
            failed += fail
            time.sleep(random.uniform(0.05, 0.15))

    # 清理 utm 后缀
    for article in updates:
        url = article.url
        if 'xiaoyuzhoufm.com/episode/' in url and '?utm_source=' in url:
            article.url = url.split('?utm_source=')[0]

    logger.info(f'[Podcast] URL 解析完成: 成功 {resolved}, 失败 {failed}')
    return updates


# ============================================================
# 播客报告生成
# ============================================================

def generate_podcast_report(updates, ai_summaries=None, metadata=None):
    """生成播客日报 Markdown 报告

    Args:
        updates: list of Article objects
        ai_summaries: dict, {episode_url: summary} 或 None
        metadata: dict, 抓取统计信息 或 None

    Returns:
        str: Markdown 报告
    """
    metadata = metadata or {}
    ai_summaries = ai_summaries or {}

    now = datetime.now(timezone.utc)
    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M')

    lines = [
        f'# 播客更新汇总 — {date_str} {time_str}',
        '',
        f'> 🎙️ 共检查 {metadata.get("checked_count", 0)} 个播客'
        f' · {metadata.get("hours", 24)}h 窗口'
        f' · 发现 {metadata.get("update_count", len(updates))} 个更新',
        '',
        '---',
        ''
    ]

    # 表格格式
    lines.append('| # | 节目 | 播客 | 排名 | 摘要 |')
    lines.append('|---:|------|------|------|------|')

    for i, update in enumerate(updates, 1):
        podcast_name = update.source
        rank = update.rank
        title = update.title
        url = update.url
        shownotes = update.full_text or update.description

        # AI 摘要优先，fallback 到截断
        summary = ai_summaries.get(url) or ai_summaries.get(title)
        if not summary:
            text = shownotes[:500] if shownotes else '内容暂无'
            summary = text[:150] + ('...' if len(text) > 150 else '')

        # 清理 URL
        display_url = url.split('?utm_source=')[0] if url else ''
        podcast_url = update.extra.get('xiaoyuzhou_url', '') or display_url
        if '?utm_source=' in podcast_url:
            podcast_url = podcast_url.split('?utm_source=')[0]

        rank_str = f"#{rank}" if rank > 0 else "-"
        title_cell = f"[**{title}**]({display_url})".replace("|", "\\|")
        podcast_cell = f"[{podcast_name}]({podcast_url})".replace("|", "\\|")
        summary_cell = summary.replace("|", "\\|").replace("\n", " ")

        lines.append(f"| {i} | {title_cell} | {podcast_cell} | {rank_str} | {summary_cell} |")

    lines.append('')

    return '\n'.join(lines)
