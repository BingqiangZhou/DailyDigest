"""
Unified article data model for all content sources (tech/podcast/wechat).
"""

from dataclasses import dataclass, field


@dataclass
class Article:
    """Unified article/entry data model.

    All three sources (tech RSS, podcast, wechat) use this single type.
    Source-specific fields go in `extra` dict.
    """
    title: str
    url: str
    source: str           # Feed name (website/podcast/account)
    category: str         # Unified category ID (e.g. ai_ml, podcast, wechat_security)
    published: str        # Formatted datetime string
    description: str = ""
    full_text: str = ""
    language: str = "en"
    extra: dict = field(default_factory=dict)

    @property
    def hn_points(self) -> int | None:
        return self.extra.get("hn_points")

    @property
    def hn_comments(self) -> int | None:
        return self.extra.get("hn_comments")

    @property
    def priority(self) -> int:
        return self.extra.get("priority", 3)

    @property
    def rank(self) -> int:
        return self.extra.get("rank", 0)
