"""
Populate the clubs table and set club_id on results + skater_entries.

Usage:
    source .venv/bin/activate && python3 scripts/normalize_clubs.py [--dry-run] [--force]

--dry-run  : print what would be done, no DB writes
--force    : re-run even if clubs table already populated
"""
from __future__ import annotations
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session_v2 import init_db, get_session
from src.db.models_v2 import Club, Result, SkaterEntry

CLUBS_CSV   = Path(__file__).parents[1] / "data" / "clubs_table.csv"
MAPPING_CSV = Path(__file__).parents[1] / "data" / "club_mapping_final_imputed_v2.csv"


def load_mapping() -> dict[str, str | None]:
    """Return raw_value → canonical_name (None = junk/null)."""
    mapping = {}
    for row in csv.DictReader(open(MAPPING_CSV)):
        raw   = row["raw_value"].strip()
        canon = row["proposed_canonical"].strip()
        conf  = row["confidence"].strip()
        mapping[raw] = None if (not canon or conf == "null") else canon
    return mapping


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force",   action="store_true",
                        help="Re-run even if clubs table already has rows")
    args = parser.parse_args()

    init_db()
    db = get_session()

    # ── Check if already populated ─────────────────────────────────────────────
    existing = db.query(Club).count()
    if existing and not args.force:
        print(f"clubs table already has {existing} rows. Use --force to re-run.")
        db.close()
        return

    if args.force and existing:
        print(f"--force: clearing {existing} existing club rows and resetting club_id columns")
        if not args.dry_run:
            db.query(Club).delete()
            db.execute(__import__("sqlalchemy").text("UPDATE results SET club_id = NULL"))
            db.execute(__import__("sqlalchemy").text("UPDATE skater_entries SET club_id = NULL"))
            db.execute(__import__("sqlalchemy").text("UPDATE classification SET club_id = NULL"))
            db.execute(__import__("sqlalchemy").text("UPDATE time_classification SET club_id = NULL"))
            db.commit()

    # ── Load clubs_table.csv → populate clubs table ────────────────────────────
    club_rows = list(csv.DictReader(open(CLUBS_CSV)))
    canonical_to_id: dict[str, int] = {}

    print(f"Loading {len(club_rows)} clubs from {CLUBS_CSV.name}…")
    for row in club_rows:
        canon = row["canonical_name"].strip()
        if not canon:
            continue
        club = Club(
            canonical_name = canon,
            abbreviation   = row.get("abbreviation", "").strip() or None,
            full_club_name = row.get("full_club_name", "").strip() or None,
            aliases        = row.get("aliases", "").strip() or None,
            occurrence     = int(row.get("occurrence", 0) or 0),
            city           = row.get("city", "").strip() or None,
            state_province = row.get("state_province", "").strip() or None,
            country        = row.get("country", "").strip() or None,
            location_note  = row.get("location_note", "").strip() or None,
            duplicate_note = row.get("duplicate_note", "").strip() or None,
        )
        if not args.dry_run:
            db.add(club)
            db.flush()
            canonical_to_id[canon] = club.id
        else:
            canonical_to_id[canon] = -1   # placeholder for dry-run

    if not args.dry_run:
        db.commit()
        print(f"  ✓ inserted {len(canonical_to_id)} clubs")
    else:
        print(f"  [dry-run] would insert {len(canonical_to_id)} clubs")

    # ── Load raw→canonical mapping ─────────────────────────────────────────────
    raw_to_canonical = load_mapping()

    # Build raw → club_id
    raw_to_club_id: dict[str, int | None] = {}
    missing_canonicals: set[str] = set()
    for raw, canon in raw_to_canonical.items():
        if canon is None:
            raw_to_club_id[raw] = None
        elif canon in canonical_to_id:
            raw_to_club_id[raw] = canonical_to_id[canon]
        else:
            missing_canonicals.add(canon)
            raw_to_club_id[raw] = None

    if missing_canonicals:
        print(f"  ⚠ {len(missing_canonicals)} canonicals in mapping not found in clubs table:")
        for c in sorted(missing_canonicals):
            print(f"      {c}")

    # ── Update results.club_id ─────────────────────────────────────────────────
    print("Updating results.club_id…")
    result_updated = result_nulled = result_skipped = 0

    from sqlalchemy import text
    for raw, club_id in raw_to_club_id.items():
        if not raw:
            continue
        if club_id is not None:
            if not args.dry_run:
                db.execute(text(
                    "UPDATE results SET club_id = :cid WHERE affiliation = :raw AND club_id IS NULL"
                ), {"cid": club_id, "raw": raw})
            result_updated += 1
        else:
            result_nulled += 1

    # Raw values that appear in DB but not in mapping → leave club_id NULL
    if not args.dry_run:
        db.commit()

    print(f"  results: {result_updated} distinct raws mapped, {result_nulled} mapped to NULL")

    # ── Update skater_entries.club_id ──────────────────────────────────────────
    print("Updating skater_entries.club_id…")
    entry_updated = entry_nulled = 0

    for raw, club_id in raw_to_club_id.items():
        if not raw:
            continue
        if club_id is not None:
            if not args.dry_run:
                db.execute(text(
                    "UPDATE skater_entries SET club_id = :cid WHERE club = :raw AND club_id IS NULL"
                ), {"cid": club_id, "raw": raw})
            entry_updated += 1
        else:
            entry_nulled += 1

    if not args.dry_run:
        db.commit()

    print(f"  skater_entries: {entry_updated} distinct raws mapped, {entry_nulled} mapped to NULL")

    # ── Update classification.club_id ──────────────────────────────────────────
    print("Updating classification.club_id…")
    class_updated = class_nulled = 0

    for raw, club_id in raw_to_club_id.items():
        if not raw:
            continue
        if club_id is not None:
            if not args.dry_run:
                db.execute(text(
                    "UPDATE classification SET club_id = :cid WHERE affiliation = :raw AND club_id IS NULL"
                ), {"cid": club_id, "raw": raw})
            class_updated += 1
        else:
            class_nulled += 1

    if not args.dry_run:
        db.commit()

    print(f"  classification: {class_updated} distinct raws mapped, {class_nulled} mapped to NULL")

    # ── Summary ────────────────────────────────────────────────────────────────
    if not args.dry_run:
        r_matched = db.execute(text("SELECT COUNT(*) FROM results WHERE club_id IS NOT NULL")).scalar()
        r_total   = db.execute(text("SELECT COUNT(*) FROM results WHERE affiliation IS NOT NULL")).scalar()
        e_matched = db.execute(text("SELECT COUNT(*) FROM skater_entries WHERE club_id IS NOT NULL")).scalar()
        e_total   = db.execute(text("SELECT COUNT(*) FROM skater_entries WHERE club IS NOT NULL")).scalar()
        c_matched = db.execute(text("SELECT COUNT(*) FROM classification WHERE club_id IS NOT NULL")).scalar()
        c_total   = db.execute(text("SELECT COUNT(*) FROM classification WHERE affiliation IS NOT NULL")).scalar()
        print(f"\nResults        matched: {r_matched:,} / {r_total:,} ({100*r_matched//r_total}%)")
        print(f"Entries        matched: {e_matched:,} / {e_total:,} ({100*e_matched//e_total}%)")
        print(f"Classification matched: {c_matched:,} / {c_total:,} ({100*c_matched//c_total if c_total else 0}%)")

    db.close()
    print("\nDone." if not args.dry_run else "\n[dry-run complete — no changes written]")


if __name__ == "__main__":
    main()
