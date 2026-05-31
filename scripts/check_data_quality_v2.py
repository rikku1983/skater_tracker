"""
Data quality checks for a parsed event in skater_tracker_round2.db.

Run after parsing any event to surface likely parser bugs and suspicious data.

Usage:
    python scripts/check_data_quality_v2.py --event-id N
    python scripts/check_data_quality_v2.py --all
"""
from __future__ import annotations
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from src.db.session_v2 import get_session
from src.db.models_v2 import Event, Classification, Result, SkaterEntry

# ── constants ─────────────────────────────────────────────────────────────────

# WR impossibility floors (seconds).  Times below this are physically impossible.
WR_FLOORS: dict[int, float] = {500: 39.0, 777: 58.0, 1000: 80.0, 1500: 125.0}

# All legitimate short-track distances (both track sizes).
KNOWN_DISTANCES = {
    # 111m track
    111, 222, 333, 444, 500, 611, 700, 777, 1000, 1500, 2000, 3000,
    # 85m track
    85, 170, 212, 255, 340,
    # Small youth tracks (43m, 55m, ~107m, etc.)
    43, 55, 107, 128, 166, 214, 267, 321, 383, 425, 428, 435,
    # Relay distances (short track: 3000m Ladies relay, 5000m Men relay)
    2000, 5000,
    # Non-standard local distances
    165, 277, 400, 440, 595, 666,
    # Small-oval youth distances (100m/50m oval — Gateway/Franklin Park style events)
    100, 200, 300, 600, 800,
}

# Valid result status tokens.  ADV-X variants (ADV-A, ADV-B, ADV-C, …) are
# also accepted — they mean "advanced directly to X final" without racing.
KNOWN_STATUSES = {'Q', 'DNF', 'DNS', 'DQ', 'DSQ', 'PEN', 'ADV', 'PB', 'FNT',
                  'F', 'FA', 'FB', 'FC', 'FD', 'FE', 'FF', 'FG',  # Final group advancement codes
                  'SA', 'SFA', 'SFB', 'SF', 'QFA', 'H',         # Super Final / Semi-Final / Quarter-Final / Heat advancement codes
                  'DNS+', 'DQ+', 'DNF+',          # Buffalo format: DNS/DQ/DNF after qualifying (+ = advanced first)
                  'no contest'}                   # race not held
KNOWN_STATUS_RE = re.compile(r'^ADV-[A-Z]$')

# Pattern for chars that must not appear in a skater name after cleaning.
# Allows: Unicode letters, spaces, hyphens, apostrophes (straight/curly), periods, parens.
INVALID_NAME_RE = re.compile(r"[0-9!@#$%^&*+=\[\]{};:\"<>,/\\|~`]")

# A time-like string leaking into an affiliation / club column.
TIME_LEAK_RE = re.compile(r'\d+:\d{2}\.\d')

MAX_SANE_TIME = 600.0   # 10 minutes — anything slower is flagged as suspicious


# ── reporting ─────────────────────────────────────────────────────────────────

class _Issue:
    def __init__(self, sev: str, code: str, msg: str, rows: list):
        self.sev = sev      # 'ERROR' | 'WARN' | 'INFO'
        self.code = code
        self.msg = msg
        self.rows = rows    # list of short strings for the first few examples


class Report:
    def __init__(self, event: Event):
        self.event = event
        self._issues: list[_Issue] = []

    def add(self, sev: str, code: str, msg: str, rows: list[str]):
        self._issues.append(_Issue(sev, code, msg, rows))

    def error(self, code: str, msg: str, rows: list[str]):
        self.add('ERROR', code, msg, rows)

    def warn(self, code: str, msg: str, rows: list[str]):
        self.add('WARN', code, msg, rows)

    def info(self, code: str, msg: str, rows: list[str]):
        self.add('INFO', code, msg, rows)

    def ok(self, code: str, msg: str):
        self._issues.append(_Issue('OK', code, msg, []))

    def print(self, max_examples: int = 5):
        ev = self.event
        print(f"\n{'='*70}")
        print(f"Event id={ev.id}: {ev.event_name}  [{ev.event_date}]")
        print(f"{'='*70}")

        counts = Counter(i.sev for i in self._issues if i.sev != 'OK')
        if not any(counts.values()):
            print("  ✓ No issues found.")
        else:
            for sev in ('ERROR', 'WARN', 'INFO'):
                for issue in self._issues:
                    if issue.sev != sev:
                        continue
                    n = len(issue.rows)
                    print(f"  [{sev}] {issue.code}: {issue.msg} (n={n})")
                    for ex in issue.rows[:max_examples]:
                        print(f"        {ex}")

        print(f"\n  Summary: {counts['ERROR']} errors, {counts['WARN']} warnings, {counts['INFO']} info")


