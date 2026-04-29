"""
LLM-based article scoring and topic grouping.

Replaces the heuristic editorial pipeline + topic clustering + AI classification
with a two-stage LLM approach:
  Stage 1: score_and_filter_articles() — 1-10 scoring, low-score articles dropped
  Stage 2: group_articles_by_theme() — dynamic theme grouping
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .llm_utils import parse_llm_json
from .logging_config import get_logger
from .llm import get_llm_client, chat_with_profile, limit_llm_workers, should_skip_optional_llm
from .config import DEPTH_MAP

logger = get_logger("llm_classify")

# Tier thresholds mapped from 1-10 score
_TIER_MAP = {
    "must_read": (8, 10),
    "noteworthy": (5, 7),
    "brief": (3, 4),
}
_DEPTH_MAP = DEPTH_MAP


def _score_to_tier(score):
    """Map a 1-10 score to a tier string."""
    if score >= 8:
        return "must_read"
    if score >= 5:
        return "noteworthy"
    return "brief"


def _format_article_for_scoring(idx, article):
    """Format a single article for the scoring prompt."""
    parts = [f"[{idx}] {article.title}"]
    if article.source:
        parts.append(f"    来源: {article.source}")
    desc = (article.description or "")[:200]
    if desc:
        parts.append(f"    摘要: {desc}")
    return "\n".join(parts)


def _format_article_for_grouping(idx, article):
    """Format a single article for the topic grouping prompt."""
    parts = [f"[{idx}] {article.title}"]
    if article.source:
        parts.append(f"    来源: {article.source}")
    score = article.extra.get("news_value_score", 5)
    parts.append(f"    评分: {score}")
    desc = (article.description or "")[:150]
    if desc:
        parts.append(f"    摘要: {desc}")
    return "\n".join(parts)


def _parse_score_response(response, batch_size):
    """Parse LLM score response into {index: score} mapping."""
    try:
        parsed = parse_llm_json(response)
    except (ValueError, json.JSONDecodeError):
        return _salvage_scores(response, batch_size)

    if not isinstance(parsed, dict):
        return _salvage_scores(response, batch_size)

    scores = {}
    for key, value in parsed.items():
        index = _extract_index(key)
        if index is None:
            continue
        if isinstance(value, dict):
            score = value.get("score")
        elif isinstance(value, (int, float)):
            score = int(value)
        else:
            continue
        if isinstance(score, (int, float)) and 1 <= index <= batch_size:
            scores[index] = max(1, min(10, int(score)))
    return scores


def _salvage_scores(response, batch_size):
    """Fallback: extract scores from malformed LLM output."""
    scores = {}
    pattern = r'["\']?(\d+)["\']?\s*[:=]\s*\{?\s*["\']?score["\']?\s*[:=]\s*(\d+)'
    for match in re.finditer(pattern, response, re.IGNORECASE):
        index = int(match.group(1))
        score = int(match.group(2))
        if 1 <= index <= batch_size and 1 <= score <= 10:
            scores[index] = score
    if not scores:
        pattern2 = r'["\']?(\d+)["\']?\s*[:=]\s*(\d+)'
        for match in re.finditer(pattern2, response):
            index = int(match.group(1))
            score = int(match.group(2))
            if 1 <= index <= batch_size and 1 <= score <= 10:
                scores[index] = score
    return scores


def _extract_index(key):
    """Extract article index from a JSON key."""
    if isinstance(key, int):
        return key
    if isinstance(key, str):
        text = key.strip().strip("[]\"'")
        try:
            return int(text)
        except ValueError:
            match = re.match(r'^(\d+)', text)
            if match:
                return int(match.group(1))
    return None


def _score_batch(client, batch, batch_idx, total_batches):
    """Score a single batch of articles. Returns list of (article, score) pairs."""
    logger.info(f"[Score] batch {batch_idx + 1}/{total_batches} ({len(batch)} articles)...")
    from config.prompts.llm_classify import SCORE_FILTER_PROMPT_ZH

    articles_text = "\n\n".join(
        _format_article_for_scoring(i, a) for i, a in enumerate(batch, start=1)
    )
    prompt = SCORE_FILTER_PROMPT_ZH.format(articles=articles_text)

    response = chat_with_profile(client, prompt, "score_filter")
    if not response:
        logger.warning(f"[Score] batch {batch_idx + 1} API failed, using default score 3")
        return [(a, 3) for a in batch]

    scores = _parse_score_response(response, len(batch))
    if not scores:
        logger.warning(f"[Score] batch {batch_idx + 1} parse failed, using default score 3")
        return [(a, 3) for a in batch]

    results = []
    for i, article in enumerate(batch, start=1):
        score = scores.get(i, 5)
        results.append((article, score))

    parsed_count = len(scores)
    score_dist = {}
    for _, s in results:
        bucket = f"{(s // 3) * 3}-{(s // 3) * 3 + 2}"
        score_dist[bucket] = score_dist.get(bucket, 0) + 1
    logger.info(f"[Score] batch {batch_idx + 1}: parsed {parsed_count}/{len(batch)}, "
                f"distribution: {score_dist}")
    return results


def score_and_filter_articles(articles, batch_size=25):
    """Stage 1: Score articles 1-10 via LLM and filter low-score ones.

    Args:
        articles: list of Article objects (post-dedup)
        batch_size: articles per LLM call

    Returns:
        (surviving_articles, stats) where stats has scoring distribution.
    """
    if not articles:
        return articles, {"total": 0, "surviving": 0, "filtered": 0}

    client = get_llm_client()

    # Split into batches (minimum 10 per batch for quality)
    batches = []
    for i in range(0, len(articles), batch_size):
        batch = articles[i:i + batch_size]
        batches.append(batch)

    # Merge last batch if too small
    if len(batches) > 1 and len(batches[-1]) < 10:
        batches[-2].extend(batches.pop())

    total_batches = len(batches)
    max_workers = min(limit_llm_workers(3), total_batches)

    all_scored = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_score_batch, client, batch, idx, total_batches): idx
            for idx, batch in enumerate(batches)
        }
        for future in as_completed(futures):
            all_scored.extend(future.result())

    # Apply scores and filter
    filter_threshold = int(os.environ.get("LLM_SCORE_FILTER_THRESHOLD", "2"))
    surviving = []
    filtered_count = 0

    for article, score in all_scored:
        tier = _score_to_tier(score)
        article.extra["news_value_score"] = score
        article.extra["editorial_tier"] = tier
        article.extra["depth"] = _DEPTH_MAP.get(tier, "headline_only")

        if score <= filter_threshold:
            filtered_count += 1
        else:
            surviving.append(article)

    # Sort by score descending
    surviving.sort(key=lambda a: a.extra.get("news_value_score", 0), reverse=True)

    stats = {
        "total": len(articles),
        "surviving": len(surviving),
        "filtered": filtered_count,
        "must_read": sum(1 for a in surviving if a.extra.get("editorial_tier") == "must_read"),
        "noteworthy": sum(1 for a in surviving if a.extra.get("editorial_tier") == "noteworthy"),
        "brief": sum(1 for a in surviving if a.extra.get("editorial_tier") == "brief"),
    }
    logger.info(f"[Score] done: {stats['surviving']}/{stats['total']} surviving "
                f"(must_read={stats['must_read']}, noteworthy={stats['noteworthy']}, "
                f"brief={stats['brief']}, filtered={stats['filtered']})")
    return surviving, stats


def _parse_theme_response(response, batch_size):
    """Parse LLM theme grouping response."""
    try:
        parsed = parse_llm_json(response)
    except (ValueError, json.JSONDecodeError):
        return _salvage_themes(response)

    if isinstance(parsed, dict) and "themes" in parsed:
        return parsed["themes"]
    if isinstance(parsed, list):
        return parsed
    return _salvage_themes(response)


def _salvage_themes(response):
    """Fallback: extract themes from malformed LLM output."""
    themes = []
    title_pattern = re.compile(r'"title"\s*:\s*"([^"]+)"', re.IGNORECASE)
    summary_pattern = re.compile(r'"summary"\s*:\s*"([^"]*)"', re.IGNORECASE)
    indices_pattern = re.compile(r'"indices"\s*:\s*\[([^\]]+)\]', re.IGNORECASE)

    titles = title_pattern.findall(response)
    for title in titles:
        themes.append({"title": title, "summary": "", "indices": []})

    for i, theme in enumerate(themes):
        # Try to find corresponding summary and indices
        block_start = response.find(theme["title"], 0)
        if block_start >= 0:
            block = response[block_start:block_start + 500]
            summary_match = summary_pattern.search(block)
            if summary_match:
                theme["summary"] = summary_match.group(1)
            indices_match = indices_pattern.search(block)
            if indices_match:
                theme["indices"] = [int(x.strip()) for x in indices_match.group(1).split(",")
                                    if x.strip().isdigit()]
    return themes


def group_articles_by_theme(articles):
    """Stage 2: Group articles into dynamic themes via LLM.

    Only sends high-scoring articles (score >= 5) to the LLM for grouping.
    Brief articles (score 3-4) go directly to leftovers.

    The LLM decides how many themes to create based on content significance.
    Only truly impactful topics become themes; remaining articles are leftovers.

    Returns:
        (themes, leftovers) where:
          themes = [{"title": str, "summary": str, "articles": [Article], "score": float}, ...]
          leftovers = [Article, ...] (articles not in any theme)
    """
    if not articles:
        return [], []

    # Separate: only noteworthy+ articles go to LLM, brief go straight to leftovers
    noteworthy = [a for a in articles if a.extra.get("news_value_score", 0) >= 5]
    auto_leftovers = [a for a in articles if a.extra.get("news_value_score", 0) < 5]

    # Cap at top N by score to keep LLM input manageable
    max_group_input = int(os.environ.get("LLM_THEME_MAX_ARTICLES", "40"))
    noteworthy.sort(key=lambda a: a.extra.get("news_value_score", 0), reverse=True)
    group_candidates = noteworthy[:max_group_input]
    overflow = noteworthy[max_group_input:]

    if not group_candidates:
        logger.info("[Theme] No noteworthy articles for grouping, all become leftovers")
        return [], list(articles)

    if len(group_candidates) < 2:
        logger.info("[Theme] Too few noteworthy articles for grouping")
        return [], list(articles)

    client = get_llm_client()
    from config.prompts.llm_classify import TOPIC_GROUP_PROMPT_ZH

    articles_text = "\n\n".join(
        _format_article_for_grouping(i, a) for i, a in enumerate(group_candidates, start=1)
    )
    prompt = TOPIC_GROUP_PROMPT_ZH.format(articles=articles_text)

    logger.info(f"[Theme] Grouping {len(group_candidates)}/{len(noteworthy)} noteworthy articles "
                f"({len(overflow)} overflow + {len(auto_leftovers)} brief → leftovers)...")
    response = chat_with_profile(client, prompt, "topic_group")

    if not response:
        logger.warning("[Theme] LLM call failed, using flat fallback")
        return [], list(articles)

    raw_themes = _parse_theme_response(response, len(group_candidates))
    if not raw_themes:
        logger.warning("[Theme] Parse failed, using flat fallback")
        return [], list(articles)

    # Resolve indices to articles
    used_indices = set()
    themes = []

    for raw in raw_themes:
        title = raw.get("title", "").strip()
        if not title:
            continue

        indices = raw.get("indices", [])
        valid_indices = [i for i in indices if isinstance(i, (int, float))
                         and 1 <= i <= len(group_candidates) and i not in used_indices]

        if len(valid_indices) < 1:
            continue

        theme_articles = []
        for idx in valid_indices:
            used_indices.add(idx)
            theme_articles.append(group_candidates[idx - 1])

        themes.append({
            "title": title,
            "summary": raw.get("summary", "").strip(),
            "articles": theme_articles,
            "score": max(a.extra.get("news_value_score", 0) for a in theme_articles),
            "cross_source": len(set(a.source for a in theme_articles)) > 1,
        })

    # Articles not in any theme + overflow + auto_leftovers
    theme_leftovers = [a for i, a in enumerate(group_candidates, start=1)
                       if i not in used_indices]
    leftovers = auto_leftovers + overflow + theme_leftovers

    # Sort themes by score descending
    themes.sort(key=lambda t: t["score"], reverse=True)

    logger.info(f"[Theme] done: {len(themes)} themes, {len(leftovers)} leftovers "
                f"({len(auto_leftovers)} brief + {len(overflow)} overflow + {len(theme_leftovers)} ungrouped)")
    for t in themes:
        logger.info(f"  - {t['title']} ({len(t['articles'])} articles, score={t['score']})")

    return themes, leftovers


def _format_cluster_for_interpret(cluster):
    """Format a cluster's articles for the theme interpretation prompt."""
    parts = []
    for i, article in enumerate(cluster["articles"][:15], start=1):
        line = f"[{i}] {article.title}"
        if article.source:
            line += f"  (来源: {article.source})"
        score = article.extra.get("news_value_score", 0)
        if score:
            line += f"  (评分: {score})"
        parts.append(line)
    return "\n".join(parts)


