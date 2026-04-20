"""
Categorise the unknown-format PDFs to guide future parsing work.

For each event with pdf_format='unknown', opens the PDF and classifies it as:
  race_results      — has individual heat pages (Event #N / Heat N of T / All Races / Results)
  classification    — only standings/ranking pages (Class Standings / Distance Classification)
  protocol_only     — no DC or race pages; mostly officials/schedule
  image             — no extractable text (scanned)
  missing           — local_path not found on disk

Usage:
    python scripts/diagnose_unknowns.py
    python scripts/diagnose_unknowns.py --season 2024-2025
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pdfplumber

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.session import get_session
from src.db.models import Event


# Markers that suggest individual race result pages
_RACE_MARKERS = [
    r'Event\s+#\d+',           # Tempus/Speedskating Pro heat header
    r'Heat\s+\d+\s+of\s+\d+',  # Heat N of T
    r'All Races',               # all_races format
    r'^Results$',               # tempus_results format
    r'RACE:\s*\d+\s+HEAT:',     # tempus protocol format
]
_RACE_RE = [re.compile(p, re.I | re.M) for p in _RACE_MARKERS]

# Markers that indicate classification/standings-only pages
_CLASS_MARKERS = [
    "DISTANCE CLASSIFICATION",
    "Distance Classification",
    "Class Standings",
    "OVERALL CLASSIFICATION",
    "FINAL CLASSIFICATION",
    "TIME CLASSIFICATION",
]


def _classify_pdf(path: Path) -> tuple[str, str]:
    """Return (category, note) for the PDF at path."""
    if not path.exists():
        return "missing", "file not found on disk"

    try:
        with pdfplumber.open(str(path)) as pdf:
            n = len(pdf.pages)
            all_texts = [p.extract_text() or "" for p in pdf.pages]
            full_text = "\n".join(all_texts)

            # Image check: most pages have no text
            empty = sum(1 for t in all_texts if not t.strip())
            if empty >= len(all_texts) * 0.8:
                return "image", f"{empty}/{n} pages have no text"

            # Check for race result markers
            has_race = any(r.search(full_text) for r in _RACE_RE)
            if has_race:
                markers_found = [r.pattern for r in _RACE_RE if r.search(full_text)]
                return "race_results", f"markers: {', '.join(markers_found[:3])}"

            # Check for classification markers
            has_class = any(m in full_text for m in _CLASS_MARKERS)
            if has_class:
                found = [m for m in _CLASS_MARKERS if m in full_text]
                return "classification", f"markers: {', '.join(found[:3])}"

            return "protocol_only", f"{n} pages, no race/class markers found"

    except Exception as e:
        return "error", str(e)


def main():
    parser = argparse.ArgumentParser(description="Categorise unknown-format PDFs")
    parser.add_argument("--season", help="Filter to one season (e.g. 2024-2025)")
    args = parser.parse_args()

    session = get_session()
    query = session.query(Event).filter(Event.pdf_format == "unknown")
    if args.season:
        query = query.filter(Event.season == args.season)
    events = query.order_by(Event.season, Event.event_name).all()

    if not events:
        print("No unknown-format events found.")
        return

    counts: dict[str, int] = {}
    rows = []

    for ev in events:
        path = Path(ev.local_path) if ev.local_path else None
        if path is None:
            category, note = "missing", "no local_path in DB"
        else:
            category, note = _classify_pdf(path)

        counts[category] = counts.get(category, 0) + 1
        rows.append((ev.season, category, ev.event_name, note))

    # Print results grouped by category
    for cat in ("race_results", "classification", "protocol_only", "image", "missing", "error"):
        cat_rows = [r for r in rows if r[1] == cat]
        if not cat_rows:
            continue
        print(f"\n=== {cat.upper()} ({len(cat_rows)}) ===")
        for season, _, name, note in cat_rows:
            print(f"  [{season}] {name}")
            print(f"    → {note}")

    print("\n--- Summary ---")
    for cat, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {cat:<20} {cnt}")
    print(f"  {'TOTAL':<20} {len(rows)}")


if __name__ == "__main__":
    main()
