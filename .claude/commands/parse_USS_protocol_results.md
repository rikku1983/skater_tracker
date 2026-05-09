# Parse USS Protocol Results

Parse any USS protocol format PDF (Tempus Competition Software) into `data/skater_tracker_round2.db`.
Full reference: `docs/parsing.md`.

## Usage
```
/parse_USS_protocol_results <pdf-filename>
```

---

## Alternative: Web scraping from shorttracklive.info

Some events are published online at **shorttracklive.info**. This is significantly easier than
parsing a PDF and produces cleaner data (explicit race numbers, full names, clean club codes).

**When to prefer web scraping:**
- The PDF is in `tempus_results` format (the hardest format — two-column interleaved layout)
- The results are confirmed available on shorttracklive.info
- The event's `pdf_format` field is `tempus_results` and the PDF covers a single-day event

**How to scrape:**

1. Find the competition on shorttracklive.info. The URL structure is:
   `index.php?saison=<SAISON>&comp=<COMP>&m=6`
   where `saison` and `comp` are integer IDs.

2. Verify the division list at `m=6` matches the DB event.

3. Run the scraper:
   ```bash
   source .venv/bin/activate && python3 scripts/scrape_shorttracklive.py \
     --event-id <DB_EVENT_ID> --comp <COMP> --saison <SAISON>
   ```
   Add `--force` to re-scrape a previously parsed event.

4. The scraper writes SkaterEntry and Result rows. It does NOT write Classification rows
   (the website has no distance classification section — `NO_CLASSIFICATION` in QC is expected).

**Known competition IDs:**
| Event | comp | saison | notes |
|---|---|---|---|
| 2025 Buffalo ST Championships & Heartland #1 | 1004 | 20 | 14 divisions, event_id=184 |

**Data quirks handled by the scraper:**
- Names include birth year suffix (e.g. "ZHANG Aiden 2014") — stripped automatically
- Non-breaking space (`\xa0`) in names — normalized to regular space
- Qual codes FA/FB/FC/FD (advancement to Final A/B/C/D) — stored in `status` field, recognized by QC
- "no finish" annotation → mapped to `DNF`
- `dist=79` ("7. Laps") = 777m, `dist=70` ("4. Laps") = 340m (85m track × 4 laps)

**After scraping:** Run QC (`check_data_quality_v2.py`) as usual. Expect `NO_CLASSIFICATION` info
and no errors if the scraper worked correctly.

---

## Step 1 — Find the event in the DB

```sql
SELECT id, event_name, pdf_format, parsed_at, local_path
FROM events WHERE local_path LIKE '%<filename>%'
```

- If not found → tell the user.
- If `parsed_at` is set → ask whether to re-parse (`--force` flag supported on all parsers).
- If `track_type = 'long'` or `'inline'` → skip; this is not a short-track event.

---

## Step 2 — Scan the page structure

Run this to classify every page:

```python
import pdfplumber
from scripts.parse_all_races_format import _page_type

with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages, 1):
        text = (page.extract_text() or '').strip()
        ptype = _page_type(text)
        # print non-results pages + sample first/last results page
```

Report the section layout: skater_list / overall / distance / skip / results / other pages, with page ranges.

**Flag these conditions immediately:**
- Pages with `(cid:` in text → **font-garbled PDF** → must use OCR
- `len(text) < 50` on data pages → **image-based PDF** → must use OCR
- Results pages show `'RESULTS'` but NOT `'All Races'` → **per-page title format** (Variant B)
- Results pages detected as `'other'` → likely per-page title or garbled font

---

## Step 3 — Choose the correct parser

| Condition | Parser |
|---|---|
| Results have `'All Races'` in text | `parse_all_races_format.py` (Variant A) |
| Results have `'RESULTS'` but not `'All Races'` | `parse_ohio_heartland.py` (Variant B) |
| OFC-style headers (`Division's Distance Round`, no `Event #N`) | `parse_ocr_all_races.py` (Variant C) |
| Font-garbled (`(cid:` sequences) | `parse_ocr_all_races.py` (auto-detects garbling, forces OCR) |
| Image-based pages | `parse_ocr_all_races.py` (auto-detects via text length) |
| STDC format (3-group Q/S/F distance classification) | `parse_stdc.py` |
| No results section (classification only) | Any matching parser; result rows will be 0 |
| Multi-competition PDF | Run primary parser first; append secondary section separately |
| Long-track distances (400/600/800/1200/1600m) | Delete data, set `track_type='long'`, stop |

**Variant B detection:** OCR or check a results page — if line 2 is `"Division's Distance Round"` and line 3 is `"RESULTS"`, it's Variant B.

