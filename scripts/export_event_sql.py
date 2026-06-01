#!/usr/bin/env python3
"""Export event data from local SQLite as chunked PostgreSQL UPSERT SQL.

Usage:
    python3 scripts/export_event_sql.py --event-id 201 202
    python3 scripts/export_event_sql.py --all-new
    python3 scripts/export_event_sql.py --all-new --upsert
    python3 scripts/export_event_sql.py --mark-synced 201 202

By default uses ON CONFLICT (id) DO NOTHING (safe for new events).
Use --upsert to overwrite existing rows (for bulk data fixes).

Output goes to stdout. Each chunk is separated by -- CHUNK_BREAK so Claude
can split and execute them one-by-one via mcp__supabase__execute_sql.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SQLITE_PATH = Path(__file__).parent.parent / "data" / "skater_tracker_round2.db"
SYNC_LOG = Path(__file__).parent.parent / ".supabase_sync_log.json"
CHUNK_SIZE = 100  # rows per execute_sql call

CHILD_TABLES = ["results", "skater_entries", "classification", "time_classification"]


# ---------------------------------------------------------------------------
# Sync log
# ---------------------------------------------------------------------------

def load_sync_log() -> dict:
    if SYNC_LOG.exists():
        return json.loads(SYNC_LOG.read_text())
    return {"synced_event_ids": [], "last_sync": None}


def save_sync_log(log: dict):
    SYNC_LOG.write_text(json.dumps(log, indent=2))


# ---------------------------------------------------------------------------
# Value quoting
# ---------------------------------------------------------------------------

def quote_value(col: str, value) -> str:
    if value is None:
        return "NULL"
    if col == "is_relay":
        return "TRUE" if value else "FALSE"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, float):
        return repr(value)
    return str(value)


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------

def rows_to_statements(table: str, cols: list, rows: list, upsert: bool = False) -> list:
    col_list = ", ".join(cols)
    if upsert:
        set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != "id")
        conflict = f"ON CONFLICT (id) DO UPDATE SET {set_clause}"
    else:
        conflict = "ON CONFLICT (id) DO NOTHING"
    stmts = []
    for row in rows:
        vals = ", ".join(quote_value(col, v) for col, v in zip(cols, row))
        stmts.append(f"INSERT INTO {table} ({col_list}) VALUES ({vals}) {conflict};")
    return stmts


def collect_referenced_ids(cur, event_ids: list) -> tuple:
    ph = ",".join("?" * len(event_ids))
    club_ids, skater_ids = set(), set()
    for tbl in CHILD_TABLES:
        cur.execute(f"SELECT DISTINCT club_id FROM {tbl} WHERE event_id IN ({ph}) AND club_id IS NOT NULL", event_ids)
        club_ids.update(r[0] for r in cur.fetchall())
        cur.execute(f"SELECT DISTINCT skater_id FROM {tbl} WHERE event_id IN ({ph}) AND skater_id IS NOT NULL", event_ids)
        skater_ids.update(r[0] for r in cur.fetchall())
    return club_ids, skater_ids


def generate_statements(cur, event_ids: list, upsert: bool = False) -> list:
    ph = ",".join("?" * len(event_ids))
    club_ids, skater_ids = collect_referenced_ids(cur, event_ids)
    stmts = []

    if club_ids:
        cph = ",".join("?" * len(club_ids))
        cur.execute(f"SELECT * FROM clubs WHERE id IN ({cph})", list(club_ids))
        cols = [d[0] for d in cur.description]
        stmts += rows_to_statements("clubs", cols, cur.fetchall(), upsert)

    if skater_ids:
        sph = ",".join("?" * len(skater_ids))
        cur.execute(f"SELECT * FROM skaters WHERE id IN ({sph})", list(skater_ids))
        cols = [d[0] for d in cur.description]
        stmts += rows_to_statements("skaters", cols, cur.fetchall(), upsert)

    cur.execute(f"SELECT * FROM events WHERE id IN ({ph})", event_ids)
    cols = [d[0] for d in cur.description]
    stmts += rows_to_statements("events", cols, cur.fetchall(), upsert)

    for tbl in CHILD_TABLES:
        cur.execute(f"SELECT * FROM {tbl} WHERE event_id IN ({ph})", event_ids)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if rows:
            stmts += rows_to_statements(tbl, cols, rows, upsert)

    return stmts


def emit_chunks(stmts: list) -> list:
    chunks = []
    for i in range(0, len(stmts), CHUNK_SIZE):
        chunks.append("\n".join(stmts[i:i + CHUNK_SIZE]))
    return chunks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--event-id", type=int, nargs="+", metavar="ID", help="Specific event IDs to export")
    g.add_argument("--all-new", action="store_true", help="Export all events not yet in sync log")
    g.add_argument("--mark-synced", type=int, nargs="+", metavar="ID", help="Mark event IDs as synced (no SQL output)")
    p.add_argument("--upsert", action="store_true", help="Use DO UPDATE SET instead of DO NOTHING (for bulk fixes)")
    return p.parse_args()


def main():
    args = parse_args()

    if args.mark_synced:
        log = load_sync_log()
        existing = set(log["synced_event_ids"])
        existing.update(args.mark_synced)
        log["synced_event_ids"] = sorted(existing)
        log["last_sync"] = datetime.now(timezone.utc).isoformat()
        save_sync_log(log)
        print(f"Marked {len(args.mark_synced)} event(s) as synced.", file=sys.stderr)
        return

    conn = sqlite3.connect(str(SQLITE_PATH))
    cur = conn.cursor()

    if args.event_id:
        event_ids = args.event_id
    else:
        log = load_sync_log()
        synced = set(log["synced_event_ids"])
        cur.execute("SELECT id FROM events ORDER BY id")
        event_ids = [r[0] for r in cur.fetchall() if r[0] not in synced]

    if not event_ids:
        print("# No new events to sync", file=sys.stderr)
        conn.close()
        return

    stmts = generate_statements(cur, event_ids, upsert=args.upsert)
    conn.close()

    chunks = emit_chunks(stmts)
    print(f"-- SYNC: event_ids={event_ids}")
    print(f"-- CHUNKS: {len(chunks)}")
    for i, chunk in enumerate(chunks, 1):
        print(f"-- CHUNK {i}/{len(chunks)}")
        print(chunk)
        if i < len(chunks):
            print("-- CHUNK_BREAK")


if __name__ == "__main__":
    main()
