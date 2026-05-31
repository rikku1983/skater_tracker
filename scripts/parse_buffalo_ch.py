#!/usr/bin/env python3
"""Parser for Buffalo Short Track Championships / Heartland format PDF.

Format:
  Pages 1-7   "Class Standings": overall classification
               Column header: Pl. No. SkaterName <dist>... Total
               Each skater occupies TWO lines: score line + club line
  Pages 8-32  "Place No. Name Assn. Time Points": race results
               Event header: Event N Division Distance [Super] Meters Round
  Pages 33-46 "Place No. Name Assn. Time": time classification
               Event header: Event N Division Distance [Super] Meters Time Classification
               No bib number in TC rows (rank Name Club time M)

Name format: LastName [M|F,] FirstName [AgeCode]
Time format: HH:MM.mmm M  (trailing "M" is timing-system marker, strip it)
"""

import re
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.session_v2 import get_session
from src.db.models_v2 import Event, Classification, Result, SkaterEntry
from src.utils.times import parse_time

# ── Patterns ───────────────────────────────────────────────────────────────────

# Set dynamically in main() from the first line of the first PDF page
PAGE_HDR_RE  = re.compile(r'^Buffalo Short Track Championships', re.I)
# MeetDirector footer line: "Printed by MeetDirector(TM) Meet Management Software ..."
FOOTER_RE    = re.compile(r'^Printed\s+by\s+MeetDirector', re.I)
TIME_RE      = re.compile(r'^\d+:\d{2}\.\d+$')
AGE_CODE_RE  = re.compile(r'^[A-Z]{1,3}\d*\+?$')          # FM1, MM, SL, 60+…
STATUS_CODES = frozenset({'DNS+', 'DNS', 'DNF', 'DQ', 'DSQ', 'PEN', 'EX'})

# Event N Division Distance [Super] Meters [tail]
# Lazy division match: stops at first standalone integer (the distance)
EVENT_RE     = re.compile(
    r'^Event\s+(\d+)\s+(.+?)\s+(\d+)\s+(Super\s+)?Meters(?:\s+(.*))?$', re.I
)
SEMI_HEAT_RE = re.compile(r'Semi.?Final\s+Heat\s+(\d+)', re.I)
FINAL_GRP_RE = re.compile(r'Final\s+([A-Z])\s+Final', re.I)
TC_HDR_RE    = re.compile(r'Time\s+Class', re.I)

# ── Name helpers ───────────────────────────────────────────────────────────────

def _split_name_club(tokens: list[str]) -> tuple[str, str | None, str]:
    """Parse [LastName [M|F,] FirstName [AgeCode] ClubWords…] tokens.

    Returns (full_name, gender, club).
    """
    if not tokens:
        return '', None, ''

    ci = next((i for i, t in enumerate(tokens) if ',' in t), None)
    if ci is None:
        return ' '.join(tokens), None, ''

    ct = tokens[ci]
    ct_stripped = ct.rstrip(',')

    # Standalone comma token "," (space before comma in PDF: "LastName , FirstName")
    if not ct_stripped:
        gender = None
        last_name = ' '.join(tokens[:ci])
    # Gender code is a separate token ending with comma: "M," or "F,"
    elif ct_stripped.upper() in ('M', 'F') and ci > 0:
        gender: str | None = 'Male' if ct_stripped.upper() == 'M' else 'Female'
        last_name = ' '.join(tokens[:ci])
    else:
        gender = None
        last_name = ct_stripped          # e.g. "Gonzales"

    after = tokens[ci + 1:]
    first_name = after[0] if after else ''
    club_tokens = after[1:]

    # Optional age/division code after first name (FM1, MM1, SL, 60+…)
    if club_tokens and AGE_CODE_RE.match(club_tokens[0]) and len(club_tokens[0]) <= 5:
        age = club_tokens[0]
        club_tokens = club_tokens[1:]
        if gender is None:
            if age[0].upper() == 'F' or age.upper().startswith('SL'):
                gender = 'Female'
            elif age[0].upper() == 'M' or age.upper().startswith('ML'):
                gender = 'Male'

    club = ' '.join(club_tokens)
    name = f'{first_name} {last_name}'.strip()
    return name, gender, club


# ── Event header ───────────────────────────────────────────────────────────────

