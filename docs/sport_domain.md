# Short Track Speed Skating — Domain Reference

## Track Types (US)

Two oval sizes are used in US competitions:

| Track | Lap length | Users |
|---|---|---|
| Standard | 111.12 m | General / competitive / international |
| Youth | 85 m | Young beginners (typically age 8 and under) |

## Distances and Lap Counts

### 111 m track

| Distance | Laps |
|---|---|
| 111 m | 1 |
| 222 m | 2 |
| 333 m | 3 |
| 444 m | 4 |
| 500 m | 4.5 |
| 777 m | 7 |
| 1000 m | ~9 |
| 1500 m | ~13.5 |

777 m is a short-track-only distance. Its presence in a results file definitively identifies the event as short track (not long track or inline).

### 85 m track

Integer and half-integer lap counts are both used, especially for younger divisions:

| Distance | Laps |
|---|---|
| 43 m | 0.5 |
| 85 m | 1 |
| 128 m | 1.5 |
| 170 m | 2 |
| 212–214 m | 2.5 |
| 255 m | 3 |
| 340 m | 4 |
| 425–428 m | 5 |

212 m and 214 m (and 425 m vs 428 m) are the same nominal distance — minor rounding differences seen across events reflect the same 2.5-lap (or 5-lap) race on an 85 m track.

**No 444 m distance exists on the 85 m track.** When a PDF shows "4. Laps" for a youth division, that is 340 m (4 × 85 m), not 444 m.

### 55 m track (very young beginners)

Some venues use an even smaller oval, approximately 55 m per lap:

| Distance | Laps |
|---|---|
| 55 m | 1 |
| 111 m | 2 |
| 166 m | 3 |

Note: 111 m at 2 laps of a 55 m track is numerically the same as 1 lap of the standard 111 m track — context (event, division) determines which applies.

### Other small-track distances

Some host venues use non-standard oval sizes. The distances seen in practice:

| Distance | Likely track | Laps |
|---|---|---|
| 107 m | ~107 m | 1 |
| 214 m | ~107 m | 2 |
| 267 m | ~107 m | 2.5 |
| 428 m | ~107 m | 4 |

These appear in certain Midwest youth events (e.g. Great Lakes Championships Toledo division). The exact track circumference may vary slightly; the lap counts are approximate.

## Competition Structure

### Divisions

- Skaters are grouped into **ability-based divisions**. Division names vary by event and host club — do not make assumptions from division names alone.
- "85 m" appearing in a division name (e.g. "Division 9 Saddle 85m") is the class/track-size label, **not** the race distance. Races within that division use the normal distance schedule.

### USS Age Group Classifications

Standard US Speedskating age groups for short track. Division names in PDFs often include these labels (e.g. "Junior D Mixed", "Junior D Women").

| Group | Age span |
|---|---|
| Junior A | 17–18 |
| Junior B | 15–16 |
| Junior C | 13–14 |
| Junior D | 11–12 |
| Junior E | 9–10 |
| Junior F | 7–8 |

**Age cutoff: July 1.** A skater's group for a given season is determined by their age on the **July 1 preceding the season start**. For example, a skater who turns 13 on August 20, 2024 was still 12 on July 1, 2024 → placed in Junior D (11–12) for the 2024–2025 season. They move to Junior C the following season.

Season → cutoff date mapping:
- 2024–2025 season → July 1, 2024
- 2023–2024 season → July 1, 2023
- etc.

Older categories (Open, Master, Senior) have no fixed age ceiling. Younger beginners (age 7 and under, i.e. under Junior F) may race on the 85 m or 55 m track under local club names ("Tiny Tot", "Pee Wee", "Pony", etc.) with no standard USS mapping.

### Race format within a division

Each division races **multiple distances** at a single event (e.g. 500 m, 1000 m, 1500 m; or 333 m, 222 m, 500 m on an 85 m track). Some events run a distance twice (e.g. 500 m appears as both an early and late distance).

Within each distance, races progress through rounds:

```
HEATS  →  FINAL A / B / C / …  →  SUPER FINAL  (optional)
```

- Each individual race holds **5–7 skaters maximum** (fewer for longer distances).
- Multiple races run in parallel within a round, each with a separate heat number.

### Advancement (picking format)

"Picking N+(M)" means: top **N** skaters from **each** heat advance, plus the **M** fastest remaining skaters across all heats (ranked by time). For example, "Picking 2+(1)" = top 2 per heat + fastest third overall → Final A.

## Status Codes in Race Results

| Code | Meaning |
|---|---|
| `Q` | Qualified / advanced to next round |
| `DNF` | Did not finish |
| `DNS` | Did not start |
| `PEN` | Penalized (time penalty or position penalty) |
| `DQ` / `DSQ` | Disqualified |
| `ADV` | Advanced without needing to race |
| `PB` | Personal best (informational annotation) |

## World-Record Impossibility Floors

Used for data quality flagging (`TIME_IMPOSSIBLE`). A result faster than the floor is physically impossible and indicates a parsing error. Floors are set just below the men's world record so no legitimate elite time is ever flagged.

| Distance | Floor (seconds) | Men's WR | Women's WR |
|---|---|---|---|
| 500 m | 39.0 | 39.584 — Wu Dajing (CHN) | 41.416 — Xandra Velzeboer (NED) |
| 777 m | 58.0 | No standard ISU senior WR | No standard ISU senior WR |
| 1000 m | 80.0 | 80.875 (1:20.875) — Hwang Dae-heon (KOR) | 86.514 (1:26.514) — Suzanne Schulting (NED) |
| 1500 m | 125.0 | 127.943 (2:07.943) — Sjinkie Knegt (NED) | 134.35 (2:14.35) — Choi Min-jeong (KOR) |

**No floors are defined for 85 m-track distances** (85/170/255/340 m). Very slow times there are normal for young beginners — do not flag them.

Note: the floors used in `src/db/load.py` (`_WR_FLOORS` dict) should be updated to match these values.

## Skater Affiliations

A skater's affiliation is one of:

- **Club name** — e.g. "Bay State Speedskating", "Pacific Northwest Ice Speed Skating Club"
- **Country code** — ISO 3-letter or 2-letter code (e.g. "USA", "CAN", "JPN")
- **"Direct"** — unaffiliated individual entry

Raw values in PDFs are messy: `USA-BSS`, `CAN-MRSSC`, bare `BSS`, full club name, country code only, or junk (leaked times, DNS codes). The cleaned mapping lives in `data/club_affiliations.csv`.

Common US clubs:

| Abbreviation | Full name | Region |
|---|---|---|
| BSS | Bay State Speedskating | Massachusetts |
| GSSC | Garden State Speed Skating Club | New Jersey |
| PTSC | Potomac Short Track Speedskating Club | DC/Virginia |
| PSSP | Pacific Northwest Ice Speed Skating Club | California |
| EPSSC | Eastern Pennsylvania Speed Skating Club | Philadelphia |
| OSC / OSSC | Oval Speed Skating Club | Colorado |
| SCSSC | Southern California Speed Skating Club | California |
| NCSA | Northern California Speed Skating Association | California |
| LWSSC | Lake Washington Speed Skating Club | Washington |
| NBSSC | North Bay Speed Skating Club | California |
| BSC | Bay State Speedskating Club | Massachusetts |

Canadian clubs appear regularly at Northeast US events (MRSSC = Montreal, MSSC = Mississauga/Montreal, NSSC = Niagara/Nova Scotia).
