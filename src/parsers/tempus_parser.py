"""
Parser for the Tempus Competition Software "DISTANCE CLASSIFICATION" PDF format.

This format is used by most US Speedskating short track events from 2023-2024 onwards,
and by some events as early as 2022.

Page structure (per distance result):
  Line 1: Event name (e.g. "2026 Presidential Cup & NEST #4")
  Line 2: Division + Distance (e.g. "Anacostia's 500")
  Line 3: "DISTANCE CLASSIFICATION"
  Line 4: Division + Distance (repeated)
  Line 5: Column header ("Points # Name Affiliation [Q ][S ]F Best")
  Lines 6..N: Data rows
  Footer: "Tempus Competition Software Printed on ..."  (optional)
  Footer: "Page X of Y"

Columns detected dynamically from the header row x-positions:
  - rank       (leftmost)
  - points     (~x0=68)
  - bib        (~x0=121)
  - name       (~x0=158)
  - affiliation (~x0=280)
  - Q heat     (~x0=420)  optional
  - S heat     (~x0=454)  optional
  - F heat     (~x0=490)
  - Best time  (~x0=525)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber

from .base import ParsedEvent, ParsedResult, PdfFormat, _extract_distance_m
from ..utils.times import parse_time, is_time, is_status, STATUS_CODES

logger = logging.getLogger(__name__)

# Tolerance in PDF points for grouping words into the same row
ROW_Y_TOLERANCE = 3.0
# X-tolerance for column boundary matching (header word x vs data word x)
COL_X_TOLERANCE = 20.0


# ── Word/Row helpers ──────────────────────────────────────────────────────────

@dataclass
class Word:
    text: str
    x0: float
    y0: float


def _extract_words(page) -> list[Word]:
    raw = page.extract_words(keep_blank_chars=False, x_tolerance=2, y_tolerance=2)
    return [Word(text=w["text"], x0=w["x0"], y0=w["top"]) for w in raw]


def _group_by_row(words: list[Word], y_tol: float = ROW_Y_TOLERANCE) -> list[list[Word]]:
    """Group words that share approximately the same y0 into rows."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (round(w.y0 / y_tol), w.x0))
    rows: list[list[Word]] = []
    current_row: list[Word] = []
    current_y: float | None = None

    for w in sorted_words:
        if current_y is None or abs(w.y0 - current_y) <= y_tol:
            current_row.append(w)
            current_y = w.y0
        else:
            if current_row:
                rows.append(current_row)
            current_row = [w]
            current_y = w.y0

    if current_row:
        rows.append(current_row)
    return rows


# ── Column schema ─────────────────────────────────────────────────────────────

@dataclass
class ColumnSchema:
    """X-position of each column header word, derived from the header row."""
    rank_x: float       # the leftmost column (rank number, not labeled)
    points_x: float
    bib_x: float        # labeled "#"
    name_x: float
    affiliation_x: float
    q_x: Optional[float]   # quarterfinal heat (optional)
    s_x: Optional[float]   # seed heat (optional)
    f_x: float             # final heat
    best_x: float          # best time


def _parse_column_schema(header_row: list[Word]) -> Optional[ColumnSchema]:
    """
    Build a ColumnSchema from the column header row words.
    Returns None if the header doesn't match the expected pattern.
    """
    texts = [w.text for w in header_row]
    joined = " ".join(texts)

    # Must contain at least these mandatory columns
    if "Points" not in texts or "Name" not in texts or "Best" not in texts:
        return None

    col = {w.text: w.x0 for w in header_row}

    # The rank column is to the left of "Points" and has no label
    # Estimate it as slightly to the left of the Points column
    points_x = col.get("Points", 0)

    # "#" header for bib
    bib_x = col.get("#", points_x + 40)

    name_x = col.get("Name", bib_x + 30)
    affil_x = col.get("Affiliation", name_x + 120)

    q_x = col.get("Q", None)
    s_x = col.get("S", None)
    f_x = col.get("F", affil_x + 160)
    best_x = col.get("Best", f_x + 40)

    return ColumnSchema(
        rank_x=points_x - 30,
        points_x=points_x,
        bib_x=bib_x,
        name_x=name_x,
        affiliation_x=affil_x,
        q_x=q_x,
        s_x=s_x,
        f_x=f_x,
        best_x=best_x,
    )


# ── Heat code detection ───────────────────────────────────────────────────────

_HEAT_CODE_RE = re.compile(r'^[A-Z]\d+$')   # A1, B2, C3 etc.


def _is_heat_code(token: str) -> bool:
    return bool(_HEAT_CODE_RE.fullmatch(token.strip()))


