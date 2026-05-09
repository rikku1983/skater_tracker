"""Session helpers for skater_tracker_round2.db."""
from __future__ import annotations
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from .models_v2 import Base

_DEFAULT_DB = Path(__file__).parents[2] / "data" / "skater_tracker_round2.db"

_engines: dict[str, object] = {}


def _engine(db_path: str | Path = _DEFAULT_DB):
    key = str(db_path)
    if key not in _engines:
        _engines[key] = create_engine(f"sqlite:///{key}", connect_args={"check_same_thread": False})
    return _engines[key]


def init_db(db_path: str | Path = _DEFAULT_DB) -> None:
    Base.metadata.create_all(_engine(db_path))


def get_session(db_path: str | Path = _DEFAULT_DB) -> Session:
    factory = sessionmaker(bind=_engine(db_path))
    return factory()
