"""
Apply corrections from data/flagged_times_corrected.csv to the DB.

For each row in the corrected CSV, the script finds the corresponding flagged
Result in the DB by (event_id, skater_name) — then applies:

  - distance_m changed vs DB → move Result to target Race; target Race is found
    by (event_id, division, distance_m) ignoring round ("correct for the event"),
    creating one consolidated Race with round=None if none exists.
  - time_seconds changed vs DB (legacy row) → update time_seconds + time_text.
  - data_flags cleared in all cases.
  - If moving a result would duplicate a skater already in the target race, the
    mislabeled incoming result is deleted instead.

Usage:
    python scripts/apply_csv_corrections.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Event, Race, Result, Skater

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CORR_CSV = Path("data/flagged_times_corrected.csv")


def _seconds_to_time_text(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}:{secs:06.3f}"


def _find_flagged_result(db, event_id: int, skater_name: str,
                          corr_time_s: float | None) -> Result | None:
    """Find the flagged Result in the DB for this event + skater.

    Matches by (event_id, skater_name) among flagged results only.
    If multiple flagged results exist for the same skater in the same event,
    prefers the one whose time_seconds is closest to corr_time_s.
    """
    candidates = (
        db.query(Result)
        .join(Race,   Result.race_id   == Race.id)
        .join(Event,  Race.event_id    == Event.id)
        .join(Skater, Result.skater_id == Skater.id)
        .filter(
            Event.id          == event_id,
            Skater.full_name  == skater_name,
            Result.data_flags.isnot(None),
        )
        .all()
    )
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Multiple flagged results for same skater — pick closest time
    if corr_time_s is not None:
        candidates.sort(key=lambda r: abs((r.time_seconds or 0) - corr_time_s))
    return candidates[0]


def _find_or_create_race(db, event_id: int, division: str,
                          distance_m: int, dry_run: bool) -> Race | None:
    """Return a Race for (event, division, distance) ignoring round.
    Picks any existing match; creates one with round=None if none found.
    """
    race = (
        db.query(Race)
        .filter(Race.event_id   == event_id,
                Race.division   == division,
                Race.distance_m == distance_m)
        .first()
    )
    if race:
        return race
    if dry_run:
        logger.info("    [dry-run] Would create Race event=%d %sm '%s'",
                    event_id, distance_m, division)
        return None
    race = Race(event_id=event_id, division=division,
                distance_m=distance_m, round=None)
    db.add(race)
    db.flush()
    return race


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(CORR_CSV) as f:
        corr_rows = list(csv.DictReader(f))

    init_db()
    db = get_session()

    stats = {"dist_moved": 0, "dist_deleted": 0, "time_fixed": 0, "skip": 0}

    for i, corr in enumerate(corr_rows, 1):
        event_id    = int(corr["event_id"])
        skater_name = corr["skater_name"]
        corr_dist   = int(corr["distance_m"])
        corr_div    = corr["division"]
        corr_time_s = float(corr["time_seconds"]) if corr["time_seconds"] else None

        result = _find_flagged_result(db, event_id, skater_name, corr_time_s)
        if result is None:
            logger.warning("  [%d] NOT FOUND: event=%d '%s'", i, event_id, skater_name)
            stats["skip"] += 1
            continue

        db_dist   = result.race.distance_m
        db_time_s = result.time_seconds or 0.0

        dist_changed = corr_dist != db_dist
        time_changed = (corr_time_s is not None and
                        abs(corr_time_s - db_time_s) > 0.05)

        # --- Distance correction ---
        if dist_changed:
            target = _find_or_create_race(db, event_id, corr_div, corr_dist, args.dry_run)

            if target and not args.dry_run:
                dup = (db.query(Result)
                       .filter(Result.race_id   == target.id,
                               Result.skater_id == result.skater_id)
                       .first())
                if dup:
                    logger.info("  [%d] DELETE dup: event=%d '%s' %sm→%sm",
                                i, event_id, skater_name, db_dist, corr_dist)
                    db.delete(result)
                    stats["dist_deleted"] += 1
                    continue
                result.race_id   = target.id
                result.data_flags = None

            logger.info("  [%d] DIST: event=%d '%s'  %sm→%sm (%s)",
                        i, event_id, skater_name, db_dist, corr_dist, corr_div)
            if args.dry_run:
                stats["dist_moved"] += 1
            else:
                stats["dist_moved"] += 1

        # --- Time correction ---
        if time_changed:
            new_text = _seconds_to_time_text(corr_time_s)
            logger.info("  [%d] TIME: event=%d '%s'  %.2f→%.2f  (%s)",
                        i, event_id, skater_name, db_time_s, corr_time_s, new_text)
            if not args.dry_run:
                result.time_seconds = corr_time_s
                result.time_text    = new_text
                result.data_flags   = None
            stats["time_fixed"] += 1

        if not dist_changed and not time_changed:
            logger.debug("  [%d] no change: event=%d '%s'", i, event_id, skater_name)

    if not args.dry_run:
        db.commit()
        logger.info("Committed.")
    else:
        logger.info("(dry-run — no changes written)")

    logger.info("\n=== Summary ===")
    logger.info("  %-20s %d", "distance moved",   stats["dist_moved"])
    logger.info("  %-20s %d", "duplicate deleted", stats["dist_deleted"])
    logger.info("  %-20s %d", "time fixed",        stats["time_fixed"])
    logger.info("  %-20s %d", "not found (skip)",  stats["skip"])
    db.close()


if __name__ == "__main__":
    main()
