"""
YouTube 字幕提取模块
通过 Supadata API 获取 YouTube 视频的字幕/转录文本。
"""

import os
import re
import time
import requests

SUPADATA_TRANSCRIPT_URL = "https://api.supadata.ai/v1/transcript"
REQUEST_TIMEOUT = 60


def _extract_video_id(url):
    """从 YouTube URL 中提取 video_id"""
    patterns = [
        r"(?:v=|/v/|youtu\.be/)([\w-]{11})",
        r"(?:embed/)([\w-]{11})",
        r"(?:shorts/)([\w-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def is_youtube_url(url):
    """判断是否为 YouTube URL"""
    return bool(url and ("youtube.com" in url or "youtu.be" in url))


def get_transcript(video_id, api_key=None):
    """获取单个 YouTube 视频的字幕文本"""
    if not api_key:
        api_key = os.environ.get("SUPADATA_API_KEY")
    if not api_key:
        return None

    youtube_url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        response = requests.get(
            SUPADATA_TRANSCRIPT_URL,
            params={"url": youtube_url, "text": "true"},
            headers={"x-api-key": api_key},
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 200:
            data = response.json()
            if "content" in data and data["content"]:
                return data["content"].strip()
            elif "transcript" in data and data["transcript"]:
                segments = data["transcript"]
                text = " ".join(seg.get("text", "") for seg in segments if seg.get("text"))
                return text.strip() if text else None
        elif response.status_code == 404:
            print(f"  [Transcript] ⚠️ 视频 {video_id} 无字幕")
        elif response.status_code == 429:
            print(f"  [Transcript] ⚠️ 请求限流，等待后重试...")
            time.sleep(3)
            return get_transcript(video_id, api_key)
        else:
            print(f"  [Transcript] ⚠️ 视频 {video_id} 获取失败 (HTTP {response.status_code})")
    except requests.exceptions.Timeout:
        print(f"  [Transcript] ⚠️ 视频 {video_id} 请求超时")
    except Exception as e:
        print(f"  [Transcript] ⚠️ 视频 {video_id} 出错: {e}")

    return None


def get_transcripts_for_articles(articles):
    """批量获取文章中 YouTube 视频的字幕"""
    api_key = os.environ.get("SUPADATA_API_KEY")
    if not api_key:
        print("[Transcript] ⚠️ 未配置 SUPADATA_API_KEY，跳过字幕获取")
        return articles

    youtube_articles = []
    other_articles = []

    for article in articles:
        link = article.get("link", "") or article.get("url", "")
        if is_youtube_url(link):
            youtube_articles.append(article)
        else:
            other_articles.append(article)

    if not youtube_articles:
        print(f"[Transcript] 无 YouTube 视频条目，跳过")
        return articles

    print(f"[Transcript] 📺 发现 {len(youtube_articles)} 个 YouTube 视频，正在获取字幕...")

    enriched = []
    success_count = 0

    for i, article in enumerate(youtube_articles):
        link = article.get("link", "") or article.get("url", "")
        video_id = _extract_video_id(link)
        if not video_id:
            enriched.append(article)
            continue

        title = article.get("title", "")[:50]
        print(f"  [{i+1}/{len(youtube_articles)}] {title}...", end=" ", flush=True)

        transcript = get_transcript(video_id, api_key)

        if transcript:
            if len(transcript) > 3000:
                transcript = transcript[:3000] + "...[已截断]"
            article["transcript"] = transcript
            enriched.append(article)
            success_count += 1
            print("✅")
        else:
            enriched.append(article)
            print("⏭️ 无字幕")

        time.sleep(1)

    print(f"[Transcript] ✅ 完成! 成功获取 {success_count}/{len(youtube_articles)} 个字幕\n")
    return other_articles + enriched