# ── individual checks ─────────────────────────────────────────────────────────

def _fmt_class(r: Classification) -> str:
    return (f"id={r.id} bib={r.bib} name={r.skater_name!r} "
            f"div={r.division!r} dist={r.distance_m} page={r.page_number}")


def _fmt_result(r: Result) -> str:
    return (f"id={r.id} bib={r.bib} name={r.skater_name!r} "
            f"div={r.division!r} dist={r.distance_m} "
            f"race={r.race_number} page={r.page_number}")


def _fmt_entry(r: SkaterEntry) -> str:
    return f"id={r.id} bib={r.bib} name={r.skater_name!r} div={r.division!r}"


def check_names(db, event_id: int, report: Report):
    """Names must not contain digits or invalid special characters."""
    bad_class = bad_result = bad_entry = []

    for r in db.query(Classification).filter(Classification.event_id == event_id).all():
        if r.skater_name and INVALID_NAME_RE.search(r.skater_name):
            bad_class.append(f"class {_fmt_class(r)}")

    for r in db.query(Result).filter(Result.event_id == event_id).all():
        if r.is_relay:
            continue  # relay team names legitimately contain digits
        if r.skater_name and INVALID_NAME_RE.search(r.skater_name):
            bad_result.append(f"result {_fmt_result(r)}")

    for r in db.query(SkaterEntry).filter(SkaterEntry.event_id == event_id).all():
        if r.skater_name and INVALID_NAME_RE.search(r.skater_name):
            bad_entry.append(f"entry {_fmt_entry(r)}")

    rows = bad_class + bad_result + bad_entry
    if rows:
        report.error('NAME_INVALID_CHARS',
                     'name contains digits or forbidden special chars — likely column parse error',
                     rows)
    else:
        report.ok('NAME_INVALID_CHARS', 'all names clean')

    # Empty names
    empty = (
        [f"class {_fmt_class(r)}" for r in db.query(Classification).filter(
            Classification.event_id == event_id,
            (Classification.skater_name == None) | (Classification.skater_name == '')
        ).all()]
        + [f"result {_fmt_result(r)}" for r in db.query(Result).filter(
            Result.event_id == event_id,
            (Result.skater_name == None) | (Result.skater_name == '')
        ).all()]
    )
    if empty:
        report.error('NAME_EMPTY', 'empty skater name', empty)


def check_affiliation_leak(db, event_id: int, report: Report):
    """Affiliation/club columns must not contain time-like strings (column overflow)."""
    rows = []
    for r in db.query(Classification).filter(Classification.event_id == event_id).all():
        if r.affiliation and TIME_LEAK_RE.search(r.affiliation):
            rows.append(f"class {_fmt_class(r)} affil={r.affiliation!r}")
    for r in db.query(Result).filter(Result.event_id == event_id).all():
        if r.affiliation and TIME_LEAK_RE.search(r.affiliation):
            rows.append(f"result {_fmt_result(r)} affil={r.affiliation!r}")
    for r in db.query(SkaterEntry).filter(SkaterEntry.event_id == event_id).all():
        if r.club and TIME_LEAK_RE.search(r.club):
            rows.append(f"entry {_fmt_entry(r)} club={r.club!r}")
    if rows:
        report.error('AFFIL_TIME_LEAK',
                     'affiliation/club contains a time string — column boundary is wrong', rows)
    else:
        report.ok('AFFIL_TIME_LEAK', 'no time strings in affiliations')


