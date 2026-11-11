"""
Routing tool for the hike planner agent.

Uses GraphHopper's Directions API (free tier, foot profile, elevation
included) for both routing and geocoding. Originally built on
OpenRouteService (ORS), but ORS's Directions V2 API began rejecting a
valid, unexpired key with 403 "Access to this API has been disallowed" —
confirmed on both api.openrouteservice.org and api.heigit.org as of
2026-08-17, despite the dashboard showing unused quota. Matched an
unresolved report on the ORS community forum from ~2026-07-25 describing
identical symptoms. Appears to be an account-side bug on HeiGIT's end, not
a code or key issue. Support ticket filed; migrated to GraphHopper to keep
the build moving rather than wait on a fix.

Supports routing through an ordered list of 2+ waypoints, not just a
single start/finish pair.

The full route geometry (every point with elevation) is kept in an
in-process registry keyed by a short route_id, so that tools/gpx_export.py
can write a GPX file later without the geometry ever passing through the
LLM's context. Only the summary numbers and the route_id are returned to
the agent.

Geocoding accuracy note: general-purpose geocoders (tested: Pelias/ORS,
GraphHopper) are unreliable for short alpine hut/peak names without
strong disambiguation — confirmed failures include a hut name resolving
to a nearby peak, and a Bulgarian summit name resolving to an unrelated
peak in Pakistan, even with a regional location bias applied. KNOWN_LOCATIONS
is the trusted source of truth for any place actually tested; the live
geocoding API is a best-effort fallback for anything not yet in that table.
"""

import os
import sys
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

GH_API_KEY = os.getenv("GRAPHHOPPER_API_KEY")
GH_ROUTE_URL = "https://graphhopper.com/api/1/route"
GH_GEOCODE_URL = "https://graphhopper.com/api/1/geocode"

KNOWN_LOCATIONS = {
    "malyovitsa hut": (23.37430, 42.18876),
    "malyovitsa peak": (23.3631, 42.1736),
    "ivan vazov hut": (23.2835, 42.1897),
    "musala hut": (23.5872, 42.1981),
    "musala peak": (23.5853, 42.1792),
    "rila monastery": (23.3403, 42.1333),
    "bukovets hut": (25.8877, 42.7895),
    "chumerna hut": (25.9600, 42.7867),
    "aglikina meadow": (26.1557, 42.8169),
    "luda kamchiya spring": (26.2125, 42.8198),
    # add more as you use them
}

# route_id -> full route data (geometry, labels, waypoint coords, stats).
# Populated by get_hiking_route; read by tools/gpx_export.py.
_ROUTES: dict[str, dict] = {}


def get_stored_route(route_id: str) -> dict:
    """Return the stored route for route_id, or raise if it is unknown."""
    route = _ROUTES.get(route_id)
    if route is None:
        known = ", ".join(sorted(_ROUTES)) or "none"
        raise KeyError(
            f"Unknown route_id {route_id!r}. Known route ids in this run: {known}. "
            "Call get_hiking_route first."
        )
    return route

def _geocode(place: str) -> tuple[tuple[float, float], str]:
    key = place.strip().lower()
    if key in KNOWN_LOCATIONS:
        lon, lat = KNOWN_LOCATIONS[key]
        print(f"  Using known location for {place!r} -> ({lat:.4f}, {lon:.4f})")
        return (lon, lat), place

    response = requests.get(
        GH_GEOCODE_URL,
        params={"q": place, "locale": "en", "limit": 1, "key": GH_API_KEY},
        timeout=10,
    )
    response.raise_for_status()
    hits = response.json()["hits"]
    if not hits:
        raise ValueError(f"No geocoding results found for {place!r}")
    point = hits[0]["point"]
    lat, lon = point["lat"], point["lng"]
    label = hits[0].get("name", place)
    print(f"  Geocoded {place!r} -> {label} ({lat:.4f}, {lon:.4f})")
    return (lon, lat), label

def get_hiking_route(waypoints: list[str]) -> dict:
    """Geocode a sequence of place names and fetch a foot-hiking route through them in order.

    Args:
        waypoints: Ordered list of place names, e.g. [start, via_point, finish].

    Returns:
        A dict with a route_id (for later GPX export), resolved labels,
        coordinates, total distance (meters), estimated duration (seconds),
        and an elevation profile.
    """
    resolved = [_geocode(wp) for wp in waypoints]
    coords = [(lat, lon) for (lon, lat), _ in resolved]
    labels = [label for _, label in resolved]

    response = requests.get(
        GH_ROUTE_URL,
        params={
            "point": [f"{lat},{lon}" for lat, lon in coords],
            "vehicle": "foot",
            "elevation": "true",
            "points_encoded": "false",
            "key": GH_API_KEY,
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()

    path = payload["paths"][0]
    # GraphHopper returns GeoJSON order: [lon, lat, ele] per point.
    geometry = [(pt[1], pt[0], pt[2]) for pt in path["points"]["coordinates"]]
    elevations = [ele for _, _, ele in geometry]

    summary = {
        "resolved_waypoints": labels,
        "waypoint_coords": [{"lat": lat, "lon": lon} for lat, lon in coords],
        "distance": path["distance"],
        "duration": path["time"] / 1000,
        "elevation_profile": {
            "min": min(elevations),
            "max": max(elevations),
            "total_ascent": path.get("ascend", 0),
            "total_descent": path.get("descend", 0),
        },
    }

    route_id = uuid.uuid4().hex[:8]
    _ROUTES[route_id] = {
        **summary,
        "requested_waypoints": list(waypoints),
        "geometry": geometry,  # list of (lat, lon, ele)
    }
    return {"route_id": route_id, **summary}

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 tools/routing.py <point1> <point2> [<point3> ...]")
        sys.exit(1)

    waypoints = sys.argv[1:]
    route = get_hiking_route(waypoints)
    print(f"Route id: {route['route_id']}")
    print(f"Waypoints: {route['resolved_waypoints']}")
    print(f"Coords: {route['waypoint_coords']}")
    print(f"Distance: {route['distance']} m")
    print(f"Duration: {route['duration']} s")
    print(f"Elevation: {route['elevation_profile']}")