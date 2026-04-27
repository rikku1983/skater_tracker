"""
Run the LLM parser on all events that currently have 0 results.

Requires: ANTHROPIC_API_KEY environment variable (get from console.anthropic.com)

Usage:
    ANTHROPIC_API_KEY=sk-ant-... python scripts/parse_with_llm.py [--dry-run] [--event-id N]

Options:
    --dry-run       Parse and print results without writing to DB
    --event-id N    Process only this event ID (for testing)
    --limit N       Process at most N events (default: all)
    --skip-errors   Skip events that error instead of stopping
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import anthropic

from src.db.session import get_session, init_db
from src.db.models import Event, Race, Result
from src.parsers.llm_parser import parse_pdf_with_llm
from src.db.load import load_parsed_event

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# track_type values that should never be parsed as short track
EXCLUDE_TRACK_TYPES = {"long", "inline"}
EXCLUDE_NAME_FRAGMENTS = ["colombian championships", "heiden challenge"]


def get_target_events(db, event_id: int | None, limit: int | None):
    """Return events with 0 results and a valid local_path."""
    from sqlalchemy import func

    q = (
        db.query(Event)
        .outerjoin(Race, Race.event_id == Event.id)
        .group_by(Event.id)
        .having(func.count(Race.id) == 0)
        .filter(Event.local_path.isnot(None))
        .filter(Event.track_type.notin_(EXCLUDE_TRACK_TYPES) | Event.track_type.is_(None))
        .order_by(Event.season, Event.event_name)
    )

    if event_id is not None:
        q = db.query(Event).filter(Event.id == event_id)

    events = q.all()

    # Filter out non-short-track events by name
    events = [
        e for e in events
        if not any(frag in (e.event_name or "").lower() for frag in EXCLUDE_NAME_FRAGMENTS)
    ]

    if limit:
        events = events[:limit]
    return events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--event-id", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--skip-errors", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("Get your key from console.anthropic.com → API Keys")
        print("Then run:  ANTHROPIC_API_KEY=sk-ant-... python scripts/parse_with_llm.py")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    init_db()
    db = get_session()

    events = get_target_events(db, args.event_id, args.limit)
    logger.info("Events to process: %d", len(events))

    stats = {"parsed": 0, "loaded": 0, "skipped": 0, "errors": 0}

    for i, ev in enumerate(events, 1):
        path = Path(ev.local_path)
        if not path.exists():
            logger.warning("[%d/%d] File missing: %s", i, len(events), path.name)
            stats["skipped"] += 1
            continue

        logger.info("[%d/%d] %s | %s", i, len(events), ev.season, ev.event_name)

        try:
            parsed = parse_pdf_with_llm(path, client)
            stats["parsed"] += 1

            if parsed.parse_errors:
                for err in parsed.parse_errors:
                    logger.warning("  parse error: %s", err)

            if not parsed.results:
                logger.warning("  → 0 results extracted")
                stats["skipped"] += 1
                continue

            logger.info("  → %d results extracted", len(parsed.results))

            if args.dry_run:
                # Print a sample without writing
                for r in parsed.results[:5]:
                    print(f"    {r.division:<25} {r.distance_m}m  {r.name:<22} {r.time_text}")
                continue

            # Clear any existing races (shouldn't be any, but be safe)
            for race in db.query(Race).filter(Race.event_id == ev.id).all():
                db.query(Result).filter(Result.race_id == race.id).delete()
            db.query(Race).filter(Race.event_id == ev.id).delete()
            db.commit()

            races_c, skaters_c, results_c = load_parsed_event(db, path, parsed)
            db.commit()
            ev.pdf_format = "llm"
            db.commit()

            logger.info(
                "  → loaded: races=%d skaters=%d results=%d",
                races_c, skaters_c, results_c,
            )
            stats["loaded"] += 1

        except Exception as exc:
            logger.error("  ERROR: %s", exc)
            stats["errors"] += 1
            if not args.skip_errors:
                raise

    db.close()
    logger.info(
        "Done. parsed=%d loaded=%d skipped=%d errors=%d",
        stats["parsed"], stats["loaded"], stats["skipped"], stats["errors"],
    )


if __name__ == "__main__":
    main()
