# Short Track Results PDF Formats

## Document Structure (All Formats)

Most results PDF would two major sections. Some might have only 1 section. Some PDF might also have skater roster or skater group sections listing all enrolled skaters.

### 1. Classification Section

Summary tables — **not** raw race data. Two sub-types:

- **Overall classification**: Final division rankings across all distances combined. Shows rank, bib, name, affiliation, points/scores per distance, cumulative ranking.
- **Distance classification**: Rankings per division per distance. Shows rank, position within final group (A/B/C), bib, name, affiliation, best time for that distance.

These are derived summaries. Do not parse race-level data from them.

### 2. Results Section

Individual race tables. Most pdf have one table per race heat/final group but some might have everything in same table.

Each race block starts with a **round header** with possible format as below

```
DIVISION_NAME  ROUND_LABEL  DISTANCE [LAP_COUNT]
```

Examples:
- `DIVISION 12 Bulls HEATS 340 m 4 lap`
- `NOVICE A FINAL 500 m 4.5 lap`
- `DIVISION 1 Heartland Men A SUPER FINAL 1500 m`

Then a table of result rows with possible following columns:
```
rank  bib  name  affiliation  time  status [points]
```

Multiple heats of the same round appear consecutively. The division+distance context is set by the **most recently seen round header** and must be carried forward as state.

---

## Known PDF Formats

| Format key | Software | Layout |
|---|---|---|
| `tempus_results` | SpeedSkating Pro / Tempus | 2-column, dense |
| `tempus` | Older Tempus | 1 division per page |
| `tempus_all_races` | Tempus all-races export | 1 row per heat |
| `tempus_races` | Tempus races only | No classification section |
| `classic_results` | Custom legacy (Heartland/NEST) | Different column order |
| `llm` | (LLM-parsed) | Arbitrary / image-based |

---

## `tempus_results` — Most Common, Most Problematic

### Layout

Pages use a **two-column layout**: left column fills top-to-bottom, then right column. `pdfplumber.extract_text()` interleaves the columns as it reads left-to-right, so the extracted text flow is:

```
[left col line 1]  [right col line 1]
[left col line 2]  [right col line 2]
...
```

This means a round header from the **right column** appears in the extracted text adjacent to race rows from the **left column**. The parser must not assume that a header and the race rows it governs are vertically adjacent in the extracted string.

### Race numbering

Race numbers (e.g. "RACE: 216") are **globally sequential** across the entire event — Race 1 through Race 300+. The number carries no division or distance information on its own. The distance/division context comes exclusively from the round header.

### "N. Laps" notation

Distances are sometimes written as lap counts rather than meters:

| PDF text | Distance |
|---|---|
| `2 lap` / `2. Laps` | 222 m (111 m track) |
| `3 lap` / `3. Laps` | 333 m (111 m track) |
| `4 lap` / `4. Laps` | 340 m (85 m track) — NOT 444 m |
| `4.5 lap` / `4.5 Laps` | 500 m (111 m track) |
| `7 lap` / `7. Laps` | 777 m (111 m track) |

The correct conversion depends on which track type the event uses. When in doubt: if the division name contains "85m" or races are in the 85/170/255/340 m range, use the 85 m track.

### Section boundary

The classification section and results section are separated by a page break or a clear heading change. Both sections can span many pages. The parser must detect which section it is in before attempting to extract race data.

---

## Known Parser Failure Modes

### Wrong distance from mismatched header (most common)
**Cause:** In two-column layout, a round header from the right column is extracted adjacent to race rows from the left column. The parser sees the header first and assigns it to the wrong race block.

**Fix:** Use `pdfplumber`'s word-coordinate extraction (`extract_words()`) to separate left and right columns by x-position before parsing. Process each column independently top-to-bottom.

### Cross-page section context
**Cause:** A round header on page N sets the division+distance context, but the race rows continue on page N+1. If the parser resets state at page boundaries, the rows on page N+1 lose their context.

**Fix:** Carry division+distance state across page boundaries within the results section.

### "85m" in division name mistaken for distance
**Cause:** Division name like "Division 9 Saddle 85m" — the "85m" is a class label, not the race distance.

**Fix:** Never extract the distance from the division name. Distance always comes from the round header's explicit distance field.

### "0:MM.SSS" time format
**Cause:** Some tempus_results PDFs store times as "0:53.265" when the actual time is either genuinely sub-60 seconds or possibly "1:53.265" with a dropped digit. This is **unverified** — do not auto-correct.

**Fix:** Flag these as `TIME_SUSPICIOUS` and require manual verification before applying any correction.

### Distance "4. Laps" → wrong distance
**Cause:** Parser converts "4. Laps" to 444 m (4 × 111 m) instead of 340 m (4 × 85 m).

**Fix:** Use 340 m for all "4. Laps" labels. The 85 m-track four-lap race (340 m) is the only four-lap distance used in US short track.

### Overall/distance classification rows parsed as race results
**Cause:** Classification tables and results tables have similar column structures.

**Fix:** Detect section boundaries (classification vs. results) at the page or heading level before parsing rows.

---

## `pdfplumber` Usage Notes

- `page.extract_text()` — good for single-column or when layout doesn't matter; unreliable for two-column because of interleaving.
- `page.extract_words()` — returns each word with its bounding box `(x0, top, x1, bottom)`. Use `x0` to split left/right columns at the page midpoint.
- `page.extract_table()` — works well when the PDF has actual table borders; falls back gracefully.
- `page.extract_tables()` — returns multiple tables per page; useful when a page has several race blocks.

Recommended approach for `tempus_results`: use `extract_words()`, split at x ≈ page_width / 2, reconstruct lines per column, then apply the state-machine parser to each column independently.
