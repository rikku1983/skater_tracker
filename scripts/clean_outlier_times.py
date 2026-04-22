"""
Null out or delete results with physically impossible or absurdly slow times.

Actions:
  TOO_FAST (non-1500m): null time_text + time_seconds, set status='INVALID_TIME'
  TOO_FAST (1500m):     delete result row (mislabeled distance — wrong race)
  TOO_SLOW (any):       null time_text + time_seconds, set status='INVALID_TIME'

Thresholds (~3× typical median for slow cutoff, world-record vicinity for fast):
  333m:  < 25s  or > 130s
  500m:  < 39s  or > 180s
  777m:  < 55s  or > 300s
  1000m: < 60s  or > 360s
  1500m: < 60s  or > 600s

Usage:
    python scripts/clean_outlier_times.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Result, Race

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

THRESHOLDS: dict[int, tuple[float, float]] = {
    333:  (25.0,  130.0),
    500:  (39.0,  180.0),
    777:  (55.0,  300.0),
    1000: (60.0,  360.0),
    1500: (60.0,  600.0),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    init_db()
    db = get_session()

    to_null: list[Result] = []
    to_delete: list[Result] = []

    for dist, (lo, hi) in THRESHOLDS.items():
        rows = (
            db.query(Result)
            .join(Race, Result.race_id == Race.id)
            .filter(
                Race.distance_m == dist,
                Result.time_seconds.isnot(None),
            )
            .all()
        )
        for result in rows:
            t = result.time_seconds
            if t < lo:
                if dist == 1500:
                    to_delete.append(result)
                else:
                    to_null.append(result)
            elif t > hi:
                to_null.append(result)

    logger.info("Results to null:   %d", len(to_null))
    logger.info("Results to delete: %d", len(to_delete))

    if not args.dry_run:
        for result in to_null:
            result.time_text = None
            result.time_seconds = None
            if not result.status:
                result.status = "INVALID_TIME"

        for result in to_delete:
            db.delete(result)

        db.commit()

    db.close()

    print("\n=== Outlier Time Cleanup Summary ===")
    print(f"  Times nulled (rank preserved): {len(to_null)}")
    print(f"  Results deleted (1500m mislab): {len(to_delete)}")
    if args.dry_run:
        print("  (dry run — no changes written)")


if __name__ == "__main__":
    main()
