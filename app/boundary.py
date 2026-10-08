"""The offshore limit line: where the sea is more than N miles from any shore.

A skipper's licence is bounded by distance from the coast, and islands count —
being 30 miles off the mainland is within a 20-mile limit if an island lies 5
miles away. So the measure is distance to the nearest coastline of anything,
which falls out of the geometry without needing to know what is mainland and
what is an islet.

The line is found by sampling that distance over a grid and tracing the
contour at the limit with marching squares. Sampling beats an exact offset
curve here: coastlines are fractal, an exact buffer of every islet would be a
huge polygon, and the skipper only needs to see where the limit runs.
"""
import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

# A nautical mile is one minute of latitude, so latitude converts to miles by
# a constant and longitude by that constant times cos(latitude).
NM_PER_DEGREE_LAT = 60.0

# Past this there is no point measuring: the exact distance far offshore does
# not change where a 20-mile line runs, and an uncapped search scans the whole
# coastline for every open-sea sample. Capping cut a test field from 11s to
# 2.3s and is what keeps the computation interactive.
MAX_SEARCH_NM = 40.0

# Index cell size. Small enough that a query usually settles in the first
# ring or two, large enough that building the index stays cheap.
CELL_NM = 1.0


@dataclass(frozen=True)
class Contour:
    """The limit line, as polylines of (lat, lon) in degrees."""
    limit_nm: float
    lines: Sequence[Sequence[tuple[float, float]]]

    @property
    def is_empty(self) -> bool:
        return not self.lines


