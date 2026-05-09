"""
OCR-based parser for image-based Tempus "All Races" format PDFs.

Some PDFs (e.g. 2025_OFC_Protocol.pdf) use the standard Tempus all-races layout but are
rendered as rasterized images — pdfplumber extracts no text.  This script OCRs each page
at 300 dpi with pytesseract, builds per-word bounding-box dicts (same format as
pdfplumber.extract_words()), and passes them through the existing gap-split helpers from
parse_all_races_format.py unchanged.

The only difference from the text-based parser is the section-header regex: OFC-format
headers look like "Group A Challenge C's 333 Semi-Finals" whereas the text-based parser
expects "... Event #N" at the end.

Usage:
    python scripts/parse_ocr_all_races.py --pdf-path PATH/TO/FILE.pdf [--force]
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber
import pytesseract
from pytesseract import Output

sys.path.insert(0, str(Path(__file__).parents[1]))

# Reuse helpers from the text-based parser
from scripts.fill_missing_race_context import fill_missing_race_context
from scripts.parse_all_races_format import (
    _words_to_rows,
    _parse_time,
    _clean_name,
    _page_type,
    _largest_gap_split,
    _split_name_affil,
    _parse_skater_list_pages,
    _parse_overall_page,
    _parse_distance_page,
    HEAT_HDR_RE,
    FINAL_HDR_RE,
    RESULT_STATUS_RE,
    TIME_RE,
)
from src.db.session_v2 import init_db, get_session
from src.db.models_v2 import Event, Classification, Result, SkaterEntry

# ── section header regex for OFC / image all-races format ────────────────────
# "Group A Challenge C's 333 Semi-Finals"  →  div="Challenge C", dist=333, round="Semi-Finals"
# "Challenge A's 500 Semi-Finals"          →  div="Challenge A", dist=500, round="Semi-Finals"
SECTION_HDR_RE = re.compile(
    r"(?:Group\s+[A-Z]\s+)?(.+?)['’]s?\s+(\d+(?:\s+\(#\d+\))?)\s+"
    r"((?:Super\s+Final\s+)?(?:Semi-?)?Finals?|Heats?|Semi-Finals?)",
    re.I,
)

# lap lookup (same as parse_all_races_format)
_LAPS = {
    111: 1.0, 222: 2.0, 333: 3.0, 444: 4.0, 500: 4.5,
    777: 7.0, 1000: 9.0, 1500: 13.5,
    43: 0.5, 85: 1.0, 128: 1.5, 170: 2.0, 212: 2.5, 214: 2.5,
    255: 3.0, 340: 4.0, 425: 5.0, 428: 5.0,
    55: 1.0, 166: 3.0, 107: 1.0, 267: 2.5,
}

def _ocr_page_type(text: str) -> str:
    """Like _page_type but catches results pages even when OCR garbles 'RESULTS'/'All Races'.

    Classification/skater keywords take priority (checked first by _page_type).
    'Other' pages that contain a heat/final race header are reclassified as results.
    This handles image PDFs where bold 'RESULTS' / 'All Races' OCR poorly.
    """
    ptype = _page_type(text)
    if ptype == 'other' and re.search(r'\bRace\s*#\d+', text):
        return 'results'
    return ptype


# round-type normalisation
def _norm_round_type(round_str: str) -> str:
    s = round_str.lower().strip()
    if 'super' in s:
        return 'SUPER FINAL'
    if 'semi' in s or 'heat' in s:
        return 'HEATS'
    if 'final' in s:
        return 'FINAL'
    return 'HEATS'


# ── OCR helpers ───────────────────────────────────────────────────────────────

def _ocr_page_words(page) -> list[dict]:
    """Render a pdfplumber page to 300 dpi and OCR it.

    Returns word dicts with x0/top/x1/bottom keys — same format as
    pdfplumber.extract_words().

    Uses pytesseract's own (block_num, par_num, line_num) grouping to assign each
    word a shared top value equal to the minimum top in its line group.  This ensures
    all words on the same visual row get identical top values and are correctly grouped
    by _words_to_rows (which uses a 3-unit tolerance designed for point coords).
    """
    from collections import defaultdict

    img  = page.to_image(resolution=300).original
    data = pytesseract.image_to_data(img, output_type=Output.DICT, config='--psm 6')

    # Group by tesseract's own line identity
    lines: dict = defaultdict(list)
    for i in range(len(data['text'])):
        text = data['text'][i].strip()
        # Fix OCR comma artifacts in time values (e.g. "0:44,564" or "2:35,.260")
        # Do NOT replace commas in points like "10,000" — they use comma as thousands sep
        if re.match(r'^\d+:\d{2}', text):
            # Normalize OCR artifacts in time strings:
            #   "0:44,564"  → "0:44.564"  (comma-as-decimal → period)
            #   "2:35,.260" → "2:35.260"  (comma+period artifact → period)
            #   "1:55..223" → "1:55.223"  (double-dot → single dot)
            text = re.sub(r',(\d)', r'.\1', text)  # comma-decimal → period
            text = text.replace(',', '')            # drop any remaining commas
            text = re.sub(r'\.{2,}', '.', text)    # collapse double-dots
        if not text or int(data['conf'][i]) < 10:
            continue
        key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
        lines[key].append({
            'text':   text,
            'x0':     float(data['left'][i]),
            'top':    float(data['top'][i]),
            'x1':     float(data['left'][i] + data['width'][i]),
            'height': float(data['height'][i]),
        })

    # Assign each group the minimum top in the group so _words_to_rows groups them
    words = []
    for key in sorted(lines):
        group = lines[key]
        group_top = min(w['top'] for w in group)
        for w in sorted(group, key=lambda x: x['x0']):
            words.append({
                'text':   w['text'],
                'x0':     w['x0'],
                'top':    group_top,
                'x1':     w['x1'],
                'bottom': group_top + w['height'],
            })
    return words


class OcrPage:
    """Thin wrapper so the existing page-based helpers work with OCR output."""

    def __init__(self, words: list[dict]):
        self._words = words
        # Build extract_text() equivalent by grouping words into rows
        rows = _words_to_rows(words)
        self._text = '\n'.join(' '.join(w['text'] for w in row) for row in rows)

    def extract_words(self) -> list[dict]:
        return self._words

    def extract_text(self) -> str:
        return self._text


# ── results parser (OCR variant) ─────────────────────────────────────────────

def _parse_results_pages_ocr(pages: list) -> list[dict]:
    """Like parse_all_races_format._parse_results_pages but uses SECTION_HDR_RE
    instead of EVENT_HDR_RE for section headers (OFC all-races image format)."""
    rows_out = []
    cur = dict(division=None, distance_m=None, round_type=None, round_label=None,
               heat=None, group_label=None, race_num=None)

    for page_num, page in pages:
        for row in _words_to_rows(page.extract_words()):
            line = ' '.join(w['text'] for w in row)

            # Group A/B Picking or bare "Picking N+(M) Event #N" — no division/distance info
            # OCR may merge "GroupA" (no space) so also match that variant
            if re.match(r'^Group\s*[A-Z]\s*Picking\b|^Picking\b', line, re.I):
                cur['division'] = cur['distance_m'] = None
                cur['round_type'] = 'HEATS'
                cur['race_num'] = None  # clear until Heat header fires
                continue

            # section header: "Group A Challenge C's 333 Semi-Finals"
            m = SECTION_HDR_RE.match(line)
            if m:
                div_raw, dist_str, round_str = m.group(1), m.group(2), m.group(3)
                cur['division']   = re.sub(r"'s$", '', div_raw.strip(), flags=re.I)
                # dist_str may contain "(#2)" qualifier — extract the leading number
                dist_num = re.match(r'(\d+)', dist_str)
                cur['distance_m'] = int(dist_num.group(1)) if dist_num else None
                cur['round_type'] = _norm_round_type(round_str)
                cur['round_label'] = round_str.strip()
                cur['heat'] = cur['group_label'] = cur['race_num'] = None
                continue

            # heat header: "Heat 1 of 4 Race #301"
            m2 = HEAT_HDR_RE.match(line)
            if m2:
                cur['heat']        = int(m2.group(1))
                cur['race_num']    = int(m2.group(2))
                cur['group_label'] = None
                # round_type intentionally NOT overridden — inherited from section header
                cur['round_label'] = f"Heat {cur['heat']}"
                continue

            # final header: "A Final Race #1104"
            m3 = FINAL_HDR_RE.match(line)
            if m3:
                cur['group_label'] = m3.group(1)
                cur['race_num']    = int(m3.group(2))
                cur['heat']        = None
                # round_type intentionally NOT overridden — inherited from section header
                cur['round_label'] = f"{cur['group_label']} Final"
                continue

            if cur['race_num'] is None:
                continue

            # data row
            texts = [w['text'] for w in row]
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
                bt     = [w['text'] for w in before]
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
                'division':    cur['division'],
                'distance_m':  cur['distance_m'],
                'round_type':  cur['round_type'],
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
    parser.add_argument('--pdf-path', required=True)
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

    import re as _re
    _FOOTER_RE = _re.compile(
        r'Tempus Competition Software.*|^\s*Page \d+ of \d+\s*$', _re.MULTILINE
    )
    def _content_hash(text: str) -> int:
        return hash(_FOOTER_RE.sub('', text))

    skater_pages, result_pages = [], []
    skater_offset = None
    skater_rows = overall_rows = distance_rows = result_rows = 0
    seen_hashes: set[int] = set()

    with pdfplumber.open(str(pdf_path)) as pdf:
        for pg_idx, raw_page in enumerate(pdf.pages):
            page_num = pg_idx + 1

            # Try native text first; fall back to OCR if short or font-garbled (cid: sequences)
            native_text = (raw_page.extract_text() or '').strip()
            is_garbled  = native_text.count('(cid:') > 5
            if len(native_text) >= 50 and not is_garbled:
                page      = raw_page
                page_text = native_text
            else:
                print(f"  OCR page {page_num}...", end='\r', flush=True)
                words     = _ocr_page_words(raw_page)
                page      = OcrPage(words)
                page_text = page.extract_text()

            ptype = _ocr_page_type(page_text)

            if ptype == 'skater_list':
                if 'FIRST NAME' in page_text or 'LAST NAME' in page_text:
                    continue   # non-standard skater list
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
                h = _content_hash(page_text)
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
                result_pages.append((page_num, page))

        print()   # newline after OCR progress line

        if skater_pages:
            for r in _parse_skater_list_pages(skater_pages, skater_offset):
                db.add(SkaterEntry(event_id=event.id, **r))
                skater_rows += 1

        if result_pages:
            for r in _parse_results_pages_ocr(result_pages):
                r.pop('event_number', None)
                db.add(Result(
                    event_id   = event.id,
                    laps       = _LAPS.get(r.get('distance_m')),
                    is_relay   = 'relay' in (r.get('division') or '').lower(),
                    **r,
                ))
                result_rows += 1

        fill_summary = fill_missing_race_context(event.id, db, verbose=True)
        db.flush()

        event.parsed_at    = datetime.now(timezone.utc).isoformat()
        event.parse_errors = None
        db.commit()

    print(f"  Skater list rows            : {skater_rows}")
    print(f"  Overall classification rows : {overall_rows}")
    print(f"  Distance classification rows: {distance_rows}")
    print(f"  Result rows                 : {result_rows}")
    print(f"  Group A heats filled        : {fill_summary['filled']} races resolved")
    if fill_summary['div_only'] or fill_summary['unknown']:
        print(f"  Group A heats unresolved    : {fill_summary['div_only']} div-only, "
              f"{fill_summary['unknown']} unknown")
    if fill_summary['review_items']:
        print("  Items for review:")
        for _, msg in fill_summary['review_items']:
            print(f"    {msg}")
    db.close()


if __name__ == '__main__':
    main()
