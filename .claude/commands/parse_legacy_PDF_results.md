# Parse Legacy PDF Results

Parse a non-Tempus legacy format PDF into `data/skater_tracker_round2.db`.
These are events from local clubs using custom results software (not USS protocol / Tempus).

## Usage
```
/parse_legacy_PDF_results <pdf-filename>
```

---

## Known legacy formats

### Gateway / St. Louis Silver Skates format

**Script:** `scripts/parse_stl_silver_skates.py`  
**Parser:** `parse_stl_silver_skates`  
**Known events:**

| Event | id | PDF |
|---|---|---|
| 2022 St. Louis Silver Skates ST Championships | 82 | `data/pdfs/2021-2022/2022_St_Louis_Silver_Skates_combined.pdf` |
| 2021 Gateway Championships | 65 | `data/pdfs/2020-2021/Gateway_CH_complete.pdf` |
| 2019 Buffalo Championships & Heartland #1 | 41 | `data/pdfs/2019-2020/2019_Buffalo_Championships_complete.pdf` |
| 24th Capital City Championships | 47 | `data/pdfs/2019-2020/2019_Capital_City_Champs_complete.pdf` |
| 2019 Desert Classic Short Track | 38 | `data/pdfs/2019-2020/2019_DesertClassic_ST_Protocol_complete.pdf` |
| 2019 Franklin Park Barrel Buster | 44 | `data/pdfs/2019-2020/2019_Franklin_Park_Barrel_Buster_ProtocolOverall.pdf` |

**Format characteristics (all variants):**
- Custom results software (not Tempus) — used by Gateway and Buffalo clubs
- 3 sections: Overall Classification → Results (Events) → Time Classification
- No club/affiliation info
- Event headers: `Event N - Division [X] Distance meters Round` (Gateway uses "X" separator, Buffalo does not)
- Sub-table headers: `No. Final A Time Points` / `No. Heat N of M Time Qual`
- Division names: candy-branded ("Hersheys Mixed"), big-cat ("Cheetahs Mixed"), or team-based ("Heartland F")
- Buffalo variant: gender markers ("m"/"f") and seeding integers appended to skater names — stripped automatically
- Buffalo variant: TC division names use full names ("Bandits Ladies") while result headers use abbreviations ("Bandits F") → 48 SCHEDULE_MISSING warnings expected, not an error
- Page headers/footers garbled into some data lines at page boundaries — a few rows silently skipped per event

**Overall Classification column formats (two variants):**

| Variant | Column header | Per-distance values | Stored as |
|---|---|---|---|
| Rankings (Desert Classic) | `Rank Number Name <dist…> Final Points CDC` | Final-round rank (1 = 1st, lower = better) | `rank` |
| Points (all others) | `No. Name <dist…> [M/F] [Results] Points` | Points earned (1000 = 1st, higher = better) | `points` |

Detection: `CDC` present in column header → rankings format; absent → points format.

**What the parser writes to `classification`:**

| `section_type` | Source | Content |
|---|---|---|
| `overall` | Sec 1 — overall row | Overall rank, total points, CDC (→ `cdr`) |
| `overall_dist` | Sec 1 — per-distance columns | One row per skater per distance; `rank` (rankings format) or `points` (points format) |
| `distance` | Sec 3 — Time Classification | Best time per skater per distance; ranked by best time (NOT final placement) |

Use `overall_dist` for final-round-based distance rankings. Use `distance` for best-time rankings only.

---

## Step 1 — Find the event in the DB

```sql
SELECT id, event_name, pdf_format, parsed_at, local_path
FROM events WHERE local_path LIKE '%<filename>%'
```

- If `parsed_at` is set → ask whether to re-parse (`--force` flag supported)

---

## Step 2 — Identify the format

Examine the PDF to determine which parser to use:

```python
import pdfplumber
with pdfplumber.open('data/pdfs/.../file.pdf') as pdf:
    for i, page in enumerate(pdf.pages[:5], 1):
        print(f'=== PAGE {i} ===')
        print(page.extract_text()[:500])
```

| Format signal | Parser |
|---|---|
| `Event N - Division X Distance meters Round` + candy division names | `parse_stl_silver_skates.py` |
| (future formats) | (document here as new parsers are built) |

