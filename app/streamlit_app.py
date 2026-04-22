"""
Skater Tracker — Streamlit web app.

Run with:
    streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.db.models import Club, Event, Race, Result, Skater
from src.db.session import get_engine

# ── Shared chart constants ────────────────────────────────────────────────────

TRAJ_DISTANCES = [333, 500, 777, 1000, 1500]
_TIME_EXPR = (
    "floor(datum.value / 60) + ':' + "
    "(datum.value % 60 < 10 ? '0' : '') + "
    "format(floor(datum.value % 60), 'd')"
)

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


def _fmt_time(s: float | None) -> str:
    """Format seconds float → 'M:SS.mmm' string."""
    if s is None:
        return ""
    m, rem = divmod(s, 60)
    return f"{int(m)}:{rem:06.3f}" if m else f"{rem:.3f}"


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
def load_clubs_df():
    session = db()
    rows = (
        session.query(
            Club.id,
            Club.name,
            Club.abbreviation,
            func.count(Skater.id).label("skaters"),
        )
        .outerjoin(Skater, Skater.club_id == Club.id)
        .group_by(Club.id)
        .order_by(func.count(Skater.id).desc())
        .all()
    )
    session.close()
    return pd.DataFrame(rows, columns=["id", "name", "abbreviation", "skaters"])


@st.cache_data(ttl=300)
def load_club_roster(club_id: int):
    session = db()
    rows = (
        session.query(
            Skater.id,
            Skater.full_name,
            Skater.gender,
            func.count(Result.id).label("results"),
        )
        .outerjoin(Result, Result.skater_id == Skater.id)
        .filter(Skater.club_id == club_id)
        .group_by(Skater.id)
        .order_by(func.count(Result.id).desc())
        .all()
    )
    session.close()
    return pd.DataFrame(rows, columns=["id", "name", "gender", "results"])


@st.cache_data(ttl=300)
def load_club_top_times(club_id: int):
    session = db()
    rows = (
        session.query(
            Skater.full_name,
            Race.distance_m,
            func.min(Result.time_seconds).label("best_s"),
        )
        .join(Result, Result.skater_id == Skater.id)
        .join(Race, Result.race_id == Race.id)
        .filter(Skater.club_id == club_id, Result.time_seconds.isnot(None))
        .group_by(Skater.id, Race.distance_m)
        .order_by(Race.distance_m, func.min(Result.time_seconds))
        .all()
    )
    session.close()
    df = pd.DataFrame(rows, columns=["name", "distance_m", "best_s"])
    df["best_time"] = df["best_s"].apply(_fmt_time)
    return df


@st.cache_data(ttl=300)
def search_skaters(
    query: str,
    club_id: int | None = None,
    gender: str | None = None,
    club_query: str = "",
):
    import re

    session = db()
    q = (
        session.query(
            Skater.id,
            Skater.full_name,
            Skater.club_name,
            Skater.gender,
            Club.abbreviation,
            func.count(Result.id).label("results"),
        )
        .outerjoin(Result, Result.skater_id == Skater.id)
        .outerjoin(Club, Skater.club_id == Club.id)
        .group_by(Skater.id)
        .order_by(func.count(Result.id).desc())
    )
    if query.strip():
        norm = re.sub(r"[^a-z0-9 ]", "", query.lower()).strip()
        q = q.filter(Skater.normalized_name.contains(norm))
    if club_id is not None:
        q = q.filter(Skater.club_id == club_id)
    if club_query.strip():
        cq = club_query.strip().lower()
        q = q.filter(
            func.lower(Club.name).contains(cq) | func.lower(Club.abbreviation).contains(cq)
        )
    if gender:
        q = q.filter(Skater.gender == gender)
    rows = q.limit(100).all()
    session.close()
    return pd.DataFrame(rows, columns=["id", "full_name", "club_name", "gender", "abbreviation", "results"])


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
            Result.time_seconds,
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
        columns=["season", "event", "date", "division", "distance_m", "rank", "heat", "time", "time_s", "status"],
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


@st.cache_data(ttl=300)
def load_skater_season_bests(skater_id: int) -> pd.DataFrame:
    hist = load_skater_results(skater_id)
    traj = (
        hist[hist["distance_m"].isin(TRAJ_DISTANCES)]
        .dropna(subset=["time_s"])
        .groupby(["season", "distance_m"])
        .agg(best_s=("time_s", "min"))
        .reset_index()
    )
    traj["best_time"] = traj["best_s"].apply(_fmt_time)
    return traj


@st.cache_data(ttl=300)
def load_skater_attrs(skater_id: int) -> dict:
    session = db()
    row = (
        session.query(Skater.id, Skater.full_name, Skater.gender, Club.abbreviation)
        .outerjoin(Club, Skater.club_id == Club.id)
        .filter(Skater.id == skater_id)
        .first()
    )
    session.close()
    return {
        "id": row.id,
        "name": row.full_name,
        "gender": row.gender or "?",
        "club": row.abbreviation or "?",
    }


# ── Sidebar navigation ────────────────────────────────────────────────────────

st.sidebar.title("⛸ Skater Tracker")
page = st.sidebar.radio(
    "Navigate",
    ["Overview", "Events", "Skater Search", "Clubs", "Leaderboard", "Compare"],
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

    display_df = events_df[["season", "event_name", "event_date", "venue", "races"]].copy()
    display_df.columns = ["Season", "Event", "Date", "Venue", "Races"]
    display_df["Date"] = pd.to_datetime(display_df["Date"]).dt.strftime("%Y-%m-%d").where(
        display_df["Date"].notna(), ""
    )

    st.write(f"**{len(events_df)} events**")

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

    clubs_df = load_clubs_df()
    club_label_to_id = {f"{r['abbreviation']} — {r['name']}": r["id"] for _, r in clubs_df.iterrows()}

    fcol1, fcol2 = st.columns(2)
    with fcol1:
        sel_club_label = st.selectbox("Club", ["All"] + list(club_label_to_id.keys()))
    with fcol2:
        sel_gender = st.selectbox("Gender", ["All", "M", "F"])

    club_id_filter = club_label_to_id.get(sel_club_label) if sel_club_label != "All" else None
    gender_filter = sel_gender if sel_gender != "All" else None

    if query.strip() or club_id_filter is not None or gender_filter is not None:
        skaters_df = search_skaters(query, club_id_filter, gender_filter)

        if skaters_df.empty:
            st.warning("No skaters found.")
        else:
            st.write(f"**{len(skaters_df)} skaters found**")

            skater_options = {
                f"{row['full_name']} ({row['club_name'] or 'no club'}, {row['gender'] or '?'}) — {row['results']} results": row["id"]
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

                    with st.expander("Personal bests by distance"):
                        pbs = (
                            hist
                            .dropna(subset=["distance_m", "time_s"])
                            .sort_values("distance_m")
                            .groupby("distance_m")
                            .agg(
                                races=("time_s", "count"),
                                best_time_s=("time_s", "min"),
                                best_rank=("rank", "min"),
                            )
                            .reset_index()
                        )
                        pbs["Best Time"] = pbs["best_time_s"].apply(_fmt_time)
                        pbs["Best Rank"] = pbs["best_rank"].fillna("").astype(str).str.replace(".0", "", regex=False)
                        display_pbs = pbs[["distance_m", "races", "Best Time", "Best Rank"]].copy()
                        display_pbs.columns = ["Distance (m)", "Races", "Best Time", "Best Rank"]
                        st.dataframe(display_pbs, use_container_width=True, hide_index=True)

                    # ── Trajectory chart ──────────────────────────────────────
                    traj = (
                        hist[hist["distance_m"].isin(TRAJ_DISTANCES)]
                        .dropna(subset=["time_s"])
                        .groupby(["season", "distance_m"])
                        .agg(best_s=("time_s", "min"))
                        .reset_index()
                    )
                    traj["best_time"] = traj["best_s"].apply(_fmt_time)

                    if traj.empty:
                        st.info("No timed results for trajectory chart.")
                    else:
                        st.subheader("Best Time by Season")
                        seasons_order = sorted(traj["season"].unique())
                        panel_w = max(200, min(420, 760 // max(len(seasons_order), 2)))

                        panel_charts = []
                        for dist in TRAJ_DISTANCES:
                            subset = traj[traj["distance_m"] == dist]
                            if subset.empty:
                                continue
                            lo, hi = DIST_DOMAINS[dist]
                            c = (
                                alt.Chart(subset)
                                .mark_line(point=True)
                                .encode(
                                    x=alt.X(
                                        "season:O",
                                        title="Season",
                                        sort=seasons_order,
                                        axis=alt.Axis(labelAngle=-30),
                                    ),
                                    y=alt.Y(
                                        "best_s:Q",
                                        title="Best Time",
                                        scale=alt.Scale(zero=False),
                                        axis=alt.Axis(labelExpr=_TIME_EXPR),
                                    ),
                                    tooltip=[
                                        alt.Tooltip("season:O", title="Season"),
                                        alt.Tooltip("best_time:N", title="Best Time"),
                                    ],
                                )
                                .properties(width=panel_w, height=200, title=f"{dist}m")
                            )
                            panel_charts.append(c)

                        rows_of_charts = [
                            alt.hconcat(*panel_charts[i : i + 2])
                            for i in range(0, len(panel_charts), 2)
                        ]
                        combined = alt.vconcat(*rows_of_charts).configure_title(fontSize=14)
                        st.altair_chart(combined, use_container_width=True)

                    display = hist[["season", "event", "date", "division", "distance_m", "rank", "time", "status"]].copy()
                    display.columns = ["Season", "Event", "Date", "Division", "Dist (m)", "Rank", "Time", "Status"]
                    display["Date"] = pd.to_datetime(display["Date"]).dt.strftime("%Y-%m-%d").where(
                        display["Date"].notna(), ""
                    )
                    display["Rank"] = display["Rank"].fillna("").astype(str).str.replace(".0", "", regex=False)
                    st.dataframe(display, use_container_width=True, hide_index=True)

# ── Page: Clubs ───────────────────────────────────────────────────────────────

elif page == "Clubs":
    st.title("Clubs")

    clubs_df = load_clubs_df()

    col_list, col_detail = st.columns([2, 3])

    with col_list:
        display_clubs = clubs_df[["abbreviation", "name", "skaters"]].copy()
        display_clubs.columns = ["Abbrev", "Club", "Skaters"]
        st.dataframe(display_clubs, use_container_width=True, hide_index=True, height=500)

    with col_detail:
        club_options = {
            f"{r['abbreviation']} — {r['name']} ({r['skaters']} skaters)": r["id"]
            for _, r in clubs_df.iterrows()
        }
        chosen_club = st.selectbox(
            "Select a club",
            options=list(club_options.keys()),
            index=None,
            placeholder="Choose a club…",
        )

        if chosen_club:
            club_id = club_options[chosen_club]

            tab_roster, tab_times = st.tabs(["Roster", "Top Times"])

            with tab_roster:
                roster = load_club_roster(club_id)
                if roster.empty:
                    st.info("No skaters found.")
                else:
                    st.write(f"**{len(roster)} skaters**")
                    display_roster = roster[["name", "gender", "results"]].copy()
                    display_roster.columns = ["Name", "Gender", "Results"]
                    display_roster["Gender"] = display_roster["Gender"].fillna("")
                    st.dataframe(display_roster, use_container_width=True, hide_index=True)

            with tab_times:
                top_times = load_club_top_times(club_id)
                if top_times.empty:
                    st.info("No timed results found.")
                else:
                    for dist in sorted(top_times["distance_m"].dropna().unique()):
                        subset = top_times[top_times["distance_m"] == dist].head(20)
                        subset = subset.reset_index(drop=True)
                        subset.insert(0, "Rank", range(1, len(subset) + 1))
                        display_t = subset[["Rank", "name", "best_time"]].copy()
                        display_t.columns = ["Rank", "Name", "Best Time"]
                        st.write(f"**{int(dist)}m**")
                        st.dataframe(display_t, use_container_width=True, hide_index=True)

# ── Page: Leaderboard ─────────────────────────────────────────────────────────

elif page == "Leaderboard":
    st.title("Leaderboard")
    st.caption("Top ranked skaters by distance, filtered by season and division")

    seasons = load_seasons()
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        sel_season = st.selectbox("Season", seasons)
    with col2:
        sel_dist = st.selectbox("Distance (m)", [500, 777, 1000, 1500, 333])
    with col3:
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

    with col4:
        sel_gender = st.selectbox("Gender", ["All", "M", "F"])

    @st.cache_data(ttl=300)
    def load_leaderboard(season: str, distance_m: int, division: str | None, gender: str | None):
        session = db()
        q = (
            session.query(
                Skater.full_name,
                Skater.club_name,
                func.min(Result.time_seconds).label("best_time_s"),
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
        if division:
            q = q.filter(Race.division == division)
        if gender:
            q = q.filter(Skater.gender == gender)
        rows = (
            q.group_by(Skater.id)
            .order_by(func.min(Result.time_seconds))
            .limit(50)
            .all()
        )
        session.close()
        return pd.DataFrame(rows, columns=["name", "club", "best_time_s", "races"])

    lb = load_leaderboard(
        sel_season,
        sel_dist,
        sel_div if sel_div != "All" else None,
        sel_gender if sel_gender != "All" else None,
    )

    if lb.empty:
        st.info("No timed results found for this combination.")
    else:
        lb.insert(0, "Rank", range(1, len(lb) + 1))
        lb["Best Time"] = lb["best_time_s"].apply(_fmt_time)
        display_lb = lb[["Rank", "name", "club", "Best Time", "races"]].copy()
        display_lb.columns = ["Rank", "Name", "Club", "Best Time", "Races"]
        st.dataframe(display_lb, use_container_width=True, hide_index=True)

# ── Page: Compare ─────────────────────────────────────────────────────────────

elif page == "Compare":
    st.title("Compare Skaters")

    if "compare_skaters" not in st.session_state:
        st.session_state.compare_skaters = {}

    # ── Search & add ──
    scol1, scol2 = st.columns(2)
    with scol1:
        name_query = st.text_input("Search by name", placeholder="e.g. Sean…")
    with scol2:
        club_query = st.text_input("Search by club", placeholder="e.g. GSSC or Garden State…")

    if name_query.strip() or club_query.strip():
        results_df = search_skaters(name_query, club_query=club_query)
        if results_df.empty:
            st.warning("No skaters found.")
        else:
            st.write(f"**{len(results_df)} skaters found** — tick to select:")
            option_labels = [
                f"{r['full_name']} ({r['abbreviation'] or r['club_name'] or '?'}, {r['gender'] or '?'}) — {r['results']} results"
                for _, r in results_df.iterrows()
            ]
            label_to_id = {label: r["id"] for label, (_, r) in zip(option_labels, results_df.iterrows())}
            label_to_name = {label: r["full_name"] for label, (_, r) in zip(option_labels, results_df.iterrows())}

            selected_labels = st.multiselect("Select skaters to add", option_labels, placeholder="Tick one or more…")
            if st.button("Add selected") and selected_labels:
                for label in selected_labels:
                    sid = label_to_id[label]
                    st.session_state.compare_skaters[sid] = label_to_name[label]
                st.rerun()

    # ── Selected skater chips ──
    if st.session_state.compare_skaters:
        chip_cols = st.columns(max(len(st.session_state.compare_skaters), 1))
        to_remove = None
        for col, (sid, name) in zip(chip_cols, list(st.session_state.compare_skaters.items())):
            with col:
                st.markdown(f"**{name}**")
                if st.button("×", key=f"rm_{sid}"):
                    to_remove = sid
        if to_remove is not None:
            del st.session_state.compare_skaters[to_remove]
            st.rerun()

    # ── Output ──
    if st.session_state.compare_skaters:
        all_traj = []
        for sid in st.session_state.compare_skaters:
            df = load_skater_season_bests(sid).copy()
            attrs = load_skater_attrs(sid)
            df["skater"] = attrs["name"]
            df["gender"] = attrs["gender"]
            df["club"]   = attrs["club"]
            all_traj.append(df)
        combined_df = pd.concat(all_traj, ignore_index=True)
        seasons_order = sorted(combined_df["season"].unique())

        # ── Season best times tables ──
        st.subheader("Season Best Times")
        for dist in TRAJ_DISTANCES:
            sub = combined_df[combined_df["distance_m"] == dist]
            if sub.empty:
                continue
            pivot = (
                sub.pivot_table(index="season", columns="skater", values="best_time", aggfunc="first")
                .reindex(seasons_order)
                .reset_index()
            )
            pivot.columns.name = None
            st.write(f"**{dist}m**")
            st.dataframe(pivot, use_container_width=True, hide_index=True)

        # ── Trajectory charts ──
        st.subheader("Trajectory")
        color_by = st.selectbox("Color by", ["Skater", "Gender", "Club"])
        color_col = {"Skater": "skater", "Gender": "gender", "Club": "club"}[color_by]

        panel_w = max(200, min(420, 760 // max(len(seasons_order), 2)))
        panel_charts = []
        for dist in TRAJ_DISTANCES:
            sub = combined_df[combined_df["distance_m"] == dist]
            if sub.empty:
                continue
            c = (
                alt.Chart(sub)
                .mark_line(point=True)
                .encode(
                    x=alt.X("season:O", title="Season", sort=seasons_order,
                             axis=alt.Axis(labelAngle=-30)),
                    y=alt.Y("best_s:Q", title="Best Time",
                             scale=alt.Scale(zero=False),
                             axis=alt.Axis(labelExpr=_TIME_EXPR)),
                    color=alt.Color(f"{color_col}:N", title=color_by),
                    detail=alt.Detail("skater:N"),
                    tooltip=[
                        alt.Tooltip("season:O", title="Season"),
                        alt.Tooltip("skater:N", title="Skater"),
                        alt.Tooltip(f"{color_col}:N", title=color_by),
                        alt.Tooltip("best_time:N", title="Best Time"),
                    ],
                )
                .properties(width=panel_w, height=200, title=f"{dist}m")
            )
            panel_charts.append(c)

        if panel_charts:
            rows_of_charts = [
                alt.hconcat(*panel_charts[i : i + 2])
                for i in range(0, len(panel_charts), 2)
            ]
            st.altair_chart(
                alt.vconcat(*rows_of_charts).configure_title(fontSize=14),
                use_container_width=True,
            )
