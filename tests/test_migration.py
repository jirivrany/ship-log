"""Startup migration: old fit_path schema -> track_path + track_source + strava_activity_id."""
from sqlalchemy import create_engine

from app.database import migrate_schema

OLD_SCHEMA = """
CREATE TABLE leg (
    id INTEGER PRIMARY KEY,
    voyage_id INTEGER NOT NULL,
    from_port VARCHAR NOT NULL,
    to_port VARCHAR NOT NULL,
    date VARCHAR NOT NULL,
    timezone VARCHAR NOT NULL,
    fit_path VARCHAR
)
"""

OLD_VOYAGE_SCHEMA = """
CREATE TABLE voyage (
    id INTEGER PRIMARY KEY,
    name VARCHAR NOT NULL,
    boat VARCHAR NOT NULL,
    crew VARCHAR,
    created_at TIMESTAMP
)
"""


def _old_db_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    with engine.connect() as conn:
        conn.exec_driver_sql(OLD_SCHEMA)
        conn.exec_driver_sql(OLD_VOYAGE_SCHEMA)
        conn.exec_driver_sql(
            "INSERT INTO voyage (name, boat, crew) VALUES ('Chorvatsko 2026', 'Bavaria', 'posádka')"
        )
        conn.exec_driver_sql(
            "INSERT INTO leg (voyage_id, from_port, to_port, date, timezone, fit_path) "
            "VALUES (1, 'Sukošan', 'Ždrelac', '2026-06-20', 'Europe/Zagreb', '/app/data/uploads/x.fit'),"
            "       (1, 'Ždrelac', 'Sv. Ante', '2026-06-21', 'Europe/Zagreb', NULL)"
        )
        conn.commit()
    return engine


def _columns(engine):
    with engine.connect() as conn:
        return {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(leg)").fetchall()}


def test_migrates_old_schema(tmp_path):
    engine = _old_db_engine(tmp_path)
    migrate_schema(engine)

    cols = _columns(engine)
    assert "fit_path" not in cols
    assert {"track_path", "track_source", "strava_activity_id"} <= cols

    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT track_path, track_source, strava_activity_id FROM leg ORDER BY id"
        ).fetchall()
    # leg with a FIT file: path preserved, source backfilled
    assert rows[0] == ("/app/data/uploads/x.fit", "fit", None)
    # trackless quick-form leg: no source
    assert rows[1] == (None, None, None)

    with engine.connect() as conn:
        voyage_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(voyage)").fetchall()}
        row = conn.exec_driver_sql(
            "SELECT name, crew, start_date, end_date, skipper FROM voyage"
        ).fetchone()
    assert {"start_date", "end_date", "skipper"} <= voyage_cols
    # existing data untouched, new columns empty
    assert row == ("Chorvatsko 2026", "posádka", None, None, None)


def test_migration_is_idempotent(tmp_path):
    engine = _old_db_engine(tmp_path)
    migrate_schema(engine)
    migrate_schema(engine)  # second run must not fail or alter data

    with engine.connect() as conn:
        count = conn.exec_driver_sql("SELECT COUNT(*) FROM leg").fetchone()[0]
    assert count == 2


def test_fresh_db_untouched(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/fresh.db")
    migrate_schema(engine)  # no leg table yet -> no-op, create_all handles it
    assert _columns(engine) == set()