---

## Step 3 — Run the parser

### Gateway / St. Louis Silver Skates format

```bash
source .venv/bin/activate && python3 scripts/parse_stl_silver_skates.py \
  --pdf-path "data/pdfs/<season>/<file>.pdf" [--force]
```

Expected output:
- **Overall classification**: N rows (one per skater per division) — `section_type='overall'`
- **Overall dist classification**: N × D rows (one per skater per distance column) — `section_type='overall_dist'`
- **Result rows**: N rows (all heats + finals across all events)
- **Time classification**: N rows (best time per skater per distance) — `section_type='distance'`
- No SkaterEntry rows written (no club info in format)

---

## Step 4 — Run QC

```bash
source .venv/bin/activate && python3 scripts/check_data_quality_v2.py --event-id <id>
```

**Expected for this format:**
- `BIB_CONSISTENCY: no skater_entries` (INFO) — no club info, so no skater entries written; not an error
- No `TIME_IMPOSSIBLE` expected (times are beginner-level, not WR-range)
- No `SCHEDULE_MISSING` expected (classification and results use same normalized division names)

---

## Step 5 — Update docs/parsing.md

Add a row to the appropriate season table:

```
| <Event name> | <id> | `parse_stl_silver_skates.py` | ✅ Gateway legacy format; <N> overall class rows, <M> results, <K> time class rows; <any notes> |
```

---

## Parser internals (for debugging)

`scripts/parse_stl_silver_skates.py` processes text in 3 phases:

1. **Strip headers/footers**: removes page title, pagination, datetime footer, "Competition results" labels
2. **Split into 3 sections**: sec1 ends at first `Event N - ... X ... meters`; sec3 begins at first `No. N Meters Best Time`
3. **Parse each section** with a state machine:
   - Sec1: division headers → column headers (skip) → rank/bib/name/scores rows
   - Sec2: event headers (via `re.search` to handle garbled prefixes) → sub-table headers → result rows
   - Sec3: distance sub-headers → rank/bib/name/time rows

**Division name normalization**: event headers say "Hersheys" but classification says "Hersheys Mixed" — the parser appends " Mixed" to all sec2 division names automatically.

**Desert Classic division header quirks**: Overall Classification uses `"Overall Classification- Open A Men"` format — the `"Overall Classification- "` prefix is stripped automatically. Some division names span two lines (e.g. `"Future Olympians"` / `"Mixed"`) — the parser detects single-word gender suffix lines and appends them to the previous division.

**Garbled rows**: pdfplumber merges "Competition results" text with first data row of some events at page boundaries. These 4 rows are silently skipped. Affected skaters appear in other events so they remain in the DB.

**Classification-only PDFs** (no results section, no Time Classification): all lines go to sec1; sec2 and sec3 are empty. `Result rows` and `Time classification` counts will be 0 — expected, not an error. QC will show `NO_CLASSIFICATION` (INFO) and `BIB_CONSISTENCY: no skater_entries` (INFO) only.

**Long skater names that wrap across lines in the PDF**: pdfplumber emits the overflow as a separate line. This gets misread as a division header and resets `cur_dist_cols`, so all subsequent skaters in that division lose their `overall_dist` rows. **After parsing, always verify**: for each division, `overall_dist` row count should be close to `skater_count × distance_column_count`. Shortfalls in a division (while other divisions look correct) indicate a name-wrap corruption. Fix:
1. Identify the wrapped name from the raw PDF text
2. Run a targeted `UPDATE classification SET skater_name = '<full name>' WHERE event_id=N AND bib='<bib>'` for all section types
3. Run `UPDATE classification SET division = '<correct division>' WHERE event_id=N AND division='<wrapped fragment>'`
4. Manually `INSERT` the missing `overall_dist` rows for the affected skaters using the point values read from the PDF

**Verification query** — check `overall_dist` row counts against expected (skaters × distances):
```python
from sqlalchemy import func
db.query(Classification.division, func.count()).filter(
    Classification.event_id==N, Classification.section_type=='overall_dist'
).group_by(Classification.division).all()
# Compare against: overall rows per division × number of distance columns in that division's header
```
