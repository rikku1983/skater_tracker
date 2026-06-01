# New Event Pipeline

Full pipeline for ingesting a new event: download → parse → link → QC → normalize → sync.

## Usage

```
/new_event
/new_event --season 20252026
```

No arguments → processes whatever new PDFs are available across all seasons.  
`--season` → limit to a specific season (e.g. `20252026`).

---

## Step 1 — Download new PDFs

```bash
.venv/bin/python scripts/download_pdfs.py [--season <season>]
```

This discovers new events on the USS website and downloads their PDFs. Already-downloaded
events are skipped automatically. Note the event names printed so you can identify what's new.

If you already have the PDF locally and just want to parse it, skip to Step 2.

---

## Step 2 — Parse and load into SQLite

```bash
.venv/bin/python scripts/parse_and_load.py [--season <season>]
```

Parses all unloaded PDFs and inserts results into the local SQLite database. Safe to re-run.

Check the output for parse errors or events marked `unparseable`. If an event fails to parse,
investigate separately (may need LLM parser or manual handling).

---

## Step 3 — Link clubs

```bash
.venv/bin/python scripts/link_clubs.py
```

Links raw affiliation strings to `club_id` FK. Safe to re-run — only updates unlinked rows.

---

## Step 4 — Flag data quality

```bash
.venv/bin/python scripts/flag_data_quality.py
```

Clears and rewrites `data_flags` on all results. Flags `TIME_IMPOSSIBLE` and
`TIME_LIKELY_MISLABELED_DIST`. Safe to re-run.

Review any flagged rows for the new event:

```python
import sqlite3
conn = sqlite3.connect("data/skater_tracker_round2.db")
for row in conn.execute("SELECT id, event_id, skater_name, distance_m, time_seconds, data_flags FROM results WHERE data_flags IS NOT NULL AND event_id IN (SELECT id FROM events ORDER BY id DESC LIMIT 5)"):
    print(row)
```

---

## Step 5 — Normalize skaters

Run the normalize_skaters pipeline to link new skater names to existing skater records
and create new skater rows for genuinely new skaters.

Follow the full `/normalize_skaters` skill steps. Pay attention to:
- New skaters that fuzzy-matched an existing skater — verify the match is correct
- New skaters that look like duplicates of existing ones — merge if confirmed same person

---

## Step 6 — Sync to Supabase

Run `/sync_to_supabase` to push the new event(s) to Supabase.

This automatically detects events not yet in `.supabase_sync_log.json` and syncs them.

---

## Checklist

- [ ] Step 1: PDFs downloaded
- [ ] Step 2: Parsed without errors (check for `unparseable` events)
- [ ] Step 3: Clubs linked
- [ ] Step 4: No unexpected `TIME_IMPOSSIBLE` flags in new event
- [ ] Step 5: New skaters normalized / merged as needed
- [ ] Step 6: Supabase sync confirmed (`SELECT COUNT(*) FROM results WHERE event_id = <id>`)

---

## Notes

- If an event was already parsed and you need to re-parse it: `parse_and_load.py --reparse --season <season>`
- If parse_and_load fails on a specific PDF, check `events.parse_errors` in the DB for details
- The USS website sometimes posts preliminary results before final — re-run with `--reparse` when finals are posted
