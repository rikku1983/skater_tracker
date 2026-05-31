# USS Protocol Format — Parsing Reference

This document captures everything learned about parsing **USS protocol report PDFs** generated
by **Tempus Competition Software** (also marketed as SpeedSkating Pro). All US short track
events parsed so far use this format.

---

## Format Overview

Every USS protocol PDF contains the same logical sections in the same order:

```
Cover / officials page(s)
Skater List               (may be absent in some events)
Overall Classification    (final division standings)
Distance Classification   (standings per distance)
Time Classification       (skip — not useful for race results)
Results                   (individual heat/final race rows)
```

A second Time Classification section sometimes appears at the end (seen in STDC 2025).

**Classification-only PDFs:** some PDFs contain only the classification section (no results).
Example: `2024_STDC_ProtocolFinal.pdf` is pages 1–46 of a 118-page document — only protocol
and classification, no race results. Parse what is there; result rows will be 0.

---

## Rendering Variants

| Variant | Text extraction | Parser approach |
|---|---|---|
| **Text-based** | `pdfplumber.extract_words()` returns words with x/y coords | Rule-based parsers; use word coordinates for column detection |
| **Image-based** | Pages are rasterized — pdfplumber returns 0 words | OCR with `pytesseract` at 300 dpi; use `image_to_data(--psm 6)` for word bounding boxes |

**Image-based detection:** if `len(page.extract_words()) < threshold` (e.g. < 50) the page is
image-based. Some PDFs mix text pages (cover, officials) with image pages (all data tables).

**OCR word grouping:** `pytesseract.image_to_data()` returns per-word bounding boxes but words
on the same visual line can have y-coordinates differing by ±6 px due to OCR jitter. Group by
`(block_num, par_num, line_num)` and assign all words in a group the minimum `top` of the
group — then the existing `_words_to_rows` (tolerance=3) groups them correctly.

**OCR time artifacts:**
- `"0:44,564"` → `,` as decimal separator → normalize with `re.sub(r',(\d)', r'.\1', text)`
- `"2:35,.260"` → comma + period artifact → `text.replace(',', '')` after comma→period fix
- `"1:55..223"` → double dot → `re.sub(r'\.{2,}', '.', text)`

Apply only to tokens that match `^\d+:\d{2}` (time-like tokens) to avoid corrupting points
values like `"10,000"`.

---

## Page Type Detection

Detect page type from `page.extract_text()` content:

| Check | Type |
|---|---|
| `'Skater List'` or `'SKATER LIST'` in text | `skater_list` |
| `'OVERALL CLASSIFICATION'` in text | `overall` |
| First line == `'Event List'` AND word count ≥ 80 | `overall` (image-title pages) |
| Any of first 3 lines matches `"Division's Distance"` pattern | `distance` |
| `'DISTANCE CLASSIFICATION'` in text | `distance` |
| Any line matches `EVENT_HDR_RE` | `results` |
| `'RESULTS'` in text AND `'All Races'` in text | `results` |
| `'Time Classification'` in text | `skip` |
| Word count < 80 (footer/legend pages) | `skip` |

**Non-standard skater list pages:** some PDFs include a custom registration list with
`'FIRST NAME'` / `'LAST NAME'` column headers (e.g. InterScholastic 2025, STDC 2025 page 3).
Skip these — they use a different format and the standard Tempus skater list appears later.

**Competition title on first line:** some PDFs (e.g. STDC 2024) prepend the competition name
as the first text line on every page, pushing the division/distance header to line 2. Check
the first **three** lines for the `"Division's Distance"` distance pattern, not just line 1.
Apply the same fix to `_parse_distance_page_stdc` — scan lines 1–3 for the match.

---

## Skater List

**Standard Tempus column header:** `# Name Gender Division Club`

**Row format:** `bib | name... | [status] | gender | division... | club...`

**Parsing strategy:**
1. Find the `Gender` keyword token (Male/Female/Mixed) in the row.
2. Everything before Gender = `bib + name [+ status]`.
   - Status tokens recognized: `JR`, `SR`, `Juv`, `Nov`, `Open`, `Master`, `\d{2}+` (e.g. `30+`),
     and single-letter + digit group codes `[A-Z]\d{1,2}` (e.g. `G1`, `G2`, `F1`, `E2`).
   - Group codes like G1/G2/F1 are seeding group assignments used by some clubs (Puget Sound).
     They appear in the Status column between Name and Gender.
   - `Master` signals a master skater; the following `\d{2}+` age bracket (e.g. `30+`) is a
     secondary status token captured alongside it.
3. Everything after Gender = `division + club`.
   - **Default:** use `_largest_gap_split` on post-gender words — splits at the largest x-gap.
   - **Failure mode:** when division has multiple words (e.g. "Combined C") and the club
     starts at a column position close to the last division word, the gap between division
     words can exceed the division/club gap. This misclassifies the first club word into the
     division.
   - **Fix:** find the x0 of `'Club'` in the header row and use `club_x0 - 5px` as the split
     threshold. Words with `x0 < threshold` → division; `x0 >= threshold` → club. (5px tolerance
     because data column alignment and header word alignment can differ by 1–2 px.)
   - **Multi-club combined PDFs:** some events (e.g. Great Lakes & Heartland) encode both
     division and club together in the division field: `"Heartland Men A Northbrook"`. This is
     not a parse error — the skater list division name simply includes the club suffix. QC
     division mismatch checks must use `startswith` matching (not exact) to handle this.

---

## Overall Classification

**Column header:** `# Name Affiliation Points CDR BDR Best Time`

**Row format:** `rank | bib | name... | affil | points | CDR | BDR | [time]`

**Parsing (right to left):**
1. Time: last token if it matches `^\d+:\d{2}\.\d+$`
2. BDR: integer
3. CDR: integer
4. Points: float (remove comma thousands separator before parsing)
5. Middle zone = `name... + affil` → split with `_split_name_affil`

**Division name:** found on the line immediately before `'OVERALL CLASSIFICATION'` in the page
text. If the page title is an image, the division will be blank — acceptable for raw storage.

---

## Distance Classification

**Column header variants:**

| Format | Columns |
|---|---|
| Standard (2-group) | `Points # Name Affiliation S F Best` |
| 3-group (STDC, some USS) | `Points # Name Affiliation Q S F Best` |
| 4-group (USS Junior/AGN) | `Points # Name Affiliation H Q S F Best` |

**Row format:** `rank | points | bib | name... | affil | [H] | [Q] | S | F | [time]`

**Parsing (right to left):**
1. Time: last token matching `TIME_RE`
2. F (Final group): token matching `^[A-Z]\d+$` OR matching `RESULT_STATUS_RE`
   (includes `DNS`, `DNF`, `FNT`, `DQ`, `DSQ`, `PEN`, `ADV`, `Q`, `PB`)
3. S (Semi group): token matching `^(\d+|[A-Z]\d+|PEN|DNS|Q)$`
4. Q / H (additional group columns): strip up to 2 more using the same SEMI_GROUP_RE pattern

**Critical:** status codes (`DNF`, `FNT`, `DQ`, `PEN`…) appear in ALL group columns, not just
the result status field. Include all of them in the F-group check (`RESULT_STATUS_RE`) or they
will not be stripped, causing them to leak into the name/affil zone and corrupt the split.

**4-column layout (H Q S F):** the H column is the heat seeding number (a bare integer like
`6`). It is stripped by the same SEMI_GROUP_RE (`^\d+$` branch) in the third strip pass.
Store only F and S group values; H and Q are discarded.

**Division + distance:** parsed from the first matching line among lines 1–3 of page text
(e.g. `"Combined E's 333"`). Use regex: `r"(.+?)'s?\s+(\d+(?:\s+\(#\d+\))?)\s*$"` —
captures division, distance. The `(#2)` qualifier (second race of same distance) is included
in the distance string; strip it with `re.match(r'(\d+)', dist_str)` to get integer distance_m.

---

## Results Section

### Row format

`rank | bib | name... | affil | time | [status]`

Column header (informational, not parsed): `# Name Affiliation Time [Qual.]`

**Parsing (right to left):**
1. Scan all tokens for `TIME_RE` matches → take the last one as `time_txt`
2. Token immediately after time (if it matches `RESULT_STATUS_RE`) → `status`
3. Tokens before the time token:
   - If `texts[0]` and `texts[1]` are both digits → `rank = texts[0]`, `bib = texts[1]`, `mid_words = row[2:ti]`
   - If only `texts[0]` is a digit → `bib = texts[0]`, `rank = None`, `mid_words = row[1:ti]`
4. `_split_name_affil(mid_words)` → `name, status_code, affil`
5. No-time rows (DNS/DNF/FNT at end): check if `texts[-1]` matches `RESULT_STATUS_RE`

### Section header variants