def _is_heat_or_status(token: str) -> bool:
    return _is_heat_code(token) or is_status(token)


# ── Row parsing ───────────────────────────────────────────────────────────────

def _assign_word_to_column(word: Word, schema: ColumnSchema) -> str:
    """
    Assign a word to a named column based on x-position proximity.
    Returns column name: "rank", "points", "bib", "name", "affiliation",
                         "q_heat", "s_heat", "f_heat", "best"
    """
    x = word.x0
    t = COL_X_TOLERANCE

    # Check columns right-to-left (so we correctly attribute words near boundaries)
    if schema.best_x and x >= schema.best_x - t:
        return "best"
    if x >= schema.f_x - t:
        return "f_heat"
    if schema.s_x and x >= schema.s_x - t:
        return "s_heat"
    if schema.q_x and x >= schema.q_x - t:
        return "q_heat"
    if x >= schema.affiliation_x - t:
        return "affiliation"
    if x >= schema.name_x - t:
        return "name"
    if x >= schema.bib_x - t:
        return "bib"
    return "rank_or_points"  # rank and points are close together on the left


def _parse_result_row(
    row_words: list[Word],
    schema: ColumnSchema,
    division: str,
    distance_raw: str,
    page_number: int,
) -> Optional[ParsedResult]:
    """
    Parse one data row into a ParsedResult.
    Returns None if the row doesn't look like a result row.
    """
    if not row_words:
        return None

    # Classify words into column buckets
    buckets: dict[str, list[str]] = {
        "rank_or_points": [], "bib": [], "name": [], "affiliation": [],
        "q_heat": [], "s_heat": [], "f_heat": [], "best": []
    }
    for word in row_words:
        col = _assign_word_to_column(word, schema)
        buckets[col].append(word.text)

    # The rank_or_points bucket contains: [rank, points] or just [rank] if points is missing
    rp = buckets["rank_or_points"]
    if not rp:
        return None

    # First token should be the rank (integer)
    rank: Optional[int] = None
    points: Optional[float] = None

    try:
        rank = int(rp[0])
    except (ValueError, IndexError):
        # Not a result row (e.g. header, footer)
        return None

    if len(rp) >= 2:
        try:
            # Points may be formatted as "10,000" or "8,500"
            points = float(rp[1].replace(",", ""))
        except ValueError:
            pass

    # Bib
    bib_tokens = buckets["bib"]
    bib = bib_tokens[0] if bib_tokens else None

    # Name (2 words typically, may have * suffix on female skaters)
    name = " ".join(buckets["name"]).strip()
    if not name:
        return None

    # Affiliation (multi-word club name)
    affiliation = " ".join(buckets["affiliation"]).strip()

    # Heat columns
    q_heat = " ".join(buckets["q_heat"]).strip() or None
    s_heat = " ".join(buckets["s_heat"]).strip() or None
    f_heat_tokens = buckets["f_heat"]
    f_heat = f_heat_tokens[0] if f_heat_tokens else None

    # Best time
    best_tokens = buckets["best"]
    time_text = best_tokens[0] if best_tokens else None

    # Resolve status vs heat vs time
    # The f_heat column sometimes holds a status code (PEN, DNF) instead of a heat
    status: Optional[str] = None

    if f_heat and is_status(f_heat):
        status = f_heat.upper()
        f_heat = None

    if time_text and is_status(time_text):
        # No valid time — the "best" slot has a status (e.g. DNS)
        if not status:
            status = time_text.upper()
        time_text = None

    time_seconds = parse_time(time_text) if time_text else None

    return ParsedResult(
        division=division,
        distance_m=_extract_distance_m(distance_raw),
        distance_raw=distance_raw,
        rank=rank,
        bib=bib,
        name=name,
        affiliation=affiliation,
        seed_heat=s_heat,
        heat_assignment=f_heat,
        time_text=time_text,
        time_seconds=time_seconds,
        points=points,
        status=status,
        page_number=page_number,
    )


# ── Page-level parsing ────────────────────────────────────────────────────────

def _is_distance_classification_page(rows: list[list[Word]]) -> bool:
    """Return True if this page is a DISTANCE CLASSIFICATION page."""
    for row in rows[:6]:
        row_text = " ".join(w.text for w in row)
        if "DISTANCE" in row_text and "CLASSIFICATION" in row_text:
            return True
    return False


