"""
Daily Digest - unified entry point.
Supports GitHub Actions and Claude Code Skill modes.

Usage:
  python main.py                    # tech news (default)
  python main.py --source tech      # tech news
  python main.py --source podcast   # podcasts
  python main.py --source all       # all sources (tech + podcast)
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
    run_podcast,
    build_merged_report,
)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

_SOURCE_RUNNERS = {
    "tech": lambda hours, limit: run_tech_unified(hours=hours, limit=limit),
    "podcast": lambda hours, limit: run_podcast(hours=hours, limit=limit),
}

_DEFAULT_HOURS = {"tech": 25, "podcast": 25}


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
  python main.py --source podcast   # podcasts
  python main.py --source all       # all sources (tech + podcast)
  python main.py --source tech --hours 72
        """,
    )
    parser.add_argument("--source", choices=["tech", "podcast", "all"],
                        default="tech", help="source type (default: tech)")
    parser.add_argument("--hours", type=int, default=None,
                        help="look-back window in hours (default: 25)")
    parser.add_argument("--finalize", action="store_true",
                        help="build report from sub-agent summaries in workspace/")
    parser.add_argument("--limit", type=int, default=None,
                        help="limit number of sources (for testing)")
    parser.add_argument("--podcast-only", choices=["tech", "podcast", "all"],
                        default=None,
                        help="generate podcast audio from existing reports (skip pipeline)")
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

    # --podcast-only: generate audio from existing reports
    if args.podcast_only:
        print("\n" + "=" * 60)
        print(f"\U0001f3a4 Podcast Audio Generation")
        print(f"⏰ {start_time.strftime('%Y-%m-%d %H:%M UTC')} | source: {args.podcast_only}")
        print("=" * 60)

        from core.podcast_generator import generate_podcast_audio
        from core.config import TECH_OUTPUT_DIR, PODCAST_OUTPUT_DIR
        date_str = start_time.strftime('%Y-%m-%d')

        sources = ["tech", "podcast"] if args.podcast_only == "all" else [args.podcast_only]
        for src in sources:
            if src == "tech":
                report_file = TECH_OUTPUT_DIR / f"{date_str}.md"
            else:
                report_file = PODCAST_OUTPUT_DIR / f"{date_str}.md"

            if not report_file.exists():
                print(f"  ⚠️ {src} report not found: {report_file}")
                continue

            result = generate_podcast_audio(str(report_file), src, date_str)
            if result:
                print(f"  ✅ {src}: {result}")
            else:
                print(f"  ❌ {src}: generation failed")

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
        # Reset LLM degraded mode between pipelines to avoid spillover
        if src != "tech" and os.environ.get("API_KEY"):
            from core.llm import reset_llm_degraded_mode
            reset_llm_degraded_mode()
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

    from core.config import TECH_OUTPUT_DIR, PODCAST_OUTPUT_DIR
    from core.report_builder import save_report

    now = datetime.now(timezone.utc)
    date_str = now.strftime('%Y-%m-%d')
    ext = f"{date_str}.md"

    def _generate_podcast_audio(fp, report_type):
        """Generate podcast audio for a saved report (API mode only)."""
        if not os.environ.get("API_KEY"):
            return
        try:
            from core.podcast_generator import generate_podcast_audio
            mp3 = generate_podcast_audio(fp, report_type, date_str)
            if mp3:
                print(f"  Podcast audio: {mp3}")
        except Exception as e:
            print(f"  Podcast generation failed: {e}")

    if args.source == "all":
        # Multi-source: save each report to its own directory using actual results
        filepaths = []
        for src, report_content in zip(all_stats.keys(), sections):
            if src == "podcast":
                fp = save_report(report_content, ext, PODCAST_OUTPUT_DIR,
                               report_type="digest")
                _generate_podcast_audio(fp, "podcast")
            else:
                fp = save_report(report_content, ext, TECH_OUTPUT_DIR,
                               report_type="digest")
                _generate_podcast_audio(fp, "tech")
            filepaths.append(fp)
        filepath = filepaths
    elif args.source == "podcast":
        report_content = sections[0]
        filepath = save_report(report_content, ext, PODCAST_OUTPUT_DIR,
                               report_type="digest")
        _generate_podcast_audio(filepath, "podcast")
    else:
        report_content = sections[0]
        filepath = save_report(report_content, ext, TECH_OUTPUT_DIR,
                               report_type="digest")
        _generate_podcast_audio(filepath, "tech")

    duration = (datetime.now(timezone.utc) - start_time).total_seconds()
    print("\n" + "=" * 60)
    if isinstance(filepath, list):
        print("✅ Daily Digest done! reports:")
        for fp in filepath:
            print(f"  \U0001F4C4 {fp}")
    else:
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
