# Ship Log

A sailing log for charter voyages. Legs are built from uploaded FIT track
files (GPS watch exports) or created live during a passage as free-text
quick notes, then annotated with weather/propulsion detail in an editable
log table.

## Stack

- **FastAPI** + **SQLModel** (SQLite) — backend and ORM
- **Jinja2** + **HTMX** — server-rendered templates with inline AJAX edits
- **Leaflet** — track/entry map
- **fitparse** — FIT file parsing, **timezonefinder** — GPS → IANA timezone

## Layout

```
app/
  models.py            Voyage, Leg, LogEntry, PropulsionType, EntrySource
  database.py           SQLModel engine/session setup
  stats.py               distance/time aggregation by propulsion
  routers/
    voyages.py            voyage CRUD
    legs.py                 leg creation (FIT upload, bare/"quick" leg, attach-fit),
                            leg detail, manual/quick-note entry creation
    log_entries.py          inline field PATCH / DELETE for a single entry
  processors/
    fit.py                  FIT metadata + manual lap parsing
    fit_track.py             FIT track parsing, turning-point detection
    merge.py                 builds LogEntry rows from a parsed track + laps
    notes.py                 quick-note creation, note-filtering for the notes summary
  templates/               Jinja2 templates (leg.html is the main detail view)
  static/                  CSS + minimal JS (most JS is inline in leg.html)
tests/                    pytest, no network/browser dependencies
```

## Data model

- **Voyage** → has many **Leg**s (from_port, to_port, date, timezone, optional `fit_path`)
- **Leg** → has many **LogEntry** rows (timestamp, optional lat/lon, `source`, weather/propulsion fields, optional `notes`)
- `EntrySource`: `turning_point`, `hourly`, `lap` (all GPS-derived from a FIT file), `manual` (map-click entries, FIT start/end anchors), `quick_note` (free-text note logged live, with or without a resolved position)

A `Leg` can exist with no `fit_path` and no entries — created via "Start leg
now" before departure. A FIT file can be attached to it later ("Attach FIT
file"); GPS-derived entries are inserted alongside any existing quick notes
with no merging — see `app/processors/merge.py::build_log_entries` and
`app/routers/legs.py::attach_fit`.

## Running locally

```bash
docker compose up
```

Serves on `http://localhost:8000`. Data persists in `./data/` (SQLite DB +
uploaded FIT files), bind-mounted into the container. Code changes hot-reload
(`uvicorn --reload`).

## Testing

```bash
make test        # pytest — pure unit tests, no Docker/DB file needed
```

Tests use an in-memory SQLite DB (`tests/conftest.py`) and never touch
`./data/`.

## Manual verification against isolated data

For exploratory testing (curl, browser) against real-looking data without
risking `./data/`:

```bash
make verify       # copies ./data -> ./data-verify, starts a second
                   # container on http://localhost:8001
make verify-down  # stops it and wipes ./data-verify
```

This runs as a separate Compose service (`verify`, in
`docker-compose.verify.yml`) alongside — not instead of — the normal `app`
service, so a running `docker compose up` dev server is unaffected.

## Notes on SQLite + schema changes

There's no migration framework (just `SQLModel.metadata.create_all`, which
only creates missing tables, never alters existing ones). If you need to
change an existing column's nullability or type, write a throwaway
migration script that backs up the DB, rebuilds it from the current models,
and copies rows across — see git history for `LogEntry.lat`/`lon` becoming
nullable as an example. Adding new columns or enum values with SQLite's
dynamic typing usually needs no migration at all.
