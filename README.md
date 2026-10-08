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
    passage.py              passage planner: measuring + 20 NM offshore limit
  passage.py             rhumb-line courses/distances, magnetic variation
  coastline.py           OSM coastline fetch (Overpass) + on-disk cache
  boundary.py            offshore-limit contour (distance field + marching squares)
  limit_store.py         persisted computed limit areas
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

## Derived map data (not in the database)

The passage planner caches two things on disk under `data/`. Neither is in
SQLite: both are derived from OpenStreetMap, reconstructible at any time, and
unrelated to voyages or legs, so they would cost a model and a migration for
no benefit.

| Path | What | Size | Worth keeping? |
| --- | --- | --- | --- |
| `data/offshore-limits.json` | Computed 20 NM limit contours, with the bounds each covers. Redrawn when the planner loads. | ~1 KB per area | Yes — small, and saves recomputing |
| `data/coastline/*.json` | Raw Overpass answers, keyed by a hash of the bounding box. Lets an area be recomputed without re-querying Overpass. | 1–3 MB per area | No — pure cache |

Both are covered by the existing `data/` entry in `.gitignore`.

Deleting `data/coastline/` is safe: the contours still redraw, and only a
*new* area needs Overpass again. Deleting `offshore-limits.json` loses the
coverage but not the expensive part — recomputing reads the coastline cache
and takes about a second instead of ~20.

**Backups.** Back up `offshore-limits.json`; skip `data/coastline/`, which
grows by 1–3 MB per area computed and regenerates itself.

### Moving computed coverage to the server

The contour file is plain coordinates with no absolute paths or machine state,
so copying it across works:

```bash
scp data/offshore-limits.json shiplog@<server>:~/ship-log/data/
```

Copying as `shiplog` lands the file owned by `shiplog`, which is the user the
app runs as, so no `chown` is needed. Two things to be aware of:

- scp carries the *source* mode (600) to a file it creates, so a first copy is
  readable only by `shiplog`. That is enough for the app, but not for another
  account on the box.
- Overwriting an existing file keeps the destination's own owner and mode, so
  later copies inherit whatever is already there.

If you copy as a different user — or via `sudo` into place — check the result,
because the app runs as `shiplog` with a uid that differs from your laptop's
(DEPLOYMENT.md §4.3). An unreadable store is not reported as an error: the
planner just starts from no coverage.

```bash
ssh <server> 'ls -l ~/ship-log/data/offshore-limits.json'
```

Copying is one-way and last-write-wins: the whole set of areas lives in this
one file, so transferring it **replaces** the server's coverage rather than
merging. Copy server → laptop and you lose anything computed only on the
server. To combine both, compute the missing areas on one machine instead.

`data/coastline/` does not need copying. It only makes recomputation faster,
and it is the bulky part.

## Notes on SQLite + schema changes

There's no migration framework (just `SQLModel.metadata.create_all`, which
only creates missing tables, never alters existing ones). If you need to
change an existing column's nullability or type, write a throwaway
migration script that backs up the DB, rebuilds it from the current models,
and copies rows across — see git history for `LogEntry.lat`/`lon` becoming
nullable as an example. Adding new columns or enum values with SQLite's
dynamic typing usually needs no migration at all.
