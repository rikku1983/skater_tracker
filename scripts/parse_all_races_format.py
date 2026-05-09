"""
Generic parser for Tempus "All Races" format PDFs.

Sections are detected by page content (not page number) using _page_type().
Field extraction uses regex patterns and the "largest word-gap" heuristic:
  - Typed fields (rank, bib, time, status codes, group labels, points, CDR, BDR)
    are extracted by matching regex patterns on token strings.
  - Name / affiliation split is found by locating the largest inter-word gap in
    the middle zone of each row.  Column breaks always produce a noticeably wider
    gap than within-column word spacing in Tempus-generated PDFs.
  - Gender and status tokens anchor the skater-list split.

Usage:
    python scripts/parse_all_races_format.py --pdf-path PATH/TO/FILE.pdf
"""
from __future__ import annotations
import argparse
import re
import sys
from datetime import timezone, datetime
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parents[1]))
from src.db.session_v2 import init_db, get_session
from src.db.models_v2 import Event, Classification, Result, SkaterEntry
from scripts.fill_missing_race_context import fill_missing_race_context

# ── field patterns ────────────────────────────────────────────────────────────

TIME_RE          = re.compile(r'^\d+:\d{2}\.\d+$')
GROUP_FINAL_RE   = re.compile(r'^[A-Z]\d+$')                      # A1, B2, C3 …
RESULT_STATUS_RE = re.compile(r'^(Q|DNF|DNS|DQ|DSQ|PEN|ADV(?:-[A-Z])?|FNT|PB)$')
GENDER_RE        = re.compile(r'^(Male|Female|Mixed)$', re.I)
STATUS_FIRST_RE  = re.compile(r'^(JR|SR|Juv|Nov|Open|Master|\d{2}\+|[A-Z]\d{1,2})$', re.I)
STATUS_SECOND_RE = re.compile(r'^[A-F]$')
EVENT_HDR_RE     = re.compile(
    r"(?:Group\s+[A-Z]\s+)?(.+?)'s?\s+(\d+)\s+(.+?)\s+Event\s+#(\d+)$"
)
# Some PDFs split the section header across two lines:
#   Format A: "Division's 1000 Event #0"  (dist on line 1) + "Finals"      (round on line 2)
#   Format B: "Division's Event #0"        (no dist/round)  + "1000 Finals" (both on line 2)
_SPLIT_HDR_A = re.compile(r"(.+?)'s?\s+(\d+)\s+Event\s+#(\d+)$")
_SPLIT_HDR_B = re.compile(r"(.+?)'s?\s+Event\s+#(\d+)$")
_ROUND_ONLY  = re.compile(r"^((?:Super\s+Final\s+)?Finals?|Heats?|Semi-?Finals?)$", re.I)
_DIST_ROUND  = re.compile(r"^(\d+)\s+((?:Super\s+Final\s+)?Finals?|Heats?|Semi-?Finals?)", re.I)
HEAT_HDR_RE  = re.compile(r'^Heat\s+(\d+)\s+of\s+\d+\s+Race\s+#(\d+)$')
FINAL_HDR_RE = re.compile(r'^([A-Z])\s+Final\s+Race\s+#(\d+)$')

# Semi-group is a bare integer, or a group code, or a status word
SEMI_GROUP_RE = re.compile(r'^(\d+|[A-Z]\d+|PEN|DNS|Q)$')


def _norm_round_type(s: str) -> str:
    """Normalize raw round string from PDF to HEATS / FINAL / SUPER FINAL."""
    t = s.lower().strip()
    if 'super' in t:
        return 'SUPER FINAL'
    if 'semi' in t or 'quarter' in t or 'heat' in t:
        return 'HEATS'
    if 'final' in t:
        return 'FINAL'
    return s


# ── low-level helpers ─────────────────────────────────────────────────────────

def _words_to_rows(words: list[dict]) -> list[list[dict]]:
    """Group pdfplumber word dicts into visual rows by y-coordinate (±2 pt)."""
    if not words:
        return []
    rows: list[list[dict]] = []
    cur_y = words[0]['top']
    cur: list[dict] = []
    for w in words:
        if abs(w['top'] - cur_y) < 3:
            cur.append(w)
        else:
            if cur:
                rows.append(sorted(cur, key=lambda x: x['x0']))
            cur, cur_y = [w], w['top']
    if cur:
        rows.append(sorted(cur, key=lambda x: x['x0']))
    return rows


