"""Fill NULL division/distance_m for results rows produced by "Group A Picking" headers.

"Group A Picking N+(M) Event #0" headers provide no division or distance.  The parser
stores those heat rows with division=NULL, distance_m=NULL.  This script fills them in
via two signals:

  1. Division  — bib overlap: the race bibs are compared against each division's bibs from
     the distance classification.  The division whose bibs overlap ≥ 2 (and has the most
     overlap with no tie) is assigned.

  2. Distance  — time ratio: the fastest time in the race is compared against the
     classification best time for each distance the division races.  The distance whose
     ratio (fastest_race / class_best) is closest to 1.0 (and ≥ 1.0) is assigned.

Usage:
    python scripts/fill_missing_race_context.py --event-id N [--verbose]

Can also be imported and called from parse_all_races_format.py main():
    from scripts.fill_missing_race_context import fill_missing_race_context
    fill_missing_race_context(event_id, db)
"""
from __future__ import annotations
import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from src.db.session_v2 import init_db, get_session
from src.db.models_v2 import Classification, Result, TimeClassification


def fill_missing_race_context(event_id: int, db, verbose: bool = False) -> dict:
    """Fill NULL division/distance_m rows for the given event.

    Returns a summary dict with keys:
      filled       — races where both division and distance were resolved
      div_only     — races where division was resolved but distance was uncertain
      unknown      — races where division could not be resolved
      review_items — list of (race_number, description) for user review
    """

    # ── 1. Build classification look-ups ──────────────────────────────────────

    # All distance-classification rows for this event
    class_rows = (
        db.query(Classification)
        .filter(
            Classification.event_id == event_id,
            Classification.section_type == 'distance',
            Classification.distance_m.isnot(None),
        )
        .order_by(Classification.id)
        .all()
    )

    if not class_rows:
        # Fallback: use time_classification rows if available (events with no
        # distance classification section but with a TC section parsed).
        tc_rows = (
            db.query(TimeClassification)
            .filter(
                TimeClassification.event_id == event_id,
                TimeClassification.distance_m.isnot(None),
            )
            .order_by(TimeClassification.id)
            .all()
        )
        if tc_rows:
            if verbose:
                print("  fill: no distance classification — using time_classification as fallback")
            class_rows = tc_rows  # duck-typing: same fields (division, distance_m, bib, best_time_seconds)
        else:
            if verbose:
                print("  fill: no distance classification rows — nothing to fill")
            return {'filled': 0, 'div_only': 0, 'unknown': 0, 'review_items': []}

    # div_bibs: division → set of bibs in ANY distance
    div_bibs: dict[str, set[str]] = defaultdict(set)
    # div_distances: division → ordered list of distance_m values (order by class id)
    div_distances: dict[str, list[int]] = defaultdict(list)
    # div_best: (division, distance_m) → minimum best_time_seconds
    div_best: dict[tuple[str, int], float] = {}

    seen_div_dist: set[tuple[str, int]] = set()

    for row in class_rows:
        div = row.division
        dist = row.distance_m
        if div is None:
            continue
        if row.bib:
            div_bibs[div].add(row.bib)
        key = (div, dist)
        if key not in seen_div_dist:
            seen_div_dist.add(key)
            div_distances[div].append(dist)
        if row.best_time_seconds and row.best_time_seconds > 0:
            if key not in div_best or row.best_time_seconds < div_best[key]:
                div_best[key] = row.best_time_seconds

    # ── 2. Find races with NULL division ──────────────────────────────────────

    null_results = (
        db.query(Result)
        .filter(
            Result.event_id == event_id,
            Result.division.is_(None),
            Result.race_number.isnot(None),
        )
        .all()
    )

    if not null_results:
        if verbose:
            print("  fill: no NULL-division rows to fill")
        return {'filled': 0, 'div_only': 0, 'unknown': 0, 'review_items': []}

    # Group by race_number
    races: dict[int, list[Result]] = defaultdict(list)
    for r in null_results:
        races[r.race_number].append(r)

    # ── 3. Identify division + distance for each race ─────────────────────────

    filled = 0
    div_only = 0
    unknown = 0
    review_items: list[tuple[int, str]] = []

    for race_num, rows in sorted(races.items()):
        bibs = {r.bib for r in rows if r.bib}
        times = [r.time_seconds for r in rows if r.time_seconds and r.time_seconds > 0]
        fastest = min(times) if times else None

        # Division identification: count bib overlaps per division
        overlap: dict[str, int] = {}
        for div, dbset in div_bibs.items():
            cnt = len(bibs & dbset)
            if cnt > 0:
                overlap[div] = cnt

        if not overlap:
            unknown += 1
            review_items.append((race_num, f"race {race_num}: bib count < 2 for all divisions (bibs={sorted(bibs)})"))
            if verbose:
                print(f"  fill: race {race_num} — no division match (bibs={sorted(bibs)})")
            continue

        max_cnt = max(overlap.values())
        if max_cnt < 2:
            unknown += 1
            review_items.append((race_num, f"race {race_num}: best overlap is {max_cnt} (<2), bibs={sorted(bibs)}"))
            if verbose:
                print(f"  fill: race {race_num} — max overlap {max_cnt} < 2 (bibs={sorted(bibs)})")
            continue

        best_divs = [d for d, c in overlap.items() if c == max_cnt]
        if len(best_divs) > 1:
            unknown += 1
            review_items.append((race_num, f"race {race_num}: tie between divisions {best_divs} at overlap={max_cnt}"))
            if verbose:
                print(f"  fill: race {race_num} — tie {best_divs}")
            continue

        division = best_divs[0]

        # Distance identification: fastest-time ratio
        distances = div_distances.get(division, [])
        if not distances:
            div_only += 1
            review_items.append((race_num, f"race {race_num}: division={division!r} but no distances in classification"))
            _apply_division_only(db, event_id, race_num, division)
            if verbose:
                print(f"  fill: race {race_num} → division={division!r} but no distances")
            continue

        # Single-distance division: assign directly without needing time comparison
        if len(distances) == 1:
            distance_m = distances[0]
            _apply_update(db, event_id, race_num, division, distance_m, verbose)
            filled += 1
            continue

        if fastest is None:
            # All DNS/DNF — pick first distance as best guess
            distance_m = distances[0]
            _apply_update(db, event_id, race_num, division, distance_m, verbose)
            filled += 1
            continue

        # Compute ratios; keep only distances where ratio >= 1.0.
        # Use a small epsilon to handle floating-point cases where the race time
        # equals the classification best (ratio should be exactly 1.0 but may be
        # 0.9999999... due to float arithmetic).
        _EPS = 1e-6
        candidates: list[tuple[float, int]] = []
        for dist in distances:
            best = div_best.get((division, dist))
            if best is None or best <= 0:
                continue
            ratio = fastest / best
            if ratio >= 1.0 - _EPS:
                candidates.append((ratio, dist))

        if not candidates:
            # All ratios < 1.0 (race faster than classification best — impossible)
            # Fall back to smallest-ratio distance (least violation)
            all_ratios = []
            for dist in distances:
                best = div_best.get((division, dist))
                if best and best > 0:
                    all_ratios.append((fastest / best, dist))
            if all_ratios:
                all_ratios.sort(key=lambda x: x[0], reverse=True)
                distance_m = all_ratios[0][1]
                review_items.append((
                    race_num,
                    f"race {race_num}: fastest={fastest:.3f} faster than all class bests for {division!r} — "
                    f"using best-ratio distance={distance_m}m (ratio={all_ratios[0][0]:.3f})"
                ))
                _apply_update(db, event_id, race_num, division, distance_m, verbose)
                filled += 1
            else:
                div_only += 1
                review_items.append((race_num, f"race {race_num}: division={division!r} but no class best times"))
                _apply_division_only(db, event_id, race_num, division)
            continue

        # Pick candidate with ratio closest to 1.0
        candidates.sort(key=lambda x: x[0])
        distance_m = candidates[0][1]
        _apply_update(db, event_id, race_num, division, distance_m, verbose)
        filled += 1

    db.flush()

    summary = {'filled': filled, 'div_only': div_only, 'unknown': unknown,
               'review_items': review_items}
    return summary


