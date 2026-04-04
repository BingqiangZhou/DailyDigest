"""
Daily Digest - 统一入口
支持 GitHub Actions 和 Claude Code Skill 两种运行模式。

用法:
  python main.py                    # 默认：科技新闻（使用 OpenAI API）
  python main.py --source tech      # 科技新闻
  python main.py --source podcast   # 播客
  python main.py --source wechat    # 微信公众号
  python main.py --source all       # 全部源
  python main.py --hours 48         # 自定义时间范围
  python main.py --language en      # 报告语言

GitHub Actions 模式: 使用 OpenAI API 生成摘要（需要 API_KEY 环境变量）
Claude Code Skill 模式: 由 Claude sub-agent 生成摘要（不需要 API_KEY）
"""

import argparse
import re
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone

# 解决 stdout 缓冲问题：确保并发抓取时进度实时输出
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
os.environ.setdefault("PYTHONUNBUFFERED", "1")

# 加载环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def finalize_reports(source, language="zh"):
    """--finalize 模式：从 workspace/ 读取 sub-agent 摘要并生成最终报告"""
    from core.config import OUTPUT_DIR, WORKSPACE_DIR
    from core.report_generator import save_report

    now = datetime.now(timezone.utc)
    date_dir = OUTPUT_DIR / now.strftime("%Y-%m-%d")

    sections = []

    if source in ("tech", "all"):
        report = _finalize_tech(language, date_dir, now)
        if report:
            sections.append(report)

    if source in ("podcast", "all"):
        report = _finalize_podcast(date_dir, now)
        if report:
            sections.append(report)

    if source in ("wechat", "all"):
        report = _finalize_wechat(date_dir, now)
        if report:
            sections.append(report)

    if not sections:
        print("⚠️ 无报告可生成。")
        return

    merged = _build_merged_report(sections, now, language)
    filepath = save_report(merged, f"{now.strftime('%Y-%m-%d')}.md", OUTPUT_DIR,
                           report_type="digest", language=language)

    print("\n" + "=" * 60)
    print(f"✅ Finalize 完成! 报告: {filepath}")
    print("=" * 60 + "\n")


def _load_http_cache(cache_name):
    """加载 HTTP 缓存"""
    from core.config import WORKSPACE_DIR
    cache_path = WORKSPACE_DIR / cache_name
    if cache_path.exists():
        try:
            with open(cache_path, "r") as f:
                return json.load(f), cache_path
        except (json.JSONDecodeError, ValueError):
            print(f"[Cache] ⚠️ 缓存文件损坏，忽略: {cache_path}")
    return {}, cache_path


def _save_http_cache(cache_path, cache):
    """保存 HTTP 缓存（原子写入）"""
    import tempfile
    try:
        tmp_path = cache_path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(cache, f)
        tmp_path.replace(cache_path)
    except Exception as e:
        print(f"[Cache] ⚠️ 缓存保存失败: {e}")


def _strip_section_header_footer(content):
    """去掉 section 中的 # 标题行和页脚行，只保留正文"""
    lines = content.split("\n")
    while lines and lines[0].startswith("#"):
        lines.pop(0)
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[0].startswith(">"):
        lines.pop(0)
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[0].strip() == "---":
        lines.pop(0)
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[-1].strip() == "":
        lines.pop()
    while lines and lines[-1].strip().startswith("*") and ("生成时间" in lines[-1] or "Generated" in lines[-1]):
        lines.pop()
    while lines and lines[-1].strip() == "":
        lines.pop()
    while lines and lines[-1].strip() == "---":
        lines.pop()
    return "\n".join(lines).strip()


