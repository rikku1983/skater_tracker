# Skater Tracker

A personal web application for browsing US short track speed skating competition results. Covers events from the 2018–2019 season through the current season.

Built for skaters and parents to track personal bests, compare progress over time, and view leaderboards.

---

## Features

- **Skater profiles** — personal bests by distance, season-best trend charts, full results history
- **Event browser** — results, classification, and time classification for every parsed event
- **Leaderboard** — fastest times by season, distance, gender, and birth year
- **Compare skaters** — season-best or event-best trend charts for multiple skaters side by side
- **Club pages** — roster and best times for each club

## Data Sources

Results are sourced from:
- [U.S. Speed Skating](https://www.usspeedskating.org/) — official PDF results
- [shorttracklive.info](https://www.shorttracklive.info/) — online results for select events

This is a personal, non-commercial tool built for skaters and parents. Not affiliated with or endorsed by U.S. Speed Skating.

---

## Running the Web App Locally

**Requirements:** Node.js 18+, the `data/skater_tracker_round2.db` database file.

```bash
cd frontend
npm install
npm run dev
```

Then open **http://localhost:3000**.

### Pages

| Page | Description |
|---|---|
| **Home** | Overview stats and recent events |
| **Skaters** | Search by name — links to individual profiles |
| **Skater profile** | Personal bests table, trend charts, full results |
| **Events** | Browse all events by season |
| **Event detail** | Results, classification, time classification (3 tabs) |
| **Clubs** | Club list and roster |
| **Leaderboard** | Top 50 times filtered by season, distance, gender, birth year |
| **Compare** | Add multiple skaters, view overlapping trend charts |

---

## Project Structure

```
skater_tracker/
├── frontend/               # Next.js web app (TypeScript + Tailwind)
│   └── src/app/            # Pages and API routes
├── data/
│   ├── skater_tracker_round2.db   # SQLite database
│   ├── pdfs/               # Source PDFs organized by season
│   └── scrape_cache/       # Cached web scrape results
├── scripts/                # Data pipeline scripts
├── src/
│   ├── parsers/            # PDF parsers for each result format
│   └── db/                 # Database models and loader
├── docs/                   # Domain reference and parsing notes
├── pictures/               # Logo and background images
└── .claude/commands/       # Claude AI skills for data pipeline
```

---

## Adding New Event Results

New events can be added using Claude Code with the project skills. The workflow depends on the PDF format.

### Step 0 — Set up the Python environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 1 — Get the PDF

Download the results PDF from the USS website or the event host. Place it in:

```
data/pdfs/<season>/          # e.g. data/pdfs/2025-2026/
```

### Step 2 — Choose the right skill

Open Claude Code in this project folder and use the appropriate slash command:

| PDF type | Skill to use | When to use it |
|---|---|---|
| USS / Tempus protocol format | `/parse_USS_protocol_results` | Standard competition software — most national and regional events |
| Legacy club format (Gateway, Buffalo, Desert Classic) | `/parse_legacy_PDF_results` | Events using older custom results software |
| MeetDirector / Meet Management format | `/parse_Meet_Management_software_formats` | Events using MeetDirector or similar software |

**Not sure which format?** Open the PDF — if it has a "Tempus Competition Software" header or structured heat/final tables, use `/parse_USS_protocol_results`. If it looks like a custom layout with candy-themed division names, use `/parse_legacy_PDF_results`.

### Step 3 — Run the skill

In Claude Code, type the skill command followed by the PDF filename:

```
/parse_USS_protocol_results 2026_Desert_Classic_ST.pdf
```

The skill will:
1. Find or create the event record in the database
2. Identify the PDF format variant
3. Parse all heats, finals, and classification sections
4. Load results into `data/skater_tracker_round2.db`
5. Run data quality checks and report any issues

### Step 4 — Verify

The skill runs QC automatically. Review the output for any flagged issues. Common expected warnings (not errors):
- `BIB_CONSISTENCY: no skater_entries` — normal for legacy formats with no club info
- `NO_CLASSIFICATION` — normal for events with no classification section

### Alternative: Web scraping

Some events are available on [shorttracklive.info](https://www.shorttracklive.info/), which gives cleaner data than PDF parsing. Use:

```
/scrape_shorttracklive
```

See the skill file (`.claude/commands/scrape_shorttracklive.md`) for details on finding the competition IDs.

---

## Skater Normalization

After adding several new events, run the normalization skill to merge any duplicate skater records that may have been created by name variations:

```
/normalize_skaters
```

---

## Tech Stack

- **Frontend:** Next.js 16 (App Router), TypeScript, Tailwind CSS, shadcn/ui, Recharts
- **Database:** SQLite via `better-sqlite3` (read-only at runtime)
- **Data pipeline:** Python, pdfplumber, SQLAlchemy
- **AI skills:** Claude Code with project-level slash commands
