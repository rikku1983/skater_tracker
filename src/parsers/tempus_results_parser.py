"""
Parser for Tempus Protocol PDFs that have "Results" pages.

Results pages contain per-heat results in a two-column layout:
  DIVISION N <name> <ROUND> <dist> m    DIVISION N <name> <ROUND> <dist> m
  RACE: N HEAT: M/T GROUP: G            RACE: N HEAT: M/T GROUP: G
  1. <bib><LAST> <First> <CLUB> <time>  ...
  <bib><LAST> <First> <CLUB> - no start

All classification pages (DISTANCE CLASSIFICATION, FINAL CLASSIFICATION,
TIME CLASSIFICATION) are skipped — only Results pages are parsed.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

import pdfplumber

from .base import ParsedEvent, ParsedResult, PdfFormat, _extract_distance_m
from ..utils.times import parse_time, TIME_RE, STATUS_CODES

logger = logging.getLogger(__name__)
RANK_RE = re.compile(r'^(\d+)\.$')
BIB_NAME_RE = re.compile(r'^(\d+)([A-Z].*)$')
AFFIL_RE = re.compile(r'^[A-Z]{2,3}(-[A-Z]{2,10})?$')
RACE_HDR_RE = re.compile(r'RACE:\s*(\d+)\s+HEAT:\s*(\d+)(?:/(\d+))?\s+GROUP:\s*(\w+)', re.I)
DIST_M_RE = re.compile(r'^(\d+(?:\.\d+)?)$')
POINTS_RE = re.compile(r'^(\d+|#{3,}|\d+E[+\-]\d+)$')

ROUND_KEYWORDS = ('HEATS', 'SEMI-FINAL', 'FINAL', 'SUPER FINAL')


def _is_results_page(page) -> bool:
    text = page.extract_text() or ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return "Results" in lines


def _split_columns(words: list, page_width: float) -> tuple[list, list]:
    """Split word list into left and right columns at the page midpoint."""
    mid = page_width / 2
    left = [w for w in words if w["x0"] < mid]
    right = [w for w in words if w["x0"] >= mid]
    return left, right


def _group_by_line(words: list, tol: int = 2) -> list[list]:
    """Group words into lines by rounding their top y-coordinate."""
    by_y: dict[int, list] = defaultdict(list)
    for w in words:
        by_y[round(w["top"] / tol) * tol].append(w)
    return [sorted(ws, key=lambda w: w["x0"]) for _, ws in sorted(by_y.items())]


def _parse_section_header(tokens: list[str]) -> Optional[tuple[str, str, Optional[int]]]:
    """
    Try to parse a section header line into (division, round, distance_m).
    e.g. ['DIVISION', '1', 'Heartland', 'W', 'A', 'HEATS', '500', 'm']
      -> ('DIVISION 1 Heartland W A', 'HEATS', 500)
    """
    text = " ".join(tokens)
    # Must end with '<dist> m' or '<dist>. Laps'
    if not (len(tokens) >= 3 and tokens[-1].lower() in ('m', 'laps')):
        return None
    dist_token = tokens[-2]
    if not re.match(r'^\d+(\.\d+)?$', dist_token):
        return None

    # Check for round keyword working backwards
    remaining = tokens[:-2]  # strip '<dist> m'
    if not remaining:
        return None

    # 'SUPER FINAL' is two tokens
    if len(remaining) >= 2 and remaining[-2] == "SUPER" and remaining[-1] == "FINAL":
        round_str = "SUPER FINAL"
        division = " ".join(remaining[:-2])
    elif remaining[-1] in ("HEATS", "SEMI-FINAL", "FINAL"):
        round_str = remaining[-1]
        division = " ".join(remaining[:-1])
    else:
        return None

    if not division:
        return None

    dist_m = _extract_distance_m(f"{dist_token} m")
    return division, round_str, dist_m


def _parse_result_row(tokens: list[str]) -> Optional[dict]:
    """
    Parse a result row. Returns dict or None.

    Formats:
      1. <bib><LAST> <First...> <CLUB> <time> [points]
      <bib><LAST> <First...> <CLUB> - no start/no finish/penalty
    """
    if not tokens:
        return None

    i = 0
    rank: Optional[int] = None

    # Optional rank prefix: '1.'
    if RANK_RE.match(tokens[0]):
        rank = int(tokens[0][:-1])
        i = 1

    if i >= len(tokens):
        return None

    # bib+lastname token: digits immediately followed by uppercase letters
    bib_match = BIB_NAME_RE.match(tokens[i])
    if not bib_match:
        return None
    bib = bib_match.group(1)
    name_start = bib_match.group(2)  # start of last name
    i += 1

    rest = tokens[i:]
    if not rest:
        return None

    # Check for status (DNS/DNF etc): look for '-' token
    status: Optional[str] = None
    time_val: Optional[str] = None
    points: Optional[float] = None
    name_tokens: list[str] = [name_start]
    affiliation: Optional[str] = None

    # Detect status token: standalone "-" separator OR a PEN/DNS/etc keyword
    status_idx = None
    if "-" in rest:
        status_idx = rest.index("-")
        before_status = rest[:status_idx]
        after_status = [t for t in rest[status_idx + 1:] if not re.match(r'^#{2,}$', t) and not (t.isdigit() and len(t) <= 2)]
        status = " ".join(after_status) if after_status else "DNS"
        if before_status and AFFIL_RE.match(before_status[-1]):
            affiliation = before_status[-1]
            name_tokens.extend(before_status[:-1])
        else:
            name_tokens.extend(before_status)
    else:
        # Check for inline status keyword (e.g. "PEN. no start", "DNS")
        for idx, tok in enumerate(rest):
            if tok.rstrip(".") in STATUS_CODES:
                status_idx = idx
                status = tok.rstrip(".")
                break
        if status_idx is not None:
            before_status = rest[:status_idx]
            if before_status and AFFIL_RE.match(before_status[-1]):
                affiliation = before_status[-1]
                name_tokens.extend(before_status[:-1])
            else:
                name_tokens.extend(before_status)
    if status_idx is not None:
        pass  # already handled above
    else:
        # Scan from right: [points] time affiliation name...
        j = len(rest) - 1
        # Optional trailing points: integer or '####'
        if j >= 0 and POINTS_RE.match(rest[j]):
            pts_tok = rest[j]
            if pts_tok.isdigit():
                points = float(pts_tok)
            j -= 1
        # Time
        if j >= 0 and TIME_RE.match(rest[j]):
            time_val = rest[j]
            j -= 1
        # Affiliation
        if j >= 0 and AFFIL_RE.match(rest[j]):
            affiliation = rest[j]
            j -= 1
        # Everything remaining is name continuation
        name_tokens.extend(rest[:j + 1])

    full_name = " ".join(name_tokens).strip()
    if not full_name:
        return None

    return {
        "rank": rank,
        "bib": bib,
        "name": full_name,
        "affiliation": affiliation or "",
        "time_val": time_val,
        "points": points,
        "status": status,
    }


def _parse_column(lines: list[list], page_num: int, state: dict) -> tuple[list[ParsedResult], list[str]]:
    """
    Parse one column's lines into ParsedResults, carrying context state across calls.
    state keys: division, round, distance_m, race_num, heat_label
    """
    results: list[ParsedResult] = []
    errors: list[str] = []

    for line_words in lines:
        tokens = [w["text"] for w in line_words]
        if not tokens:
            continue
        text = " ".join(tokens)

        # Skip page-level headers
        if text in ("Results",) or re.match(r'^20\d{2}$', text):
            continue

        # Race header: RACE: N HEAT: M/T GROUP: G
        race_match = RACE_HDR_RE.search(text)
        if race_match:
            race_n = race_match.group(1)
            heat_n = race_match.group(2)
            heat_t = race_match.group(3)  # may be None
            group = race_match.group(4)
            state["race_num"] = race_n
            heat_str = f"Heat {heat_n}/{heat_t}" if heat_t else f"Heat {heat_n}"
            state["heat_label"] = f"{heat_str} Group {group}"
            continue

        # Section header: ends with '<dist> m' or '<dist> Laps' with round keyword
        parsed_hdr = _parse_section_header(tokens)
        if parsed_hdr:
            division, round_str, dist_m = parsed_hdr
            state["division"] = division
            state["round"] = round_str
            state["distance_m"] = dist_m
            state["race_num"] = None
            state["heat_label"] = None
            continue

        # Result row
        if state.get("division") is None:
            continue

        row = _parse_result_row(tokens)
        if row is None:
            continue

        heat_str = state.get("heat_label")
        if state.get("race_num"):
            round_full = f"Race {state['race_num']} {state.get('round', '')} {heat_str or ''}".strip()
        else:
            round_full = f"{state.get('round', '')} {heat_str or ''}".strip()

        time_seconds = parse_time(row["time_val"]) if row["time_val"] else None

        results.append(ParsedResult(
            division=state["division"],
            distance_m=state["distance_m"],
            distance_raw=f"{state['division']} {state.get('distance_m', '')}m",
            rank=row["rank"],
            bib=row["bib"],
            name=row["name"],
            affiliation=row["affiliation"],
            round=round_full or None,
            seed_heat=None,
            heat_assignment=None,
            time_text=row["time_val"],
            time_seconds=time_seconds,
            points=row["points"],
            status=row["status"],
            page_number=page_num,
        ))

    return results, errors


def parse_tempus_results_pdf(pdf_path: str | Path) -> ParsedEvent:
    """Parse all 'Results' pages from a Tempus Protocol PDF."""
    pdf_path = Path(pdf_path)
    parsed = ParsedEvent(pdf_path=str(pdf_path), format=PdfFormat.TEMPUS_RESULTS)

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            # Extract event name from first text page
            for page in pdf.pages[:5]:
                text = page.extract_text() or ""
                first_line = next((l.strip() for l in text.split("\n") if l.strip()), None)
                if first_line and first_line not in ("Results",):
                    parsed.event_name = first_line
                    break

            # Shared state carried across pages (sections can span pages)
            left_state: dict = {"division": None, "round": None, "distance_m": None,
                                "race_num": None, "heat_label": None}
            right_state: dict = {"division": None, "round": None, "distance_m": None,
                                 "race_num": None, "heat_label": None}

            for i, page in enumerate(pdf.pages, 1):
                if not _is_results_page(page):
                    continue

                words = page.extract_words()
                if not words:
                    continue

                left_words, right_words = _split_columns(words, page.width)
                left_lines = _group_by_line(left_words)
                right_lines = _group_by_line(right_words)

                l_results, l_errors = _parse_column(left_lines, i, left_state)
                r_results, r_errors = _parse_column(right_lines, i, right_state)

                parsed.results.extend(l_results)
                parsed.results.extend(r_results)
                if l_errors or r_errors:
                    parsed.parse_errors.extend([f"p{i}: {e}" for e in l_errors + r_errors])

    except Exception as e:
        parsed.parse_errors.append(f"PDF error: {e}")
        logger.error("Failed to parse %s: %s", pdf_path.name, e)

    logger.info(
        "%s → %d results from Results pages (errors: %d)",
        pdf_path.name, len(parsed.results), len(parsed.parse_errors),
    )
    return parsed
