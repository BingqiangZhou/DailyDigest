"""
Daily Digest - unified entry point.
Supports GitHub Actions and Claude Code Skill modes.

Usage:
  python main.py                    # tech news (default)
  python main.py --source tech      # tech news
  python main.py --hours 48         # custom time range
  python main.py --finalize         # build report from sub-agent summaries
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# Flush stdout on every print (helps concurrent progress output)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True, errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")
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
    build_merged_report,
)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

_SOURCE_RUNNERS = {
    "tech": lambda hours, limit: run_tech_unified(hours=hours, limit=limit),
}

_DEFAULT_HOURS = {"tech": 25}


def _try_build_unified_report(sections, now, source):
    """Attempt to build a unified two-part report from workspace data.

    Returns None if API_KEY is not set (falls back to merged report).
    """
    from core.pipeline import try_build_unified_report
    return try_build_unified_report(source, now)


def main():
    parser = argparse.ArgumentParser(
        description="Daily Digest - unified daily digest generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # tech news (default)
  python main.py --source tech --hours 72
        """,
    )
    parser.add_argument("--source", choices=["tech", "all"],
                        default="all", help="source type (default: all)")
    parser.add_argument("--hours", type=int, default=None,
                        help="look-back window in hours (default: 25)")
    parser.add_argument("--finalize", action="store_true",
                        help="build report from sub-agent summaries in workspace/")
    parser.add_argument("--limit", type=int, default=None,
                        help="limit number of sources (for testing)")
    args = parser.parse_args()

    start_time = datetime.now(timezone.utc)

    # --finalize: read sub-agent summaries and produce final report
    if args.finalize:
        print("\n" + "=" * 60)
        print(f"\U0001F4CB Daily Digest -- Finalize mode")
        print(f"⏰ {start_time.strftime('%Y-%m-%d %H:%M UTC')} | source: {args.source}")
        print("=" * 60)
        finalize_reports(args.source)
        return

    # Normal mode: fetch, summarise, and generate
    print("\n" + "=" * 60)
    print(f"\U0001F4E1 Daily Digest")
    print(f"⏰ {start_time.strftime('%Y-%m-%d %H:%M UTC')} | source: {args.source}")
    print("=" * 60)

    sections = []
    all_stats = {}

    for src, runner in _SOURCE_RUNNERS.items():
        if args.source not in (src, "all"):
            continue
        hours = args.hours or _DEFAULT_HOURS.get(src, 25)
        result = runner(hours, args.limit)
        if result:
            report, stats = result
            sections.append(report)
            all_stats[src] = stats
        else:
            print(f"\n  ⚠️ {src} pipeline returned no results")

    if not sections:
        print("\n⚠️ no updates, nothing to report.")
        return

    from core.config import TECH_OUTPUT_DIR
    from core.report_builder import save_report

    now = datetime.now(timezone.utc)
    date_str = now.strftime('%Y-%m-%d')
    ext = f"{date_str}.md"

    report_content = sections[0]
    filepath = save_report(report_content, ext, TECH_OUTPUT_DIR,
                           report_type="digest")

    duration = (datetime.now(timezone.utc) - start_time).total_seconds()
    print("\n" + "=" * 60)
    print(f"✅ Daily Digest done! report: {filepath}")
    for src, st in all_stats.items():
        print(f"  {src}: {st}")
    print(f"⏱️ total: {duration:.1f}s")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ interrupted by user.")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"\n\n❌ file not found: {e}")
        print("   check that config files exist in config/")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"\n\n❌ config format error: {e}")
        print("   check JSON syntax in config files")
        sys.exit(1)
    except ConnectionError as e:
        print(f"\n\n❌ connection failed: {e}")
        print("   check network or try again later")
        sys.exit(1)
    except TimeoutError as e:
        print(f"\n\n❌ request timeout: {e}")
        print("   try --limit to reduce the number of sources")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
