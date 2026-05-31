"""
One-shot database integrity check before Supabase migration.
Usage: source .venv/bin/activate && python3 scripts/db_integrity_check.py
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from src.db.session_v2 import get_session
from sqlalchemy import text

db = get_session()

PASS  = "[PASS] "
WARN  = "[WARN] "
ERROR = "[ERROR]"
INFO  = "[INFO] "

issues = 0

def report(level: str, msg: str, rows: list = None):
    global issues
    print(f"{level} {msg}")
    if rows:
        for r in rows[:10]:
            print(f"         {r}")
        if len(rows) > 10:
            print(f"         ... and {len(rows)-10} more")
    if level in (WARN, ERROR):
        issues += 1

# ---------------------------------------------------------------------------
# 1. Referential integrity
# ---------------------------------------------------------------------------
print("\n=== 1. Referential Integrity ===")
checks = [
    ("results",             "event_id",  "events",  "id"),
    ("results",             "skater_id", "skaters", "id"),
    ("results",             "club_id",   "clubs",   "id"),
    ("classification",      "event_id",  "events",  "id"),
    ("classification",      "skater_id", "skaters", "id"),
    ("classification",      "club_id",   "clubs",   "id"),
    ("skater_entries",      "event_id",  "events",  "id"),
    ("skater_entries",      "skater_id", "skaters", "id"),
    ("skater_entries",      "club_id",   "clubs",   "id"),
    ("time_classification", "event_id",  "events",  "id"),
    ("time_classification", "skater_id", "skaters", "id"),
    ("time_classification", "club_id",   "clubs",   "id"),
]
for tbl, col, ref_tbl, ref_col in checks:
    rows = db.execute(text(f"""
        SELECT DISTINCT t.{col} FROM {tbl} t
        WHERE t.{col} IS NOT NULL
          AND t.{col} NOT IN (SELECT {ref_col} FROM {ref_tbl})
        LIMIT 20
    """)).fetchall()
    if rows:
        report(ERROR, f"Orphan {tbl}.{col} → {ref_tbl}: {len(rows)} bad values", rows)
    else:
        report(PASS,  f"Referential integrity — {tbl}.{col}: 0 orphans")

# ---------------------------------------------------------------------------
# 2. Time validity
# ---------------------------------------------------------------------------
print("\n=== 2. Time Validity ===")
WR_FLOORS = {500: 39.0, 777: 58.0, 1000: 80.0, 1500: 125.0}
for dist, floor in WR_FLOORS.items():
    rows = db.execute(text("""
        SELECT r.id, e.event_name, r.skater_name, r.distance_m, r.time_seconds
        FROM results r JOIN events e ON e.id=r.event_id
        WHERE r.distance_m=:d AND r.time_seconds IS NOT NULL AND r.time_seconds < :floor
        ORDER BY r.time_seconds
    """), {"d": dist, "floor": floor}).fetchall()
    if rows:
        report(WARN, f"TIME_IMPOSSIBLE {dist}m < {floor}s: {len(rows)} rows", rows)
    else:
        report(PASS, f"TIME_IMPOSSIBLE {dist}m: 0 rows")

# Very slow times
slow = db.execute(text("""
    SELECT r.id, e.event_name, r.skater_name, r.distance_m, r.time_seconds
    FROM results r JOIN events e ON e.id=r.event_id
    WHERE r.time_seconds > 600 AND r.is_relay=0
    ORDER BY r.time_seconds DESC
""")).fetchall()
if slow:
    report(WARN, f"TIME_VERY_SLOW (>600s, non-relay): {len(slow)} rows", slow)
else:
    report(PASS, "TIME_VERY_SLOW: 0 rows")

# time_text set but time_seconds NULL
parse_fail = db.execute(text("""
    SELECT r.id, e.event_name, r.skater_name, r.time_text
    FROM results r JOIN events e ON e.id=r.event_id
    WHERE r.time_text IS NOT NULL AND r.time_text != ''
      AND r.time_seconds IS NULL
      AND r.status IS NULL
    LIMIT 20
