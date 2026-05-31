"""
SQLAlchemy models for skater_tracker_round2.db.

Tables: events, clubs, skaters, classification, results, skater_entries, time_classification.
All parsed text is stored raw — no normalization at parse time.
Club normalization:  clubs table + club_id FK columns (scripts/normalize_clubs.py).
Skater normalization: skaters table + skater_id FK columns (scripts/normalize_skaters.py).
"""
from __future__ import annotations
from sqlalchemy import Column, Integer, Float, Text, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Club(Base):
    """Canonical club/country entity. Populated by scripts/normalize_clubs.py."""
    __tablename__ = "clubs"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    canonical_name = Column(Text, nullable=False, unique=True)
    abbreviation   = Column(Text)    # e.g. "NBSSC", "USA"
    full_club_name = Column(Text)    # full official name
    aliases        = Column(Text)    # pipe-separated raw variants
    occurrence     = Column(Integer) # total rows mapped to this club
    city           = Column(Text)
    state_province = Column(Text)
    country        = Column(Text)
    location_note  = Column(Text)
    duplicate_note = Column(Text)


class Skater(Base):
    """Canonical skater entity. Populated by scripts/normalize_skaters.py."""
    __tablename__ = "skaters"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    full_name       = Column(Text, nullable=False)
    first_name      = Column(Text)
    last_name       = Column(Text)
    normalized_name = Column(Text, index=True)   # lowercase alpha+space tokens
    gender          = Column(Text)    # 'Male' | 'Female' | NULL
    birth_year      = Column(Integer) # single year (from CSV); NULL if only range known
    birth_year_min  = Column(Integer) # lower bound when only range available
    birth_year_max  = Column(Integer) # upper bound when only range available
    known_aliases   = Column(Text)    # pipe-separated raw name variants seen in DB
    source          = Column(Text)    # 'csv' | 'db_new'


class Event(Base):
    __tablename__ = "events"

    id           = Column(Integer, primary_key=True)  # matches knowledge_base/events.csv id
    season       = Column(Text)
    event_name   = Column(Text)
    event_date   = Column(Text)
    end_date     = Column(Text)
    series       = Column(Text)
    event_group  = Column(Text)
    track_type   = Column(Text)
    pdf_format   = Column(Text)
    local_path   = Column(Text, unique=True)
    venue        = Column(Text)
    city         = Column(Text)
    state        = Column(Text)
    parsed_at    = Column(Text)   # ISO timestamp; NULL = not yet parsed
    parse_errors = Column(Text)   # newline-joined error messages

    classifications  = relationship("Classification", back_populates="event",
                                    cascade="all, delete-orphan")
    results          = relationship("Result",         back_populates="event",
                                    cascade="all, delete-orphan")
    skater_entries       = relationship("SkaterEntry",        back_populates="event",
                                        cascade="all, delete-orphan")
    time_classifications = relationship("TimeClassification", back_populates="event",
                                        cascade="all, delete-orphan")


class Classification(Base):
    """One row per skater per entry in the classification section of a PDF."""
    __tablename__ = "classification"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    event_id          = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    section_type      = Column(Text)    # 'overall' or 'distance'
    division          = Column(Text)    # raw division name
    distance_m        = Column(Integer) # NULL for overall classification
    rank              = Column(Integer)
    bib               = Column(Text)
    skater_name       = Column(Text)
    affiliation       = Column(Text)    # raw club/country string
    club_id           = Column(Integer) # FK → clubs.id (nullable; no DB-level constraint)
    skater_id         = Column(Integer) # FK → skaters.id (nullable; no DB-level constraint)
    points            = Column(Float)
    # Overall classification only
    skater_status     = Column(Text)    # age/level code: JR D, SR, 30+, etc.
    cdr               = Column(Integer) # sum of distance ranks
    bdr               = Column(Integer) # best distance rank
    # Distance classification only
    semi_group        = Column(Text)    # S column: which semi heat group (A1, A2, B1…)
    final_group       = Column(Text)    # F column: which final heat group (A1, B1, DNS…)
    # Shared
    best_time_text    = Column(Text)
    best_time_seconds = Column(Float)
    page_number       = Column(Integer)

    event = relationship("Event", back_populates="classifications")


class Result(Base):
    """One row per skater per race entry in the results section of a PDF."""
    __tablename__ = "results"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    event_id     = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    division     = Column(Text)     # raw division name from round header
    distance_m   = Column(Integer)  # computed from laps + track type
    laps         = Column(Float)    # lap count as written in PDF (e.g. 4.5)
    round_type   = Column(Text)     # HEATS / FINAL / SUPER FINAL
    round_label  = Column(Text)     # full round label text
    race_number  = Column(Integer)  # global race# within event; NULL if not present
    heat         = Column(Integer)
    group_label  = Column(Text)     # A / B / C
    rank         = Column(Integer)
    bib          = Column(Text)
    skater_name  = Column(Text)
    affiliation  = Column(Text)     # raw club/country string
    club_id      = Column(Integer)  # FK → clubs.id (nullable; no DB-level constraint)
    skater_id    = Column(Integer)  # FK → skaters.id (nullable; no DB-level constraint)
    time_text    = Column(Text)
    time_seconds = Column(Float)
    status       = Column(Text)     # Q / DNF / DNS / DQ / PEN / ADV / etc.
    points       = Column(Float)
    page_number  = Column(Integer)
    parse_note   = Column(Text)     # parser warning for uncertain rows
    is_relay     = Column(Boolean, default=False)

    event = relationship("Event", back_populates="results")


class SkaterEntry(Base):
    """One row per registered skater per event (from the skater list section of a PDF)."""
    __tablename__ = "skater_entries"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    event_id    = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    bib         = Column(Text)
    skater_name = Column(Text)
    status      = Column(Text)    # age/level code: JR D, SR, 30+, etc.
    gender      = Column(Text)    # Male / Female
    division    = Column(Text)    # raw division name
    club         = Column(Text)    # raw club name
    club_id      = Column(Integer) # FK → clubs.id (nullable; no DB-level constraint)
    skater_id    = Column(Integer) # FK → skaters.id (nullable; no DB-level constraint)
    birth_year_1 = Column(Integer) # older end of 2-year age bracket (cutoff July 1)
    birth_year_2 = Column(Integer) # younger end of 2-year age bracket
    page_number  = Column(Integer)

    event = relationship("Event", back_populates="skater_entries")


class TimeClassification(Base):
    """One row per skater per distance in the Time Classification section of a PDF.

    Time Classification ranks skaters by best race time per distance within their
    division — separate from the Distance Classification (which shows seeding groups).
    """
    __tablename__ = "time_classification"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    event_id          = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    division          = Column(Text)    # raw division name
    distance_m        = Column(Integer)
    rank              = Column(Integer) # rank within this division+distance
    bib               = Column(Text)
    skater_name       = Column(Text)
    affiliation       = Column(Text)    # raw club/country string (from PDF if present)
    club_id           = Column(Integer) # FK → clubs.id (nullable; no DB-level constraint)
    skater_id         = Column(Integer) # FK → skaters.id (nullable; no DB-level constraint)
    best_time_text    = Column(Text)    # e.g. "1:23.456"
    best_time_seconds = Column(Float)
    page_number       = Column(Integer)

    event = relationship("Event", back_populates="time_classifications")
