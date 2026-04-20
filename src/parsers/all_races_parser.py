"""
Parser for Tempus Competition Software PDFs that contain "All Races RESULTS" pages.

These PDFs (2023-present) include per-heat results broken down as:
  <Event Title>  (e.g. "Brickton's 222 Finals  Event #1")
    <Heat>  (e.g. "A Final  Race #101")
      # Name  Affiliation  Time  [Qual.]
      <rank> <bib> <name>  <affiliation>  <time>  [status]
      ...

Parsing strategy: use word x-coordinates to separate columns.
The column header "# Name Affiliation Time" anchors column positions.
Numbers to the left of the Name column are rank + bib (2) or just bib (1).
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

import pdfplumber

from .base import ParsedEvent, ParsedResult, PdfFormat, _extract_distance_m
from ..utils.times import parse_time, TIME_RE, STATUS_CODES, QUAL_FLAGS

logger = logging.getLogger(__name__)

_SPECIAL = STATUS_CODES | QUAL_FLAGS
EVENT_NUM_RE = re.compile(r"Event\s+(#\d+)")
RACE_NUM_RE = re.compile(r"Race\s+(#\d+)")
DIST_RE = re.compile(r"'s\s+(\d{3,4})\b")
DIV_RE = re.compile(r"^(?:Group\s+\w+\s+)?(.+?)'s\s+\d")


def _is_all_races_page(page) -> bool:
    text = page.extract_text() or ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for i in range(len(lines) - 1):
        if lines[i] == "All Races" and lines[i + 1] == "RESULTS":
            return True
    return False


def _parse_page(page, page_num: int) -> tuple[list[ParsedResult], list[str]]:
    """Parse one All Races RESULTS page. Returns (results, errors)."""
    words = page.extract_words()
    if not words:
        return [], ["no words extracted"]

    # Group words by line (round y to nearest pixel)
    by_y: dict[int, list] = defaultdict(list)
    for w in words:
        by_y[round(w["top"])].append(w)
    sorted_lines = [(y, sorted(ws, key=lambda w: w["x0"])) for y, ws in sorted(by_y.items())]

    # Find column positions from the "# Name Affiliation Time" header row
    col: dict[str, float] = {}
    for _, line_words in sorted_lines:
        texts = [w["text"] for w in line_words]
        if "#" in texts and "Name" in texts and "Affiliation" in texts and "Time" in texts:
            for w in line_words:
                t = w["text"]
                if t == "#":
                    col["hash_x"] = w["x0"]
                elif t == "Name":
                    col["name_x"] = w["x0"]
                elif t == "Affiliation":
                    col["affil_x"] = w["x0"]
                elif t == "Time":
                    col["time_x"] = w["x0"]
            break

    if "name_x" not in col or "affil_x" not in col:
        return [], ["no column header found"]

    results: list[ParsedResult] = []
    errors: list[str] = []

    current_division: Optional[str] = None
    current_distance: Optional[int] = None
    current_event_title: Optional[str] = None
    current_heat: Optional[str] = None
    current_race_num: Optional[str] = None

    for _, line_words in sorted_lines:
        texts = [w["text"] for w in line_words]
        text_str = " ".join(texts)

        # Skip page-level headers / footer
        if text_str in ("All Races", "RESULTS"):
            continue
        if texts and texts[0] in ("Tempus", "Page"):
            continue
        if "#" in texts and "Name" in texts and "Affiliation" in texts:
            continue  # column header

        # Event section header: contains "Event #N"
        if "Event" in texts:
            ev_match = EVENT_NUM_RE.search(text_str)
            if ev_match:
                ev_idx = texts.index("Event")
                title = " ".join(texts[:ev_idx]).rstrip("- ")
                dist_match = DIST_RE.search(title)
                current_distance = int(dist_match.group(1)) if dist_match else _extract_distance_m(title)
                div_match = DIV_RE.search(title)
                current_division = div_match.group(1).strip() if div_match else title.strip()
                current_event_title = title
                current_heat = None
                current_race_num = None
                continue

        # Continuation line for wrapped event header (e.g. lone "Finals" after line break)
        stripped = text_str.strip()
        if stripped in ("Finals", "Semi-Finals", "Final Finals", "Super Final Finals", "Super Final"):
            # Don't reset current context — just skip
            continue

        # Heat/race header: contains "Race #N"
        if "Race" in texts:
            race_match = RACE_NUM_RE.search(text_str)
            if race_match:
                race_idx = texts.index("Race")
                current_heat = " ".join(texts[:race_idx]).strip()
                current_race_num = race_match.group(1)
                continue

        # Result row: must have a time or a status keyword
        has_time = any(TIME_RE.match(t) for t in texts)
        has_status = any(t in _SPECIAL for t in texts)
        if not (has_time or has_status):
            continue
        if len(line_words) < 3:
            continue
        if current_division is None or current_heat is None:
            continue  # not in a result section yet

        # Numbers strictly left of the name column → rank and/or bib
        pre_name = sorted(
            [w for w in line_words if w["x0"] < col["name_x"] - 2 and w["text"].isdigit()],
            key=lambda w: w["x0"],
        )
        time_val: Optional[str] = next((w["text"] for w in line_words if TIME_RE.match(w["text"])), None)
        status: Optional[str] = next((w["text"] for w in line_words if w["text"] in _SPECIAL), None)
        # Column header positions are slightly right of actual data.
        # Name data can start a few pixels left of the "Name" header, so anchor
        # the left bound at the bib (#) column. Affiliation data can start ~17px
        # left of the "Affiliation" header, so use -20 as tolerance.
        affil_threshold = col["affil_x"] - 20
        name_parts = [
            w["text"] for w in line_words
            if w["x0"] > col["hash_x"] + 2
            and w["x0"] < affil_threshold
            and not w["text"].isdigit()
            and not TIME_RE.match(w["text"])
            and w["text"] not in _SPECIAL
        ]
        affil_parts = [
            w["text"] for w in line_words
            if w["x0"] >= affil_threshold
            and not TIME_RE.match(w["text"])
            and w["text"] not in _SPECIAL
        ]

        if len(pre_name) == 2:
            rank: Optional[int] = int(pre_name[0]["text"])
            bib: Optional[str] = pre_name[1]["text"]
        elif len(pre_name) == 1:
            rank = None
            bib = pre_name[0]["text"]
        else:
            errors.append(f"unexpected num count ({len(pre_name)}): {text_str[:80]}")
            continue

        if not name_parts:
            errors.append(f"no name parsed: {text_str[:80]}")
            continue

        time_seconds: Optional[float] = parse_time(time_val) if time_val else None
        distance_raw = current_event_title or current_division or ""

        results.append(ParsedResult(
            division=current_division,
            distance_m=current_distance,
            distance_raw=distance_raw,
            rank=rank,
            bib=bib,
            name=" ".join(name_parts),
            affiliation=" ".join(affil_parts),
            round=current_heat,
            seed_heat=None,
            heat_assignment=None,
            time_text=time_val,
            time_seconds=time_seconds,
            points=None,
            status=status,
            page_number=page_num,
        ))

    return results, errors


def parse_all_races_pdf(pdf_path: str | Path) -> ParsedEvent:
    """Parse all 'All Races RESULTS' pages from a Tempus PDF."""
    pdf_path = Path(pdf_path)
    parsed = ParsedEvent(pdf_path=str(pdf_path), format=PdfFormat.ALL_RACES)

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            # Extract event name from the first text page
            for page in pdf.pages[:3]:
                text = page.extract_text() or ""
                first_line = next((l.strip() for l in text.split("\n") if l.strip()), None)
                if first_line:
                    parsed.event_name = first_line
                    break

            for i, page in enumerate(pdf.pages, 1):
                if not _is_all_races_page(page):
                    continue
                results, errors = _parse_page(page, i)
                parsed.results.extend(results)
                if errors:
                    parsed.parse_errors.extend([f"p{i}: {e}" for e in errors])

    except Exception as e:
        parsed.parse_errors.append(f"PDF error: {e}")
        logger.error("Failed to parse %s: %s", pdf_path.name, e)

    logger.info(
        "%s → %d results from All Races pages (errors: %d)",
        pdf_path.name, len(parsed.results), len(parsed.parse_errors),
    )
    return parsed
