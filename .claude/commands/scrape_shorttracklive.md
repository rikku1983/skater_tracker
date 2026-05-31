# Scrape shorttracklive.info

Scrape a competition from shorttracklive.info into `data/skater_tracker_round2.db`.

## Usage
```
/scrape_shorttracklive <event-url>
```

Pass any page URL for the competition, e.g.:
`https://www.shorttracklive.info/index.php?comp=1004&m=0&saison=20`

The scraper extracts `comp` and `saison` from the URL automatically,
discovers all divisions from the schedule page, and matches the event in the DB by name.

---

## Step 1 — Run the scraper

```bash
source .venv/bin/activate && python3 scripts/scrape_shorttracklive.py \
  --url "<paste URL here>"
```

The scraper will:
1. Parse `comp` and `saison` from the URL
2. Fetch the m=6 schedule page to get the event name and division list
3. Match the event name against DB events automatically
4. Fetch participants (m=9) and results (m=2) for every division × distance
5. Write SkaterEntry and Result rows to the DB

**If multiple DB candidates are found**, the scraper prints them and exits — add `--event-id N` to select.

**First run:** fetches live from server (~72 requests, 0.5s delay each ≈ 36s).  
**All subsequent `--force` runs:** replayed from `data/scrape_cache/shorttracklive_<comp>_<saison>.json` — zero network requests.  
Use `--no-cache` only if you need fresh data (e.g. live event still in progress).

---

## Step 2 — Run QC

```bash
source .venv/bin/activate && python3 scripts/check_data_quality_v2.py --event-id <id>
```

**Expected for web-scraped events:**
- `NO_CLASSIFICATION` (INFO) — website has no distance classification section; not an error
- Status codes FA/FB/FC/FD (advancement to Final A/B/C/D) are recognized — no action needed

---

## Step 3 — Update docs/parsing.md

Add a row to the appropriate season table:

```
| <Event name> | <id> | `scrape_shorttracklive.py` | ✅ web-scraped comp=<comp> saison=<saison>; <N> participants, <M> results |
```

---

## Known competitions

| Event | comp | saison | event_id |
|---|---|---|---|
| 2025 Buffalo ST Championships & Heartland #1 | 1004 | 20 | 184 |
| 2025 Saratoga Cup & NEST | 1026 | 20 | 187 |
| 2026 Empire State Short Track & Heartland | 1058 | 20 | 201 |
| 2024 Buffalo Championships & Heartland #1 | 925 | 19 | 144 |
| 2024 Saratoga Cup & NEST #1 | 939 | 19 | 148 |
| 2025 Empire State Short Track & Heartland | 959 | 19 | 164 |
| 2023 Buffalo ST Championships & Heartland #1 | 832 | 18 | 116 |
| Saratoga Cup & NEST #1 (Nov 2023) | 844 | 18 | 118 |
| 2024 Empire State Games Short Track | 865 | 18 | 131 |
| 2023 Burpee Pinnacle Championships | 725 | 17 | 88 |
| 2023 Desert Classic Short Track | 727 | 17 | 89 |
| 2023 USS Fall World Cup Qualifier Short Track | 739 | 17 | 90 |
| 104th Silver Skates Meet | 763 | 17 | 92 |
| 2023 Great Lakes ST & Heartland #2 | 740 | 17 | 93 |
| 2023 Park Ridge Open | 757 | 17 | 94 |
| 2023 Franklin Park Barrel Buster | 758 | 17 | 214 |
| 26th Capital City Championships | 759 | 17 | 215 |
| 2023 US Championships & Jr. National Championships | 761 | 17 | 100 |
| 2023 Gateway Short Track Championships | 809 | 17 | 102 |
| 2023 American Dream ST Championship (Feb) | 788 | 17 | 217 |
| 2023 Empire State Games Short Track | 785 | 17 | 104 |
| 2023 NorthBurke Short Track Open | 810 | 17 | 107 |
| 2023 Presidential Cup | 814 | 17 | 106 |
| 98th St. Louis Silver Skates Short Track | 801 | 17 | 109 |
| 2023 US Age Group Nationals Short Track | 815 | 17 | 112 |
| 2022 Buffalo Short Track Championships | 735 | 17 | 91 |
| 2022 Saratoga Cup Short Track Championship | 746 | 17 | 95 |
| 2021 Buffalo Short Track Championships | 639 | 16 | 71 |
| 103rd Chicago Silver Skates (2021) | 665 | 16 | 72 |
| 2021 Great Lakes Short Track | 669 | 16 | 73 |
| 2021 Park Ridge Open | 670 | 16 | 75 |
| 2021 Franklin Park Barrell Buster | 671 | 16 | 76 |
| 2022 Ohio Invitational & Heartland #3 | 676 | 16 | 211 |
| 2021 GSS Holiday Dash | 680 | 16 | 212 |
| 2021 Bay State Championships & NEST #2 | 681 | 16 | 77 |
| 2022 Mini Jan Thaw | 686 | 16 | 213 |
| 2022 US Junior Championships Short Track | 690 | 16 | 80 |
| 2022 U.S. Olympic Team Trials Short Track | 683 | 16 | 78 |
| 2022 MASA ST Championships (MASTC) | 710 | 16 | 83 |
| 2022 Land of Lincoln & Heartland #5 | 711 | 16 | 84 |
| 2022 Michigan State Meet | 712 | 16 | 85 |
| 2022 US Age Group Nationals Short Track | 701 | 16 | 86 |
| 2021 Saratoga Cup & N.E.S.T. #1 | 662 | 16 | 74 |

---

## Quick reference: URL parameters

| m= | Content |
|---|---|
| m=6 | Schedule — event name, all divisions, race order |
| m=9 | Participants — bib, name, club (all divisions, one page) |
| m=1 | Division overview — links to m=2 result pages per distance |
| m=2 | Results — one page per division × distance; requires `dist=` and `ord=` |
| m=8 | Time analysis — cross-division best times per distance |

**dist → distance:** `14`=222m, `16`=333m, `17`=500m, `19`=1000m, `21`=1500m, `58`=777m, `70`=340m (4×85m), `79`=777m (7×111m)
