"""
Unified article data model for all content sources (tech/podcast/wechat).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypedDict


class ArticleExtra(TypedDict, total=False):
    """Type definition for Article.extra dict keys.

    Not enforced at runtime — documents the expected keys
    and provides IDE autocompletion support.
    """
    hn_points: int | None
    hn_comments: int | None
    priority: int
    published_raw: str
    _feed_meta: dict
    author: str
    rank: int
    xiaoyuzhou_url: str
    transcript: str
    content_source: str
    # Editorial pipeline fields (core/editorial.py)
    news_value_score: float          # Composite 0-1 newsworthiness score
    editorial_tier: str              # "must_read" | "noteworthy" | "brief"
    editorial_factors: dict          # Individual factor scores for debugging
    depth: str                       # "deep_analysis" | "summary_only" | "headline_only"


@dataclass
class Article:
    """Unified article/entry data model.

    All three sources (tech RSS, podcast, wechat) use this single type.
    Source-specific fields go in `extra` dict (see ArticleExtra for key docs).
    """
    title: str
    url: str
    source: str           # Feed name (website/podcast/account)
    category: str         # Unified category ID (e.g. ai_ml, podcast, wechat_security)
    published: str        # Formatted datetime string
    description: str = ""
    full_text: str = ""
    language: str = "en"
    extra: ArticleExtra = field(default_factory=dict)  # type: ignore[assignment]

    @property
    def hn_points(self) -> int | None:
        """Hacker News upvote count."""
        return self.extra.get("hn_points")

    @property
    def hn_comments(self) -> int | None:
        """Hacker News comment count."""
        return self.extra.get("hn_comments")

    @property
    def priority(self) -> int:
        """Feed priority (1=highest, 3=default)."""
        return self.extra.get("priority", 3)

    @property
    def rank(self) -> int:
        """Podcast rank."""
        return self.extra.get("rank", 0)

    @property
    def published_dt(self) -> datetime | None:
        """Parse published string to datetime. Returns None on parse failure."""
        if not self.published:
            return None
        from .rss_fetcher import parse_rss_date
        return parse_rss_date(self.published)

    @classmethod
    def from_dict(cls, data: dict) -> "Article":
        """Construct Article from a dict, ignoring unknown keys.

        Safer than Article(**data) which raises TypeError on extra keys.
        """
        import dataclasses
        valid_fields = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


def format_article_item(article, index, desc_limit=300, include_source_type=False):
    """Format a single article for prompt output.

    Returns list of line strings (without trailing blank line).
    """
    lang_tag = "🇨🇳" if article.language == "zh" else "🇺🇸"
    lines = [
        f"{index}. [{lang_tag}] {article.title}",
        f"   来源: {article.source}",
        f"   链接: {article.url}",
    ]
    desc = (article.description or "")[:desc_limit]
    if desc:
        lines.append(f"   摘要: {desc}")
    if include_source_type and article.category:
        lines.append(f"   来源类型: {article.category}")
    return lines