def _parse_dc_page(
    page,
    page_number: int,
) -> Optional[tuple[str, str, list[ParsedResult]]]:
    """
    Parse one DISTANCE CLASSIFICATION page.
    Returns (division, distance_raw, results) or None if not a DC page.
    """
    words = _extract_words(page)
    rows = _group_by_row(words)

    if not _is_distance_classification_page(rows):
        return None

    # Find "DISTANCE CLASSIFICATION" row index
    dc_row_idx = None
    for i, row in enumerate(rows):
        joined = " ".join(w.text for w in row)
        if "DISTANCE" in joined and "CLASSIFICATION" in joined:
            dc_row_idx = i
            break

    if dc_row_idx is None:
        return None

    # The row immediately before "DISTANCE CLASSIFICATION" is the division+distance header
    # (e.g. "Anacostia's 500")
    if dc_row_idx > 0:
        division_row = rows[dc_row_idx - 1]
        distance_raw = " ".join(w.text for w in division_row).strip()
    else:
        distance_raw = ""

    # After "DISTANCE CLASSIFICATION" comes another division+distance line, then the column header
    # Find the column header row (contains "Points" and "Name" and "Best")
    schema: Optional[ColumnSchema] = None
    schema_row_idx = None
    for i in range(dc_row_idx + 1, min(dc_row_idx + 5, len(rows))):
        row = rows[i]
        schema = _parse_column_schema(row)
        if schema is not None:
            schema_row_idx = i
            break

    if schema is None or schema_row_idx is None:
        logger.debug("Could not find column header on DC page %d", page_number)
        return None

    # Parse the division name — from the distance_raw, strip the distance number
    # "Anacostia's 500" → division="Anacostia", distance=500
    # "Junior D Women's 1000" → division="Junior D Women"
    # "NEST MEN A 777" → division="NEST MEN A"
    division = _extract_division_name(distance_raw)

    # Parse data rows (everything after the column header until footer)
    results: list[ParsedResult] = []
    for row in rows[schema_row_idx + 1:]:
        row_text = " ".join(w.text for w in row)
        # Stop at footer lines
        if "Tempus" in row_text or "Page " in row_text:
            break
        result = _parse_result_row(row, schema, division, distance_raw, page_number)
        if result is not None:
            results.append(result)

    return division, distance_raw, results


def _extract_division_name(distance_raw: str) -> str:
    """
    Extract the division name from a combined division+distance string.

    "Anacostia's 500"         → "Anacostia"
    "Anacostia's 1500 Super Final" → "Anacostia"
    "Junior D Women's 1000"   → "Junior D Women"
    "NEST MEN A 777"          → "NEST MEN A"
    "Combined C's 500"        → "Combined C"
    """
    # Remove possessive "'s" and trailing distance number
    name = re.sub(r"'s?\s*\d+.*$", "", distance_raw, flags=re.IGNORECASE).strip()
    if name:
        return name
    # Fallback: remove trailing number
    name = re.sub(r'\s+\d+.*$', '', distance_raw).strip()
    return name or distance_raw


# ── Event metadata ────────────────────────────────────────────────────────────

def _parse_event_metadata(pdf) -> dict:
    """Extract event name, date, venue from the cover page (page 1)."""
    meta = {}
    try:
        page1 = pdf.pages[0]
        text = page1.extract_text() or ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if lines:
            meta["event_name"] = lines[0]
        # Look for date pattern
        for line in lines:
            if re.search(r'\b(January|February|March|April|May|June|July|August|'
                         r'September|October|November|December)\b', line, re.I):
                meta["event_date_raw"] = line
                break
        # Venue is often 2nd or 3rd non-empty line after date
        if len(lines) >= 3:
            meta["venue"] = lines[2] if len(lines) > 2 else None
    except Exception as e:
        logger.debug("Could not parse event metadata: %s", e)
    return meta


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_tempus_pdf(pdf_path: str | Path) -> ParsedEvent:
    """
    Parse a Tempus-format ST protocol PDF.

    Returns a ParsedEvent with all DISTANCE CLASSIFICATION results extracted.
    """
    pdf_path = str(pdf_path)
    event = ParsedEvent(pdf_path=pdf_path, format=PdfFormat.TEMPUS)

    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Metadata from cover page
            meta = _parse_event_metadata(pdf)
            event.event_name = meta.get("event_name")
            event.event_date_raw = meta.get("event_date_raw")
            event.venue = meta.get("venue")

            # Parse every page for DC pages
            for page_number, page in enumerate(pdf.pages, start=1):
                try:
                    result = _parse_dc_page(page, page_number)
                    if result is not None:
                        _, _, results = result
                        event.results.extend(results)
                except Exception as e:
                    msg = f"Page {page_number}: {e}"
                    logger.warning("%s — %s", pdf_path, msg)
                    event.parse_errors.append(msg)

    except Exception as e:
        event.parse_errors.append(f"Fatal: {e}")
        logger.error("Failed to parse %s: %s", pdf_path, e)

    return event
