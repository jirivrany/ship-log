import os
from sqlmodel import SQLModel, Session, create_engine

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:////app/data/ship_log.db")

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


def migrate_schema(target_engine) -> None:
    """Idempotent in-place migrations for the single-user SQLite DB.

    Runs before create_all so an existing `leg` table is brought up to the
    current model shape; on a fresh DB the table doesn't exist yet and
    create_all builds it directly.
    """
    with target_engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(leg)").fetchall()}
        if not cols:
            return

        # 2026-07: fit_path -> track_path + track_source + strava_activity_id
        if "fit_path" in cols and "track_path" not in cols:
            conn.exec_driver_sql("ALTER TABLE leg RENAME COLUMN fit_path TO track_path")
            cols.discard("fit_path")
            cols.add("track_path")
        if "track_source" not in cols:
            conn.exec_driver_sql("ALTER TABLE leg ADD COLUMN track_source VARCHAR")
            conn.exec_driver_sql(
                "UPDATE leg SET track_source = 'fit' WHERE track_path IS NOT NULL"
            )
        if "strava_activity_id" not in cols:
            conn.exec_driver_sql("ALTER TABLE leg ADD COLUMN strava_activity_id INTEGER")

        # 2026-07: leg forecast block (synoptic situation, warnings, sun times)
        for col in ("synoptic_situation", "forecast", "warnings", "sunrise",
                    "sunset", "forecast_source", "synoptic_chart_path"):
            if col not in cols:
                conn.exec_driver_sql(f"ALTER TABLE leg ADD COLUMN {col} VARCHAR")

        # 2026-07: voyage start/end dates (drive Strava import window) + skipper
        voyage_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(voyage)").fetchall()}
        if voyage_cols:
            for col in ("start_date", "end_date", "skipper"):
                if col not in voyage_cols:
                    conn.exec_driver_sql(f"ALTER TABLE voyage ADD COLUMN {col} VARCHAR")

            # 2026-07: boat -> boat_name + boat_maker + boat_model + year_built.
            # The old combined value stays in boat_name; the user splits it by hand.
            if "boat" in voyage_cols and "boat_name" not in voyage_cols:
                conn.exec_driver_sql("ALTER TABLE voyage RENAME COLUMN boat TO boat_name")
                voyage_cols.discard("boat")
                voyage_cols.add("boat_name")
            for col in ("boat_maker", "boat_model"):
                if col not in voyage_cols:
                    conn.exec_driver_sql(f"ALTER TABLE voyage ADD COLUMN {col} VARCHAR")
            if "year_built" not in voyage_cols:
                conn.exec_driver_sql("ALTER TABLE voyage ADD COLUMN year_built INTEGER")

            # 2026-07: user profile — per-voyage "I was the skipper" flag
            if "was_skipper" not in voyage_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE voyage ADD COLUMN was_skipper BOOLEAN NOT NULL DEFAULT 0"
                )

            # 2026-07: navigation area (A/B/C/2/1) for per-area mile summary
            if "area" not in voyage_cols:
                conn.exec_driver_sql("ALTER TABLE voyage ADD COLUMN area VARCHAR")

        # 2026-07: weather enrichment — exact wind speed + provenance marker
        entry_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(logentry)").fetchall()}
        if entry_cols:
            if "wind_speed_kn" not in entry_cols:
                conn.exec_driver_sql("ALTER TABLE logentry ADD COLUMN wind_speed_kn FLOAT")
            if "weather_source" not in entry_cols:
                conn.exec_driver_sql("ALTER TABLE logentry ADD COLUMN weather_source VARCHAR")

            # 2026-07: sail configuration per entry
            if "sails" not in entry_cols:
                conn.exec_driver_sql("ALTER TABLE logentry ADD COLUMN sails VARCHAR")

        conn.commit()


def init_db():
    os.makedirs("/app/data", exist_ok=True)
    migrate_schema(engine)
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