---

## Step 4 — Run the parser

```bash
source .venv/bin/activate && python3 scripts/<parser>.py --pdf-path "data/pdfs/<season>/<file>.pdf"
```

All parsers support `--pdf-path`. `parse_stdc.py` and `parse_ohio_heartland.py` also support `--force`.

Report counts: skater list / overall classification / distance classification / result rows.

**`parse_all_races_format.py` and `parse_ohio_heartland.py` automatically run `fill_missing_race_context.py`** after the parse pass. It identifies heats from `"Group A/B Picking"` headers (which have no division/distance in the header) and fills in the correct division and distance via bib matching against the classification. The parser prints how many races were filled and flags any that couldn't be resolved. No manual intervention needed for these heats in most cases.

**`fill_missing_race_context.py` fallback:** When a PDF has no distance classification rows, the fill script automatically falls back to using the `time_classification` table as the bib source — provided TC has already been parsed (Step 6). This covers events like Bay State Championships where only overall classification and TC are present, with no distance classification section.

After parsing, also run the standard duplicate-bib cleanup if `DUPLICATE_BIB_IN_RACE` errors appear:
```sql
DELETE FROM results WHERE event_id=N AND id NOT IN (
    SELECT MIN(id) FROM results WHERE event_id=N GROUP BY race_number, bib
)
```

---

## Step 5 — Run QC

```bash
source .venv/bin/activate && python3 scripts/check_data_quality_v2.py --event-id <id>
```

After printing the report, QC automatically writes a **race schedule `.md`** file next to the PDF (same name, `.md` extension) derived from the distance classification. On re-parse, check if this file exists first — it gives the full division order, distances, and skater counts without needing to re-derive from the PDF.

The QC includes a **`SCHEDULE_MISSING`** check: it reads every `(division, distance_m)` pair from the distance classification section and confirms at least one result row exists for each. A warning here means the parser silently skipped a section — see Step 7 for diagnosis.

`NO_CLASSIFICATION` (INFO) means the PDF has no distance classification section (results-only format) — this is expected and not an error.

**Classification-only PDFs** (US Championships, AGN, Desert Classic, Winter Challenge, etc.) have no results section. `SCHEDULE_MISSING` will fire for every division+distance — this is expected and not an error. The valuable data is in the classification and TC sections.

---

## Step 6 — Parse and cross-check Time Classification

```bash
source .venv/bin/activate && python3 scripts/parse_time_classification.py --event-id <id>
```

If the PDF has no Time Classification section (results-only PDFs), the script writes 0 rows — expected, not an error. Otherwise it prints a summary of divisions and skater counts per distance.

**When the TC section has no "Time Classification" keyword on any page** (e.g. Ohio State, AGN 2023), use `--start-page N` to force-enter TC mode from page N:
```bash
python3 scripts/parse_time_classification.py --event-id <id> --start-page 44
```

**When TC pages are followed by non-TC pages** (classification pages after TC, unusual order), add `--end-page N` to stop before the parser picks up classification headers as division names:
```bash
python3 scripts/parse_time_classification.py --event-id <id> --end-page 23
# or combined:
python3 scripts/parse_time_classification.py --event-id <id> --start-page 44 --end-page 58
```

**When TC is parsed before the main parser** (events with no distance classification and Group A Picking heats), parse TC first, then run the main parser — `fill_missing_race_context` will use TC as bib source automatically.

Then run the cross-check:

```python
from src.db.session_v2 import get_session
from src.db.models_v2 import TimeClassification, Classification, Result
from sqlalchemy import func
from collections import Counter
db = get_session()
EID = <id>

rows = db.query(TimeClassification).filter(TimeClassification.event_id==EID).all()

# 1. Division match
tc_divs = set(r[0] for r in db.query(TimeClassification.division).filter(TimeClassification.event_id==EID).distinct())
cl_divs = set(r[0] for r in db.query(Classification.division).filter(Classification.event_id==EID, Classification.section_type=='distance').distinct())
print('TC only:', tc_divs - cl_divs)
print('Class only:', cl_divs - tc_divs)

# 2. Bib match per division
for div in sorted(tc_divs):
    tc_b = set(r[0] for r in db.query(TimeClassification.bib).filter(TimeClassification.event_id==EID, TimeClassification.division==div).distinct())
    cl_b = set(r[0] for r in db.query(Classification.bib).filter(Classification.event_id==EID, Classification.division==div, Classification.section_type=='distance').distinct())
    if tc_b - cl_b or cl_b - tc_b:
        print(f'{div}: TC_only={tc_b-cl_b} Class_only={cl_b-tc_b}')

# 3. Time match: TC best time vs best race result (matched by bib)
for tc in rows:
    if tc.best_time_seconds is None: continue
    best = db.query(func.min(Result.time_seconds)).filter(
        Result.event_id==EID, Result.bib==tc.bib,
        Result.distance_m==tc.distance_m, Result.time_seconds.isnot(None)
    ).scalar()
    if best is None:
        print(f'NO RESULT: bib={tc.bib} {tc.skater_name} {tc.division} {tc.distance_m}m tc={tc.best_time_text}')
    elif abs(best - tc.best_time_seconds) > 0.011:
        print(f'MISMATCH: bib={tc.bib} {tc.skater_name} {tc.division} {tc.distance_m}m TC={tc.best_time_text} result={best:.3f} diff={best-tc.best_time_seconds:+.3f}')
db.close()
```

