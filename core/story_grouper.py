"""
Story grouping module for DailyDigest.

Replaces the AI/non-AI binary split with tiered story grouping:
  - Headlines: top 5-8 stories (editorial score >= 0.60)
  - Noteworthy: 20-30 articles grouped by theme (score 0.30-0.59)
  - Brief: remaining articles in compact table (score 0.15-0.29)
  - Discarded: score < 0.15

Used by report_builder to generate magazine-style reports.
"""

import os
from dataclasses import dataclass, field

from .article import Article
from .logging_config import get_logger

logger = get_logger("story_grouper")

# Tier thresholds (configurable via env)
HEADLINE_THRESHOLD = float(os.getenv("STORY_HEADLINE_THRESHOLD", "0.60"))
NOTEWORTHY_THRESHOLD = float(os.getenv("STORY_NOTEWORTHY_THRESHOLD", "0.30"))
BRIEF_THRESHOLD = float(os.getenv("STORY_BRIEF_THRESHOLD", "0.15"))
MAX_HEADLINES = int(os.getenv("STORY_MAX_HEADLINES", "8"))
MAX_BRIEF = int(os.getenv("STORY_MAX_BRIEF", "60"))

# Theme mapping: display name -> feed category keywords
THEME_MAPPING: dict[str, set[str]] = {
    "模型与平台": {"ai_ml", "llm", "model_release", "ai_model"},
    "开源与工具": {"open_source", "tools", "developer", "coding"},
    "评测与实战": {"review", "benchmark", "tutorial", "comparison"},
    "行业与商业": {"business", "investment", "policy", "startup", "tech_general"},
    "研究与方法": {"research", "paper", "academic", "methodology"},
    "硬件与基础设施": {"hardware", "chip", "infrastructure", "cloud"},
    "产品与应用": {"product", "app", "consumer_tech", "tech_product"},
}

THEME_ORDER = [
    "模型与平台", "开源与工具", "评测与实战",
    "行业与商业", "研究与方法", "硬件与基础设施", "产品与应用",
]


@dataclass
class HeadlineStory:
    """A headline story with its related articles from the same cluster."""
    main: Article
    related: list[Article] = field(default_factory=list)
    editorial_score: float = 0.0
    theme: str = ""


@dataclass
class PipelineStats:
    """Statistics for the data overview section."""
    total_scanned: int = 0
    after_dedup: int = 0
    after_noise_filter: int = 0
    after_editorial: int = 0
    included: int = 0
    headlines_count: int = 0
    noteworthy_count: int = 0
    brief_count: int = 0
    discarded_count: int = 0


@dataclass
class StoryGroup:
    """Grouped stories ready for report rendering."""
    headlines: list[HeadlineStory] = field(default_factory=list)
    noteworthy: dict[str, list[Article]] = field(default_factory=dict)
    brief: list[Article] = field(default_factory=list)
    stats: PipelineStats = field(default_factory=PipelineStats)
    discarded_count: int = 0


def _assign_theme(article: Article, cluster_map: dict) -> str:
    """Assign a display theme to an article using three-level fallback."""
    # Level 1: Check if any theme keyword matches the article category
    cat = (article.category or "").lower()
    for theme, keywords in THEME_MAPPING.items():
        if cat in keywords or any(kw in cat for kw in keywords):
            return theme

    # Level 2: Check title for theme-related keywords
    title = (article.title or "").lower()
    theme_title_keywords = {
        "模型与平台": {"model", "gpt", "claude", "llm", "gemini", "deepseek", "模型", "大模型"},
        "开源与工具": {"open source", "github", "release", "tool", "sdk", "api", "开源", "工具"},
        "评测与实战": {"review", "benchmark", "test", "compare", "vs ", "评测", "测试", "横评"},
        "行业与商业": {"invest", "funding", "policy", "regulation", "投资", "融资", "监管", "政策"},
        "研究与方法": {"paper", "research", "study", "arxiv", "论文", "研究"},
        "硬件与基础设施": {"chip", "gpu", "tpu", "nvidia", "芯片", "算力", "gpu"},
        "产品与应用": {"app", "launch", "product", "feature", "发布", "上线", "产品"},
    }
    for theme, keywords in theme_title_keywords.items():
        if any(kw in title for kw in keywords):
            return theme

    # Level 3: Default to "行业与商业"
    return "行业与商业"


