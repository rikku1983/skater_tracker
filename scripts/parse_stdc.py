"""
Parser for 2025 Desert Classic Short Track (STDC) PDF.

Page layout (as given):
  pages  3–8  : skater list
  pages  9–40 : overall + distance classification
  pages 41–54 : time classification (skip)
  pages 55–153: results
  pages 154–157: time classification (skip)

Titles are image-based on many pages; tables are always text-based.

Detection strategy per page:
  - 'Skater List' in text                       → skater_list
  - first text line == 'Event List', words ≥ 80 → overall classification
  - first line matches "Division's Distance"     → distance classification
  - any line matches EVENT_HDR_RE               → results
  - 'Time Classification' in text               → skip
  - otherwise (legend pages, footers, etc.)     → skip

Usage:
    python scripts/parse_stdc.py [--force]
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
    _largest_gap_split,
    _parse_overall_page,
    _parse_distance_page,
    _parse_results_pages,
    EVENT_HDR_RE, HEAT_HDR_RE, FINAL_HDR_RE,
    RESULT_STATUS_RE, TIME_RE, GENDER_RE, STATUS_FIRST_RE, STATUS_SECOND_RE,
)
from src.db.session_v2 import init_db, get_session
from src.db.models_v2 import Event, Classification, Result, SkaterEntry

PDF_PATH = Path('data/pdfs/2025-2026/2025_STDC_updated_1014.pdf')

_DIV_DIST_RE = re.compile(
    r"(.+?)'s?\s+(\d+(?:\s+\(#\d+\))?)\s*$",
    re.I,
)

_FOOTER_RE = re.compile(
    r'Tempus Competition Software.*|^\s*Page \d+ of \d+\s*$',
    re.MULTILINE,
)

_LAPS = {
    111: 1.0, 222: 2.0, 333: 3.0, 444: 4.0, 500: 4.5,
    777: 7.0, 1000: 9.0, 1500: 13.5,
}


def _content_hash(text: str) -> int:
    return hash(_FOOTER_RE.sub('', text))


def _page_category(text: str, words: list) -> str:
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if not lines:
        return 'skip'
    first = lines[0]

    if 'Skater List' in text:
        return 'skater_list'
    if 'Time Classification' in text:
        return 'skip'
    if len(words) < 80:
        return 'skip'   # legend / footer-only pages
    if first == 'Event List' or 'OVERALL CLASSIFICATION' in text:
        return 'overall'
    if any(_DIV_DIST_RE.match(l) for l in lines[:3]):
        return 'distance'
    if any(EVENT_HDR_RE.match(l) for l in lines):
        return 'results'
    return 'skip'


def _parse_skater_list_stdc(pages: list, page_offset: int) -> list[dict]:
    """Skater list parser for STDC: uses header-row x-positions to locate the
    Division / Club column boundary rather than relying on gap detection.

    The header row "# Name Gender Division Club" tells us exactly where 'Club'
    starts — everything to the left (after Gender) is Division.
    """
    rows_out = []
    for pg_idx, page in enumerate(pages):
        page_num = page_offset + pg_idx
        word_rows = _words_to_rows(page.extract_words())

        # Find the header row and record x0 of the 'Club' column
        club_x0 = None
        for row in word_rows:
            texts = [w['text'] for w in row]
            if texts == ['#', 'Name', 'Gender', 'Division', 'Club']:
                club_word = next(w for w in row if w['text'] == 'Club')
                club_x0 = club_word['x0']
                break

        for row in word_rows:
            texts = [w['text'] for w in row]
            if not texts or not texts[0].isdigit():
                continue

            bib = texts[0]
            gender_idx = next(
                (i for i, t in enumerate(texts[1:], 1) if GENDER_RE.match(t)), None
            )
            if gender_idx is None:
                continue

            # Status between bib and gender (optional)
            status_idx = next(
                (i for i, t in enumerate(texts[1:gender_idx], 1)
                 if STATUS_FIRST_RE.match(t)), None
            )
            if status_idx is not None:
                name_raw = ' '.join(texts[1:status_idx])
                status_parts = [texts[status_idx]]
                if status_idx + 1 < gender_idx and STATUS_SECOND_RE.match(texts[status_idx + 1]):
                    status_parts.append(texts[status_idx + 1])
                status = ' '.join(status_parts)
            else:
                name_raw = ' '.join(texts[1:gender_idx])
                status = None

            gender = texts[gender_idx]
            post_gender = [w for w in row if w['x0'] > row[gender_idx]['x0']]

            if club_x0 is not None:
                # Data club words start ~1px left of the header 'Club' label;
                # subtract 5px tolerance so they're correctly classified as club.
                threshold  = club_x0 - 5
                div_words  = [w for w in post_gender if w['x0'] < threshold]
                club_words = [w for w in post_gender if w['x0'] >= threshold]
                division = _clean_name(' '.join(w['text'] for w in div_words))
                club     = ' '.join(w['text'] for w in club_words) or None
            else:
                division, club = _largest_gap_split(post_gender)

            rows_out.append({
                'bib':         bib,
                'skater_name': _clean_name(name_raw),
                'status':      status or None,
                'gender':      gender,
                'division':    division or None,
                'club':        club or None,
                'page_number': page_num,
            })
    return rows_out


_GROUP_RE   = re.compile(r'^[A-Z]\d+$')
_ROUND_CODES = frozenset({'DNS', 'DNF', 'PEN', 'DQ', 'DSQ', 'FNT', 'ADV', 'Q', 'PB'})
# Codes that can appear in Q/S/F group columns
_SEMI_RE    = re.compile(r'^(\d+|[A-Z]\d+|DNS|DNF|PEN|DQ|DSQ|FNT|ADV|Q)$')
_NORM_ROUND = {
    'quarter-finals': 'HEATS', 'quarter finals': 'HEATS',
    'semi-finals': 'HEATS', 'semi finals': 'HEATS',
    'finals': 'FINAL', 'final': 'FINAL',
    'super final': 'SUPER FINAL', 'super final finals': 'SUPER FINAL',
}


def _norm_round(s: str) -> str:
    # Strip leading "(#2)" or similar qualifiers
    s = re.sub(r'^\(#\d+\)\s*', '', s).lower().strip()
    return _NORM_ROUND.get(s, s.upper())


def _parse_distance_page_stdc(page, page_num: int):
    """Distance classification parser for STDC format.

    Pages start with "Division's Distance" header text.
    Column order: rank | points | bib | name... | affil | Q | S | F | time
    Three group columns (Q/S/F) instead of the standard two (S/F).
    """
    text = page.extract_text() or ''
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # Header may be on first or second line (some PDFs prepend a competition title)
    m = None
    for l in lines[:3]:
        m = _DIV_DIST_RE.match(l)
        if m:
            break
    if not m:
        return '', None, []

    div_raw  = m.group(1)
    dist_str = m.group(2)
    division = re.sub(r"'s?$", '', div_raw.strip(), flags=re.I)
    dist_num = re.match(r'(\d+)', dist_str)
    distance_m = int(dist_num.group(1)) if dist_num else None

    rows_out = []
    for row in _words_to_rows(page.extract_words()):
        texts = [w['text'] for w in row]
        if len(texts) < 6 or not texts[0].isdigit():
            continue

        rank = int(texts[0])
        if not texts[1].replace(',', '').isdigit():
            continue
        points = float(texts[1].replace(',', ''))
        if not texts[2].isdigit():
            continue
        bib = texts[2]

        j = len(texts) - 1
        # time
        time_txt = None
        if TIME_RE.match(texts[j]):
            time_txt = texts[j]; j -= 1
        # F group
        final_group = semi_group = None
        if j >= 3 and (_GROUP_RE.match(texts[j]) or texts[j] in _ROUND_CODES):
            final_group = texts[j]; j -= 1
        # S group
        if j >= 3 and _SEMI_RE.match(texts[j]):
            semi_group = texts[j]; j -= 1
        # Q group (strip silently)
        if j >= 3 and _SEMI_RE.match(texts[j]):
            j -= 1

        mid_words = row[3:j + 1]
        name, _, affil = _split_name_affil(mid_words)

        time_ok = bool(time_txt and TIME_RE.match(time_txt))
        rows_out.append({
            'rank':              rank,
            'bib':               bib,
            'skater_name':       name,
            'affiliation':       affil or None,
            'points':            points,
            'semi_group':        semi_group or None,
            'final_group':       final_group or None,
            'best_time_text':    time_txt if time_ok else None,
            'best_time_seconds': _parse_time(time_txt) if time_ok else None,
            'page_number':       page_num,
        })
    return division, distance_m, rows_out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--pdf-path', default=None, help='Path to PDF (default: 2025 STDC)')
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path).resolve() if args.pdf_path else PDF_PATH.resolve()
    if not pdf_path.exists():
        print(f"ERROR: file not found: {pdf_path}")
        sys.exit(1)

    init_db()
    db = get_session()

    event = db.query(Event).filter(Event.local_path == str(pdf_path)).first()
    if not event:
        print(f"ERROR: event not found in DB for {pdf_path}")
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

    skater_pages, result_pages = [], []
    skater_offset = None
    skater_rows = overall_rows = distance_rows = result_rows = 0
    seen_hashes: set[int] = set()

    with pdfplumber.open(str(pdf_path)) as pdf:
        for pg_idx, page in enumerate(pdf.pages):
            page_num = pg_idx + 1
            text  = page.extract_text() or ''
            words = page.extract_words()
            cat   = _page_category(text, words)

            if cat == 'skater_list':
                if 'FIRST NAME' in text or 'LAST NAME' in text:
                    continue
                if skater_offset is None:
                    skater_offset = page_num
                skater_pages.append(page)

            elif cat == 'overall':
                division, rows = _parse_overall_page(page, page_num)
                for r in rows:
                    db.add(Classification(
                        event_id=event.id, section_type='overall',
                        division=division, distance_m=None, **r,
                    ))
                    overall_rows += 1

            elif cat == 'distance':
                division, distance_m, rows = _parse_distance_page_stdc(page, page_num)
                for r in rows:
                    db.add(Classification(
                        event_id=event.id, section_type='distance',
                        division=division, distance_m=distance_m, **r,
                    ))
                    distance_rows += 1

            elif cat == 'results':
                h = _content_hash(text)
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
                result_pages.append((page_num, page))

        if skater_pages:
            for r in _parse_skater_list_stdc(skater_pages, skater_offset):
                db.add(SkaterEntry(event_id=event.id, **r))
                skater_rows += 1

        if result_pages:
            for r in _parse_results_pages(result_pages):
                r.pop('event_number', None)
                r['round_type'] = _norm_round(r.get('round_type') or '')
                db.add(Result(
                    event_id = event.id,
                    laps     = _LAPS.get(r.get('distance_m')),
                    is_relay = 'relay' in (r.get('division') or '').lower(),
                    **r,
                ))
                result_rows += 1

    event.parsed_at    = datetime.now(timezone.utc).isoformat()
    event.parse_errors = None
    db.commit()

    print(f"  Skater list rows            : {skater_rows}")
    print(f"  Overall classification rows : {overall_rows}")
    print(f"  Distance classification rows: {distance_rows}")
    print(f"  Result rows                 : {result_rows}")
    db.close()


if __name__ == '__main__':
    main()
