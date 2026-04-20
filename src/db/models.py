from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, Date, Float, Text, Boolean, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Event(Base):
    """
    One row per downloaded ST PDF. Serves as the download manifest
    and will also become the primary event record once parsing is done.
    All events are short track.
    """
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    season = Column(String(9), nullable=False, index=True)   # e.g. "2025-2026"
    event_name = Column(String(255), nullable=False)
    event_date = Column(Date, nullable=True)                 # parsed from link text
    source_url = Column(String(512), nullable=False)         # USS results page URL
    pdf_url = Column(String(512), nullable=False, unique=True)
    local_path = Column(String(512), nullable=True)
    downloaded_at = Column(DateTime, nullable=True)
    checksum = Column(String(64), nullable=True)             # sha256
    is_parseable = Column(Boolean, nullable=True)            # False for image-only PDFs
    event_group = Column(String(100), nullable=True, index=True)  # canonical recurring event name
    event_year = Column(Integer, nullable=True, index=True)       # calendar year the event took place
    pdf_format = Column(String(30), nullable=True)                # parser used: "all_races", "tempus", etc.

    # Fields to be filled in after parsing
    venue = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(50), nullable=True)
    end_date = Column(Date, nullable=True)

    races = relationship("Race", back_populates="event", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Event {self.season} | {self.event_date} | {self.event_name}>"


class Race(Base):
    """One row per division+distance within an event."""
    __tablename__ = "races"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    division = Column(String(100), nullable=True)            # e.g. "Anacostia", "NEST MEN A", "Junior D Women"
    distance_m = Column(Integer, nullable=True)              # e.g. 500, 777, 1000, 1500
    gender = Column(String(10), nullable=True)               # "M" / "F" / "Mixed"
    round = Column(String(50), nullable=True)                # "Distance Classification", "Final A", "Heat 1", etc.
    race_date = Column(Date, nullable=True)
    page_number = Column(Integer, nullable=True)

    event = relationship("Event", back_populates="races")
    results = relationship("Result", back_populates="race", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Race {self.distance_m}m {self.division}>"


class Skater(Base):
    """One row per unique skater (deduplicated across events)."""
    __tablename__ = "skaters"

    id = Column(Integer, primary_key=True)
    full_name = Column(String(255), nullable=False)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    normalized_name = Column(String(255), nullable=False, index=True)  # lowercase, no punctuation
    club_name = Column(String(255), nullable=True)
    state = Column(String(50), nullable=True)

    results = relationship("Result", back_populates="skater")

    def __repr__(self):
        return f"<Skater {self.full_name}>"


class Result(Base):
    """One row per skater per race (distance classification result)."""
    __tablename__ = "results"

    id = Column(Integer, primary_key=True)
    race_id = Column(Integer, ForeignKey("races.id"), nullable=False, index=True)
    skater_id = Column(Integer, ForeignKey("skaters.id"), nullable=False, index=True)
    bib = Column(String(20), nullable=True)
    rank = Column(Integer, nullable=True)
    seed_heat = Column(String(10), nullable=True)            # seeding heat assignment, e.g. "A2" (Tempus ST "S" col)
    heat_assignment = Column(String(10), nullable=True)      # final heat placement, e.g. "A1", "B3" (Tempus "F" col)
    time_text = Column(String(30), nullable=True)            # raw string from PDF, e.g. "1:23.456"
    time_seconds = Column(Float, nullable=True)              # normalized to seconds
    points = Column(Float, nullable=True)
    status = Column(String(20), nullable=True)               # "DNS", "DNF", "DQ", "PEN", "ADV", etc.
    note = Column(Text, nullable=True)

    race = relationship("Race", back_populates="results")
    skater = relationship("Skater", back_populates="results")

    __table_args__ = (
        UniqueConstraint("race_id", "skater_id", name="uq_result_race_skater"),
    )

    def __repr__(self):
        return f"<Result race={self.race_id} skater={self.skater_id} rank={self.rank}>"