def _parse_time(text: str) -> float | None:
    if not text or not TIME_RE.match(text.strip()):
        return None
    m, s = text.strip().split(':')
    return int(m) * 60 + float(s)


def _clean_name(raw: str) -> str:
    # '*' marks youth/junior skaters in some PDFs; '^' is a font-encoding artifact
    return raw.replace('*', '').replace('^', '').strip()


def _is_relay(division: str | None) -> bool:
    """Return True if the division name indicates a relay event."""
    return bool(division and 'relay' in division.lower())


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


def _largest_gap_split(words: list[dict]) -> tuple[str, str]:
    """Split a word list into two parts at the largest inter-word gap.

    Returns (left_text, right_text).  If only one word, returns (word, '').
    """
    if not words:
        return '', ''
    if len(words) == 1:
        return words[0]['text'], ''
    gaps = [words[i + 1]['x0'] - words[i]['x0'] for i in range(len(words) - 1)]
    split_at = max(range(len(gaps)), key=lambda i: gaps[i])
    left  = ' '.join(w['text'] for w in words[:split_at + 1])
    right = ' '.join(w['text'] for w in words[split_at + 1:])
    return left, right


def _split_name_affil(mid_words: list[dict]) -> tuple[str, str | None, str]:
    """Return (name, status_or_None, affiliation) from the middle zone words.

    Tries status-keyword split first (for events that have a status column).
    Falls back to largest-gap split when no status keyword is found.
    """
    texts = [w['text'] for w in mid_words]

    # Status-based split (Jan Thaw style: name STATUS affil)
    status_idx = next(
        (i for i, t in enumerate(texts) if STATUS_FIRST_RE.match(t)), None
    )
    if status_idx is not None and status_idx > 0:
        name = _clean_name(' '.join(texts[:status_idx]))
        status_parts = [texts[status_idx]]
        if status_idx + 1 < len(texts) and STATUS_SECOND_RE.match(texts[status_idx + 1]):
            status_parts.append(texts[status_idx + 1])
        status = ' '.join(status_parts)
        affil_words = texts[status_idx + len(status_parts):]
        return name, status, ' '.join(affil_words)

    # Largest-gap split (St. Louis style: name affil, no status)
    left, right = _largest_gap_split(mid_words)
    return _clean_name(left), None, right


# ── skater list parser ────────────────────────────────────────────────────────

def _parse_skater_list_pages(pages: list, page_offset: int) -> list[dict]:
    rows_out = []
    for pg_idx, page in enumerate(pages):
        page_num = page_offset + pg_idx
        for row in _words_to_rows(page.extract_words()):
            texts = [w['text'] for w in row]
            if not texts or not texts[0].isdigit():
                continue
            bib = texts[0]

            # Find gender keyword in the row
            gender_idx = next(
                (i for i, t in enumerate(texts[1:], 1) if GENDER_RE.match(t)), None
            )
            if gender_idx is None:
                continue

            # Status may appear between bib and gender (Jan Thaw format)
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
            post_gender_words = [w for w in row if w['x0'] > row[gender_idx]['x0']]

            # Split division / club at the largest gap after gender
            division, club = _largest_gap_split(post_gender_words)

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


# ── overall classification parser ─────────────────────────────────────────────

def _parse_overall_page(page, page_num: int) -> tuple[str, list[dict]]:
    """Return (division_name, list_of_row_dicts)."""
    text = page.extract_text() or ''
    lines = [l.strip() for l in text.split('\n')]
    division = ''
    for i, ln in enumerate(lines):
        if 'OVERALL CLASSIFICATION' in ln:
            division = lines[i - 1]
            break

    results = []
    for row in _words_to_rows(page.extract_words()):
        texts = [w['text'] for w in row]
        if len(texts) < 6:
            continue

        # Rank: first token (digit)
        if not texts[0].isdigit():
            continue
        rank = int(texts[0])

        # Bib: second token (digit)
        if not texts[1].isdigit():
            continue
        bib = texts[1]

        # From right: optional time, then BDR, CDR, points
        j = len(texts) - 1
        time_txt = None
        if TIME_RE.match(texts[j]):
            time_txt = texts[j]
            j -= 1

        try:
            bdr    = int(texts[j]);                   j -= 1
            cdr    = int(texts[j]);                   j -= 1
            points = float(texts[j].replace(',', '')); j -= 1
        except (ValueError, IndexError):
            continue

        # Middle zone: words between bib (index 1) and points (index j+1)
        mid_words = row[2:j + 1]
        if not mid_words:
            continue

        name, status, affil = _split_name_affil(mid_words)
        time_ok = bool(time_txt and TIME_RE.match(time_txt))
        results.append({
            'rank':              rank,
            'bib':               bib,
            'skater_name':       name,
            'skater_status':     status or None,
            'affiliation':       affil or None,
            'points':            points,
            'cdr':               cdr,
            'bdr':               bdr,
            'best_time_text':    time_txt if time_ok else None,
            'best_time_seconds': _parse_time(time_txt) if time_ok else None,
            'page_number':       page_num,
        })
    return division, results


