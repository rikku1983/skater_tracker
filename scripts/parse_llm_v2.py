"""
LLM-based parser for image-based PDFs, writing to skater_tracker_round2.db.

Reuses the vision + prompt infrastructure from src/parsers/llm_parser.py but
writes directly to the v2 schema (Event, Classification, Result, SkaterEntry).

Usage:
    ANTHROPIC_API_KEY=sk-ant-... python scripts/parse_llm_v2.py --pdf-path PATH
    ANTHROPIC_API_KEY=sk-ant-... python scripts/parse_llm_v2.py --pdf-path PATH --force
    ANTHROPIC_API_KEY=sk-ant-... python scripts/parse_llm_v2.py --pdf-path PATH --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parents[1]))

# Load .env from project root if present (so ANTHROPIC_API_KEY doesn't need to be
# exported manually on every run)
_env_file = Path(__file__).parents[1] / '.env'
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

import anthropic
from scripts.fill_missing_race_context import fill_missing_race_context
from src.db.session_v2 import init_db, get_session
from src.db.models_v2 import Event, Classification, Result, SkaterEntry
from src.parsers.llm_parser import (
    _call_claude_text,
    _call_claude_vision,
    _page_to_base64,
    API_CALL_DELAY,
    MAX_PAGES_PER_CALL,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── page-response cache ───────────────────────────────────────────────────────

def _load_cache(pdf_path: Path) -> dict:
    cache_path = pdf_path.with_suffix('.llm_cache.json')
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(pdf_path: Path, cache: dict) -> None:
    cache_path = pdf_path.with_suffix('.llm_cache.json')
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


# ── division normalisation ────────────────────────────────────────────────────

_GROUP_PLACEHOLDER_RE = re.compile(r'^Group\s+[A-Z]\s*$', re.I)


def _normalize_division(raw: str | None) -> str | None:
    """Return None for empty or generic 'Group A/B/C' placeholder divisions."""
    s = (raw or '').strip()
    if not s or _GROUP_PLACEHOLDER_RE.match(s):
        return None
    return s


# ── round-type helpers ────────────────────────────────────────────────────────

_SUPER_FINAL_RE = re.compile(r'super\s*final', re.I)
_FINAL_RE       = re.compile(r'\bfinal\b', re.I)
_HEAT_RE        = re.compile(r'\b(?:heat|semi|quarter|prelim)', re.I)
_GROUP_RE       = re.compile(r'\b([A-C])\s*(?:final|group)\b|\bfinal\s+([A-C])\b|^([A-C])\b', re.I)
_HEAT_NUM_RE    = re.compile(r'heat\s+(\d+)', re.I)
_RACE_NUM_RE    = re.compile(r'race\s*#?\s*(\d+)', re.I)


def _parse_round(round_str: str | None) -> tuple[str, str | None, int | None, int | None]:
    """Return (round_type, group_label, heat_num, race_number) from a free-form round string."""
    if not round_str:
        return 'HEATS', None, None, None
    s = round_str.strip()

    race_number = None
    m = _RACE_NUM_RE.search(s)
    if m:
        race_number = int(m.group(1))

    if _SUPER_FINAL_RE.search(s):
        return 'SUPER FINAL', None, None, race_number

    if _FINAL_RE.search(s):
        group = None
        gm = _GROUP_RE.search(s)
        if gm:
            group = (gm.group(1) or gm.group(2) or gm.group(3)).upper()
        return 'FINAL', group, None, race_number

    heat = None
    hm = _HEAT_NUM_RE.search(s)
    if hm:
        heat = int(hm.group(1))
    return 'HEATS', None, heat, race_number


_TRACK_LAPS = {
    # 111m track
    111: 1.0, 222: 2.0, 333: 3.0, 444: 4.0, 500: 4.5,
    777: 7.0, 1000: 9.0, 1500: 13.5,
    # 85m track
    43: 0.5, 85: 1.0, 128: 1.5, 170: 2.0, 212: 2.5, 214: 2.5,
    255: 3.0, 340: 4.0, 425: 5.0, 428: 5.0,
    # 55m track
    55: 1.0, 166: 3.0,
    # ~107m track
    107: 1.0, 267: 2.5,
}


def _laps(distance_m: int | None) -> float | None:
    if distance_m is None:
        return None
    return _TRACK_LAPS.get(distance_m)


def _is_classification_round(round_str: str | None, division: str = '') -> tuple[bool, str]:
    """Return (is_classification, section_type) for a round string or division name."""
    for s in (round_str or '', division):
        s = s.lower()
        if 'overall' in s:
            return True, 'overall'
        if 'distance' in s and ('class' in s or 'rank' in s):
            return True, 'distance'
        if 'classification' in s or 'ranking' in s:
            return True, 'overall'
    return False, ''


# ── parse time ────────────────────────────────────────────────────────────────

def _parse_time(text: str | None) -> float | None:
    if not text:
        return None
    text = text.strip()
    m = re.match(r'^(\d+):(\d{2})\.(\d+)$', text)
    if m:
        return int(m.group(1)) * 60 + float(f"{m.group(2)}.{m.group(3)}")
    m = re.match(r'^(\d{1,2})\.(\d+)$', text)
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    return None


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf-path', required=True)
    parser.add_argument('--force', action='store_true',
                        help='Re-parse even if already parsed')
    parser.add_argument('--dry-run', action='store_true',
                        help='Parse but do not write to DB')
    parser.add_argument('--no-cache', action='store_true',
                        help='Ignore existing cache and make fresh API calls')
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path).resolve()
    if not pdf_path.exists():
        print(f"ERROR: file not found: {pdf_path}")
        sys.exit(1)

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)

    init_db()
    db = get_session()

    event = db.query(Event).filter(Event.local_path == str(pdf_path)).first()
    if not event:
        print(f"ERROR: event not found in DB for path {pdf_path}")
        sys.exit(1)

    print(f"Event id={event.id}: {event.event_name}")

    if event.parsed_at and not args.force:
        print(f"Already parsed at {event.parsed_at}. Use --force to re-parse.")
        db.close()
        return

    if not args.dry_run:
        db.query(SkaterEntry).filter(SkaterEntry.event_id == event.id).delete()
        db.query(Classification).filter(Classification.event_id == event.id).delete()
        db.query(Result).filter(Result.event_id == event.id).delete()
        db.flush()

    result_rows = 0
    classif_rows = 0
    parse_errors: list[str] = []
    call_count = 0

    def _write_sections(sections: list[dict], page_num: int):
        nonlocal result_rows, classif_rows
        for sec in sections:
            division   = _normalize_division(sec.get('division'))
            distance_m = sec.get('distance_m')
            round_str  = sec.get('round') or ''
            if distance_m is not None:
                try:
                    distance_m = int(distance_m)
                except (TypeError, ValueError):
                    distance_m = None

            is_classif, section_type = _is_classification_round(round_str, division or '')
            round_type, group_label, heat_num, race_number = _parse_round(round_str)

            for row in sec.get('results', []):
                name = (row.get('name') or '').strip()
                if not name:
                    continue
                time_text = row.get('time') or None
                status    = row.get('status') or None
                # Skip rows with no time and no status (registration entries)
                if not time_text and not status:
                    continue

                time_secs = _parse_time(time_text)
                try:
                    rank = int(row['rank']) if row.get('rank') is not None else None
                except (TypeError, ValueError):
                    rank = None
                try:
                    points = float(row['points']) if row.get('points') is not None else None
                except (TypeError, ValueError):
                    points = None
                bib    = str(row['bib']) if row.get('bib') is not None else None
                affil  = (row.get('club') or '').strip() or None

                if is_classif:
                    c = Classification(
                        event_id     = event.id,
                        section_type = section_type,
                        division     = division,
                        distance_m   = distance_m if section_type == 'distance' else None,
                        rank         = rank,
                        bib          = bib,
                        skater_name  = name,
                        affiliation  = affil,
                        points       = points,
                        best_time_text    = time_text,
                        best_time_seconds = time_secs,
                        page_number  = page_num,
                    )
                    if not args.dry_run:
                        db.add(c)
                    classif_rows += 1
                else:
                    # Skip unfillable orphan rows — no division AND no race_number
                    if division is None and race_number is None:
                        continue
                    r = Result(
                        event_id     = event.id,
                        division     = division,
                        distance_m   = distance_m,
                        laps         = _laps(distance_m),
                        is_relay     = 'relay' in (division or '').lower(),
                        round_type   = round_type,
                        round_label  = round_str or None,
                        race_number  = race_number,
                        heat         = heat_num,
                        group_label  = group_label,
                        rank         = rank,
                        bib          = bib,
                        skater_name  = name,
                        affiliation  = affil,
                        time_text    = time_text,
                        time_seconds = time_secs,
                        status       = status,
                        points       = points,
                        page_number  = page_num,
                    )
                    if not args.dry_run:
                        db.add(r)
                    result_rows += 1

    cache = {} if args.no_cache else _load_cache(pdf_path)

    with pdfplumber.open(str(pdf_path)) as pdf:
        page_texts: list[tuple[int, str, bool]] = []
        for i, page in enumerate(pdf.pages, 1):
            text = (page.extract_text() or '').strip()
            page_texts.append((i, text, len(text) < 50))

        batch_texts:  list[str] = []
        batch_pages:  list[int] = []
        prev_page_text = ''

        def flush_text_batch():
            nonlocal call_count, prev_page_text
            if not batch_texts:
                return
            page_key = str(batch_pages[0])
            if page_key in cache:
                sections = cache[page_key]
                logger.info("Cache hit pages %s", batch_pages)
            else:
                if call_count > 0:
                    time.sleep(API_CALL_DELAY)
                call_count += 1
                logger.info("Text batch pages %s", batch_pages)
                try:
                    data = _call_claude_text(client, batch_texts, prev_page_text=prev_page_text)
                    sections = data.get('sections', [])
                    cache[page_key] = sections
                    if not args.dry_run:
                        _save_cache(pdf_path, cache)
                except Exception as e:
                    err = f"LLM text parse failed (pages {batch_pages}): {e}"
                    parse_errors.append(err)
                    logger.warning(err)
                    sections = []
            _write_sections(sections, batch_pages[0])
            prev_page_text = batch_texts[-1]
            batch_texts.clear()
            batch_pages.clear()

        for page_num, text, is_image in page_texts:
            if is_image:
                flush_text_batch()
                page_key = str(page_num)
                if page_key in cache:
                    sections = cache[page_key]
                    logger.info("Cache hit page %d", page_num)
                else:
                    if call_count > 0:
                        time.sleep(API_CALL_DELAY)
                    call_count += 1
                    logger.info("Vision page %d", page_num)
                    try:
                        page = pdf.pages[page_num - 1]
                        b64  = _page_to_base64(page)
                        data = _call_claude_vision(client, b64, page_num,
                                                   prev_page_text=prev_page_text)
                        sections = data.get('sections', [])
                        cache[page_key] = sections
                        if not args.dry_run:
                            _save_cache(pdf_path, cache)
                    except Exception as e:
                        err = f"LLM vision failed (page {page_num}): {e}"
                        parse_errors.append(err)
                        logger.warning(err)
                        sections = []
                _write_sections(sections, page_num)
                prev_page_text = text
            else:
                if len(batch_texts) >= MAX_PAGES_PER_CALL:
                    flush_text_batch()
                batch_texts.append(text)
                batch_pages.append(page_num)

        flush_text_batch()

    if not args.dry_run:
        db.flush()
        fill_summary = fill_missing_race_context(event.id, db, verbose=True)
        db.flush()
        event.parsed_at    = datetime.now(timezone.utc).isoformat()
        event.parse_errors = '\n'.join(parse_errors) if parse_errors else None
        db.commit()
    else:
        fill_summary = {'filled': 0, 'div_only': 0, 'unknown': 0, 'review_items': []}

    mode = ' [DRY RUN]' if args.dry_run else ''
    print(f"  Result rows      : {result_rows}{mode}")
    print(f"  Classification   : {classif_rows}{mode}")
    print(f"  API calls        : {call_count}")
    print(f"  Group A heats filled: {fill_summary['filled']} races resolved")
    if fill_summary['div_only'] or fill_summary['unknown']:
        print(f"  Unresolved      : {fill_summary['div_only']} div-only, "
              f"{fill_summary['unknown']} unknown")
    for _, msg in fill_summary['review_items']:
        print(f"    {msg}")
    if parse_errors:
        print(f"  Errors ({len(parse_errors)}):")
        for e in parse_errors:
            print(f"    {e}")
    db.close()


if __name__ == '__main__':
    main()
