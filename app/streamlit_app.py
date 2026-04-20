"""
Skater Tracker — Streamlit web app.

Run with:
    streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import pandas as pd
import streamlit as st
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.db.models import Event, Race, Result, Skater
from src.db.session import get_engine

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Skater Tracker",
    page_icon="⛸",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── DB helpers ────────────────────────────────────────────────────────────────


@st.cache_resource
def _engine():
    return get_engine()


def db() -> Session:
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=_engine())()


@st.cache_data(ttl=300)
def load_overview_stats():
    session = db()
    events = session.query(func.count(Event.id)).filter(Event.is_parseable == True).scalar()
    skaters = session.query(func.count(Skater.id)).scalar()
    results = session.query(func.count(Result.id)).scalar()
    races = session.query(func.count(Race.id)).scalar()
    session.close()
    return events, races, skaters, results


@st.cache_data(ttl=300)
def load_events_df():
    session = db()
    rows = (
        session.query(
            Event.id,
            Event.season,
            Event.event_name,
            Event.event_date,
            Event.venue,
            func.count(Race.id).label("races"),
        )
        .outerjoin(Race, Race.event_id == Event.id)
        .filter(Event.is_parseable == True)
        .group_by(Event.id)
        .order_by(Event.season, Event.event_date)
        .all()
    )
    session.close()
    return pd.DataFrame(rows, columns=["id", "season", "event_name", "event_date", "venue", "races"])


@st.cache_data(ttl=300)
def load_races_for_event(event_id: int):
    session = db()
    rows = (
        session.query(
            Race.id,
            Race.division,
            Race.distance_m,
            func.count(Result.id).label("results"),
        )
        .outerjoin(Result, Result.race_id == Race.id)
        .filter(Race.event_id == event_id)
        .group_by(Race.id)
        .order_by(Race.distance_m, Race.division)
        .all()
    )
    session.close()
    return pd.DataFrame(rows, columns=["id", "division", "distance_m", "results"])


@st.cache_data(ttl=300)
def load_results_for_race(race_id: int):
    session = db()
    rows = (
        session.query(
            Result.rank,
            Result.bib,
            Skater.full_name,
            Skater.club_name,
            Result.heat_assignment,
            Result.time_text,
            Result.time_seconds,
            Result.status,
            Skater.id.label("skater_id"),
        )
        .join(Skater, Result.skater_id == Skater.id)
        .filter(Result.race_id == race_id)
        .order_by(Result.rank)
        .all()
    )
    session.close()
    return pd.DataFrame(
        rows,
        columns=["rank", "bib", "name", "club", "heat", "time", "time_s", "status", "skater_id"],
    )


@st.cache_data(ttl=300)
def search_skaters(query: str):
    if not query.strip():
        return pd.DataFrame(columns=["id", "full_name", "club_name", "results"])
    import re

    q = re.sub(r"[^a-z0-9 ]", "", query.lower()).strip()
    session = db()
    rows = (
        session.query(
            Skater.id,
            Skater.full_name,
            Skater.club_name,
            func.count(Result.id).label("results"),
        )
        .outerjoin(Result, Result.skater_id == Skater.id)
        .filter(Skater.normalized_name.contains(q))
        .group_by(Skater.id)
        .order_by(func.count(Result.id).desc())
        .limit(50)
        .all()
    )
    session.close()
    return pd.DataFrame(rows, columns=["id", "full_name", "club_name", "results"])


@st.cache_data(ttl=300)
def load_skater_results(skater_id: int):
    session = db()
    rows = (
        session.query(
            Event.season,
            Event.event_name,
            Event.event_date,
            Race.division,
            Race.distance_m,
            Result.rank,
            Result.heat_assignment,
            Result.time_text,
            Result.status,
        )
        .join(Race, Result.race_id == Race.id)
        .join(Event, Race.event_id == Event.id)
        .filter(Result.skater_id == skater_id)
        .order_by(Event.event_date, Race.distance_m)
        .all()
    )
    session.close()
    return pd.DataFrame(
        rows,
        columns=["season", "event", "date", "division", "distance_m", "rank", "heat", "time", "status"],
    )


@st.cache_data(ttl=300)
def load_seasons():
    session = db()
    seasons = (
        session.query(Event.season)
        .filter(Event.is_parseable == True)
        .distinct()
        .order_by(Event.season.desc())
        .all()
    )
    session.close()
    return [s[0] for s in seasons]


# ── Sidebar navigation ────────────────────────────────────────────────────────

st.sidebar.title("⛸ Skater Tracker")
page = st.sidebar.radio(
    "Navigate",
    ["Overview", "Events", "Skater Search", "Leaderboard"],
    label_visibility="collapsed",
)

# ── Page: Overview ────────────────────────────────────────────────────────────

if page == "Overview":
    st.title("⛸ Skater Tracker")
    st.caption("US Short Track Speedskating results database")

    events, races, skaters, results = load_overview_stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Events", f"{events:,}")
    c2.metric("Races", f"{races:,}")
    c3.metric("Skaters", f"{skaters:,}")
    c4.metric("Results", f"{results:,}")

    st.divider()

    events_df = load_events_df()
    by_season = (
        events_df.groupby("season")
        .agg(events=("id", "count"), races=("races", "sum"))
        .reset_index()
        .sort_values("season", ascending=False)
    )
    by_season.columns = ["Season", "Events", "Races"]

    st.subheader("Events by Season")
    st.dataframe(by_season, use_container_width=True, hide_index=True)

# ── Page: Events ─────────────────────────────────────────────────────────────

elif page == "Events":
    st.title("Events")

    seasons = load_seasons()
    selected_season = st.selectbox("Season", ["All"] + seasons)

    events_df = load_events_df()
    if selected_season != "All":
        events_df = events_df[events_df["season"] == selected_season]

    # Display events table
    display_df = events_df[["season", "event_name", "event_date", "venue", "races"]].copy()
    display_df.columns = ["Season", "Event", "Date", "Venue", "Races"]
    display_df["Date"] = pd.to_datetime(display_df["Date"]).dt.strftime("%Y-%m-%d").where(
        display_df["Date"].notna(), ""
    )

    st.write(f"**{len(events_df)} events**")

    # Use selectbox for choosing an event to drill into
    event_options = {
        f"{row['event_name']} ({row['season']})": row["id"]
        for _, row in events_df.iterrows()
    }

    col_table, col_detail = st.columns([2, 3])

    with col_table:
        st.dataframe(display_df, use_container_width=True, hide_index=True, height=400)

    with col_detail:
        chosen_label = st.selectbox(
            "Select an event to see races",
            options=list(event_options.keys()),
            index=None,
            placeholder="Choose an event…",
        )
        if chosen_label:
            event_id = event_options[chosen_label]
            races_df = load_races_for_event(event_id)

            if races_df.empty:
                st.info("No races found for this event.")
            else:
                st.write(f"**{len(races_df)} races**")

                race_options = {
                    f"{int(row['distance_m']) if row['distance_m'] else '?'}m — {row['division']} ({row['results']} results)": row["id"]
                    for _, row in races_df.iterrows()
                }

                chosen_race = st.selectbox(
                    "Select a race",
                    options=list(race_options.keys()),
                    index=None,
                    placeholder="Choose a race…",
                )

                if chosen_race:
                    race_id = race_options[chosen_race]
                    results_df = load_results_for_race(race_id)

                    if results_df.empty:
                        st.info("No results found.")
                    else:
                        display_results = results_df[["rank", "bib", "name", "club", "heat", "time", "status"]].copy()
                        display_results.columns = ["Rank", "Bib", "Name", "Club", "Heat", "Time", "Status"]
                        display_results["Rank"] = display_results["Rank"].fillna("").astype(str).str.replace(".0", "", regex=False)
                        st.dataframe(display_results, use_container_width=True, hide_index=True)

# ── Page: Skater Search ───────────────────────────────────────────────────────

elif page == "Skater Search":
    st.title("Skater Search")

    query = st.text_input("Search by name", placeholder="e.g. Smith, John…")

    if query:
        skaters_df = search_skaters(query)

        if skaters_df.empty:
            st.warning("No skaters found.")
        else:
            st.write(f"**{len(skaters_df)} skaters found**")

            skater_options = {
                f"{row['full_name']} ({row['club_name'] or 'no club'}) — {row['results']} results": row["id"]
                for _, row in skaters_df.iterrows()
            }

            chosen = st.selectbox(
                "Select skater",
                options=list(skater_options.keys()),
                index=0 if len(skater_options) == 1 else None,
                placeholder="Choose a skater…",
            )

            if chosen:
                skater_id = skater_options[chosen]
                hist = load_skater_results(skater_id)

                if hist.empty:
                    st.info("No results found.")
                else:
                    st.write(f"**{len(hist)} results across {hist['event'].nunique()} events**")

                    # Summary by distance
                    with st.expander("Personal bests by distance"):
                        timed = hist[hist["time"].notna() & (hist["status"].isna() | (hist["status"] == ""))].copy()
                        if not timed.empty and "time_s" not in timed.columns:
                            pass  # time_s not loaded in this df; skip
                        pbs = (
                            hist[hist["status"].isna() | (hist["status"] == "")]
                            .dropna(subset=["distance_m"])
                            .sort_values("distance_m")
                            .groupby("distance_m")
                            .agg(
                                races=("time", "count"),
                                best_time=("time", "min"),
                                best_rank=("rank", "min"),
                            )
                            .reset_index()
                        )
                        pbs.columns = ["Distance (m)", "Races", "Best Time", "Best Rank"]
                        pbs["Best Rank"] = pbs["Best Rank"].fillna("").astype(str).str.replace(".0", "", regex=False)
                        st.dataframe(pbs, use_container_width=True, hide_index=True)

                    display = hist[["season", "event", "date", "division", "distance_m", "rank", "time", "status"]].copy()
                    display.columns = ["Season", "Event", "Date", "Division", "Dist (m)", "Rank", "Time", "Status"]
                    display["Date"] = pd.to_datetime(display["Date"]).dt.strftime("%Y-%m-%d").where(
                        display["Date"].notna(), ""
                    )
                    display["Rank"] = display["Rank"].fillna("").astype(str).str.replace(".0", "", regex=False)
                    st.dataframe(display, use_container_width=True, hide_index=True)

# ── Page: Leaderboard ─────────────────────────────────────────────────────────

elif page == "Leaderboard":
    st.title("Leaderboard")
    st.caption("Top ranked skaters by distance, filtered by season and division")

    seasons = load_seasons()
    col1, col2, col3 = st.columns(3)

    with col1:
        sel_season = st.selectbox("Season", seasons)
    with col2:
        sel_dist = st.selectbox("Distance (m)", [500, 777, 1000, 1500, 333])
    with col3:
        # Load available divisions for this season+distance
        @st.cache_data(ttl=300)
        def load_divisions(season: str, distance_m: int):
            session = db()
            rows = (
                session.query(Race.division)
                .join(Event, Race.event_id == Event.id)
                .filter(Event.season == season, Race.distance_m == distance_m)
                .distinct()
                .order_by(Race.division)
                .all()
            )
            session.close()
            return [r[0] for r in rows if r[0]]

        divisions = load_divisions(sel_season, sel_dist)
        sel_div = st.selectbox("Division", ["All"] + divisions)

    @st.cache_data(ttl=300)
    def load_leaderboard(season: str, distance_m: int, division: str | None):
        session = db()
        q = (
            session.query(
                Skater.full_name,
                Skater.club_name,
                func.min(Result.time_seconds).label("best_time"),
                func.min(Result.time_text).label("best_time_text"),
                func.count(Result.id).label("races"),
            )
            .join(Result, Result.skater_id == Skater.id)
            .join(Race, Result.race_id == Race.id)
            .join(Event, Race.event_id == Event.id)
            .filter(
                Event.season == season,
                Race.distance_m == distance_m,
                Result.time_seconds.isnot(None),
            )
        )
        if division and division != "All":
            q = q.filter(Race.division == division)
        rows = (
            q.group_by(Skater.id)
            .order_by(func.min(Result.time_seconds))
            .limit(50)
            .all()
        )
        session.close()
        return pd.DataFrame(rows, columns=["name", "club", "best_time_s", "best_time", "races"])

    lb = load_leaderboard(sel_season, sel_dist, sel_div if sel_div != "All" else None)

    if lb.empty:
        st.info("No timed results found for this combination.")
    else:
        lb.insert(0, "Rank", range(1, len(lb) + 1))
        display_lb = lb[["Rank", "name", "club", "best_time", "races"]].copy()
        display_lb.columns = ["Rank", "Name", "Club", "Best Time", "Races"]
        st.dataframe(display_lb, use_container_width=True, hide_index=True)
