# Parse MeetDirector / Meet Management Software PDF Results

Parse a PDF produced by **MeetDirector (MeetManagement Software)** into `data/skater_tracker_round2.db`.
These are events using custom meet-management software distinct from Tempus and the Gateway legacy format.

## Usage
```
/parse_Meet_Management_software_formats <pdf-filename>
```

---

## Known events

| Event | id | PDF |
|---|---|---|
| 2018 Buffalo Championships & Heartland #1 | 4 | `data/pdfs/2018-2019/2018_BuffaloCH_complete.pdf` |
| 2019 Empire State Games Short Track | 26 | `data/pdfs/2018-2019/2019_Empire_State_Games_ST.pdf` |
| 2020 Empire State Games Short Track | 53 | `data/pdfs/2019-2020/2020_EmpireST_complete.pdf` |

---

## Format characteristics

**Script:** `scripts/parse_buffalo_ch.py`

- Page title: competition name on every page (detected dynamically from page 1 line 1)
- Footer: `Printed by MeetDirector(TM) Meet Management Software MM/DD/YYYY HH:MM pm` — stripped automatically
- **Three sections** detected by sub-header on each page:
  - `Class Standings` → Overall Classification
  - `Place No. Name Assn. Time Points` → Results
  - `Place No. Name Assn. Time` (no Points) → Time Classification
- Name format: `LastName [M|F,] FirstName [AgeCode]` — reconstructed as `FirstName LastName`
  - Gender code `M,` or `F,` appears between last name and comma (e.g. `Ventura M, Milo`)
  - Or at end of last name token with comma attached (e.g. `Gonzales,`)
  - Age/division code may follow first name (e.g. `Magnus, Mary FM1`) — stripped from name
  - **Space-before-comma variant**: `LastName , FirstName` (standalone `,` token) — handled
- Time format: `H:MM.mmm` — trailing `M` marker stripped (appears on same line or as standalone line before data row in TC)
- Status codes: `DNS`, `DNS+`, `DNF`, `DNF+`, `DQ`, `DQ+` — the `+` means the skater had advanced from a heat then withdrew/was disqualified
- Points: floats (e.g. `1000.00`, `53.5`) — scale varies by event

---

## Section details

### Overall Classification (`Class Standings`)

**Column header:** `Pl. No. SkaterName <dist1> <dist2> … Total`

**Two format variants:**

| Variant | Club line | Example events |
|---|---|---|
| Two-line (with club) | Club name on the line immediately after the score line | Buffalo, 2020 Empire State |
| Single-line (no club) | Score line only; next line is already a new data row | 2019 Empire State |

The parser detects which variant by looking ahead: if the line after a data row is followed by a `Pl.` column header, it's a division header (not a club) — this prevents consuming division headers as club names.

**What is written:**
- `Classification(section_type='overall')` — one row per skater per division
- `Classification(section_type='overall_dist')` — one row per skater per distance column
- `SkaterEntry` — one per unique bib (from classification first, then falls back to results for bibs not in classification)

### Results (`Place No. Name Assn. Time Points`)

**Row format:** `rank bib LastName, FirstName [AgeCode] [Club] time M [points]`

- `rank` or status code (`DNS`, `DQ+`, etc.) as first token
- `bib` as second token (integer)
- Club may be absent for some events (2019 Empire State has no club in results)
- Points absent in heat rows; present (floats) in final rows
- Trailing `M` after time is always stripped

**Event header:** `Event N Division Distance [Super] Meters Round`
- `Super` before `Meters` → `SUPER FINAL` round type
- Round types: `Semi-Final Heat N` → `HEATS`, `Final A Final` → `FINAL`, `Super Meters Final A Final` → `SUPER FINAL`

### Time Classification (`Place No. Name Assn. Time`)

**Row format:** `rank LastName, FirstName [AgeCode] [Club] time` — **no bib number**

- Standalone `M` lines before data rows (PDF layout artifact) are skipped
- `-` in place of time = DNS, stored as NULL