There are three distinct variants for how section context (division / distance / round type)
is communicated:

#### Variant A — Inline section headers (`parse_all_races_format.py`)

Used by: Jan Thaw, GLST, Capital City, Chicago, Franklin Park, Full Throttle, InterScholastic,
Bay State, Great Lakes, Park Ridge, Jeff City, Rock N Roll, NorthBurke, Presidential Cup,
Land of Lincoln, MASTC, Washington State, Badger International, Oval Winter Challenge,
US Junior Championships, US ST Championships, US AGN, USS AGN ST, Robby Kaufman

A section header row appears inline **above** the first heat/final in each group:

```
Group A Challenge C's 333 Semi-Finals Event #1
Heat 1 of 4 Race #301
1  384  Meinuo Li  LARSC  0:40.502
```

Pattern: `EVENT_HDR_RE = r"(?:Group\s+[A-Z]\s+)?(.+?)'s?\s+(\d+(?:\s+\(#\d+\))?)\s+(.+?)\s+Event\s+#(\d+)$"`

- Group 1: division name (strip trailing `'s`)
- Group 2: distance (may include `(#2)` qualifier for second race of same distance)
- Group 3: round label
- Group 4: event number

Section context is **stateful across pages** — the active division/distance/round carries
forward until a new section header appears. Critical for multi-page events.

#### Variant B — Per-page title headers (`parse_ohio_heartland.py`)

Used by: Ohio Invitational & Heartland #3 (2024 and 2025), Michigan State ST Championships,
Ohio State Short Track Meet

The page title itself (lines 1–3) specifies the section context. One round per page.

```
2025 Ohio Invitational and Heartland 3    ← line 1: event title
Heartland's 1000 Semi-Finals              ← line 2: division + distance + round
RESULTS                                   ← line 3: section type
```

Pattern for line 2: `r"(.+?)'s?\s+(\d+(?:\s+\(#\d+\))?)\s+(...)"`

No cross-page state needed — each page is self-contained. Heat/final sub-headers still appear
within the page in the usual format.

**Detection:** result pages contain `'RESULTS'` in text but NOT `'All Races'` — `_page_type`
returns `'other'`. Use a separate `_page_category` function that checks `'RESULTS' in text`.

#### Variant C — OFC/image format (`parse_ocr_all_races.py`)

Used by: OFC Protocol (OCR image-based), Oval Winter Challenge (text-based OFC format)

Section header appears as first data line on each page (no "Event #N" suffix):

```
Group A Challenge C's 333 Semi-Finals
Heat 1 of 4 Race #301
```

Pattern (`SECTION_HDR_RE`):
`r"(?:Group\s+[A-Z]\s+)?(.+?)'s?\s+(\d+(?:\s+\(#\d+\))?)\s+((?:Super\s+Final\s+)?(?:Semi-?)?Finals?|Heats?|Semi-Finals?)"`

`parse_ocr_all_races.py` tries native text extraction first and falls back to OCR only when
fewer than 50 characters are extracted — so it handles both image-based and text-based OFC
PDFs without modification.

### Heat and final sub-headers

These appear within all variants after the section header:

| Pattern | Match |
|---|---|
| `Heat N of M Race #NNN` | `HEAT_HDR_RE` — sets `heat` and `race_number` |
| `X Final Race #NNN` | `FINAL_HDR_RE` — sets `group_label` (A/B/C/D) and `race_number` |

**Important:** heat/final sub-headers do NOT override the `round_type` set by the section
header. The section header's `round_type` persists for all rows until a new section header.
Only the `round_label`, `heat`, `group_label`, and `race_number` fields change.

### round_type normalization

Free-form round strings from section headers are normalized to three canonical values:

| Raw string | `round_type` |
|---|---|
| `Semi-Finals`, `Quarter-Finals`, `Heats` | `HEATS` |
| `Finals`, `Final` | `FINAL` |
| `Super Final`, `Super Final Finals` | `SUPER FINAL` |

Rule: if `'super'` in lowercase string → `SUPER FINAL`; elif `'semi'` or `'heat'` → `HEATS`;
elif `'final'` → `FINAL`.

For the `(#2)` qualifier in round strings (e.g. `"(#2) Finals"`): strip the qualifier before
normalizing with `re.sub(r'^\(#\d+\)\s*', '', s)`.

---

## Name / Affiliation Split

The middle zone of a row contains `name... [status_code] affil`. Two strategies:

### Status-keyword split (classification / skater list)

Scan tokens for a status code (JR, SR, Juv, Nov, Open, Master, 35+, G1, F2…). If found at index > 0:
- Left of status → name
- Status token(s) → `skater_status`
- Right of status → affiliation

### Largest-gap split (results + fallback)

No status keyword → split the middle zone at the largest `x0`-to-`x0` gap between adjacent
words. Tempus column breaks produce visibly wider gaps than within-column word spacing.

**Failure cases:**
- Multi-word affiliations (e.g. `"1. Direct"`, `"EVD Speed Skating"`) where within-affil
  gap < name-affil gap → affil words leak into name.
- Result: name like `"Yiming He 1. Direct"` — detected by `NAME_INVALID_CHARS` QC check.
- Usually caused by status codes (`FNT`, `DNF`, `PEN`) in group columns that were not stripped,
  pushing all subsequent words into mid_words and creating a large gap at the wrong place.

---

## Multi-section and Multi-competition PDFs

### Multi-competition PDFs

Some PDFs bundle two separate competitions (e.g. Badger International + Heartland #7, or
US AGN + Jersey Challenge). Both competitions share a PDF but have separate skater lists,
classifications, and results sections.

**Handling:** `parse_all_races_format.py` naturally handles the all_races sections from both.
If the second competition uses a different format (e.g. per-page title), append its results
separately without deleting the existing data for the event.

**Race number collision:** in multi-competition PDFs, race numbers restart for the second
competition. The same race number + bib may appear on two different pages. QC reports this
as `DUPLICATE_BIB_IN_RACE_MULTIDAY` — expected behavior, not an error.

### Classification-only PDFs

Some events have incomplete PDFs containing only the classification section. Result rows will
be 0. Parse what is available (overall + distance classification) and record `parsed_at`.
Example: `2024_STDC_ProtocolFinal.pdf` (pages 1–46 of 118).

### Non-short-track events

PDFs tagged `all_races` in the events table may still be long-track or inline events.
Signs: unusual distances (400m, 600m, 800m, 1200m, 1600m — typical of 400m outdoor oval).
Action: remove parsed data, set `track_type = 'long'` (or `'inline'`), do not re-parse.
Example: 2025 WSA Gold Cup — long-track event on 400m oval, removed from DB.

---

## Content Deduplication

Some PDFs contain duplicate pages (e.g. Capital City 2026 was printed twice).

**Detect:** hash the page text after stripping the Tempus footer line
(`"Tempus Competition Software..."` + `"Page N of M"`). Skip pages with a seen hash.

**Do not confuse with multi-day combined PDFs:** events like Franklin Park and Full Throttle
combine two days into one PDF. Day 2 continues race numbering from Day 1 — pages are NOT
duplicates even if they contain the same section headers. The content hash correctly
distinguishes them since race numbers differ.

---

## Distance and Laps

### Standard short-track distances

| Distance (m) | Laps | Track |
|---|---|---|
| 111 | 1.0 | 111 m |
| 222 | 2.0 | 111 m |
| 333 | 3.0 | 111 m |
| 444 | 4.0 | 111 m |
| 500 | 4.5 | 111 m |
| 700 | — | 111 m (Junior E AGN format) |
| 777 | 7.0 | 111 m |
| 1000 | 9.0 | 111 m |
| 1500 | 13.5 | 111 m |
| 85 | 1.0 | 85 m |
| 170 | 2.0 | 85 m |
| 255 | 3.0 | 85 m |
| 340 | 4.0 | 85 m |
| 425 | 5.0 | 85 m |
| 2000–3000 | — | Relay |

### Non-standard / local-track distances

These appear in specific events and are valid — not parse errors:

| Distance (m) | Notes |
|---|---|
| 435 | Local 85m-track variant (~5 laps), Ohio State Short Track Meet |
| 440 | Non-standard local track, Puget Sound area |
| 595 | 7 laps × 85m, Puget Sound area |
| 611 | Jeff City (Capital City Championships) local track |

For the `(#2)` distance qualifier: `distance_m` is still the integer (e.g. 500). The two
races are distinguishable by their race numbers or page sequence.

---

## Parser Scripts

All scripts accept `--pdf-path PATH` and write to `data/skater_tracker_round2.db`.
Run `check_data_quality_v2.py --event-id N` after each parse.

