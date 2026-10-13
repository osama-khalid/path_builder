"""
path_builder.py
---------------
Turn a handful of GPS waypoints (e.g. from photo EXIF data) into a realistic
route that follows real trails, using OpenStreetMap data.

How it works
------------
1. Read waypoints (lat, lon) from a CSV, in visiting order.
2. Download walkable/rideable ways (path, footway, cycleway, track, ...) in the
   waypoints' bounding box from the public Overpass API (no API key needed).
3. Build a node graph from those ways.
4. Snap each waypoint to its nearest graph node.
5. Run Dijkstra between consecutive snapped waypoints and stitch the results.

Usage
-----
    python path_builder.py examples/waypoints.csv -o route
    # writes route.geojson and route.csv

Standard library only.
"""

import argparse
import csv
import hashlib
import heapq
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CACHE_DIR = Path(".cache")
USER_AGENT = "path_builder/0.1 (open-source trail snapping tool)"

DEFAULT_HIGHWAYS = (
    "path|footway|cycleway|track|bridleway|steps|pedestrian|"
    "living_street|residential|service|unclassified|tertiary|secondary"
)


# ── Geometry ─────────────────────────────────────────────────────────────────

def haversine(a, b):
    """Distance in metres between two (lat, lon) points."""
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# ── Input ────────────────────────────────────────────────────────────────────

def read_waypoints(path):
    """Read a CSV with latitude/longitude columns (lat/lon/lng also accepted)."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"No rows found in {path}")
    keys = {k.lower().strip(): k for k in rows[0]}
    lat_key = next((keys[k] for k in ("latitude", "lat") if k in keys), None)
    lon_key = next((keys[k] for k in ("longitude", "lon", "lng") if k in keys), None)
    if not lat_key or not lon_key:
        sys.exit("CSV needs latitude and longitude columns (lat/lon also accepted).")
    return [(float(r[lat_key]), float(r[lon_key])) for r in rows]


# ── OpenStreetMap ────────────────────────────────────────────────────────────

def fetch_osm(bbox, highways, use_cache=True):
    """Fetch ways + nodes in bbox=(south, west, north, east); cached on disk."""
    query = (
        f'[out:json][timeout:90];'
        f'way["highway"~"^({highways})$"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});'
        f'(._;>;);out body;'
    )
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / (hashlib.sha1(query.encode()).hexdigest() + ".json")
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text())

    req = urllib.request.Request(
        OVERPASS_URL,
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    cache_file.write_text(json.dumps(data))
    return data


def build_graph(osm):
    """Return (coords, adj): node id -> (lat, lon), node id -> [(nbr, metres)]."""
    coords = {
        e["id"]: (e["lat"], e["lon"]) for e in osm["elements"] if e["type"] == "node"
    }
    adj = {}
    for e in osm["elements"]:
        if e["type"] != "way":
            continue
        nodes = [n for n in e.get("nodes", []) if n in coords]
        for a, b in zip(nodes, nodes[1:]):
            d = haversine(coords[a], coords[b])
            adj.setdefault(a, []).append((b, d))
            # Walkers/cyclists can ignore oneway, so every edge is two-way.
            adj.setdefault(b, []).append((a, d))
    return coords, adj


# ── Routing ──────────────────────────────────────────────────────────────────

def nearest_node(point, coords, adj):
    best, best_d = None, float("inf")
    for nid in adj:
        d = haversine(point, coords[nid])
        if d < best_d:
            best, best_d = nid, d
    return best, best_d


def dijkstra(adj, src, dst):
    """Shortest path of node ids from src to dst, or None if unreachable."""
    dist, prev = {src: 0.0}, {}
    heap = [(0.0, src)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == dst:
            path = [u]
            while u in prev:
                u = prev[u]
                path.append(u)
            return path[::-1]
        if d > dist.get(u, float("inf")):
            continue
        for v, w in adj.get(u, ()):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v], prev[v] = nd, u
                heapq.heappush(heap, (nd, v))
    return None


def build_route(waypoints, coords, adj, max_snap):
    """Stitch shortest paths between consecutive waypoints into one polyline."""
    snapped = []
    for i, wp in enumerate(waypoints):
        nid, d = nearest_node(wp, coords, adj)
        if nid is None or d > max_snap:
            print(f"warning: waypoint {i} is {d:.0f} m from any trail; skipping",
                  file=sys.stderr)
            continue
        snapped.append(nid)

    route = []
    for a, b in zip(snapped, snapped[1:]):
        seg = dijkstra(adj, a, b)
        if seg is None:
            print(f"warning: no trail connection {a} -> {b}; drawing a straight line",
                  file=sys.stderr)
            seg = [a, b]
        route.extend(seg if not route else seg[1:])
    return [coords[n] for n in route]


# ── Output ───────────────────────────────────────────────────────────────────

def write_outputs(route, stem):
    stem = Path(stem)
    geojson = {
        "type": "Feature",
        "properties": {"length_m": round(sum(
            haversine(a, b) for a, b in zip(route, route[1:])), 1)},
        "geometry": {"type": "LineString",
                     "coordinates": [[lon, lat] for lat, lon in route]},
    }
    stem.with_suffix(".geojson").write_text(json.dumps(geojson))
    with stem.with_suffix(".csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["latitude", "longitude"])
        w.writerows(route)
    return geojson["properties"]["length_m"]


def main():
    p = argparse.ArgumentParser(description="Snap GPS waypoints to real OSM trails.")
    p.add_argument("waypoints", help="CSV with latitude,longitude columns, in order")
    p.add_argument("-o", "--output", default="route", help="output file stem")
    p.add_argument("--pad", type=float, default=0.01,
                   help="degrees of padding around the waypoint bbox (default 0.01)")
    p.add_argument("--max-snap", type=float, default=300,
                   help="max metres a waypoint may be from a trail (default 300)")
    p.add_argument("--highways", default=DEFAULT_HIGHWAYS,
                   help="OSM highway values to allow, separated by |")
    p.add_argument("--no-cache", action="store_true", help="ignore the on-disk cache")
    args = p.parse_args()

    wps = read_waypoints(args.waypoints)
    lats, lons = [w[0] for w in wps], [w[1] for w in wps]
    bbox = (min(lats) - args.pad, min(lons) - args.pad,
            max(lats) + args.pad, max(lons) + args.pad)

    print(f"{len(wps)} waypoints; fetching OSM trails for bbox {bbox} ...")
    coords, adj = build_graph(fetch_osm(bbox, args.highways, not args.no_cache))
    print(f"graph: {len(adj):,} nodes")

    route = build_route(wps, coords, adj, args.max_snap)
    if len(route) < 2:
        sys.exit("Could not build a route (too few waypoints near trails).")
    length = write_outputs(route, args.output)
    print(f"route: {len(route):,} points, {length / 1000:.2f} km -> "
          f"{args.output}.geojson, {args.output}.csv")


if __name__ == "__main__":
    main()
