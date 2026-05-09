"""
Apply the reviewed club affiliation mapping to Skater.club_name in the DB.

Reads data/club_affiliations.csv (produced by build_club_mapping.py, reviewed by user).

For each row:
  type=club    → SET club_name=canonical; if canonical matches Club.name, also set club_id
  type=country → SET club_name=canonical (ISO code)
  type=direct  → SET club_name='Direct'
  type=junk    → SET club_name=NULL
  type=unknown or canonical empty → skip (not yet reviewed)

Supports --dry-run.

Usage:
    python scripts/apply_club_mapping.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Club, Skater

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CSV_PATH = Path("data/club_affiliations.csv")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(CSV_PATH, newline="") as f:
        rows = list(csv.DictReader(f))

    init_db()
    db = get_session()

    # Build Club.name → club_id lookup
    club_id_by_name: dict[str, int] = {
        c.name: c.id for c in db.query(Club).all()
    }

    stats: Counter[str] = Counter()
    skipped = 0

    for row in rows:
        raw = row["raw_value"]
        typ = row["type"].strip().lower()
        canonical = row["canonical"].strip()

        # Skip unreviewed / empty
        if not typ or typ == "unknown" or (typ != "junk" and not canonical):
            skipped += 1
            continue

        # Determine what to set
        if typ == "junk":
            new_club_name = None
            new_club_id = None
        else:
            new_club_name = canonical
            new_club_id = club_id_by_name.get(canonical) if typ == "club" else None

        # Find skaters with this raw club_name
        affected = db.query(Skater).filter(Skater.club_name == raw).all()
        if not affected:
            continue

        logger.info("  %-4s  %-40s → %-35s  (%d skaters)",
                    typ, raw[:40], canonical or "NULL", len(affected))

        if not args.dry_run:
            for skater in affected:
                skater.club_name = new_club_name
                if new_club_id and not skater.club_id:
                    skater.club_id = new_club_id

        stats[typ] += len(affected)

    if not args.dry_run:
        db.commit()
        logger.info("Committed.")
    else:
        logger.info("(dry-run — no changes written)")

    logger.info("\n=== Summary ===")
    logger.info("  %-15s %d", "skipped (not reviewed)", skipped)
    for typ in ["club", "country", "direct", "junk"]:
        if stats[typ]:
            logger.info("  %-15s %d skaters updated", typ, stats[typ])

    db.close()


if __name__ == "__main__":
    main()
