"""
Build the skaters table and populate skater_id FKs on results, classification,
skater_entries, and time_classification.

Seeding:   knowledge_base/skaters_cleaner.csv (3,582 deduplicated skaters from v1)
Matching:  6-pass algorithm with confidence scoring
Output:    data/skater_matching_review.csv — medium-confidence pairs for manual review

Usage:
    source .venv/bin/activate && python3 scripts/normalize_skaters.py [--dry-run] [--force]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session_v2 import init_db, get_session
from src.db.models_v2 import Skater, Result, Classification, SkaterEntry, TimeClassification
from sqlalchemy import text

SKATERS_CSV   = Path(__file__).parents[1] / "knowledge_base" / "skaters_cleaner.csv"
REVIEW_CSV    = Path(__file__).parents[1] / "data" / "skater_matching_review.csv"

AUTO_ACCEPT   = 60   # score ≥ this → auto link
REVIEW_MIN    = 45   # score ≥ this → output for review

# ── Name helpers (adapted from scripts/clean_skater_names.py) ─────────────────

_SUFFIXES_TO_KEEP = {"II", "III", "IV", "JR.", "SR."}


def clean_name(raw: str) -> tuple[str, str | None]:
    """Return (cleaned_name, gender_signal). Reused from clean_skater_names.py."""
    name = raw.strip()
    gender: str | None = None

    name = re.sub(r"\s+\d+:\d+\.\d+.*$", "", name).strip()

    if "*" in name:
        gender = "F"
        name = name.replace("*", "").strip()

    name = name.replace("^", "").strip()

    m = re.search(r"\(\s*([MFmf])\s*\)", name)
    if m:
        if gender is None:
            gender = m.group(1).upper()
        name = re.sub(r"\s*\(\s*[MFmf]\s*\)\s*", " ", name).strip()

    m = re.search(r"\(\s*\d+\+\s*([MFmf])\s*\)\s*$", name)
    if m:
        if gender is None:
            gender = m.group(1).upper()
        name = re.sub(r"\s*\(\s*\d+\+\s*[MFmf]\s*\)\s*$", "", name).strip()

    name = re.sub(r"\s*\([^)]*\)\s*", " ", name).strip()

    m = re.match(r"^(.+?)\s+([mMfF])\s*$", name)
    if m:
        if gender is None:
            gender = m.group(2).upper()
        name = m.group(1).strip()

    name = re.sub(r"\s+JR\s+[A-Za-z]\s*$", "", name, flags=re.IGNORECASE).strip()

    if "," in name:
        last, _, first = name.partition(",")
        first, last = first.strip(), last.strip()
        if first:
            name = f"{first} {last}"

    tokens = name.split()
    fixed = []
    for tok in tokens:
        alpha = re.sub(r"[^a-zA-Z]", "", tok)
        if len(alpha) >= 3 and alpha.isupper() and tok not in _SUFFIXES_TO_KEEP:
            tok = "-".join(seg.capitalize() for seg in tok.split("-"))
        fixed.append(tok)
    name = " ".join(fixed)
    name = re.sub(r"\s+", " ", name).strip()
    return name, gender


def norm(s: str) -> str:
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def norm_sorted(s: str) -> str:
    return " ".join(sorted(norm(s).split()))


def flip_caps_first(raw: str, cleaned: str) -> str:
    """If the raw name's first token is ALL-CAPS (≥3 alpha chars), treat as
    LASTNAME Firstname format (speedskatinglive / AmCup convention) and return
    Firstname Lastname instead.

    'ZHANG Dylan'    → 'Dylan Zhang'
    'ZHANG DYLAN'    → 'Dylan Zhang'   (both tokens were caps → flip still correct)
    'HSIAO-YING CHUNG' → 'Chung Hsiao-Ying'
    'Dylan Zhang'    → 'Dylan Zhang'   (first token not all-caps → no change)
    """
    raw_parts = raw.strip().split()
    if not raw_parts:
        return cleaned
    alpha_first = re.sub(r"[^a-zA-Z]", "", raw_parts[0])
    if len(alpha_first) >= 3 and alpha_first == alpha_first.upper():
        parts = cleaned.split()
        if len(parts) >= 2:
            return " ".join(parts[1:] + [parts[0]])
    return cleaned


def _split_name(full: str) -> tuple[str | None, str | None]:
    parts = full.strip().split()
    if len(parts) >= 2:
        return " ".join(parts[:-1]), parts[-1]   # first_name = all but last, last_name = last token
    if parts:
        return None, parts[0]
    return None, None


def _last_name(full: str) -> str:
    parts = norm(full).split()
    return parts[-1] if parts else ""


def _levenshtein(a: str, b: str) -> int:
    if a == b: return 0
    if len(a) < len(b): a, b = b, a
    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j+1]+1, curr[-1]+1, prev[j]+(ca != cb)))
        prev = curr
    return prev[-1]


def _is_initial_name(s: str) -> bool:
    return bool(re.match(r"^[A-Za-z]\.\s", s))


# ── Load CSV seed data ────────────────────────────────────────────────────────

def load_csv_skaters() -> list[dict]:
    rows = []
    for r in csv.DictReader(open(SKATERS_CSV)):
        rows.append({
            "full_name":       r["full_name"].strip(),
            "first_name":      r["first_name"].strip(),
            "last_name":       r["last_name"].strip(),
            "normalized_name": r["normalized_name"].strip(),
            "gender":          "Male" if r["gender"].strip() == "M" else ("Female" if r["gender"].strip() == "F" else None),
            "birth_year":      int(r["birth_year"]) if r.get("birth_year", "").strip().isdigit() else None,
            "club_name":       r.get("club_name", "").strip() or None,
        })
    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force",   action="store_true",
                        help="Re-run even if skaters table already populated")
    args = parser.parse_args()

    init_db()
    db = get_session()

    existing = db.query(Skater).count()
    if existing and not args.force:
        print(f"skaters table already has {existing} rows. Use --force to re-run.")
        db.close()
        return

    if args.force and existing:
        print(f"--force: clearing {existing} skater rows and resetting skater_id columns")
        if not args.dry_run:
            db.query(Skater).delete()
            for tbl in ("results", "skater_entries", "classification", "time_classification"):
                db.execute(text(f"UPDATE {tbl} SET skater_id = NULL"))
            db.commit()

    # ── Step 1: Seed from CSV ─────────────────────────────────────────────────
    print(f"Loading CSV seed from {SKATERS_CSV.name}…")
    csv_rows = load_csv_skaters()

    # norm → skater_id for quick lookup
    norm_to_id:        dict[str, int] = {}
    norm_sorted_to_id: dict[str, int] = {}

    skater_objects: dict[int, Skater] = {}   # id → Skater (in-memory before flush)

    for r in csv_rows:
        s = Skater(
            full_name       = r["full_name"],
            first_name      = r["first_name"],
            last_name       = r["last_name"],
            normalized_name = r["normalized_name"],
            gender          = r["gender"],
            birth_year      = r["birth_year"],
            source          = "csv",
        )
        if not args.dry_run:
            db.add(s)
            db.flush()
            norm_to_id[r["normalized_name"]]        = s.id
            norm_sorted_to_id[" ".join(sorted(r["normalized_name"].split()))] = s.id
            skater_objects[s.id] = s

    if not args.dry_run:
        db.commit()
        # Also index the flipped form of each CSV name so CAPS-first raw names
        # match in Pass 1 (exact_norm) directly rather than falling through to Pass 2.
        for r in csv_rows:
            n = r["normalized_name"]
            flipped = " ".join(n.split()[-1:] + n.split()[:-1])  # move last token to front
            if flipped not in norm_to_id:
                norm_to_id[flipped] = norm_to_id[n]
    print(f"  Seeded {len(csv_rows)} skaters from CSV")

    # ── Step 2: Collect all distinct raw names from DB ────────────────────────
    print("Collecting raw names from DB…")
    raw_names: dict[str, dict] = {}   # raw_name → {gender, by_min, by_max, club_id}

    # skater_entries is richest (has gender + birth_year)
    for name, gender, by1, by2, cid in db.execute(text("""
        SELECT skater_name, gender, birth_year_1, birth_year_2, club_id
        FROM skater_entries WHERE skater_name IS NOT NULL
    """)).fetchall():
        if name and name not in raw_names:
            raw_names[name] = {"gender": gender, "by_min": by2, "by_max": by1, "club_id": cid}

    # results, classification, time_classification (name only)
    for tbl in ("results", "classification", "time_classification"):
        for (name,) in db.execute(text(f"SELECT DISTINCT skater_name FROM {tbl} WHERE skater_name IS NOT NULL")).fetchall():
            if name and name not in raw_names:
                raw_names[name] = {"gender": None, "by_min": None, "by_max": None, "club_id": None}

    print(f"  {len(raw_names)} distinct raw names to match")

    # ── Step 3: Pre-clean all raw names ──────────────────────────────────────
    cleaned:   dict[str, str] = {}    # raw → cleaned (title-cased, markers stripped)
    canonical: dict[str, str] = {}    # raw → canonical (cleaned + flipped if CAPS-first)
    clean_gender: dict[str, str] = {}  # raw → gender extracted from name
    for raw in raw_names:
        c, g = clean_name(raw)
        cleaned[raw] = c
        canonical[raw] = flip_caps_first(raw, c)
        if g:
            clean_gender[raw] = "Male" if g == "M" else "Female"

    # ── Step 4: Build same-race conflict index ────────────────────────────────
    # (event_id, race_number, heat) → set of skater_names
    print("Building same-race conflict index…")
    race_names: dict[tuple, set] = defaultdict(set)
    for name, eid, rn, heat in db.execute(text("""
        SELECT skater_name, event_id, race_number, heat
        FROM results WHERE skater_name IS NOT NULL
    """)).fetchall():
        if name and rn is not None:
            race_names[(eid, rn, heat)].add(name)

    # For each raw name, which other names share a race?
    same_race_pairs: set[tuple] = set()
    for names in race_names.values():
        nlist = list(names)
        for i in range(len(nlist)):
            for j in range(i+1, len(nlist)):
                a, b = nlist[i], nlist[j]
                same_race_pairs.add((min(a,b), max(a,b)))

    def conflicts_race(raw_a: str, raw_b: str) -> bool:
        key = (min(raw_a, raw_b), max(raw_a, raw_b))
        return key in same_race_pairs

    # ── Step 5: Build per-skater best times AND season bests ─────────────────
    print("Building personal best times…")
    name_bests: dict[str, dict[int, float]] = defaultdict(dict)
    for name, dist, best in db.execute(text("""
        SELECT skater_name, distance_m, MIN(time_seconds)
        FROM results
        WHERE time_seconds IS NOT NULL AND time_seconds > 0 AND distance_m IS NOT NULL
        GROUP BY skater_name, distance_m
    """)).fetchall():
        if name and dist:
            name_bests[name][dist] = best

    # season-best index: name → distance → [(season, best_time)]
    name_season_bests: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for name, dist, season, best in db.execute(text("""
        SELECT r.skater_name, r.distance_m, e.season, MIN(r.time_seconds)
        FROM results r JOIN events e ON e.id = r.event_id
        WHERE r.time_seconds IS NOT NULL AND r.time_seconds > 10
          AND r.distance_m IS NOT NULL
        GROUP BY r.skater_name, r.distance_m, e.season
    """)).fetchall():
        if name and dist and season:
            name_season_bests[name][dist].append((season, best))

    def times_consistent(raw: str, skater: Skater) -> bool:
        """Return False if merging raw into skater would produce implausible regressions.

        Two checks:
        1. Raw's best is not >10% faster than skater's known best (physically impossible).
        2. Combined season-by-season timeline does not show >40% regression on any
           distance — that level of slowdown across all distances signals two different
           people, not a bad day.
        """
        if not skater.normalized_name:
            return True
        skater_norm = skater.normalized_name

        # Gather bests for all names already mapped to this skater
        skater_bests: dict[int, float] = {}
        skater_season_bests: dict[int, list] = defaultdict(list)
        for r2 in raw_names:
            if norm(canonical.get(r2, r2)) == skater_norm:
                for dist, t in name_bests.get(r2, {}).items():
                    if dist not in skater_bests or t < skater_bests[dist]:
                        skater_bests[dist] = t
                for dist, entries in name_season_bests.get(r2, {}).items():
                    skater_season_bests[dist].extend(entries)

        if not skater_bests:
            return True

        raw_bests = name_bests.get(raw, {})

        # Check 1: incoming time impossibly fast
        for dist, t in raw_bests.items():
            existing = skater_bests.get(dist)
            if existing and t < existing * 0.90:
                return False

        # Check 2: combined seasonal regression
        raw_season_bests = name_season_bests.get(raw, {})
        for dist, raw_entries in raw_season_bests.items():
            combined = sorted(skater_season_bests.get(dist, []) + raw_entries)
            if len(combined) < 2:
                continue
            fast_best = min(t for _, t in combined)
            slow_worst = max(t for _, t in combined)
            # If ANY season is >40% slower than the overall best → suspect
            if fast_best > 0 and (slow_worst - fast_best) / fast_best > 0.40:
                # Only flag if the regression is consistent across ≥2 distances
                # (a single bad parse shouldn't kill a match)
                return False

        return True

    # ── Step 6: Index skaters for matching ───────────────────────────────────
    # Build last-name group index for Levenshtein pass
    by_last: dict[str, list[int]] = defaultdict(list)
    if not args.dry_run:
        for sid, sname in db.execute(text("SELECT id, normalized_name FROM skaters WHERE normalized_name IS NOT NULL")).fetchall():
            ln = sname.split()[-1] if sname.split() else ""
            if ln:
                by_last[ln].append(sid)

    skater_by_id_cache: dict[int, Skater] = {}
    def get_skater(sid: int) -> Skater | None:
        if sid in skater_by_id_cache:
            return skater_by_id_cache[sid]
        s = db.query(Skater).filter_by(id=sid).first()
        if s:
            skater_by_id_cache[sid] = s
        return s

    # ── Step 7: Score a candidate match ──────────────────────────────────────
    def score_match(raw: str, meta: dict, skater: Skater, match_type: str) -> tuple[int, list[str]]:
        score = 0
        signals = [match_type]

        base_scores = {
            "exact_norm": 50, "exact_sorted": 60, "initial": 30,
            "levenshtein_1": 45, "levenshtein_2": 35,
            "fuzzy_92": 40, "fuzzy_85": 25,
        }
        score += base_scores.get(match_type, 0)

        # Gender
        gender_hint = meta.get("gender") or clean_gender.get(raw)
        if gender_hint and skater.gender:
            if gender_hint == skater.gender:
                score += 15
                signals.append("gender_match")
            else:
                score -= 25   # strong negative
                signals.append("gender_conflict!")

        # Birth year overlap
        by_min, by_max = meta.get("by_min"), meta.get("by_max")
        if by_min and by_max and skater.birth_year:
            if by_min <= skater.birth_year <= by_max:
                score += 20
                signals.append("birth_year_overlap")
            else:
                score -= 15
                signals.append("birth_year_mismatch!")

        # Time consistency
        if match_type in ("fuzzy_92", "fuzzy_85", "levenshtein_2"):
            if not times_consistent(raw, skater):
                score -= 10
                signals.append("time_inconsistent!")

        return score, signals

    # ── Step 8: Match loop ────────────────────────────────────────────────────
    print("Matching names…")
    accepted: dict[str, int]  = {}   # raw → skater_id
    review:   list[dict]      = []
    new_skaters_needed: list[str] = []

    stats = defaultdict(int)

    for raw, meta in raw_names.items():
        if raw in accepted:
            continue
        c = canonical.get(raw, raw)   # use flip-corrected canonical form
        n = norm(c)
        ns = norm_sorted(c)

        best_sid = None
        best_score = 0
        best_signals: list[str] = []

        if args.dry_run:
            # In dry-run, just count what would happen without writing
            if n in norm_to_id:
                stats["would_exact"] += 1
            elif ns in norm_sorted_to_id:
                stats["would_sorted"] += 1
            continue

        # Pass 1: exact norm
        if n in norm_to_id:
            sid = norm_to_id[n]
            if not conflicts_race(raw, get_skater(sid).full_name if get_skater(sid) else ""):
                accepted[raw] = sid
                stats["exact_norm"] += 1
                # Add alias
                s = get_skater(sid)
                if s and raw not in (s.known_aliases or "").split(" | ") and raw != s.full_name:
                    s.known_aliases = (s.known_aliases + " | " + raw).lstrip(" | ") if s.known_aliases else raw
                continue

        # Pass 2: exact norm_sorted
        if ns in norm_sorted_to_id:
            sid = norm_sorted_to_id[ns]
            score, signals = score_match(raw, meta, get_skater(sid), "exact_sorted")
            if score >= AUTO_ACCEPT and not conflicts_race(raw, get_skater(sid).full_name if get_skater(sid) else ""):
                accepted[raw] = sid
                stats["exact_sorted"] += 1
                s = get_skater(sid)
                if s and raw != s.full_name:
                    s.known_aliases = (s.known_aliases + " | " + raw).lstrip(" | ") if s.known_aliases else raw
                continue
            if score >= REVIEW_MIN:
                best_sid, best_score, best_signals = sid, score, signals

        # Pass 3: initial expansion ("A. Zhang" → "Aiden Zhang")
        parts = n.split()
        if len(parts) == 2 and len(parts[0]) == 1:
            last = parts[1]
            candidates_last = by_last.get(last, [])
            for sid in candidates_last:
                s = get_skater(sid)
                if not s: continue
                sparts = (s.normalized_name or "").split()
                if len(sparts) >= 2 and sparts[0][0] == parts[0] and sparts[-1] == last:
                    score, signals = score_match(raw, meta, s, "initial")
                    if conflicts_race(raw, s.full_name): continue
                    if score >= AUTO_ACCEPT:
                        accepted[raw] = sid
                        stats["initial"] += 1
                        if raw != s.full_name:
                            s.known_aliases = (s.known_aliases + " | " + raw).lstrip(" | ") if s.known_aliases else raw
                        best_sid = None
                        break
                    if score > best_score and score >= REVIEW_MIN:
                        best_sid, best_score, best_signals = sid, score, signals
            if raw in accepted:
                continue

        # Pass 4: Levenshtein ≤ 2 within same last-name group
        last_key = parts[-1] if parts else ""
        if last_key:
            for sid in by_last.get(last_key, []):
                s = get_skater(sid)
                if not s: continue
                sn = s.normalized_name or ""
                lev = _levenshtein(n, sn)
                if lev < 1 or lev > 2: continue
                match_type = f"levenshtein_{lev}"
                score, signals = score_match(raw, meta, s, match_type)
                if conflicts_race(raw, s.full_name): continue
                if score >= AUTO_ACCEPT:
                    accepted[raw] = sid
                    stats[match_type] += 1
                    if raw != s.full_name:
                        s.known_aliases = (s.known_aliases + " | " + raw).lstrip(" | ") if s.known_aliases else raw
                    best_sid = None
                    break
                if score > best_score and score >= REVIEW_MIN:
                    best_sid, best_score, best_signals = sid, score, signals
            if raw in accepted:
                continue

        # Passes 5-6: rapidfuzz (if available)
        try:
            from rapidfuzz import fuzz
            all_sids = list({sid for sids in by_last.values() for sid in sids})
            for sid in all_sids[:500]:   # cap at 500 candidates for performance
                s = get_skater(sid)
                if not s: continue
                ratio = fuzz.token_sort_ratio(n, s.normalized_name or "") / 100.0
                if ratio < 0.85: continue
                match_type = "fuzzy_92" if ratio >= 0.92 else "fuzzy_85"
                score, signals = score_match(raw, meta, s, match_type)
                if conflicts_race(raw, s.full_name): continue
                if score >= AUTO_ACCEPT:
                    accepted[raw] = sid
                    stats[match_type] += 1
                    if raw != s.full_name:
                        s.known_aliases = (s.known_aliases + " | " + raw).lstrip(" | ") if s.known_aliases else raw
                    best_sid = None
                    break
                if score > best_score and score >= REVIEW_MIN:
                    best_sid, best_score, best_signals = sid, score, signals
            if raw in accepted:
                continue
        except ImportError:
            pass

        # Record review candidate or mark as new
        if best_sid and best_score >= REVIEW_MIN:
            s = get_skater(best_sid)
            review.append({
                "raw_name":        raw,
                "cleaned_name":    c,
                "candidate_name":  s.full_name if s else "",
                "candidate_id":    best_sid,
                "score":           best_score,
                "signals":         " | ".join(best_signals),
                "gender_raw":      meta.get("gender") or "",
                "gender_csv":      s.gender if s else "",
                "birth_year_range": f"{meta.get('by_min')}-{meta.get('by_max')}" if meta.get('by_min') else "",
                "birth_year_csv":  s.birth_year if s else "",
            })
            stats["review"] += 1
        else:
            new_skaters_needed.append(raw)
            stats["new"] += 1

    if args.dry_run:
        print(f"  [dry-run] would exact-norm={stats['would_exact']}, sorted={stats['would_sorted']}")
        db.close()
        return

    # ── Step 9: Create new Skater rows for unmatched names ───────────────────
    print(f"Creating {len(new_skaters_needed)} new skater rows…")
    for raw in new_skaters_needed:
        c = canonical.get(raw, raw)   # use flip-corrected canonical form
        meta = raw_names[raw]
        gender_hint = meta.get("gender") or (
            "Male" if clean_gender.get(raw) == "M" else
            "Female" if clean_gender.get(raw) == "F" else None
        )
        fn, ln = _split_name(c)
        s = Skater(
            full_name       = c,
            first_name      = fn,
            last_name       = ln,
            normalized_name = norm(c),
            gender          = gender_hint,
            birth_year_min  = meta.get("by_max"),   # by_max = older end = min birth year
            birth_year_max  = meta.get("by_min"),   # by_min = younger end = max birth year
            known_aliases   = raw if raw != c else None,
            source          = "db_new",
        )
        db.add(s)
        db.flush()
        accepted[raw] = s.id
        norm_to_id[norm(c)] = s.id

    db.commit()

    # ── Step 10: Populate skater_id FK columns ────────────────────────────────
    print("Writing skater_id to all tables…")
    for raw, sid in accepted.items():
        for tbl, col in [
            ("results",             "skater_name"),
            ("skater_entries",      "skater_name"),
            ("classification",      "skater_name"),
            ("time_classification", "skater_name"),
        ]:
            db.execute(text(
                f"UPDATE {tbl} SET skater_id = :sid WHERE {col} = :raw AND skater_id IS NULL"
            ), {"sid": sid, "raw": raw})
    db.commit()

    # ── Step 11: Write review CSV ─────────────────────────────────────────────
    fields = ["raw_name","cleaned_name","candidate_name","candidate_id","score",
              "signals","gender_raw","gender_csv","birth_year_range","birth_year_csv","merge"]
    with open(REVIEW_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sorted(review, key=lambda x: -x["score"]):
            r["merge"] = ""
            w.writerow(r)

    # ── Summary ───────────────────────────────────────────────────────────────
    total_skaters = db.query(Skater).count()
    for tbl in ("results", "skater_entries", "classification", "time_classification"):
        linked = db.execute(text(f"SELECT COUNT(*) FROM {tbl} WHERE skater_id IS NOT NULL")).scalar()
        total  = db.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
        print(f"  {tbl:<25} skater_id linked: {linked:,}/{total:,} ({100*linked//total if total else 0}%)")

    print(f"\nSkaters table: {total_skaters:,} total")
    print(f"  from CSV:    {db.query(Skater).filter_by(source='csv').count():,}")
    print(f"  new (db):    {db.query(Skater).filter_by(source='db_new').count():,}")
    print(f"\nMatch breakdown:")
    for k, v in sorted(stats.items()):
        print(f"  {k:<20} {v:,}")
    print(f"\nReview CSV: {len(review)} rows → {REVIEW_CSV}")
    db.close()


if __name__ == "__main__":
    main()
