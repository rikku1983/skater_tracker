"""
Enrich skater records using the USA Skating registry CSV (usa_skaters.csv).

For each DB skater that matches a CSV entry (by normalized "FirstName LastName"):
  - Sets gender if not already set
  - Corrects full_name / first_name / last_name to the canonical capitalisation
    from the registry (e.g. "aidan murphy" → "Aidan Murphy")

CSV format:  Name, Member, gender
  Name is "LASTNAME\xa0Firstname" (ALL-CAPS last, title-case first).
  Some entries have parenthetical preferred names: "ZHOU Zongxin (Renee)".

Matching strategy (direct only — no fuzzy):
  - Primary key:  normalize("Firstname Lastname")  ← matches most DB entries
  - Nickname key: normalize("Nickname Lastname")   ← catches preferred-name users

Usage:
    python scripts/enrich_from_registry.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Skater

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CSV_PATH = Path("data/usa_skaters.csv")


def _normalize(s: str) -> str:
    s = s.lower().replace("\xa0", " ").replace("\xc2\xa0", " ").strip()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _title_name(part: str) -> str:
    """Title-case a name segment, preserving hyphens: 'SANTOS-GRISWOLD' → 'Santos-Griswold'."""
    return "-".join(seg.capitalize() for seg in part.split("-"))


def _has_broken_caps(name: str) -> bool:
    """True if any token is 2+ letters and entirely uppercase — likely a parser artifact."""
    return any(len(tok) >= 2 and tok.isupper() for tok in name.split())


def load_registry(csv_path: Path) -> dict[str, dict]:
    """
    Returns a dict: normalized_name → {gender, canonical_first, canonical_last}.
    Both 'firstname lastname' and 'nickname lastname' keys are added.
    """
    df = pd.read_csv(csv_path)
    lookup: dict[str, dict] = {}

    for _, row in df.iterrows():
        raw = str(row["Name"]).replace("\xa0", " ").replace("\xc2\xa0", " ").strip()
        gender = str(row["gender"]).strip()

        # Extract parenthetical nickname
        nick_m = re.search(r"\(([^)]+)\)", raw)
        nickname = nick_m.group(1).strip() if nick_m else None
        raw_clean = re.sub(r"\s*\([^)]+\)", "", raw).strip()

        parts = raw_clean.split()
        if len(parts) < 2:
            continue

        last_raw = parts[0]                           # ALL-CAPS, may be hyphenated
        first = " ".join(_title_name(p) for p in parts[1:])  # title-case each token
        last = _title_name(last_raw)

        entry = {"gender": gender, "first": first, "last": last}

        # Primary key: "Firstname Lastname"
        key = _normalize(f"{first} {last}")
        lookup.setdefault(key, entry)   # first occurrence wins for duplicates

        # Nickname key: "Nickname Lastname" (e.g. "Renee Zhou")
        if nickname:
            nick_key = _normalize(f"{nickname} {last}")
            lookup.setdefault(nick_key, entry)

    return lookup


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not CSV_PATH.exists():
        logger.error("CSV not found: %s", CSV_PATH)
        sys.exit(1)

    registry = load_registry(CSV_PATH)
    logger.info("Registry loaded: %d entries", len(registry))

    init_db()
    db = get_session()

    skaters = db.query(Skater).all()
    logger.info("DB skaters: %d", len(skaters))

    stats = {
        "matched": 0,
        "gender_set": 0,
        "name_corrected": 0,
        "unmatched": 0,
    }

    for skater in skaters:
        entry = registry.get(skater.normalized_name)
        if entry is None:
            stats["unmatched"] += 1
            continue

        stats["matched"] += 1
        changed = False

        # 1. Gender
        if not skater.gender and entry["gender"] in ("M", "F"):
            if not args.dry_run:
                skater.gender = entry["gender"]
            stats["gender_set"] += 1
            changed = True

        # 2. Canonical name: only fix if the DB name has clearly broken capitalisation
        #    (a token that is all-uppercase, e.g. "Brian LI" or "MATTHEW LORENZ").
        #    Skip if the DB name looks fine — avoids regressions like O'Brien → Obrien
        #    or DeRosa → Derosa when the CSV uses simple title-casing.
        canonical_full = f"{entry['first']} {entry['last']}"
        canonical_first = entry["first"]
        canonical_last = entry["last"]

        if skater.full_name != canonical_full and _has_broken_caps(skater.full_name):
            if not args.dry_run:
                skater.full_name = canonical_full
                skater.first_name = canonical_first
                skater.last_name = canonical_last
            stats["name_corrected"] += 1
            changed = True

    if not args.dry_run:
        db.commit()
    db.close()

    total = len(skaters)
    print("\n=== Registry Enrichment Summary ===")
    print(f"  Registry entries:   {len(registry)}")
    print(f"  DB skaters:         {total}")
    print(f"  Matched:            {stats['matched']}  ({stats['matched']/total*100:.1f}%)")
    print(f"  Unmatched:          {stats['unmatched']}")
    print(f"  Gender set:         {stats['gender_set']}")
    print(f"  Names corrected:    {stats['name_corrected']}")
    if args.dry_run:
        print("  (dry run — no changes written)")


if __name__ == "__main__":
    main()
