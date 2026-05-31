"""Session helpers for skater_tracker_round2.db."""
from __future__ import annotations
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from .models_v2 import Base

_DEFAULT_DB = Path(__file__).parents[2] / "data" / "skater_tracker_round2.db"

_engines: dict[str, object] = {}


def _engine(db_path: str | Path = _DEFAULT_DB):
    key = str(db_path)
    if key not in _engines:
        _engines[key] = create_engine(f"sqlite:///{key}", connect_args={"check_same_thread": False})
    return _engines[key]


def _apply_migrations(engine) -> None:
    """Add new columns to existing tables (idempotent — ignores duplicate-column errors)."""
    stmts = [
        "ALTER TABLE results ADD COLUMN club_id INTEGER",
        "ALTER TABLE skater_entries ADD COLUMN club_id INTEGER",
        "ALTER TABLE classification ADD COLUMN club_id INTEGER",
        "ALTER TABLE time_classification ADD COLUMN affiliation TEXT",
        "ALTER TABLE time_classification ADD COLUMN club_id INTEGER",
        "ALTER TABLE skater_entries ADD COLUMN birth_year_1 INTEGER",
        "ALTER TABLE skater_entries ADD COLUMN birth_year_2 INTEGER",
        "ALTER TABLE results ADD COLUMN skater_id INTEGER",
        "ALTER TABLE skater_entries ADD COLUMN skater_id INTEGER",
        "ALTER TABLE classification ADD COLUMN skater_id INTEGER",
        "ALTER TABLE time_classification ADD COLUMN skater_id INTEGER",
    ]
    with engine.connect() as conn:
        for sql in stmts:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass  # column already exists


def init_db(db_path: str | Path = _DEFAULT_DB) -> None:
    engine = _engine(db_path)
    Base.metadata.create_all(engine)   # creates clubs table + any other new tables
    _apply_migrations(engine)


def get_session(db_path: str | Path = _DEFAULT_DB) -> Session:
    factory = sessionmaker(bind=_engine(db_path))
    return factory()
