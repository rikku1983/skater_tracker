"""
Downloads PDFs discovered by discover.py, stores them locally,
and records each download in the events manifest table.
"""

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from sqlalchemy.orm import Session

from .discover import DiscoveredEvent
from ..db.models import Event

logger = logging.getLogger(__name__)

RAW_PDFS_DIR = Path(__file__).parents[2] / "data" / "raw_pdfs"
DOWNLOAD_DELAY_S = 0.5  # polite delay between requests


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_filename(event_name: str) -> str:
    """Convert event name to a filesystem-safe filename component."""
    name = event_name.strip()
    name = re.sub(r'[^\w\s-]', '', name)
    name = re.sub(r'\s+', '_', name)
    return name[:80]


def _local_path(event: DiscoveredEvent, base_dir: Path = RAW_PDFS_DIR) -> Path:
    """
    Determine the local file path for a PDF.
    Uses the filename from the PDF URL (most descriptive), stored under season/.
    """
    season_dir = base_dir / event.season
    season_dir.mkdir(parents=True, exist_ok=True)

    # Try to use the original filename from the URL
    url_filename = event.pdf_url.split("/")[-1]
    if url_filename.lower().endswith(".pdf"):
        return season_dir / url_filename

    # Fallback: construct from event name + date
    date_str = event.event_date.strftime("%Y-%m-%d") if event.event_date else "unknown"
    safe_name = _safe_filename(event.event_name)
    return season_dir / f"{date_str}_{safe_name}.pdf"


def _is_already_downloaded(db: Session, pdf_url: str) -> bool:
    existing = db.query(Event).filter(Event.pdf_url == pdf_url).first()
    return existing is not None and existing.downloaded_at is not None


def download_pdf(
    event: DiscoveredEvent,
    local_path: Path,
    session: requests.Session,
) -> bool:
    """
    Download a single PDF to local_path.
    Returns True on success, False on failure.
    """
    try:
        logger.info("Downloading: %s", event.event_name)
        resp = session.get(event.pdf_url, timeout=60, stream=True)
        resp.raise_for_status()

        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)

        logger.debug("Saved to: %s", local_path)
        return True

    except Exception as exc:
        logger.error("Failed to download %s: %s", event.pdf_url, exc)
        if local_path.exists():
            local_path.unlink()
        return False


def upsert_event_record(
    db: Session,
    event: DiscoveredEvent,
    local_path: Path,
    downloaded: bool,
) -> Event:
    """Create or update the Event manifest record in the DB."""
    existing = db.query(Event).filter(Event.pdf_url == event.pdf_url).first()

    if existing is None:
        record = Event(
            season=event.season,
            event_name=event.event_name,
            event_date=event.event_date,
            source_url=event.source_url,
            pdf_url=event.pdf_url,
        )
        db.add(record)
    else:
        record = existing

    if downloaded and local_path.exists():
        record.local_path = str(local_path)
        record.downloaded_at = datetime.now(timezone.utc)
        record.checksum = _sha256(local_path)

    db.commit()
    db.refresh(record)
    return record


def download_all(
    events: list[DiscoveredEvent],
    db: Session,
    base_dir: Path = RAW_PDFS_DIR,
    skip_existing: bool = True,
) -> dict:
    """
    Download all discovered events.

    Args:
        events: List of DiscoveredEvent from discover.discover_events().
        db: SQLAlchemy session.
        base_dir: Root directory for PDF storage.
        skip_existing: If True, skip PDFs already in the manifest DB.

    Returns:
        Summary dict with counts: downloaded, skipped, failed.
    """
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}
    http_session = requests.Session()
    http_session.headers["User-Agent"] = "SkaterTracker/1.0"

    for i, event in enumerate(events, 1):
        logger.info("[%d/%d] %s | %s", i, len(events), event.season, event.event_name)

        if skip_existing and _is_already_downloaded(db, event.pdf_url):
            logger.info("  → already downloaded, skipping")
            stats["skipped"] += 1
            continue

        local_path = _local_path(event, base_dir)
        success = download_pdf(event, local_path, http_session)
        upsert_event_record(db, event, local_path, downloaded=success)

        if success:
            stats["downloaded"] += 1
        else:
            stats["failed"] += 1

        if i < len(events):
            time.sleep(DOWNLOAD_DELAY_S)

    logger.info(
        "Done. Downloaded: %d | Skipped: %d | Failed: %d",
        stats["downloaded"], stats["skipped"], stats["failed"],
    )
    return stats
