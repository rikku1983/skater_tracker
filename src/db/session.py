from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from .models import Base

DB_PATH = Path(__file__).parents[2] / "data" / "skater_tracker.db"


def get_engine(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{db_path}", echo=False)


def _apply_migrations(engine) -> None:
    """Add columns that were introduced after the initial schema creation."""
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(events)")).fetchall()
        existing = {r[1] for r in rows}
        if "link_text" not in existing:
            conn.execute(text("ALTER TABLE events ADD COLUMN link_text VARCHAR(512)"))
            conn.commit()
        rows = conn.execute(text("PRAGMA table_info(skaters)")).fetchall()
        existing = {r[1] for r in rows}
        if "age_at_2026" not in existing:
            conn.execute(text("ALTER TABLE skaters ADD COLUMN age_at_2026 INTEGER"))
            conn.commit()
        if "birth_year" not in existing:
            conn.execute(text("ALTER TABLE skaters ADD COLUMN birth_year INTEGER"))
            conn.commit()
        rows = conn.execute(text("PRAGMA table_info(results)")).fetchall()
        existing = {r[1] for r in rows}
        if "data_flags" not in existing:
            conn.execute(text("ALTER TABLE results ADD COLUMN data_flags VARCHAR(255)"))
            conn.commit()
        rows = conn.execute(text("PRAGMA table_info(events)")).fetchall()
        existing = {r[1] for r in rows}
        if "track_type" not in existing:
            conn.execute(text("ALTER TABLE events ADD COLUMN track_type VARCHAR(20)"))
            conn.commit()


def init_db(db_path: Path = DB_PATH):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    _apply_migrations(engine)
    return engine


def get_session(db_path: Path = DB_PATH) -> Session:
    engine = get_engine(db_path)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()
