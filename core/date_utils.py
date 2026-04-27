"""
Date parsing utilities for DailyDigest.

Extracted from rss_fetcher.py to break circular import between
article.py <-> rss_fetcher.py.
"""

import time
from datetime import datetime, timezone, timedelta


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
