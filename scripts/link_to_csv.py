#!/usr/bin/env python3
"""Stage 2: Link skater_id and club_id to parsed CSV files.

Reads CSVs from data/parse_output/<event_id>/, matches skater names and club
affiliations against the existing SQLite DB (read-only), and writes enriched
CSVs plus a skater_matches.csv review file.

Usage:
    python3 scripts/link_to_csv.py --event-id 128
"""
import argparse
import csv
import re
import sqlite3
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher, get_close_matches
from pathlib import Path

SQLITE_PATH = Path(__file__).parent.parent / "data" / "skater_tracker_round2.db"
AFFIL_CSV   = Path(__file__).parent.parent / "data" / "club_affiliations.csv"


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------

def normalize(name: str) -> str:
    if not name:
        return ""
    name = name.lower().strip()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-z0-9 ]", "", name)
    return re.sub(r"\s+", " ", name).strip()


def similarity(a: str, b: str) -> int:
    return round(SequenceMatcher(None, a, b).ratio() * 100)


# ---------------------------------------------------------------------------
# Load reference data
# ---------------------------------------------------------------------------

def load_skaters(conn):
    """Returns {normalized_name: (id, full_name)}"""
    rows = conn.execute(
        "SELECT id, full_name, normalized_name FROM skaters WHERE normalized_name IS NOT NULL"
    ).fetchall()
    return {norm: (sid, full) for sid, full, norm in rows if norm}


def load_clubs(conn):
    """Returns {canonical_name_lower: (id, canonical_name)}"""
    rows = conn.execute("SELECT id, canonical_name FROM clubs").fetchall()
    return {name.lower().strip(): (cid, name) for cid, name in rows if name}


def load_affil_map():
    """Returns {raw_lower: canonical_name} from club_affiliations.csv"""
    result = {}
    if not AFFIL_CSV.exists():
        return result
    with open(AFFIL_CSV) as f:
        for row in csv.DictReader(f):
            raw    = row.get("raw_affiliation", "").strip()
            canon  = row.get("canonical_name", "").strip()
            if raw and canon:
                result[raw.lower()] = canon
    return result


# ---------------------------------------------------------------------------
# Canonical name per bib (priority: TC > overall > results > dist_class)
# ---------------------------------------------------------------------------

def build_bib_canon(parse_dir: Path):
    """Returns {bib: canonical_name} using text-based sources first."""
    bib_name = {}
    for fname in ["classification_distance.csv", "results.csv",
                  "classification_overall.csv", "time_classification.csv"]:
        p = parse_dir / fname
        if not p.exists():
            continue
        with open(p) as f:
            for r in csv.DictReader(f):
                bib = r.get("bib", "").strip()
                name = r.get("skater_name", "").strip()
                if bib and name:
                    bib_name[bib] = name  # later files overwrite (TC is last = highest priority)
    return bib_name


# ---------------------------------------------------------------------------
# Match a single name against skaters table
# ---------------------------------------------------------------------------

def norm_sorted(name: str) -> str:
    """Normalize then sort words — catches LastName FirstName vs FirstName LastName."""
    return " ".join(sorted(normalize(name).split()))


def match_skater(name: str, skater_map: dict, all_norms: list):
    """
    Returns (skater_id, full_name, confidence, method) or (None, None, 0, 'new').
    confidence: 100=exact, 70-99=fuzzy, 0=new
    """
    norm = normalize(name)
    if not norm:
        return None, None, 0, "empty"

    # Pass 1: exact normalized match
    if norm in skater_map:
        sid, full = skater_map[norm]
        return sid, full, 100, "exact"

    # Pass 2: sorted-words match (catches LastName FirstName reversal)
    ns = norm_sorted(name)
    for candidate, (sid, full) in skater_map.items():
        if norm_sorted(full) == ns:
            return sid, full, 100, "exact_sorted"

    # Pass 3: fuzzy via difflib
    close = get_close_matches(norm, all_norms, n=1, cutoff=0.82)
    if close:
        sid, full = skater_map[close[0]]
        score = similarity(norm, close[0])
        return sid, full, score, "fuzzy"

    # Pass 4: lower threshold — flag for review
    close2 = get_close_matches(norm, all_norms, n=1, cutoff=0.65)
    if close2:
        sid, full = skater_map[close2[0]]
        score = similarity(norm, close2[0])
        return sid, full, score, "review"

    return None, None, 0, "new"


