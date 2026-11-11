"""
GPX export tool for the hike planner agent.

Turns a route previously computed by tools/routing.py into a GPX 1.1 file
using gpxpy (https://pypi.org/project/gpxpy/), so the plan can be loaded
onto a GPS watch or into apps like OsmAnd, Komoot or Garmin Connect.

Design notes:
- The route geometry never passes through the LLM. get_hiking_route stores
  the full GraphHopper polyline (lat, lon, ele per point) in an in-process
  registry under a short route_id; this tool looks the geometry up by that
  id. The model only supplies the id plus a human-readable name/description,
  so it cannot invent or mangle coordinates.
- Output: one <wpt> per named stop (start, via points, finish) and one <trk>
  whose segments are split at the via points, so each leg is its own
  <trkseg>. Elevation comes from GraphHopper's elevation model.
- The file name is derived from the resolved place names and today's date,
  not from free text supplied by the model.

Standalone usage (routes and exports in one go; needs GRAPHHOPPER_API_KEY):
    python3 -m tools.gpx_export <point1> <point2> [<point3> ...]
"""

import math
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import gpxpy
import gpxpy.gpx

from tools.routing import get_stored_route

OUTPUT_DIR = Path("output")
CREATOR = "hike-planner-agent"
MAX_SLUG_LEN = 80


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return text.strip("-")


def _nearest_index(
    geometry: list[tuple[float, float, float]], lat: float, lon: float, start: int = 0
) -> int:
    """Index of the geometry point closest to (lat, lon), searching from `start`.

    Uses an equirectangular approximation, which is plenty for picking the
    nearest vertex of a hiking route a few tens of km long.
    """
    cos_lat = math.cos(math.radians(lat))
    best_i, best_d = start, float("inf")
    for i in range(start, len(geometry)):
        plat, plon, _ = geometry[i]
        d = (plat - lat) ** 2 + ((plon - lon) * cos_lat) ** 2
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def _build_gpx(route: dict, name: str | None, description: str | None) -> gpxpy.gpx.GPX:
    geometry = route["geometry"]
    labels = route["resolved_waypoints"]
    coords = route["waypoint_coords"]

    gpx = gpxpy.gpx.GPX()
    gpx.creator = CREATOR
    gpx.name = name or " - ".join(labels)
    gpx.description = description
    gpx.time = datetime.now(timezone.utc)

    # Where along the polyline each named stop falls. The first and last
    # stops are pinned to the ends; via points are found in order so the
    # resulting segment boundaries are monotonic.
    split_indices = [0]
    for wp in coords[1:-1]:
        split_indices.append(_nearest_index(geometry, wp["lat"], wp["lon"], start=split_indices[-1]))
    split_indices.append(len(geometry) - 1)

    for label, wp, idx in zip(labels, coords, split_indices):
        gpx.waypoints.append(
            gpxpy.gpx.GPXWaypoint(
                latitude=wp["lat"],
                longitude=wp["lon"],
                elevation=geometry[idx][2],
                name=label,
            )
        )

    track = gpxpy.gpx.GPXTrack(name=gpx.name, description=description)
    gpx.tracks.append(track)
    for leg_start, leg_end in zip(split_indices, split_indices[1:]):
        segment = gpxpy.gpx.GPXTrackSegment()
        for lat, lon, ele in geometry[leg_start : leg_end + 1]:
            segment.points.append(gpxpy.gpx.GPXTrackPoint(lat, lon, elevation=ele))
        track.segments.append(segment)

    return gpx


def export_gpx(route_id: str, name: str | None = None, description: str | None = None) -> dict:
    """Write the route identified by route_id to a GPX file.

    Args:
        route_id: The id returned by get_hiking_route.
        name: Optional human-readable trip name stored in the GPX metadata.
        description: Optional plan text (dates, weather notes, advice) stored
            in the GPX metadata so it travels with the file.

    Returns:
        A dict with the absolute path of the written file and a few counts
        describing its contents.
    """
    route = get_stored_route(route_id)
    gpx = _build_gpx(route, name, description)

    slug = _slugify("_".join(route["resolved_waypoints"]))[:MAX_SLUG_LEN] or route_id
    filename = f"{date.today().isoformat()}_{slug}.gpx"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    path.write_text(gpx.to_xml(), encoding="utf-8")

    return {
        "gpx_path": str(path.resolve()),
        "waypoints": len(gpx.waypoints),
        "track_segments": len(gpx.tracks[0].segments),
        "track_points": gpx.get_track_points_no(),
        "distance_2d": round(gpx.length_2d()),
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 -m tools.gpx_export <point1> <point2> [<point3> ...]")
        sys.exit(1)

    from tools.routing import get_hiking_route

    route = get_hiking_route(sys.argv[1:])
    result = export_gpx(route["route_id"])
    for key, value in result.items():
        print(f"{key}: {value}")