| Script | Format | `--pdf-path` | Notes |
|---|---|---|---|
| `parse_all_races_format.py` | Text, inline section headers (Variant A) | Yes | Most events |
| `parse_ohio_heartland.py` | Text, per-page title headers (Variant B) | Yes | Ohio Invitational, Michigan State, Ohio State |
| `parse_ocr_all_races.py` | OFC section headers; OCR fallback (Variant C) | Yes | OFC, Oval Winter Challenge |
| `parse_stdc.py` | Text, inline headers + 3-group dist classification | Yes | STDC (2024 classification-only; 2025 full) |
| `parse_llm_v2.py` | Image (LLM vision), arbitrary layout | Yes | Legacy fallback for image PDFs |

---

## Common Pitfalls

| Pitfall | Symptom | Fix |
|---|---|---|
| OCR y-jitter splits rows | Wrong row grouping, many skipped rows | Use tesseract block+line grouping; assign min-top to all words in a group |
| Comma decimal in OCR times | `"0:44,564"` not parsed | `re.sub(r',(\d)', r'.\1', text)` on `^\d+:\d{2}` tokens |
| Double-dot in OCR times | `"1:55..223"` not parsed | `re.sub(r'\.{2,}', '.', text)` |
| `FNT`/status in F-group column | Leaks into name, triggers NAME_INVALID_CHARS | F-group check must use `RESULT_STATUS_RE` not just `GROUP_FINAL_RE` |
| H/Q/S/F 4-column layout | Bare digit heat number leaks into name | Add 2 extra SEMI_GROUP_RE strip passes after S-group |
| Super Final round_type wrong | FINAL_HDR_RE overrides section header | Never set `round_type` in FINAL_HDR_RE or HEAT_HDR_RE branches — inherit from section header |
| `(#2)` distance not matched | Second 500m page silently skipped | Make `(\d+)` in section header regex `(\d+(?:\s+\(#\d+\))?)` |
| Club header x-position drift | Division includes first club word | Use `club_x0 - 5px` as split threshold, not exact `club_x0` |
| Competition title on first line | Distance classification page not detected | Check first 3 lines for distance header, not just line 1 |
| Division+club merged in skater list | DIVISION_MISMATCH false positive in QC | QC division check: use `startswith` match, not just `endswith` or exact |
| Group codes (G1/F2) in skater list | Name includes "G2" digit, NAME_INVALID_CHARS | Add `[A-Z]\d{1,2}` to `STATUS_FIRST_RE` |
| Legend/footer-only pages | Junk rows parsed | Skip pages with word count < 80 |
| Non-standard skater list | Garbled skater entries | Check for `'FIRST NAME'`/`'LAST NAME'` in text; skip those pages |
| Long-track PDF in all_races format | 400m/600m/800m unknown distances | Check distances; if 400m oval format, mark `track_type='long'` and skip |

---

## Round 2 Parsing Status

**Target database:** `data/skater_tracker_round2.db`

### 2018-2019 season

| Event | id | Parser | Notes |
|---|---|---|---|
| 2018 Buffalo Championships & Heartland #1 | 4 | `parse_buffalo_ch.py` | ✅ New Buffalo/MeetDirector format; 143 overall class rows, 569 overall_dist rows, 999 results, 565 time class rows; 147 skater entries with clubs; name format "LastName [M\|F,] FirstName [AgeCode]" → "FirstName LastName"; time format "H:MM.mmm M" (trailing M stripped); DNS+/DNF+/DQ+ status codes added to QC |
| 2019 Empire State Games Short Track | 26 | `parse_buffalo_ch.py` | ✅ MeetDirector format (no club in results, single-line classification); 21 overall class rows, 82 overall_dist rows, 586 results, 0 TC; 88 skater entries (from classification + results); 2 divisions in classification (Nest Ladies/Men), 12 in results; "Printed by MeetDirector" footer strip; lookahead fix prevents division headers being consumed as club lines |
| 2018 Saratoga Cup & NEST #1 | 7 | `parse_stl_silver_skates.py` | ✅ NEST food/racing-themed divisions; 77 overall class rows, 369 results, 230 time class rows; 9 divisions (NEST Men/Ladies, Sistercharlie/MASTER/Catholic Boy/Diversify/Imperial Hint/Tenfold/Backyard Heaven Mixed); new parser fixes: strip "#1" standalone split-header line (classification pages) and "Meet & NEST #1" split-header (results pages) |
| 2019 St. Louis Silver Skates | 5 | `parse_stl_silver_skates.py` | ✅ Halloween-themed divisions; 71 overall class rows, 407 results, 230 time class rows; 9 divisions (Pumpkins/Goblins/Werewolves/Zombies/Vampires/Monsters/Ghosts/Witches/Warlocks Mixed); fractional point scale (500/400/320/256/205/164/131/105/84/67/53.5/43…) — new parser fix: `_parse_overall` now accepts fractional score tokens (53.5 etc.); parenthetical pronunciation guide stripped from name "Jersey Chytla (hit-la)" → "Jersey Chytla"; Pumpkins uses 6 distance columns (222 222 111 111 333 333) |
| 2018 Desert Classic Short Track | 1 | `parse_stl_silver_skates.py` | ✅ Standard points format (not CDC/rankings); 69 overall class rows, 507 results, 206 time class rows; 5 divisions (Combined B/C Mixed, Open A Ladies/Men, Open B Men); `is_time()` fix applied (fractional point values "53.5" were parsed as times — required ≥2 decimal digits) |
| 2018 Great Lakes Short Track | 6 | `parse_stl_silver_skates.py` | ✅ Gateway legacy format; 75 overall class rows, 430 results, 232 time class rows; 8 divisions (Groups 1–6 Mixed + Heartland Ladies/Men) |
| 2018 Franklin Park Barrel Buster | 10 | `parse_stl_silver_skates.py` | ✅ Gateway legacy format; 63 overall class rows, 314 results, 206 time class rows; 10 divisions (Groups 1–10 Mixed); PEN rows had dummy "0.200" time stored as 0.2s — nulled via SQL (result ids 91972/91973, classification ids 54547/54548) |
| 2018 Bay State Championships & NEST #2 | 12 | `parse_stl_silver_skates.py` | ✅ BSSC format; 56 overall class rows, 332 results, 168 time class rows; 9 divisions (NorthEast Ladies/Men + 7 Mixed); 1 CLASS_BIB_NOT_IN_LIST expected (DNS skater in classification only) |
| 2018 Capital City Championships | 14 | `parse_stl_silver_skates.py` | ✅ Jefferson City legacy format; 37 overall class rows, 208 results, 179 time class rows; 7 divisions (Meet Elite/Terminators/Fantastic Finishers/Fabulous Flyers/Super Stars/Future Olympians/Gold Seekers Mixed) |
| Central Wisconsin Open | 16 | `parse_stl_silver_skates.py` | ✅ Legacy format; 36 overall class rows, 239 results, 106 time class rows; 4 divisions (Blazing Blades/Future Stars/Ice Rockets/Rink Rangers Mixed) |
| 2018 Park Ridge Open | 9 | `parse_stl_silver_skates.py` | ✅ Age-group format (Junior C/D/E/PeeWee/Open A/B/Master/Tiny Tots); 75 overall class rows, 341 results, 266 time class rows; 21 divisions using full Ladies/Men names in classification vs F/M/X abbreviations in results — 13 SQL division fixes applied (F→Ladies, M→Men, bare→Mixed); "Overall Classification" standalone sub-header stripped (new `_STRIP_RES` pattern); name-wrap fix: bib 60 "Immanuel Martinez-Mohabir" + division correction for bib 50 ("Mohabir"→"Junior E Novice Men"); bib=999 Alexander Thurston DNS (1 CLASS_BIB_NOT_IN_LIST expected) |
| 2018 Jeff Golz Memorial Ohio Invitational & Heartland #3 | 13 | `parse_stl_silver_skates.py` | ✅ Ohio/Heartland Christmas-themed divisions; 83 overall class rows, 577 results, 332 time class rows; 8 divisions (Heartland Men/Ladies + Polar Bears/Reindeer/Candy Canes/Jingle Bells/Snowmen/Penguins Mixed); 3-line results page header stripped (new patterns: Jeff Golz, Ohio Invitational Meet, - Heartland Series line); results use "Heartland F"/"M" → translated to Ladies/Men; 2 CLASS_BIB_NOT_IN_LIST (bibs 3/86 DNS in Penguins) |
| 2019 January Thaw & NEST #3 | 17 | `parse_stl_silver_skates.py` | ✅ NEST ride-themed divisions; 112 overall class rows, 550 results, 0 TC rows (no TC section in PDF); 10 divisions (NEST Men/Ladies + Bizarro/DejaVu/El Toro/King Da Ka/Master/Novice/Teacups/Twister Mixed); split header "and NEST 3" stripped (new pattern) |
| 50th Gateway Championships | 22 | `parse_stl_silver_skates.py` | ✅ Gateway format; 42 overall class rows, 241 results, 178 time class rows; 8 divisions (Terminators/Future Olympians/Meet Elite/Fantastic Finishers/Gold Seekers/Blades of Glory/Super Stars/Fabulous Flyers Mixed); Gateway uses 55m and 166m distances for youngest divisions; 3-line results page header stripped (new "Speedskating" + "Championship" singular patterns); garbled "Overall reMseueltts Elite Mixed" → "Meet Elite Mixed" fixed via SQL; 4 CLASS_BIB_NOT_IN_LIST (DNS bibs) |
| 2019 MASA ST Championships & NEST #6 | 31 | `parse_stl_silver_skates.py` | ✅ TC-only PDF (no classification or results); 0 overall class rows, 0 results, 289 time class rows; divisions: NEST Men/Ladies + Bantam Men/Ladies + Senior Men/Ladies (MASTC age-group format); 39 SCHEDULE_MISSING expected (no results to match TC entries) |
| 2019 NorthBurke ST Open | 27 | `parse_stl_silver_skates.py` | ✅ Corporate-sponsor-named divisions; 93 overall class rows, 616 results, 0 TC rows (no TC section in PDF); 10 divisions (Olivers Trains & Toys/Perfomance & Sport/Wellness Executive/Lake Forest Peds/Hansen Family/Hepkema Family/Illinois Bone & Joint/UltraSlide/Evanston Subaru/Buckun & Burns Mixed); event headers with round on next line ("Semifinals") handled by existing parser; 2 CLASS_BIB_NOT_IN_LIST (DNS bibs 82/81) |
| 2018 AmCup 1 ST & Fall WC Qualifier | 2 | `parse_stl_silver_skates.py` | ✅ International AmCup format; 79 overall class rows, 570 results, 231 time class rows; 2 divisions (AmCup Men/Ladies); point scale uses full ISU table (values from 1 to 1000); results use "AmCup F"/"M" → translated to Ladies/Men by updated `_detect_mixed_suffix` |
| 2019 Winter Challenge Short Track | 19 | `parse_stl_silver_skates.py` | ✅ 16 overall class rows, 89 results, 44 time class rows; 2 divisions (Challenge A/B Mixed); new parser fix: "Winter Challenge" page title strip pattern |
| 2019 US Championships Short Track & AmCup 2 | 20 | `parse_stl_silver_skates.py` | ✅ AmCup elite format; 32 overall class rows, 216 results, 93 time class rows; 2 divisions (AmCup Men/Ladies); asterisk guest markers stripped; results use "AmCup M"/"F" → translated by `_detect_mixed_suffix` fix |
| 2019 Presidential Cup & NEXT #5 | 29 | `parse_stl_silver_skates.py` | ✅ NEST format; 112 overall class rows, 730 results, 429 time class rows; 12 divisions (NEST Men/Ladies, Junior C/D/E/F/Master Men/Ladies, Novice Mixed); 2 CLASS_BIB_NOT_IN_LIST (DNS bibs 78/43) |
| 2019 Ohio State Championships | 30 | `parse_stl_silver_skates.py` | ✅ Classification-only PDF (3 pages); 45 overall class rows, 0 results, 0 TC; 6 divisions (Olympic-city themed: Salt Lake/Torino/Nagano/Sochi/Pyeongchang/Vancouver); relay placeholder rows skipped |
| 2019 Amcup 3 Short Track | 36 | `parse_stl_silver_skates.py` | ✅ International AmCup format; 46 overall class rows, 311 results, 145 time class rows; 2 divisions (AmCup Men/Ladies) + relay TC; 5000m relay distance added to KNOWN_DISTANCES; results use "AmCup F"/"M" → translated; 3 CLASS_BIB_NOT_IN_LIST (DNS bibs); relay TC has no results (3 SCHEDULE_MISSING expected) |
| 2019 Age Group Nationals Short Track | 37 | `parse_stl_silver_skates.py` | ✅ AGN format; 185 overall class rows, 1453 results, 710 time class rows; 11 divisions (Junior A Open/AB Open/B/C/D/E + Master Group 1 Men/Ladies); age-group labels in names (e.g. "Kevin Geminder (35)") stripped; `_detect_mixed_suffix` fix resolves F/M→Ladies/Men translation for all divisions; 2 SCHEDULE_MISSING expected (AGN Relay Ladies/Men TC only — no individual relay results) |
| 2019 Cheese Cup & Heartland #5 | 35 | `parse_stl_silver_skates.py` | ✅ Cheese-themed divisions; 96 overall class rows, 566 results, 289 time class rows; 10 divisions (Heartland Men/Ladies + Cheese Curds/Provolone/Cheddar/Parmesan/Mozzarella/Gouda/Pepper Jack/Aged Cheddar Mixed); Heartland Men/Ladies use 6 distance columns (1000×2, 500×2, 1500×2); results use "Heartland F"/"Heartland M" → normalized to Ladies/Men; Fun Race 333/222 Mixed in classification (times stored as points) and in results — 2 DIVISION_MISMATCH warnings expected; 2 CLASS_BIB_NOT_IN_LIST (DNS bibs 35/155 in Gouda); 1 TIME_IMPOSSIBLE: Xavier Lawrence 1500m heat time 32.200 (source error, should be ~2:32) — nulled |

