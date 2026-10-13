# path_builder

Turn a few GPS waypoints (for example, from photo EXIF data) into a route that
follows real trails, using [OpenStreetMap](https://www.openstreetmap.org) data.

Straight lines between waypoints cut across ridges and lakes. `path_builder`
snaps each waypoint to the actual trail network and finds the shortest path
along real paths between them.

## How it works

1. Reads waypoints (`latitude`, `longitude`) from a CSV, in visiting order.
2. Downloads trails, paths and roads around them from the public
   [Overpass API](https://overpass-api.de) (no account or API key needed).
3. Builds a node graph from the OSM ways.
4. Snaps each waypoint to its nearest graph node.
5. Runs Dijkstra's shortest path between consecutive waypoints and stitches the
   segments into one route.

## Requirements

Python 3.8+. No third-party packages.

## Usage

```bash
python path_builder.py examples/waypoints.csv -o route
```

This writes:

- `route.geojson`: a `LineString` you can drop into [geojson.io](https://geojson.io), QGIS, etc.
- `route.csv`: `latitude,longitude` points in order, ready for animation or plotting.

### Input format

```csv
latitude,longitude
37.5035,-121.8830
37.5120,-121.8815
```

`lat`/`lon`/`lng` column names also work. Extra columns are ignored.

### Options

| Flag | Default | Meaning |
|------|---------|---------|
| `-o, --output` | `route` | Output file stem |
| `--pad` | `0.01` | Degrees of padding around the waypoints' bounding box |
| `--max-snap` | `300` | Skip waypoints farther than this many metres from any trail |
| `--highways` | paths, tracks, roads | OSM `highway` values to allow, separated by `\|` |
| `--no-cache` | off | Ignore the on-disk Overpass cache (`.cache/`) |

## Tips

- If a route picks the wrong branch at a junction, add a waypoint closer to the
  trail you actually took, since the shortest path is only as good as its anchors.
- For a known named trail, restrict `--highways` (for example `path|footway|cycleway`)
  so routing stays on trails instead of nearby roads.
- Waypoints in the same bounding box share one cached download, so re-runs are fast.

## Data and etiquette

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright),
available under the ODbL. The tool caches responses locally and sends a
descriptive `User-Agent`; please don't hammer the public Overpass server.

