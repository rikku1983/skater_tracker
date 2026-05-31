#!/usr/bin/env python3
"""Migrate data from local SQLite to Supabase PostgreSQL.

Usage:
    python scripts/migrate_to_supabase.py

Reads DATABASE_URL from environment or .env file.
"""

import os
import sqlite3
import psycopg2
import psycopg2.extras
from pathlib import Path

SQLITE_PATH = Path(__file__).parent.parent / "data" / "skater_tracker_round2.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not DATABASE_URL:
    env_file = Path(__file__).parent.parent / ".env"
    for line in env_file.read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            DATABASE_URL = line.split("=", 1)[1].strip().strip('"\'')
            break

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL not set. Add it to .env or export it.")

# ---------------------------------------------------------------------------
# Schema — mirrors SQLite exactly but with PostgreSQL types
# ---------------------------------------------------------------------------
SCHEMA = """
DROP TABLE IF EXISTS time_classification CASCADE;
DROP TABLE IF EXISTS classification CASCADE;
DROP TABLE IF EXISTS skater_entries CASCADE;
DROP TABLE IF EXISTS results CASCADE;
DROP TABLE IF EXISTS events CASCADE;
DROP TABLE IF EXISTS skaters CASCADE;
DROP TABLE IF EXISTS clubs CASCADE;

CREATE TABLE clubs (
    id          INTEGER PRIMARY KEY,
    canonical_name TEXT,
    abbreviation TEXT,
    full_club_name TEXT,
    aliases     TEXT,
    occurrence  INTEGER,
    city        TEXT,
    state_province TEXT,
    country     TEXT,
    location_note TEXT,
    duplicate_note TEXT
);

CREATE TABLE skaters (
    id              INTEGER PRIMARY KEY,
    full_name       TEXT,
    first_name      TEXT,
    last_name       TEXT,
    normalized_name TEXT,
    gender          TEXT,
    birth_year      INTEGER,
    birth_year_min  INTEGER,
    birth_year_max  INTEGER,
    known_aliases   TEXT,
    source          TEXT
);

CREATE TABLE events (
    id          INTEGER PRIMARY KEY,
    season      TEXT,
    event_name  TEXT,
    event_date  TEXT,
    end_date    TEXT,
    series      TEXT,
    event_group TEXT,
    track_type  TEXT,
    pdf_format  TEXT,
    local_path  TEXT,
    venue       TEXT,
    city        TEXT,
    state       TEXT,
    parsed_at   TEXT,
    parse_errors TEXT
);

CREATE TABLE results (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER REFERENCES events(id),
    division    TEXT,
    distance_m  INTEGER,
    laps        DOUBLE PRECISION,
    round_type  TEXT,
    round_label TEXT,
    race_number INTEGER,
    heat        INTEGER,
    group_label TEXT,
    rank        INTEGER,
    bib         TEXT,
    skater_name TEXT,
    affiliation TEXT,
    time_text   TEXT,
    time_seconds DOUBLE PRECISION,
    status      TEXT,
    points      DOUBLE PRECISION,
    page_number INTEGER,
    parse_note  TEXT,
    is_relay    BOOLEAN,
    club_id     INTEGER REFERENCES clubs(id),
    skater_id   INTEGER REFERENCES skaters(id)
);

CREATE TABLE skater_entries (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER REFERENCES events(id),
    bib         TEXT,
    skater_name TEXT,
    status      TEXT,
    gender      TEXT,
    division    TEXT,
    club        TEXT,
    page_number INTEGER,
    club_id     INTEGER REFERENCES clubs(id),
    birth_year_1 INTEGER,
    birth_year_2 INTEGER,
    skater_id   INTEGER REFERENCES skaters(id)
);

CREATE TABLE classification (
    id              INTEGER PRIMARY KEY,
    event_id        INTEGER REFERENCES events(id),
    section_type    TEXT,
    division        TEXT,
    distance_m      INTEGER,
    rank            INTEGER,
    bib             TEXT,
    skater_name     TEXT,
    affiliation     TEXT,
    points          DOUBLE PRECISION,
    skater_status   TEXT,
    cdr             INTEGER,
    bdr             INTEGER,
    semi_group      TEXT,
    final_group     TEXT,
    best_time_text  TEXT,
    best_time_seconds DOUBLE PRECISION,
    page_number     INTEGER,
    club_id         INTEGER REFERENCES clubs(id),
    skater_id       INTEGER REFERENCES skaters(id)
);

CREATE TABLE time_classification (
    id              INTEGER PRIMARY KEY,
    event_id        INTEGER REFERENCES events(id),
    division        TEXT,
    distance_m      INTEGER,
    rank            INTEGER,
    bib             TEXT,
    skater_name     TEXT,
    best_time_text  TEXT,
    best_time_seconds DOUBLE PRECISION,
    page_number     INTEGER,
    affiliation     TEXT,
    club_id         INTEGER REFERENCES clubs(id),
    skater_id       INTEGER REFERENCES skaters(id)
);

CREATE INDEX IF NOT EXISTS idx_results_skater    ON results(skater_id);
CREATE INDEX IF NOT EXISTS idx_results_event     ON results(event_id);
CREATE INDEX IF NOT EXISTS idx_results_club      ON results(club_id);
CREATE INDEX IF NOT EXISTS idx_results_dist      ON results(distance_m);
CREATE INDEX IF NOT EXISTS idx_class_event       ON classification(event_id);
CREATE INDEX IF NOT EXISTS idx_class_skater      ON classification(skater_id);
CREATE INDEX IF NOT EXISTS idx_timeclass_event   ON time_classification(event_id);
CREATE INDEX IF NOT EXISTS idx_skater_entries_ev ON skater_entries(event_id);
"""

TABLES_ORDER = ["clubs", "skaters", "events", "results",
                "skater_entries", "classification", "time_classification"]

BATCH = 2000


def migrate_table(sqlite_cur, pg_cur, table: str):
    sqlite_cur.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in sqlite_cur.description]
    col_list = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

    total = 0
    while True:
        rows = sqlite_cur.fetchmany(BATCH)
        if not rows:
            break
        # Convert SQLite booleans (0/1) to Python bool for is_relay column
        if "is_relay" in cols:
            idx = cols.index("is_relay")
            rows = [tuple(bool(v) if i == idx and v is not None else v
                          for i, v in enumerate(row))
                    for row in rows]
        psycopg2.extras.execute_batch(pg_cur, insert_sql, rows)
        total += len(rows)
        print(f"  {table}: {total:,} rows inserted", end="\r")
    print(f"  {table}: {total:,} rows ✓                ")
    return total


def main():
    print(f"Connecting to SQLite: {SQLITE_PATH}")
    sqlite_conn = sqlite3.connect(str(SQLITE_PATH))
    sqlite_conn.row_factory = None

    print(f"Connecting to Supabase PostgreSQL...")
    # Ensure sslmode is set for Supabase
    pg_url = DATABASE_URL
    if "sslmode" not in pg_url:
        pg_url += "?sslmode=require"
    pg_conn = psycopg2.connect(pg_url)
    pg_conn.autocommit = False
    pg_cur = pg_conn.cursor()

    print("Creating schema...")
    pg_cur.execute(SCHEMA)
    pg_conn.commit()
    print("Schema created.")

    sqlite_cur = sqlite_conn.cursor()
    for table in TABLES_ORDER:
        migrate_table(sqlite_cur, pg_cur, table)
        pg_conn.commit()

    pg_cur.close()
    pg_conn.close()
    sqlite_conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