### 2019-2020 season

| Event | id | Parser | Notes |
|---|---|---|---|
| 2020 Empire State Games Short Track | 53 | `parse_buffalo_ch.py` | ✅ MeetDirector format; 75 overall class rows, 290 overall_dist rows, 518 results, 292 time class rows; 8 divisions; classification has club on 2nd line; TC has standalone "M" before each row (skipped automatically); space-before-comma name fix added ("Babkine-Osterrath , Xavier") |
| 2019 Buffalo Championships & Heartland #1 | 41 | `parse_stl_silver_skates.py` | ✅ Buffalo legacy format; 140 overall class rows, 966 results, 556 time class rows; no club info; gender markers (m/f) stripped from names; 37 garbled times from page 6 timing display artifact nulled; 48 SCHEDULE_MISSING expected (TC uses "Bandits Ladies", results use "Bandits F") |
| 24th Capital City Championships | 47 | `parse_stl_silver_skates.py` | ✅ Jefferson City legacy format; 26 overall class rows, 151 results, 143 time class rows; no club info |
| 2019 Desert Classic Short Track | 38 | `parse_stl_silver_skates.py` | ✅ Desert Classic legacy format; 89 overall class rows, 759 results, 257 time class rows; no club info; 12 SCHEDULE_MISSING expected (TC uses "Combined B Mixed"/"Open A Men", results use "Combined B"/"Open A M"); CLASS_BIB_NOT_IN_LIST for DNS-only skaters expected |
| 2019 Franklin Park Barrel Buster | 44 | `parse_stl_silver_skates.py` | ✅ Classification-only PDF (no results or TC); 63 overall class rows, 9 divisions (Groups 1–9); manual correction: bib 7 name fixed to "Luis Fernando Marimoto Taqueushi" (name wrapped across lines in PDF); 3 Group 1 rows had division fixed from "Taqueushi" to "Group 1 Mixed" |
| 2019 Holiday Classic | 218 | `parse_stl_silver_skates.py` | ✅ Gateway legacy format; 52 overall class rows, 312 results, 204 time class rows; 6 divisions (Penguins/Reindeer/Narwhals/Polar Bears/Seals/Walruses Mixed); small-oval distances (100/200/300/400/600/800m) added to KNOWN_DISTANCES; bib 18 name normalized from "Zaltannah [ZJ] Schmeisser" to "Zaltannah Schmeisser" (bracket nickname); bib 139 has no name in source PDF — 2 NAME_EMPTY result rows expected |
| 2019 Ohio Invitational & Heartland #3 | 48 | `parse_stl_silver_skates.py` | ✅ Gateway legacy format; 103 overall class rows, 616 results, 312 time class rows; 10 divisions; page title "2019 OHIO INVITATIONAL - / HEARTLAND MEET" added to strip patterns; suffix deduplication fix applied (prevents "Heartland Ladies Ladies" / "Heartland Men Men"); 1 CLASS_BIB_NOT_IN_LIST expected (bib 50 in overall classification only); 30 SCHEDULE_MISSING expected (classification uses full names "Heartland Ladies", results use abbreviations "Heartland Ladies F") |
| 2019 Saratoga Cup & NEST #1 | 43 | `parse_stl_silver_skates.py` | ✅ Results-only PDF (no classification or TC); 483 results; guest-skater asterisk names (e.g. "Simon Ludlow*") stripped automatically; 1 row silently dropped (bib 192 Lucas Li, Old Red 333m Final A rank 1 — rank digit merged into bib in PDF source) |
| 2020 January Thaw & NEST #3 | 52 | `parse_stl_silver_skates.py` | ✅ Gateway legacy format; 96 overall class rows, 466 results, 286 time class rows; 8 divisions (NEST/Open/Junior C–G); 18 SCHEDULE_MISSING expected (6 divisions use F/M suffix in results vs Men/Ladies in classification, appended Mixed → mismatch); Yundi Gao (Open Mixed) has 4 score values in PDF instead of 6 — 1000m overall_dist row not stored |
| 2019 Land of Lincoln & Heartland #6 | 32 | `parse_stl_silver_skates.py` | ✅ Gateway legacy format; 94 overall class rows, 629 results, 0 TC rows (no TC section in PDF); 11 divisions; manual correction: bib 112 "Luis Fernando Marimoto Taqueushi" name-wrap — Caroline Hensley (bib 108, rank 5 Pee Wee B Mixed) division fixed from "Taqueushi" to "Pee Wee B Mixed", 6 overall_dist rows inserted; 2 CLASS_BIB_NOT_IN_LIST expected (bibs 16, 101 in classification only — registered but did not race) |
| 2020 Middle Atlantic ST Championships & NEST #6 | 62 | `parse_stl_silver_skates.py` | ✅ Gateway legacy format; 86 overall class rows, 416 results, 255 TC rows; 11 divisions; (F)/(M) gender markers in names stripped automatically; 18 SCHEDULE_MISSING expected (6 divisions use F/M suffix in results — Barnum/Ellsworth/Hale Men, Hale/NEST Ladies, NEST Men) |
| 2020 Ohio State Championships | 60 | `parse_stl_silver_skates.py` | ✅ Gateway/Ohio legacy format; 51 overall class rows, 331 results, 192 TC rows; 6 divisions named after Olympic cities (Pyeongchang/Sochi/Nagano/Torino/Vancouver/Salt Lake); division names normalized from " M" suffix to " Men" in results and SkaterEntry via SQL (results use abbreviated "M", classification uses full "Men"); 5 relay TC rows deleted (relay teams, not individual skaters); new parser fixes: TC suffix-append logic, ", SR"/", JR" name suffix stripping, Ohio State strip patterns; 3 CLASS_BIB_NOT_IN_LIST expected (bibs 31/40/22 — DNS skaters, in classification only) |
| 2020 Presidential Cup & NEST #5 | 57 | `parse_stl_silver_skates.py` | ✅ NEST format; 89 overall class rows, 618 results, 363 TC rows; 10 divisions; garbled classification division names fixed via SQL ("Overall reOspueltns Mixed" → "Open Mixed", "Overall reJsuunltisor D Ladies" → "Junior D Ladies" — "Overall results" page label merges with first division header at page boundaries); new parser fix: result "F"/"M" gender suffixes normalized to "Ladies"/"Men" when append_mixed=True; NEST Ladies and Junior C Mixed use 6 distance columns (777 777 500 500 1000 1000 — each distance listed twice for two counted heats) |
| 102nd Chicago Silver Skate | 40 | `parse_stl_silver_skates.py` | ✅ Chicago format; 97 overall class rows, 494 results, 293 TC rows; 12 animal divisions (Kangaroos through Springbok/Giant Tortoises); 111m distance (Kangaroos division); "African Wild Dogs Mixed" split across two lines in PDF — handled by suffix-append; some divisions use 5-column classification (777 500 777 500 1000) with each distance repeated for two counted heats; Garden Snails/Giant Tortoises/Springbok use non-standard point scale (500/400/320/256...) |
| 2020 US Short Track Championships & Junior Championships | 50 | `parse_stl_silver_skates.py` | ✅ AmCup championship format; 57 overall class rows (16 ladies + 41 men), 570 results, 179 TC rows; 2 divisions (AmCup Ladies, AmCup Men) + relay; classification uses "Best/Combined/Points" columns (not per-distance) → 0 overall_dist rows; club codes (MAS/DIR/OSA/WSS/WSA/ASI) stripped from classification names; `^` qualifier markers stripped throughout; result divisions "AmCup F"/"AmCup M" normalized to "AmCup Ladies"/"AmCup Men" via SQL; new parser fixes: `^` stripping in names, 3-letter club code stripping, HEAT_HDR_RE updated for (A)/(B) group label suffix, strip patterns for US Championships/Junior Champs/Rankings labels; 1 SCHEDULE_MISSING expected (MG Relay Mixed 2000m — relay TC, no matching results) |
| 2020 St. Louis Silver Skates | 61 | `parse_stl_silver_skates.py` | ✅ Gateway format; 43 overall class rows, 252 results, 192 TC rows; 9 divisions (Blades of Glory/Meet Elite/Terminators/Fantastic Finishers/Fabulous Flyers/Super Stars/Future Olympians/Gold Seekers/Masters Mixed); PDF has reversed section order (Classification → TC → Results instead of normal Classif → Results → TC) — fixed by new `tc_first` flag in `_split_sections`; "Fantastic Finishers Mixed" split across two lines — handled by suffix-append; 1 CLASS_BIB_NOT_IN_LIST expected (bib 37, DNS) |
| 2020 US ST AmCup 3 | 58 | `parse_stl_silver_skates.py` | ✅ AmCup international format; 52 overall class rows, 505 results, 155 TC rows; 2 divisions (AmCup Ladies/Men); country codes (CAN/USA/HKG) stripped from classification names; classification uses "Best/Combined/Points" columns → 0 overall_dist rows; result divisions "AmCup F"/"AmCup M" normalized to "AmCup Ladies"/"AmCup Men" via SQL; new strip pattern for "AmCup N" page headers |
| 2020 Northbrook Open | 56 | `parse_stl_silver_skates.py` | ✅ Corporate-sponsor-named divisions; 86 overall class rows, 557 results, 0 TC rows (results-only PDF, no TC section); 10 divisions (all Mixed); 3 garbled classification division names fixed via SQL (2-word continuation lines: "Psychology Mixed" → "Performance & Sport Psychology Mixed", "Executive Mixed" → "The Wellness Executive Mixed", "Institute Mixed" → "Illinois Bone & Joint Institute Mixed"); new parser fixes: EVENT_SEARCH_RE round type optional, lookahead buffer for event headers split across 2 PDF lines; NO_CLASSIFICATION INFO expected; 4 CLASS_BIB_NOT_IN_LIST (DNS bibs 46/36/80/34) |
| 2019 Bay State Championships Short Track | 49 | `parse_stl_silver_skates.py` | ✅ BSSC food-themed format; 56 overall class rows, 336 results, 168 TC rows; 10 divisions (NorthEast Men/Ladies + Thanksgiving food names Mixed + Apple Pie Men); page headers "BSSC Fall 2019" contain year — handled by _YEAR_RE; F/M suffix normalized to Ladies/Men for NorthEast divisions |
| 2020 AmCup 1 Short Track | 39 | `parse_stl_silver_skates.py` | ✅ USS/national event in legacy-compatible results format; overall classification deleted (uses USS format with country codes — names like "Ryan Pivirotto USA" incompatible with legacy parser); 635 results, 190 TC rows; (Y) Youth markers stripped from names; 6 SCHEDULE_MISSING expected (TC uses "AmCup Men"/"AmCup Ladies", results use "AmCup F"/"AmCup M") |
| 2020 Land of Lincoln & Heartland #5 | 59 | `parse_stl_silver_skates.py` | ✅ Gateway legacy format; 94 overall class rows, 421 overall_dist rows, 642 results, 0 TC rows (no TC section in PDF); 11 divisions (Tiny Tot/Pee Wee B/Pony B/Midget B/Open B/Senior B/Pee Wee A/Junior E/Junior D/Junior C/Heartland/Open A/Master Mixed); manual correction: bib 22 "Luis Fernando Marimoto Taqueushi" name-wrap — "Taqueushi" division rows reassigned to "Junior E (Pony) Mixed"; NO_CLASSIFICATION INFO expected (no distance TC) |

