"""
Delete and reload all classic_results events with the fixed parser.

This corrects:
  - Name order: "LastName, FirstName" → "FirstName LastName"
  - Inverted first/last in Skater table
  - Gender/Masters codes stripped from names and affiliations

Usage:
    python scripts/reload_classic_results.py [--dry-run]
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Event, Race, Skater, Result
from src.db.load import load_parsed_event
from src.parsers import parse_pdf

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen without making changes")
    args = parser.parse_args()

    init_db()
    db = get_session()

    classic_events = (
        db.query(Event)
        .filter(Event.pdf_format == "classic_results")
        .all()
    )

    if not classic_events:
        print("No classic_results events found in DB.")
        db.close()
        return

    print(f"Found {len(classic_events)} classic_results event(s):")
    for ev in classic_events:
        result_count = (
            db.query(Result)
            .join(Result.race)
            .filter(Race.event_id == ev.id)
            .count()
        )
        print(f"  [{ev.season}] {ev.event_name}  ({result_count} results)  path={ev.local_path}")

    before_skaters = db.query(Skater).count()
    before_results = db.query(Result).count()

    if args.dry_run:
        print("\n--dry-run: no changes made.")
        db.close()
        return

    print("\nStep 1: Deleting races (cascades to results) for classic_results events...")
    for ev in classic_events:
        races = db.query(Race).filter(Race.event_id == ev.id).all()
        for race in races:
            db.delete(race)
        db.flush()
        logger.info("  Deleted %d races for event %d (%s)", len(races), ev.id, ev.event_name)

    print("Step 2: Deleting orphan skaters (no remaining results)...")
    orphan_ids = (
        db.query(Skater.id)
        .outerjoin(Result, Result.skater_id == Skater.id)
        .filter(Result.id.is_(None))
        .all()
    )
    orphan_ids = [row[0] for row in orphan_ids]
    db.query(Skater).filter(Skater.id.in_(orphan_ids)).delete(synchronize_session=False)
    logger.info("  Deleted %d orphan skaters", len(orphan_ids))

    db.commit()

    print("Step 3: Re-parsing and reloading...")
    total_races = total_skaters = total_results = 0
    for ev in classic_events:
        if not ev.local_path:
            logger.warning("  No local_path for event %d, skipping", ev.id)
            continue
        pdf_path = Path(ev.local_path)
        if not pdf_path.exists():
            logger.warning("  File not found: %s", pdf_path)
            continue

        logger.info("  Parsing: %s", pdf_path.name)
        parsed = parse_pdf(pdf_path)

        if not parsed.results:
            logger.warning("  → 0 results, errors: %s", parsed.parse_errors[:3])
            continue

        races, skaters, results = load_parsed_event(db, pdf_path, parsed)
        ev.pdf_format = parsed.format.value
        db.commit()

        total_races += races
        total_skaters += skaters
        total_results += results
        logger.info("  → races=%d  skaters=%d  results=%d", races, skaters, results)

    after_skaters = db.query(Skater).count()
    after_results = db.query(Result).count()
    db.close()

    print("\n=== Reload Summary ===")
    print(f"  Skaters before:  {before_skaters}  →  after: {after_skaters}  (delta: {after_skaters - before_skaters:+d})")
    print(f"  Results before:  {before_results}  →  after: {after_results}  (delta: {after_results - before_results:+d})")
    print(f"  Races created:   {total_races}")
    print(f"  Skaters created: {total_skaters}")
    print(f"  Results loaded:  {total_results}")


if __name__ == "__main__":
    main()
