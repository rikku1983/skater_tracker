# Sync New Events to Supabase

Push unsynced events from local SQLite to Supabase via MCP. No DATABASE_URL or psycopg2 needed — uses `mcp__supabase__execute_sql` directly.

## Usage

```
/sync_to_supabase
/sync_to_supabase --event-id 201 202
```

No arguments → syncs all events not yet recorded in `.supabase_sync_log.json`.  
`--event-id` → syncs specific events regardless of sync log.

---

## Step 1 — Generate SQL

Run from the project root:

```bash
python3 scripts/export_event_sql.py --all-new
```

Or for specific events:

```bash
python3 scripts/export_event_sql.py --event-id <id1> [<id2> ...]
```

**If stdout begins with `# No new events to sync`** → report this to the user and stop.

The output format is:
```
-- SYNC: event_ids=[201, 202]
-- CHUNKS: 12
-- CHUNK 1/12
INSERT INTO clubs ...
...
-- CHUNK_BREAK
-- CHUNK 2/12
...
```

Parse the event IDs from the `-- SYNC:` header line — you will need them in Step 3.

---

## Step 2 — Execute SQL via MCP

Split the output on `-- CHUNK_BREAK`. For each chunk **in order**:

1. Call `mcp__supabase__execute_sql` with the chunk SQL string
2. If the call succeeds → proceed to the next chunk
3. If any chunk fails → **stop immediately**, report the error and chunk number to the user, and **do not** update the sync log

Chunks are ordered: clubs → skaters → events → results → skater_entries → classification → time_classification. Do not reorder.

---

## Step 3 — Update sync log

Only after **all chunks succeed**, mark the events as synced:

```bash
python3 scripts/export_event_sql.py --mark-synced <id1> [<id2> ...]
```

Use the event IDs from the `-- SYNC:` header line.

---

## Step 4 — Verify

Run a spot-check via MCP:

```sql
SELECT id, event_name, season FROM events ORDER BY id DESC LIMIT 5;
```

And confirm result counts for the most recently synced event:

```sql
SELECT COUNT(*) FROM results WHERE event_id = <most_recent_id>;
```

---

## Error recovery

All inserts use `ON CONFLICT (id) DO NOTHING` — fully idempotent. If a sync fails mid-way, re-run from Step 1. Already-inserted rows will silently no-op and the remaining rows will land correctly.

Never manually edit `.supabase_sync_log.json` to mark events synced unless you have confirmed those events are fully present in Supabase.

## Notes

- A large event (1,000+ results) may produce 10–30 chunks — this is normal
- `--event-id` bypasses the sync log — useful for re-syncing a specific event after a data fix
- The sync log is at `.supabase_sync_log.json` in the project root; it is gitignored (add it if you want it tracked)
