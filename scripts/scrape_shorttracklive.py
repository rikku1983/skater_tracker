"""
Scraper for USS short-track events published on shorttracklive.info.

Accepts the event webpage URL directly. Parses comp/saison from the URL,
auto-discovers divisions, and matches the event in the DB by name/date.

Raw HTML responses are cached to disk (data/scrape_cache/) on the first run.
All subsequent --force re-parses replay from cache with zero network requests.
Use --no-cache to bypass the cache and hit the server fresh.

Usage:
    python scripts/scrape_shorttracklive.py --url "https://www.shorttracklive.info/index.php?comp=1004&m=0&saison=20" [--force] [--dry-run] [--no-cache]
    python scripts/scrape_shorttracklive.py --url <URL> --event-id N   # skip DB auto-match
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parents[1]))
from src.db.session_v2 import init_db, get_session
from src.db.models_v2 import Event, Result, SkaterEntry

BASE_URL      = 'https://www.shorttracklive.info/index.php'
REQUEST_DELAY = 0.5   # seconds between live requests (polite)
CACHE_DIR     = Path(__file__).parents[1] / 'data' / 'scrape_cache'

# dist parameter → (distance_m, laps)
DIST_MAP: dict[int, tuple[int, float]] = {
    14: (222,  2.0),
    16: (333,  3.0),
    17: (500,  4.5),
    19: (1000, 9.0),
    21: (1500, 13.5),
    58: (777,  7.0),
    70: (340,  4.0),   # 4 laps × 85 m track
    79: (777,  7.0),   # 7 laps × 111 m track (relay)
    51: (400,  None),  # 400 m (non-standard distance, ~4.7 laps on 85m track)
    57: (111,  1.0),   # 1 lap × 111.12 m track (111m expressed precisely)
    62: (107,  1.0),   # 1 lap × 107 m track
    63: (214,  2.0),   # 2 laps × 107 m track
    64: (321,  3.0),   # 3 laps × 107 m track
    65: (428,  4.0),   # 4 laps × 107 m track
    66: (383,  None),  # ~3.58 laps × 107 m track (383m super final)
}

RELAY_DIST_IDS = {79}


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_path(comp: int, saison: int) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f'shorttracklive_{comp}_{saison}.json'


def _load_cache(comp: int, saison: int) -> dict[str, str]:
    p = _cache_path(comp, saison)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def _save_cache(comp: int, saison: int, cache: dict[str, str]) -> None:
    _cache_path(comp, saison).write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8'
    )


def _cache_key(params: dict) -> str:
    return '&'.join(f'{k}={v}' for k, v in sorted(params.items()))


# ── HTTP / cache fetch ────────────────────────────────────────────────────────

def _get(url: str, params: dict, http: requests.Session,
         cache: dict, comp: int, saison: int) -> tuple[BeautifulSoup, bool]:
    """Fetch a page, using cache when available. Returns (soup, was_cached)."""
    key = _cache_key(params)
    if key in cache:
        return BeautifulSoup(cache[key], 'html.parser'), True

    resp = http.get(url, params=params, timeout=30)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)

    cache[key] = resp.text
    _save_cache(comp, saison, cache)

    return BeautifulSoup(resp.text, 'html.parser'), False


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _parse_time(text: str) -> Optional[float]:
    t = text.strip()
    m = re.match(r'^(\d+):(\d{2})\.(\d+)$', t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / (10 ** len(m.group(3)))
    m = re.match(r'^(\d+)\.(\d+)$', t)
    if m:
        return int(m.group(1)) + int(m.group(2)) / (10 ** len(m.group(2)))
    return None


def _norm_round_type(label: str) -> str:
    s = label.lower()
    if 'super' in s:
        return 'SUPER FINAL'
    if 'semi' in s or 'heat' in s or 'quarter' in s:
        return 'HEATS'
    if 'final' in s:
        return 'FINAL'
    return 'HEATS'


_BIRTH_YEAR_RE = re.compile(r'\s+\d{4}$')


def _parse_name(cell) -> str:
    full = cell.find('span', class_='sklive-full')
    if full:
        a = full.find('a')
        raw = a.get_text(strip=True) if a else full.get_text(strip=True)
    else:
        a = cell.find('a')
        raw = a.get_text(strip=True) if a else cell.get_text(strip=True)
    raw = raw.replace('\xa0', ' ').replace('*', "'")
    raw = _BIRTH_YEAR_RE.sub('', raw)
    # Remove embedded seeding digits like "WU 6 9 11 Jonathan" → "WU Jonathan"
    raw = re.sub(r'(\s+\d+){2,}', '', raw)
    return raw.strip()


# ── URL parsing ───────────────────────────────────────────────────────────────

def parse_url(url: str) -> tuple[int, int]:
    """Extract (comp, saison) from a shorttracklive.info URL."""
    qs = parse_qs(urlparse(url).query)
    try:
        comp   = int(qs['comp'][0])
        saison = int(qs['saison'][0])
    except (KeyError, ValueError, IndexError):
        print('ERROR: URL must contain comp= and saison= parameters')
        sys.exit(1)
    return comp, saison


# ── Division discovery (m=6) ──────────────────────────────────────────────────

def get_divisions(comp: int, saison: int, http: requests.Session,
                  cache: dict) -> list[tuple[int, int, str]]:
    """Fetch m=6 schedule page and return all (cat, categ, label) division tuples."""
    soup, _ = _get(BASE_URL, {'comp': comp, 'saison': saison, 'm': 6},
                   http, cache, comp, saison)

    seen: set[tuple[int, int]] = set()
    divisions: list[tuple[int, int, str]] = []

    for a in soup.find_all('a', href=True):
        href = a['href']
        params: dict[str, str] = {}
        for part in href.split('?', 1)[-1].split('&'):
            if '=' in part:
                k, v = part.split('=', 1)
                params[k] = v

        # Division links have cat, categ, and m=1 (or no m= at all for nav links)
        if 'cat' not in params or 'categ' not in params:
            continue
        try:
            cat   = int(params['cat'])
            categ = int(params['categ'])
        except ValueError:
            continue
        if cat <= 0 or categ <= 0:
            continue

        key = (cat, categ)
        if key in seen:
            continue
        seen.add(key)

        label = a.get_text(strip=True).upper() or f'CAT {cat}'
        divisions.append((cat, categ, label))

    # Sort by cat for consistent ordering
    divisions.sort(key=lambda x: x[0])
    return divisions


# ── Event name from website (m=6) ─────────────────────────────────────────────

def get_website_event_name(comp: int, saison: int, http: requests.Session,
                           cache: dict) -> str:
    """Return the event name as shown on the m=6 page.

    The name lives in the first blue-background <td> inside <table id='program'>.
    Full text: "2025 Buffalo Short Track Championship, NY, USA, 25.10. - 26.10.2025"
    We return everything before the first ", XX," location suffix.
    """
    soup, _ = _get(BASE_URL, {'comp': comp, 'saison': saison, 'm': 6},
                   http, cache, comp, saison)
    table = soup.find('table', id='program')
    if table:
        td = table.find('td', class_=lambda c: c and 'sklive-blue-bg' in c)
        if td:
            full = td.get_text(strip=True)
            # Strip trailing ", City, Country, DD.MM. - DD.MM.YYYY" or ", DD.MM. - ..."
            name = re.split(r',\s*(?:[A-Z]{2}[,\s]|\d{2}\.)', full)[0].strip()
            return name
    return ''


# ── DB event matching ─────────────────────────────────────────────────────────

def _word_score(a: str, b: str) -> float:
    """Fraction of significant words from `a` that appear in `b`."""
    stopwords = {'short', 'track', 'speed', 'skating', 'championship', 'championships',
                 'open', 'cup', 'the', 'and', 'of', 'in', 'at', 'st', '&'}
    words_a = {w.lower() for w in re.findall(r'\w+', a) if w.lower() not in stopwords}
    words_b = {w.lower() for w in re.findall(r'\w+', b)}
    if not words_a:
        return 0.0
    return len(words_a & words_b) / len(words_a)


def find_event_in_db(website_name: str, db) -> list[Event]:
    """Return DB events whose names have the most word overlap with the website name."""
    all_events = db.query(Event).all()
    scored = [(e, _word_score(website_name, e.event_name)) for e in all_events]
    max_score = max(s for _, s in scored)
    if max_score < 0.3:
        return []
    threshold = max(max_score - 0.15, 0.3)
    matches = [e for e, s in scored if s >= threshold]
    matches.sort(key=lambda e: _word_score(website_name, e.event_name), reverse=True)
    return matches


# ── Participants (m=9) ────────────────────────────────────────────────────────

def scrape_participants(comp: int, saison: int, http: requests.Session,
                        cache: dict) -> tuple[list[dict], bool]:
    soup, cached = _get(BASE_URL, {'comp': comp, 'cat': -1, 'categ': -1, 'm': 9, 'saison': saison},
                        http, cache, comp, saison)
    rows_out: list[dict] = []
    cur_division: Optional[str] = None

    for tr in soup.find_all('tr'):
        strong = tr.find('strong')
        if strong:
            cur_division = strong.get_text(strip=True)
            continue
        tds = tr.find_all('td')
        if len(tds) < 5 or tr.get('id') == 'expl':
            continue
        bib_text  = tds[2].get_text(strip=True).rstrip('.')
        name_text = _parse_name(tds[3])
        club_text = tds[4].get_text(strip=True)
        if not bib_text or not bib_text.isdigit():
            continue
        rows_out.append({
            'bib':         bib_text,
            'skater_name': name_text,
            'club':        club_text or None,
            'division':    cur_division,
            'page_number': None,
        })

    return rows_out, cached


# ── Division result links (m=1) ───────────────────────────────────────────────

def get_result_links(comp: int, saison: int, cat: int, categ: int,
                     http: requests.Session, cache: dict) -> tuple[list[dict], bool]:
    soup, cached = _get(BASE_URL, {'comp': comp, 'saison': saison, 'cat': cat, 'categ': categ, 'm': 1},
                        http, cache, comp, saison)
    seen: set[tuple] = set()
    unique: list[dict] = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'm=2' not in href:
            continue
        params: dict[str, str] = {}
        for part in href.split('?', 1)[-1].split('&'):
            if '=' in part:
                k, v = part.split('=', 1)
                params[k] = v
        if 'dist' not in params or 'ord' not in params:
            continue
        key = (int(params['dist']), int(params['ord']))
        if key not in seen:
            seen.add(key)
            unique.append({'dist': key[0], 'ord': key[1]})
    return unique, cached


# ── Race results (m=2) ───────────────────────────────────────────────────────

def scrape_results(comp: int, saison: int, cat: int, categ: int,
                   dist_id: int, ord_id: int, http: requests.Session,
                   cache: dict) -> tuple[list[dict], bool]:
    soup, cached = _get(BASE_URL, {
        'comp': comp, 'saison': saison,
        'cat': cat, 'categ': categ,
        'dist': dist_id, 'ord': ord_id, 'm': 2,
    }, http, cache, comp, saison)

    distance_m, laps = DIST_MAP.get(dist_id, (None, None))
    is_relay = dist_id in RELAY_DIST_IDS

    rows_out: list[dict] = []
    cur_division = cur_round_type = cur_round_label = None
    cur_race = cur_heat = cur_group = None

    table = soup.find('table', id='Heats')
    if table is None:
        return rows_out, cached

    for tr in table.find_all('tr'):
        tr_cls   = tr.get('class', [])
        tr_id    = tr.get('id', '')
        tr_style = tr.get('style', '')
        tds      = tr.find_all('td')
        if not tds:
            continue

        b_tag = tr.find('b')
        if b_tag and 'sklive-large' in (tr.parent.get('class') or []):
            block_text = b_tag.get_text(' ', strip=True).replace('\xa0', ' ')
            m2 = re.match(r'(.+?)\s{2,}(HEATS?|QUARTER.?FINAL|SEMI.?FINAL|SUPER FINAL|FINAL)', block_text, re.I)
            if m2:
                cur_division    = m2.group(1).strip()
                round_str       = m2.group(2).strip()
                cur_round_type  = _norm_round_type(round_str)
                cur_round_label = round_str
            continue

        if 'sklive-large' in tr_cls and 'background-color' in tr_style:
            text   = tr.get_text(' ', strip=True)
            race_m = re.search(r'RACE:\s*(\d+)', text)
            heat_m = re.search(r'HEAT:\s*(\d+)', text)
            grp_m  = re.search(r'GROUP:\s*([A-Z])', text)
            if race_m:
                cur_race  = int(race_m.group(1))
                cur_heat  = int(heat_m.group(1)) if heat_m else None
                cur_group = grp_m.group(1) if grp_m else None
            continue

        if tr_id == 'expl' or len(tds) < 8:
            continue

        place_text     = tds[0].get_text(strip=True)
        qual_text      = tds[1].get_text(strip=True)
        bib_text       = tds[3].get_text(strip=True).rstrip('. ')
        name_text      = _parse_name(tds[4])
        club_text      = tds[5].get_text(strip=True)
        time_cell      = tds[7]
        time_main      = time_cell.find(string=True, recursive=False)
        time_raw       = (time_main or '').strip()
        time_note      = time_cell.find('small')
        time_note_text = time_note.get_text(strip=True) if time_note else ''

        if not bib_text or not name_text:
            continue

        try:
            rank = int(place_text)
        except ValueError:
            rank = None

        time_secs        = _parse_time(time_raw) if time_raw not in ('NT', '', '-') else None
        time_text_stored = time_raw if time_raw not in ('NT', '', '-') else None

        status = qual_text or None
        if time_note_text:
            note_up = time_note_text.upper()
            if 'NO START' in note_up or 'DNS' in note_up:
                status = 'DNS'; time_text_stored = None; time_secs = None
            elif 'PENALTY' in note_up or 'PEN' in note_up:
                status = 'PEN'
            elif 'DISQ' in note_up or 'DQ' in note_up:
                status = 'DQ'
            elif 'NO FINISH' in note_up or 'NOT FINISH' in note_up or 'DNF' in note_up:
                status = 'DNF'
            elif 'YELLOW CARD' in note_up or 'IMPEDING' in note_up:
                status = 'PEN'
            elif 'FALSE START' in note_up:
                status = 'DQ'
            elif 'FINISHED' in note_up:
                status = None   # informational only; skater finished normally
            elif not qual_text:
                status = time_note_text

        rows_out.append({
            'division':     cur_division,
            'distance_m':   distance_m,
            'laps':         laps,
            'round_type':   cur_round_type,
            'round_label':  cur_round_label,
            'race_number':  cur_race,
            'heat':         cur_heat,
            'group_label':  cur_group,
            'rank':         rank,
            'bib':          bib_text,
            'skater_name':  name_text,
            'affiliation':  club_text or None,
            'time_text':    time_text_stored,
            'time_seconds': time_secs,
            'status':       status,
            'is_relay':     is_relay,
            'page_number':  None,
        })

    return rows_out, cached


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--url',      required=True,
                    help='shorttracklive.info event URL (any page for the competition)')
    ap.add_argument('--event-id', type=int, default=None,
                    help='DB event id — skip auto-match if already known')
    ap.add_argument('--force',    action='store_true',
                    help='Re-parse even if already parsed (replays from cache)')
    ap.add_argument('--no-cache', action='store_true',
                    help='Ignore existing cache and fetch fresh from server')
    ap.add_argument('--dry-run',  action='store_true',
                    help='Print counts but do not write to DB')
    args = ap.parse_args()

    comp, saison = parse_url(args.url)
    print(f'Competition: comp={comp}, saison={saison}')

    init_db()
    db = get_session()

    cache: dict[str, str] = {} if args.no_cache else _load_cache(comp, saison)
    http = requests.Session()
    http.headers['User-Agent'] = 'skater-tracker-research/1.0'
    live_calls = 0

    # ── Resolve DB event ──────────────────────────────────────────────────────
    if args.event_id:
        event = db.query(Event).filter(Event.id == args.event_id).first()
        if not event:
            print(f'ERROR: event {args.event_id} not found in DB')
            sys.exit(1)
    else:
        website_name = get_website_event_name(comp, saison, http, cache)
        print(f'Website event name: "{website_name}"')
        candidates = find_event_in_db(website_name, db)

        if not candidates:
            print('ERROR: no matching event found in DB. Use --event-id to specify manually.')
            sys.exit(1)

        if len(candidates) > 1:
            print('Multiple DB candidates — use --event-id to pick one:')
            for e in candidates:
                print(f'  id={e.id:4d}  {e.event_date or "":12s}  {e.event_name}')
            sys.exit(1)

        event = candidates[0]

    print(f'DB event id={event.id}: {event.event_name}')

    if event.parsed_at and not args.force:
        print(f'Already parsed at {event.parsed_at}. Use --force to re-parse (from cache).')
        db.close()
        return

    # ── Discover divisions from m=6 ───────────────────────────────────────────
    divisions = get_divisions(comp, saison, http, cache)
    if not divisions:
        print('ERROR: no divisions found on m=6 schedule page')
        sys.exit(1)
    print(f'Found {len(divisions)} divisions: {", ".join(label for _, _, label in divisions)}')

    if not args.dry_run:
        db.query(SkaterEntry).filter(SkaterEntry.event_id == event.id).delete()
        db.query(Result).filter(Result.event_id == event.id).delete()
        db.flush()

    total_entries = total_results = 0

    # ── Participants ──────────────────────────────────────────────────────────
    participant_rows, cached = scrape_participants(comp, saison, http, cache)
    if not cached:
        live_calls += 1
    print(f'  Participants: {len(participant_rows)} ({"cached" if cached else "live"})')

    if not args.dry_run:
        for r in participant_rows:
            db.add(SkaterEntry(event_id=event.id, **r))
        db.flush()
    total_entries = len(participant_rows)

    # ── Results ───────────────────────────────────────────────────────────────
    for cat, categ, div_label in divisions:
        links, lk_cached = get_result_links(comp, saison, cat, categ, http, cache)
        if not lk_cached:
            live_calls += 1

        if not links:
            print(f'  {div_label}: no result links found')
            continue

        div_results = 0
        for link in links:
            dist_id = link['dist']
            ord_id  = link['ord']
            rows, r_cached = scrape_results(comp, saison, cat, categ, dist_id, ord_id, http, cache)
            if not r_cached:
                live_calls += 1

            if not args.dry_run and rows:
                for r in rows:
                    db.add(Result(event_id=event.id, **r))
                db.flush()
            div_results += len(rows)

        total_results += div_results
        print(f'  {div_label}: {div_results} result rows')

    # ── Finish ────────────────────────────────────────────────────────────────
    if not args.dry_run:
        event.parsed_at    = datetime.now(timezone.utc).isoformat()
        event.parse_errors = None
        db.commit()

    total_cached = len(cache)
    print()
    print(f'Scrape complete for event {event.id} ({event.event_name}):')
    print(f'  Skater entries  : {total_entries}')
    print(f'  Result rows     : {total_results}')
    print(f'  Live HTTP calls : {live_calls}  (cached: {total_cached - live_calls} of {total_cached})')
    cache_file = _cache_path(comp, saison)
    if cache_file.exists():
        print(f'  Cache           : {cache_file.name}  ({cache_file.stat().st_size // 1024} KB)')
    if args.dry_run:
        print('  (DRY RUN — nothing written to DB)')

    db.close()


if __name__ == '__main__':
    main()