def _parse_event_hdr(line: str) -> dict | None:
    m = EVENT_RE.match(line.strip())
    if not m:
        return None

    event_num  = int(m.group(1))
    division   = m.group(2).strip()
    distance_m = int(m.group(3))
    is_super   = bool(m.group(4))
    tail       = (m.group(5) or '').strip()

    if TC_HDR_RE.search(tail) or not tail:
        return dict(event_num=event_num, division=division,
                    distance_m=distance_m, is_tc=True)

    sm = SEMI_HEAT_RE.search(tail)
    fm = FINAL_GRP_RE.search(tail)

    if sm:
        return dict(event_num=event_num, division=division, distance_m=distance_m,
                    is_tc=False, round_type='HEATS',
                    round_label=f'Heat {sm.group(1)}',
                    heat=int(sm.group(1)), group_label=None)
    if fm:
        grp = fm.group(1).upper()
        return dict(event_num=event_num, division=division, distance_m=distance_m,
                    is_tc=False,
                    round_type='SUPER FINAL' if is_super else 'FINAL',
                    round_label=f'Final {grp}',
                    heat=None, group_label=grp)

    # Fallback
    rt = 'SUPER FINAL' if is_super else ('FINAL' if 'final' in tail.lower() else 'HEATS')
    return dict(event_num=event_num, division=division, distance_m=distance_m,
                is_tc=False, round_type=rt, round_label=tail or None,
                heat=None, group_label=None)


# ── Classification (pages 1-7) ─────────────────────────────────────────────────

def _parse_classification(lines: list[str]) -> list[dict]:
    rows: list[dict] = []
    cur_division: str | None = None
    cur_dist_cols: list[int] = []
    pending: dict | None = None

    # Build a cleaned list with indices so we can lookahead
    clean: list[str] = []
    for raw in lines:
        l = raw.strip()
        if l and not PAGE_HDR_RE.search(l) and not FOOTER_RE.search(l) and l != 'Class Standings':
            clean.append(l)

    for idx, line in enumerate(clean):
        tokens = line.split()
        if not tokens:
            continue

        # Column header "Pl. No. SkaterName 333 111 ... Total"
        if tokens[0] == 'Pl.':
            cur_dist_cols = [int(t) for t in tokens if t.isdigit()]
            if pending is not None:
                rows.append(pending)
                pending = None
            continue

        # Pending skater: decide if this line is a club or a division header
        if pending is not None:
            # Look ahead: if next line is a "Pl." column header → current line is a division header
            next_line = clean[idx + 1] if idx + 1 < len(clean) else ''
            next_is_pl = next_line.split()[0] == 'Pl.' if next_line.split() else False

            flush_no_club = (
                (tokens[0].isdigit() and len(tokens) >= 2 and tokens[1].isdigit())  # new data row
                or next_is_pl                                                          # division header
                or EVENT_RE.search(line)
            )
            if flush_no_club:
                rows.append(pending)
                pending = None
                # fall through to process this line (division header or data row)
            else:
                # It's a club line
                pending['affiliation'] = line
                rows.append(pending)
                pending = None
                continue

        # Data row: starts with rank digit
        if tokens[0].isdigit() and len(tokens) >= 4 and tokens[1].isdigit():
            rank = int(tokens[0])
            bib  = tokens[1]
            rest = tokens[2:]

            # Scores are floats at the end; scan right→left
            score_start = len(rest)
            for i in range(len(rest) - 1, -1, -1):
                try:
                    float(rest[i])
                    score_start = i
                except ValueError:
                    break

            name_tokens  = rest[:score_start]
            score_tokens = rest[score_start:]

            name, gender, _ = _split_name_club(name_tokens)

            total      = float(score_tokens[-1]) if score_tokens else None
            dist_scores = score_tokens[:-1]

            dist_rows = []
            for i, dist_m in enumerate(cur_dist_cols):
                if i < len(dist_scores):
                    try:
                        dist_rows.append(dict(distance_m=dist_m, points=float(dist_scores[i])))
                    except ValueError:
                        pass

            pending = dict(
                division=cur_division, rank=rank, bib=bib,
                skater_name=name, gender=gender, points=total,
                affiliation=None, dist_rows=dist_rows,
            )
            continue

        # Division header (non-digit, not column header, not waiting for club)
        if pending is None and not EVENT_RE.search(line):
            cur_division = line

    if pending is not None:
        rows.append(pending)
    return rows


# ── Results (pages 8-32) ───────────────────────────────────────────────────────