def check_distances(db, event_id: int, report: Report):
    """All distance_m values must be from the known short-track set."""
    unknown_class, unknown_result = [], []
    missing_result = []

    for r in db.query(Classification).filter(
        Classification.event_id == event_id,
        Classification.section_type == 'distance',
    ).all():
        if r.distance_m is None:
            unknown_class.append(f"class {_fmt_class(r)} — distance_m is NULL")
        elif r.distance_m not in KNOWN_DISTANCES:
            unknown_class.append(f"class {_fmt_class(r)} — distance_m={r.distance_m}")

    for r in db.query(Result).filter(Result.event_id == event_id).all():
        if r.distance_m is None:
            missing_result.append(f"result {_fmt_result(r)} — distance_m is NULL")
        elif r.distance_m not in KNOWN_DISTANCES:
            unknown_result.append(f"result {_fmt_result(r)} — distance_m={r.distance_m}")

    if unknown_class:
        report.error('DISTANCE_UNKNOWN_CLASS',
                     'distance classification rows with unknown or null distance_m', unknown_class)
    if missing_result:
        report.error('DISTANCE_NULL_RESULT',
                     'result rows with null distance_m — division/distance context was lost', missing_result)
    if unknown_result:
        report.warn('DISTANCE_UNKNOWN_RESULT',
                    'result rows with a distance not in the known short-track set', unknown_result)
    if not unknown_class and not missing_result and not unknown_result:
        report.ok('DISTANCES', 'all distances are known short-track values')


def check_times(db, event_id: int, report: Report):
    """Check for impossible times and very slow times."""
    impossible, very_slow = [], []

    for r in db.query(Result).filter(Result.event_id == event_id).all():
        t = r.time_seconds
        dist = r.distance_m
        txt = r.time_text or ''

        if t is None:
            continue

        floor = WR_FLOORS.get(dist)
        if floor and t < floor:
            impossible.append(
                f"result {_fmt_result(r)} time={txt} ({t:.3f}s) — floor={floor}s for {dist}m"
            )

        if t > MAX_SANE_TIME:
            very_slow.append(
                f"result {_fmt_result(r)} time={txt} ({t:.1f}s = {t/60:.1f} min)"
            )

    for r in db.query(Classification).filter(Classification.event_id == event_id).all():
        t = r.best_time_seconds
        txt = r.best_time_text or ''
        dist = r.distance_m

        if t is None:
            continue

        floor = WR_FLOORS.get(dist) if dist else None
        if floor and t < floor:
            impossible.append(
                f"class {_fmt_class(r)} time={txt} ({t:.3f}s) — floor={floor}s for {dist}m"
            )

        if t > MAX_SANE_TIME:
            very_slow.append(
                f"class {_fmt_class(r)} time={txt} ({t:.1f}s = {t/60:.1f} min)"
            )

    if impossible:
        report.error('TIME_IMPOSSIBLE',
                     'time is below the WR floor — almost certainly a parse error', impossible)
    else:
        report.ok('TIME_IMPOSSIBLE', 'no times below WR floor')

    if very_slow:
        report.warn('TIME_VERY_SLOW',
                    f'time > {MAX_SANE_TIME:.0f}s ({MAX_SANE_TIME/60:.0f} min) — check for garbled data',
                    very_slow)


def check_result_statuses(db, event_id: int, report: Report):
    """Result status codes should be from the known set."""
    unknown = []
    for r in db.query(Result).filter(
        Result.event_id == event_id,
        Result.status != None,
        Result.status != '',
    ).all():
        if r.status in KNOWN_STATUSES or KNOWN_STATUS_RE.match(r.status):
            continue
        tokens = r.status.split()
        for tok in tokens:
            if tok not in KNOWN_STATUSES and not KNOWN_STATUS_RE.match(tok):
                unknown.append(
                    f"result {_fmt_result(r)} status={r.status!r}"
                )
                break
    if unknown:
        report.warn('STATUS_UNKNOWN',
                    'unrecognised status code — possible new code or column parse error', unknown)
    else:
        report.ok('STATUS_UNKNOWN', 'all result status codes are recognised')


def check_missing_fields(db, event_id: int, report: Report):
    """Results must have division and distance; classification must have division."""
    no_div_result = [
        f"result {_fmt_result(r)}"
        for r in db.query(Result).filter(
            Result.event_id == event_id,
            (Result.division == None) | (Result.division == ''),
        ).all()
    ]
    if no_div_result:
        report.error('RESULT_NO_DIVISION',
                     'results with no division — section header was not parsed', no_div_result)
    else:
        report.ok('RESULT_NO_DIVISION', 'all results have a division')

    no_div_class = [
        f"class {_fmt_class(r)}"
        for r in db.query(Classification).filter(
            Classification.event_id == event_id,
            Classification.section_type == 'distance',
            (Classification.division == None) | (Classification.division == ''),
        ).all()
    ]
    if no_div_class:
        report.error('CLASS_NO_DIVISION',
                     'distance classification rows with no division', no_div_class)