### 2020-2021 season

| Event | id | Parser | Notes |
|---|---|---|---|
| 2021 Gateway Championships | 65 | `parse_stl_silver_skates.py` | ✅ Gateway legacy format; 31 overall class rows, 148 results, 121 time class rows; no club info; 166m added to known distances |
| 2021 St. Louis Silver Skates | 63 | `parse_stl_silver_skates.py` | ✅ Car-themed divisions; 30 overall class rows, 143 results, 0 TC rows (results-only PDF); 8 divisions (Mustangs/Camaros/Challengers/Teslas/Ferraris/Oldsmobiles/Mini Coopers/Raptors Mixed); 2 garbled classification rows lost (bib 15 Hufsah Sadiq in Challengers, bib 32 Sawyer Niemeth in Raptors — "Overall results" label merges with skater data rows at page boundaries); Mini Coopers and Raptors use 85m track distances; page header "Skates at Gateway" silently skipped in results parser; 2 CLASS_BIB_NOT_IN_LIST (bibs 50/22, DNS zeros) |
| 2022 Toyota ST Invitational | 68 | `parse_stl_silver_skates.py` | ✅ Toyota car-themed divisions; 68 overall class rows, 710 results, 239 TC rows; 8 classified divisions (Tacoma/4Runner/Highlander/RAV4/Prius/Sienna/Venza/Camry Mixed) + Corolla (TC-only) + Mixed Relay; new parser fixes: strip "Toyota Invitational" page header (non-year title would otherwise become division name), alphanumeric division name detection for "4Runner Mixed" (starts with digit character); Sienna Mixed uses 6 distances with non-standard point scale; 1 SCHEDULE_MISSING expected (Mixed Relay Mixed 3000m) |
| 2022 Gateway Short Track Championships | 79 | `parse_stl_silver_skates.py` | ✅ Gateway format; 36 overall class rows, 211 results, 132 TC rows; 8 bird-themed divisions (Peregrines/Goshawks/Merlins/Golden Eagles/Barn Swallows/Red-tail Hawks/Sparrow Hawks/American Kestrels Mixed); Sparrow Hawks uses 85m track distances |
| 2022 NorthBurke Open | 81 | `parse_stl_silver_skates.py` | ✅ Olympic-city-themed divisions; 73 overall class rows, 510 results, 294 TC rows; 9 divisions (Pyeongchang 2018/Sochi 2014/Turin 2006/Salt Lake City 2002/Lillehammer 1994/Nagano 1998/Albertville 1992/Calgary 1988/Vancouver 2010 Mixed); Pyeongchang 2018 uses 85m track distances; major parser fixes: removed `_YEAR_RE` from `_parse_overall`, `_parse_time_classification`, and `_is_division_header` (year-containing division names like "Pyeongchang 2018" were incorrectly rejected), added "NorthBurke ST"/"BSSC" strip patterns, result event header no longer double-appends "Mixed" when already present; name-wrap: bib 80 "Luis Fernando Marimoto Taqueushi" — name fixed, bib 103 (Liam Sears) overall row relocated, 4 overall_dist rows inserted |

