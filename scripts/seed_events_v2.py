"""
Seed the events table in skater_tracker_round2.db from knowledge_base/events.csv.

Usage:
    python scripts/seed_events_v2.py [--db PATH]
"""
from __future__ import annotations
import argparse
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session_v2 import init_db, get_session
from src.db.models_v2 import Event

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CSV_PATH = Path("knowledge_base/events.csv")

# Columns present in knowledge_base/events.csv → Event model attributes
COL_MAP = {
    "season":      "season",
    "event_name":  "event_name",
    "event_date":  "event_date",
    "series":      "series",
    "event_group": "event_group",
    "track_type":  "track_type",
    "pdf_format":  "pdf_format",
    "local_path":  "local_path",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="Path to SQLite DB (default: data/skater_tracker_round2.db)")
    parser.add_argument("--csv", default=str(CSV_PATH), help="Path to events CSV")
    args = parser.parse_args()

    db_kwargs = {"db_path": args.db} if args.db else {}
    init_db(**db_kwargs)
    db = get_session(**db_kwargs)

    with open(args.csv, newline="") as f:
        rows = list(csv.DictReader(f))

    inserted = updated = 0
    for row in rows:
        local_path = row.get("local_path", "").strip()
        if not local_path:
            continue

        existing = db.query(Event).filter(Event.local_path == local_path).first()
        if existing:
            for csv_col, attr in COL_MAP.items():
                val = row.get(csv_col, "").strip() or None
                setattr(existing, attr, val)
            updated += 1
        else:
            ev = Event()
            for csv_col, attr in COL_MAP.items():
                val = row.get(csv_col, "").strip() or None
                setattr(ev, attr, val)
            db.add(ev)
            inserted += 1

    db.commit()
    total = db.query(Event).count()
    logger.info("Inserted: %d  Updated: %d  Total in DB: %d", inserted, updated, total)
    db.close()


if __name__ == "__main__":
    main()