---

## Step 1 — Find the event in the DB

```sql
SELECT id, event_name, pdf_format, parsed_at, local_path
FROM events WHERE local_path LIKE '%<filename>%'
```

- If `parsed_at` is set → ask whether to re-parse (`--force` flag supported)

---

## Step 2 — Confirm it's MeetDirector format

Examine the first page:

```python
import pdfplumber
with pdfplumber.open('data/pdfs/.../file.pdf') as pdf:
    print(pdf.pages[0].extract_text()[:600])
```

| Signal | Confirms format |
|---|---|
| First line = competition title (no year digit required) | ✓ |
| Second line = `Class Standings` | ✓ |
| Column header contains `Pl. No. SkaterName` | ✓ |
| Results sub-header = `Place No. Name Assn. Time Points` | ✓ |
| Footer = `Printed by MeetDirector(TM)...` | ✓ |
| Name format `LastName, FirstName` (comma-separated) | ✓ |

---

## Step 3 — Run the parser

```bash
source .venv/bin/activate && python3 scripts/parse_buffalo_ch.py \
  --pdf-path "data/pdfs/<season>/<file>.pdf" [--force]
```

Expected output:
- **Overall classification**: N rows (one per skater per division)
- **Overall dist classification**: N × D rows (one per skater per distance column)
- **Result rows**: N rows (heats + finals across all events)
- **Time classification**: N rows (0 if no TC section in PDF)
- **Skater entries**: N (from classification + results fallback)

---

## Step 4 — Run QC

```bash
source .venv/bin/activate && python3 scripts/check_data_quality_v2.py --event-id <id>
```

**Expected for this format:**
- `BIB_CONSISTENCY: no skater_entries` — will NOT appear (entries are written)
- `NO_CLASSIFICATION` (INFO) — if no TC section in PDF (e.g. 2019 Empire State)
- `BIB_NOT_IN_SKATER_LIST` — only if result bibs have no entry at all (rare; bibs in results but not classification get entry from first result row)
- No `TIME_IMPOSSIBLE` expected (times are reasonable for recreational skaters)

---

## Step 5 — Update docs/parsing.md

Add a row to the appropriate season table:

```
| <Event name> | <id> | `parse_buffalo_ch.py` | ✅ MeetDirector format; <N> overall class rows, <M> overall_dist rows, <K> results, <J> time class rows; <divisions>; <any notes> |
```

---

## Parser internals (for debugging)

`scripts/parse_buffalo_ch.py` processes text in 3 phases:

1. **Section detection**: each PDF page is classified by its sub-header line (`Class Standings` / `Place No. Name Assn. Time Points` / `Place No. Name Assn. Time`)
2. **Dynamic page title**: `PAGE_HDR_RE` is built from the first non-empty line of page 1 — strips the competition title from all subsequent lines
3. **Parsing each section** with a sequential line parser:
   - Classification: division headers → column headers → data rows (with lookahead to distinguish division headers from club lines)
   - Results: event headers → data rows (time scanned right-to-left)
   - TC: event headers → data rows (no bib; standalone `M` lines skipped)

**Name parsing (`_split_name_club`)**: handles three comma variants:
- `LastName, FirstName` — comma at end of last name token
- `LastName M, FirstName` — gender code `M`/`F` before comma as separate token
- `LastName , FirstName` — standalone comma token (space before comma in PDF)

**Age/division codes stripped from names**: `FM1`, `MM`, `SL`, `F`, `M`, `60+`, etc. (pattern: 1–3 uppercase letters + optional digits/`+`)

**Known truncation issue**: some PDF columns are too narrow; first names may be truncated (e.g. `Xavier` → `X`). This is a source data issue, not a parser bug.

**Lookahead for division vs club**: when a pending skater row is present, the parser peeks at `clean[idx+1]`. If the next non-empty line starts with `Pl.`, the current line is a division header (not a club) and the pending row is flushed without a club.
