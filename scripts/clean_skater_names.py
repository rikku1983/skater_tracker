"""
Clean skater full_name values in the database.

Handles:
  * / ^       — gender markers (strip, set gender='F' for *)
  (F) / (M)   — inline gender markers (strip, set gender)
  trailing m/f— stray lowercase gender codes (strip, set gender)
  (50+ M) etc — Masters age-category suffixes (strip, set gender)
  (Y) (JA) () — division / misc parenthetical labels (strip)
  (Nickname)  — preferred name in parens (strip)
  JR A / JR B — division labels appended to name (strip)
  ALL-CAPS    — broken capitalisation from older parsers (title-case)
  comma       — "Last, First" legacy format (reverse)
  time in name— "ZHOU name USA-PSSP 0:56.423 8.7" junk (strip from time)

After cleaning, any two skater records that share a normalized name are
merged: Results are re-pointed to the lower-id record; the duplicate is
deleted.

Usage:
    python scripts/clean_skater_names.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Skater, Result

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_SUFFIXES_TO_KEEP = {"II", "III", "IV", "JR.", "SR."}


def _normalize(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def clean_name(raw: str) -> tuple[str, str | None]:
    """
    Returns (cleaned_name, gender_signal) where gender_signal is 'M', 'F', or None.
    """
    name = raw.strip()
    gender: str | None = None

    # 0. Strip time-pattern junk: "name USA-PSSP 0:56.423 8.7" → "name"
    name = re.sub(r"\s+\d+:\d+\.\d+.*$", "", name).strip()

    # 1. Asterisk = female marker
    if "*" in name:
        gender = "F"
        name = name.replace("*", "").strip()

    # 2. Caret = qualifier marker (strip only)
    name = name.replace("^", "").strip()

    # 3. Inline (F) / (M) gender markers — anywhere in the name
    m = re.search(r"\(\s*([MFmf])\s*\)", name)
    if m:
        g = m.group(1).upper()
        if gender is None:
            gender = g
        name = re.sub(r"\s*\(\s*[MFmf]\s*\)\s*", " ", name).strip()

    # 4. Masters age-category suffix: "(50+ M)", "(30+ F)" etc.
    m = re.search(r"\(\s*\d+\+\s*([MFmf])\s*\)\s*$", name)
    if m:
        g = m.group(1).upper()
        if gender is None:
            gender = g
        name = re.sub(r"\s*\(\s*\d+\+\s*[MFmf]\s*\)\s*$", "", name).strip()

    # 5. Strip all remaining parenthetical content (nicknames, division labels, etc.)
    name = re.sub(r"\s*\([^)]*\)\s*", " ", name).strip()

    # 6. Trailing stray gender code: single lowercase m/f at end
    m = re.match(r"^(.+?)\s+([mMfF])\s*$", name)
    if m:
        # Only treat as gender code if it's a single letter (not a surname)
        g = m.group(2).upper()
        if gender is None:
            gender = g
        name = m.group(1).strip()

    # 7. Strip "JR A" / "JR B" / "JR C" division labels at end
    name = re.sub(r"\s+JR\s+[A-Za-z]\s*$", "", name, flags=re.IGNORECASE).strip()

    # 8. Fix "Last, First" comma format → "First Last"
    if "," in name:
        last, _, first = name.partition(",")
        first, last = first.strip(), last.strip()
        if first:
            name = f"{first} {last}"

    # 9. Fix all-caps tokens (3+ alpha chars, all uppercase, not a kept suffix)
    tokens = name.split()
    fixed = []
    for tok in tokens:
        alpha = re.sub(r"[^a-zA-Z]", "", tok)
        if len(alpha) >= 3 and alpha.isupper() and tok not in _SUFFIXES_TO_KEEP:
            # title-case the token, preserving hyphens
            tok = "-".join(seg.capitalize() for seg in tok.split("-"))
        fixed.append(tok)
    name = " ".join(fixed)

    # 10. Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()

    return name, gender


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

    init_db()
    db = get_session()

    skaters = db.query(Skater).all()
    logger.info("Loaded %d skaters", len(skaters))

    # Phase 1: compute cleaned name + gender for every skater
    skater_data: dict[int, dict] = {}
    for s in skaters:
        cleaned, gender_signal = clean_name(s.full_name)
        skater_data[s.id] = {
            "skater": s,
            "cleaned": cleaned,
            "new_norm": _normalize(cleaned),
            "gender_signal": gender_signal,
        }

    # Phase 2: find collision groups (same new_norm → same person)
    norm_groups: dict[str, list[int]] = defaultdict(list)
    for sid, data in skater_data.items():
        norm_groups[data["new_norm"]].append(sid)

    collision_groups = {norm: ids for norm, ids in norm_groups.items() if len(ids) > 1}
    logger.info("Collision groups (duplicates to merge): %d", len(collision_groups))

    stats = {
        "names_cleaned": 0,
        "gender_set": 0,
        "merged_into": 0,
        "duplicates_deleted": 0,
        "results_repointed": 0,
        "results_dropped": 0,
    }

    # Phase 3: merge collision groups
    merged_into: dict[int, int] = {}  # duplicate_id → canonical_id

    for norm, ids in collision_groups.items():
        # Sort: prefer the already-clean record (no special chars in original), then by ID
        def sort_key(sid):
            orig = skater_data[sid]["skater"].full_name
            has_junk = any(c in orig for c in "*^()+") or re.search(r"\s[mMfF]$", orig) or re.search(r"\bJR\s+[A-Z]\b", orig)
            return (1 if has_junk else 0, sid)

        ids_sorted = sorted(ids, key=sort_key)
        canonical_id = ids_sorted[0]
        duplicate_ids = ids_sorted[1:]

        for dup_id in duplicate_ids:
            merged_into[dup_id] = canonical_id

    if not args.dry_run:
        # Re-point Results from duplicates to canonicals
        for dup_id, canon_id in merged_into.items():
            results = db.query(Result).filter(Result.skater_id == dup_id).all()
            for result in results:
                # Check if canonical already has a result for this race
                existing = db.query(Result).filter(
                    Result.race_id == result.race_id,
                    Result.skater_id == canon_id,
                ).first()
                if existing:
                    # Canonical already has this result — drop the duplicate
                    db.delete(result)
                    stats["results_dropped"] += 1
                else:
                    result.skater_id = canon_id
                    stats["results_repointed"] += 1
            db.flush()
            stats["merged_into"] += 1

        # Delete duplicate skater records
        for dup_id in merged_into:
            s = db.query(Skater).get(dup_id)
            if s:
                db.delete(s)
                stats["duplicates_deleted"] += 1
        db.flush()

    # Phase 4: update names, normalized_name, gender for all remaining skaters
    surviving_ids = set(skater_data.keys()) - set(merged_into.keys())
    for sid in surviving_ids:
        data = skater_data[sid]
        s = data["skater"]
        cleaned = data["cleaned"]
        new_norm = data["new_norm"]
        gender_signal = data["gender_signal"]

        name_changed = s.full_name != cleaned or s.normalized_name != new_norm
        if name_changed:
            stats["names_cleaned"] += 1
            if not args.dry_run:
                s.full_name = cleaned
                s.normalized_name = new_norm
                first, last = _split_name(cleaned)
                s.first_name = first
                s.last_name = last

        # Set gender if not already set and we have a signal
        effective_gender = gender_signal
        if not effective_gender and s.full_name != cleaned:
            # Might have extracted gender from dirty original
            pass
        if effective_gender and not s.gender:
            stats["gender_set"] += 1
            if not args.dry_run:
                s.gender = effective_gender

    if not args.dry_run:
        db.commit()
    db.close()

    print("\n=== Name Cleanup Summary ===")
    print(f"  Names cleaned:        {stats['names_cleaned']}")
    print(f"  Gender set from name: {stats['gender_set']}")
    print(f"  Duplicate groups:     {len(collision_groups)}")
    print(f"  Skaters merged:       {stats['merged_into']}")
    print(f"  Skaters deleted:      {stats['duplicates_deleted']}")
    print(f"  Results re-pointed:   {stats['results_repointed']}")
    print(f"  Results dropped:      {stats['results_dropped']}")
    if args.dry_run:
        print("  (dry run — no changes written)")


if __name__ == "__main__":
    main()
