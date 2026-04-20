"""
Parse all downloaded PDFs and load results into the database.

Usage:
    python scripts/parse_and_load.py [--season 2025-2026] [--reparse] [--verbose]

Options:
    --season    Only process PDFs from this season (e.g. "2025-2026")
    --reparse   Re-parse and reload even if the event already has results
    --verbose   Debug logging
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Event, Result
from src.db.load import load_parsed_event
from src.parsers import parse_pdf
from src.parsers.base import PdfFormat

RAW_PDFS = Path("data/raw_pdfs")
PARSED_PDFS = Path("data/parsed_pdfs")


def _move_to_parsed(pdf_path: Path) -> Path:
    """Move a PDF from raw_pdfs/season/file.pdf to parsed_pdfs/season/file.pdf."""
    try:
        rel = pdf_path.relative_to(RAW_PDFS.resolve())
    except ValueError:
        return pdf_path  # already in parsed_pdfs or elsewhere
    dest = PARSED_PDFS.resolve() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pdf_path), str(dest))
    return dest


def main():
    parser = argparse.ArgumentParser(description="Parse PDFs and load results into DB")
    parser.add_argument("--season", default=None, help="Limit to a specific season")
    parser.add_argument("--reparse", action="store_true", help="Re-parse already-loaded events")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    logger = logging.getLogger(__name__)

    init_db()
    db = get_session()

    query = db.query(Event).filter(Event.local_path.isnot(None))
    if args.season:
        query = query.filter(Event.season == args.season)
    events = query.order_by(Event.season, Event.event_date).all()

    logger.info("Events to process: %d", len(events))

    stats = {"parsed": 0, "skipped": 0, "no_results": 0, "error": 0,
             "total_races": 0, "total_skaters": 0, "total_results": 0}

    for i, event in enumerate(events, 1):
        pdf_path = Path(event.local_path)
        if not pdf_path.exists():
            logger.warning("[%d/%d] Missing file: %s", i, len(events), pdf_path)
            stats["error"] += 1
            continue

        if not args.reparse:
            existing = db.query(Result).join(Result.race).filter_by(event_id=event.id).count()
            if existing > 0:
                logger.debug("[%d/%d] Already loaded (%d results), skipping: %s",
                             i, len(events), existing, pdf_path.name)
                stats["skipped"] += 1
                continue

        logger.info("[%d/%d] Parsing: %s", i, len(events), pdf_path.name)
        try:
            parsed = parse_pdf(pdf_path)

            if parsed.format in (PdfFormat.IMAGE, PdfFormat.UNKNOWN):
                label = "image PDF" if parsed.format == PdfFormat.IMAGE else "unknown format"
                logger.info("  → %s, skipping", label)
                event.is_parseable = False
                event.pdf_format = parsed.format.value
                db.commit()
                stats["skipped"] += 1
                continue

            if not parsed.results:
                logger.warning("  → 0 results parsed (errors: %s)", parsed.parse_errors[:2])
                event.is_parseable = False
                event.pdf_format = parsed.format.value
                db.commit()
                stats["no_results"] += 1
                continue

            races, skaters, results = load_parsed_event(db, pdf_path, parsed)

            # Record the parser used
            event.pdf_format = parsed.format.value

            # Move file from raw_pdfs → parsed_pdfs and update local_path
            if str(pdf_path.resolve()).startswith(str(RAW_PDFS.resolve())):
                new_path = _move_to_parsed(pdf_path.resolve())
                event.local_path = str(new_path)
                logger.info("  → moved to %s", new_path)

            db.commit()
            stats["parsed"] += 1
            stats["total_races"] += races
            stats["total_skaters"] += skaters
            stats["total_results"] += results

        except Exception as e:
            logger.error("[%d/%d] Failed: %s — %s", i, len(events), pdf_path.name, e)
            import traceback; traceback.print_exc()
            stats["error"] += 1

    db.close()

    print("\n=== Parse & Load Summary ===")
    print(f"  Parsed:          {stats['parsed']}")
    print(f"  Skipped:         {stats['skipped']}")
    print(f"  Zero results:    {stats['no_results']}")
    print(f"  Errors:          {stats['error']}")
    print(f"  Races created:   {stats['total_races']}")
    print(f"  Skaters created: {stats['total_skaters']}")
    print(f"  Results loaded:  {stats['total_results']}")


if __name__ == "__main__":
    main()
