"""
Parse 2026 Jan Thaw and NEST#3 (all_races format).
File: data/pdfs/2025-2026/2026_JanThaw_Protocol.pdf

Page structure (detected dynamically by page content):
  cover / officials : skipped
  SKATER LIST       : one skater per row
  OVERALL CLASSIFICATION : one division per page
  DISTANCE CLASSIFICATION: one division+distance per page
  Time Classification     : skipped
  All Races / RESULTS     : race tables

Column x-ranges (from word-coordinate analysis across all pages):
  Skater list:
    bib 0-88 | name 88-200 | status 200-250 | gender 250-310
    | division 310-385 | club 385+

  Overall classification (positions shift per page; ranges span all 8 pages):
    rank 0-55 | bib 55-85 | name 85-185 | status 185-235
    | affiliation 235-393 | points 393-447 | cdr 447-483 | bdr 483-522 | time 522+
    (columns are right-aligned so wider values start at lower x0)

  Distance classification (bib header ~116, shifts on Super Final pages):
    rank 0-60 | points 60-112 | bib 112-150 | name 150-275
    | affiliation 275-443 | semi 443-483 | final 483-522 | time 522+
    (DNS rows: rank "0" appears at x0~87 in points range; S/F "DNS" at x0~444/484)

  Results:
    rank 0-62 | bib 62-100 | name 100-245 | affiliation 245-470
    | time 470-530 | status 530+
"""
from __future__ import annotations
import re
import sys
from datetime import timezone, datetime
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parents[1]))
from src.db.session_v2 import init_db, get_session
from src.db.models_v2 import Event, Classification, Result, SkaterEntry

PDF_PATH = Path("data/pdfs/2025-2026/2026_JanThaw_Protocol.pdf")

TIME_RE = re.compile(r'^\d+:\d{2}\.\d+$')
GROUP_RE = re.compile(r'^[A-Z]\d+$')

# Status detection for overall classification rows.
# First word of status: JR, SR, age-code (30+/35+/…), or non-standard labels.
# Second word (only after JR): a single letter A–F.
STATUS_FIRST_RE = re.compile(r'^(JR|SR|Juv|Nov|Open|\d{2}\+)$', re.IGNORECASE)
STATUS_SECOND_RE = re.compile(r'^[A-F]$')

EVENT_HDR_RE = re.compile(
    r"(?:Group\s+[A-Z]\s+)?(.+?)'s\s+(\d+)\s+(.+?)\s+Event\s+#(\d+)$"
)
HEAT_HDR_RE  = re.compile(r'^Heat\s+(\d+)\s+of\s+\d+\s+Race\s+#(\d+)$')
FINAL_HDR_RE = re.compile(r'^([A-Z])\s+Final\s+Race\s+#(\d+)$')


# ── helpers ─────────────────────────────────────────────────────────────────

def _words_to_rows(words: list[dict]) -> list[list[dict]]:
    """Group words into rows by y-coordinate (±2 pt tolerance)."""
    if not words:
        return []
    rows: list[list[dict]] = []
    cur_y = words[0]['top']
    cur_row: list[dict] = []
    for w in words:
        if abs(w['top'] - cur_y) < 3:
            cur_row.append(w)
        else:
            if cur_row:
                rows.append(sorted(cur_row, key=lambda x: x['x0']))
            cur_row = [w]
            cur_y = w['top']
    if cur_row:
        rows.append(sorted(cur_row, key=lambda x: x['x0']))
    return rows


def _col(words: list[dict], x_min: float, x_max: float = 9999) -> str:
    return ' '.join(w['text'] for w in words if x_min <= w['x0'] < x_max)


def _parse_time(text: str) -> float | None:
    if not text or not TIME_RE.match(text.strip()):
        return None
    m, s = text.strip().split(':')
    return int(m) * 60 + float(s)


def _clean_name(raw: str) -> str:
    return raw.replace('*', '').strip()