def _build_merged_report(sections, now, language="zh"):
    """将多个 section 合并为一份完整报告，添加统一头部和目录"""
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    section_names = []
    for section in sections:
        first_line = section.split("\n")[0].strip()
        name = first_line.lstrip("#").strip()
        section_names.append(name)

    if language == "zh":
        header = f"# 📰 Daily Digest - {date_str}\n\n"
        header += f"> {', '.join(section_names)}\n\n"
        header += f"> ⏰ 生成时间: {time_str} UTC\n"
    else:
        header = f"# 📰 Daily Digest - {date_str}\n\n"
        header += f"> {', '.join(section_names)}\n\n"
        header += f"> ⏰ Generated at: {time_str} UTC\n"

    header += "\n---\n\n"

    # 处理各 section：去掉各自的标题和页脚
    cleaned_sections = []
    all_headings = []  # 收集所有 ## 标题用于 TOC
    for section in sections:
        cleaned = _strip_section_header_footer(section)
        if cleaned:
            # 提取 ## 标题
            for line in cleaned.split("\n"):
                if line.startswith("## "):
                    heading_text = line.lstrip("#").strip()
                    # 生成锚点（GitHub 风格：小写、空格转-、去除特殊字符）
                    anchor = heading_text.lower()
                    anchor = re.sub(r'[^\w\u4e00-\u9fff\s-]', '', anchor)
                    anchor = re.sub(r'[\s]+', '-', anchor)
                    all_headings.append((heading_text, anchor))
            cleaned_sections.append(cleaned)

    # 生成 TOC
    toc_label = "## 📑 目录" if language == "zh" else "## 📑 Table of Contents"
    toc_lines = [toc_label, ""]
    for heading_text, anchor in all_headings:
        toc_lines.append(f"- [{heading_text}](#{anchor})")
    toc = "\n".join(toc_lines) + "\n"

    return header + toc + "\n---\n\n" + "\n\n---\n\n".join(cleaned_sections)


