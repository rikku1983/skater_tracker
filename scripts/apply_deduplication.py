"""
Apply the manually-curated deduplication from skaters_fully_deduplicated.csv.

For each skater row, the CSV has three new columns:
  canonical_name   — corrected full name
  canonical_gender — corrected gender ('M'/'F')
  canonical_club   — club abbreviation to look up from the clubs table

Steps:
  1. Find duplicate groups (rows that share the same normalized canonical_name).
     Within each group, keep the "cleanest" record (name already matches canonical,
     then lowest id). Re-point Results from duplicates to canonical; delete duplicates.
  2. Update remaining skaters: full_name, first_name, last_name, normalized_name,
     gender, club_id.

Usage:
    python scripts/apply_deduplication.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Club, Skater, Result

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CSV_PATH = Path("data/skaters_fully_deduplicated.csv")


def _normalize(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _split_name(full: str) -> tuple[str | None, str | None]:
    parts = full.strip().split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    if parts:
        return None, parts[0]
    return None, None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not CSV_PATH.exists():
        logger.error("CSV not found: %s", CSV_PATH)
        sys.exit(1)

    init_db()
    db = get_session()

    # Build club abbreviation → id mapping
    abbrev_to_club_id: dict[str, int] = {
        c.abbreviation: c.id for c in db.query(Club).all()
    }

    # Load CSV
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8")))
    logger.info("Loaded %d rows from CSV", len(rows))

    # Index by id
    csv_by_id: dict[int, dict] = {int(r["id"]): r for r in rows}

    # Build duplicate groups keyed by normalized canonical_name
    groups: dict[str, list[int]] = defaultdict(list)
    for sid, r in csv_by_id.items():
        norm = _normalize(r["canonical_name"])
        groups[norm].append(sid)

    dup_groups = {norm: ids for norm, ids in groups.items() if len(ids) > 1}
    logger.info("Duplicate groups to merge: %d", len(dup_groups))

    stats = {
        "merged": 0,
        "deleted": 0,
        "results_repointed": 0,
        "results_dropped": 0,
        "names_updated": 0,
        "gender_updated": 0,
        "club_updated": 0,
    }

    # Phase 1: resolve duplicates
    merged_into: dict[int, int] = {}  # dup_id → canonical_id

    for norm, ids in dup_groups.items():
        # Prefer the record whose current full_name already matches canonical_name,
        # then lowest id.
        def sort_key(sid: int) -> tuple:
            r = csv_by_id[sid]
            already_clean = (r["full_name"].strip() == r["canonical_name"].strip())
            return (0 if already_clean else 1, sid)

        ids_sorted = sorted(ids, key=sort_key)
        canonical_id = ids_sorted[0]
        for dup_id in ids_sorted[1:]:
            merged_into[dup_id] = canonical_id

    if not args.dry_run:
        for dup_id, canon_id in merged_into.items():
            results = db.query(Result).filter(Result.skater_id == dup_id).all()
            for result in results:
                existing = db.query(Result).filter(
                    Result.race_id == result.race_id,
                    Result.skater_id == canon_id,
                ).first()
                if existing:
                    db.delete(result)
                    stats["results_dropped"] += 1
                else:
                    result.skater_id = canon_id
                    stats["results_repointed"] += 1
            db.flush()
            stats["merged"] += 1

        for dup_id in merged_into:
            s = db.query(Skater).filter(Skater.id == dup_id).first()
            if s:
                db.delete(s)
                stats["deleted"] += 1
        db.flush()
    else:
        stats["merged"] = len(merged_into)
        stats["deleted"] = len(merged_into)

    # Phase 2: update surviving skaters
    surviving_ids = set(csv_by_id.keys()) - set(merged_into.keys())

    for sid in surviving_ids:
        r = csv_by_id[sid]
        canonical_name = r["canonical_name"].strip()
        canonical_gender = r["canonical_gender"].strip() or None
        canonical_club_abbrev = r["canonical_club"].strip() or None

        if not canonical_name:
            continue

        new_norm = _normalize(canonical_name)
        new_club_id = abbrev_to_club_id.get(canonical_club_abbrev) if canonical_club_abbrev else None

        if args.dry_run:
            if canonical_name != r["full_name"].strip():
                stats["names_updated"] += 1
            if canonical_gender and canonical_gender != r["gender"].strip():
                stats["gender_updated"] += 1
            current_abbrev = r["club_abbreviation"].strip()
            if canonical_club_abbrev and canonical_club_abbrev != current_abbrev:
                stats["club_updated"] += 1
            continue

        s = db.query(Skater).filter(Skater.id == sid).first()
        if s is None:
            continue

        if s.full_name != canonical_name or s.normalized_name != new_norm:
            first, last = _split_name(canonical_name)
            s.full_name = canonical_name
            s.first_name = first
            s.last_name = last
            s.normalized_name = new_norm
            stats["names_updated"] += 1

        if canonical_gender and s.gender != canonical_gender:
            s.gender = canonical_gender
            stats["gender_updated"] += 1

        if new_club_id is not None and s.club_id != new_club_id:
            s.club_id = new_club_id
            stats["club_updated"] += 1

    if not args.dry_run:
        db.commit()
    db.close()

    print("\n=== Deduplication Summary ===")
    print(f"  Duplicate groups:      {len(dup_groups)}")
    print(f"  Skaters merged:        {stats['merged']}")
    print(f"  Skaters deleted:       {stats['deleted']}")
    print(f"  Results re-pointed:    {stats['results_repointed']}")
    print(f"  Results dropped:       {stats['results_dropped']}")
    print(f"  Names updated:         {stats['names_updated']}")
    print(f"  Gender updated:        {stats['gender_updated']}")
    print(f"  Club updated:          {stats['club_updated']}")
    if args.dry_run:
        print("  (dry run — no changes written)")


if __name__ == "__main__":
    main()
