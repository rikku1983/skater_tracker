# Data Quality — Observations and Decisions

## Time Flags

| Flag | Condition | Meaning |
|---|---|---|
| `TIME_IMPOSSIBLE` | time < WR floor for that distance | Physically impossible; almost certainly a parse error |
| `TIME_LIKELY_MISLABELED_DIST` | 1500 m result with time < 120 s | Time looks like a shorter distance stored under the wrong distance label |

No floors are defined for 85 m-track distances (85/170/255/340 m). Very slow times there (e.g. 90 s for 170 m) are normal for very young beginners or skaters fell.

## Club / Affiliation Normalization

`data/club_affiliations.csv` is the canonical mapping from raw PDF values to clean affiliations.

Columns: `raw_value, count, type, canonical, country, notes`

Types:
- `club` — a named speed skating club; `canonical` = Club.name in the DB
- `country` — ISO country code; `canonical` = country code (e.g. "USA", "CAN")
- `direct` — unaffiliated individual; `canonical` = "Direct"
- `junk` — leaked timing values, DNS/DNF codes, bare numbers; `canonical` = "" → stored as NULL

This file is loaded lazily at parse time by `src/db/load.py:_normalise_affiliation()`. Future parses automatically use the cleaned values.

## Skater Deduplication

Approximately **1,066 duplicate skater pairs** were merged in prior sessions using `scripts/merge_skater_pairs.py`.

Common duplicate causes:
- Transliteration variants (Sofia / Sofya / Sophia)
- Initial-only first name ("S. Koons" vs "Sofya Koons")
- Chinese/Korean name-order reversal ("ZHANG Dylan" vs "Dylan Zhang")
- Minor typos (extra letter, transposed characters)

Merge rules applied:
- `id_a` is kept as canonical; `id_b` is deleted
- Results re-pointed from `id_b` → `id_a`; same-race duplicates dropped
- If `id_a` has an initial-only first name and `id_b` has the full name, `id_a`'s name is updated to the full version
- Null fields on `id_a` (club, gender, birth_year) filled from `id_b`

Review files:
- `data/skater_candidates.csv` — HIGH confidence pairs, all merged
- `data/skater_review.csv` — MEDIUM/LOW pairs, user-reviewed

## Known Distance Mislabeling Issues

### 1500 m → 1000 m (tempus_results)
Parser put some 1000 m race results under 1500 m races. Symptom: `TIME_LIKELY_MISLABELED_DIST` flag on 1500 m results. Fix applied via `scripts/fix_tempus_results.py` (Step 2b).

### 1000 m → 500 m (tempus_results)
A small number of 500 m race results were stored under 1000 m races. Symptom: `TIME_IMPOSSIBLE` on 1000 m results with times 60–72 s. Fix applied via `scripts/fix_tempus_results.py` (Step 2c).

### Misattributed rounds to wrong division (tempus_results)
Some races ended up under the wrong division entirely (e.g. DIVISION 1 Heartland 1500 m SUPER FINAL stored as DIVISION 12 Bulls 170 m). This happens because a round header from one column was assigned to race rows from the adjacent column. These require manual correction or a re-parse with column-aware extraction.

### "4. Laps" stored as 222 m instead of 340 m
The parser converted "4. Laps" → 222 m (2 × 111 m), but on the 85 m track this should be 340 m (4 × 85 m). Any race labeled "4. Laps" in the results section of a youth-division event is 340 m.

## "0:MM.SSS" Prefix Fix — UNVERIFIED

`scripts/fix_tempus_results.py` (Step 2a) added 60 seconds to ~269 `tempus_results` results that had `TIME_IMPOSSIBLE` and `time_text` starting with "0:". The assumption was that the PDF dropped the leading "1" from "1:MM.SSS".

**This assumption has not been manually verified against source PDFs.** When the parser is rewritten, do not carry this fix forward without verification. Instead:
1. Flag any result with `time_text LIKE '0:%'` as `TIME_SUSPICIOUS`
2. Output them for manual review
3. Apply correction only after the PDF confirms the actual time

## Track Type Classification

`Event.track_type` values: `"short"`, `"long"`, `"inline"`, `"mixed"`, `NULL` (unknown).

Classification heuristics (applied in order):
1. Division has any 777 m / 85 m-track race → `"short"`
2. Event name contains "short track" → `"short"`
3. Event name contains "long track" → `"long"`
4. Event name contains "inline" → `"inline"`
5. Has any 5000 m / 10000 m race → `"long"`
6. Otherwise → `NULL` (unknown; treat as short for filtering)

Data quality flags and comparisons should filter to `track_type IN ('short', NULL)` to exclude confirmed long-track and inline events.