**Interpreting results:**

| Finding | Meaning |
|---|---|
| `NO RESULT` (TC has time, result missing) | That division+distance section is absent from PDF results — either a parse failure (re-parse) or genuinely missing from PDF |
| `MISMATCH` with **positive diff** (result slower than TC) | Finals or heats missing — TC records the best time across all rounds including missing ones |
| `MISMATCH` with **negative diff** (result faster than TC) | Multi-round/multi-day event where TC records first-round times only — structural, not a bug |
| Bib in Class but not TC | DNS skater — no time recorded, expected |
| Spurious TC division (e.g. `"(Tempus) 500 extra"`) | Parse artifact from an unusual page element — delete those rows manually |
| 0 TC rows written | PDF has no Time Classification section — skip this check |

**Positive-diff NO RESULT / MISMATCH diagnosis:** check whether the missing races exist in the PDF by scanning for the expected race numbers. If found, re-parse with `--force`. If absent from PDF entirely, the PDF is incomplete — document as unfixable gap. Note that `SCHEDULE_MISSING` in QC only catches cases where *classification* has rows but results don't; the TC cross-check additionally catches cases where classification is also absent (i.e., both classification and results pages are missing).

**Classification-only PDFs (no results):** Compare TC times against the distance classification `best_time_seconds` instead of result rows. Replace the `Result.time_seconds` query with a `Classification.best_time_seconds` query, filtering by `section_type='distance'`. Expect `NO CLASS BEST` for any classification page with a non-standard column layout that didn't extract best times (e.g. US Championships format with `MCD AGD LQD` columns). Exact matches confirm TC and classification agree.

**Name abbreviation note:** The TC section often uses full names; distance classification sections often abbreviate first names to initial only (`"S. Sun"` → `"Sean Sun"`; `"K. Santos-Griswold"` → `"Kristen Santos-Griswold"`). The affiliation may also be concatenated with the name in classification rows. The cross-check matches by **bib number**, so abbreviations do not affect correctness. When two skaters share the same initial+last name (e.g. two skaters both showing as `"F. Chen"`), only bib distinguishes them — bib is always the canonical key.

**Name storage:** Names are stored **exactly as they appear in each PDF section** — no normalization. Distance classification rows may have abbreviated names; TC rows typically have full names. This is by design (raw-first approach).

---

## Step 7 — Diagnose and fix QC errors

### `SCHEDULE_MISSING` (classification division/distance has no results)
Build the expected race schedule from the distance classification, then find the missing pages in the PDF:
1. Query `SELECT division, distance_m FROM classification WHERE event_id=N AND section_type='distance' ORDER BY rowid` — the row order matches the order distances were raced
2. Using the race-number format XXNN (XX=event number, NN=race within event), map each missing division+distance to the expected event numbers
3. Confirm with bib cross-check: skaters in the missing result pages should match the classification bibs for that division+distance
4. Either re-parse with `--force` (if the root cause is a parser bug), or insert missing rows manually using the race-number inference approach
5. See "Missing races (heats with no section context)" in Step 7 for the Group A Picking case

### `TIME_IMPOSSIBLE`
Check the PDF directly. Options:
- Source data error in PDF (e.g. `0:23.780` for 1500m) → manual `UPDATE results SET time_text=..., time_seconds=... WHERE id=...`
- Wrong distance context → check section headers; may need race-number mapping fix
- Long-track event → set `track_type='long'`, remove data

### `DISTANCE_UNKNOWN` (unknown distance value)
Check the PDF. If the distance is a real local-track value, add it to `KNOWN_DISTANCES` in `check_data_quality_v2.py`. Known non-standard distances: 425, 435, 440, 595, 611, 700m.

