"""
Parser for the Gateway club legacy PDF format.

Used for: St. Louis Silver Skates, Gateway Championships (same software, same structure).
Custom results software (not Tempus). Three sections:
  1. Overall Classification  → Classification(section_type='overall')
  2. Results (events)        → Result rows
  3. Time Classification     → Classification(section_type='distance')

No club/affiliation info exists in this format — SkaterEntry rows are not written.
Page headers are auto-detected from the first page so the parser works for any
event title.

Usage:
    python scripts/parse_stl_silver_skates.py --pdf-path PATH [--force]
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session_v2 import get_session
from src.db.models_v2 import Event, Classification, Result, SkaterEntry
from src.utils.times import parse_time, is_time, STATUS_CODES


# ── Regex patterns ─────────────────────────────────────────────────────────────

# Page header / footer strip patterns covering all known formats:
#   St. Louis Silver Skates: "St. Louis Silver Skates 2022 by" / "Gateway" / "St. Louis Silver Skates N of 12" / "2022 by Gateway"
#   Gateway Championships:   "Gateway Championships 2021" / "Gateway N of 9" / "Championships 2021"
#   Buffalo Championships:   "2019 Buffalo Short Track Championship" / "2019 Buffalo Short N of 40" /
#                            "Track Championship" / "2019 Buffalo Short Track" / "Championship"
_STRIP_RES = [
    # St. Louis Silver Skates (any year)
    re.compile(r'^St\.\s+Louis\s+Silver\s+Skates', re.I),
    re.compile(r'^(\d{4}\s+by\s+)?Gateway$', re.I),
    # Gateway Championships (any year)
    re.compile(r'^Gateway\s+Championships\s+\d{4}$', re.I),
    re.compile(r'^Gateway\s+\d+\s+of\s+\d+$', re.I),
    re.compile(r'^Championships\s+\d{4}$', re.I),
    # Buffalo Short Track Championship (any year)
    re.compile(r'^\d{4}\s+Buffalo\s+Short', re.I),             # "2019 Buffalo Short Track Championship" / "2019 Buffalo Short N of 40" / "2019 Buffalo Short Track"
    re.compile(r'^Track\s+Championship$', re.I),               # second line when paginated: "2019 Buffalo Short N of 40 / Track Championship"
    re.compile(r'^Championship$', re.I),                       # second line in TC pages: "2019 Buffalo Short Track / Championship"
    # Barrel Buster (Franklin Park)
    re.compile(r'^\d{4}\s+Barrel\s+Buster$', re.I),           # "2019 Barrel Buster"
    # Ohio Invitational / Heartland Meet
    re.compile(r'^\d{4}\s+OHIO\s+INVITATIONAL', re.I),        # "2019 OHIO INVITATIONAL -"
    re.compile(r'^HEARTLAND\s+MEET$', re.I),                   # "HEARTLAND MEET"
    re.compile(r'^INVITATIONAL\s+-$', re.I),                   # second header line on result pages
    # Saratoga Cup / NEST
    re.compile(r'^\d{4}\s+Saratoga\s+Cup', re.I),             # "2019 Saratoga Cup & N of 24"
    re.compile(r'^NEST\s+#?\d+$', re.I),                       # "NEST 1" or "NEST #3"
    # January Thaw / NEST
    re.compile(r'^\d{4}\s+Jan\s+Thaw\s+Meet', re.I),          # "2020 Jan Thaw Meet & NEST #3" / "2020 Jan Thaw Meet & N of 21"
    # MASTC / Middle Atlantic Short Track
    re.compile(r'^\d{4}\s+MASTC\s+&\s+NEST', re.I),           # "2020 MASTC & NEST #6" / "2020 MASTC & NEST N of 19"
    # Ohio State Meet
    re.compile(r'^\d{4}\s+OHIO\s+STATE\s+MEET$', re.I),        # "2020 OHIO STATE MEET" (overall/TC pages)
    re.compile(r'^\d{4}\s+OHIO\s+STATE\s+\d+\s+of\s+\d+$', re.I),  # "2020 OHIO STATE 1 of 17" (results pages)
    re.compile(r'^MEET$'),                                      # split "MEET" line on results pages
    # Presidential Cup / NEST (NEST events with month label)
    re.compile(r'^\d{4}\s+Presidential\s+Cup', re.I),           # "2020 Presidential Cup & NEST 5" / "2020 Presidential Cup 1 of 29"
    re.compile(r'^&\s+NEST\s+\d', re.I),                        # "& NEST 5 Feb" (split results page header)
    re.compile(r'^Feb$|^Jan$|^Mar$|^Apr$|^May$|^Jun$|^Jul$|^Aug$|^Sep$|^Oct$|^Nov$|^Dec$'),  # month labels
    # US Championships format
    re.compile(r'^US\s+Championships', re.I),                   # "US Championships & Junior Champs" / "US Championships & N of 24"
    re.compile(r'^Junior\s+Champs$', re.I),                     # split second line of results page header
    re.compile(r'^Rankings$', re.I),                             # section label between division name and column header
    # AmCup results/TC page headers (e.g. "AmCup 3", "AmCup 3 1 of 22")
    re.compile(r'^AmCup\s+\d+', re.I),                          # "AmCup 3" / "AmCup 3 1 of 22"
    # Toyota Invitational page headers
    re.compile(r'^Toyota\s+Invitational', re.I),                # "Toyota Invitational" / "Toyota Invitational 1 of 32"
    # NorthBurke ST page headers (non-year-leading event name)
    re.compile(r'^NorthBurke\s+ST\b', re.I),                    # "NorthBurke ST 2022" / "NorthBurke ST 2022 1 of 24"
    # BSSC (Bay State Short Circuit) page headers
    re.compile(r'^BSSC\b', re.I),                               # "BSSC Fall 2019" / "BSSC Fall 2019 1 of 17"
    # Bay State Championships (2018 variant with different header format)
    re.compile(r'^Bay\s+State', re.I),                          # "Bay State - Northeast ST" / "Bay State - Northeast 1 of 17"
    re.compile(r'^ST\s+Championships$', re.I),                  # split second line "ST Championships"
    # Central Wisconsin Open
    re.compile(r'^Central\s+Wisconsin', re.I),                  # "Central Wisconsin Open" / "Central Wisconsin 1 of 11"
    re.compile(r'^Open$', re.I),                                # split second line "Open"
    # Desert Classic 2018 (non-year-leading variant)
    re.compile(r'^Desert\s+Classic', re.I),                     # "Desert Classic 2018" / "Desert Classic 2018 1 of 22"
    # Jefferson City Championships
    re.compile(r'^Jefferson\s+City', re.I),                     # "Jefferson City 2018 Championships" / "Jefferson City 2018 1 of 11"
    # Great Lakes Short Track split headers
    re.compile(r'^Short\s+Track$', re.I),                       # "Short Track" (from "2018 Great Lakes Short Track" split)
    re.compile(r'^Championships?$', re.I),                      # bare "Championships" or "Championship" split header
    # Franklin Park / Barrel Buster split header
    re.compile(r'^Barrel\s+Buster$', re.I),                     # "Barrel Buster" split from "2018 Franklin Park 1 of N"
    # Saratoga Ability Meet split header: "2018 Saratoga Ability 4 of 18" / "Meet & NEST #1"
    re.compile(r'^Meet\s+&\s+NEST\s+#?\d+$', re.I),            # "Meet & NEST #1"
    re.compile(r'^#\d+$'),                                       # "#1" standalone on classification pages
    # Gateway Speedskating Championship split header: "2019 Gateway N of M" / "Speedskating" / "Championship"
    re.compile(r'^Speedskating$', re.I),                        # "Speedskating" split line
    # January Thaw split header: "2019 January Thaw N of 24" / "and NEST 3"
    re.compile(r'^and\s+NEST\s+\d+$', re.I),                   # "and NEST 3"
    # Jeff Golz Memorial Ohio Invitational split headers (3 lines on results pages)
    re.compile(r'^Jeff\s+Golz\b', re.I),                       # "Jeff Golz Memorial Ohio Invitational" / "Jeff Golz Memorial N of 26"
    re.compile(r'^Ohio\s+Invitational\s+Meet$', re.I),         # "Ohio Invitational Meet"
    re.compile(r'^-\s+Heartland\s+Series', re.I),              # "- Heartland Series Eve"
    re.compile(r'^Meet\s+-\s+Heartland', re.I),                # "Meet - Heartland Series Event #3"
    # Park Ridge Open: "Overall Classification" appears as a standalone sub-header between div name and column header
    re.compile(r'^Overall\s+Classification$', re.I),           # strip bare "Overall Classification" label
    # AGN (National Age Group Championship) — page title starts with "National" not a year digit
    re.compile(r'^National\s+Age\s+Group\s+Championship', re.I),
    # Winter Challenge — page title "Winter Challenge 2019" / "Winter Challenge 2019 1 of 5"
    re.compile(r'^Winter\s+Challenge\b', re.I),
    # Common section labels
    re.compile(r'^\d+\s+of\s+\d+$'),
    re.compile(r'^\d+/\d+/\d{4}\s+\d+:\d+:\d+\s+[AP]M'),
    re.compile(r'^Competition\s+results?$', re.I),
    re.compile(r'^Overall\s+results?$', re.I),
    re.compile(r'^Time\s+Classification$', re.I),
]

# Trailing gender marker in names ("John Manning m", "Sarah Clarke f")
_GENDER_RE = re.compile(r'\s+[mf]$', re.I)

# Section 2: event header
#   Gateway/St. Louis format: "Event N - Division X Distance meters Round"
#   Buffalo format:           "Event N - Division Distance meters Round"  (no "X")
# Use search() so garbled prefixes still match.
EVENT_SEARCH_RE = re.compile(
    r'(\d+)\s*[-–]\s*(.+?)\s+(\d+)\s+meters?(?:\s+(.+))?$', re.I
)

# Section 3: distance sub-header  "No. 1000 Meters Best Time"
TC_HDR_RE = re.compile(r'(\d+)\s+Meters\s+Best\s+Time', re.I)

# Sub-table headers in section 2
FINAL_HDR_RE = re.compile(r'Final\s+([A-Z])\s+Time', re.I)
HEAT_HDR_RE  = re.compile(r'Heat\s+(\d+)\s+of\s+(\d+)(?:\s*\(([A-Z])\))?\s+Time', re.I)

PICKING_RE   = re.compile(r'Picking\s+top\s+\d+', re.I)
_YEAR_RE     = re.compile(r'\b(?:19|20)\d{2}\b')   # 4-digit year in a line → page title

# Overall classification prefix seen in Desert Classic format: "Overall Classification- Open A Men"
_OVERALL_CLASS_RE = re.compile(r'^Overall\s+Classification[-–]\s*', re.I)
# Single-word gender suffix that may appear on its own line after the division name
_GENDER_SUFFIXES = {'mixed', 'men', 'women', 'ladies', 'boys', 'girls'}


def _extract_gender(raw: str) -> str | None:
    """Return 'Male', 'Female', or None from a raw name token string."""
    # Strip trailing integers first, then check for gender marker
    s = re.sub(r'(\s+\d+)+\s*$', '', raw).strip()
    m = _GENDER_RE.search(s)
    if not m:
        return None
    marker = m.group(0).strip().lower()
    return 'Male' if marker == 'm' else 'Female'


def _clean_name(raw: str) -> str:
    """Strip double-quotes, trailing asterisks, seeding integers, and gender markers from names.

    Handles:
      'Robert "Joey" Dodson'       → 'Robert Joey Dodson'
      'John Manning m'             → 'John Manning'
      'Jonathan Wu m 4 15'         → 'Jonathan Wu'  (seeding digits after gender marker)
      'Xavier Babkine-Osterrath *' → 'Xavier Babkine-Osterrath'  (guest-skater marker)
    """
    name = raw.replace('"', '').strip()
    # Strip leading 3-4 letter all-caps club code prefix (e.g. "MAS Kristen Santos" → "Kristen Santos")
    name = re.sub(r'^[A-Z]{2,4}\s+(?=[A-Z])', '', name).strip()
    # Strip trailing asterisk(s) and caret(s) — used as guest/qualifier markers
    name = re.sub(r'[\s*^]+$', '', name).strip()
    # Strip trailing single-letter competition markers: (F)=Female, (M)=Male, (Y)=Youth, etc.
    name = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()
    # Strip trailing generational suffix: ", Sr" / ", Jr" / ", II" etc.
    name = re.sub(r',\s*(?:SR|JR|II|III|IV)\.?\s*$', '', name, flags=re.I).strip()
    # Strip trailing seeding integers first (e.g. "m 4 15" → "m")
    name = re.sub(r'(\s+\d+)+\s*$', '', name).strip()
    # Then strip trailing gender marker
    return _GENDER_RE.sub('', name).strip()


def _detect_mixed_suffix(sec1_lines: list[str]) -> bool:
    """Return True if result division names need gender-suffix translation (F→Ladies, M→Men).

    Triggered when classification divisions end with 'Mixed', 'Ladies', or 'Men' —
    any of these means the event uses gender-suffix convention in both classification
    and result event headers, so F/M abbreviations in headers must be translated.
    """
    for line in sec1_lines:
        tokens = line.split()
        if not tokens or tokens[0].isdigit() or tokens[0] in ('No.',):
            continue
        if EVENT_SEARCH_RE.search(line):
            break
        if tokens[-1].lower() in ('mixed', 'ladies', 'men', 'women'):
            return True
    return False


# ── Text extraction ────────────────────────────────────────────────────────────

def _clean_lines(pdf_path: Path) -> list[str]:
    """Extract text from all pages, strip page headers/footers, return clean lines."""
    lines: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            for raw in text.splitlines():
                line = raw.strip()
                if not line:
                    continue
                if any(r.search(line) for r in _STRIP_RES):
                    continue
                lines.append(line)
    return lines


# ── Section 1: Overall Classification ─────────────────────────────────────────

def _parse_overall(lines: list[str]) -> list[dict]:
    """Parse overall classification rows until the first event header.

    Each returned dict has:
      division, rank, bib, skater_name, points, cdr  — the overall row
      dist_rows: list of {distance_m, rank, points}   — one entry per distance column
        has_cdc=True  → values are final-round rankings; stored in rank (points=None)
        has_cdc=False → values are points; stored in points (rank=None)
    """
    rows: list[dict] = []
    cur_division: str | None = None
    has_cdc = False
    cur_dist_cols: list[int] = []  # distance columns identified from the header line

    for line in lines:
        if EVENT_SEARCH_RE.search(line):
            break  # reached results section

        tokens = line.split()
        if not tokens:
            continue

        # Column header "No. Name ... Points" or "Rank Number Name ... [CDC]" — skip;
        # capture CDC flag and extract the distance columns (consecutive integers after "Name").
        if tokens[0] in ('No.', 'Rank') and 'Name' in tokens:
            has_cdc = 'CDC' in tokens
            name_idx = tokens.index('Name')
            cur_dist_cols = []
            for t in tokens[name_idx + 1:]:
                if re.match(r'^\d+$', t):
                    cur_dist_cols.append(int(t))
                else:
                    break  # stop at first non-integer (Final, Points, M/F, …)
            continue

        # Data row: first two tokens are integers (rank, bib)
        if tokens[0].isdigit() and len(tokens) >= 4 and tokens[1].isdigit():
            rank = int(tokens[0])
            bib  = tokens[1]
            # Split name tokens (non-integer) from score tokens (all-integer)
            name_parts: list[str] = []
            score_parts: list[str] = []
            in_scores = False
            for t in tokens[2:]:
                if not in_scores and re.match(r'^\d+(?:\.\d+)?$', t):
                    in_scores = True
                if in_scores:
                    score_parts.append(t)
                else:
                    name_parts.append(t)
            name = _clean_name(' '.join(name_parts))
            cdc_val = None
            try:
                if has_cdc and len(score_parts) >= 2:
                    # Last column is CDC; second-to-last is total "Final Points"
                    total   = int(score_parts[-2])
                    cdc_val = int(score_parts[-1])
                else:
                    total = float(score_parts[-1]) if score_parts else None
            except ValueError:
                total = None

            # Per-distance rows from the distance columns in the header
            dist_rows: list[dict] = []
            for i, dist_m in enumerate(cur_dist_cols):
                if i >= len(score_parts):
                    break
                try:
                    val = float(score_parts[i])
                except ValueError:
                    continue
                if has_cdc:
                    # Rankings format: value is final-round rank (lower = better)
                    dist_rows.append(dict(distance_m=dist_m, rank=val, points=None))
                else:
                    # Points format: value is points earned (higher = better)
                    dist_rows.append(dict(distance_m=dist_m, rank=None, points=float(val)))

            rows.append(dict(
                division=cur_division, rank=rank, bib=bib,
                skater_name=name, points=total, cdr=cdc_val,
                dist_rows=dist_rows,
            ))
        else:
            # Division header: not a data row and not a year-containing page title.
            # Normal case: token starts with a letter.
            # Special case: token starts with a digit but is alphanumeric (e.g. "4Runner")
            # AND all remaining tokens are pure-alpha — distinguishes from garbled rows.
            _first = tokens[0]
            _valid_div_start = (
                not _first[0].isdigit()
                or (not _first.isdigit()
                    and all(re.match(r'^[A-Za-z&]+$', t) for t in tokens[1:]))
            )
            if _valid_div_start:
                # Strip "Overall Classification- " prefix (Desert Classic format)
                new_div = _OVERALL_CLASS_RE.sub('', line.strip())
                # Deduplicate trailing repeated word: "Heartland Men Men" → "Heartland Men"
                ndiv_words = new_div.split()
                if len(ndiv_words) >= 2 and ndiv_words[-1].lower() == ndiv_words[-2].lower():
                    new_div = ' '.join(ndiv_words[:-1])
                # Single-word gender suffix on its own line:
                if len(tokens) == 1 and tokens[0].lower() in _GENDER_SUFFIXES and cur_division:
                    if not cur_division.lower().endswith(tokens[0].lower()):
                        cur_division = cur_division + ' ' + new_div  # append missing suffix
                    # else already ends with suffix — keep cur_division unchanged
                else:
                    cur_division = new_div
                    has_cdc = False    # reset; column header for new division not yet seen
                    cur_dist_cols = []

    return rows


# ── Section 2: Results ─────────────────────────────────────────────────────────

def _parse_results(section2_lines: list[str], append_mixed: bool = False) -> list[dict]:
    """Parse result rows from the events section."""
    rows: list[dict] = []

    cur_event_num:  int | None = None
    cur_division:   str | None = None
    cur_distance_m: int | None = None
    cur_round_type: str | None = None
    cur_round_label: str | None = None
    cur_heat:       int | None = None
    cur_group_label: str | None = None

    # Lookahead buffer: some PDFs split event headers across two lines, e.g.
    #   "Event 2 - Division X 333\nmeters Semifinals"
    # When an event-like line doesn't fully match, store it and combine with next.
    _pending_event: str | None = None
    _EVENT_START_RE = re.compile(r'\bEvent\s+\d+\s*[-–]', re.I)

    for line in section2_lines:
        # If previous line looked like an incomplete event header, combine now
        if _pending_event is not None:
            combined = _pending_event + ' ' + line
            if EVENT_SEARCH_RE.search(combined):
                line = combined
            # Whether or not combined matches, clear the buffer and process normally
            _pending_event = None

        # Event header
        m = EVENT_SEARCH_RE.search(line)
        if m:
            cur_event_num   = int(m.group(1))
            # Normalize gender suffix in event header division name:
            #   "Hersheys X" → "Hersheys Mixed"  (X = mixed-gender marker)
            #   "NEST F"     → "NEST Ladies"      (F = female, when append_mixed events use F/M)
            #   "NEST M"     → "NEST Men"          (M = male)
            div = m.group(2).strip()
            if div.endswith(' X') or div.endswith(' x'):
                div = div[:-2].strip()
                cur_division = (div + ' Mixed') if append_mixed else div
            elif append_mixed and (div.endswith(' F') or div.endswith(' f')):
                cur_division = div[:-2].strip() + ' Ladies'
            elif append_mixed and (div.endswith(' M') or div.endswith(' m')):
                cur_division = div[:-2].strip() + ' Men'
            else:
                # Append " Mixed" only if the division doesn't already end with it
                # (event headers for Olympic-year divisions include full name: "Pyeongchang 2018 Mixed")
                if append_mixed and not div.lower().endswith(' mixed'):
                    cur_division = div + ' Mixed'
                else:
                    cur_division = div
            cur_distance_m  = int(m.group(3))
            round_raw       = (m.group(4) or '').strip()
            cur_round_type  = 'FINAL' if re.search(r'final', round_raw, re.I) else 'HEATS'
            cur_round_label = None
            cur_heat        = None
            cur_group_label = None
            continue

        # If line looks like the start of an event header but didn't fully match,
        # buffer it and combine with the next line (handles split distance/round lines)
        if _EVENT_START_RE.search(line):
            _pending_event = line
            continue

        # Sub-table: Final X
        m = FINAL_HDR_RE.search(line)
        if m and cur_event_num is not None:
            cur_group_label  = m.group(1).upper()
            cur_round_type   = 'FINAL'
            cur_round_label  = f'Final {cur_group_label}'
            cur_heat         = None
            continue

        # Sub-table: Heat N of M  (optional group letter: "Heat 1 of 6 (A)")
        m = HEAT_HDR_RE.search(line)
        if m and cur_event_num is not None:
            cur_heat        = int(m.group(1))
            cur_round_type  = 'HEATS'
            grp = m.group(3)  # None or 'A'/'B' group letter
            if grp:
                cur_round_label = f'Heat {m.group(1)} of {m.group(2)} ({grp})'
                cur_group_label = grp
            else:
                cur_round_label = f'Heat {m.group(1)} of {m.group(2)}'
                cur_group_label = None
            continue

        if PICKING_RE.search(line):
            continue

        # Data row
        if cur_event_num is None:
            continue

        tokens = line.split()
        if len(tokens) < 3:
            continue

        first = tokens[0]
        if first.isdigit():
            rank   = int(first)
            status = None
        elif first.upper() in STATUS_CODES:
            rank   = None
            status = first.upper()
        else:
            continue  # garbled or unrecognised — skip

        if not tokens[1].isdigit():
            continue
        bib  = tokens[1]
        rest = tokens[2:]

        # Scan right→left for time token or '-'
        time_idx: int | None = None
        for i in range(len(rest) - 1, -1, -1):
            if rest[i] == '-' or is_time(rest[i]):
                time_idx = i
                break
        if time_idx is None:
            continue

        raw_name = ' '.join(rest[:time_idx])
        gender   = _extract_gender(raw_name)
        name     = _clean_name(raw_name)
        time_str = rest[time_idx]
        extra    = rest[time_idx + 1] if time_idx + 1 < len(rest) else None

        qual   = None
        points = None
        if extra == 'Q':
            qual = 'Q'
            if status is None:
                status = 'Q'
        elif extra and re.match(r'^\d+(?:\.\d+)?$', extra):
            points = float(extra)

        time_seconds = parse_time(time_str) if time_str != '-' else None

        rows.append(dict(
            division    = cur_division,
            distance_m  = cur_distance_m,
            round_type  = cur_round_type,
            round_label = cur_round_label,
            race_number = cur_event_num,
            heat        = cur_heat,
            group_label = cur_group_label,
            rank        = rank,
            bib         = bib,
            skater_name = name,
            gender      = gender,
            time_text   = None if time_str == '-' else time_str,
            time_seconds= time_seconds,
            status      = status,
            points      = points,
        ))

    return rows


def _build_skater_entries(result_rows: list[dict]) -> list[dict]:
    """Build one SkaterEntry per unique bib from result rows, preserving gender."""
    seen: dict[str, dict] = {}
    for r in result_rows:
        bib = r['bib']
        if bib not in seen:
            seen[bib] = dict(
                bib         = bib,
                skater_name = r['skater_name'],
                division    = r['division'],
                gender      = r['gender'],
            )
        elif seen[bib]['gender'] is None and r['gender'] is not None:
            # Fill in gender if we get it from a later row
            seen[bib]['gender'] = r['gender']
    return list(seen.values())


# ── Section 3: Time Classification ────────────────────────────────────────────

def _parse_time_classification(section3_lines: list[str]) -> list[dict]:
    """Parse time classification rows (best time per skater per distance)."""
    rows: list[dict] = []
    cur_division:   str | None = None
    cur_distance_m: int | None = None

    for line in section3_lines:
        m = TC_HDR_RE.search(line)
        if m:
            cur_distance_m = int(m.group(1))
            continue

        tokens = line.split()
        if not tokens:
            continue

        if tokens[0].isdigit() and len(tokens) >= 4 and tokens[1].isdigit():
            rank     = int(tokens[0])
            bib      = tokens[1]
            time_str = tokens[-1]
            name     = _clean_name(' '.join(tokens[2:-1]))
            if not is_time(time_str) and time_str != '-':
                continue  # not a valid time token
            rows.append(dict(
                division        = cur_division,
                distance_m      = cur_distance_m,
                rank            = rank,
                bib             = bib,
                skater_name     = name,
                best_time_text  = None if time_str == '-' else time_str,
                best_time_seconds = parse_time(time_str) if time_str != '-' else None,
            ))
        else:
            # Division header: not a digit/status row, no year (page title), not TC sub-header
            if (tokens and not tokens[0].isdigit()
                    and tokens[0].upper() not in STATUS_CODES
                    and not TC_HDR_RE.search(line)):
                div_raw = line.strip()
                # Deduplicate trailing repeated word: "Heartland Ladies Ladies" → "Heartland Ladies"
                tc_words = div_raw.split()
                if len(tc_words) >= 2 and tc_words[-1].lower() == tc_words[-2].lower():
                    div_raw = ' '.join(tc_words[:-1])
                    tc_words = div_raw.split()
                # Single-word gender suffix on its own line (e.g. "Midget & Under Relay" / "Men")
                if len(tc_words) == 1 and tc_words[0].lower() in _GENDER_SUFFIXES and cur_division:
                    if not cur_division.lower().endswith(tc_words[0].lower()):
                        cur_division = cur_division + ' ' + div_raw
                    # else: already ends with suffix — keep cur_division unchanged
                else:
                    cur_division = div_raw

    return rows


# ── Section splitting ──────────────────────────────────────────────────────────

def _is_division_header(line: str) -> bool:
    """True if a line looks like a plain text division header (not data, not a sub-table header)."""
    tokens = line.split()
    if not tokens:
        return False
    if tokens[0].isdigit():
        return False
    if tokens[0].upper() in STATUS_CODES:   # "PEN ...", "DNS ..." are data rows, not headers
        return False
    if tokens[0] in ('No.', 'Picking', 'Rank'):
        return False
    if EVENT_SEARCH_RE.search(line) or FINAL_HDR_RE.search(line) or HEAT_HDR_RE.search(line):
        return False
    return True


def _split_sections(lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Return (section1_lines, section2_lines, section3_lines).

    When transitioning from sec2 → sec3, the line immediately before the first
    TC_HDR_RE match is a division header that belongs to sec3 (e.g. "Hersheys Mixed"
    on the same page as "No. 1000 Meters Best Time").  Move it over.
    """
    sec1: list[str] = []
    sec2: list[str] = []
    sec3: list[str] = []

    in_events = False
    in_tc     = False
    tc_first  = False  # True when TC appears before the results section

    for line in lines:
        if not in_events and not in_tc:
            if EVENT_SEARCH_RE.search(line):
                in_events = True
                sec2.append(line)
            elif TC_HDR_RE.search(line):
                # TC appears before the results section (reversed PDF layout)
                in_tc    = True
                tc_first = True
                if sec1 and _is_division_header(sec1[-1]):
                    sec3.append(sec1.pop())
                sec3.append(line)
            else:
                sec1.append(line)
        elif in_events and not in_tc:
            # Normal layout: events → TC
            if TC_HDR_RE.search(line):
                in_tc = True
                if sec2 and _is_division_header(sec2[-1]):
                    sec3.append(sec2.pop())
                sec3.append(line)
            else:
                sec2.append(line)
        elif in_tc and not in_events:
            # Reversed layout: TC done, now results section begins
            if EVENT_SEARCH_RE.search(line):
                in_events = True
                sec2.append(line)
            else:
                sec3.append(line)
        else:
            # Both sections seen — reversed: route remaining results to sec2;
            # normal: route remaining TC lines to sec3.
            if tc_first:
                sec2.append(line)
            else:
                sec3.append(line)

    return sec1, sec2, sec3


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='Parse St. Louis Silver Skates PDF')
    parser.add_argument('--pdf-path', required=True, type=Path)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    pdf_path = args.pdf_path.resolve()
    if not pdf_path.exists():
        sys.exit(f'ERROR: file not found: {pdf_path}')

    db = get_session()
    event = db.query(Event).filter(Event.local_path == str(pdf_path)).first()
    if event is None:
        db.close()
        sys.exit(f'ERROR: no event in DB with local_path={pdf_path}')

    print(f'Event id={event.id}: {event.event_name}')

    if event.parsed_at and not args.force:
        print(f'Already parsed at {event.parsed_at}. Use --force to re-parse.')
        db.close()
        return

    # Delete existing rows
    db.query(Classification).filter(Classification.event_id == event.id).delete()
    db.query(Result).filter(Result.event_id == event.id).delete()
    db.query(SkaterEntry).filter(SkaterEntry.event_id == event.id).delete()
    db.flush()

    # Extract and split
    lines = _clean_lines(pdf_path)
    sec1, sec2, sec3 = _split_sections(lines)

    # Parse
    append_mixed  = _detect_mixed_suffix(sec1)
    overall_rows  = _parse_overall(sec1)
    result_rows   = _parse_results(sec2, append_mixed=append_mixed)
    tc_rows       = _parse_time_classification(sec3)
    skater_entries = _build_skater_entries(result_rows)

    # Write overall classification + per-distance rows
    for r in overall_rows:
        db.add(Classification(
            event_id     = event.id,
            section_type = 'overall',
            division     = r['division'],
            rank         = r['rank'],
            bib          = r['bib'],
            skater_name  = r['skater_name'],
            points       = r['points'],
            cdr          = r.get('cdr'),
        ))
        for dr in r.get('dist_rows', []):
            db.add(Classification(
                event_id     = event.id,
                section_type = 'overall_dist',
                division     = r['division'],
                bib          = r['bib'],
                skater_name  = r['skater_name'],
                distance_m   = dr['distance_m'],
                rank         = dr['rank'],
                points       = dr['points'],
            ))

    # Write results
    for r in result_rows:
        db.add(Result(
            event_id    = event.id,
            division    = r['division'],
            distance_m  = r['distance_m'],
            round_type  = r['round_type'],
            round_label = r['round_label'],
            race_number = r['race_number'],
            heat        = r['heat'],
            group_label = r['group_label'],
            rank        = r['rank'],
            bib         = r['bib'],
            skater_name = r['skater_name'],
            time_text   = r['time_text'],
            time_seconds= r['time_seconds'],
            status      = r['status'],
            points      = r['points'],
        ))

    # Write time classification
    for r in tc_rows:
        db.add(Classification(
            event_id          = event.id,
            section_type      = 'distance',
            division          = r['division'],
            distance_m        = r['distance_m'],
            rank              = r['rank'],
            bib               = r['bib'],
            skater_name       = r['skater_name'],
            best_time_text    = r['best_time_text'],
            best_time_seconds = r['best_time_seconds'],
        ))

    # Null out timing-display artifacts (times > 10 min are never real race times)
    garbled = 0
    for r in db.query(Result).filter(
            Result.event_id == event.id,
            Result.time_seconds > 600).all():
        r.time_text    = None
        r.time_seconds = None
        r.parse_note   = 'garbled time from PDF (timing display artifact)'
        garbled += 1

    # Write skater entries (one per unique bib, with gender where available)
    for s in skater_entries:
        db.add(SkaterEntry(
            event_id    = event.id,
            bib         = s['bib'],
            skater_name = s['skater_name'],
            division    = s['division'],
            gender      = s['gender'],
        ))

    db.flush()
    event.parsed_at    = datetime.now(timezone.utc).isoformat()
    event.parse_errors = None
    db.commit()

    n_with_gender = sum(1 for s in skater_entries if s['gender'])
    print(f'  Overall classification : {len(overall_rows)} rows')
    print(f'  Result rows            : {len(result_rows)}')
    print(f'  Time classification    : {len(tc_rows)} rows')
    print(f'  Skater entries         : {len(skater_entries)} ({n_with_gender} with gender)')
    if garbled:
        print(f'  Garbled times nulled   : {garbled}')
    db.close()


if __name__ == '__main__':
    main()