""")).fetchall()
if parse_fail:
    report(WARN, f"time_text set but time_seconds NULL (no status): {len(parse_fail)} rows", parse_fail)
else:
    report(PASS, "time_text/time_seconds consistency: OK")

# ---------------------------------------------------------------------------
# 3. Distance completeness
# ---------------------------------------------------------------------------
print("\n=== 3. Distance Completeness ===")
KNOWN_DISTS = {
    85,107,111,114,170,214,222,255,321,333,340,383,400,428,
    444,500,611,700,777,1000,1500,2000,3000,5000,10000,
    # 85m track
    85,170,255,340,
}
null_dist = db.execute(text("SELECT COUNT(*) FROM results WHERE distance_m IS NULL")).scalar()
total_res = db.execute(text("SELECT COUNT(*) FROM results")).scalar()
if null_dist:
    report(WARN, f"results with distance_m NULL: {null_dist:,} / {total_res:,} ({100*null_dist/total_res:.1f}%)")
else:
    report(PASS, "results distance_m NULL: 0")

unknown_dist = db.execute(text(f"""
    SELECT distance_m, COUNT(*) as cnt
    FROM results
    WHERE distance_m IS NOT NULL
      AND distance_m NOT IN ({','.join(str(d) for d in KNOWN_DISTS)})
    GROUP BY distance_m ORDER BY cnt DESC
""")).fetchall()
if unknown_dist:
    report(INFO, f"results with non-standard distance_m: {len(unknown_dist)} values", unknown_dist)

# ---------------------------------------------------------------------------
# 4. Division / round completeness
# ---------------------------------------------------------------------------
print("\n=== 4. Division & Round Completeness ===")
null_div = db.execute(text("""
    SELECT COUNT(*) FROM results WHERE division IS NULL OR division=''
""")).scalar()
if null_div:
    rows = db.execute(text("""
        SELECT r.id, e.event_name, r.skater_name, r.round_type, r.distance_m
        FROM results r JOIN events e ON e.id=r.event_id
        WHERE r.division IS NULL OR r.division='' LIMIT 20
    """)).fetchall()
    report(WARN, f"results with NULL/empty division: {null_div:,}", rows)
else:
    report(PASS, "results division NULL: 0")

null_round = db.execute(text("""
    SELECT COUNT(*) FROM results WHERE round_type IS NULL OR round_type=''
""")).scalar()
if null_round:
    report(INFO, f"results with NULL round_type: {null_round:,} (may be classification-only events)")
else:
    report(PASS, "results round_type NULL: 0")

# ---------------------------------------------------------------------------
# 5. Linkage rates
# ---------------------------------------------------------------------------
print("\n=== 5. Linkage Rates ===")
for tbl in ("results", "skater_entries", "classification", "time_classification"):
    total = db.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
    linked_s = db.execute(text(f"SELECT COUNT(*) FROM {tbl} WHERE skater_id IS NOT NULL")).scalar()
    linked_c = db.execute(text(f"SELECT COUNT(*) FROM {tbl} WHERE club_id IS NOT NULL")).scalar()
    pct_s = 100*linked_s/total if total else 0
    pct_c = 100*linked_c/total if total else 0
    level_s = PASS if pct_s >= 97 else (WARN if pct_s >= 90 else ERROR)
    level_c = PASS if pct_c >= 90 else INFO
    report(level_s, f"{tbl:<25} skater_id: {linked_s:,}/{total:,} ({pct_s:.1f}%)")
    report(level_c, f"{tbl:<25} club_id:   {linked_c:,}/{total:,} ({pct_c:.1f}%)")

events_no_results = db.execute(text("""
    SELECT e.id, e.event_name FROM events e
    WHERE e.parsed_at IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM results r WHERE r.event_id=e.id)
    ORDER BY e.id
