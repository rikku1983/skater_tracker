# Skater Tracker — Project Notes for Claude

## Domain Reference

Full domain knowledge is in `docs/`:
- `docs/sport_domain.md` — track types, distances, competition structure, status codes, WR floors, clubs
- `docs/pdf_formats.md` — PDF section structure, format variants, `tempus_results` column layout, known parser failure modes
- `docs/data_quality.md` — time flags, club normalization, deduplication history, known mislabeling issues

**Key facts to always remember:**
- US has two track sizes: 111 m (standard) and 85 m (youth). 85 m distances: 85/170/255/340 m. No 444 m exists.
- "4. Laps" in a PDF = 340 m (85 m track × 4 laps).
- "85m" in a division name (e.g. "Division 9 Saddle 85m") is a class label, NOT the race distance.
- Division names vary by event — never assume anything from the name alone.
- `tempus_results` PDFs use two-column layout; `pdfplumber` interleaves the columns. Always split by x-coordinate before parsing.
- The "0:MM.SSS" prefix fix applied to ~269 results is **unverified** — do not rely on it or extend it.

---

## Track Type Classification (Planned)

Some events in the database may be long track or inline speed skating, not short track.
These need to be identified and labeled so they are excluded from short track comparisons.

### Data model change
Add `track_type` column to `Event`: values `"short"` / `"long"` / `"inline"` / `"unknown"`.

### Classification strategy (in order)

1. **Keyword pass** — check event name:
   - contains "short track" → `short`
   - contains "long track" → `long`
   - contains "inline" → `inline`

2. **Distance profile pass** — check existing race results:
   - any 777m (or 85/170/255/340/111/222/333m) race → `short` (these distances are short track only; note: no 444m distance exists)
   - any 5000m or 10000m race → `long` (long track only)
   - 500/1000/1500m alone is ambiguous — do not decide from these

3. **Anomaly pass** — check `data_flags`:
   - if >50% of results are flagged `TIME_IMPOSSIBLE` → likely long track (flag for manual review, don't auto-assign)

4. **Manual review** — print remaining `"unknown"` events; expect < 10 edge cases

### Web app change
Filter all queries to `track_type = "short"` (or `"unknown"` as fallback) by default.

### LLM batch runner
Skip events where `track_type` is already set to `"long"` or `"inline"`.

### Known non-short-track events (already excluded from parsing)
- 2025 Colombian Championships (long track / inline)
- 2019 Heiden Challenge (long track)

---

## Parser Architecture Refactor (Planned — after LLM batch completes)

Current approach of 7 separate parsers dispatched by format detection is brittle: format drift silently breaks enumerated rules (e.g. lap-count header variants in `tempus_results_parser`).

### Tier 1: Unified Tempus Parser (~85% of all PDFs)

Replace 4 Tempus variants with one parser using **regex scoring** instead of rigid token-position checks:
- A line is a section header if it contains a round keyword (`HEATS`, `FINAL`, `SUPER FINAL`) AND ends with something distance-like (meters or laps in any format)
- Catches all current variants: `"500 m 4.5 lap"`, `"SUPER FINAL 7. Laps"`, `"FINAL B 777 m 7 lap"`
- Use `pdfplumber.extract_table()` where table borders exist; fall back to word-coordinate extraction only otherwise

Files to consolidate → `src/parsers/tempus_unified_parser.py`:
- `src/parsers/tempus_parser.py`
- `src/parsers/tempus_all_races_parser.py`
- `src/parsers/tempus_results_parser.py`
- `src/parsers/tempus_races_parser.py`

### Tier 2: LLM Parsing (DONE)
- `src/parsers/llm_parser.py` — handles legacy, image, and non-standard PDFs
- `scripts/parse_with_llm.py` — batch runner (78 events, run with `--skip-errors`)
- After batch: re-run `scripts/flag_data_quality.py` to label bad times

### Tier 3: Parse-time Validation

Move `data_flags` from post-DB-load (`scripts/flag_data_quality.py`) into `ParsedResult` itself:
- Add `flags: list[str]` to `ParsedResult` in `src/parsers/base.py`
- Validation checks in `_sections_to_results()` and equivalents:
  - `TIME_IMPOSSIBLE`: below WR floor (500m < 36s, 777m < 58s, 1000m < 78s, 1500m < 120s)
  - `TIME_LIKELY_MISLABELED_DIST`: 1500m time < 120s
  - `NAME_SUSPICIOUS`: single token or contains digits
- Propagate to `Result.data_flags` in `src/db/load.py`
- `scripts/flag_data_quality.py` becomes a backfill/re-check tool only

### What to keep
- `pdfplumber`, two-column layout splitting, state machine (section context across pages), `_laps_to_meters()`
- `src/parsers/classic_results_parser.py` (pre-Tempus Heartland/NEST format — keep as-is)

### Suggested order
1. LLM batch + `flag_data_quality.py` finish
2. Track type classification (see above)
3. Tier 3 (parse-time validation) — small, isolated, low risk
4. Tier 1 (unified Tempus parser) — larger refactor, re-parse all Tempus events
5. Rotate the Anthropic API key at console.anthropic.com
