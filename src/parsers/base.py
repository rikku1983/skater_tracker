"""
Base parser and PDF format detection for USS short track result PDFs.

Three known formats:
  - Tempus:  Pages have "DISTANCE CLASSIFICATION" header + structured columns.
             Covers 2023-2024 onwards. Most events since ~2024.
  - Legacy:  Pages have "Competition results" / "Overall results" sections.
             Uses heats listed individually, per-event structure. 2018-2022.
  - Image:   No extractable text (scanned). Flagged as not parseable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional

import pdfplumber

logger = logging.getLogger(__name__)


class PdfFormat(str, Enum):
    ALL_RACES = "all_races"                 # Tempus "All Races RESULTS" per-heat pages (2023+)
    TEMPUS = "tempus"                       # Tempus "DISTANCE CLASSIFICATION" pages
    TEMPUS_RESULTS = "tempus_results"       # Tempus Protocol with "Results" per-heat pages
    SPEEDSKATING_PRO = "speedskating_pro"   # Speedskating Pro web-export, one heat per page
    TEMPUS_RACES = "tempus_races"           # Tempus per-heat export: "Event #N" + "# Name Affiliation" header
    CLASSIC_RESULTS = "classic_results"     # Pre-Tempus: "Place No. Name Assn. Time" + "Event N Division Distance Meters Round"
    LEGACY = "legacy"
    IMAGE = "image"
    UNKNOWN = "unknown"


@dataclass
class ParsedResult:
    """One skater's result for one distance within one event."""
    # Race identification
    division: str           # e.g. "Anacostia", "NEST MEN A", "Junior D Women"
    distance_m: Optional[int]   # e.g. 500, 777, 1000, 1500
    distance_raw: str       # raw string from PDF, e.g. "Anacostia's 500"

    # Skater
    rank: Optional[int]
    bib: Optional[str]
    name: str               # full name as in PDF (may include * for female)
    affiliation: str        # club name

    # Result
    seed_heat: Optional[str]    # e.g. "A2" (seeding round heat)
    heat_assignment: Optional[str]  # e.g. "A1", "B3" (final heat placement)
    time_text: Optional[str]    # raw time string, e.g. "1:23.456"
    time_seconds: Optional[float]
    points: Optional[float]
    status: Optional[str]       # "DNS", "DNF", "DQ", "PEN", etc.

    # Round/heat (for ALL_RACES format)
    round: Optional[str] = None   # e.g. "A Final", "Heat 1 of 3", "B Final"

    # Gender (from per-row code or * marker; None if unknown)
    gender: Optional[str] = None  # 'M' / 'F'

    # Metadata
    page_number: int = 0


@dataclass
class ParsedEvent:
    """All parsed data from one PDF file."""
    pdf_path: str
    format: PdfFormat
    event_name: Optional[str] = None
    event_date_raw: Optional[str] = None   # date string from PDF cover
    venue: Optional[str] = None
    results: list[ParsedResult] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)


def detect_format(pdf_path: str | Path) -> PdfFormat:
    """
    Detect the PDF format without fully parsing it.
    Fast: only reads the first few pages and looks for key markers.
    """
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            # IMAGE detection: sample up to 10 pages spread across the PDF.
            # A PDF is image-only if the vast majority of sampled pages have no text
            # (cover/officials pages may have text even in otherwise-scanned PDFs).
            n = len(pdf.pages)
            sample_indices = list(range(min(3, n))) + list(range(3, n, max(1, n // 7)))
            sample_pages = [pdf.pages[i] for i in sorted(set(sample_indices))]
            empty_count = sum(1 for p in sample_pages if not (p.extract_text() or "").strip())
            if empty_count >= len(sample_pages) * 0.8:
                return PdfFormat.IMAGE

            # ALL_RACES pages appear late in the PDF (after Distance Classification),
            # so do a full pre-scan before falling back to sequential detection.
            all_text_pages = [page.extract_text() or "" for page in pdf.pages]

            for text in all_text_pages:
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                for i in range(len(lines) - 1):
                    if lines[i] == "All Races" and lines[i + 1] == "RESULTS":
                        return PdfFormat.ALL_RACES

            # TEMPUS_RESULTS: has a "Results" header page with RACE: heat entries
            for text in all_text_pages:
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if "Results" in lines and any("RACE:" in l for l in lines):
                    return PdfFormat.TEMPUS_RESULTS

            # SPEEDSKATING_PRO: web-exported, "RESULTS" all-caps + "Skater # Name Affiliation" header
            for text in all_text_pages:
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if "RESULTS" in lines and any("Skater" in l and "Affiliation" in l for l in lines):
                    return PdfFormat.SPEEDSKATING_PRO

            # TEMPUS_RACES: Tempus per-heat export with "# Name Affiliation Time" column header.
            # Variant A: "Event #N" on the first line of a page.
            # Variant B: "RESULTS" standalone line + "Event #N" somewhere on the page.
            for text in all_text_pages:
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                has_heat_cols = any(l.startswith("# Name Affiliation Time") for l in lines)
                has_event_n   = any(re.search(r'Event\s+#\d+', l) for l in lines)
                if has_heat_cols and has_event_n and (
                    (lines and re.search(r'Event\s+#\d+', lines[0]))  # variant A
                    or "RESULTS" in lines                               # variant B
                ):
                    return PdfFormat.TEMPUS_RACES

            for text in all_text_pages:
                if not text.strip():
                    continue
                if "DISTANCE CLASSIFICATION" in text or "Distance Classification" in text:
                    return PdfFormat.TEMPUS
                if "Competition results" in text or "Overall results" in text:
                    return PdfFormat.LEGACY

            # CLASSIC_RESULTS: "Place No. Name Assn. Time" header + "Event N ... Meters" events
            for text in all_text_pages:
                if "Place No. Name Assn. Time" in text and re.search(r'Event\s+\d+\s+\S+.*\d{3,4}\s+Meters', text):
                    return PdfFormat.CLASSIC_RESULTS

            return PdfFormat.UNKNOWN

    except Exception as e:
        logger.warning("Could not detect format for %s: %s", pdf_path, e)
        return PdfFormat.UNKNOWN


def _extract_distance_m(distance_raw: str) -> Optional[int]:
    """
    Extract numeric distance in meters from a raw distance string.

    Examples:
      "Anacostia's 500"        → 500
      "Junior D Women's 1000"  → 1000
      "NEST MEN A 777"         → 777
      "1500 Super Final"       → 1500
      "333"                    → 333
    """
    import re
    m = re.search(r'\b(\d{3,4})\b', distance_raw)
    if m:
        val = int(m.group(1))
        # Sanity check: reasonable ST distances
        if val in (222, 333, 500, 600, 777, 1000, 1111, 1500, 3000):
            return val
        # Also accept any value between 100 and 3000 as a valid distance
        if 100 <= val <= 3000:
            return val
    return None
