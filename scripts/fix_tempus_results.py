"""
Fix three classes of data errors produced by the tempus_results parser.

  2a) "0:MM.SSS" times where the real time has 1 minute prepended.
      The PDF stores e.g. "0:53.265" but the race time is "1:53.265".
      Fix: add 60 s, change "0:" → "1:", clear data_flags.

  2b) 1500m → 1000m distance relabeling.
      1000m times (<2:00) stored under a 1500m Race.
      Fix: move Result.race_id to the correct 1000m Race.

  2c) 1000m → 500m distance relabeling.
      500m times (1:00–1:12, i.e. 60–72 s) stored under a 1000m Race.
      Fix: move Result.race_id to the correct 500m Race.

Usage:
    python scripts/fix_tempus_results.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Event, Race, Result

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WR_FLOORS: dict[int, float] = {
    500:  36.0,
    777:  58.0,
    1000: 78.0,
    1500: 120.0,
}


def _find_or_create_race(db, event_id: int, division: str, distance_m: int,
                          round_: str | None, dry_run: bool) -> Race | None:
    race = (
        db.query(Race)
        .filter(
            Race.event_id == event_id,
            Race.division == division,
            Race.distance_m == distance_m,
            Race.round == round_,
        )
        .first()
    )
    if race:
        return race
    if dry_run:
        logger.info("  [dry-run] Would create Race event=%s %sm %s %s",
                    event_id, distance_m, division, round_)
        return None
    race = Race(event_id=event_id, division=division,
                distance_m=distance_m, round=round_)
    db.add(race)
    db.flush()
    return race


def fix_prefix(db, dry_run: bool) -> int:
    """2a: add 60 s to all '0:MM.SSS' TIME_IMPOSSIBLE results from tempus_results."""
    rows = (
        db.query(Result, Race.distance_m)
        .join(Race, Result.race_id == Race.id)
        .join(Event, Race.event_id == Event.id)
        .filter(
            Event.pdf_format == "tempus_results",
            Result.data_flags == "TIME_IMPOSSIBLE",
        )
        .all()
    )

    count = 0
    for result, distance_m in rows:
        if not result.time_text or not result.time_text.startswith("0:"):
            continue
        original_s = result.time_seconds
        corrected_s = original_s + 60.0
        floor = WR_FLOORS.get(distance_m)
        if floor and corrected_s <= floor:
            logger.warning("  SKIP prefix fix: %s + 60 = %.1f still ≤ WR floor %.1f for %sm",
                           result.time_text, corrected_s, floor, distance_m)
            continue
        new_text = "1:" + result.time_text[2:]
        logger.info("  PREFIX  result_id=%d  %s → %s  (%.2f → %.2f s)",
                    result.id, result.time_text, new_text, original_s, corrected_s)
        if not dry_run:
            result.time_text = new_text
            result.time_seconds = corrected_s
            result.data_flags = None
        count += 1
    return count


def fix_1500_to_1000(db, dry_run: bool) -> int:
    """2b: move TIME_LIKELY_MISLABELED_DIST results to 1000m Race.
    Also fixes '0:' prefix on the same result when present.
    Covers both tempus_results and llm formats.
    """
    rows = (
        db.query(Result, Race)
        .join(Race, Result.race_id == Race.id)
        .join(Event, Race.event_id == Event.id)
        .filter(
            Event.pdf_format.in_(["tempus_results", "llm"]),
            Result.data_flags == "TIME_LIKELY_MISLABELED_DIST",
        )
        .all()
    )

    count = 0
    deleted = 0
    for result, src_race in rows:
        target = _find_or_create_race(
            db, src_race.event_id, src_race.division, 1000, src_race.round, dry_run)

        # Check if target race already has a result for this skater — if so, the
        # mislabeled result is a duplicate. Delete it rather than moving it.
        if target and not dry_run:
            existing = (
                db.query(Result)
                .filter(Result.race_id == target.id,
                        Result.skater_id == result.skater_id)
                .first()
            )
            if existing:
                logger.info("  1500→DELETE (dup)  result_id=%d  skater_id=%d  event=%d",
                            result.id, result.skater_id or 0, src_race.event_id)
                db.delete(result)
                deleted += 1
                continue

        # Also fix "0:" prefix if present (some rows have both issues)
        if result.time_text and result.time_text.startswith("0:"):
            new_text = "1:" + result.time_text[2:]
            new_secs = result.time_seconds + 60.0
            logger.info("  1500→1000+prefix  result_id=%d  %s → %s  event=%d",
                        result.id, result.time_text, new_text, src_race.event_id)
            if not dry_run:
                result.time_text = new_text
                result.time_seconds = new_secs
        else:
            logger.info("  1500→1000  result_id=%d  %s  event=%d  %s",
                        result.id, result.time_text, src_race.event_id, src_race.division[:40])
        if not dry_run and target:
            result.race_id = target.id
            result.data_flags = None
        count += 1

    if deleted:
        logger.info("  (deleted %d duplicate mislabeled results)", deleted)
    return count + deleted


def fix_1000_to_shorter(db, dry_run: bool) -> int:
    """2c: move 1000m TIME_IMPOSSIBLE results to 500m (or 777m) Race.

    Handles both '1:MM.SSS' prefix (still too fast for 1000m) and
    '0:MM.SSS' prefix where +60 didn't clear the 1000m floor — those are
    moved to 500m with the prefix fix applied.

    Prefers 500m. Falls back to 777m if 500m races don't exist for that division.
    Skips if neither shorter distance is present in the event.
    """
    rows = (
        db.query(Result, Race)
        .join(Race, Result.race_id == Race.id)
        .join(Event, Race.event_id == Event.id)
        .filter(
            Event.pdf_format == "tempus_results",
            Result.data_flags == "TIME_IMPOSSIBLE",
            Race.distance_m == 1000,
        )
        .all()
    )

    count = 0
    for result, src_race in rows:
        if not result.time_text:
            continue
        # Accept "1:" prefix AND "0:" prefix that still failed the 1000m guard
        if result.time_text.startswith("1:"):
            fix_prefix_here = False
        elif result.time_text.startswith("0:"):
            # Would adding 60 s still be below 500m WR floor (36 s)?
            if (result.time_seconds + 60) < 36:
                logger.warning("  SKIP 1000→shorter: even +60 is < 36s for result_id=%d (%s)",
                               result.id, result.time_text)
                continue
            fix_prefix_here = True
        else:
            continue

        # Prefer 500m; fall back to 777m if division has no 500m in this event
        existing_dists = {
            d[0] for d in
            db.query(Race.distance_m)
            .filter(Race.event_id == src_race.event_id,
                    Race.division == src_race.division)
            .distinct().all()
        }
        if 500 in existing_dists:
            target_dist = 500
        elif 777 in existing_dists:
            target_dist = 777
        else:
            logger.warning("  SKIP 1000→shorter: no 500m or 777m for event=%d %s",
                           src_race.event_id, src_race.division)
            continue

        target = _find_or_create_race(
            db, src_race.event_id, src_race.division, target_dist, src_race.round, dry_run)
        if fix_prefix_here:
            new_text = "1:" + result.time_text[2:]
            new_secs = result.time_seconds + 60.0
            logger.info("  1000→%dm+prefix  result_id=%d  %s → %s  event=%d  %s",
                        target_dist, result.id, result.time_text, new_text,
                        src_race.event_id, src_race.division[:40])
            if not dry_run:
                result.time_text = new_text
                result.time_seconds = new_secs
        else:
            logger.info("  1000→%dm  result_id=%d  %s  event=%d  %s",
                        target_dist, result.id, result.time_text,
                        src_race.event_id, src_race.division[:40])
        if not dry_run and target:
            result.race_id = target.id
            result.data_flags = None
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    init_db()
    db = get_session()

    logger.info("=== 2a: Fix '0:' time prefix ===")
    n_prefix = fix_prefix(db, args.dry_run)
    logger.info("  → %d results updated\n", n_prefix)

    logger.info("=== 2b: Fix 1500m → 1000m mislabeling ===")
    n_1500 = fix_1500_to_1000(db, args.dry_run)
    logger.info("  → %d results moved\n", n_1500)

    logger.info("=== 2c: Fix 1000m → 500m/777m mislabeling ===")
    n_1000 = fix_1000_to_shorter(db, args.dry_run)
    logger.info("  → %d results moved\n", n_1000)

    if not args.dry_run:
        db.commit()
        logger.info("Committed all changes.")
    else:
        logger.info("(dry-run — no changes written)")

    logger.info("\n=== Summary ===")
    logger.info("  %-30s %d", "0: prefix fixed", n_prefix)
    logger.info("  %-30s %d", "1500m → 1000m moved", n_1500)
    logger.info("  %-30s %d", "1000m → 500/777m moved", n_1000)
    logger.info("  %-30s %d", "total", n_prefix + n_1500 + n_1000)
    db.close()


if __name__ == "__main__":
    main()
