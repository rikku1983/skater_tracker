"""
Find candidate duplicate skaters for manual review.

Two generation passes:
  Pass A — Initial expansion: "S. Koons" ↔ "Sofya Koons"
            (initial-only first name + full-name skater sharing last name and first letter)
  Pass B — Fuzzy name match: Levenshtein distance ≤ 2 on full_name within the same
            last-name collision group.

Filters:
  - Same normalized_name → already identical, skip
  - Gender known for both and differs → skip (GENDER conflict)
  - Same race → mark conflict=SAME_RACE (kept in output, sorted to bottom)

Output: data/skater_candidates.csv
  One row per candidate pair.  User fills in merge=Y to confirm; then run
  merge_skater_pairs.py to apply.

Usage:
    python scripts/find_duplicate_candidates.py [--out PATH]
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
from src.db.models import Result, Race, Event, Skater

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUT_PATH = Path("data/skater_candidates.csv")
DISTANCES = [111, 222, 333, 500, 777, 1000, 1500]
INITIAL_RE = re.compile(r"^([A-Z])\.\s")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[-1] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def _fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return ""
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}:{s:06.3f}"


def _last_name(full: str) -> str:
    parts = full.strip().split()
    return parts[-1].lower() if parts else ""


def _confidence(match_type: str, club_match: str, conflict: str) -> str:
    if conflict:
        return "CONFLICT"
    if match_type == "INITIAL" and club_match == "Y":
        return "HIGH"
    if match_type == "FUZZY_1":
        return "HIGH"
    if match_type == "INITIAL" and club_match in ("", None):
        return "MEDIUM"
    if match_type == "FUZZY_2" and club_match == "Y":
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()
    out_path = Path(args.out)

    init_db()
    db = get_session()

    logger.info("Loading skaters…")
    all_skaters: list[Skater] = db.query(Skater).all()
    skater_by_id: dict[int, Skater] = {s.id: s for s in all_skaters}

    # --- Precompute per-skater race sets (for same-race conflict check) ---
    logger.info("Loading race memberships…")
    race_rows = db.query(Result.skater_id, Result.race_id).all()
    skater_races: dict[int, set[int]] = defaultdict(set)
    for sid, rid in race_rows:
        skater_races[sid].add(rid)

    # --- Precompute personal bests per distance ---
    logger.info("Computing personal bests…")
    from sqlalchemy import func
    pb_rows = (
        db.query(Result.skater_id, Race.distance_m,
                 func.min(Result.time_seconds).label("best"))
        .join(Race, Result.race_id == Race.id)
        .filter(Result.time_seconds.isnot(None), Result.time_seconds > 0)
        .group_by(Result.skater_id, Race.distance_m)
        .all()
    )
    personal_bests: dict[int, dict[int, float]] = defaultdict(dict)
    for sid, dist, best in pb_rows:
        if dist in DISTANCES:
            personal_bests[sid][dist] = best

    # --- Precompute seasons per skater ---
    logger.info("Computing seasons…")
    season_rows = (
        db.query(Result.skater_id, Event.season)
        .join(Race, Result.race_id == Race.id)
        .join(Event, Race.event_id == Event.id)
        .distinct()
        .all()
    )
    skater_seasons: dict[int, set[str]] = defaultdict(set)
    for sid, season in season_rows:
        skater_seasons[sid].add(season)

    # --- Index skaters by last name ---
    by_last: dict[str, list[Skater]] = defaultdict(list)
    for s in all_skaters:
        by_last[_last_name(s.full_name)].append(s)

    # --- Build candidate pairs ---
    seen_pairs: set[tuple[int, int]] = set()
    candidates: list[dict] = []

    def add_pair(a: Skater, b: Skater, match_type: str) -> None:
        """Add a candidate pair, with a=canonical (fuller name), b=duplicate."""
        key = (min(a.id, b.id), max(a.id, b.id))
        if key in seen_pairs:
            return
        seen_pairs.add(key)

        # Gender conflict → skip entirely
        if a.gender and b.gender and a.gender != b.gender:
            return
        # Same normalized name → already deduped
        if _normalize(a.full_name) == _normalize(b.full_name):
            return

        # Same-race conflict check
        shared_races = skater_races[a.id] & skater_races[b.id]
        conflict = "SAME_RACE" if shared_races else ""

        # Club match
        ca = (a.club_name or "").strip().lower()
        cb = (b.club_name or "").strip().lower()
        if ca and cb:
            club_match = "Y" if ca == cb else "N"
        else:
            club_match = ""

        conf = _confidence(match_type, club_match, conflict)

        def bests(sid: int) -> list[str]:
            pb = personal_bests[sid]
            return [_fmt_time(pb.get(d)) for d in DISTANCES]

        candidates.append({
            "id_a": a.id, "name_a": a.full_name,
            "club_a": a.club_name or "", "gender_a": a.gender or "",
            "birth_year_a": a.birth_year or "",
            "seasons_a": " ".join(sorted(skater_seasons[a.id])),
            **{f"best_{d}_a": v for d, v in zip(DISTANCES, bests(a.id))},
            "id_b": b.id, "name_b": b.full_name,
            "club_b": b.club_name or "", "gender_b": b.gender or "",
            "birth_year_b": b.birth_year or "",
            "seasons_b": " ".join(sorted(skater_seasons[b.id])),
            **{f"best_{d}_b": v for d, v in zip(DISTANCES, bests(b.id))},
            "match_type": match_type,
            "conflict": conflict,
            "club_match": club_match,
            "confidence": conf,
            "merge": "",
        })

    # ------------------------------------------------------------------
    # Pass A: Initial expansion
    # "S. Koons" → matches "Sofya Koons" (same last name, first letter S)
    # ------------------------------------------------------------------
    logger.info("Pass A: initial expansion…")
    for last, group in by_last.items():
        if not last:
            continue
        init_skaters = [s for s in group if INITIAL_RE.match(s.full_name)]
        full_skaters = [s for s in group if not INITIAL_RE.match(s.full_name)]
        for si in init_skaters:
            init_letter = si.full_name[0]
            for sf in full_skaters:
                if sf.full_name[0] == init_letter:
                    # Full-name skater is canonical (id_a)
                    add_pair(sf, si, "INITIAL")

    logger.info("  Pass A produced %d pairs so far", len(candidates))

    # ------------------------------------------------------------------
    # Pass B: Fuzzy name match (Levenshtein ≤ 2) within last-name groups
    # ------------------------------------------------------------------
    logger.info("Pass B: fuzzy name match…")
    for last, group in by_last.items():
        if len(group) < 2:
            continue
        for i, sa in enumerate(group):
            for sb in group[i + 1:]:
                # Skip pairs whose first character differs — almost certainly different people
                if sa.full_name and sb.full_name and sa.full_name[0] != sb.full_name[0]:
                    continue
                dist = _levenshtein(sa.full_name.lower(), sb.full_name.lower())
                if dist < 1 or dist > 2:
                    continue
                match_type = f"FUZZY_{dist}"
                # Canonical = longer / more complete name; tiebreak = lower id
                if len(sa.full_name) >= len(sb.full_name):
                    add_pair(sa, sb, match_type)
                else:
                    add_pair(sb, sa, match_type)

    logger.info("  Total candidate pairs: %d", len(candidates))

    # ------------------------------------------------------------------
    # Sort: HIGH → MEDIUM → LOW, conflicts last
    # ------------------------------------------------------------------
    conf_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "CONFLICT": 3}
    candidates.sort(key=lambda r: (conf_order.get(r["confidence"], 9),
                                   r["match_type"], r["name_a"]))

    # ------------------------------------------------------------------
    # Write CSV
    # ------------------------------------------------------------------
    fieldnames = (
        ["id_a", "name_a", "club_a", "gender_a", "birth_year_a", "seasons_a"]
        + [f"best_{d}_a" for d in DISTANCES]
        + ["id_b", "name_b", "club_b", "gender_b", "birth_year_b", "seasons_b"]
        + [f"best_{d}_b" for d in DISTANCES]
        + ["match_type", "conflict", "club_match", "confidence", "merge"]
    )

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(candidates)

    logger.info("Wrote %d rows to %s", len(candidates), out_path)

    # Summary
    from collections import Counter
    by_conf = Counter(r["confidence"] for r in candidates)
    for conf in ["HIGH", "MEDIUM", "LOW", "CONFLICT"]:
        logger.info("  %-10s %d", conf, by_conf.get(conf, 0))

    db.close()


if __name__ == "__main__":
    main()
