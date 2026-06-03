#!/usr/bin/env python3
"""Stage 3: Apply parsed CSV files to local SQLite DB.

Clears existing rows for the event from results, classification, and
time_classification, then inserts fresh rows from the parsed CSVs.
SQLite auto-assigns new IDs. Run /sync_to_supabase after this.

Usage:
    python3 scripts/apply_parse_output.py --event-id 128
    python3 scripts/apply_parse_output.py --event-id 128 --dry-run
"""
import argparse
import csv
import sqlite3
from pathlib import Path

SQLITE_PATH = Path(__file__).parent.parent / "data" / "skater_tracker_round2.db"


def parse_val(col: str, val: str):
    """Convert CSV string value to appropriate Python type for SQLite."""
    if val == '' or val is None:
        return None
    if col in ('event_id', 'distance_m', 'race_number', 'heat', 'rank',
               'page_number', 'cdr', 'bdr'):
        try: return int(val)
        except: return None
    if col in ('laps', 'time_seconds', 'best_time_seconds', 'points'):
        try: return float(val)
        except: return None
    if col == 'is_relay':
        return val.strip().upper() in ('TRUE', '1', 'YES')
    if col in ('skater_id', 'club_id'):
        try: return int(val)
        except: return None
    return val or None


def insert_csv(conn, csv_path: Path, table: str, event_id: int, dry_run: bool):
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print(f"  {csv_path.name}: 0 rows, skipping")
        return 0

    # Remove 'id' — let SQLite auto-assign
    cols = [c for c in rows[0].keys() if c != 'id']
    placeholders = ', '.join(['?'] * len(cols))
    col_list = ', '.join(cols)
    sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    values = []
    for r in rows:
        row_vals = tuple(parse_val(c, r.get(c, '')) for c in cols)
        values.append(row_vals)

    if dry_run:
        print(f"  [DRY RUN] {csv_path.name}: would insert {len(values)} rows into {table}")
        return len(values)

    conn.executemany(sql, values)
    print(f"  {csv_path.name}: inserted {len(values)} rows into {table}")
    return len(values)


def apply_event(event_id: int, dry_run: bool):
    parse_dir = Path(__file__).parent.parent / "data" / "parse_output" / str(event_id)

    # Verify all CSVs exist
    files = {
        'results':             parse_dir / 'results.csv',
        'time_classification': parse_dir / 'time_classification.csv',
        'skater_entries':      parse_dir / 'skater_entries.csv',
        'classification':      [parse_dir / 'classification_overall.csv',
                                parse_dir / 'classification_distance.csv'],
    }
    for table, paths in files.items():
        for p in ([paths] if isinstance(paths, Path) else paths):
            if not p.exists():
                raise FileNotFoundError(f"Missing: {p}")

    conn = sqlite3.connect(str(SQLITE_PATH))
    conn.execute("PRAGMA foreign_keys = OFF")  # allow delete without FK cascade issues

    # Show current counts
    for tbl in ['results', 'classification', 'time_classification', 'skater_entries']:
        count = conn.execute(
            f"SELECT COUNT(*) FROM {tbl} WHERE event_id=?", (event_id,)
        ).fetchone()[0]
        print(f"  Current {tbl} rows for event {event_id}: {count}")

    print()

    if dry_run:
        print(f"[DRY RUN] Would delete all rows for event {event_id} from 4 tables")
    else:
        for tbl in ['results', 'classification', 'time_classification', 'skater_entries']:
            deleted = conn.execute(
                f"DELETE FROM {tbl} WHERE event_id=?", (event_id,)
            ).rowcount
            print(f"  Deleted {deleted} rows from {tbl}")

    print()

    total = 0
    # Insert in FK-safe order
    total += insert_csv(conn, files['results'], 'results', event_id, dry_run)
    total += insert_csv(conn, files['time_classification'], 'time_classification', event_id, dry_run)
    total += insert_csv(conn, files['skater_entries'], 'skater_entries', event_id, dry_run)
    for p in files['classification']:
        total += insert_csv(conn, p, 'classification', event_id, dry_run)

    if not dry_run:
        conn.commit()
        print(f"\n  Committed. Total rows inserted: {total}")

        # Verify
        print()
        for tbl in ['results', 'classification', 'time_classification', 'skater_entries']:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE event_id=?", (event_id,)
            ).fetchone()[0]
            print(f"  New {tbl} rows for event {event_id}: {count}")

        # Show ID ranges assigned
        print()
        for tbl in ['results', 'classification', 'time_classification', 'skater_entries']:
            row = conn.execute(
                f"SELECT MIN(id), MAX(id) FROM {tbl} WHERE event_id=?", (event_id,)
            ).fetchone()
            print(f"  {tbl} IDs: {row[0]} – {row[1]}")
    else:
        print(f"\n[DRY RUN] Total rows that would be inserted: {total}")

    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--event-id", type=int, required=True)
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would happen without modifying DB")
    args = p.parse_args()

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Applying parse output for event {args.event_id}")
    print()
    apply_event(args.event_id, args.dry_run)
