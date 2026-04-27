"""
Scrapes the US Speedskating results page to discover events and PDF URLs.

The USS results page is static HTML with accordion sections per season.
Each event is an <a> tag whose text is "{date} - {event_name}" and
whose href is the full Contentstack CDN PDF URL.
"""

import re
import html
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

logger = logging.getLogger(__name__)

USS_RESULTS_URL = "https://www.usspeedskating.org/results"

# Season accordion IDs as they appear on the page (newest first)
KNOWN_SEASONS = [
    "20252026",
    "20242025",
    "20232024",
    "20222023",
    "20212022",
    "20202021",
    "20192020",
    "20182019",
]

# Map accordion ID → human-readable season string
def _season_id_to_label(season_id: str) -> str:
    """'20252026' → '2025-2026'"""
    if len(season_id) == 8:
        return f"{season_id[:4]}-{season_id[4:]}"
    return season_id


@dataclass
class DiscoveredEvent:
    season: str          # "2025-2026"
    event_name: str
    event_date: Optional[date]
    pdf_url: str
    link_text: str = ""  # raw anchor text from USS results page
    source_url: str = USS_RESULTS_URL


def _parse_event_date(raw_date_str: str) -> Optional[date]:
    """
    Parse messy date strings from the USS site, e.g.:
      "2026-2-22", "2026-01-19", "2026-2-1-", "2025-12-6"
    Returns None on failure.
    """
    cleaned = raw_date_str.strip().rstrip("-").strip()
    try:
        return dateutil_parser.parse(cleaned).date()
    except Exception:
        logger.debug("Could not parse date: %r", raw_date_str)
        return None


def _parse_anchor_text(text: str) -> tuple[Optional[date], str]:
    """
    Parse anchor text like "2026-2-22 - 101 Annual St. Louis Silver Skate"
    Returns (event_date, event_name).
    """
    text = html.unescape(text).strip()

    # Try to split on " - " or fall back to first space-separated token as date
    # Handle rare cases like "2026-01-05 2026 U.S. Olympic Team Trials" (no separator)
    sep_match = re.match(
        r'^(\d{4}-\d{1,2}-\d{1,2})\s*[-–]\s*(.+)$', text, re.DOTALL
    )
    if sep_match:
        raw_date = sep_match.group(1)
        event_name = sep_match.group(2).strip()
    else:
        # No separator — try "YYYY-MM-DD rest of name"
        no_sep_match = re.match(r'^(\d{4}-\d{1,2}-\d{1,2})\s+(.+)$', text, re.DOTALL)
        if no_sep_match:
            raw_date = no_sep_match.group(1)
            event_name = no_sep_match.group(2).strip()
        else:
            return None, text

    event_date = _parse_event_date(raw_date)
    return event_date, event_name


def _is_long_track(event_name: str, pdf_url: str) -> bool:
    """
    Return True if this event is long track only and should be excluded.
    Checks both the event name and the PDF filename for LT indicators.
    """
    name = event_name.lower()
    filename = pdf_url.split("/")[-1].lower()

    # Specific non-short-track events confirmed by manual review
    non_st_names = [
        'heiden challenge',          # long track event
        'colombian championships',   # inline/long track, not USS short track
    ]
    for n in non_st_names:
        if n in name:
            return True

    # Clear LT keywords in event name
    lt_name_patterns = [
        r'\blong\s+track\b',
        r'\b lt \b',        # standalone " LT " abbreviation
        r'\blt\b(?!\s*championships?\s+short)',  # "LT" not followed by "Short"
        r'\ballround\b',
        r'\ball.around\b',
    ]
    for pat in lt_name_patterns:
        if re.search(pat, name):
            return True

    # LT indicators in PDF filename
    lt_file_patterns = [
        r'[_\-]lt[_\-.]',   # _LT_ or _LT. or -LT-
        r'^lt[_\-]',         # starts with LT_
        r'long.?track',
        r'\bgllt\b',         # Great Lakes Long Track abbreviation
        r'lt_agn',
        r'allround',
    ]
    for pat in lt_file_patterns:
        if re.search(pat, filename):
            return True

    return False


def _extract_season_block(soup: BeautifulSoup, season_id: str) -> Optional[BeautifulSoup]:
    """Return the accordion content element for a given season ID."""
    # The content div has aria-labelledby containing the season trigger ID
    content = soup.find(
        attrs={"aria-labelledby": re.compile(rf"trigger:{season_id}$")}
    )
    return content


def discover_events(
    url: str = USS_RESULTS_URL,
    seasons: Optional[list[str]] = None,
) -> list[DiscoveredEvent]:
    """
    Fetch the USS results page and return a list of DiscoveredEvent objects.

    Args:
        url: The USS results page URL.
        seasons: List of season accordion IDs to scrape (e.g. ["20252026"]).
                 Defaults to all known seasons.

    Returns:
        List of DiscoveredEvent, ordered newest first.
    """
    if seasons is None:
        seasons = KNOWN_SEASONS

    logger.info("Fetching USS results page: %s", url)
    resp = requests.get(url, timeout=30, headers={"User-Agent": "SkaterTracker/1.0"})
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    events: list[DiscoveredEvent] = []

    for season_id in seasons:
        season_label = _season_id_to_label(season_id)
        block = _extract_season_block(soup, season_id)

        if block is None:
            logger.warning("Season block not found for ID: %s", season_id)
            continue

        anchors = block.find_all("a", href=True)
        season_events = 0

        for a in anchors:
            href = a["href"]
            if ".pdf" not in href.lower():
                continue

            raw_text = a.get_text(" ", strip=True)
            event_date, event_name = _parse_anchor_text(raw_text)

            if not event_name:
                logger.warning("Could not parse event name from: %r", raw_text)
                continue

            if _is_long_track(event_name, href):
                logger.debug("Skipping LT event: %s", event_name)
                continue

            events.append(DiscoveredEvent(
                season=season_label,
                event_name=event_name,
                event_date=event_date,
                pdf_url=href,
                link_text=raw_text,
                source_url=url,
            ))
            season_events += 1

        logger.info("Season %s: found %d events", season_label, season_events)

    logger.info("Total events discovered: %d", len(events))
    return events


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    found = discover_events()
    for e in found:
        print(f"{e.season} | {e.event_date} | {e.event_name}")
        print(f"  {e.pdf_url}")