class _SegmentIndex:
    """Coastline segments in a uniform grid, for nearest-distance queries.

    Projected once to a local equirectangular frame in nautical miles: over a
    single viewport the distortion is far below the resolution of the sampling
    grid, and it makes the distance arithmetic plain Euclidean.
    """

    def __init__(self, ways: Iterable[Sequence[tuple[float, float]]],
                 reference_lat: float):
        self.lon_scale = NM_PER_DEGREE_LAT * math.cos(math.radians(reference_lat))
        self.segments: list[tuple[float, float, float, float]] = []
        for way in ways:
            for (lat1, lon1), (lat2, lon2) in zip(way, way[1:]):
                self.segments.append((
                    lon1 * self.lon_scale, lat1 * NM_PER_DEGREE_LAT,
                    lon2 * self.lon_scale, lat2 * NM_PER_DEGREE_LAT,
                ))

        self.grid: dict[tuple[int, int], list[int]] = {}
        for i, (x1, y1, x2, y2) in enumerate(self.segments):
            for cx in range(int(min(x1, x2) // CELL_NM),
                            int(max(x1, x2) // CELL_NM) + 1):
                for cy in range(int(min(y1, y2) // CELL_NM),
                                int(max(y1, y2) // CELL_NM) + 1):
                    self.grid.setdefault((cx, cy), []).append(i)

    @property
    def is_empty(self) -> bool:
        return not self.segments

    def project(self, lat: float, lon: float) -> tuple[float, float]:
        return lon * self.lon_scale, lat * NM_PER_DEGREE_LAT

    def distance_nm(self, lat: float, lon: float,
                    cap_nm: float = MAX_SEARCH_NM) -> float:
        """Distance to the nearest coastline, in NM, saturating at cap_nm.

        Searches rings of cells outward, stopping as soon as the best distance
        found is closer than the edge of the ring still unexamined — anything
        further out cannot beat it.
        """
        px, py = self.project(lat, lon)
        cx0, cy0 = int(px // CELL_NM), int(py // CELL_NM)

        best = cap_nm
        ring = 0
        while ring * CELL_NM <= cap_nm:
            for cx in range(cx0 - ring, cx0 + ring + 1):
                for cy in range(cy0 - ring, cy0 + ring + 1):
                    # Only the newly added shell; inner cells were done.
                    if ring and abs(cx - cx0) != ring and abs(cy - cy0) != ring:
                        continue
                    for i in self.grid.get((cx, cy), ()):
                        d = _point_segment_nm(px, py, self.segments[i])
                        if d < best:
                            best = d
            if best <= ring * CELL_NM:
                break
            ring += 1
        return best


def _point_segment_nm(px: float, py: float,
                      segment: tuple[float, float, float, float]) -> float:
    ax, ay, bx, by = segment
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _trace_contour(field: Sequence[Sequence[float]],
                   lat_of, lon_of, limit_nm: float
                   ) -> list[list[tuple[float, float]]]:
    """Marching squares over a sampled field, chained into polylines."""
    def interpolate(v1, v2, p1, p2):
        if abs(v2 - v1) < 1e-9:
            return p1
        t = (limit_nm - v1) / (v2 - v1)
        return (p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t)

    rows, cols = len(field), len(field[0])
    edges: list[tuple[tuple[float, float], tuple[float, float]]] = []

    for j in range(rows - 1):
        for i in range(cols - 1):
            values = (field[j][i], field[j][i + 1],
                      field[j + 1][i + 1], field[j + 1][i])
            corners = ((lat_of(j), lon_of(i)), (lat_of(j), lon_of(i + 1)),
                       (lat_of(j + 1), lon_of(i + 1)), (lat_of(j + 1), lon_of(i)))
            # All corners on one side of the limit: no line through this cell.
            state = sum(1 << n for n in range(4) if values[n] > limit_nm)
            if state in (0, 15):
                continue
            crossings = []
            for n in range(4):
                a, b = n, (n + 1) % 4
                if (values[a] > limit_nm) != (values[b] > limit_nm):
                    crossings.append(
                        interpolate(values[a], values[b], corners[a], corners[b]))
            # A saddle cell yields four crossings; pairing them in order keeps
            # the line continuous, which is all that matters for display.
            for k in range(0, len(crossings) - 1, 2):
                edges.append((crossings[k], crossings[k + 1]))

    return _chain(edges)


def _chain(edges) -> list[list[tuple[float, float]]]:
    """Join loose edges into polylines by their shared endpoints."""
    def key(point):
        return (round(point[0], 6), round(point[1], 6))

    neighbours: dict = {}
    for a, b in edges:
        neighbours.setdefault(key(a), []).append(b)
        neighbours.setdefault(key(b), []).append(a)

    visited: set = set()
    lines = []
    for a, _ in edges:
        if key(a) in visited:
            continue
        line = [a]
        current, previous = a, None
        while True:
            visited.add(key(current))
            nxt = [p for p in neighbours.get(key(current), ())
                   if key(p) != previous and key(p) not in visited]
            if not nxt:
                break
            previous, current = key(current), nxt[0]
            line.append(current)
        if len(line) > 2:
            lines.append(line)
    return lines


def offshore_limit(
    ways: Iterable[Sequence[tuple[float, float]]],
    south: float, west: float, north: float, east: float,
    limit_nm: float = 20.0,
    resolution_nm: float = 2.0,
) -> Contour:
    """Trace the limit_nm line through the given bbox.

    resolution_nm is the sampling step: finer follows narrow channels more
    closely and costs proportionally more time.
    """
    if limit_nm <= 0:
        raise ValueError("limit_nm must be positive")
    if limit_nm > MAX_SEARCH_NM:
        raise ValueError(f"limit_nm must not exceed {MAX_SEARCH_NM} NM")

    index = _SegmentIndex(ways, reference_lat=(south + north) / 2)
    if index.is_empty:
        # No coastline in range: everywhere is "far from shore" and there is
        # no line to draw. An empty contour says that honestly.
        return Contour(limit_nm=limit_nm, lines=[])

    lon_scale = index.lon_scale
    rows = max(2, int((north - south) * NM_PER_DEGREE_LAT / resolution_nm) + 1)
    cols = max(2, int((east - west) * lon_scale / resolution_nm) + 1)

    def lat_of(j: float) -> float:
        return south + (north - south) * j / (rows - 1)

    def lon_of(i: float) -> float:
        return west + (east - west) * i / (cols - 1)

    # Saturate at the limit plus a margin: values beyond only need to read as
    # "further than the limit" for the contour to be in the right place.
    cap = min(MAX_SEARCH_NM, limit_nm * 1.5)
    field = [[index.distance_nm(lat_of(j), lon_of(i), cap_nm=cap)
              for i in range(cols)] for j in range(rows)]

    return Contour(limit_nm=limit_nm,
                   lines=_trace_contour(field, lat_of, lon_of, limit_nm))


@dataclass(frozen=True)
class ComputedArea:
    """A contour that has already been computed, with the view it covers."""
    south: float
    west: float
    north: float
    east: float
    limit_nm: float
    lines: Sequence[Sequence[tuple[float, float]]]

    def covers(self, south: float, west: float,
               north: float, east: float) -> bool:
        return (south >= self.south and north <= self.north
                and west >= self.west and east <= self.east)

    def as_dict(self) -> dict:
        return {
            "south": self.south, "west": self.west,
            "north": self.north, "east": self.east,
            "limit_nm": self.limit_nm,
            "lines": [[[round(lat, 5), round(lon, 5)] for lat, lon in line]
                      for line in self.lines],
        }

    @staticmethod
    def from_dict(raw: dict) -> "ComputedArea":
        return ComputedArea(
            south=float(raw["south"]), west=float(raw["west"]),
            north=float(raw["north"]), east=float(raw["east"]),
            limit_nm=float(raw["limit_nm"]),
            lines=[[(float(lat), float(lon)) for lat, lon in line]
                   for line in raw["lines"]],
        )