### `NAME_INVALID_CHARS` (digit/special char in name)
Common causes:
- Group code leaked in (e.g. `G2 John Smith`) → check OCR row; fix with targeted `UPDATE`
- Skater list `Status` column not stripped → `STATUS_FIRST_RE` should handle `[A-Z]\d{1,2}` and `Master`
- OCR misread (e.g. `A2 Tiffany Zhang` from OCR artifact) → fix with `UPDATE results SET skater_name=... WHERE skater_name=...`

### `DIVISION_MISMATCH`
Often a false positive when skater list encodes division+club as one string (e.g. `"Heartland Men A Northbrook"`). QC uses `startswith` matching — check if it's genuinely wrong or just a naming variation.

### `DUPLICATE_BIB_IN_RACE` (same page)
Section header printed twice on same page (OCR artifact or PDF layout). Fix:
```sql
DELETE FROM results WHERE event_id=N AND race_number=R AND bib=B AND id != (
    SELECT MIN(id) FROM results WHERE event_id=N AND race_number=R AND bib=B
)
```
Or: deduplicate all at once keeping `MIN(id)` per `(race_number, bib)`.

### `DUPLICATE_BIB_IN_RACE_MULTIDAY` (different pages)
Expected for multi-competition PDFs or races that span two pages. Not an error.

### `CLASS_NO_DIVISION` / `DISTANCE_UNKNOWN_CLASS`
Distance classification header not parsed. Usually: competition title on line 1 pushes header to line 2. Already handled by `_parse_distance_page` multi-line scan. If still failing, check OCR apostrophe — may need `[''‘’!]` character class.

### Missing races (heats with no section context)
`"Group A/B Picking N+(M) Event #0"` headers carry no division or distance. `parse_all_races_format.py` now handles these automatically via a 2-pass approach:

1. **Parse pass**: heat rows under a Group A/B Picking header are stored with `division=NULL`, `distance_m=NULL` (not dropped).
2. **Fill pass** (`scripts/fill_missing_race_context.py`): runs automatically after parsing. For each NULL-division race:
   - Identifies division via bib overlap against classification (≥2 matching bibs, no tie).
   - Identifies distance via fastest-time ratio: `fastest_race / class_best` per distance — picks the distance with ratio closest to 1.0 (and ≥ 1.0).
   - Updates the rows in-place; reports any races that couldn't be resolved.

If any races remain unresolved (overlap < 2 bibs, or time ratio ambiguous), the parser prints them as "Items for review". Fix single-bib cases manually by checking which division that bib belongs to in the classification.

Run standalone on an already-parsed event:
```bash
source .venv/bin/activate && python3 scripts/fill_missing_race_context.py --event-id N --verbose
```

**If `SCHEDULE_MISSING` persists after fill:** the rows are absent from the PDF entirely (skater withdrew after classification, or the PDF is incomplete). Document as unfixable gap in `docs/parsing.md`.

---

## Step 8 — Watch for these format-specific issues

**Image-based PDFs and font-garbled PDFs (`(cid:`):**
- OCR takes ~2 min per 100 pages
- `parse_ocr_all_races.py` auto-detects image pages (`len < 50` after text extraction) and falls back to OCR
- Results pages detected by presence of `Race #N` in OCR text (bold "RESULTS"/"All Races" headers may not OCR at confident threshold)
- "Group A Picking" header may OCR as "GroupA Picking" (no space) — parser handles both
- Single-distance divisions get distance assigned directly without needing classification best times
- For multi-distance divisions with no class best times: assign based on time range thresholds (see January Thaw 2024 example in parsing.md)
- OCR artifacts common: `=` prefix on names, rank/bib column confusion, dropped `1:` from times (e.g., `1:57.37` → `57.37`) → expect some TIME_IMPOSSIBLE and NAME_INVALID_CHARS in QC
- Curly apostrophes (U+2019) in section headers and distance page titles → already handled in parsers
- `!` as OCR artifact for apostrophe → also handled
- Group A/B Picking heats are auto-filled by `fill_missing_race_context.py` — no manual race-number mapping needed

**Multi-competition PDFs:**
- All-races parser handles both sections' classification automatically
- If secondary competition uses Variant B, append results using `parse_ohio_heartland._parse_results_page` directly without deleting existing data
- Race number collision between competitions → `DUPLICATE_BIB_IN_RACE_MULTIDAY` warning is expected