def _interpret_single_cluster(client, cluster, cluster_idx, total, prompt_template=None):
    """Interpret a single cluster via LLM. Returns theme dict or None."""
    from config.prompts.llm_classify import THEME_INTERPRET_PROMPT_ZH

    template = prompt_template or THEME_INTERPRET_PROMPT_ZH
    articles_text = _format_cluster_for_interpret(cluster)
    prompt = template.format(articles=articles_text)

    response = chat_with_profile(client, prompt, "brief_summary", optional=True)
    if not response:
        return None

    try:
        parsed = parse_llm_json(response)
    except (ValueError, json.JSONDecodeError):
        parsed = _salvage_interpret(response)

    if not isinstance(parsed, dict):
        return None

    title = (parsed.get("title") or "").strip()
    summary = (parsed.get("summary") or "").strip()
    importance = parsed.get("importance")

    if not title:
        return None

    try:
        importance = int(importance)
        importance = max(1, min(10, importance))
    except (TypeError, ValueError):
        importance = max(a.extra.get("news_value_score", 0) for a in cluster["articles"])

    logger.info(f"[Interpret] {cluster_idx + 1}/{total}: {title} "
                f"({cluster['size']} articles, importance={importance})")

    return {
        "title": title,
        "summary": summary,
        "importance": importance,
        "articles": cluster["articles"],
        "score": max(a.extra.get("news_value_score", 0) for a in cluster["articles"]),
        "cross_source": cluster["cross_source"],
        "source_count": cluster["source_count"],
        "cluster_size": cluster["size"],
    }


