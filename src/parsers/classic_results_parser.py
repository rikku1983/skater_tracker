"""
Parser for the "classic" short track results PDF format.

Used by Heartland/NEST events ~2018–2022 (Buffalo Championships, Gateway, Bay State, etc.).

Page structure (results section, after Class Standings pages):
  <Event Name>
  Place No. Name Assn. Time Points
  <Division Name>
  Event N <Division> <Distance> Meters <Round>
  1 12 LastName [Cat,] FirstName Club 1:22.740 M [points]
  DNS+ 177 LastName F, FirstName Club

Detection markers:
  - results pages: "Place No. Name Assn. Time" header
  - skip pages: "Class Standings" or "Time Classification" in text

Names in this format sometimes embed gender/category codes (M, F, MM1, FM1, SL, etc.)
between last name and club — these are preserved as-is in the name field.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import pdfplumber

from .base import ParsedEvent, ParsedResult, PdfFormat, _extract_distance_m
from ..utils.times import parse_time, TIME_RE, STATUS_CODES

logger = logging.getLogger(__name__)

# "Event 76 Junior D Men 500 Meters Final A Final"
_EVENT_HDR_RE = re.compile(
    r'^Event\s+\d+\s+(.+?)\s+(\d{3,4})\s+Meters\s+(.+)$',
    re.IGNORECASE,
)

# Progression rule suffix like "P1+1", "P2", "P1+2" at end of round string
_PROG_RULE_RE = re.compile(r'\s+P\d+(?:\+\d+)?\s*$')

# Status prefix: DNS, DNS+, DQ+, DNF, PEN, DIS (with optional +)
_STATUS_PREFIX_RE = re.compile(r'^([A-Z]{2,4})\+?\s+', re.IGNORECASE)

_SKIP_MARKERS = ("Class Standings", "Time Classification", "DISTANCE CLASSIFICATION")
_PAGE_MARKER = "Place No. Name Assn. Time"

# Standalone gender markers and Masters category codes to strip from name tokens.
# Single-char gender codes: "M" or "F" appearing as an isolated token.
# Masters codes: MM1, FM1, FM2, FM3, SL, SCH (may be followed by a letter token like "C")
_GENDER_CODES = frozenset({"M", "F"})
_MASTERS_CODE_RE = re.compile(r'^[MF]{1,2}\d+$|^SL$|^SCH$', re.IGNORECASE)


def _is_results_page(text: str) -> bool:
    if any(m in text for m in _SKIP_MARKERS):
        return False
    return _PAGE_MARKER in text


def _normalize_round(raw: str) -> str:
    """Clean up round string from event header."""
    r = _PROG_RULE_RE.sub('', raw).strip()
    # "Final A Final" → "Final A",  "Final B Final" → "Final B"
    r = re.sub(r'^(Final\s+[A-Z])\s+Final$', r'\1', r, flags=re.IGNORECASE)
    return r


def _parse_event_header(line: str) -> Optional[tuple[str, int, str]]:
    """Return (division, distance_m, round_str) or None."""
    m = _EVENT_HDR_RE.match(line.strip())
    if not m:
        return None
    return m.group(1).strip(), int(m.group(2)), _normalize_round(m.group(3))


def _parse_result_row(line: str) -> Optional[dict]:
    """
    Parse one result row. Returns dict with keys:
      rank, bib, name, affiliation, time_text, time_seconds, status
    or None if the line doesn't match.
    """
    line = line.strip()
    if not line:
        return None

    tokens = line.split()
    if len(tokens) < 3:
        return None

    # Determine rank or status from first token
    rank: Optional[int] = None
    status: Optional[str] = None
    first = tokens[0].rstrip('+')  # strip trailing + from DNS+, DQ+, etc.

    if first.isdigit():
        rank = int(first)
        rest_tokens = tokens[1:]
    elif first.upper() in STATUS_CODES or _STATUS_PREFIX_RE.match(tokens[0]):
        status = first.upper()
        rest_tokens = tokens[1:]
    else:
        return None

    if not rest_tokens or not rest_tokens[0].isdigit():
        return None

    bib = rest_tokens[0]
    name_affil_tokens = rest_tokens[1:]

    # Find time token (or end of line for DNS with no time)
    time_idx = next(
        (i for i, t in enumerate(name_affil_tokens) if TIME_RE.match(t)),
        None,
    )

    if time_idx is not None:
        middle = name_affil_tokens[:time_idx]
        time_text = name_affil_tokens[time_idx]
        # Tokens after time: optional "M" marker + optional points — ignore both
        time_seconds = parse_time(time_text)
        # Check if a status code appears after time (e.g. DQ row with time recorded)
        after_time = name_affil_tokens[time_idx + 1:]
        for t in after_time:
            if t.upper() in STATUS_CODES:
                status = t.upper()
                break
    else:
        # DNS / DNF with no time
        middle = name_affil_tokens
        time_text = None
        time_seconds = None

    if not middle:
        return None

    # Split name from affiliation using the comma in the name
    # Format: LastName[, | Cat,] FirstName  Club...
    # Find the first token that ends with ','
    comma_idx = next((i for i, t in enumerate(middle) if t.endswith(',')), None)

    row_gender: Optional[str] = None

    if comma_idx is not None and comma_idx + 1 < len(middle):
        last_name_tokens = middle[:comma_idx + 1]   # includes the comma-bearing token
        first_name = middle[comma_idx + 1]
        affiliation_tokens = middle[comma_idx + 2:]

        # Strip standalone gender codes and Masters category codes from last-name tokens.
        # "SCH C" is two tokens — strip both when "SCH" appears.
        cleaned = []
        skip_next = False
        for tok in last_name_tokens:
            if skip_next:
                skip_next = False
                continue
            bare = tok.rstrip(',')
            if bare.upper() in _GENDER_CODES or _MASTERS_CODE_RE.match(bare):
                if bare.upper() in _GENDER_CODES and row_gender is None:
                    row_gender = bare.upper()
                if bare.upper() == 'SCH':
                    skip_next = True   # also drop the following "C" token
                continue
            cleaned.append(tok)

        last_name = cleaned[-1].rstrip(',') if cleaned else ''
        name = f'{first_name} {last_name}'.strip()

        # Also strip a leading gender/category code from affiliation (e.g. "F Saratoga" → "Saratoga")
        while affiliation_tokens:
            bare = affiliation_tokens[0].rstrip(',')
            if bare.upper() in _GENDER_CODES or _MASTERS_CODE_RE.match(bare):
                if bare.upper() in _GENDER_CODES and row_gender is None:
                    row_gender = bare.upper()
                affiliation_tokens = affiliation_tokens[1:]
                if bare.upper() == 'SCH' and affiliation_tokens:
                    affiliation_tokens = affiliation_tokens[1:]   # drop "C" after "SCH"
            else:
                break
        affiliation = ' '.join(affiliation_tokens)
    else:
        # Fallback: treat everything as name, empty affiliation.
        # Still strip leading gender/category codes.
        cleaned = []
        skip_next = False
        for tok in middle:
            if skip_next:
                skip_next = False
                continue
            bare = tok.rstrip(',')
            if bare.upper() in _GENDER_CODES or _MASTERS_CODE_RE.match(bare):
                if bare.upper() in _GENDER_CODES and row_gender is None:
                    row_gender = bare.upper()
                if bare.upper() == 'SCH':
                    skip_next = True
                continue
            cleaned.append(tok)
        name = ' '.join(cleaned)
        affiliation = ''

    if not name:
        return None

    return {
        'rank': rank,
        'bib': bib,
        'name': name,
        'affiliation': affiliation,
        'time_text': time_text,
        'time_seconds': time_seconds,
        'status': status,
        'gender': row_gender,
    }


def parse_classic_results_pdf(pdf_path: str | Path) -> ParsedEvent:
    """Parse the results section of a classic-format short track PDF."""
    pdf_path = Path(pdf_path)
    parsed = ParsedEvent(pdf_path=str(pdf_path), format=PdfFormat.CLASSIC_RESULTS)

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            # Event name from the first non-empty line of the first page
            first_text = pdf.pages[0].extract_text() or ''
            for line in first_text.split('\n'):
                line = line.strip()
                if line:
                    parsed.event_name = line
                    break

            for page_num, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ''
                if not _is_results_page(text):
                    continue

                division: Optional[str] = None
                distance_m: Optional[int] = None
                round_str: Optional[str] = None

                for line in text.split('\n'):
                    line = line.strip()
                    if not line:
                        continue

                    # Skip page header and column header lines
                    if line == parsed.event_name or _PAGE_MARKER in line:
                        continue

                    # Event header
                    ev = _parse_event_header(line)
                    if ev:
                        division, distance_m, round_str = ev
                        continue

                    # Division label line (e.g. "Novice Ladies") — kept as fallback
                    # but event headers already carry the division, so just skip.

                    if division is None or round_str is None:
                        continue

                    row = _parse_result_row(line)
                    if row is None:
                        continue

                    parsed.results.append(ParsedResult(
                        division=division,
                        distance_m=distance_m,
                        distance_raw=f'{division} {distance_m}m',
                        rank=row['rank'],
                        bib=row['bib'],
                        name=row['name'],
                        affiliation=row['affiliation'],
                        round=round_str,
                        seed_heat=None,
                        heat_assignment=None,
                        time_text=row['time_text'],
                        time_seconds=row['time_seconds'],
                        points=None,
                        status=row['status'],
                        gender=row.get('gender'),
                        page_number=page_num,
                    ))

    except Exception as e:
        parsed.parse_errors.append(f'PDF error: {e}')
        logger.error('Failed to parse %s: %s', pdf_path.name, e)

    logger.info(
        '%s → %d results from classic results pages (errors: %d)',
        pdf_path.name, len(parsed.results), len(parsed.parse_errors),
    )
    return parsed
