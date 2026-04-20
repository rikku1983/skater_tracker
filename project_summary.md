# Skater Tracker Web App – Project Summary

## Project Goal

Build a local data pipeline and web application that collects, parses, stores, and visualizes speed skating race results from official US Speedskating result PDFs.

The official results site provides downloadable race result documents organized by season and event. These documents will be used as the primary data source for this project. ([US Speedskating][1])

The system will:

1. Automatically download new race result PDFs
2. Parse and normalize the data
3. Store results in a relational database
4. Provide a web interface to explore skater performance

The project will initially run **entirely locally** (local database + local web app).

---

# System Architecture

The project consists of three main layers:

1. Data ingestion
2. Data normalization and storage
3. Web visualization

```
USS Results Website
        │
        ▼
PDF Downloader
        │
        ▼
Local PDF Storage
        │
        ▼
PDF Parsing Pipeline
        │
        ▼
Normalized Database (SQLite)
        │
        ▼
Local Web App
(Streamlit / FastAPI)
```

---

# Technology Stack

## Core

Python will be used for the entire backend pipeline.

Recommended tools:

* Python 3.11+
* SQLite (local database)
* SQLAlchemy ORM
* requests
* beautifulsoup4
* pdfplumber
* camelot (optional)
* pandas

## Web App

First prototype:

* Streamlit

Possible future stack:

* FastAPI backend
* Next.js frontend
* PostgreSQL (Supabase)

---

# Project Components

## 1. PDF Downloader

Purpose:

Automatically download result PDFs from the US Speedskating results website.

Features:

* Manually triggered script
* Detect already downloaded events
* Download only new PDFs
* Store metadata in a manifest database table
* Maintain local event archive

Workflow:

1. Fetch results page
2. Extract event links and PDF URLs
3. Compare with local manifest
4. Download only new files
5. Record metadata in database

Example folder structure:

```
data/
  raw_pdfs/
    2025-2026/
      2026-age-group-nationals.pdf
      2026-midwest-open.pdf
  extracted/
  logs/
  skater_tracker.db
```

Downloader should store:

* event_name
* season
* source_url
* pdf_url
* local_path
* download_timestamp
* checksum

---

# 2. PDF Parsing Pipeline

The PDFs contain human-readable race tables but formats vary slightly between events.

Therefore the parser must be robust and modular.

Recommended libraries:

* pdfplumber (primary)
* camelot (table-based fallback)
* tabula-py (optional)

## Parsing Workflow

For each PDF:

1. Extract event metadata
2. Identify race sections
3. Detect table headers
4. Parse race result rows
5. Normalize data
6. Store structured results
7. Log parsing errors

---

# Parsing Architecture

Use multiple parser classes:

```
BaseParser
│
├── ProtocolParser
├── MeetSummaryParser
└── FallbackTextParser
```

Each parser handles a specific PDF layout.

---

# Database Design

Initial database will use **SQLite**.

Key tables:

## events

```
id
season
event_name
venue
city
state
start_date
end_date
discipline
pdf_path
downloaded_at
```

## races

```
id
event_id
race_name
distance_m
category
gender
round
race_date
page_number
```

## skaters

```
id
full_name
first_name
last_name
normalized_name
club_name
state
```

## results

```
id
race_id
skater_id
bib
lane
rank
time_text
time_seconds
points
status
note
```

Optional later tables:

```
laps
splits
teams
clubs
```

---

# Data Storage Strategy

Maintain three layers of data:

### 1. Source Files

Original PDFs.

### 2. Raw Extraction

Intermediate parsed tables.

### 3. Normalized Database

Clean relational schema used by the web app.

This allows parsers to be improved later without re-downloading PDFs.

---

# Web Application

Initial UI will be built with **Streamlit**.

Goals:

Provide an interface to explore race results.

## MVP Features

### Event Browser

* List all events
* Filter by season
* Open event to see races

### Skater Search

* Search by skater name
* View full race history
* Show summary statistics

### Race Results Page

* View full race results table

### Basic Visualizations

Examples:

* finishing position over time
* race time progression
* participation statistics

---

# Project Folder Structure

```
skater-tracker/
│
├── app/
│   └── streamlit_app.py
│
├── src/
│   ├── downloader/
│   │   ├── discover.py
│   │   └── download.py
│   │
│   ├── parsers/
│   │   ├── base.py
│   │   ├── protocol_parser.py
│   │   ├── summary_parser.py
│   │   └── normalize.py
│   │
│   ├── db/
│   │   ├── models.py
│   │   ├── session.py
│   │   └── load.py
│   │
│   └── utils/
│       ├── names.py
│       ├── times.py
│       └── logging.py 
│
├── data/
│   ├── raw_pdfs/
│   ├── extracted/
│   └── skater_tracker.db
│
├── notebooks/
│   └── parser_debug.ipynb
│
├── tests/
│
└── requirements.txt
```

---

# Development Roadmap

## Phase 1 – Downloader

* Implement event discovery
* Build PDF downloader
* Maintain download manifest

## Phase 2 – Parser Prototype

* Collect 5–10 example PDFs
* Develop first parser
* Extract race tables

## Phase 3 – Database Integration

* Normalize results
* Insert into SQLite database

## Phase 4 – Web App MVP

* Build Streamlit interface
* Event browser
* Skater search
* Race tables

## Phase 5 – Parser Expansion

* Support additional PDF formats
* Improve normalization

## Phase 6 – Advanced Features

Possible future improvements:

* skater identity resolution
* rankings
* performance analytics
* interactive charts
* Supabase/Postgres backend
* public website deployment

---

# Key Design Principles

1. Keep raw PDFs permanently
2. Separate raw extraction from normalized data
3. Make parsers modular
4. Build simple UI first
5. Ensure reproducible ingestion pipeline

---

# Expected Outcome

The project will produce a **local searchable database of speed skating race results** with an interactive web interface for exploring skater performance and race history.

This platform could later expand into:

* national skater statistics
* ranking analytics
* athlete profiles
* public results portal

[1]: https://www.usspeedskating.org/results?utm_source=chatgpt.com "Results"