def check_duplicate_bibs_in_race(db, event_id: int, report: Report):
    """The same bib must not appear twice in the same race.

    Duplicates with differing page numbers are likely a multi-day combined PDF
    where both days share the same race numbering scheme — those are downgraded
    to WARN rather than ERROR.
    """
    from sqlalchemy import func
    dupes = (
        db.query(Result.race_number, Result.bib, func.count().label('n'))
        .filter(
            Result.event_id == event_id,
            Result.race_number != None,
            Result.rank != None,
        )
        .group_by(Result.race_number, Result.bib)
        .having(func.count() > 1)
        .all()
    )
    if not dupes:
        report.ok('DUPLICATE_BIB_IN_RACE', 'no duplicate bibs within a race')
        return

    errors, warns = [], []
    for d in dupes:
        pages = [r.page_number for r in db.query(Result).filter(
            Result.event_id == event_id,
            Result.race_number == d.race_number,
            Result.bib == d.bib,
            Result.rank != None,
        ).all()]
        label = f"race_number={d.race_number} bib={d.bib} appears {d.n}× (pages {pages})"
        if len(set(pages)) > 1:
            warns.append(label)   # different pages → likely multi-day combined PDF
        else:
            errors.append(label)  # same page → real parser error

    if errors:
        report.error('DUPLICATE_BIB_IN_RACE',
                     'same bib appears more than once in the same race (same page — parser error)',
                     errors)
    if warns:
        report.warn('DUPLICATE_BIB_IN_RACE_MULTIDAY',
                    'same race number + bib on different pages — likely multi-day combined PDF',
                    warns)
    if not errors and not warns:
        report.ok('DUPLICATE_BIB_IN_RACE', 'no duplicate bibs within a race')


def check_bib_consistency(db, event_id: int, report: Report):
    """Bibs and divisions in results should align with the skater list (if present)."""
    entries = db.query(SkaterEntry).filter(SkaterEntry.event_id == event_id).all()
    if not entries:
        report.info('BIB_CONSISTENCY',
                    'no skater_entries for this event — skipping bib/division cross-check', [])
        return

    entry_bibs = {e.bib for e in entries}
    entry_divs = {e.division for e in entries if e.division}

    # Bibs in results not in skater list
    unknown_bibs = set()
    for r in db.query(Result).filter(Result.event_id == event_id).all():
        if r.bib and r.bib not in entry_bibs:
            unknown_bibs.add(r.bib)
    if unknown_bibs:
        report.warn('BIB_NOT_IN_SKATER_LIST',
                    f'{len(unknown_bibs)} bib(s) in results not found in skater list',
                    [f"bib={b}" for b in sorted(unknown_bibs)])

    # Divisions in results not matching any entry division
    result_divs = {
        r.division for r in db.query(Result).filter(Result.event_id == event_id).all()
        if r.division
    }
    # Also accept divisions that are a suffix of a known skater-list division
    # (e.g. 'Men' matches 'Group A Men' when finals combine multiple qualifying groups)
    def _div_covered(d: str) -> bool:
        if d in entry_divs:
            return True
        # suffix match: 'Men' matches 'Group A Men' (finals combining qualifying groups)
        # prefix match: 'Heartland Men A' matches 'Heartland Men A Northbrook' (div+club in skater list)
        return any(ed.endswith(d) or ed.startswith(d) for ed in entry_divs)

    unknown_divs = {d for d in (result_divs - entry_divs) if not _div_covered(d)}
    if unknown_divs:
        report.warn('DIVISION_MISMATCH',
                    'divisions in results not present in skater list',
                    [f"div={d!r}" for d in sorted(unknown_divs)])
    else:
        report.ok('DIVISION_MISMATCH', 'all result divisions match skater list')

    # Skaters in list with no race result
    result_bibs = {r.bib for r in db.query(Result).filter(Result.event_id == event_id).all()}
    no_result = [e.bib for e in entries if e.bib not in result_bibs]
    if no_result:
        report.info('SKATER_NO_RESULT',
                    f'{len(no_result)} skater(s) in skater list have no race result (may have withdrawn)',
                    [f"bib={b}" for b in sorted(no_result)])


