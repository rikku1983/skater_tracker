"""
Parser for USS protocol PDFs using the per-page-title format.

Each results page has the section context in the page header:
    Line 1: event title
    Line 2: "Division's Distance Round-Type"
    Line 3: "RESULTS"

Heat headers:  "Heat N of M Race #NNN"
Final headers: "X Final Race #NNN"
Result rows:   rank  bib  name...  affiliation  time  [status]

Used for: Ohio Invitational & Heartland #3, Ohio State Open Meet, OFC Protocol

Usage:
    python scripts/parse_ohio_heartland.py --pdf-path PATH [--force]
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parents[1]))

from scripts.parse_all_races_format import (
    _words_to_rows, _parse_time, _clean_name, _split_name_affil,
    _parse_overall_page, _parse_distance_page,
    HEAT_HDR_RE, FINAL_HDR_RE, RESULT_STATUS_RE, TIME_RE,
)
from scripts.fill_missing_race_context import fill_missing_race_context
from src.db.session_v2 import init_db, get_session
from src.db.models_v2 import Event, Classification, Result, SkaterEntry

PDF_PATH = Path('data/pdfs/2025-2026/2025_Ohio_Invitational_and_Heartland_3update.pdf')

# ── regexes ───────────────────────────────────────────────────────────────────

PAGE_HDR_RE = re.compile(
    r"(.+?)'s?\s+(\d+(?:\s+\(#\d+\))?)\s+"
    r"((?:Super\s+Final\s+)?(?:Quarter-?|Semi-?)?Finals?|Heats?|Super\s+Final)",
    re.I,
)
EVENT_NUM_RE = re.compile(r'Event\s+#(\d+)', re.I)

_LAPS = {
    111: 1.0, 222: 2.0, 333: 3.0, 444: 4.0, 500: 4.5,
    777: 7.0, 1000: 9.0, 1500: 13.5, 2000: 18.0, 3000: 27.0,
    43: 0.5, 85: 1.0, 128: 1.5, 170: 2.0, 212: 2.5, 214: 2.5,
    255: 3.0, 340: 4.0, 425: 5.0, 428: 5.0,
    55: 1.0, 166: 3.0, 107: 1.0, 267: 2.5,
}


def _norm_round_type(s: str) -> str:
    s = s.lower()
    if 'super' in s:
        return 'SUPER FINAL'
    if 'quarter' in s or 'semi' in s or 'heat' in s:
        return 'HEATS'
    if 'final' in s:
        return 'FINAL'
    return 'HEATS'


def _page_category(text: str) -> str:
    if 'RESULTS' in text:
        return 'results'
    if 'OVERALL CLASSIFICATION' in text:
        return 'overall'
    if 'DISTANCE CLASSIFICATION' in text:
        return 'distance'
    if 'Time Classification' in text:
        return 'skip'
    return 'other'


# ── results page parser ───────────────────────────────────────────────────────

def _parse_results_page(page, page_num: int) -> list[dict]:
    """Parse one results page. Returns list of result row dicts."""
    text = page.extract_text() or ''
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # Section header is typically on line 2, but some formats prepend a timestamp
    # and competition title, pushing it to line 4. Try lines 1-4.
    m = None
    hdr_line = ''
    for idx in range(1, min(5, len(lines))):
        m = PAGE_HDR_RE.match(lines[idx])
        if m:
            hdr_line = lines[idx]
            break

    # If no match but a "Group A Picking" header is present, store with NULL
    # division/distance so fill_missing_race_context can fill it later.
    _group_pick = None
    if not m:
        for idx in range(1, min(5, len(lines))):
            if re.match(r'^Group\s+[A-Z]\s+Picking\b', lines[idx], re.I):
                _group_pick = lines[idx]
                break
        if not _group_pick:
            return []

    if m:
        div_raw   = m.group(1)
        dist_str  = m.group(2)
        round_str = m.group(3)
        division   = re.sub(r"'s?$", '', div_raw.strip(), flags=re.I)
        dist_num   = re.match(r'(\d+)', dist_str)
        distance_m = int(dist_num.group(1)) if dist_num else None
        round_type = _norm_round_type(round_str)
    else:
        # Group A Picking — division/distance unknown; fill_missing_race_context fills later
        division = distance_m = None
        round_type = 'HEATS'

    rows_out = []
    cur = dict(heat=None, group_label=None, race_num=None, round_label=None)

    for row in _words_to_rows(page.extract_words()):
        line = ' '.join(w['text'] for w in row)
        texts = [w['text'] for w in row]

        # heat header
        m2 = HEAT_HDR_RE.match(line)
        if m2:
            cur['heat']        = int(m2.group(1))
            cur['race_num']    = int(m2.group(2))
            cur['group_label'] = None
            cur['round_label'] = f"Heat {cur['heat']}"
            continue

        # final header
        m3 = FINAL_HDR_RE.match(line)
        if m3:
            cur['group_label'] = m3.group(1)
            cur['race_num']    = int(m3.group(2))
            cur['heat']        = None
            cur['round_label'] = f"{cur['group_label']} Final"
            continue

        # data row: must start with a digit (rank)
        if not texts or not texts[0].isdigit():
            continue

        time_indices = [i for i, t in enumerate(texts) if TIME_RE.match(t)]

        if time_indices:
            ti       = time_indices[-1]
            time_txt = texts[ti]
            status   = texts[ti + 1] if ti + 1 < len(texts) else None
            if status and not RESULT_STATUS_RE.match(status):
                status = None
            before = row[:ti]
            bt = [w['text'] for w in before]
            if len(bt) >= 2 and bt[0].isdigit() and bt[1].isdigit():
                rank, bib = int(bt[0]), bt[1]
                mid_words = before[2:]
            elif len(bt) >= 1 and bt[0].isdigit():
                rank, bib = None, bt[0]
                mid_words = before[1:]
            else:
                continue
        else:
            status = texts[-1] if texts and RESULT_STATUS_RE.match(texts[-1]) else None
            if not status:
                continue
            if len(texts) >= 2 and texts[0].isdigit() and texts[1].isdigit():
                rank, bib = int(texts[0]), texts[1]
                mid_words = row[2:-1]
            else:
                rank, bib = None, texts[0]
                mid_words = row[1:-1]
            time_txt = None

        name, _, affil = _split_name_affil(mid_words)
        time_secs = _parse_time(time_txt)

        if not time_secs and time_txt and not status:
            status   = time_txt
            time_txt = None

        rows_out.append({
            'division':    division,
            'distance_m':  distance_m,
            'laps':        _LAPS.get(distance_m),
            'round_type':  round_type,
            'round_label': cur['round_label'],
            'race_number': cur['race_num'],
            'heat':        cur['heat'],
            'group_label': cur['group_label'],
            'rank':        rank,
            'bib':         bib,
            'skater_name': name,
            'affiliation': affil or None,
            'time_text':   time_txt or None,
            'time_seconds': time_secs,
            'status':      status or None,
            'page_number': page_num,
        })

    return rows_out


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf-path', default=str(PDF_PATH),
                        help='Path to PDF (default: Ohio Heartland 2025)')
    parser.add_argument('--force', action='store_true',
                        help='Re-parse even if already parsed')
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path).resolve()
    if not pdf_path.exists():
        print(f"ERROR: file not found: {pdf_path}")
        sys.exit(1)

    init_db()
    db = get_session()

    event = db.query(Event).filter(Event.local_path == str(pdf_path)).first()
    if not event:
        print(f"ERROR: event not found in DB for path {pdf_path}")
        sys.exit(1)

    print(f"Event id={event.id}: {event.event_name}")

    if event.parsed_at and not args.force:
        print(f"Already parsed at {event.parsed_at}. Use --force to re-parse.")
        db.close()
        return

    db.query(SkaterEntry).filter(SkaterEntry.event_id == event.id).delete()
    db.query(Classification).filter(Classification.event_id == event.id).delete()
    db.query(Result).filter(Result.event_id == event.id).delete()
    db.flush()

    overall_rows = distance_rows = result_rows = 0

    with pdfplumber.open(str(pdf_path)) as pdf:
        for pg_idx, page in enumerate(pdf.pages):
            page_num = pg_idx + 1
            text = page.extract_text() or ''
            cat  = _page_category(text)

            if cat == 'overall':
                division, rows = _parse_overall_page(page, page_num)
                for r in rows:
                    db.add(Classification(
                        event_id=event.id, section_type='overall',
                        division=division, distance_m=None, **r,
                    ))
                    overall_rows += 1

            elif cat == 'distance':
                division, distance_m, rows = _parse_distance_page(page, page_num)
                for r in rows:
                    db.add(Classification(
                        event_id=event.id, section_type='distance',
                        division=division, distance_m=distance_m, **r,
                    ))
                    distance_rows += 1

            elif cat == 'results':
                for r in _parse_results_page(page, page_num):
                    db.add(Result(event_id=event.id,
                                  is_relay='relay' in (r.get('division') or '').lower(),
                                  **r))
                    result_rows += 1

    db.flush()
    fill_summary = fill_missing_race_context(event.id, db, verbose=True)
    db.flush()
    event.parsed_at    = datetime.now(timezone.utc).isoformat()
    event.parse_errors = None
    db.commit()
    filled = fill_summary['filled']
    print(f'  Group A heats filled        : {filled} races resolved')
    if fill_summary['div_only'] or fill_summary['unknown']:
        div_only = fill_summary['div_only']
        unknown = fill_summary['unknown']
        print(f'  Unresolved                  : {div_only} div-only, {unknown} unknown')
    for _, msg in fill_summary['review_items']:
        print(f'    {msg}')

    print(f"  Overall classification rows : {overall_rows}")
    print(f"  Distance classification rows: {distance_rows}")
    print(f"  Result rows                 : {result_rows}")
    db.close()


if __name__ == '__main__':
    main()
