"""
Generate data/club_affiliations.csv — a mapping from every raw Skater.club_name
value to a clean canonical affiliation for user review.

Auto-classification rules (applied in order):
  1. Known ISU/ISO country codes → type=country
  2. "Direct" / "DIR" / "1. Direct …" variants → type=direct, canonical="Direct"
  3. Value contains a time pattern OR is a bare number → type=junk
  4. Known junk tokens (DNS, DNF, DQ, A1, A2 …) → type=junk
  5. Exact match to Club.name → type=club, canonical=Club.name
  6. "USA-{abbrev}" where abbrev matches Club.abbreviation → type=club
  7. Normalised edit distance ≤ 1 from any Club.name → type=club
  8. Everything else → type=unknown (user fills in canonical + type)

Output columns: raw_value, count, type, canonical, notes
Sorted: unknown rows first (highest count first), then auto-classified rows.

Usage:
    python scripts/build_club_mapping.py [--out PATH]
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Club, Skater

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUT_PATH = Path("data/club_affiliations.csv")

# ISU / Olympic country codes used in speed skating results
COUNTRY_CODES: set[str] = {
    # 3-letter ISU/IOC codes
    "USA", "JPN", "CAN", "NOR", "POL", "ITA", "CHN", "GER", "KOR", "FIN",
    "AUS", "SUI", "DEN", "HUN", "CZE", "BEL", "SWE", "GRE", "SGP", "TPE",
    "COL", "KAZ", "AUT", "ARG", "ROU", "UZB", "EST", "NED", "GBR", "ESP",
    "BRA", "ECU", "NZL", "SVK", "PHI", "HKG", "ANG", "INA", "CHE", "PHI",
    "IND", "POC", "POCO", "MTV", "VNC", "TCL", "PNV", "RRK", "LTSS", "SRF",
    "BUR", "WTC", "L2SS", "NCA", "PSSC", "PCSSC", "PCSM", "PCSK",
    # 2-letter / alternate forms
    "UK", "HK", "BC",
    # Composite / region codes seen in results
    "BC,CAN",
}

DIRECT_RE = re.compile(r"^(direct|dir|1\.\s*direct|dir\.?)$", re.I)
TIME_RE = re.compile(r"\d+:\d{2}\.\d+")
BARE_NUMBER_RE = re.compile(r"^\d+(\.\d+)?$")
JUNK_TOKENS: set[str] = {
    "DNF", "DNS", "DQ", "ADV", "PEN", "DSQ", "EX", "FNT",
    "A1", "A2", "A3", "B1", "B2", "B3",
    "MIXED", "STATE", "SPEEDSKATING", "SPEEDSKATING CLUB",
    "ALEC", "UV", "DIR",
}


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


def _classify(raw: str, clubs_by_name: dict[str, str],
              clubs_by_abbrev: dict[str, str],
              club_norms: list[tuple[str, str]]) -> tuple[str, str, str]:
    """Return (type, canonical, notes)."""
    stripped = raw.strip()
    upper = stripped.upper()

    # 1. Country code
    if upper in COUNTRY_CODES:
        return "country", upper, ""

    # 2. Direct
    if DIRECT_RE.match(stripped):
        return "direct", "Direct", ""

    # 3. Time leaked / bare number
    if TIME_RE.search(stripped) or BARE_NUMBER_RE.match(stripped):
        # Try to salvage the prefix before the time
        prefix = TIME_RE.split(stripped)[0].strip().rstrip("-,; ")
        note = f"time leaked; prefix='{prefix}'" if prefix else "time leaked"
        return "junk", "", note

    # 4. Known junk tokens
    if upper in JUNK_TOKENS:
        return "junk", "", "junk token"

    # 5. Exact match to Club.name (case-insensitive)
    lower = stripped.lower()
    for cname, _ in clubs_by_name.items():
        if cname.lower() == lower:
            return "club", cname, "exact"

    # 6a. "USA-{abbrev}" pattern
    m = re.match(r"^USA-([A-Z0-9]+)$", stripped, re.I)
    if m:
        abbrev = m.group(1).upper()
        if abbrev in clubs_by_abbrev:
            return "club", clubs_by_abbrev[abbrev], f"USA-{abbrev}"

    # 6b. "CAN-{abbrev}" pattern
    m = re.match(r"^CAN-([A-Z0-9]+)$", stripped, re.I)
    if m:
        abbrev = m.group(1).upper()
        if abbrev in clubs_by_abbrev:
            return "club", clubs_by_abbrev[abbrev], f"CAN-{abbrev}"

    # 6c. Bare abbreviation match (raw == Club.abbreviation, case-insensitive)
    upper_strip = stripped.upper()
    if upper_strip in clubs_by_abbrev:
        return "club", clubs_by_abbrev[upper_strip], f"abbrev={upper_strip}"

    # 7. Normalised edit distance ≤ 1 from any Club.name
    norm = _normalize(stripped)
    for cnorm, cname in club_norms:
        if _levenshtein(norm, cnorm) <= 1:
            return "club", cname, f"fuzzy~'{cname}'"

    # 8. Raw value is a prefix of a Club.name (min 6 chars, case-insensitive)
    if len(stripped) >= 6:
        lower_strip = stripped.lower()
        for cname in clubs_by_name:
            if cname.lower().startswith(lower_strip):
                return "club", cname, f"prefix~'{cname}'"

    return "unknown", "", ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()
    out_path = Path(args.out)

    init_db()
    db = get_session()

    # Load Club table
    clubs = db.query(Club).all()
    clubs_by_name: dict[str, str] = {c.name: c.name for c in clubs}
    clubs_by_abbrev: dict[str, str] = {}
    for c in clubs:
        for abbrev in c.abbreviation.split("/"):
            clubs_by_abbrev[abbrev.strip().upper()] = c.name
    club_norms: list[tuple[str, str]] = [
        (_normalize(c.name), c.name) for c in clubs
    ]

    # Count raw club_name values
    rows = db.query(Skater.club_name).filter(Skater.club_name.isnot(None)).all()
    counts: Counter[str] = Counter(r[0].strip() for r in rows if r[0] and r[0].strip())

    logger.info("Distinct club_name values: %d", len(counts))

    results: list[dict] = []
    type_counts: Counter[str] = Counter()

    for raw, count in counts.most_common():
        typ, canonical, notes = _classify(
            raw, clubs_by_name, clubs_by_abbrev, club_norms)
        type_counts[typ] += 1
        results.append({
            "raw_value": raw,
            "count": count,
            "type": typ,
            "canonical": canonical,
            "notes": notes,
        })

    # Sort: unknown first (by count desc), then rest (by type, count desc)
    unknown = sorted([r for r in results if r["type"] == "unknown"],
                     key=lambda r: -r["count"])
    known = sorted([r for r in results if r["type"] != "unknown"],
                   key=lambda r: (r["type"], -r["count"]))
    sorted_results = unknown + known

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["raw_value", "count", "type", "canonical", "notes"])
        w.writeheader()
        w.writerows(sorted_results)

    logger.info("Wrote %d rows to %s", len(sorted_results), out_path)
    logger.info("Breakdown:")
    for typ in ["unknown", "club", "country", "direct", "junk"]:
        logger.info("  %-10s %d", typ, type_counts.get(typ, 0))

    db.close()


if __name__ == "__main__":
    main()
