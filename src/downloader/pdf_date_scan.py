"""
Lightweight scan of the first 1-2 pages of a PDF to find an event date.
Used when the USS link text doesn't include a date (older seasons).
"""
from __future__ import annotations

import re
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import pdfplumber
from dateutil import parser as dateutil_parser

logger = logging.getLogger(__name__)

# Month names for regex
_MONTHS = (
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?"
)

# Patterns ordered from most-specific to least-specific.
# Each captures enough for dateutil to parse; we grab group(0) and feed it in.
_PATTERNS: list[re.Pattern] = [
    # "January 15-16, 2025"  or "January 15 – 16, 2025"  → take "January 15, 2025"
    re.compile(
        rf"({_MONTHS}\s+\d{{1,2}})\s*[-–]\s*\d{{1,2}},?\s*(\d{{4}})",
        re.IGNORECASE,
    ),
    # "January 15, 2025"  or "Jan. 15, 2025"
    re.compile(
        rf"{_MONTHS}\s+\d{{1,2}},?\s*\d{{4}}",
        re.IGNORECASE,
    ),
    # "15-16 January 2025"  or "15 – 16 January 2025"
    re.compile(
        rf"\d{{1,2}}\s*[-–]\s*\d{{1,2}}\s+{_MONTHS}\s+\d{{4}}",
        re.IGNORECASE,
    ),
    # "15 January 2025"
    re.compile(
        rf"\d{{1,2}}\s+{_MONTHS}\s+\d{{4}}",
        re.IGNORECASE,
    ),
    # ISO: "2025-01-15"  or "2025/01/15"
    re.compile(r"\b(20\d{2})[/-](0?[1-9]|1[0-2])[/-](0?[1-9]|[12]\d|3[01])\b"),
]


def _try_parse(text: str) -> Optional[date]:
    """Try dateutil on a raw match string, return date or None."""
    try:
        return dateutil_parser.parse(text, fuzzy=True).date()
    except Exception:
        return None


def _search_text(page_text: str) -> Optional[date]:
    for pat in _PATTERNS:
        for m in pat.finditer(page_text):
            matched = m.group(0)
            # For the first pattern (range like "Jan 15-16, 2025"), reconstruct
            # the start date from captured groups if available.
            if pat.groups and len(m.groups()) == 2 and m.lastindex == 2:
                matched = f"{m.group(1)}, {m.group(2)}"
            d = _try_parse(matched)
            if d and 2015 <= d.year <= 2030:
                return d
    return None


def scan_pdf_for_date(pdf_path: Path) -> Optional[date]:
    """
    Read pages 0 and 1 of pdf_path and return the first plausible event date found.
    Returns None on any error (image PDF, missing file, parse failure).
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[:2]:
                text = page.extract_text() or ""
                if not text.strip():
                    continue
                result = _search_text(text)
                if result is not None:
                    return result
    except Exception as exc:
        logger.debug("pdf_date_scan failed for %s: %s", pdf_path.name, exc)
    return None
