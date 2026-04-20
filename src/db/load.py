"""
Loads a ParsedEvent into the SQLite database.

Flow:
  1. Locate the existing Event record (created by the downloader) via local_path.
  2. Update event metadata (venue, parsed date) from the PDF.
  3. For each unique (division, distance_m) in results → find-or-create a Race row.
  4. For each ParsedResult → find-or-create a Skater, then upsert a Result row.

All operations are idempotent: re-running a load is safe.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..parsers.base import ParsedEvent, ParsedResult
from .models import Event, Race, Skater, Result

logger = logging.getLogger(__name__)


# ── Name normalization ────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Lowercase, strip leading asterisk (female marker), collapse whitespace."""
    name = name.strip().lstrip("*").strip()
    name = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def _split_name(full_name: str) -> tuple[Optional[str], Optional[str]]:
    """Best-effort split into (first, last). Returns (None, None) on failure."""
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    if len(parts) == 1:
        return None, parts[0]
    return None, None


# ── Finders / creators ────────────────────────────────────────────────────────

def _find_event(db: Session, pdf_path: str | Path) -> Optional[Event]:
    path_str = str(pdf_path)
    return (
        db.query(Event)
        .filter(Event.local_path == path_str)
        .first()
    )


def _find_or_create_race(
    db: Session,
    event_id: int,
    division: str,
    distance_m: Optional[int],
    page_number: int,
    round: Optional[str] = None,
) -> Race:
    q = db.query(Race).filter(
        Race.event_id == event_id,
        Race.division == division,
        Race.distance_m == distance_m,
    )
    # ALL_RACES format includes round (e.g. "A Final", "Heat 1 of 3"), so each
    # heat becomes its own Race row.
    if round is not None:
        q = q.filter(Race.round == round)
    else:
        q = q.filter(Race.round.is_(None))
    race = q.first()
    if race is None:
        race = Race(
            event_id=event_id,
            division=division,
            distance_m=distance_m,
            page_number=page_number,
            round=round,
        )
        db.add(race)
        db.flush()
    return race


def _find_or_create_skater(db: Session, full_name: str, affiliation: str) -> Skater:
    normalized = _normalize_name(full_name)
    skater = (
        db.query(Skater)
        .filter(Skater.normalized_name == normalized)
        .first()
    )
    if skater is None:
        first, last = _split_name(full_name.strip().lstrip("*").strip())
        skater = Skater(
            full_name=full_name.strip().lstrip("*").strip(),
            first_name=first,
            last_name=last,
            normalized_name=normalized,
            club_name=affiliation or None,
        )
        db.add(skater)
        db.flush()
    elif affiliation and not skater.club_name:
        skater.club_name = affiliation
    return skater


def _upsert_result(
    db: Session,
    race_id: int,
    skater_id: int,
    pr: ParsedResult,
) -> Result:
    result = (
        db.query(Result)
        .filter(Result.race_id == race_id, Result.skater_id == skater_id)
        .first()
    )
    if result is None:
        result = Result(race_id=race_id, skater_id=skater_id)
        db.add(result)

    result.bib = pr.bib
    result.rank = pr.rank
    result.seed_heat = pr.seed_heat
    result.heat_assignment = pr.heat_assignment
    result.time_text = pr.time_text
    result.time_seconds = pr.time_seconds
    result.points = pr.points
    result.status = pr.status
    return result


# ── Main entry point ──────────────────────────────────────────────────────────

def load_parsed_event(
    db: Session,
    pdf_path: str | Path,
    parsed: ParsedEvent,
) -> tuple[int, int, int]:
    """
    Load a ParsedEvent into the DB.

    Returns:
        (races_created, skaters_created, results_upserted)
    """
    pdf_path = Path(pdf_path).resolve()
    event = _find_event(db, pdf_path)

    if event is None:
        logger.warning("No Event record found for %s — skipping", pdf_path.name)
        return 0, 0, 0

    # Update event metadata from parsed PDF
    if parsed.event_name and not event.venue:
        # Only store venue/date-extra if not already set
        pass
    if parsed.venue:
        event.venue = parsed.venue
    event.is_parseable = len(parsed.results) > 0 or not parsed.parse_errors

    races_created = 0
    skaters_created = 0
    results_upserted = 0

    for pr in parsed.results:
        # Race
        q = db.query(Race).filter(
            Race.event_id == event.id,
            Race.division == pr.division,
            Race.distance_m == pr.distance_m,
        )
        if pr.round is not None:
            q = q.filter(Race.round == pr.round)
        else:
            q = q.filter(Race.round.is_(None))
        existing_races = q.count()
        race = _find_or_create_race(db, event.id, pr.division, pr.distance_m, pr.page_number, pr.round)
        if existing_races == 0:
            races_created += 1

        # Skater
        existing_skaters = db.query(Skater).filter(
            Skater.normalized_name == _normalize_name(pr.name)
        ).count()
        skater = _find_or_create_skater(db, pr.name, pr.affiliation)
        if existing_skaters == 0:
            skaters_created += 1

        # Result
        _upsert_result(db, race.id, skater.id, pr)
        results_upserted += 1

    db.commit()
    logger.info(
        "%s → races=%d skaters=%d results=%d (errors=%d)",
        pdf_path.name,
        races_created,
        skaters_created,
        results_upserted,
        len(parsed.parse_errors),
    )
    return races_created, skaters_created, results_upserted