# ── distance classification parser ────────────────────────────────────────────

def _parse_distance_page(page, page_num: int) -> tuple[str, int | None, list[dict]]:
    """Return (division, distance_m, list_of_row_dicts)."""
    text = page.extract_text() or ''
    lines = [l.strip() for l in text.split('\n')]
    division, distance_m = '', None
    # U+0027 straight, U+2018/U+2019 curly apostrophes, ! (OCR artifact for apostrophe)
    _APOS = "['\u2018\u2019!]"
    _DIV_DIST_INLINE = re.compile("(.+?)" + _APOS + r"s?\s+(\d+(?:\s+\(#\d+\))?)")
    for i, ln in enumerate(lines):
        if 'DISTANCE CLASSIFICATION' in ln:
            # check lines before and after the keyword (OCR may repeat or misplace the header)
            candidates = list(reversed(lines[max(0, i-3):i])) + lines[i+1:i+3]
            for header in candidates:
                m = _DIV_DIST_INLINE.match(header)
                if m:
                    division   = re.sub("['\u2018\u2019!]s?$", '', m.group(1).strip())
                    dist_str   = m.group(2)
                    dist_num   = re.match(r'(\d+)', dist_str)
                    distance_m = int(dist_num.group(1)) if dist_num else None
                    break
            break

    results = []
    for row in _words_to_rows(page.extract_words()):
        texts = [w['text'] for w in row]
        if len(texts) < 4:
            continue

        # Rank: first token (digit, including 0 for DNS)
        if not texts[0].isdigit():
            continue
        rank = int(texts[0])

        # DNS rows: rank=0, bib is texts[1], no points column
        if rank == 0 and len(texts) >= 3 and texts[1].isdigit():
            bib = texts[1]
            # DNS tokens at end
            j = len(texts) - 1
            dns_groups: list[str] = []
            while j >= 2 and texts[j] == 'DNS':
                dns_groups.insert(0, texts[j])
                j -= 1
            if dns_groups:
                mid_words = row[2:j + 1]
                name, _, affil = _split_name_affil(mid_words)
                results.append({
                    'rank': 0, 'bib': bib,
                    'skater_name':       name,
                    'affiliation':       affil or None,
                    'points':            None,
                    'semi_group':        dns_groups[0] if dns_groups else None,
                    'final_group':       dns_groups[1] if len(dns_groups) > 1 else None,
                    'best_time_text':    None,
                    'best_time_seconds': None,
                    'page_number':       page_num,
                })
            continue

        # Points: second token (comma-formatted integer)
        if not texts[1].replace(',', '').isdigit():
            continue
        points = float(texts[1].replace(',', ''))

        # Bib: third token (digit)
        if not texts[2].isdigit():
            continue
        bib = texts[2]

        # From right: time (optional), F group (optional), S group (optional)
        j = len(texts) - 1

        time_txt = None
        if TIME_RE.match(texts[j]):
            time_txt = texts[j]
            j -= 1

        final_group = semi_group = None
        # F: last remaining non-time token if it looks like a group or a status code
        if j >= 3 and (GROUP_FINAL_RE.match(texts[j]) or RESULT_STATUS_RE.match(texts[j])):
            final_group = texts[j]
            j -= 1
            # S: next token if it looks like a semi-group marker
            if j >= 3 and SEMI_GROUP_RE.match(texts[j]):
                semi_group = texts[j]
                j -= 1
                # Q / H columns (events with H Q S F layout): strip up to 2 more
                if j >= 3 and SEMI_GROUP_RE.match(texts[j]):
                    j -= 1
                if j >= 3 and SEMI_GROUP_RE.match(texts[j]):
                    j -= 1

        # Middle: words between bib (index 2) and j
        mid_words = row[3:j + 1]
        name, _, affil = _split_name_affil(mid_words)

        time_ok = bool(time_txt and TIME_RE.match(time_txt))
        results.append({
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
    return division, distance_m, results


# ── results parser ────────────────────────────────────────────────────────────

def _parse_results_pages(pages: list) -> list[dict]:
    """pages is a list of (page_num, pdfplumber_page) tuples."""
    rows_out = []
    cur = dict(division=None, distance_m=None, round_type=None, round_label=None,
               event_num=None, heat=None, group_label=None, race_num=None)
    # Pending split-header state: when line 1 of a 2-line header is detected,
    # store partial info here; line 2 completes it.
    _pending = {}   # keys: 'div', 'dist' (optional), 'ev_num'

    for page_num, page in pages:
        for row in _words_to_rows(page.extract_words()):
            line = ' '.join(w['text'] for w in row)

            # ── section / race headers ─────────────────────────────────────
            m = EVENT_HDR_RE.match(line)
            if m:
                _pending.clear()
                div_raw, dist_str, round_str, ev_num = m.groups()
                cur['division']   = re.sub(r"'s$", '', div_raw.strip())
                cur['distance_m'] = int(dist_str)
                cur['round_type'] = _norm_round_type(round_str)
                cur['event_num']  = int(ev_num)
                cur['heat'] = cur['group_label'] = cur['race_num'] = None
                cur['round_label'] = None
                continue

            # Split-header line 2: complete a pending partial header
            if _pending:
                if 'dist' in _pending:
                    # Format A: line 2 is the round string
                    if _ROUND_ONLY.match(line):
                        cur['division']   = _pending['div']
                        cur['distance_m'] = _pending['dist']
                        cur['round_type'] = _norm_round_type(line)
                        cur['event_num']  = _pending['ev_num']
                        cur['heat'] = cur['group_label'] = cur['race_num'] = None
                        cur['round_label'] = None
                        _pending.clear()
                        continue
                else:
                    # Format B: line 2 is "DIST ROUND"
                    dm = _DIST_ROUND.match(line)
                    if dm:
                        cur['division']   = _pending['div']
                        cur['distance_m'] = int(dm.group(1))
                        cur['round_type'] = _norm_round_type(dm.group(2))
                        cur['event_num']  = _pending['ev_num']
                        cur['heat'] = cur['group_label'] = cur['race_num'] = None
                        cur['round_label'] = None
                        _pending.clear()
                        continue
                # Line 2 didn't complete the header — discard pending
                _pending.clear()

            # Split-header line 1 detection (Format A: dist present, round absent)
            ma = _SPLIT_HDR_A.match(line)
            if ma:
                _pending = {'div': re.sub(r"'s?$", '', ma.group(1).strip()),
                            'dist': int(ma.group(2)), 'ev_num': int(ma.group(3))}
                continue

            # Split-header line 1 detection (Format B: only div + event#)
            mb = _SPLIT_HDR_B.match(line)
            if mb:
                _pending = {'div': re.sub(r"'s?$", '', mb.group(1).strip()),
                            'ev_num': int(mb.group(2))}
                continue

            # "Group A Picking N+(M) Event #N" — combined heat with a real event number
            # but no division/distance info in the header line.  Clear the current
            # context so subsequent skater rows are not falsely attributed to the
            # previous section's division/distance.
            if re.match(r'^Group\s+[A-Z]\s+Picking\b', line, re.I):
                _pending.clear()
                cur['division'] = cur['distance_m'] = None
                cur['round_type'] = 'HEATS'
                cur['race_num'] = None  # clear until Heat header fires
                continue

            m2 = HEAT_HDR_RE.match(line)
            if m2:
                cur['heat']        = int(m2.group(1))
                cur['race_num']    = int(m2.group(2))
                cur['group_label'] = None
                cur['round_label'] = f"Heat {cur['heat']}"
                continue

            m3 = FINAL_HDR_RE.match(line)
            if m3:
                cur['group_label'] = m3.group(1)
                cur['race_num']    = int(m3.group(2))
                cur['heat']        = None
                cur['round_label'] = f"{cur['group_label']} Final"
                continue

            if cur['race_num'] is None:
                continue

            # ── data row ──────────────────────────────────────────────────
            texts = [w['text'] for w in row]
            if not texts or not texts[0].isdigit():
                continue

            # Determine if row has a time (→ ranked) or just status (→ DNS/PEN/etc.)
            time_indices = [i for i, t in enumerate(texts) if TIME_RE.match(t)]

            if time_indices:
                # Ranked row: [rank] bib {name affil} time [status]
                ti = time_indices[-1]
                time_txt = texts[ti]
                status = texts[ti + 1] if ti + 1 < len(texts) else None
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
                # No-time row: [rank] bib {name affil} status
                status = texts[-1] if texts and RESULT_STATUS_RE.match(texts[-1]) else None
                if not status:
                    continue
                # If first TWO tokens are digits, row is ranked (e.g. "2 117 Name Club FNT")
                if len(texts) >= 2 and texts[0].isdigit() and texts[1].isdigit():
                    rank, bib = int(texts[0]), texts[1]
                    mid_words = row[2:-1]
                else:
                    rank, bib = None, texts[0]
                    mid_words = row[1:-1]
                time_txt = None

            name, _, affil = _split_name_affil(mid_words)
            time_secs = _parse_time(time_txt)

            # Promote status from time column if needed (DNS in time slot)
            if not time_secs and time_txt and not status:
                status   = time_txt
                time_txt = None

            rows_out.append({
                'division':    cur['division'],
                'distance_m':  cur['distance_m'],
                'round_type':  cur['round_type'],
                'round_label': cur['round_label'],
                'race_number': cur['race_num'],
                'event_number': cur['event_num'],
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
    parser.add_argument('--pdf-path', required=True, help='Path to the PDF file')
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"ERROR: file not found: {pdf_path}")
        sys.exit(1)

    init_db()
    db = get_session()

    event = db.query(Event).filter(Event.local_path == str(pdf_path.resolve())).first()
    if not event:
        print(f"ERROR: event not found in DB for path {pdf_path}")
        print("Make sure the event is seeded in knowledge_base/events.csv first.")
        sys.exit(1)

    print(f"Event id={event.id}: {event.event_name}")

    # Clear existing rows (idempotent)
    db.query(SkaterEntry).filter(SkaterEntry.event_id == event.id).delete()
    db.query(Classification).filter(Classification.event_id == event.id).delete()
    db.query(Result).filter(Result.event_id == event.id).delete()
    db.flush()

    # Content-hash dedup: strip Tempus footer (which contains varying page numbers)
    # before hashing, so pages that differ only in the "Page N of M" footer are
    # treated as duplicates.  This handles PDFs that were printed twice (like
    # Capital City) without falsely deduplicating two-day combined events.
    _FOOTER_RE = re.compile(r'Tempus Competition Software.*|^\s*Page \d+ of \d+\s*$',
                             re.MULTILINE)

    def _content_hash(text: str) -> int:
        return hash(_FOOTER_RE.sub('', text))

    with pdfplumber.open(pdf_path) as pdf:
        pages = pdf.pages
        skater_pages, result_pages = [], []
        skater_offset = None
        skater_rows = overall_rows = distance_rows = result_rows = 0
        seen_result_hashes: set[int] = set()

        for pg_idx, page in enumerate(pages):
            page_num = pg_idx + 1
            text = page.extract_text() or ''
            ptype = _page_type(text)

            if ptype == 'skater_list':
                # Skip non-standard skater list pages (e.g. InterScholastic page 3
                # uses "FIRST NAME" / "LAST NAME" column headers instead of Tempus format)
                if 'FIRST NAME' in text or 'LAST NAME' in text:
                    print(f"  Skipping non-standard skater list page {page_num}")
                    continue
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
                h = _content_hash(text)
                if h in seen_result_hashes:
                    continue          # exact-duplicate page — skip
                seen_result_hashes.add(h)
                result_pages.append((page_num, page))

        if skater_pages:
            for r in _parse_skater_list_pages(skater_pages, skater_offset):
                db.add(SkaterEntry(event_id=event.id, **r))
                skater_rows += 1

        if result_pages:
            for r in _parse_results_pages(result_pages):
                r.pop('event_number', None)
                db.add(Result(event_id=event.id,
                              is_relay=_is_relay(r.get('division')), **r))
                result_rows += 1

        # Fill NULL-division rows from Group A Picking heats
        fill_summary = fill_missing_race_context(event.id, db, verbose=True)
        db.flush()

        event.parsed_at = datetime.now(timezone.utc).isoformat()
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