def _finalize_tech(language, date_dir, now):
    """Finalize 科技新闻报告"""
    from core.config import WORKSPACE_DIR
    from core.article import Article
    from core.report_generator import generate_tech_report, save_report

    updates_path = WORKSPACE_DIR / "tech_updates.json"
    if not updates_path.exists():
        print("⚠️ 未找到 workspace/tech_updates.json，请先运行抓取。")
        return None

    with open(updates_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    updates = [Article(**u) for u in data.get("updates", [])]
    stats = data.get("metadata", {})

    # 合并所有 batch 的 sub-agent 摘要
    summary_map = {}
    summary_dir = WORKSPACE_DIR
    for p in sorted(summary_dir.glob("tech_summary_batch_*.json")):
        with open(p, "r", encoding="utf-8") as f:
            batch = json.load(f)
        for item in batch.get("summaries", []):
            url = item.get("url", "")
            if url:
                summary_map[url] = item

    # 合并趋势洞察
    trend_path = WORKSPACE_DIR / "tech_trend_insight.json"
    trend_insight = None
    if trend_path.exists():
        with open(trend_path, "r", encoding="utf-8") as f:
            trend_insight = json.load(f)

    report = generate_tech_report(updates, summary_map, trend_insight, stats, language)
    print(f"✅ 科技新闻报告生成完成 ({len(updates)} 篇)")
    return report


def _finalize_podcast(date_dir, now):
    """Finalize 播客报告"""
    from core.config import WORKSPACE_DIR
    from core.podcast_utils import generate_podcast_report
    from core.report_generator import save_report

    updates_path = WORKSPACE_DIR / "podcast_updates.json"
    if not updates_path.exists():
        print("⚠️ 未找到 workspace/podcast_updates.json，请先运行抓取。")
        return None

    with open(updates_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 合并所有 batch 的 sub-agent 摘要
    ai_summaries = {}
    summary_dir = WORKSPACE_DIR
    for p in sorted(summary_dir.glob("podcast_summary_batch_*.json")):
        with open(p, "r", encoding="utf-8") as f:
            batch = json.load(f)
        for url, summary in batch.items():
            ai_summaries[url] = summary

    report = generate_podcast_report(data, ai_summaries)
    print(f"✅ 播客报告生成完成 ({len(ai_summaries)} 条摘要)")
    return report


def _finalize_wechat(date_dir, now):
    """Finalize 微信公众号报告"""
    from core.config import WORKSPACE_DIR
    from core.wechat_utils import generate_wechat_report
    from core.report_generator import save_report

    updates_path = WORKSPACE_DIR / "wechat_updates.json"
    if not updates_path.exists():
        print("⚠️ 未找到 workspace/wechat_updates.json，请先运行抓取。")
        return None

    with open(updates_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 合并所有 batch 的 sub-agent 摘要
    ai_summaries = {}
    summary_dir = WORKSPACE_DIR
    for p in sorted(summary_dir.glob("wechat_summary_batch_*.json")):
        with open(p, "r", encoding="utf-8") as f:
            batch = json.load(f)
        for item in batch.get("summaries", []):
            url = item.get("article_url", "")
            if url:
                ai_summaries[url] = item.get("ai_summary", "")

    report = generate_wechat_report(data, ai_summaries)
    print(f"✅ 微信公众号报告生成完成 ({len(ai_summaries)} 条摘要)")
    return report


def run_tech(hours=48, language="zh", limit=None):
    """科技新闻 pipeline"""
    from core.config import load_feed_config, ensure_dirs, OUTPUT_DIR, WORKSPACE_DIR
    from core.dedup import filter_and_mark, cleanup_old_entries

    ensure_dirs(OUTPUT_DIR, WORKSPACE_DIR)

    # Step 1: 获取 RSS
    print("\n📡 Step 1/4: 获取科技新闻 RSS...")
    config = load_feed_config("tech")
    feed_list = [
        {"name": f["name"], "url": f["url"], "category": c["name"],
         "language": f.get("language", "en"), "priority": f.get("priority", 3)}
        for c in config.get("categories", [])
        for f in c.get("feeds", [])
    ]
    if limit:
        feed_list = feed_list[:limit]
        print(f"   (限制模式: 仅抓取前 {limit} 个源)")
    settings = config.get("settings", {})

    # 根据是否有 API_KEY 选择后端
    api_key = os.environ.get("API_KEY")
    if api_key:
        from core.rss_fetcher import fetch_feeds_feedparser
        articles_by_category, fetch_stats = fetch_feeds_feedparser(
            feed_list, hours=settings.get("hours_back", hours),
            max_per_feed=settings.get("max_articles_per_feed", 10)
        )
    else:
        from core.rss_fetcher import fetch_feeds_stdlib
        cache, cache_path = _load_http_cache(".http_cache.json")
        updates, stats, new_cache = fetch_feeds_stdlib(feed_list, hours=hours, workers=20, cache=cache)
        _save_http_cache(cache_path, new_cache)
        # 统一为 articles_by_category 格式
        articles_by_category = {}
        for u in updates:
            cat = u.category
            articles_by_category.setdefault(cat, []).append(u)
        fetch_stats = stats

    all_articles = [a for arts in articles_by_category.values() for a in arts]
    if not all_articles:
        print("⚠️ 未获取到任何文章。")
        return None

    # Step 2: 去重
    print("🔍 Step 2/4: 去重过滤...")
    cleanup_old_entries(days=30)
    new_articles = filter_and_mark(all_articles)
    if not new_articles:
        print("⚠️ 所有文章均已处理过。")
        return None

    new_by_category = {}
    for a in new_articles:
        cat = a.category
        new_by_category.setdefault(cat, []).append(a)
    print(f"✅ {len(new_articles)} 篇新文章")

    # Step 3: AI 摘要
    print("🤖 Step 3/4: AI 摘要生成...")
    if api_key:
        from core.ai_summarizer import summarize_all_categories
        category_results, executive_summary = summarize_all_categories(new_by_category, language)
        if not category_results:
            print("⚠️ AI 摘要生成失败。")
            return None

        from core.report_generator import generate_category_report, save_report
        report_stats = {"total_articles": len(new_articles), "categories": len(category_results)}
        report = generate_category_report(category_results, executive_summary, report_stats, language)
    else:
        # Skill 模式：无 API_KEY，生成不含 AI 摘要的报告
        # Claude sub-agent 会在 SKILL.md 流程中单独处理摘要
        from core.report_generator import generate_tech_report, save_report
        # 保存原始数据供 sub-agent 使用
        updates_path = WORKSPACE_DIR / "tech_updates.json"
        with open(updates_path, "w", encoding="utf-8") as f:
            json.dump({"metadata": fetch_stats, "updates": [asdict(a) for a in new_articles]}, f, ensure_ascii=False, indent=2)
        print(f"💡 无 API_KEY，已保存原始数据到 {updates_path}")
        print("   请使用 Claude sub-agent 生成摘要后，再运行:")
        print(f"   python main.py --source tech --finalize")
        return None

    # Step 4: 生成报告
    print("📄 Step 4/4: 生成报告...")
    return report, {"total_articles": len(new_articles), "categories": len(new_by_category)}


def run_podcast(hours=24, limit=None):
    """播客 pipeline"""
    from core.config import CONFIG_DIR, ensure_dirs, OUTPUT_DIR, WORKSPACE_DIR
    from core.rss_fetcher import fetch_feeds_stdlib
    from core.podcast_utils import resolve_xiaoyuzhou_urls, generate_podcast_report
    from core.report_generator import save_report
    from core.dedup import filter_and_mark

    ensure_dirs(OUTPUT_DIR, WORKSPACE_DIR)
    api_key = os.environ.get("API_KEY")

    print("\n🎙️ Step 1/3: 检查播客更新...")
    with open(CONFIG_DIR / "podcast_feeds.json", "r", encoding="utf-8") as f:
        pdata = json.load(f)

    podcasts = pdata.get("podcasts", [])[:pdata.get("settings", {}).get("count", 1000)]
    if limit:
        podcasts = podcasts[:limit]
        print(f"   (限制模式: 仅抓取前 {limit} 个播客)")
    feed_list = [
        {"name": p["name"], "url": p["url"], "category": "podcast", "language": "zh",
         "_podcast_meta": {"rank": p.get("rank", 0), "xiaoyuzhou_url": p.get("xiaoyuzhou_url", "")}}
        for p in podcasts if p.get("url")
    ]

    cache, cache_path = _load_http_cache(".podcast_http_cache.json")
    raw_updates, stats, new_cache = fetch_feeds_stdlib(feed_list, hours=hours, workers=30, cache=cache)
    _save_http_cache(cache_path, new_cache)

    # 去重（在 Article 对象上）
    raw_updates = filter_and_mark(raw_updates)

    if not raw_updates:
        print("⚠️ 无播客更新。")
        return None

    updates = []
    for u in raw_updates:
        meta = u.extra.get("_feed_meta", {}).get("_podcast_meta", {})
        updates.append({
            "podcast_name": u.source,
            "rank": meta.get("rank", 0),
            "episode_title": u.title,
            "episode_url": u.url,
            "pub_date": u.published,
            "shownotes": u.full_text or u.description,
            "xiaoyuzhou_url": meta.get("xiaoyuzhou_url", ""),
        })

    if not updates:
        print("⚠️ 无播客更新。")
        return None

    print(f"✅ {len(updates)} 个播客更新")

    # Step 2: 解析小宇宙链接
    print("🔗 Step 2/3: 解析小宇宙链接...")
    updates = resolve_xiaoyuzhou_urls(updates)

    # 保存原始数据供 sub-agent 使用
    updates_path = WORKSPACE_DIR / "podcast_updates.json"
    with open(updates_path, "w", encoding="utf-8") as f:
        json.dump({"metadata": stats, "updates": updates}, f, ensure_ascii=False, indent=2)

    # Step 3: 生成报告
    if api_key:
        # GitHub Actions 模式：使用 OpenAI API 生成摘要
        print("📄 Step 3/3: AI 摘要 + 生成报告...")
        from core.ai_summarizer import summarize_podcast_batch
        ai_summaries = summarize_podcast_batch(updates)
        report = generate_podcast_report({"metadata": stats, "updates": updates}, ai_summaries)
    else:
        # Skill 模式：生成不含 AI 摘要的报告，等待 sub-agent 处理
        print("📄 Step 3/3: 生成初步报告（无 AI 摘要）...")
        report = generate_podcast_report({"metadata": stats, "updates": updates})
        print(f"💡 无 API_KEY，已保存原始数据到 {updates_path}")
        print("   请使用 Claude sub-agent 生成摘要后，再运行:")
        print(f"   python main.py --source podcast --finalize")

    return report, {"total_episodes": len(updates)}


def run_wechat(hours=24, limit=None):
    """微信公众号 pipeline"""
    from core.config import ensure_dirs, OUTPUT_DIR, WORKSPACE_DIR
    from core.wechat_utils import fetch_wechat_feed_list, generate_wechat_report, enrich_wechat_articles
    from core.rss_fetcher import fetch_feeds_stdlib
    from core.report_generator import save_report
    from core.dedup import filter_and_mark

    ensure_dirs(OUTPUT_DIR, WORKSPACE_DIR)
    api_key = os.environ.get("API_KEY")

    # Step 1: 获取 Feed 列表
    print("\n📱 Step 1/3: 获取微信公众号 Feed 列表...")
    feed_data = fetch_wechat_feed_list()
    feeds = [f for f in feed_data.get("feeds", []) if f.get("active")]
    if limit:
        feeds = feeds[:limit]
        print(f"   (限制模式: 仅抓取前 {limit} 个公众号)")
    feed_list = [
        {"name": f["name"], "url": f["url"], "category": f.get("category", "其他"), "language": "zh",
         "_wechat_meta": {"index": f.get("index", 0)}}
        for f in feeds
    ]

    # Step 2: 检查更新
    print("📡 Step 2/3: 检查公众号更新...")
    cache, cache_path = _load_http_cache(".wechat_http_cache.json")
    raw_updates, stats, new_cache = fetch_feeds_stdlib(feed_list, hours=hours, workers=10, cache=cache)
    _save_http_cache(cache_path, new_cache)

    # 去重（在 Article 对象上）
    raw_updates = filter_and_mark(raw_updates)

    if not raw_updates:
        print("⚠️ 无公众号更新。")
        return None

    updates = []
    for u in raw_updates:
        updates.append({
            "account_name": u.source,
            "article_title": u.title,
            "article_url": u.url,
            "pub_date": u.published,
            "category": u.category,
            "summary_text": u.description,
            "full_text": u.full_text,
        })

    if not updates:
        print("⚠️ 无公众号更新。")
        return None

    print(f"✅ {len(updates)} 条公众号更新")

    # 补充获取微信文章全文（提升 AI 摘要质量）
    if api_key:
        print("📖 补充获取微信文章全文...")
        updates = enrich_wechat_articles(updates)

    # 保存原始数据供 sub-agent 使用
    updates_path = WORKSPACE_DIR / "wechat_updates.json"
    with open(updates_path, "w", encoding="utf-8") as f:
        json.dump({"metadata": stats, "updates": updates}, f, ensure_ascii=False, indent=2)

    # Step 3: 生成报告
    if api_key:
        # GitHub Actions 模式：使用 OpenAI API 生成摘要
        print("📄 Step 3/3: AI 摘要 + 生成报告...")
        from core.ai_summarizer import summarize_wechat_batch
        ai_summaries = summarize_wechat_batch(updates)
        report = generate_wechat_report({"metadata": stats, "updates": updates}, ai_summaries)
    else:
        # Skill 模式：生成不含 AI 摘要的报告，等待 sub-agent 处理
        print("📄 Step 3/3: 生成初步报告（无 AI 摘要）...")
        report = generate_wechat_report({"metadata": stats, "updates": updates})
        print(f"💡 无 API_KEY，已保存原始数据到 {updates_path}")
        print("   请使用 Claude sub-agent 生成摘要后，再运行:")
        print(f"   python main.py --source wechat --finalize")

    return report, {"total_articles": len(updates)}


def main():
    parser = argparse.ArgumentParser(
        description="Daily Digest - 统一日报生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                    # 科技新闻（默认）
  python main.py --source podcast   # 播客
  python main.py --source wechat    # 微信公众号
  python main.py --source all       # 全部源
  python main.py --source tech --hours 72  # 自定义时间范围
        """
    )
    parser.add_argument("--source", choices=["tech", "podcast", "wechat", "all"],
                        default="tech", help="信息源类型 (默认: tech)")
    parser.add_argument("--hours", type=int, default=None,
                        help="时间范围（小时），默认: tech=48, podcast/wechat=24")
    parser.add_argument("--language", choices=["zh", "en"], default=None,
                        help="报告语言 (默认: zh)")
    parser.add_argument("--finalize", action="store_true",
                        help="Skill 模式：从 workspace/ 读取 sub-agent 摘要并生成最终报告")
    parser.add_argument("--limit", type=int, default=None,
                        help="限制抓取的源数量（用于测试）")
    args = parser.parse_args()

    language = args.language or os.environ.get("REPORT_LANGUAGE", "zh")
    start_time = datetime.now(timezone.utc)

    # --finalize 模式：从 workspace/ 读取 sub-agent 摘要并生成最终报告
    if args.finalize:
        print("\n" + "=" * 60)
        print("📋 Daily Digest — Finalize 模式")
        print(f"⏰ {start_time.strftime('%Y-%m-%d %H:%M UTC')} | 源: {args.source}")
        print("=" * 60)
        finalize_reports(args.source, language)
        return

    print("\n" + "=" * 60)
    print("📡 Daily Digest")
    print(f"⏰ {start_time.strftime('%Y-%m-%d %H:%M UTC')} | 源: {args.source} | 语言: {language}")
    print("=" * 60)

    sections = []
    all_stats = {}

    if args.source in ("tech", "all"):
        hours = args.hours or 25
        result = run_tech(hours=hours, language=language, limit=args.limit)
        if result:
            report, stats = result
            sections.append(report)
            all_stats["tech"] = stats

    if args.source in ("podcast", "all"):
        hours = args.hours or 25
        result = run_podcast(hours=hours, limit=args.limit)
        if result:
            report, stats = result
            sections.append(report)
            all_stats["podcast"] = stats

    if args.source in ("wechat", "all"):
        hours = args.hours or 25
        result = run_wechat(hours=hours, limit=args.limit)
        if result:
            report, stats = result
            sections.append(report)
            all_stats["wechat"] = stats

    # 合并为一份报告
    if not sections:
        print("\n⚠️ 无任何更新，不生成报告。")
        return

    from core.config import OUTPUT_DIR
    from core.report_generator import save_report
    now = datetime.now(timezone.utc)
    merged = _build_merged_report(sections, now, language)
    filepath = save_report(merged, f"{now.strftime('%Y-%m-%d')}.md", OUTPUT_DIR,
                           report_type="digest", language=language)

    # 完成
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()
    print("\n" + "=" * 60)
    print(f"✅ Daily Digest 完成! 报告: {filepath}")
    for src, st in all_stats.items():
        print(f"  {src}: {st}")
    print(f"⏱️ 总耗时: {duration:.1f} 秒")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断。")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"\n\n❌ 文件未找到: {e}")
        print("   请检查配置文件是否存在（config/ 目录）")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"\n\n❌ 配置文件格式错误: {e}")
        print("   请检查 JSON 文件语法是否正确")
        sys.exit(1)
    except ConnectionError as e:
        print(f"\n\n❌ 网络连接失败: {e}")
        print("   请检查网络连接或稍后重试")
        sys.exit(1)
    except TimeoutError as e:
        print(f"\n\n❌ 请求超时: {e}")
        print("   可以尝试使用 --limit 参数减少源数量")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