# ---------------------------------------------------------------------------
# Match a club affiliation
# ---------------------------------------------------------------------------

CLUB_OVERRIDES = {
    "saratoga skaters": "Saratoga Winter Club",
    "pgs": "Pinnacle Speedskating",
}


def match_club(raw_affil: str, club_map: dict, affil_map: dict):
    """Returns (club_id, canonical_name) or (None, raw_affil)."""
    if not raw_affil:
        return None, None

    # Step 0: hard-coded overrides for known aliases not in affil CSV
    override = CLUB_OVERRIDES.get(raw_affil.lower())
    if override and override.lower() in club_map:
        cid, cname = club_map[override.lower()]
        return cid, cname

    # Step 1: check affil_map for raw -> canonical
    canonical = affil_map.get(raw_affil.lower(), raw_affil)

    # Step 2: exact lookup in clubs
    if canonical.lower() in club_map:
        cid, cname = club_map[canonical.lower()]
        return cid, cname

    # Step 3: fuzzy on canonical
    close = get_close_matches(canonical.lower(), list(club_map.keys()), n=1, cutoff=0.75)
    if close:
        cid, cname = club_map[close[0]]
        return cid, cname

    return None, canonical


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_event(event_id: int):
    parse_dir = Path(__file__).parent.parent / "data" / "parse_output" / str(event_id)

    conn = sqlite3.connect(str(SQLITE_PATH))
    skater_map = load_skaters(conn)
    all_norms  = list(skater_map.keys())
    club_map   = load_clubs(conn)
    affil_map  = load_affil_map()
    conn.close()

    bib_canon = build_bib_canon(parse_dir)

    # Match every unique bib once
    bib_results = {}  # bib -> (skater_id, matched_name, confidence, method)
    for bib, name in sorted(bib_canon.items()):
        sid, matched, conf, method = match_skater(name, skater_map, all_norms)
        bib_results[bib] = (sid, matched, conf, method, name)

    # Write skater_matches.csv review file
    review_path = parse_dir / "skater_matches.csv"
    with open(review_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bib", "parsed_name", "matched_name", "skater_id", "confidence", "method"])
        for bib, (sid, matched, conf, method, parsed) in sorted(bib_results.items(), key=lambda x: int(x[0])):
            w.writerow([bib, parsed, matched or "", sid or "", conf, method])

    # Enrich each CSV file
    files = ["classification_overall.csv", "time_classification.csv",
             "classification_distance.csv", "results.csv"]
    total_rows = 0
    for fname in files:
        p = parse_dir / fname
        if not p.exists():
            continue
        with open(p) as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue

        has_affil = "affiliation" in rows[0]
        fieldnames = list(rows[0].keys())
        if "skater_id" not in fieldnames:
            fieldnames.append("skater_id")
        if has_affil and "club_id" not in fieldnames:
            fieldnames.append("club_id")

        for r in rows:
            bib = r.get("bib", "").strip()
            sid, matched, conf, method, parsed = bib_results.get(bib, (None, None, 0, "unknown", ""))
            r["skater_id"] = sid or ""

            if has_affil:
                raw_affil = r.get("affiliation", "") or ""
                cid, _ = match_club(raw_affil, club_map, affil_map)
                r["club_id"] = cid or ""

        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        total_rows += len(rows)

    # Summary
    methods = defaultdict(int)
    for bib, (sid, matched, conf, method, parsed) in bib_results.items():
        methods[method] += 1

    print(f"\nEvent {event_id} — {len(bib_canon)} unique skaters")
    print(f"  exact   : {methods['exact']:>4}")
    print(f"  fuzzy   : {methods['fuzzy']:>4}")
    print(f"  review  : {methods['review']:>4}  ← check skater_matches.csv")
    print(f"  new     : {methods['new']:>4}  ← will need new skater rows")
    print(f"  {total_rows} CSV rows enriched with skater_id / club_id")
    print(f"\nReview file: {review_path}")

    # Print review and new cases
    if methods["review"] or methods["new"]:
        print()
        for bib, (sid, matched, conf, method, parsed) in sorted(bib_results.items(), key=lambda x: int(x[0])):
            if method in ("review", "new"):
                tag = f"score={conf}" if method == "review" else "NEW"
                print(f"  bib {bib:>4}  {parsed:<30} -> {matched or 'NO MATCH':<30} [{tag}]")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--event-id", type=int, required=True)
    args = p.parse_args()
    process_event(args.event_id)