def _page_type(text: str) -> str:
    if 'SKATER LIST' in text:
        return 'skater_list'
    if 'OVERALL CLASSIFICATION' in text:
        return 'overall'
    if 'DISTANCE CLASSIFICATION' in text:
        return 'distance'
    if 'Time Classification' in text:
        return 'skip'
    if 'RESULTS' in text and 'All Races' in text:
        return 'results'
    return 'other'


# ── skater list parser ───────────────────────────────────────────────────────

def _parse_skater_list_pages(pages: list, page_offset: int) -> list[dict]:
    """Parse skater list pages (pp. 3–5). Return list of row dicts."""
    # Column x-ranges measured from word-coordinate analysis across all three pages:
    #   bib 0-88 | name 88-200 | status 200-250 | gender 250-310
    #   | division 310-385 | club 385+
    rows_out = []
    for pg_idx, page in enumerate(pages):
        page_num = page_offset + pg_idx
        words = page.extract_words()
        rows = _words_to_rows(words)
        for row in rows:
            bib      = _col(row, 0,   88)
            name_raw = _col(row, 88,  200)
            status   = _col(row, 200, 250)
            gender   = _col(row, 250, 310)
            division = _col(row, 310, 385)
            club     = _col(row, 385)

            if not bib.isdigit():
                continue

            rows_out.append({
                'bib':         bib,
                'skater_name': _clean_name(name_raw),
                'status':      status or None,
                'gender':      gender or None,
                'division':    division or None,
                'club':        club or None,
                'page_number': page_num,
            })
    return rows_out


# ── overall classification parser ────────────────────────────────────────────

def _parse_overall_page(page, page_num: int) -> tuple[str, list[dict]]:
    """Return (division, list of row dicts).

    Name/status/affiliation are identified by regex rather than fixed x-ranges
    because column positions shift across pages and between events.
    Points/CDR/BDR/time use stable right-side fixed ranges.
    """
    text = page.extract_text() or ''
    lines = [l.strip() for l in text.split('\n')]
    division = ''
    for i, ln in enumerate(lines):
        if 'OVERALL CLASSIFICATION' in ln:
            division = lines[i - 1]
            break

    words = page.extract_words()
    rows = _words_to_rows(words)
    results = []
    for row in rows:
        rank_txt   = _col(row, 0,  55)
        bib        = _col(row, 55, 85)

        if not rank_txt.isdigit() or not bib.isdigit():
            continue

        # Right-side columns: stable fixed ranges across all events/pages.
        points_txt = _col(row, 393, 447)
        cdr_txt    = _col(row, 447, 483)
        bdr_txt    = _col(row, 483, 522)
        time_txt   = _col(row, 522)

        # Middle zone (name + status + affiliation): identify status by regex.
        bib_x0 = max(w['x0'] for w in row if w['text'] == bib.split()[-1])
        mid = [w for w in row if bib_x0 < w['x0'] < 393]

        # Find first status word (JR, SR, age code, etc.)
        status_idx = next(
            (i for i, w in enumerate(mid) if STATUS_FIRST_RE.match(w['text'])), None
        )
        if status_idx is None:
            continue  # unrecognised row format

        name_raw = ' '.join(w['text'] for w in mid[:status_idx])

        # Status: first word + optional single-letter qualifier (e.g. "JR D")
        status_parts = [mid[status_idx]['text']]
        if (status_idx + 1 < len(mid)
                and STATUS_SECOND_RE.match(mid[status_idx + 1]['text'])):
            status_parts.append(mid[status_idx + 1]['text'])
        status = ' '.join(status_parts)

        # Affiliation: everything after the status token(s)
        affil_start = status_idx + len(status_parts)
        affiliation = ' '.join(w['text'] for w in mid[affil_start:])

        time_match = TIME_RE.match(time_txt)
        results.append({
            'rank': int(rank_txt),
            'bib': bib,
            'skater_name': _clean_name(name_raw),
            'skater_status': status or None,
            'affiliation': affiliation or None,
            'points': float(points_txt) if points_txt else None,
            'cdr': int(cdr_txt) if cdr_txt.isdigit() else None,
            'bdr': int(bdr_txt) if bdr_txt.isdigit() else None,
            'best_time_text':    time_txt if time_match else None,
            'best_time_seconds': _parse_time(time_txt) if time_match else None,
            'page_number': page_num,
        })
    return division, results