def _select_headlines(
    candidates: list[Article],
    cluster_map: dict,
    scores: dict[str, float],
) -> list[HeadlineStory]:
    """Select headline stories, deduplicating within same cluster."""
    headlines: list[HeadlineStory] = []
    seen_clusters: set[str] = set()

    for article in candidates:
        if len(headlines) >= MAX_HEADLINES:
            break

        cluster_info = cluster_map.get(article.url, {})
        cluster_id = cluster_info.get("cluster_id", article.url)

        if cluster_id in seen_clusters:
            continue
        seen_clusters.add(cluster_id)

        score = scores.get(article.url, article.extra.get("news_value_score", 0))

        # Find related articles in same cluster
        related = []
        for other_url, info in cluster_map.items():
            if other_url != article.url and info.get("cluster_id") == cluster_id:
                pass  # Related articles would be fetched from the cluster membership

        headlines.append(HeadlineStory(
            main=article,
            related=related,
            editorial_score=score,
            theme=_assign_theme(article, cluster_map),
        ))

    return headlines


def group_stories(
    articles: list[Article],
    cluster_map: dict | None = None,
) -> StoryGroup:
    """Group editorial-scored articles into headlines/noteworthy/brief tiers.

    Args:
        articles: Articles with editorial scores in extra["news_value_score"]
        cluster_map: Topic cluster mapping from topic_cluster module

    Returns:
        StoryGroup with tiered and themed articles
    """
    cluster_map = cluster_map or {}
    scores = {a.url: a.extra.get("news_value_score", 0) for a in articles}

    # Sort by editorial score descending
    sorted_articles = sorted(articles, key=lambda a: scores.get(a.url, 0), reverse=True)

    headline_candidates = []
    noteworthy_list = []
    brief_list = []
    discarded = 0

    for article in sorted_articles:
        score = scores.get(article.url, 0)
        if score >= HEADLINE_THRESHOLD:
            headline_candidates.append(article)
        elif score >= NOTEWORTHY_THRESHOLD:
            noteworthy_list.append(article)
        elif score >= BRIEF_THRESHOLD:
            brief_list.append(article)
        else:
            discarded += 1

    # Select headlines with cluster dedup
    headlines = _select_headlines(headline_candidates, cluster_map, scores)

    # Remaining headline candidates not selected become noteworthy
    headline_urls = {h.main.url for h in headlines}
    for a in headline_candidates:
        if a.url not in headline_urls:
            noteworthy_list.append(a)

    # Sort noteworthy back by score
    noteworthy_list.sort(key=lambda a: scores.get(a.url, 0), reverse=True)

    # Group noteworthy by theme
    noteworthy_grouped: dict[str, list[Article]] = {}
    for article in noteworthy_list:
        theme = _assign_theme(article, cluster_map)
        noteworthy_grouped.setdefault(theme, []).append(article)

    # Cap brief list
    brief_list = brief_list[:MAX_BRIEF]

    # Build stats
    stats = PipelineStats(
        total_scanned=len(articles),
        after_editorial=len(articles),
        included=len(headlines) + len(noteworthy_list) + len(brief_list),
        headlines_count=len(headlines),
        noteworthy_count=len(noteworthy_list),
        brief_count=len(brief_list),
        discarded_count=discarded,
    )

    result = StoryGroup(
        headlines=headlines,
        noteworthy=noteworthy_grouped,
        brief=brief_list,
        stats=stats,
        discarded_count=discarded,
    )

    logger.info(
        f"📊 Story grouper: {len(headlines)} headlines, "
        f"{len(noteworthy_list)} noteworthy ({len(noteworthy_grouped)} themes), "
        f"{len(brief_list)} brief, {discarded} discarded"
    )

    return result