""")).fetchall()
if events_no_results:
    report(INFO, f"Parsed events with 0 result rows: {len(events_no_results)}", events_no_results)
else:
    report(PASS, "All parsed events have result rows")

# ---------------------------------------------------------------------------
# 6. Duplicate rows
# ---------------------------------------------------------------------------
print("\n=== 6. Duplicate Rows ===")
dups = db.execute(text("""
    SELECT event_id, bib, race_number, heat, distance_m, COUNT(*) as cnt
    FROM results
    WHERE bib IS NOT NULL AND race_number IS NOT NULL AND distance_m IS NOT NULL
      AND status NOT IN ('PEN','DQ','DNF','DNS','DQS')
    GROUP BY event_id, bib, race_number, heat, distance_m
    HAVING cnt > 1
    ORDER BY cnt DESC
    LIMIT 20
""")).fetchall()
if dups:
    report(WARN, f"Duplicate (event,bib,race,heat,dist) in results: {len(dups)} groups", dups)
else:
    report(PASS, "Duplicate result rows: 0")

# ---------------------------------------------------------------------------
# 7. Skater quality
# ---------------------------------------------------------------------------
print("\n=== 7. Skater Quality ===")
digit_names = db.execute(text("""
    SELECT id, full_name FROM skaters
    WHERE full_name GLOB '*[0-9]*' OR full_name NOT LIKE '% %'
    ORDER BY full_name
""")).fetchall()
if digit_names:
    report(WARN, f"Skaters with digits or single-token name: {len(digit_names)}", digit_names)
else:
    report(PASS, "Skater names: no digits/single-token")

no_gender = db.execute(text("""
    SELECT s.id, s.full_name,
           (SELECT COUNT(*) FROM results WHERE skater_id=s.id) as n_results
    FROM skaters s
    WHERE s.gender IS NULL
      AND (SELECT COUNT(*) FROM results WHERE skater_id=s.id) > 5
    ORDER BY n_results DESC
    LIMIT 20
""")).fetchall()
if no_gender:
    total_no_gender = db.execute(text("""
        SELECT COUNT(*) FROM skaters s WHERE s.gender IS NULL
          AND (SELECT COUNT(*) FROM results WHERE skater_id=s.id) > 5
    """)).scalar()
    report(INFO, f"Active skaters (>5 results) with gender NULL: {total_no_gender}", no_gender)
else:
    report(PASS, "Active skater gender coverage: OK")

no_by = db.execute(text("""
    SELECT COUNT(*) FROM skaters WHERE birth_year IS NULL
      AND (SELECT COUNT(*) FROM results WHERE skater_id=skaters.id) > 5
""")).scalar()
report(INFO, f"Active skaters (>5 results) with birth_year NULL: {no_by}")

# ---------------------------------------------------------------------------
# 8. Track type coverage
# ---------------------------------------------------------------------------
print("\n=== 8. Track Type Coverage ===")
track_counts = db.execute(text("""
    SELECT COALESCE(track_type,'NULL') as tt, COUNT(*) as cnt
    FROM events GROUP BY tt ORDER BY cnt DESC
""")).fetchall()
for tt, cnt in track_counts:
    report(INFO, f"track_type={tt}: {cnt} events")

unknowns = db.execute(text("""
    SELECT id, event_name, season FROM events
    WHERE track_type IS NULL OR track_type='unknown'
    ORDER BY season, id
""")).fetchall()
if unknowns:
    report(INFO, f"Events with unknown/NULL track_type: {len(unknowns)}", unknowns)

# ---------------------------------------------------------------------------
# 9. Club quality
# ---------------------------------------------------------------------------
print("\n=== 9. Club Quality ===")
null_clubs = db.execute(text("""
    SELECT COUNT(*) FROM clubs WHERE canonical_name IS NULL OR canonical_name=''
""")).scalar()
if null_clubs:
    report(ERROR, f"Clubs with NULL/empty canonical_name: {null_clubs}")
else:
    report(PASS, "Club canonical_name: all present")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*60}")
print(f"SUMMARY: {issues} issue group(s) flagged (WARN or ERROR)")
if issues == 0:
    print("Database looks migration-ready.")
else:
    print("Review WARN/ERROR items above before migrating.")

db.close()