def _parse_result_row(tokens: list[str]) -> dict | None:
    if not tokens:
        return None

    first = tokens[0]
    if first.isdigit():
        rank, status = int(first), None
    elif first.upper().rstrip('+') in STATUS_CODES or first.upper() in STATUS_CODES:
        rank, status = None, first.upper()
    else:
        return None

    if len(tokens) < 3 or not tokens[1].isdigit():
        return None
    bib  = tokens[1]
    rest = tokens[2:]

    # Find rightmost time token
    time_idx = next((i for i in range(len(rest) - 1, -1, -1) if TIME_RE.match(rest[i])), None)

    if time_idx is None:
        # Status row with no time (DNS etc.)
        name, gender, club = _split_name_club(rest)
        return dict(rank=rank, bib=bib, skater_name=name, affiliation=club,
                    gender=gender, time_text=None, time_seconds=None,
                    status=status or 'DNS', points=None)

    name, gender, club = _split_name_club(rest[:time_idx])
    time_str = rest[time_idx]

    after = rest[time_idx + 1:]
    if after and after[0].upper() == 'M':       # strip timing marker
        after = after[1:]

    points = None
    if after:
        try:
            points = float(after[0])
        except ValueError:
            pass

    return dict(
        rank=rank, bib=bib, skater_name=name, affiliation=club,
        gender=gender, time_text=time_str, time_seconds=parse_time(time_str),
        status=status, points=points,
    )


def _parse_results(lines: list[str]) -> list[dict]:
    rows: list[dict] = []
    cur_ev: dict | None = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if PAGE_HDR_RE.search(line):
            continue
        if re.match(r'^Place\s+No\.\s+Name', line, re.I):
            continue

        ev = _parse_event_hdr(line)
        if ev:
            if not ev.get('is_tc'):
                cur_ev = ev
            continue

        if cur_ev is None:
            continue

        tokens = line.split()
        # Skip division sub-labels repeated at page tops (non-digit, non-status)
        if tokens and not tokens[0].isdigit() and tokens[0].upper().rstrip('+') not in STATUS_CODES:
            continue

        row = _parse_result_row(tokens)
        if row:
            row.update(dict(
                division   = cur_ev['division'],
                distance_m = cur_ev['distance_m'],
                round_type = cur_ev['round_type'],
                round_label= cur_ev.get('round_label'),
                race_number= cur_ev['event_num'],
                heat       = cur_ev.get('heat'),
                group_label= cur_ev.get('group_label'),
            ))
            rows.append(row)

    return rows


# ── Time Classification (pages 33-46) ─────────────────────────────────────────

def _parse_tc_row(tokens: list[str]) -> dict | None:
    """TC rows have no bib: rank Name Club time M"""
    if not tokens or not tokens[0].isdigit():
        return None
    rank = int(tokens[0])
    rest = tokens[1:]

    time_idx = next(
        (i for i in range(len(rest) - 1, -1, -1)
         if TIME_RE.match(rest[i]) or rest[i] == '-'),
        None
    )
    if time_idx is None:
        return None

    name, gender, club = _split_name_club(rest[:time_idx])
    time_str = rest[time_idx]
    time_sec = parse_time(time_str) if time_str != '-' else None

    return dict(rank=rank, bib=None, skater_name=name, affiliation=club,
                gender=gender, best_time_text=None if time_str == '-' else time_str,
                best_time_seconds=time_sec)


def _parse_tc(lines: list[str]) -> list[dict]:
    rows: list[dict] = []
    cur_ev: dict | None = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if PAGE_HDR_RE.search(line):
            continue
        if re.match(r'^Place\s+No\.\s+Name', line, re.I):
            continue

        ev = _parse_event_hdr(line)
        if ev:
            if ev.get('is_tc'):
                cur_ev = ev
            continue

        if cur_ev is None:
            continue

        tokens = line.split()
        if not tokens or not tokens[0].isdigit():
            continue   # division sub-label or header

        row = _parse_tc_row(tokens)
        if row:
            row.update(dict(division=cur_ev['division'], distance_m=cur_ev['distance_m']))
            rows.append(row)

    return rows


# ── Page section detection ─────────────────────────────────────────────────────

