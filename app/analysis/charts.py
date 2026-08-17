"""Chart geometry for the result page (PRD §9.7).

Charts are inline SVG rendered server-side: the page must stay readable
without JavaScript, and the app ships no chart library. These functions return
plain geometry so the templates contain no arithmetic.
"""
import math
from dataclasses import dataclass
from typing import Optional

from app.analysis.aggregate import OCTANTS

# Rating colours, matched to Bootstrap's success/warning/danger.
RATING_COLORS = {
    "green": "#198754",
    "yellow": "#ffc107",
    "red": "#dc2626",
}


@dataclass(frozen=True)
class RoseSector:
    """One wind-rose petal, as an SVG path plus its label position."""
    sector: str
    days: int
    percent: float
    path: str
    label_x: float
    label_y: float


@dataclass(frozen=True)
class Bar:
    """One histogram bar, sized as a percentage of the largest value."""
    label: str
    days: int
    percent: float          # share of all days, for the readout
    width_pct: float        # bar length relative to the biggest bar
    color: Optional[str] = None


def _polar(cx: float, cy: float, radius: float, degrees: float) -> tuple[float, float]:
    """SVG coordinates for a compass bearing (0 deg = north = up)."""
    radians = math.radians(degrees - 90)
    return (cx + radius * math.cos(radians), cy + radius * math.sin(radians))


def wind_rose(distribution: dict, size: float = 220.0) -> list[RoseSector]:
    """Eight petals scaled by frequency (PRD §9.7).

    `distribution` is the summary's direction_distribution: sector ->
    {"days": n, "percent": p}. Petal radius is proportional to the share of
    days, so the shape reads as a wind rose rather than a pie.
    """
    cx = cy = size / 2
    max_radius = size / 2 - 18          # leave room for the sector labels
    biggest = max((d["days"] for d in distribution.values()), default=0)

    sectors = []
    for i, name in enumerate(OCTANTS):
        entry = distribution.get(name, {"days": 0, "percent": 0.0})
        bearing = i * 45
        # Petals span their full 45 deg sector, minus a hairline gap.
        start, end = bearing - 21.5, bearing + 21.5
        radius = (max_radius * entry["days"] / biggest) if biggest else 0.0

        if radius > 0:
            x1, y1 = _polar(cx, cy, radius, start)
            x2, y2 = _polar(cx, cy, radius, end)
            path = (f"M {cx:.1f} {cy:.1f} L {x1:.1f} {y1:.1f} "
                    f"A {radius:.1f} {radius:.1f} 0 0 1 {x2:.1f} {y2:.1f} Z")
        else:
            path = ""                    # nothing to draw for an unused sector

        label_x, label_y = _polar(cx, cy, max_radius + 10, bearing)
        sectors.append(RoseSector(
            sector=name,
            days=entry["days"],
            percent=entry["percent"],
            path=path,
            label_x=round(label_x, 1),
            label_y=round(label_y, 1),
        ))
    return sectors


def _bars(counts: dict, colors: Optional[dict] = None) -> list[Bar]:
    biggest = max((c["days"] for c in counts.values()), default=0)
    return [
        Bar(
            label=label,
            days=entry["days"],
            percent=entry["percent"],
            width_pct=round(100.0 * entry["days"] / biggest, 1) if biggest else 0.0,
            color=(colors or {}).get(label),
        )
        for label, entry in counts.items()
    ]


def wind_histogram(summary: dict) -> list[Bar]:
    """Wind-speed distribution bars (PRD §9.7)."""
    return _bars(summary["wind_bins"])


def rating_bars(summary: dict) -> list[Bar]:
    """Green/yellow/red distribution, in their own colours."""
    return _bars(summary["sailing_categories"]["ratings"], RATING_COLORS)


def rainfall_histogram(days: list, bins: tuple = (0.1, 1.0, 5.0, 20.0)) -> list[Bar]:
    """Daily-rainfall bars (PRD §9.7).

    Rain is heavily zero-inflated, so a dry bucket is kept separate rather
    than swamping a linear axis.
    """
    measured = [d.rain_total_mm for d in days if d.rain_total_mm is not None]
    labels = ["dry", "<1", "1-5", "5-20", ">20"]
    counts = {label: 0 for label in labels}
    for value in measured:
        if value < bins[0]:
            counts["dry"] += 1
        elif value < bins[1]:
            counts["<1"] += 1
        elif value < bins[2]:
            counts["1-5"] += 1
        elif value <= bins[3]:
            counts["5-20"] += 1
        else:
            counts[">20"] += 1

    total = len(measured)
    return _bars({
        label: {"days": n, "percent": round(100.0 * n / total, 1) if total else 0.0}
        for label, n in counts.items()
    })
