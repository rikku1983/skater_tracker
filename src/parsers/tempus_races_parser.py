"""
Parser for Tempus per-heat export PDFs (no "All Races / RESULTS" header).

Each page contains one or more heats:
  <Division> <Round> Event #N       ← e.g. "Group A Combined E's 333 Quarter-Finals Event #1"
  Heat M of T  Race #RRRR
  # Name  Affiliation  Time  Qual.
  <rank> <bib> <name...>  <affil...>  <time>  [Q/ADV]
  <bib> <name...>  <affil...>  DNS/DNF/DQ/FNT

Column positions are anchored from the "# Name Affiliation Time Qual." header row.

_find_col_positions, _parse_result_row, and _parse_heat_page are also used by
speedskating_pro_parser via import.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

import pdfplumber

from .base import ParsedEvent, ParsedResult, PdfFormat, _extract_distance_m
from ..utils.times import parse_time, TIME_RE, STATUS_CODES, QUAL_FLAGS

logger = logging.getLogger(__name__)

_SPECIAL = STATUS_CODES | QUAL_FLAGS
HEAT_HDR_RE = re.compile(r'Heat\s+(\d+)\s+of\s+(\d+)', re.I)
RACE_NUM_RE = re.compile(r'Race\s+(#\d+)', re.I)
EVENT_HDR_RE = re.compile(r'Event\s+(#\d+)', re.I)


def _is_races_page(page) -> bool:
    """Page has heat results: Event #N + Race # + '# Name Affiliation Time' column header."""
    text = page.extract_text() or ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return False
    has_heat_cols = any(l.startswith("# Name Affiliation Time") for l in lines)
    has_event_n   = any(re.search(r'Event\s+#\d+', l) for l in lines)
    if not (has_heat_cols and has_event_n):
        return False
    # Variant A: Event #N on the first line (STDC style)
    if re.search(r'Event\s+#\d+', lines[0]):
        return True
    # Variant B: RESULTS line present (Great Lakes style)
    return "RESULTS" in lines


def _find_col_positions(line_words: list, require_skater: bool = False) -> Optional[dict]:
    """Extract column x-positions from the column-header row.

    When require_skater=True, expects "Skater # Name Affiliation" (Speedskating Pro).
    When False, expects standalone "# Name Affiliation" (Tempus races).
    """
    texts = [w["text"] for w in line_words]
    if require_skater:
        if "Skater" not in texts or "Name" not in texts or "Affiliation" not in texts:
            return None
    else:
        if "#" not in texts or "Name" not in texts or "Affiliation" not in texts:
            return None
        # Exclude "Race #101" lines where '#' is attached to a number
        if not [w for w in line_words if w["text"] == "#"]:
            return None

    cols: dict = {}
    for w in line_words:
        t = w["text"]
        if t == "#":
            cols["bib_x"] = w["x0"]
        elif t == "Name":
            cols["name_x"] = w["x0"]
        elif t == "Affiliation":
            cols["affil_x"] = w["x0"]
        elif t == "Time":
            cols["time_x"] = w["x0"]
        elif t == "Qual.":
            cols["qual_x"] = w["x0"]
    return cols if "name_x" in cols and "affil_x" in cols else None


def _parse_result_row(line_words: list, cols: dict, bib_x_tol: float = 15) -> Optional[dict]:
    """Parse one result row using column x-positions."""
    if not line_words:
        return None

    bib_x = cols["bib_x"]
    name_x = cols["name_x"]
    affil_x = cols["affil_x"]
    time_x = cols.get("time_x", affil_x + 200)
    qual_x = cols.get("qual_x", time_x + 40)

    rank_words  = [w for w in line_words if w["x0"] < bib_x - bib_x_tol]
    bib_words   = [w for w in line_words if bib_x - bib_x_tol <= w["x0"] < name_x - 2]
    name_words  = [w for w in line_words if name_x - 2 <= w["x0"] < affil_x - 2]
    affil_words = [w for w in line_words if affil_x - 2 <= w["x0"] < time_x - 2]
    time_words  = [w for w in line_words if time_x - 20 <= w["x0"] < qual_x - 2]
    qual_words  = [w for w in line_words if w["x0"] >= qual_x - 2]

    if not bib_words or not bib_words[0]["text"].isdigit():
        return None

    bib = bib_words[0]["text"]
    rank = int(rank_words[0]["text"]) if rank_words and rank_words[0]["text"].isdigit() else None
    name = " ".join(w["text"] for w in name_words).strip()
    affiliation = " ".join(w["text"] for w in affil_words).strip()

    time_val: Optional[str] = None
    for w in time_words:
        if TIME_RE.match(w["text"]):
            time_val = w["text"]
            break

    status: Optional[str] = None
    candidates = [w["text"] for w in qual_words] + [w["text"] for w in time_words if w["text"] in _SPECIAL]
    for t in candidates:
        if t in STATUS_CODES and t not in QUAL_FLAGS:
            status = t
            break

    if not name:
        return None

    return {
        "rank": rank,
        "bib": bib,
        "name": name,
        "affiliation": affiliation,
        "time_val": time_val,
        "status": status,
    }


