"""
Apply confirmed skater merges from data/skater_candidates.csv.

Reads all rows where merge=Y (case-insensitive).
For each pair (id_a = canonical, id_b = duplicate):
  1. Re-point id_b's Results to id_a; drop any that would collide (same race_id).
  2. If id_a has an initial-only first name and id_b has the full name, update
     id_a's full_name, first_name, last_name, normalized_name to the fuller version.
  3. Copy club / gender from id_b to id_a where id_a's field is currently null.
  4. Delete id_b.

Handles chains: if id_b was already merged into id_c by a prior row in the same
run, re-points to the final canonical instead.

Usage:
    python scripts/merge_skater_pairs.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Result, Skater

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CSV_PATH = Path("data/skater_candidates.csv")
INITIAL_RE = re.compile(r"^[A-Z]\.\s")


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--csv", default=str(CSV_PATH), help="Path to reviewed CSV")
    args = parser.parse_args()

    with open(args.csv, newline="") as f:
        all_rows = list(csv.DictReader(f))

    confirmed = [r for r in all_rows if r.get("merge", "").strip().upper() == "Y"]
    logger.info("Confirmed merge pairs: %d / %d", len(confirmed), len(all_rows))
    if not confirmed:
        logger.info("Nothing to do.")
        return

    init_db()
    db = get_session()

    # canonical_of[id] → the current canonical id for that skater
    # (handles chains where B→A and C→B in the same run)
    canonical_of: dict[int, int] = {}

    def resolve(sid: int) -> int:
        while sid in canonical_of:
            sid = canonical_of[sid]
        return sid

    stats = {"merged": 0, "repointed": 0, "dropped": 0, "name_updated": 0}

    for row in confirmed:
        id_a = int(row["id_a"])
        id_b = int(row["id_b"])

        # Resolve chains
        id_a = resolve(id_a)
        id_b = resolve(id_b)

        if id_a == id_b:
            logger.debug("  Skip already-merged pair %d/%d", id_a, id_b)
            continue

        skater_a = db.query(Skater).filter(Skater.id == id_a).first()
        skater_b = db.query(Skater).filter(Skater.id == id_b).first()

        if not skater_a or not skater_b:
            logger.warning("  Skater not found: a=%d b=%d — skip", id_a, id_b)
            continue

        logger.info("  MERGE %d '%s' ← %d '%s'",
                    id_a, skater_a.full_name, id_b, skater_b.full_name)

        # --- Re-point Results from b → a ---
        b_results = db.query(Result).filter(Result.skater_id == id_b).all()
        for result in b_results:
            collision = db.query(Result).filter(
                Result.race_id == result.race_id,
                Result.skater_id == id_a,
            ).first()
            if collision:
                logger.debug("    drop dup result_id=%d race=%d", result.id, result.race_id)
                if not args.dry_run:
                    db.delete(result)
                stats["dropped"] += 1
            else:
                if not args.dry_run:
                    result.skater_id = id_a
                stats["repointed"] += 1

        if not args.dry_run:
            db.flush()

        # --- Update canonical name if id_a has initials but id_b has full name ---
        if INITIAL_RE.match(skater_a.full_name) and not INITIAL_RE.match(skater_b.full_name):
            logger.info("    name: '%s' → '%s'", skater_a.full_name, skater_b.full_name)
            if not args.dry_run:
                skater_a.full_name = skater_b.full_name
                fn, ln = _split_name(skater_b.full_name)
                skater_a.first_name = fn
                skater_a.last_name = ln
                skater_a.normalized_name = _normalize(skater_b.full_name)
            stats["name_updated"] += 1

        # --- Fill in null fields on id_a from id_b ---
        if not args.dry_run:
            if not skater_a.gender and skater_b.gender:
                skater_a.gender = skater_b.gender
            if not skater_a.club_name and skater_b.club_name:
                skater_a.club_name = skater_b.club_name
            if not skater_a.club_id and skater_b.club_id:
                skater_a.club_id = skater_b.club_id
            if not skater_a.birth_year and skater_b.birth_year:
                skater_a.birth_year = skater_b.birth_year

        # --- Delete id_b ---
        if not args.dry_run:
            db.delete(skater_b)
            db.flush()

        canonical_of[id_b] = id_a
        stats["merged"] += 1

    if not args.dry_run:
        db.commit()
        logger.info("Committed.")
    else:
        logger.info("(dry-run — no changes written)")

    logger.info("\n=== Summary ===")
    logger.info("  %-20s %d", "pairs merged", stats["merged"])
    logger.info("  %-20s %d", "results repointed", stats["repointed"])
    logger.info("  %-20s %d", "results dropped", stats["dropped"])
    logger.info("  %-20s %d", "names updated", stats["name_updated"])
    db.close()


if __name__ == "__main__":
    main()
