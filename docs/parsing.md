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

### 2022-2023 season (complete — USS protocol format introduced mid-season)

| Event | id | Parser | Notes |
|---|---|---|---|
| 2023 Empire State Games Short Track | 104 | `scrape_shorttracklive.py` | ✅ web-scraped comp=785 saison=17; 55 participants, 380 results |
| 2022 Buffalo Short Track Championships | 91 | `scrape_shorttracklive.py` | ✅ web-scraped comp=735 saison=17; 132 participants, 909 results; dist=57 (111m); 1 TIME_IMPOSSIBLE (LIPPA 1500m→99.8s, source error on website) |
| 2022 Saratoga Cup Short Track Championship | 95 | `scrape_shorttracklive.py` | ✅ web-scraped comp=746 saison=17; 94 participants, 457 results |
| 2023 Bay State Championships & NEST #2 | 96 | `parse_ohio_heartland.py` | ✅ Variant B; Group A Picking heats filled via TC fallback (no dist class); TC `--end-page 23` |
| UOO Winter Challenge | 99 | `parse_all_races_format.py` | ✅ Classification-only; TC `--start-page 44 --end-page 58`; page 7 image-based (dist class partially missing) |
| 2023 US Championships & Junior Championships Short Track | 100 | `parse_all_races_format.py` | ✅ Classification-only; page 8 image (Women overall OCR inserted manually); TC `--end-page 7` |
| 2023 Ohio State Championships | 108 | `parse_ohio_heartland.py` | ✅ Variant B; TC `--start-page 43` (no TC keyword in PDF); 1 TC source typo (Thatcher 1000m) |
| 2023 Middle Atlantic Short Track Championships | 110 | `parse_all_races_format.py` | ✅ Classification-only; TC section "TIME CLASSIFICATION" all-caps, cross-page; 6 DNS bibs absent from TC |
| 2023 US Age Group Nationals Short Track | 112 | `parse_all_races_format.py` | ✅ Classification-only; distance→overall order per division; TC `--start-page 44 --end-page 58` (no keyword); 347/347 TC exact matches |

### 2025-2026 season (complete)

| Event | id | Parser | Notes |
|---|---|---|---|
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