def _page_section(lines: list[str]) -> str:
    """Return 'class', 'result', 'tc', or 'unknown' for a page."""
    for line in lines[:5]:
        if 'Class Standings' in line:
            return 'class'
        if re.search(r'Time\s+Points\s*$', line, re.I):
            return 'result'
        if re.match(r'Place\s+No\.\s+Name\s+Assn\.\s+Time\s*$', line.strip(), re.I):
            return 'tc'
    return 'unknown'


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--pdf-path', required=True)
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    pdf_path = Path(args.pdf_path)
    db = get_session()
    try:
        event = db.query(Event).filter(
            Event.local_path.like(f'%{pdf_path.name}%')
        ).first()
        if event is None:
            print(f'Event not found for {pdf_path.name}')
            return

        if event.parsed_at and not args.force:
            print(f'Event id={event.id}: {event.event_name}')
            print(f'Already parsed at {event.parsed_at}. Use --force to re-parse.')
            return

        print(f'Event id={event.id}: {event.event_name}')

        if args.force:
            db.query(Result).filter(Result.event_id == event.id).delete()
            db.query(Classification).filter(Classification.event_id == event.id).delete()
            db.query(SkaterEntry).filter(SkaterEntry.event_id == event.id).delete()

        class_lines: list[str] = []
        result_lines: list[str] = []
        tc_lines: list[str] = []

        with pdfplumber.open(pdf_path) as pdf:
            # Detect the page title from the first non-empty line of page 1
            first_lines = (pdf.pages[0].extract_text() or '').splitlines()
            page_title = next((l.strip() for l in first_lines if l.strip()), '')
            if page_title:
                global PAGE_HDR_RE
                PAGE_HDR_RE = re.compile(r'^' + re.escape(page_title), re.I)
            for page in pdf.pages:
                txt   = page.extract_text() or ''
                lines = txt.splitlines()
                sec   = _page_section(lines)
                if sec == 'class':
                    class_lines.extend(lines)
                elif sec == 'result':
                    result_lines.extend(lines)
                elif sec == 'tc':
                    tc_lines.extend(lines)

        class_rows  = _parse_classification(class_lines)
        result_rows = _parse_results(result_lines)
        tc_rows     = _parse_tc(tc_lines)

        # ── Write overall classification ──────────────────────────────────────
        written_class = written_dist = 0
        for r in class_rows:
            if r['division'] is None:
                continue
            db.add(Classification(
                event_id=event.id, section_type='overall',
                division=r['division'], rank=r['rank'], bib=r['bib'],
                skater_name=r['skater_name'], affiliation=r.get('affiliation'),
                points=r['points'],
            ))
            written_class += 1
            for dr in r.get('dist_rows', []):
                db.add(Classification(
                    event_id=event.id, section_type='overall_dist',
                    division=r['division'], bib=r['bib'],
                    skater_name=r['skater_name'],
                    distance_m=dr['distance_m'], points=dr['points'],
                ))
                written_dist += 1

        # ── Write skater entries from classification ───────────────────────────
        seen: dict[str, dict] = {}
        for r in class_rows:
            if r['bib'] not in seen:
                seen[r['bib']] = r
                db.add(SkaterEntry(
                    event_id=event.id, bib=r['bib'],
                    skater_name=r['skater_name'], division=r['division'],
                    club=r.get('affiliation'), gender=r.get('gender'),
                ))

        # ── Build skater entries from results (for bibs not in classification) ──
        for r in result_rows:
            bib = r.get('bib')
            if bib and bib not in seen:
                seen[bib] = r
                db.add(SkaterEntry(
                    event_id=event.id, bib=bib,
                    skater_name=r['skater_name'], division=r['division'],
                    club=r.get('affiliation') or None, gender=r.get('gender'),
                ))

        # ── Write results ─────────────────────────────────────────────────────
        written_results = 0
        for r in result_rows:
            if r['division'] is None:
                continue
            db.add(Result(
                event_id   =event.id,
                division   =r['division'],
                distance_m =r['distance_m'],
                round_type =r['round_type'],
                round_label=r.get('round_label'),
                race_number=r.get('race_number'),
                heat       =r.get('heat'),
                group_label=r.get('group_label'),
                rank       =r['rank'],
                bib        =r['bib'],
                skater_name=r['skater_name'],
                affiliation=r.get('affiliation'),
                time_text  =r.get('time_text'),
                time_seconds=r.get('time_seconds'),
                status     =r.get('status'),
                points     =r.get('points'),
            ))
            written_results += 1

        # ── Write time classification ─────────────────────────────────────────
        written_tc = 0
        for r in tc_rows:
            if r['division'] is None:
                continue
            db.add(Classification(
                event_id        =event.id, section_type='distance',
                division        =r['division'], distance_m=r['distance_m'],
                rank            =r['rank'], bib=r.get('bib'),
                skater_name     =r['skater_name'], affiliation=r.get('affiliation'),
                best_time_text  =r.get('best_time_text'),
                best_time_seconds=r.get('best_time_seconds'),
            ))
            written_tc += 1

        event.parsed_at = datetime.now(timezone.utc)
        db.commit()

        print(f'  Overall classification : {written_class} rows')
        print(f'  Overall dist class     : {written_dist} rows')
        print(f'  Result rows            : {written_results}')
        print(f'  Time classification    : {written_tc} rows')
        print(f'  Skater entries         : {len(seen)}')

    finally:
        db.close()


if __name__ == '__main__':
    main()