def check_overall_vs_skater_list(db, event_id: int, report: Report):
    """Every skater in the overall classification should be in the skater list."""
    entries = db.query(SkaterEntry).filter(SkaterEntry.event_id == event_id).all()
    if not entries:
        return
    entry_bibs = {e.bib for e in entries}

    rows = [
        f"class {_fmt_class(r)}"
        for r in db.query(Classification).filter(
            Classification.event_id == event_id,
            Classification.section_type == 'overall',
        ).all()
        if r.bib not in entry_bibs
    ]
    if rows:
        report.warn('CLASS_BIB_NOT_IN_LIST',
                    'overall classification bibs not found in skater list', rows)
    else:
        report.ok('CLASS_BIB_NOT_IN_LIST',
                  'all overall classification bibs are in skater list')


def check_schedule_completeness(db, event_id: int, report: Report):
    """Every (division, distance_m) in the distance classification should have result rows."""
    from sqlalchemy import func
    class_pairs = (
        db.query(Classification.division, Classification.distance_m)
        .filter(
            Classification.event_id == event_id,
            Classification.section_type == 'distance',
            Classification.distance_m.isnot(None),
        )
        .distinct()
        .all()
    )
    if not class_pairs:
        report.info('NO_CLASSIFICATION',
                    'no distance classification rows — skipping schedule completeness check', [])
        return

    # Build set of (division, distance_m) that have at least one result row
    result_pairs = set(
        db.query(Result.division, Result.distance_m)
        .filter(Result.event_id == event_id)
        .distinct()
        .all()
    )

    # Build lowercase version of result pairs for case-insensitive fallback
    result_pairs_lower = {((d.lower() if d else d), dist) for d, dist in result_pairs}

    missing = []
    for div, dist in sorted(set(class_pairs), key=lambda x: (x[0] or '', x[1] or 0)):
        if (div, dist) not in result_pairs:
            # Try case-insensitive match as fallback
            if ((div.lower() if div else div), dist) not in result_pairs_lower:
                missing.append(f"division={div!r} distance={dist}m")

    if missing:
        report.warn('SCHEDULE_MISSING',
                    f'{len(missing)} classification division/distance combo(s) have no result rows',
                    missing)
    else:
        report.ok('SCHEDULE_MISSING', 'all classification division/distances have result rows')


# ── main ─────────────────────────────────────────────────────────────────────

def run_checks(db, event: Event) -> Report:
    report = Report(event)
    eid = event.id
    check_names(db, eid, report)
    check_affiliation_leak(db, eid, report)
    check_distances(db, eid, report)
    check_times(db, eid, report)
    check_result_statuses(db, eid, report)
    check_missing_fields(db, eid, report)
    check_duplicate_bibs_in_race(db, eid, report)
    check_bib_consistency(db, eid, report)
    check_overall_vs_skater_list(db, eid, report)
    check_schedule_completeness(db, eid, report)
    return report