def _salvage_interpret(response):
    """Fallback parser for theme interpretation response."""
    result = {}
    title_m = re.search(r'"title"\s*:\s*"([^"]+)"', response)
    if title_m:
        result["title"] = title_m.group(1)
    summary_m = re.search(r'"summary"\s*:\s*"([^"]*)"', response, re.DOTALL)
    if summary_m:
        result["summary"] = summary_m.group(1)
    imp_m = re.search(r'"importance"\s*:\s*(\d+)', response)
    if imp_m:
        result["importance"] = int(imp_m.group(1))
    return result if result else None


def interpret_themes_with_llm(clusters, max_workers=3, prompt_template=None):
    """Interpret each cluster via LLM to produce theme titles and summaries.

    Each cluster is interpreted independently and in parallel.

    Args:
        clusters: list of cluster dicts from cluster_by_embedding()
        max_workers: parallel LLM call limit
        prompt_template: optional custom prompt template for theme interpretation

    Returns:
        (themes, singletons) where:
          themes = list of interpreted non-singleton themes
          singletons = list of singleton cluster articles (not interpreted)
    """
    if not clusters:
        return [], []

    # Separate singletons from real clusters
    real_clusters = [c for c in clusters if not c["is_singleton"]]
    singletons = []
    for c in clusters:
        if c["is_singleton"]:
            singletons.extend(c["articles"])

    if not real_clusters:
        logger.info("[Interpret] No multi-article clusters to interpret")
        return [], singletons

    if should_skip_optional_llm():
        logger.info("[Interpret] Skipping due to degraded mode")
        themes = []
        for c in real_clusters:
            themes.append(_fallback_theme(c))
        return themes, singletons

    client = get_llm_client()
    total = len(real_clusters)
    max_workers = min(limit_llm_workers(max_workers), total)

    logger.info(f"[Interpret] Interpreting {total} clusters with {max_workers} workers...")

    themes = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_interpret_single_cluster, client, c, idx, total, prompt_template): idx
            for idx, c in enumerate(real_clusters)
        }
        results = [None] * total
        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()

    for i, result in enumerate(results):
        if result:
            themes.append(result)
        else:
            themes.append(_fallback_theme(real_clusters[i]))

    themes.sort(key=lambda t: (-t["importance"], -t["score"]))
    logger.info(f"[Interpret] done: {len(themes)} themes, {len(singletons)} singletons")
    for t in themes:
        logger.info(f"  - {t['title']} ({t['cluster_size']} articles, importance={t['importance']})")

    return themes, singletons


def _fallback_theme(cluster):
    """Build a theme dict without LLM, using the lead article's title."""
    articles = cluster["articles"]
    lead = articles[0]
    title = lead.title[:20] if lead.title else "未命名主题"
    return {
        "title": title,
        "summary": "",
        "importance": max(a.extra.get("news_value_score", 0) for a in articles),
        "articles": articles,
        "score": max(a.extra.get("news_value_score", 0) for a in articles),
        "cross_source": cluster["cross_source"],
        "source_count": cluster["source_count"],
        "cluster_size": cluster["size"],
    }

