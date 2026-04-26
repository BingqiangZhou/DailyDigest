"""
Daily Digest - unified entry point.
Supports GitHub Actions and Claude Code Skill modes.

Usage:
  python main.py                    # tech news (default)
  python main.py --source tech      # tech news
  python main.py --source podcast   # podcasts
  python main.py --source wechat    # WeChat accounts
  python main.py --source all       # all sources
  python main.py --hours 48         # custom time range
  python main.py --language en      # report language
  python main.py --finalize         # build report from sub-agent summaries
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# Flush stdout on every print (helps concurrent progress output)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
os.environ.setdefault("PYTHONUNBUFFERED", "1")

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from core.pipeline import (
    finalize_reports,
    run_tech_unified,
    run_podcast,
    run_wechat,
    build_merged_report,
)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

_SOURCE_RUNNERS = {
    "tech": lambda hours, lang, limit: run_tech_unified(hours=hours, language=lang, limit=limit),
    "podcast": lambda hours, _lang, limit: run_podcast(hours=hours, limit=limit),
    "wechat": lambda hours, _lang, limit: run_wechat(hours=hours, limit=limit),
}

_DEFAULT_HOURS = {"tech": 25, "podcast": 25, "wechat": 25}


def _try_build_unified_report(sections, now, language, source, output_format="markdown"):
    """Attempt to build a unified two-part report from workspace data.

    Returns None if API_KEY is not set (falls back to merged report).
    """
    from core.pipeline import try_build_unified_report
    return try_build_unified_report(source, now, language, output_format=output_format)


def main():
    parser = argparse.ArgumentParser(
        description="Daily Digest - unified daily digest generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # tech news (default)
  python main.py --source podcast   # podcasts
  python main.py --source wechat    # WeChat accounts
  python main.py --source all       # all sources
  python main.py --source tech --hours 72
        """,
    )
    parser.add_argument("--source", choices=["tech", "podcast", "wechat", "all"],
                        default="tech", help="source type (default: tech)")
    parser.add_argument("--hours", type=int, default=None,
                        help="look-back window in hours (default: 25)")
    parser.add_argument("--language", choices=["zh", "en"], default=None,
                        help="report language (default: zh)")
    parser.add_argument("--finalize", action="store_true",
                        help="build report from sub-agent summaries in workspace/")
    parser.add_argument("--limit", type=int, default=None,
                        help="limit number of sources (for testing)")
    parser.add_argument("--format", choices=["markdown", "wechat"],
                        default="markdown", dest="output_format",
                        help="output format: markdown (default) or wechat (公众号)")
    args = parser.parse_args()

    language = args.language or os.environ.get("REPORT_LANGUAGE", "zh")
    start_time = datetime.now(timezone.utc)

    # --finalize: read sub-agent summaries and produce final report
    if args.finalize:
        print("\n" + "=" * 60)
        print(f"\U0001F4CB Daily Digest -- Finalize mode")
        print(f"\u23f0 {start_time.strftime('%Y-%m-%d %H:%M UTC')} | source: {args.source}")
        print("=" * 60)
        finalize_reports(args.source, language, output_format=args.output_format)
        return

    # Normal mode: fetch, summarise, and generate
    print("\n" + "=" * 60)
    print(f"\U0001F4E1 Daily Digest")
    print(f"\u23f0 {start_time.strftime('%Y-%m-%d %H:%M UTC')} | source: {args.source} | lang: {language}")
    print("=" * 60)

    sections = []
    all_stats = {}

    for src, runner in _SOURCE_RUNNERS.items():
        if args.source not in (src, "all"):
            continue
        # WeChat already included in run_tech_unified; skip to avoid double-fetch
        if src == "wechat" and args.source == "all":
            continue
        hours = args.hours or _DEFAULT_HOURS.get(src, 25)
        result = runner(hours, language, args.limit)
        if result:
            report, stats = result
            sections.append(report)
            all_stats[src] = stats

    if not sections:
        print("\n\u26a0\ufe0f no updates, nothing to report.")
        return

    from core.config import OUTPUT_DIR
    from core.report_generator import save_report

    now = datetime.now(timezone.utc)

    # Try to build unified two-part report (AI deep + non-AI)
    unified = _try_build_unified_report(sections, now, language, args.source,
                                        output_format=args.output_format)
    if unified:
        report_content = unified
    else:
        report_content = build_merged_report(sections, now, language)

    is_wechat = args.output_format == "wechat"
    ext = "wechat-" + now.strftime('%Y-%m-%d') + ".md" if is_wechat else now.strftime('%Y-%m-%d') + ".md"
    filepath = save_report(report_content, ext, OUTPUT_DIR,
                           report_type="digest", language=language,
                           skip_tldr=is_wechat)

    duration = (datetime.now(timezone.utc) - start_time).total_seconds()
    print("\n" + "=" * 60)
    print(f"\u2705 Daily Digest done! report: {filepath}")
    for src, st in all_stats.items():
        print(f"  {src}: {st}")
    print(f"\u23f1\ufe0f total: {duration:.1f}s")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n\u26a0\ufe0f interrupted by user.")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"\n\n\u274c file not found: {e}")
        print("   check that config files exist in config/")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"\n\n\u274c config format error: {e}")
        print("   check JSON syntax in config files")
        sys.exit(1)
    except ConnectionError as e:
        print(f"\n\n\u274c connection failed: {e}")
        print("   check network or try again later")
        sys.exit(1)
    except TimeoutError as e:
        print(f"\n\n\u274c request timeout: {e}")
        print("   try --limit to reduce the number of sources")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n\u274c error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
