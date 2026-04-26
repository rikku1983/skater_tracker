"""
Backfill event_group, series, and event_date for existing Event records.

  --dry-run          Print proposed changes without writing to DB
  --event-group-only Only backfill event_group/series
  --date-only        Only backfill event_date
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Event
from src.downloader.normalize import normalize_event
from src.downloader.pdf_date_scan import scan_pdf_for_date

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def backfill_event_groups(db, dry_run: bool) -> tuple[int, int]:
    """Return (updated, skipped)."""
    events = db.query(Event).filter(Event.event_group.is_(None)).all()
    updated = 0
    skipped = 0
    for ev in events:
        group, series, conf = normalize_event(ev.event_name)
        if group:
            status = "DRY" if dry_run else "SET"
            logger.info(
                "[%s] event_group=%r series=%r conf=%.2f | %s",
                status, group, series, conf, ev.event_name,
            )
            if not dry_run:
                ev.event_group = group
                if series and ev.series is None:
                    ev.series = series
            updated += 1
        else:
            logger.warning("NO MATCH (conf=%.2f) | %s", conf, ev.event_name)
            skipped += 1
    if not dry_run:
        db.commit()
    return updated, skipped


def backfill_dates(db, dry_run: bool) -> tuple[int, int]:
    """Return (updated, skipped)."""
    events = (
        db.query(Event)
        .filter(Event.event_date.is_(None), Event.local_path.isnot(None))
        .all()
    )
    updated = 0
    skipped = 0
    for ev in events:
        path = Path(ev.local_path)
        if not path.exists():
            logger.debug("File not found: %s", path)
            skipped += 1
            continue
        d = scan_pdf_for_date(path)
        if d:
            status = "DRY" if dry_run else "SET"
            logger.info("[%s] date=%s | %s", status, d, ev.event_name)
            if not dry_run:
                ev.event_date = d
            updated += 1
        else:
            logger.warning("NO DATE FOUND | %s", ev.event_name)
            skipped += 1
    if not dry_run:
        db.commit()
    return updated, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--event-group-only", action="store_true")
    parser.add_argument("--date-only", action="store_true")
    args = parser.parse_args()

    init_db()
    db = get_session()

    try:
        if not args.date_only:
            logger.info("=== Backfilling event_group / series ===")
            updated, skipped = backfill_event_groups(db, args.dry_run)
            logger.info("event_group: %d updated, %d no match", updated, skipped)

        if not args.event_group_only:
            logger.info("=== Backfilling event_date from PDFs ===")
            updated, skipped = backfill_dates(db, args.dry_run)
            logger.info("event_date: %d updated, %d not found", updated, skipped)
    finally:
        db.close()


if __name__ == "__main__":
    main()
