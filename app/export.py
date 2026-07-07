"""Voyage/leg PDF export: context assembly + WeasyPrint over print templates.

The document mirrors the paper logbooks the app is modeled on: a cover page
with the boat data, then one day page per leg (forecast block, entries table,
totals, track map), and a voyage summary. Images are embedded as data URIs so
the rendered HTML is self-contained.
"""
import base64
import os
import unicodedata
from datetime import date, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from weasyprint import HTML

from app.models import Leg, LogEntry, Voyage
from app.processors import loader
from app.processors.notes import filter_note_entries
from app.stats import aggregate_stats, compute_stats, weather_summary
from app.synoptic import ATTRIBUTION as SYNOPTIC_ATTRIBUTION
from app.templates_env import templates
from app.trackmap import ATTRIBUTION as MAP_ATTRIBUTION, render_track_map

MAX_MAP_POINTS = 500  # polyline sampling: plenty for an A5-sized print map


def _data_uri(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


def _track_map_uri(leg: Leg, map_client: Optional[httpx.Client]) -> Optional[str]:
    """Track map as a data URI; None for track-less legs or offline tiles."""
    if not leg.track_path:
        return None
    try:
        track = loader.parse_track(leg.track_path)
    except Exception:
        return None
    points = [(p.lat, p.lon) for p in track.track_points]
    step = max(1, len(points) // MAX_MAP_POINTS)
    png = render_track_map(points[::step] + points[-1:], client=map_client)
    return _data_uri(png) if png else None


def _synoptic_chart_uri(leg: Leg) -> Optional[str]:
    if not leg.synoptic_chart_path or not os.path.exists(leg.synoptic_chart_path):
        return None
    with open(leg.synoptic_chart_path, "rb") as f:
        return _data_uri(f.read())


def leg_context(
    leg: Leg,
    entries: list[LogEntry],
    map_client: Optional[httpx.Client] = None,
) -> dict:
    """Everything one day page needs; entries must be ordered by timestamp."""
    try:
        tz = ZoneInfo(leg.timezone)
    except Exception:
        tz = ZoneInfo("UTC")

    def local_hhmm(entry: LogEntry) -> str:
        return entry.timestamp.replace(tzinfo=timezone.utc).astimezone(tz).strftime("%H:%M")

    return {
        "leg": leg,
        "local_entries": [(e, local_hhmm(e)) for e in entries],
        "local_notes": [(e, local_hhmm(e)) for e in filter_note_entries(entries)],
        "stats": compute_stats(entries),
        "weather": weather_summary(entries),
        "track_map_uri": _track_map_uri(leg, map_client),
        "synoptic_chart_uri": _synoptic_chart_uri(leg),
    }


def _render_pdf(template_name: str, context: dict) -> bytes:
    context.update({
        "generated_on": date.today().isoformat(),
        "map_attribution": MAP_ATTRIBUTION,
        "synoptic_attribution": SYNOPTIC_ATTRIBUTION,
    })
    html = templates.env.get_template(template_name).render(context)
    return HTML(string=html).write_pdf()


def render_voyage_pdf(voyage: Voyage, leg_contexts: list[dict]) -> bytes:
    """Cover page + one day page per leg + voyage summary."""
    return _render_pdf("print/voyage_pdf.html", {
        "voyage": voyage,
        "legs": leg_contexts,
        "voyage_stats": aggregate_stats([c["stats"] for c in leg_contexts]),
    })


def render_leg_pdf(voyage: Voyage, context: dict) -> bytes:
    """A single day page, standalone."""
    return _render_pdf("print/leg_pdf.html", {"voyage": voyage, **context})


def export_filename(voyage: Voyage, leg: Optional[Leg] = None) -> str:
    """ASCII-safe attachment filename, e.g. "chorvatsko-2026_2026-07-08.pdf"."""
    base = leg.date if leg else date.today().isoformat()
    # NFKD strips diacritics to their base letters (Sukošan -> sukosan)
    name = unicodedata.normalize("NFKD", voyage.name).encode("ascii", "ignore").decode()
    slug = "".join(c if c.isalnum() else "-" for c in name.lower())
    slug = "-".join(p for p in slug.split("-") if p) or "voyage"
    return f"{slug}_{base}.pdf"