def _apply_update(db, event_id: int, race_num: int, division: str, distance_m: int,
                  verbose: bool) -> None:
    rows = (
        db.query(Result)
        .filter(
            Result.event_id == event_id,
            Result.race_number == race_num,
            Result.division.is_(None),
        )
        .all()
    )
    for r in rows:
        r.division = division
        r.distance_m = distance_m
        r.round_type = 'HEATS'
    if verbose:
        print(f"  fill: race {race_num} → division={division!r} distance={distance_m}m ({len(rows)} rows)")


def _apply_division_only(db, event_id: int, race_num: int, division: str) -> None:
    rows = (
        db.query(Result)
        .filter(
            Result.event_id == event_id,
            Result.race_number == race_num,
            Result.division.is_(None),
        )
        .all()
    )
    for r in rows:
        r.division = division
        r.round_type = 'HEATS'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--event-id', type=int, required=True)
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    init_db()
    db = get_session()

    summary = fill_missing_race_context(args.event_id, db, verbose=args.verbose)
    db.commit()
    db.close()

    print(f"Fill complete for event {args.event_id}:")
    print(f"  Races fully resolved (div + dist) : {summary['filled']}")
    print(f"  Races with division only           : {summary['div_only']}")
    print(f"  Races with unknown division        : {summary['unknown']}")
    if summary['review_items']:
        print("  Items for review:")
        for race_num, msg in summary['review_items']:
            print(f"    {msg}")


if __name__ == '__main__':
    main()