def write_event_schedule_md(db, event: Event) -> None:
    """Write a .md file next to the PDF summarising the event's race schedule.

    Shows the interleaved event order: all divisions race round 1 of their 1st
    distance together, then round 2 of their 1st distance, then round 1 of their
    2nd distance, etc.  Event numbers (XX in XXNN race format) are inferred from
    this ordering.

    Overwrites on every QC run.  Skips if no local_path or no distance classification.
    """
    if not event.local_path:
        return

    from sqlalchemy import func

    class_rows = (
        db.query(Classification.division, Classification.distance_m)
        .filter(
            Classification.event_id == event.id,
            Classification.section_type == 'distance',
            Classification.distance_m.isnot(None),
        )
        .order_by(Classification.id)
        .all()
    )
    if not class_rows:
        return

    # ── build per-division ordered distance list ──────────────────────────────
    div_order: list[str] = []
    div_distances: dict[str, list[int]] = {}
    seen_pairs: set = set()
    for div, dist in class_rows:
        if div not in div_distances:
            div_order.append(div)
            div_distances[div] = []
        if (div, dist) not in seen_pairs:
            seen_pairs.add((div, dist))
            div_distances[div].append(dist)

    # ── skater count and repeat detection ────────────────────────────────────
    raw_q = (
        db.query(
            Classification.division,
            Classification.distance_m,
            func.count(Classification.id).label('total'),
            func.count(func.distinct(Classification.bib)).label('distinct_n'),
        )
        .filter(
            Classification.event_id == event.id,
            Classification.section_type == 'distance',
            Classification.distance_m.isnot(None),
        )
        .group_by(Classification.division, Classification.distance_m)
        .all()
    )
    skater_counts = {(r.division, r.distance_m): r.distinct_n for r in raw_q}
    # A distance was raced twice if total rows > distinct bibs (two classification
    # entries per skater, e.g. 444 and 444 #2 both stored as distance_m=444)
    is_repeated = {(r.division, r.distance_m): r.total > r.distinct_n for r in raw_q}

    # Divisions with ≥6 skaters need heats; ≤5 go straight to finals
    div_skaters = {
        div: max(skater_counts.get((div, d), 0) for d in dists)
        for div, dists in div_distances.items()
    }
    is_large = {div: div_skaters[div] >= 6 for div in div_order}

    # ── generate interleaved schedule ─────────────────────────────────────────
    max_slots = max(len(dists) for dists in div_distances.values()) if div_distances else 0
    schedule: list[tuple[int, str, int, str]] = []  # (event_num, div, dist, round)
    event_num = 1

    for slot in range(max_slots):
        is_last = (slot == max_slots - 1)

        # First occurrence of this slot (heats for large, final for small)
        for div in div_order:
            dists = div_distances[div]
            if slot >= len(dists):
                continue
            dist = dists[slot]
            round_lbl = 'Heats' if (is_large[div] and not is_last) else 'Final'
            schedule.append((event_num, div, dist, round_lbl))
            event_num += 1

        # Second occurrence (finals for large; repeat final for small if distance was repeated)
        if not is_last:
            for div in div_order:
                dists = div_distances[div]
                if slot >= len(dists):
                    continue
                dist = dists[slot]
                if is_large[div]:
                    schedule.append((event_num, div, dist, 'Final'))
                    event_num += 1
                elif is_repeated.get((div, dist), False):
                    schedule.append((event_num, div, dist, 'Final (#2)'))
                    event_num += 1

    # ── write the file ────────────────────────────────────────────────────────
    md_path = Path(event.local_path).with_suffix('.md')
    lines = [
        f"# {event.event_name} — Race Schedule",
        "",
        f"Generated from distance classification (event id={event.id}).",
        "",
        "## Event Order",
        "",
        "Races interleave across divisions: all divisions complete one round before",
        "moving to the next.  Event numbers match the XXNN race number format (XX = event).",
        "",
        "| Event | Division | Distance | Round | Skaters |",
        "|---|---|---|---|---|",
    ]
    for ev, div, dist, rnd in schedule:
        n = skater_counts.get((div, dist), '?')
        lines.append(f"| {ev:02d} | {div} | {dist}m | {rnd} | {n} |")

    lines += [
        "",
        "## Notes",
        "",
        "- Divisions with ≤5 skaters skip heats and go straight to finals.",
        "- Small divisions repeat their first N-1 distances (shown as Final / Final (#2)).",
        "- The last distance slot has no heats for anyone (direct finals).",
        "- Race number format: XXNN where XX = event number above, NN = race within event.",
    ]

    md_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f"  Schedule saved → {md_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument('--event-id', type=int, help='Check a single event by id')
    grp.add_argument('--all', action='store_true', help='Check all parsed events')
    parser.add_argument('--max-examples', type=int, default=5,
                        help='Max example rows printed per issue (default: 5)')
    args = parser.parse_args()

    db = get_session()

    if args.all:
        events = (
            db.query(Event)
            .filter(Event.parsed_at != None)
            .order_by(Event.id)
            .all()
        )
    else:
        events = db.query(Event).filter(Event.id == args.event_id).all()
        if not events:
            print(f"ERROR: event id={args.event_id} not found or not yet parsed.")
            sys.exit(1)

    total_errors = total_warns = 0
    for event in events:
        report = run_checks(db, event)
        report.print(max_examples=args.max_examples)
        write_event_schedule_md(db, event)
        counts = Counter(i.sev for i in report._issues)
        total_errors += counts['ERROR']
        total_warns += counts['WARN']

    if len(events) > 1:
        print(f"\n{'='*70}")
        print(f"TOTAL across {len(events)} events: {total_errors} errors, {total_warns} warnings")

    db.close()


if __name__ == '__main__':
    main()
