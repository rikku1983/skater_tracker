"""
Parser for the legacy "Competition results" / "Overall results" PDF format.

Used by most US Speedskating short track events from 2018-2022.

Two page types are relevant:
  1. "Competition results" pages — heat-by-heat per event/distance
     Sections like:
       Event N - {Division} {Distance} {Round}
       No. Final A Time Points
       1 63 James Cooley 1:23.480 1000
       No. Heat 1 of 2 Time Qual
       1 45 Alice Smith 1:24.500 Q

  2. "Overall results" pages — summary table per division
     Format:
       {Division Name} [Men/Women/Mixed]
       No. Name {dist1} {dist2} ... Points
       1 468 Tommaso Dotti 1000 262 ... 2662

For Phase 2 we extract the FINALS results (Final A/B/C rows) from the
"Competition results" pages, as these represent the canonical best result
per distance per skater.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import pdfplumber

from .base import ParsedEvent, ParsedResult, PdfFormat, _extract_distance_m
from ..utils.times import parse_time, is_time, is_status, STATUS_CODES

logger = logging.getLogger(__name__)

# ── Patterns ──────────────────────────────────────────────────────────────────

# "Event 3 - Open A F 1500 meters Semifinals"
_EVENT_HEADER_RE = re.compile(
    r'Event\s+\d+\s*[-–]\s*(.+?)\s+(\d{2,4})\s+meters?\s+(.*)',
    re.IGNORECASE,
)
# "No. Final A Time Points"  or  "No. Final B Time Points"
_FINAL_HEADER_RE = re.compile(r'^No\.\s+Final\s+([A-Z])\s+Time\s+Points', re.IGNORECASE)
# "No. Heat 1 of 2 Time Qual"
_HEAT_HEADER_RE = re.compile(r'^No\.\s+Heat\s+\d+\s+of\s+\d+\s+Time\s+Qual', re.IGNORECASE)

# Data row: "1 63 James Cooley 1:23.480 1000"
#           rank bib name... time points_or_qual
_RESULT_ROW_RE = re.compile(r'^(\d+)\s+(\d+)\s+(.+?)\s+([\d:\.]+)\s+(.+)$')

# Status-only at start: "PEN 63 James Cooley ..."  or  "ADV 63 ..."
_STATUS_ROW_RE = re.compile(r'^(DNS|DNF|DQ|PEN|ADV|DSQ)\s+(\d+)\s+(.+)', re.IGNORECASE)


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _parse_event_header(line: str) -> Optional[tuple[str, int, str]]:
    """
    Parse "Event N - Division Distance meters Round".
    Returns (division, distance_m, round_str) or None.
    """
    m = _EVENT_HEADER_RE.match(line.strip())
    if not m:
        return None
    division_raw = m.group(1).strip()
    distance_m = int(m.group(2))
    round_str = m.group(3).strip()
    return division_raw, distance_m, round_str


def _parse_final_row(line: str) -> Optional[tuple[Optional[int], str, str, Optional[float], Optional[float], Optional[str]]]:
    """
    Parse a finals result row: "1 63 James Cooley 1:23.480 1000"
    or a status row:           "PEN 63 Alice Smith -"
    Returns (rank, bib, name, time_seconds, points, status) or None.
    """
    line = line.strip()
    if not line:
        return None

    # Status row
    m = _STATUS_ROW_RE.match(line)
    if m:
        status = m.group(1).upper()
        bib = m.group(2)
        name = m.group(3).strip().split()[0:2]  # take first 2 tokens as name
        return None, bib, " ".join(name), None, None, None, status

    # Normal row
    m = _RESULT_ROW_RE.match(line)
    if not m:
        return None

    rank = int(m.group(1))
    bib = m.group(2)
    rest = m.group(3).strip()
    time_str = m.group(4)
    last_token = m.group(5).strip()

    time_sec = parse_time(time_str) if is_time(time_str) else None

    points: Optional[float] = None
    status: Optional[str] = None
    if is_status(last_token):
        status = last_token.upper()
    else:
        try:
            points = float(last_token.replace(",", ""))
        except ValueError:
            pass

    # "rest" is everything between bib and time — should be the name
    name = rest
    time_text = time_str if time_sec is not None else None

    return rank, bib, name, time_text, time_sec, points, status


# ── Page processing ───────────────────────────────────────────────────────────

def _parse_competition_page(
    text: str,
    page_number: int,
) -> list[ParsedResult]:
    """
    Extract results from a "Competition results" page.
    Only captures FINAL rows (not heats), as these are the canonical results.
    """
    results: list[ParsedResult] = []
    lines = text.split("\n")

    current_division: Optional[str] = None
    current_distance_m: Optional[int] = None
    current_distance_raw: Optional[str] = None
    in_final: bool = False
    current_final_letter: Optional[str] = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Skip page headers / footers
        if re.match(r'^Competition results', line, re.I):
            continue
        if re.match(r'^\d+\s+of\s+\d+$', line):
            continue

        # Event header
        ev = _parse_event_header(line)
        if ev:
            current_division, current_distance_m, round_str = ev
            current_distance_raw = f"{current_division} {current_distance_m}"
            in_final = "final" in round_str.lower()
            current_final_letter = None
            continue

        # Final sub-header: "No. Final A Time Points"
        m = _FINAL_HEADER_RE.match(line)
        if m:
            in_final = True
            current_final_letter = m.group(1).upper()
            continue

        # Heat sub-header — reset finals mode
        if _HEAT_HEADER_RE.match(line):
            in_final = False
            current_final_letter = None
            continue

        # Picking top N... line — ignore
        if re.match(r'^Picking top', line, re.I):
            continue

        # Try to parse a result row (only when in a final)
        if in_final and current_division and current_distance_m:
            parsed = _parse_final_row(line)
            if parsed:
                rank, bib, name, time_text, time_sec, points, status = parsed
                heat_assignment = f"Final {current_final_letter}" if current_final_letter else "Final"
                results.append(ParsedResult(
                    division=current_division,
                    distance_m=current_distance_m,
                    distance_raw=current_distance_raw or "",
                    rank=rank,
                    bib=bib,
                    name=name,
                    affiliation="",  # not available in legacy format
                    seed_heat=None,
                    heat_assignment=heat_assignment,
                    time_text=time_text,
                    time_seconds=time_sec,
                    points=points,
                    status=status,
                    page_number=page_number,
                ))

    return results


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_legacy_pdf(pdf_path: str | Path) -> ParsedEvent:
    """
    Parse a legacy-format ST protocol PDF.

    Extracts finals results from "Competition results" pages.
    """
    pdf_path = str(pdf_path)
    event = ParsedEvent(pdf_path=pdf_path, format=PdfFormat.LEGACY)

    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Metadata from cover page
            try:
                page1_text = pdf.pages[0].extract_text() or ""
                lines = [l.strip() for l in page1_text.split("\n") if l.strip()]
                event.event_name = lines[0] if lines else None
                for line in lines:
                    if re.search(r'\b(January|February|March|April|May|June|July|'
                                 r'August|September|October|November|December)\b',
                                 line, re.I):
                        event.event_date_raw = line
                        break
            except Exception:
                pass

            # Process pages
            for page_number, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                    if not text.strip():
                        continue

                    if re.search(r'competition results', text, re.I):
                        results = _parse_competition_page(text, page_number)
                        event.results.extend(results)

                except Exception as e:
                    msg = f"Page {page_number}: {e}"
                    logger.warning("%s — %s", pdf_path, msg)
                    event.parse_errors.append(msg)

    except Exception as e:
        event.parse_errors.append(f"Fatal: {e}")
        logger.error("Failed to parse %s: %s", pdf_path, e)

    return event