### 2021-2022 season

| Event | id | Parser | Notes |
|---|---|---|---|
| 2021 Buffalo Short Track Championships | 71 | `scrape_shorttracklive.py` | ✅ web-scraped comp=639 saison=16; 111 participants, 768 results; 107m track (dist 62-66, incl. 383m super final) |
| 103rd Chicago Silver Skates (2021) | 72 | `scrape_shorttracklive.py` | ✅ web-scraped comp=665 saison=16; 63 participants, 318 results; dist=51 (400m non-standard) |
| 2021 Great Lakes Short Track | 73 | `scrape_shorttracklive.py` | ✅ web-scraped comp=669 saison=16; 91 participants, 464 results |
| 2021 Park Ridge Open | 75 | `scrape_shorttracklive.py` | ✅ web-scraped comp=670 saison=16; 62 participants, 310 results |
| 2021 Franklin Park Barrell Buster | 76 | `scrape_shorttracklive.py` | ✅ web-scraped comp=671 saison=16; 55 participants, 267 results |
| 2022 Ohio Invitational & Heartland #3 | 211 | `scrape_shorttracklive.py` | ✅ web-scraped comp=676 saison=16; 87 participants, 519 results; event added to DB (was missing) |
| 2021 GSS Holiday Dash | 212 | `scrape_shorttracklive.py` | ✅ web-scraped comp=680 saison=16; 13 participants, 39 results; event added to DB (was missing) |
| 2021 Bay State Championships & NEST #2 | 77 | `scrape_shorttracklive.py` | ✅ web-scraped comp=681 saison=16; 68 participants, 404 results |
| 2022 Mini Jan Thaw | 213 | `scrape_shorttracklive.py` | ✅ web-scraped comp=686 saison=16; 19 participants, 57 results; event added to DB (was missing) |
| 2022 US Junior Championships Short Track | 80 | `scrape_shorttracklive.py` | ✅ web-scraped comp=690 saison=16; 39 participants, 575 results; date corrected 2/4→2/8; 2 bogus times nulled |
| 2022 U.S. Olympic Team Trials Short Track | 78 | `scrape_shorttracklive.py` | ✅ web-scraped comp=683 saison=16; 31 participants, 390 results |
| 2022 MASA ST Championships (MASTC) | 83 | `scrape_shorttracklive.py` | ✅ web-scraped comp=710 saison=16; 96 participants, 323 results |
| 2022 Land of Lincoln & Heartland #5 | 84 | `scrape_shorttracklive.py` | ✅ web-scraped comp=711 saison=16; 115 participants, 780 results |
| 2022 Michigan State Meet | 85 | `scrape_shorttracklive.py` | ✅ web-scraped comp=712 saison=16; 34 participants, 270 results; date corrected 3/13→3/16; non-USS sanctioned |
| 2022 US Age Group Nationals Short Track | 86 | `scrape_shorttracklive.py` | ✅ web-scraped comp=701 saison=16; 145 participants, 941 results |
| 2021 Saratoga Cup & N.E.S.T. #1 | 74 | `scrape_shorttracklive.py` | ✅ web-scraped comp=662 saison=16; 98 participants, 483 results |
| 2022 St. Louis Silver Skates ST Championships | 82 | `parse_stl_silver_skates.py` | ✅ Gateway club legacy format; 38 overall class rows, 224 results, 143 time class rows; no club info; 4 first-rank rows skipped (garbled page boundaries); division names normalized ("Kitkats" → "Kitkats Mixed") |

### 2022-2023 season (complete — USS protocol format introduced mid-season)

