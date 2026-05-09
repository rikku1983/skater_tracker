"""
Scan all results and label known data quality issues in results.data_flags.

Flags (pipe-separated if multiple apply):
  TIME_IMPOSSIBLE          — time is below the short-track world-record floor for the distance
  TIME_LIKELY_MISLABELED_DIST — distance_m=1500 but time < 120s (these are almost certainly
                                1000m results mislabeled by the tempus_results parser)

Safe to re-run: clears existing flags and rewrites them from scratch.

Usage:
  python scripts/flag_data_quality.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Result, Race, Event

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Hard impossibility floors (seconds). Anything faster = bad parse.
# Based on world records with a small buffer below.
WR_FLOORS: dict[int, float] = {
    500:  36.0,
    777:  58.0,
    1000: 78.0,
    1500: 120.0,
}


def compute_flags(time_seconds: float | None, distance_m: int | None) -> list[str]:
    flags: list[str] = []
    if time_seconds is None or time_seconds <= 0 or distance_m is None:
        return flags

    floor = WR_FLOORS.get(distance_m)
    if floor and time_seconds < floor:
        if distance_m == 1500 and time_seconds < 120:
            flags.append("TIME_LIKELY_MISLABELED_DIST")
        else:
            flags.append("TIME_IMPOSSIBLE")
    return flags


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report counts without writing to DB")
    args = parser.parse_args()

    init_db()
    db = get_session()

    # Clear stale flags from non-short-track events (long/mixed have valid "fast" times).
    if not args.dry_run:
        stale = (
            db.query(Result)
            .join(Race, Result.race_id == Race.id)
            .join(Event, Race.event_id == Event.id)
            .filter(
                Result.data_flags.isnot(None),
                Event.track_type.notin_(["short"]),
                Event.track_type.isnot(None),
            )
            .all()
        )
        for r in stale:
            r.data_flags = None
        db.commit()
        logger.info("Cleared stale flags from %d non-short-track results.", len(stale))

    # Load all results joined with race distance in one pass.
    # Only flag short-track events; long/mixed events have valid fast times.
    rows = (
        db.query(Result, Race.distance_m, Event.pdf_format, Event.event_name)
        .join(Race, Result.race_id == Race.id)
        .join(Event, Race.event_id == Event.id)
        .filter(Event.track_type.in_(["short", None]))
        .all()
    )

    counts: dict[str, int] = {}
    flagged_results: list[tuple[Result, list[str]]] = []

    for result, distance_m, pdf_format, event_name in rows:
        flags = compute_flags(result.time_seconds, distance_m)
        # Track old flags to detect changes
        new_flag_str = "|".join(flags) if flags else None

        if flags:
            flagged_results.append((result, flags))
            for f in flags:
                counts[f] = counts.get(f, 0) + 1
            if not args.dry_run:
                result.data_flags = new_flag_str
        else:
            if not args.dry_run:
                result.data_flags = None  # clear any stale flag

    if not args.dry_run:
        db.commit()

    # Summary
    total_flagged = len(flagged_results)
    logger.info("=== Data Quality Flag Summary ===")
    logger.info("Total results scanned : %d", len(rows))
    logger.info("Total results flagged : %d", total_flagged)
    for flag, count in sorted(counts.items()):
        logger.info("  %-35s %d", flag, count)

    # Breakdown by pdf_format
    from collections import defaultdict
    by_format: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for result, flags in flagged_results:
        # re-fetch format from joined data — look it up
        pass

    # Do a separate breakdown query for reporting
    logger.info("\n=== Flagged results by pdf_format ===")
    breakdown = (
        db.query(Event.pdf_format, Result.data_flags if not args.dry_run else Result.id)
        .join(Race, Result.race_id == Race.id)
        .join(Event, Race.event_id == Event.id)
        .filter(Result.data_flags.isnot(None))
        .all()
    ) if not args.dry_run else []

    fmt_counts: dict[str, int] = defaultdict(int)
    for fmt, _ in breakdown:
        fmt_counts[fmt] += 1
    for fmt, cnt in sorted(fmt_counts.items(), key=lambda x: -x[1]):
        logger.info("  %-25s %d", fmt, cnt)

    if args.dry_run:
        logger.info("\n(dry-run — no changes written)")

    db.close()


if __name__ == "__main__":
    main()
