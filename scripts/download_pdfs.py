#!/usr/bin/env python3
"""
CLI entry point: discover and download all USS race result PDFs.

Usage:
    python scripts/download_pdfs.py
    python scripts/download_pdfs.py --season 20252026 20242025
    python scripts/download_pdfs.py --no-skip   # re-download everything
    python scripts/download_pdfs.py --dry-run    # list events without downloading
"""

import argparse
import logging
import sys
from pathlib import Path

# Ensure src/ is on the path when running from project root
sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import init_db, get_session
from src.downloader.discover import discover_events, KNOWN_SEASONS
from src.downloader.download import download_all


def main():
    parser = argparse.ArgumentParser(description="Download USS speed skating result PDFs")
    parser.add_argument(
        "--season",
        nargs="+",
        metavar="SEASON_ID",
        help=f"Season IDs to fetch (default: all). Known: {', '.join(KNOWN_SEASONS)}",
    )
    parser.add_argument(
        "--no-skip",
        action="store_true",
        help="Re-download PDFs already in the manifest",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and list events without downloading",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Init DB
    engine = init_db()
    db = get_session()

    # Discover
    events = discover_events(seasons=args.season or KNOWN_SEASONS)

    if args.dry_run:
        print(f"\nDiscovered {len(events)} events:\n")
        for e in events:
            date_str = str(e.event_date) if e.event_date else "unknown date"
            print(f"  {e.season} | {date_str} | {e.event_name}")
        db.close()
        return

    # Download
    stats = download_all(
        events=events,
        db=db,
        skip_existing=not args.no_skip,
    )

    print(f"\nSummary: {stats}")
    db.close()


if __name__ == "__main__":
    main()
