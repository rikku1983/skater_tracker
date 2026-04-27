"""
Classify each event as short / long / mixed / inline / unknown.

Classification passes (in order of precedence):

  1. Manual overrides (known edge cases)
  2. Distance profile — unambiguous markers:
       - Has any of {111, 222, 333, 444, 666, 777}m  → short
         (also has 5000 or 10000m)                   → mixed
       - Has 5000 or 10000m, no short-track markers  → long
       - Has 85, 170, 255, or 425m, no 111m-multiples→ short (small-rink)
  3. Name keywords:
       - "short track"    → short
       - "long track"     → long
       - "WSA" or "inline"→ inline
       - "marathon"       → excluded (set long, skip in app)
  4. Anything still unclassified → unknown (printed for review)

Usage:
    python scripts/classify_track_type.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.db.session import get_session, init_db
from src.db.models import Event, Race

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# --- Configurable sets ---

# Distances that only appear in short track (111m-rink multiples that don't
# overlap with long track standard distances).
ST_ONLY_DISTANCES = {111, 222, 333, 444, 666, 777}

# Distances that only appear in long track.
LT_ONLY_DISTANCES = {5000, 10000}

# Small-rink short track (85m or 76m ice surfaces).
SMALL_RINK_DISTANCES = {85, 170, 255, 340, 425}

# Manual overrides: event_id → track_type
# Add entries here for events that the automatic rules mis-classify.
MANUAL_OVERRIDES: dict[int, str] = {
    21: "long",   # 2026 Beehive Burn — Utah Olympic Oval (long track venue), 49% flagged
    37: "inline", # 2025 Adirondack Single Distance Championships — non-111m distances
    53: "inline", # 2024 Adirondack Sprint Championships — 100/200m distances
    197: "inline",# I-94 Sprints — 100/200/300/400m distances
    174: "long",  # 2019 US Marathon & World Open Marathon
    187: "short", # 2019 International Children's Winter Games — ICWG uses short track
    210: "mixed", # 2026 US AmCup Final & US Open Masters & Colombian Championships
}

# Name fragments that conclusively indicate short track (checked case-insensitively).
# Ordered from most specific to least.
ST_NAME_FRAGMENTS = [
    "short track",
    "heartland",      # Heartland Short Track series
    "nest",           # North East Short Track series
    "bay state",      # Bay State Short Track Championships
    "land of lincoln",
    "park ridge",
    "ohio invitational",
    "ohio state",
    "presidential cup",
    "gold cup",       # USSS short track Gold Cup (distinct from WSA Gold Cup, caught earlier)
    "barrel buster",  # Franklin Park Barrel Buster — local Chicago short track
    " st ",           # "US ST Championships" etc.
    " st\t",
    "junior championships short track",
]


def classify(event: Event, distances: set[int]) -> str:
    """Return the track_type for an event given its set of race distances."""
    name_lower = (event.event_name or "").lower()

    # Pass 1: manual overrides
    if event.id in MANUAL_OVERRIDES:
        return MANUAL_OVERRIDES[event.id]

    # Pass 2a: unambiguous name indicators — checked before distances
    # so "US Junior Championships Short Track" isn't mis-tagged by a relay 5000m.
    if "long track" in name_lower:
        return "long"
    if "wsa" in name_lower or "inline" in name_lower:
        return "inline"
    if "marathon" in name_lower:
        return "long"
    name_says_short = any(f in name_lower for f in ST_NAME_FRAGMENTS)

    # Pass 2b: distance profile
    has_st = bool(distances & ST_ONLY_DISTANCES)
    has_small = bool(distances & SMALL_RINK_DISTANCES)
    # 10000m is unambiguously long track (no short track 10000m event exists).
    has_lt_definitive = 10000 in distances
    # 5000m alone can be the short track relay — only flag as LT if name doesn't say ST.
    has_lt = has_lt_definitive or (5000 in distances and not name_says_short)

    if (has_st or has_small) and has_lt:
        return "mixed"
    if has_st or has_small:
        return "short"
    if has_lt:
        return "long"

    # Pass 3: remaining name-based short track indicators
    if name_says_short:
        return "short"

    return "unknown"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print assignments without writing to DB")
    args = parser.parse_args()

    init_db()
    db = get_session()

    events = db.query(Event).order_by(Event.id).all()

    counts: dict[str, int] = {}
    unknown_events: list[Event] = []

    for ev in events:
        dist_rows = db.query(Race.distance_m).filter(Race.event_id == ev.id).distinct().all()
        distances = {d[0] for d in dist_rows if d[0]}

        track_type = classify(ev, distances)
        counts[track_type] = counts.get(track_type, 0) + 1

        if track_type == "unknown":
            unknown_events.append(ev)

        logger.debug("  %4d  %-10s  %-50s  %s", ev.id, track_type, ev.event_name[:50], sorted(distances)[:8])

        if not args.dry_run:
            ev.track_type = track_type

    if not args.dry_run:
        db.commit()
        logger.info("Committed track_type for %d events.", len(events))

    logger.info("")
    logger.info("=== Classification summary ===")
    for tt, n in sorted(counts.items()):
        logger.info("  %-10s  %d events", tt, n)

    if unknown_events:
        logger.info("")
        logger.info("=== Events still UNKNOWN — review manually ===")
        for ev in unknown_events:
            dist_rows = db.query(Race.distance_m).filter(Race.event_id == ev.id).distinct().all()
            distances = sorted(d[0] for d in dist_rows if d[0])
            logger.info("  %4d  %s  dists=%s", ev.id, ev.event_name[:60], distances[:10])

    db.close()


if __name__ == "__main__":
    main()