# ── distance classification parser ───────────────────────────────────────────

def _parse_distance_page(page, page_num: int) -> tuple[str, int | None, list[dict]]:
    """Return (division, distance_m, list of row dicts)."""
    text = page.extract_text() or ''
    lines = [l.strip() for l in text.split('\n')]
    division, distance_m = '', None
    for i, ln in enumerate(lines):
        if 'DISTANCE CLASSIFICATION' in ln:
            header = lines[i - 1]   # e.g. "NEST Women's 1000" or "Group 2's 500 Super Final"
            # Extract division and distance; ignore any suffix after the distance number
            m = re.match(r"(.+?)'s\s+(\d+)", header)
            if m:
                division   = m.group(1)
                distance_m = int(m.group(2))
            break

    words = page.extract_words()
    rows = _words_to_rows(words)
    results = []
    for row in rows:
        rank_txt    = _col(row, 0,   60)
        points_txt  = _col(row, 60,  112).replace(',', '')
        bib         = _col(row, 112, 150)
        name_raw    = _col(row, 150, 275)
        affiliation = _col(row, 275, 443)
        semi        = _col(row, 443, 483)
        final       = _col(row, 483, 522)
        time_txt    = _col(row, 522)

        if not bib.isdigit():
            continue

        if rank_txt.isdigit():
            rank = int(rank_txt)
        else:
            # DNS rows: rank "0" appears at x0~87 (in points range), S/F show "DNS"
            row_text = ' '.join(w['text'] for w in row)
            if 'DNS' not in row_text:
                continue
            rank = 0
            points_txt = ''  # the "0" at x0~87 is a rank marker, not points

        time_secs = _parse_time(time_txt) if TIME_RE.match(time_txt) else None
        results.append({
            'rank': rank,
            'bib': bib,
            'skater_name': _clean_name(name_raw),
            'affiliation': affiliation or None,
            'points': float(points_txt) if points_txt else None,
            'semi_group': semi or None,
            'final_group': final or None,
            'best_time_text': time_txt if time_txt else None,
            'best_time_seconds': time_secs,
            'page_number': page_num,
        })
    return division, distance_m, results


# ── results parser ────────────────────────────────────────────────────────────

