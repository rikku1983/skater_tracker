"""
Create the clubs table, populate it from clubs_with_normalized_names_and_abbrevs.csv,
and link each skater to their club via club_id.

Only rows where BOTH normalized_club and abbreviation are non-empty are inserted
as Club records (high-confidence entries).

Usage:
    python scripts/link_clubs.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from sqlalchemy import text

from src.db.session import get_session, init_db, get_engine
from src.db.models import Base, Club, Skater

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CSV_PATH = Path("data/clubs_with_normalized_names_and_abbrevs.csv")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not CSV_PATH.exists():
        logger.error("CSV not found: %s", CSV_PATH)
        sys.exit(1)

    # Create tables (clubs + add club_id column to skaters if missing)
    engine = init_db()

    # Add club_id column to skaters if not present (idempotent)
    with engine.connect() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(skaters)"))]
        if "club_id" not in cols:
            conn.execute(text("ALTER TABLE skaters ADD COLUMN club_id INTEGER REFERENCES clubs(id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_skaters_club_id ON skaters (club_id)"))
            conn.commit()
            logger.info("Added club_id column to skaters")

    # Load CSV — build two mappings:
    #   raw_club_name → normalized_club name  (for skater lookup)
    #   normalized_club name → abbreviation   (for Club creation)
    raw_to_norm: dict[str, str] = {}
    norm_to_abbrev: dict[str, str] = {}

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw = row["club_name"].strip()
            norm = row["normalized_club"].strip()
            abbrev = row["abbreviation"].strip()
            if norm and abbrev:
                raw_to_norm[raw] = norm
                norm_to_abbrev.setdefault(norm, abbrev)

    logger.info("CSV: %d raw entries map to %d canonical clubs", len(raw_to_norm), len(norm_to_abbrev))

    db = get_session()

    # Phase 1: create Club rows for each unique canonical name
    clubs_created = 0
    norm_to_club_id: dict[str, int] = {}

    fake_id = -1
    for norm, abbrev in norm_to_abbrev.items():
        existing = db.query(Club).filter(Club.name == norm).first()
        if existing:
            norm_to_club_id[norm] = existing.id
        else:
            clubs_created += 1
            if not args.dry_run:
                club = Club(name=norm, abbreviation=abbrev)
                db.add(club)
                db.flush()
                norm_to_club_id[norm] = club.id
            else:
                norm_to_club_id[norm] = fake_id
                fake_id -= 1

    if not args.dry_run:
        db.flush()

    # Phase 2: link skaters
    skaters_linked = 0
    skaters_skipped = 0

    for skater in db.query(Skater).all():
        if not skater.club_name:
            skaters_skipped += 1
            continue
        norm = raw_to_norm.get(skater.club_name.strip())
        if norm is None:
            skaters_skipped += 1
            continue
        club_id = norm_to_club_id.get(norm)
        if club_id is None:
            skaters_skipped += 1
            continue
        if skater.club_id != club_id:
            if not args.dry_run:
                skater.club_id = club_id
            skaters_linked += 1

    if not args.dry_run:
        db.commit()
    db.close()

    print("\n=== Club Linking Summary ===")
    print(f"  Canonical clubs created: {clubs_created}")
    print(f"  Skaters linked:          {skaters_linked}")
    print(f"  Skaters unlinked:        {skaters_skipped}")
    if args.dry_run:
        print("  (dry run — no changes written)")


if __name__ == "__main__":
    main()