**`parse_ohio_heartland.py` (Variant B) specifics:**
- Section header is usually on line 2 of each results page, but some PDFs prepend a timestamp and competition title, pushing it to line 4 — the parser scans lines 1–4 automatically
- "Group A Picking" headers (no division/distance) are handled the same as Variant A: rows stored with `division=NULL`, filled by `fill_missing_race_context` after parse
- Calls `fill_missing_race_context` automatically after parse (same as Variant A)
- If TC is parsed before results (needed when fill relies on TC as bib source), TC must be in DB before running the main parser

**Classification-only PDFs** (US Championships, AGN, Winter Challenge, STDC, etc.):
- Sections present: overall classification + distance classification + TC. No results.
- `SCHEDULE_MISSING` fires for every classification combo — expected, not an error
- TC cross-check compares against `Classification.best_time_seconds` (distance section), not results
- Non-standard column layouts (e.g. `MCD AGD LQD LBPD BFTD` columns in US Championships format) may not extract `best_time_seconds` → `NO CLASS BEST` in TC cross-check is expected
- Unusual section ordering (e.g. AGN: distance classification THEN overall per division; US Winter Challenge: TC then classification) — `_page_type` detects correctly regardless of order; only matters for TC parser `--start-page`/`--end-page`

**Results-only PDFs:**
- No skater list, no classification — result rows only
- `BIB_CONSISTENCY` info check will note no skater entries — expected

**No skater list:**
- Common for some event formats (Michigan State, Ohio State, time-classification-only PDFs)
- QC will show `BIB_CONSISTENCY: no skater_entries` info — expected, not an error

**Non-standard distances:**
- Verify in PDF before adding to `KNOWN_DISTANCES`
- Non-short-track distances (400/600/800m) = likely long-track event → remove data

**Round structure — more than 2 rounds:**
Most events have 2 rounds per distance (heat + final, or semi-final + final). In larger events there can be 3+ rounds: quarter-final → semi-final → final, or heat → semi-final → final.
- The `round_type` normalization (HEATS / FINAL / SUPER FINAL) is intentionally coarse: quarter-finals, semi-finals, and heats all map to HEATS because in a 2-round event they're equivalent.
- In a 3-round event, quarter-finals and semi-finals are both HEATS in the DB — that's correct and expected. The full label ("Quarter-Final", "Semi-Final") is preserved in `round_label` for distinction if needed.
- Do NOT try to distinguish quarter-final from semi-final in `round_type`; the coarse grouping is by design.

---

## Step 9 — After a clean QC, update `docs/parsing.md`

Add a row to the **Round 2 Parsing Status** table:

```
| <Event name> | <id> | `<parser_script>` | ✅ [notes if any] |
```

Notable notes to record: non-standard distances added, manual corrections, multi-competition, OCR used, missing heats fixed via mapping.

---

## Quick reference: parser decision tree

```
Has '(cid:' garbling or image pages?
  YES → parse_ocr_all_races.py
  NO  → Has 'All Races' in results pages?
          YES → parse_all_races_format.py
          NO  → Results have 'RESULTS' without 'All Races'?
                  YES → parse_ohio_heartland.py
                  NO  → STDC format (Q/S/F columns)?
                          YES → parse_stdc.py
                          NO  → check docs/parsing.md
```

## Quick reference: TC parser flags

| Situation | Command |
|---|---|
| Normal PDF (TC has "Time Classification" header) | `parse_time_classification.py --event-id N` |
| No TC keyword on any page (e.g. Ohio State, AGN 2023) | `--start-page N` |
| TC followed by non-TC pages (unusual order) | `--end-page N` |
| Both — TC buried in middle with no keyword | `--start-page N --end-page M` |
| Re-parse, existing TC rows already present | `--force` |
| Event with no dist class; fill needs TC as bib source | Parse TC **first**, then run main parser |

## Quick reference: TC cross-check mismatch patterns

| Pattern | Cause | Action |
|---|---|---|
| Positive diff (result > TC) | Missing finals/heats — TC has faster time from a round not in results | Check PDF for those race numbers; re-parse or document as missing |
| Negative diff (result < TC) | Multi-round event — TC records first-round times, later rounds are faster | Structural, not a bug; no action needed |
| NO RESULT | Section absent from PDF results entirely | Verify in PDF; if absent, document as incomplete PDF |
| Bib in Class not TC | DNS skater with no recorded time | Expected; no action |
| Bib in TC not Class | Skater raced but was excluded from classification (e.g. DNS in another distance) | Expected; no action |
| Spurious division name | Page header or footer misread as division | Delete those TC rows manually |
| 0 TC rows | PDF has no Time Classification section | Skip check |

**Name matching:** TC cross-check uses bib numbers, not names. TC sections commonly abbreviate first names (`S. Sun` → `Sean Sun`). This does not affect bib-based matching but will appear when reading TC output manually.