def _parse_heat_page(
    page,
    bib_x_tol: float = 15,
    col_header_requires_skater: bool = False,
    skip_line_fn: Optional[Callable[[str], bool]] = None,
) -> list[dict]:
    """Parse one heat-results page into raw row dicts.

    Each dict: division, heat_label, rank, bib, name, affiliation, time_val, status.
    Callers convert to ParsedResult with format-specific fields (distance_m, etc.).
    """
    words = page.extract_words()
    if not words:
        return []

    by_y: dict[int, list] = defaultdict(list)
    for w in words:
        by_y[round(w["top"])].append(w)
    lines = [(y, sorted(ws, key=lambda w: w["x0"])) for y, ws in sorted(by_y.items())]

    division: Optional[str] = None
    heat_label: Optional[str] = None
    cols: Optional[dict] = None
    rows: list[dict] = []

    for _, line_words in lines:
        texts = [w["text"] for w in line_words]
        text_str = " ".join(texts)

        if skip_line_fn and skip_line_fn(text_str):
            continue

        col_pos = _find_col_positions(line_words, require_skater=col_header_requires_skater)
        if col_pos:
            cols = col_pos
            continue

        heat_match = HEAT_HDR_RE.search(text_str)
        if heat_match:
            heat_n = heat_match.group(1)
            heat_t = heat_match.group(2)
            race_match = RACE_NUM_RE.search(text_str)
            race_str = f" {race_match.group(1)}" if race_match else ""
            heat_label = f"Heat {heat_n} of {heat_t}{race_str}"
            cols = None
            continue

        race_match = RACE_NUM_RE.search(text_str)
        if race_match and not HEAT_HDR_RE.search(text_str):
            race_idx = text_str.index("Race")
            round_label = text_str[:race_idx].strip()
            heat_label = f"{round_label} {race_match.group(1)}" if round_label else race_match.group(1)
            cols = None
            continue

        event_match = EVENT_HDR_RE.search(text_str)
        if event_match:
            ev_idx = texts.index("Event") if "Event" in texts else -1
            division = " ".join(texts[:ev_idx]).strip() if ev_idx > 0 else text_str
            heat_label = None
            cols = None
            continue

        if cols is None or division is None or heat_label is None:
            continue

        row = _parse_result_row(line_words, cols, bib_x_tol=bib_x_tol)
        if row:
            rows.append({**row, "division": division, "heat_label": heat_label})

    return rows


def parse_tempus_races_pdf(pdf_path: str | Path) -> ParsedEvent:
    """Parse all per-heat pages from a Tempus races export PDF."""
    pdf_path = Path(pdf_path)
    parsed = ParsedEvent(pdf_path=str(pdf_path), format=PdfFormat.TEMPUS_RACES)

    def _skip(text_str: str) -> bool:
        return text_str.startswith("Tempus Competition Software")

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages[:3]:
                text = page.extract_text() or ""
                for line in text.split("\n"):
                    line = line.strip()
                    if line and not re.search(r'Event\s+#\d+', line):
                        parsed.event_name = line
                        break
                if parsed.event_name:
                    break

            for page_num, page in enumerate(pdf.pages, 1):
                if not _is_races_page(page):
                    continue

                for row in _parse_heat_page(page, bib_x_tol=15, skip_line_fn=_skip):
                    division = row["division"]
                    round_str = f"{division} {row['heat_label']}".strip()
                    parsed.results.append(ParsedResult(
                        division=division,
                        distance_m=_extract_distance_m(division),
                        distance_raw=division,
                        rank=row["rank"],
                        bib=row["bib"],
                        name=row["name"],
                        affiliation=row["affiliation"],
                        round=round_str,
                        seed_heat=None,
                        heat_assignment=None,
                        time_text=row["time_val"],
                        time_seconds=parse_time(row["time_val"]) if row["time_val"] else None,
                        points=None,
                        status=row["status"],
                        page_number=page_num,
                    ))

    except Exception as e:
        parsed.parse_errors.append(f"PDF error: {e}")
        logger.error("Failed to parse %s: %s", pdf_path.name, e)

    logger.info(
        "%s → %d results from per-heat pages (errors: %d)",
        pdf_path.name, len(parsed.results), len(parsed.parse_errors),
    )
    return parsed
