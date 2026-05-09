"""
Parser for the Time Classification section of USS protocol PDFs (Tempus).

The Time Classification ranks skaters by best race time per distance within their
division. It is distinct from the Distance Classification (seeding groups).

Page format (text-based):
    <Event title lines>
    Time Classification
    <Division name>
    <NNN> Meters
    Skater # Name Best Time
    1  <bib>  <name...>  <M:SS.sss>
    2  ...
    <NNN2> Meters          ← new distance, same division
    ...
    <Division2>            ← new division
    ...

Pages are identified by 'Time Classification' in the extracted text.
Sections continue across page breaks — division context carries over.

Usage:
    python scripts/parse_time_classification.py --event-id N
    python scripts/parse_time_classification.py --event-id N --force
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parents[1]))
from src.db.session_v2 import init_db, get_session
from src.db.models_v2 import Event, TimeClassification

TIME_RE  = re.compile(r'^\d+:\d{2}\.\d+$')
DIST_RE  = re.compile(r'^(\d+)\s+Meters?$', re.I)

# Lines to skip (headers, event titles, etc.)
_SKIP = {'Time Classification', 'TIME CLASSIFICATION', 'Skater # Name Best Time', 'Skater # Name Best Ti'}

# Tempus page footer
_FOOTER_RE   = re.compile(r'^(Tempus Competition Software|Page \d+ of \d+)', re.I)
# Event title lines: contain a year + event keywords, or known header-only tokens
_TITLE_RE    = re.compile(r'(\d{4}.*Championship|\d{4}.*Invitational|\d{4}.*Open|Short Track|^All Races$|^RESULTS$)', re.I)


def _parse_time(text: str) -> float | None:
    m = re.match(r'^(\d+):(\d{2})\.(\d+)$', text)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / (10 ** len(m.group(3)))


def _is_division_line(line: str) -> bool:
    """Heuristic: a division line is not a skip line, not a distance, not a title,
    and doesn't look like a data row (which starts with a digit)."""
    if not line or line in _SKIP:
        return False
    if DIST_RE.match(line):
        return False
    if _FOOTER_RE.match(line):
        return False
    if line[0].isdigit():
        return False
    if _TITLE_RE.search(line):
        return False
    return True


def parse_time_classification_pages(pdf_path: Path,
                                     start_page: int | None = None,
                                     end_page: int | None = None) -> list[dict]:
    """Extract all time classification rows from a PDF. Returns list of row dicts.

    start_page: 1-based page number to force-enter TC mode from (for PDFs with no
                'Time Classification' header keyword).
    """
    rows_out = []
    cur_division: str | None = None
    cur_distance_m: int | None = None

    # If start_page is given, skip the keyword check and begin from that page.
    in_tc_section = start_page is not None

    with pdfplumber.open(str(pdf_path)) as pdf:
        for pg_idx, page in enumerate(pdf.pages):
            page_num = pg_idx + 1
            text = (page.extract_text() or '').strip()

            # If start_page specified, skip pages before it
            if start_page is not None and page_num < start_page:
                continue

            # Stop after end_page if specified
            if end_page is not None and page_num > end_page:
                break

            # Enter TC section on first page containing the header; process all
            # subsequent pages (cross-page TC sections don't repeat the header).
            if not in_tc_section:
                if 'time classification' not in text.lower():
                    continue
                in_tc_section = True

            for line in text.split('\n'):
                line = line.strip()
                if not line or line in _SKIP:
                    continue
                if _FOOTER_RE.match(line):
                    continue

                # Distance line: "1500 Meters"
                dm = DIST_RE.match(line)
                if dm:
                    cur_distance_m = int(dm.group(1))
                    continue

                # Data row: starts with rank digit, ends with time
                tokens = line.split()
                if tokens and tokens[0].isdigit() and len(tokens) >= 3:
                    last = tokens[-1]
                    if TIME_RE.match(last):
                        rank = int(tokens[0])
                        bib  = tokens[1]
                        name = ' '.join(tokens[2:-1]).replace('*', '').strip()
                        rows_out.append({
                            'division':       cur_division,
                            'distance_m':     cur_distance_m,
                            'rank':           rank,
                            'bib':            bib,
                            'skater_name':    name,
                            'best_time_text': last,
                            'best_time_seconds': _parse_time(last),
                            'page_number':    page_num,
                        })
                        continue

                # Division line (must come after distance/data checks)
                if _is_division_line(line):
                    cur_division = line
                    cur_distance_m = None  # reset distance on new division

    return rows_out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--event-id', type=int, required=True)
    ap.add_argument('--force', action='store_true',
                    help='Re-parse even if time_classification rows already exist')
    ap.add_argument('--start-page', type=int, default=None,
                    help='Treat this 1-based page number as the start of the TC section '
                         '(for PDFs with no "Time Classification" header keyword)')
    ap.add_argument('--end-page', type=int, default=None,
                    help='Stop processing TC after this 1-based page number')
    args = ap.parse_args()

    init_db()
    db = get_session()

    event = db.query(Event).filter(Event.id == args.event_id).first()
    if not event:
        print(f'ERROR: event {args.event_id} not found')
        sys.exit(1)

    print(f'Event id={event.id}: {event.event_name}')

    existing = db.query(TimeClassification).filter(
        TimeClassification.event_id == event.id
    ).count()
    if existing and not args.force:
        print(f'  Already has {existing} time_classification rows. Use --force to re-parse.')
        db.close()
        return

    if existing:
        db.query(TimeClassification).filter(TimeClassification.event_id == event.id).delete()
        db.flush()

    pdf_path = Path(event.local_path)
    if not pdf_path.exists():
        print(f'ERROR: PDF not found: {pdf_path}')
        sys.exit(1)

    rows = parse_time_classification_pages(pdf_path, start_page=args.start_page,
                                            end_page=args.end_page)

    for r in rows:
        db.add(TimeClassification(event_id=event.id, **r))

    db.commit()
    print(f'  Wrote {len(rows)} time_classification rows')

    # Summary
    from collections import Counter
    by_div = Counter((r['division'], r['distance_m']) for r in rows)
    for (div, dist), n in sorted(by_div.items(), key=lambda x: (x[0][0] or '', x[0][1] or 0)):
        print(f'    {div!r:30s} {str(dist)+"m":8s} {n} skaters')

    db.close()


if __name__ == '__main__':
    main()