| Event | id | Parser | Notes |
|---|---|---|---|
| 2023 Burpee Pinnacle Championships | 88 | `scrape_shorttracklive.py` | ✅ web-scraped comp=725 saison=17; 66 participants, 730 results; date corrected 7/28→7/23; dist=22 (2000m relay) |
| 2023 Desert Classic Short Track | 89 | `scrape_shorttracklive.py` | ✅ web-scraped comp=727 saison=17; 113 participants, 947 results |
| 2023 USS Fall World Cup Qualifier Short Track | 90 | `scrape_shorttracklive.py` | ✅ web-scraped comp=739 saison=17; 47 participants, 687 results |
| 104th Silver Skates Meet | 92 | `scrape_shorttracklive.py` | ✅ web-scraped comp=763 saison=17; 90 participants, 459 results; 1 impossible time nulled (15.39s for 1000m) |
| 2023 Great Lakes ST & Brat Fry & Heartland #2 | 93 | `scrape_shorttracklive.py` | ✅ web-scraped comp=740 saison=17; 94 participants, 490 results; date corrected 11/5→11/11 |
| 2023 Park Ridge Open | 94 | `scrape_shorttracklive.py` | ✅ web-scraped comp=757 saison=17; 69 participants, 340 results |
| 2023 Franklin Park Barrel Buster | 214 | `scrape_shorttracklive.py` | ✅ web-scraped comp=758 saison=17; 54 participants, 269 results; event added to DB; 1 bogus time nulled (10:17 for 500m) |
| 26th Capital City Championships | 215 | `scrape_shorttracklive.py` | ✅ web-scraped comp=759 saison=17; 26 participants, 143 results; event added to DB; dist=13 (165m), 59 (666m) added; 13 "Skill Race" rows deleted |
| 2023 Gateway Short Track Championships | 102 | `scrape_shorttracklive.py` | ✅ web-scraped comp=809 saison=17; 31 participants, 186 results; 1 impossible time nulled (54.0s for 777m) |
| 2023 American Dream ST Championship (Feb) | 217 | `scrape_shorttracklive.py` | ✅ web-scraped comp=788 saison=17; 94 participants, 594 results; event added to DB (separate from Jan NEST#3 comp=764) |
| 2023 Empire State Games Short Track | 104 | `scrape_shorttracklive.py` | ✅ web-scraped comp=785 saison=17; 55 participants, 380 results |
| 2023 NorthBurke Short Track Open | 107 | `scrape_shorttracklive.py` | ✅ web-scraped comp=810 saison=17; 94 participants, 472 results; 5 Skill Race rows deleted |
| 2022 Buffalo Short Track Championships | 91 | `scrape_shorttracklive.py` | ✅ web-scraped comp=735 saison=17; 132 participants, 909 results; dist=57 (111m); 1 TIME_IMPOSSIBLE (LIPPA 1500m→99.8s, source error on website) |
| 2022 Saratoga Cup Short Track Championship | 95 | `scrape_shorttracklive.py` | ✅ web-scraped comp=746 saison=17; 94 participants, 457 results |
| 2023 Bay State Championships & NEST #2 | 96 | `parse_ohio_heartland.py` | ✅ Variant B; Group A Picking heats filled via TC fallback (no dist class); TC `--end-page 23` |
| UOO Winter Challenge | 99 | `parse_all_races_format.py` | ✅ Classification-only; TC `--start-page 44 --end-page 58`; page 7 image-based (dist class partially missing) |
| 2023 US Championships & Junior Championships Short Track | 100 | `parse_all_races_format.py` + `scrape_shorttracklive.py` | ✅ PDF: classification-only; web: comp=761 saison=17 added 549 race results (MEN/WOMEN from website); classification preserved |
| 2023 Ohio State Championships | 108 | `parse_ohio_heartland.py` | ✅ Variant B; TC `--start-page 43` (no TC keyword in PDF); 1 TC source typo (Thatcher 1000m) |
| 2023 Middle Atlantic Short Track Championships | 110 | `parse_all_races_format.py` | ✅ Classification-only; TC section "TIME CLASSIFICATION" all-caps, cross-page; 6 DNS bibs absent from TC |
| 2023 US Age Group Nationals Short Track | 112 | `parse_all_races_format.py` + `scrape_shorttracklive.py` | ✅ PDF: classification-only; web: comp=815 saison=17 added 843 race results; 27 SCHEDULE_MISSING expected (website names differ: "Junior D Girls/Boys" vs PDF "Junior D Women/Men"; Combined/Master absent from website); dist=23 (3000m) added |
| 2023 Presidential Cup | 106 | `scrape_shorttracklive.py` | ✅ web-scraped comp=814 saison=17; 103 participants, 780 results; 2 bogus times nulled |
| 98th St. Louis Silver Skates Short Track | 109 | `scrape_shorttracklive.py` | ✅ web-scraped comp=801 saison=17; 37 participants, 248 results; 1 bogus time nulled; dist=23 (3000m) added |
| 2023 Ohio Invitational ST Meet | 97 | `parse_stl_silver_skates.py` | ✅ Gateway legacy format; 90 overall class rows, 258 overall_dist rows, 270 distance class rows, 534 results; 7 divisions (HEARTLAND Men/Ladies + holiday-themed Mixed: Polar Bears/Reindeer/Jingle Bells/Elves/Candy Canes); manual correction: bib 82 "Luís Fernando Marimoto Taqueushi" name-wrap — "Taqueushi" division rows reassigned to "ELVES Mixed"; missing overall_dist rows for bibs 75, 62, 77, 44 (caught in name-wrap, cur_dist_cols reset before their rows) |
| 2023 Michigan State Meet | 105 | `parse_llm_v2.py` | ✅ Font-garbled text PDF (28 pages via LLM vision); results-only (no distance classification); 110 overall class rows, 419 results; 5 divisions (Junior A Men/Junior D Female/Junior F Mix/Masters Mix); division name normalization: 100 rows with trailing gender codes ("M"/"Ladies"/"Mixed"/"X") stripped (LLM appended round gender code to division name on some pages); NO_CLASSIFICATION INFO expected |
| 2023 Land of Lincoln & Heartland #5 | 111 | `parse_stl_silver_skates.py` | ✅ Gateway legacy format; 88 overall class rows, 404 overall_dist rows, 604 results, 0 TC rows (no TC section in PDF); 11 divisions (Heartland/Tiny Tot/Pee Wee B/Pony B/Open B/Senior B/Pee Wee A/Junior E/Junior D/Open A/Master Mixed); manual correction: bib 22 "Luis Fernando Marimoto Taqueushi" name-wrap — "Taqueushi" division rows reassigned to "Junior E (Pony) Mixed"; missing overall_dist rows for bibs 42, 53, 11, 86, 27, 39, 92, 69; NO_CLASSIFICATION INFO expected (no distance TC); 277m added to KNOWN_DISTANCES |

### 2025-2026 season (complete)

| Event | id | Parser | Notes |
|---|---|---|---|
| 2025 Robby Kaufman Short Track Challenge | 196 | `parse_all_races_format.py` | ✅ 49 skater entries, 49 overall class rows, 114 distance class rows, 195 results, 97 TC rows; 7 divisions (Division 1–7); 85m track distances (170m, 255m) for Division 1; TC header rows misread as division names — 138 spurious rows deleted; TC cross-check: all times match |
| 2025 Winter SPEEDtacular | 195 | `parse_llm_v2.py` | ✅ Image-based PDF (45 pages via LLM vision); 40 overall class rows, 81 distance class rows, 119 results; 5 divisions (Stolz/Kepka/Coyle/Cepuran/Overall); 45 API calls; 3 SQL fixes: 5 "Hansen's 222" rows had NULL division, 5 "Hansen's 111" rows had NULL division + wrong distance_m=777 (corrected to 111m), 3 "Hansen's 111 (#2)" rows had wrong distance_m=777 (corrected to 111m) — LLM misread 111 as 777 on those pages; 16 SCHEDULE_MISSING warnings expected (LLM naming inconsistencies: "Coyle's 333" vs "Coyle", "A" vs "A Final") |
| 2025 UOO Winter Challenge | 193 | `parse_llm_v2.py` | ✅ Image-based PDF (64 pages via LLM vision); 151 overall class rows, 262 distance class rows, 405 results; 5 divisions (Challenge A/B/D/E + overall); 64 API calls; page-level cache at `.llm_cache.json`; 2 SQL fixes: 15 distance class rows for "Challenge C's 333" had distance_m=NULL (set to 333), 18 result rows for Challenge E's 333 semi-finals had NULL division (fill_missing_race_context tied — resolved to "Challenge E's 333" 333m); 15 SCHEDULE_MISSING warnings expected (LLM used inconsistent division names across classification vs results — data present under variant names) |
| 2026 January Thaw & NEST #3 | 186 | `parse_all_races_format.py` | ✅ |
| 2026 Gateway Championships | 192 | `parse_all_races_format.py` | ✅ |
| 2026 LOL Protocol | 200 | `parse_all_races_format.py` | ✅ |
| 2026 Empire State Short Track & Heartland | 201 | `scrape_shorttracklive.py` | ✅ web-scraped comp=1058 saison=20; 72 participants, 503 results; 1 invalid time nulled (46:35 for 333m) |
| 2026 NorthBurke Open | 202 | `parse_all_races_format.py` | ✅ |
| 2026 Park Ridge Open | 203 | `parse_all_races_format.py` | ✅ |
| 2026 Presidential Cup | 203 | `parse_all_races_format.py` | ✅ |
| 2026 Badger/Heartland #4 | 185 | `parse_all_races_format.py` | ✅ |
| 2026 US Junior Championships | 207 | `parse_all_races_format.py` | ✅ |
| 2026 US ST Championships | 208 | `parse_all_races_format.py` | ✅ |
| 2026 USS AGN ST | 210 | `parse_all_races_format.py` | ✅ 700m added |
| 2025 OFC Protocol | 181 | `parse_ocr_all_races.py` | ✅ |
| 2025 Ohio Invitational & Heartland #3 | 191 | `parse_ohio_heartland.py` | ✅ |
| 2025 Desert Classic Short Track | 179 | `parse_stdc.py` | ✅ |
| 2025 Chicago Silver Skates | 183 | `parse_all_races_format.py` | ✅ |
| 2025 Franklin Park Barrel Buster | 186 | `parse_all_races_format.py` | ✅ |
| 2025 InterScholastic | 178 | `parse_all_races_format.py` | ✅ |
| 101st St. Louis Silver Skates | 205 | `parse_all_races_format.py` | ✅ |
| 29th Capital City Championships | 204 | `parse_all_races_format.py` | ✅ |
| 2025 Buffalo ST Championships & Heartland #1 | 184 | `scrape_shorttracklive.py` | ✅ web-scraped comp=1004 saison=20; 173 participants, 1203 results, 14 divisions |
| 2025 Saratoga Cup & NEST | 187 | `scrape_shorttracklive.py` | ✅ web-scraped comp=1026 saison=20; 91 participants, 414 results; 7 duplicates deduped |

### 2023-2024 season (complete)

| Event | id | Parser | Notes |
|---|---|---|---|
| Buffalo Championships (2023) | 116 | `scrape_shorttracklive.py` | ✅ web-scraped comp=832 saison=18; 173 participants, 839 results; 40 duplicates deduped |
| Saratoga Cup & NEST #1 (2023) | 118 | `scrape_shorttracklive.py` | ✅ web-scraped comp=844 saison=18; 101 participants, 312 results |
| 2023 Bay State Championships | 122 | `parse_ocr_all_races.py` | ✅ font-garbled; OCR; Group A heats fixed via race map |
| 105th Chicago Silver Skates | 117 | `parse_ohio_heartland.py` | ✅ per-page title format; no skater list |
| 2023 Franklin Park Barrel Buster | 121 | `parse_all_races_format.py` | ✅ 39 Group A heats imputed via race-number mapping; events 1-4 (pages 50-52) inserted manually; bulk round_type normalization applied to all events |
| 2023-2024 Desert Classic Short Track | 114 | `parse_stdc.py` | ✅ classification only (no results section); SCHEDULE_MISSING warnings expected |
| 2023 Great Lakes ST Championships & Heartland #2 | 119 | `parse_ohio_heartland.py` | ✅ per-page title (Variant B); no skater list; no distance classification |
| 2023 Park Ridge Open | 120 | `parse_all_races_format.py` | ✅ Group A heats auto-filled (27 races); 155 PDF duplicates removed |
| 99th Annual St. Louis Silver Skate | 134 | `parse_llm_v2.py` | ✅ Font-garbled text PDF (28 pages via LLM vision); results-only (no classification); 478 results; 7 board-game divisions (CandyLand/Chutes and Ladders/Monopoly/Pictionary/Risk/Scrabble/Yahtzee, all "M" suffix); 3 fixes: 19 standalone "M" rows reassigned to correct divisions by bib cross-match (Scrabble×7, Risk×7, Chutes and Ladders×5); 167 "...Men" → "...M" normalized for consistency; NO_CLASSIFICATION INFO expected |
| 2023 Rock'n'Roll Invitational & Heartland #4 | 126 | `parse_all_races_format.py` | ✅ Group A heats auto-filled (24 races); 2-line split section headers handled; 132 PDF duplicates removed |
| 2024 USS AGN Championships Short Track | 138 | `parse_all_races_format.py` | ✅ Group A heats auto-filled (96 races); 3 rounds per distance for large divisions; "Group B Picking" also present |
| 2024 MASA Short Track Championships & NEST #5 | 135 | `parse_all_races_format.py` | ✅ `^` font artifact stripped from 260 names; 259 PDF duplicates removed |
| 2024 Empire State Games Short Track | 131 | `scrape_shorttracklive.py` | ✅ web-scraped comp=865 saison=18; 73 participants, 500 results |
| 2024 NorthBurke Short Track Open | 132 | `parse_all_races_format.py` | ✅ Group A heats auto-filled (45 races); 230 PDF duplicates removed; race 3702 single-bib manually assigned to Raging Bull 500m |
| 2024 Presidential Cup | 133 | `parse_ohio_heartland.py` | ✅ per-page title (Variant B); 3 rounds per distance (Quarter-Finals/Semi-Finals/Finals); added Quarter-Finals to PAGE_HDR_RE; 27 PDF duplicates removed |
| 2023-2024 US Championships Short Track & YOGQ | 115 | `parse_all_races_format.py` | ✅ Group A heats auto-filled (66 races); 343 PDF duplicates removed |
| 2024 US Junior Championships & Winter WCQ Short Track | 127 | `parse_all_races_format.py` | ✅ Group A heats auto-filled (83 races); 430 PDF duplicates removed (section header printed twice on p78) |
| 2024 Land of Lincoln | 136 | `parse_all_races_format.py` | ✅ Group A heats auto-filled (81 races); 3 skater names fixed (status `*Junior E, Pony B` with comma leaked into name field); 8 skaters withdrew |
| 2024 January Thaw & NEST #3 | 128 | `parse_llm_v2.py` | ✅ Image-based PDF (111 pages, all via LLM vision); 641 results; 2-pass fill using TC CSV (pages 12-23) as bib source; all 11 divisions × 3 distances covered; page-level cache built at `.llm_cache.json` (future re-parses cost $0) |

### 2024-2025 season (complete)

| Event | id | Parser | Notes |
|---|---|---|---|
| 2024 Buffalo Championships & Heartland #1 | 144 | `scrape_shorttracklive.py` | ✅ web-scraped comp=925 saison=19; 183 participants, 573 results; 107m track (dist 62-65); 84 duplicates deduped |
| 2024 Saratoga Cup & NEST #1 | 148 | `scrape_shorttracklive.py` | ✅ web-scraped comp=939 saison=19; 102 participants, 356 results; 10 duplicates deduped |
| 2025 Bay State Championships & NEST #2 | 190 | `parse_all_races_format.py` | ✅ |
| 2024 Bay State Championships & NEST #2 | 152 | `parse_all_races_format.py` | ✅ |
| 2024 Franklin Park Barrel Buster | 147 | `parse_all_races_format.py` | ✅ |
| 2024 Great Lakes ST & Heartland #2 | 149 | `parse_all_races_format.py` | ✅ div+club in skater list |
| 2024 Jeff City (Capital City) | 151 | `parse_all_races_format.py` | ✅ 611m local dist |
| 2024 Ohio Invitational & Heartland #3 | 153 | `parse_ohio_heartland.py` | ✅ 425m added |
| 2024 Park Ridge Open | 146 | `parse_all_races_format.py` | ✅ |
| 2024 Robby Kaufman ST Challenge | 155 | `parse_all_races_format.py` | ✅ 440/595m; G1/F2 status codes |
| 108th Annual Chicago Silver Skates | 145 | `parse_llm_v2.py` | ✅ Image-based PDF (85 pages via LLM vision); 189 overall class rows, 212 distance class rows, 327 results; 8 candy-themed divisions (Bit of Honey/Chuckles/Dots/Good & Plenty/Jolly Ranchers/Kit Kats/Laffy Taffy/Skittles); 3 SQL fixes: 7 "Skittles' 111" classification rows had dist=777 misread (corrected to 111m), 8 races 301-302 null-division rows → "Bit of Honey's 777", 6 races 401-402 null-division rows → "Good & Plenty's 333"; 26 SCHEDULE_MISSING warnings expected (LLM naming inconsistencies) |
| 2024 Utah Olympic Oval Fall Challenge | 142 | `parse_llm_v2.py` | ✅ Image-based PDF (42 pages via LLM vision); 142 overall class rows, 110 distance class rows, 250 results; 3 divisions (Challenge A/B/C); 2 Group A heats filled automatically (races 101-102 → Challenge A's 500m); 1 SQL fix: 5 null-division rows for race 201 set to "Challenge B's 333" (fill tied between 3 Challenge B distances; resolved from dist=333 already set by LLM); 4 SCHEDULE_MISSING warnings expected (naming inconsistencies — data present under variant names) |
| 2025 Pacific Northwest Short Track Open | 162 | `parse_llm_v2.py` | ✅ Image-based PDF (39 pages via LLM vision); 33 overall class rows, 72 distance class rows, 200 results; 6 divisions (Division 1–6); non-standard distances 440m and 595m (already in KNOWN_DISTANCES); 10 SCHEDULE_MISSING warnings expected (LLM naming inconsistency: "Division X's" in classification vs "Division X" in results — data present) |
| 2024 Rock N Roll Invitational | 156 | `parse_all_races_format.py` | ✅ |
| 2024 STDC Protocol (classification only) | 141 | `parse_stdc.py` | ✅ no results in PDF |
| 2025 100th St. Louis Silver Skates | 173 | `parse_all_races_format.py` | ✅ |
| 2025 Badger International & Heartland #7 | 174 | `parse_all_races_format.py` | ✅ two-competition PDF |
| 2025 Gateway Championships | 160 | `parse_all_races_format.py` | ✅ 2 manual corrections |
| 2025 Land of Lincoln | 170 | `parse_all_races_format.py` | ✅ |
| 2025 MAST Championships & NEST #5 | 168 | `parse_all_races_format.py` | ✅ |
| 2025 Michigan State ST Championships | 171 | `parse_ohio_heartland.py` | ✅ no skater list |
| 2025 Empires States Short Track Championship & Heartland | 164 | `scrape_shorttracklive.py` | ✅ web-scraped comp=959 saison=19; 92 participants, 639 results |
| 2025 NorthBurke Short Track Open | 165 | `parse_all_races_format.py` | ✅ |
| 2025 Ohio State Short Track Meet | 167 | `parse_ohio_heartland.py` | ✅ 435m added |
| 2025 Oval Winter Challenge | 157 | `parse_ocr_all_races.py` | ✅ results-only PDF |
| 2025 Presidential Cup & NEST #4 | 166 | `parse_all_races_format.py` | ✅ |
| 2025 USS Age Group Nationals & Jersey Challenge | 175 | both parsers | ✅ AGN + appended Jersey Challenge |
| 2025 US Junior ST Championships | 158 | `parse_all_races_format.py` | ✅ H/Q/S/F 4-col dist |
| 2025 US ST Championships | 143 | `parse_all_races_format.py` | ✅ |
| 2025 Washington State ST Championships | 176 | `parse_all_races_format.py` | ✅ |
| 2025 WSA Gold Cup | 163 | — | ⛔ long track; data removed |
| 2025 Desert Classic Short Track | 179 | `parse_stdc.py` | ✅ |
| 2025 January Thaw & NEST #3 | 159 | `parse_all_races_format.py` | ✅ |