def _parse_results_pages(pages: list, page_offset: int) -> list[dict]:
    """Parse all results pages and return list of result row dicts."""
    rows_out = []
    cur_division = cur_distance_m = cur_round_type = cur_event_num = None
    cur_round_label = cur_heat = cur_group = cur_race_num = None

    for pg_idx, page in enumerate(pages):
        page_num = page_offset + pg_idx
        words = page.extract_words()
        rows = _words_to_rows(words)

        for row in rows:
            line = ' '.join(w['text'] for w in row)

            # ── event header ────────────────────────────────────────────────
            m = EVENT_HDR_RE.match(line)
            if m:
                div_raw, dist_str, round_str, ev_num = m.groups()
                cur_division   = re.sub(r"'s$", '', div_raw.strip())
                cur_distance_m = int(dist_str)
                cur_round_type = round_str.strip()
                cur_event_num  = int(ev_num)
                cur_heat = cur_group = cur_race_num = None
                cur_round_label = None
                continue

            # ── race header (heat) ───────────────────────────────────────────
            m2 = HEAT_HDR_RE.match(line)
            if m2:
                cur_heat      = int(m2.group(1))
                cur_race_num  = int(m2.group(2))
                cur_group     = None
                cur_round_label = f"Heat {cur_heat}"
                continue

            # ── race header (final) ──────────────────────────────────────────
            m3 = FINAL_HDR_RE.match(line)
            if m3:
                cur_group     = m3.group(1)
                cur_race_num  = int(m3.group(2))
                cur_heat      = None
                cur_round_label = f"{cur_group} Final"
                continue

            # ── skip non-data lines ──────────────────────────────────────────
            if not cur_division:
                continue

            rank_txt    = _col(row, 0, 62)
            bib         = _col(row, 62, 100)
            name_raw    = _col(row, 100, 245)
            affiliation = _col(row, 245, 470)
            time_txt    = _col(row, 470, 530)
            status      = _col(row, 530)

            if not bib.isdigit():
                continue
            if not name_raw:
                continue

            # DNS/PEN rows have no rank (bib sits at ~70, in the "bib" slot)
            has_rank = rank_txt.isdigit()
            rank = int(rank_txt) if has_rank else None

            time_secs = _parse_time(time_txt) if TIME_RE.match(time_txt or '') else None
            # For DNS/PEN/FNT rows the status may be in the time column
            if not time_secs and time_txt and not status:
                status = time_txt
                time_txt = None

            rows_out.append({
                'division':     cur_division,
                'distance_m':   cur_distance_m,
                'round_type':   cur_round_type,
                'round_label':  cur_round_label,
                'race_number':  cur_race_num,
                'event_number': cur_event_num,
                'heat':         cur_heat,
                'group_label':  cur_group,
                'rank':         rank,
                'bib':          bib,
                'skater_name':  _clean_name(name_raw),
                'affiliation':  affiliation or None,
                'time_text':    time_txt or None,
                'time_seconds': time_secs,
                'status':       status or None,
                'page_number':  page_num,
            })

    return rows_out


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    init_db()
    db = get_session()

    event = db.query(Event).filter(Event.local_path == str(PDF_PATH.resolve())).first()
    if not event:
        print(f"ERROR: event not found for path {PDF_PATH}")
        return

    print(f"Event id={event.id}: {event.event_name}")

    # Clear existing rows for this event (idempotent)
    db.query(SkaterEntry).filter(SkaterEntry.event_id == event.id).delete()
    db.query(Classification).filter(Classification.event_id == event.id).delete()
    db.query(Result).filter(Result.event_id == event.id).delete()
    db.flush()

    with pdfplumber.open(PDF_PATH) as pdf:
        pages = pdf.pages

        skater_pages, result_pages = [], []
        skater_offset = result_offset = None
        skater_entry_rows = overall_rows = distance_rows = 0

        for pg_idx, page in enumerate(pages):
            page_num = pg_idx + 1
            text = page.extract_text() or ''
            ptype = _page_type(text)

            if ptype == 'skater_list':
                if skater_offset is None:
                    skater_offset = page_num
                skater_pages.append(page)

            elif ptype == 'overall':
                division, rows = _parse_overall_page(page, page_num)
                for r in rows:
                    db.add(Classification(
                        event_id=event.id, section_type='overall',
                        division=division, distance_m=None, **r,
                    ))
                    overall_rows += 1

            elif ptype == 'distance':
                division, distance_m, rows = _parse_distance_page(page, page_num)
                for r in rows:
                    db.add(Classification(
                        event_id=event.id, section_type='distance',
                        division=division, distance_m=distance_m, **r,
                    ))
                    distance_rows += 1

            elif ptype == 'results':
                if result_offset is None:
                    result_offset = page_num
                result_pages.append(page)

        # Parse skater list
        if skater_pages:
            for r in _parse_skater_list_pages(skater_pages, skater_offset):
                db.add(SkaterEntry(event_id=event.id, **r))
                skater_entry_rows += 1

        # Parse results
        result_rows = 0
        if result_pages:
            for r in _parse_results_pages(result_pages, result_offset):
                r.pop('event_number', None)  # not a DB column
                db.add(Result(event_id=event.id, **r))
                result_rows += 1

        event.parsed_at = datetime.now(timezone.utc).isoformat()
        db.commit()

    print(f"  Skater list rows            : {skater_entry_rows}")
    print(f"  Overall classification rows : {overall_rows}")
    print(f"  Distance classification rows: {distance_rows}")
    print(f"  Result rows                 : {result_rows}")
    db.close()


if __name__ == '__main__':
    main()
