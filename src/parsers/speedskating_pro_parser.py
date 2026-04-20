"""
Parser for Speedskating Pro web-exported PDFs.

Each page is one heat, printed from the Speedskating Pro website:
  <timestamp>                         ← browser print header
  <Event Title>
  RESULTS
  <Division> <Scoring> Event #N       ← e.g. "Group A Picking 3 Event #1"
  Heat M of T  Race #RRRR
  Skater # Name  Affiliation  Time  Qual.
  <rank> <bib> <first> <last>  <affiliation...>  <time>  [Q/ADV]
  <bib> <first> <last>  <affiliation...>  DNS/DNF/DQ

Column positions are anchored from the "Skater # Name Affiliation Time Qual." header row.
Shared column/row parsing logic lives in tempus_races_parser.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pdfplumber

from .base import ParsedEvent, ParsedResult, PdfFormat
from .tempus_races_parser import _parse_heat_page
from ..utils.times import parse_time

logger = logging.getLogger(__name__)


def _is_results_page(page) -> bool:
    text = page.extract_text() or ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return "RESULTS" in lines


def _skip_sp_line(text_str: str) -> bool:
    """Skip browser print header/footer lines."""
    return (
        bool(re.match(r'^\d+/\d+/\d+', text_str))
        or text_str.startswith("file://")
        or text_str == "RESULTS"
    )


def parse_speedskating_pro_pdf(pdf_path: str | Path) -> ParsedEvent:
    """Parse all RESULTS pages from a Speedskating Pro web-exported PDF."""
    pdf_path = Path(pdf_path)
    parsed = ParsedEvent(pdf_path=str(pdf_path), format=PdfFormat.SPEEDSKATING_PRO)

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages[:5]:
                text = page.extract_text() or ""
                for line in text.split("\n"):
                    line = line.strip()
                    if line and not re.match(r'^\d+/\d+/\d+', line):
                        parsed.event_name = line
                        break
                if parsed.event_name:
                    break

            for page_num, page in enumerate(pdf.pages, 1):
                if not _is_results_page(page):
                    continue

                for row in _parse_heat_page(
                    page,
                    bib_x_tol=8,
                    col_header_requires_skater=True,
                    skip_line_fn=_skip_sp_line,
                ):
                    division = row["division"]
                    round_str = f"{division} {row['heat_label']}".strip()
                    parsed.results.append(ParsedResult(
                        division=division,
                        distance_m=None,  # not shown per-page in this format
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
        "%s → %d results from RESULTS pages (errors: %d)",
        pdf_path.name, len(parsed.results), len(parsed.parse_errors),
    )
    return parsed
