# Skater Tracker

A database and web application for US Short Track Speedskating results. Parses competition PDFs published by USA Skating, loads them into a SQLite database, and exposes a Streamlit web interface for browsing, searching, and comparing skaters.

## Features

- **PDF parsing** — supports 6 result formats (Tempus, All Races, Tempus Results, Tempus Races, Speedskating Pro, Classic Results)
- **Skater deduplication** — normalizes names and merges duplicate records across events
- **Club enrichment** — links skaters to canonical club records with abbreviations
- **Gender inference** — from division names, per-row codes, registry CSV, and name markers
- **Web app** — browse events, search skaters, view trajectories, compare skaters side by side

## Project Structure

```
app/
  streamlit_app.py        # Streamlit web application
data/
  skater_tracker.db       # SQLite database (git-ignored)
  usa_skaters.csv         # USA Skating registry for enrichment
  clubs_with_normalized_names_and_abbrevs.csv
src/
  db/
    models.py             # SQLAlchemy models (Event, Race, Skater, Club, Result)
    load.py               # Loads parsed events into the DB
    session.py            # DB engine / session helpers
  parsers/
    base.py               # Format detection + ParsedResult dataclass
    all_races_parser.py
    classic_results_parser.py
    tempus_parser.py
    tempus_results_parser.py
    tempus_races_parser.py
    speedskating_pro_parser.py
  downloader/             # PDF download utilities
  utils/
    times.py              # Time parsing helpers
scripts/
  download_pdfs.py        # Download result PDFs from USS website
  parse_and_load.py       # Parse PDFs and load results into DB
  clean_skater_names.py   # Normalize skater names, merge duplicates
  enrich_from_registry.py # Enrich from USA Skating registry CSV
  link_clubs.py           # Link skaters to canonical club records
  apply_deduplication.py  # Apply manual deduplication CSV
  clean_outlier_times.py  # Null/delete physically impossible times
  reload_classic_results.py
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Data Pipeline

Run these in order to build the database from scratch:

```bash
# 1. Download PDFs
python scripts/download_pdfs.py

# 2. Parse and load into DB
python scripts/parse_and_load.py

# 3. Clean skater names and merge duplicates
python scripts/clean_skater_names.py

# 4. Enrich from USA Skating registry (requires data/usa_skaters.csv)
python scripts/enrich_from_registry.py

# 5. Link skaters to canonical clubs
python scripts/link_clubs.py

# 6. Remove outlier times
python scripts/clean_outlier_times.py
```

All scripts support `--dry-run` to preview changes without writing to the database.

## Web App

```bash
streamlit run app/streamlit_app.py
```

Pages:
- **Overview** — event and result counts by season
- **Events** — browse events, drill into races and results
- **Skater Search** — search by name, club, or gender; view history and improvement trajectory
- **Clubs** — browse clubs, view roster and top times
- **Leaderboard** — fastest times by season, distance, division, and gender
- **Compare** — select multiple skaters to compare season-best times and trajectories side by side

## Database Schema

| Table | Description |
|-------|-------------|
| `events` | One row per downloaded PDF |
| `races` | One row per division + distance within an event |
| `skaters` | Deduplicated skater records |
| `clubs` | Canonical club records with abbreviations |
| `results` | One row per skater per race |
