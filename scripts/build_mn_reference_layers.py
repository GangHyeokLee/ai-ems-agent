"""Build the Mongolia *reference-only* 110 kV line and substation layers for map review.

The output is a separate JSON file next to ``mn-osm-35k-map.json``. It never modifies the
existing map, the 166-bus calculation model, the XIIDM or any PF/N-1/Sensitivity result.
Reference equipment is NOT part of any electrical model and has no electrical connection to
model buses: lines are drawn as straight segments between the two ends of the original OSM way.

Inputs are read-only:
- the 2026-10-20 coverage audit (collapsed segments and OSM lineage CSVs),
- PyPSA-Earth MN_35k raw OSM GeoJSON and stored build lines (existing map line geometry),
- the active map JSON (buses) whose SHA-256 is pinned in the output, and the unsimplified
  MN_35k country shape for inside/outside decisions,
- GADM admin shapes for region labels in the review summary.

Standard library only, so the selection rules are unit-testable in the AI-EMS venv.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

SCHEMA = "mn-reference-layers/v1"
GRID_ID = "mn-osm-35k"
REFERENCE_VOLTAGE_V = 110000
EARTH_RADIUS_KM = 6371.0088

# Selection thresholds (documented in the output so a reviewer can re-check every decision).
MIN_PATH_KM = 0.5                 # shorter ways are gantry/entry spans inside a merged station
MIN_CHORD_RATIO = 0.5             # straight chord must represent at least half of the OSM path
EXISTING_OVERLAP_KM = 0.15        # corridor half-width around existing map line geometry
EXISTING_OVERLAP_SHARE = 0.8      # share of the way inside that corridor -> duplicate
CANDIDATE_OVERLAP_KM = 0.10       # corridor half-width around a longer accepted candidate
CANDIDATE_OVERLAP_SHARE = 0.8
SAMPLE_STEP_KM = 0.02
SUBSTATION_MIN_VOLTAGE_V = 35000
SUBSTATION_SAME_AS_BUS_KM = 1.0   # within this distance the existing map bus already marks the site
SUBSTATION_DUPLICATE_KM = 0.2     # two OSM objects this close are treated as one site

DEFAULT_EARTH = Path("../mongolia-grid-poc/pypsa-earth")
DEFAULT_AUDIT = Path("../mongolia-grid-poc/docs/audits/2026-10-20")


# ---------------------------------------------------------------- geometry helpers (lon, lat)
def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def path_km(points: list[tuple[float, float]]) -> float:
    return sum(haversine_km(p, q) for p, q in zip(points, points[1:]))


def _local_xy(point, origin_lat: float) -> tuple[float, float]:
    k = math.pi / 180 * EARTH_RADIUS_KM
    return point[0] * k * math.cos(math.radians(origin_lat)), point[1] * k


def _point_segment_km(p, a, b) -> float:
    lat0 = p[1]
    px, py = _local_xy(p, lat0)
    ax, ay = _local_xy(a, lat0)
    bx, by = _local_xy(b, lat0)
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _bbox(parts: list[list[tuple[float, float]]]) -> tuple[float, float, float, float]:
    xs = [p[0] for part in parts for p in part]
    ys = [p[1] for part in parts for p in part]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_near(box, p, margin_deg: float) -> bool:
    return box[0] - margin_deg <= p[0] <= box[2] + margin_deg and box[1] - margin_deg <= p[1] <= box[3] + margin_deg


def samples(points: list[tuple[float, float]], step_km: float = SAMPLE_STEP_KM) -> list[tuple[tuple[float, float], float]]:
    """Points along a polyline, each with the path length it represents (for length-weighted shares)."""
    out = []
    for a, b in zip(points, points[1:]):
        length = haversine_km(a, b)
        n = max(1, math.ceil(length / step_km))
        for i in range(n):
            t = (i + 0.5) / n
            out.append(((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), length / n))
    return out


def corridor_share(points, others: list[list[list[tuple[float, float]]]], width_km: float) -> list[float]:
    """Length share of ``points`` within ``width_km`` of each polyline-set in ``others``."""
    pts = samples(points)
    total = sum(w for _, w in pts) or 1.0
    margin = width_km / 80.0 + 0.01   # degrees; generous for longitude at Mongolian latitudes
    shares = []
    for parts in others:
        box = _bbox(parts)
        inside = 0.0
        for p, w in pts:
            if not _bbox_near(box, p, margin):
                continue
            if any(_point_segment_km(p, a, b) <= width_km for part in parts for a, b in zip(part, part[1:])):
                inside += w
        shares.append(inside / total)
    return shares


def point_in_polygon(point, rings: list[list[list[float]]]) -> bool:
    """Even-odd rule over all rings (outer + holes)."""
    x, y = point
    inside = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
    return inside


def polygon_centroid(ring: list[list[float]]) -> tuple[float, float]:
    area = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area) < 1e-18:
        return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
    return cx / (3 * area), cy / (3 * area)


def line_parts(geometry: dict) -> list[list[tuple[float, float]]]:
    if geometry["type"] == "LineString":
        return [[tuple(p[:2]) for p in geometry["coordinates"]]]
    if geometry["type"] == "MultiLineString":
        return [[tuple(p[:2]) for p in part] for part in geometry["coordinates"]]
    raise ValueError(f"unsupported line geometry {geometry['type']}")


def feature_location(geometry: dict) -> tuple[float, float]:
    if geometry["type"] == "Point":
        return tuple(geometry["coordinates"][:2])
    if geometry["type"] == "Polygon":
        return polygon_centroid(geometry["coordinates"][0])
    if geometry["type"] == "LineString":   # unclosed substation outline way
        return polygon_centroid(geometry["coordinates"])
    if geometry["type"] == "MultiPolygon":
        largest = max(geometry["coordinates"], key=lambda poly: len(poly[0]))
        return polygon_centroid(largest[0])
    raise ValueError(f"unsupported substation geometry {geometry['type']}")


# ---------------------------------------------------------------- tag helpers
def parse_other_tags(raw) -> dict:
    if not raw or raw == "UNKNOWN":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = ast.literal_eval(str(raw))
    except (ValueError, SyntaxError):
        return {}
    return value if isinstance(value, dict) else {}


def voltages(tag) -> list[int]:
    if tag in (None, "", "UNKNOWN"):
        return []
    out = []
    for part in str(tag).split(";"):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return sorted(set(out), reverse=True)


def osm_url(kind: str, osm_id: str) -> str:
    return f"https://www.openstreetmap.org/{kind}/{osm_id}"


# ---------------------------------------------------------------- IO
def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def read_features(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["features"]


# ---------------------------------------------------------------- selection: 110 kV lines
def inside_country(point, country_polygons: list) -> bool:
    return any(point_in_polygon(point, polygon) for polygon in country_polygons)


def select_lines(collapsed: list[dict], raw_lines: dict[str, dict], lineage: dict[str, dict],
                 existing: dict[str, list[list[tuple[float, float]]]], buses: list[dict],
                 country_polygons: list) -> tuple[list[dict], list[dict]]:
    """Group collapsed 110 kV segments by OSM way and apply the documented rules in order."""
    segments: dict[str, list[dict]] = defaultdict(list)
    for row in collapsed:
        if float(row["voltage_kv"]) * 1000 == REFERENCE_VOLTAGE_V:
            segments[str(row["osm_id"])].append(row)

    accepted_pre, excluded = [], []
    for osm_id in sorted(segments, key=int):
        rows = segments[osm_id]
        base = {
            "osm_id": osm_id,
            "collapsed_segment_ids": sorted(r["line_id"] for r in rows),
            "collapsed_bus_ids": sorted({str(r["collapsed_bus_id"]) for r in rows}, key=int),
        }
        feature = raw_lines.get(osm_id)
        if not feature or not feature.get("geometry"):
            excluded.append({**base, "reason": "no_raw_geometry"})
            continue
        props = feature["properties"]
        tags = parse_other_tags(props.get("other_tags"))
        if REFERENCE_VOLTAGE_V not in voltages(props.get("tags.voltage")):
            excluded.append({**base, "reason": "voltage_not_110kV", "voltage_tag": props.get("tags.voltage")})
            continue
        if any(key.startswith("tags.demolished") or key.startswith("tags.disused") or key.startswith("tags.abandoned")
               for key in tags):
            excluded.append({**base, "reason": "demolished_or_disused_tag", "tags": tags})
            continue
        map_ids = lineage.get(osm_id, {}).get("map_ids", "UNKNOWN")
        if map_ids not in ("", "UNKNOWN"):
            excluded.append({**base, "reason": "way_partly_in_existing_map", "existing_map_line_ids": map_ids.split(";")})
            continue
        parts = line_parts(feature["geometry"])
        points = [p for part in parts for p in part]
        start, end = points[0], points[-1]
        length = path_km(points)
        chord = haversine_km(start, end)
        metrics = {"raw_path_km": round(length, 3), "chord_km": round(chord, 3)}
        if length < MIN_PATH_KM:
            excluded.append({**base, **metrics, "reason": "short_span_fragment"})
            continue
        if chord < MIN_CHORD_RATIO * length:
            excluded.append({**base, **metrics, "reason": "straight_line_not_representative"})
            continue
        if not (inside_country(start, country_polygons) and inside_country(end, country_polygons)):
            excluded.append({**base, **metrics, "reason": "endpoint_outside_country"})
            continue
        shares = corridor_share(points, list(existing.values()), EXISTING_OVERLAP_KM)
        best_share, best_line = max(zip(shares, existing), default=(0.0, None))
        if best_share >= EXISTING_OVERLAP_SHARE:
            excluded.append({**base, **metrics, "reason": "overlaps_existing_map_line",
                             "existing_map_line_id": best_line, "overlap_share": round(best_share, 3)})
            continue
        accepted_pre.append({**base, **metrics, "points": points, "tags": tags, "props": props,
                             "existing_overlap": {"line_id": best_line, "share": round(best_share, 3)}})

    # Redundant ways: mostly inside the corridor of a longer accepted way (split/duplicate mapping).
    accepted: list[dict] = []
    for item in sorted(accepted_pre, key=lambda x: (-x["raw_path_km"], int(x["osm_id"]))):
        shares = corridor_share(item["points"], [[a["points"]] for a in accepted], CANDIDATE_OVERLAP_KM)
        hit = next((a for a, s in zip(accepted, shares) if s >= CANDIDATE_OVERLAP_SHARE), None)
        if hit is not None:
            share = shares[accepted.index(hit)]
            hit.setdefault("merged", []).append({"osm_id": item["osm_id"], "overlap_share": round(share, 3),
                                                 "collapsed_segment_ids": item["collapsed_segment_ids"]})
            excluded.append({k: item[k] for k in ("osm_id", "collapsed_segment_ids", "collapsed_bus_ids",
                                                   "raw_path_km", "chord_km")}
                            | {"reason": "contained_in_other_reference_line", "merged_into_osm_id": hit["osm_id"],
                               "overlap_share": round(share, 3)})
            continue
        accepted.append(item)

    lines = []
    for item in sorted(accepted, key=lambda x: int(x["osm_id"])):
        start, end = item["points"][0], item["points"][-1]
        tags = item["tags"]
        lines.append({
            "ref_id": f"REF-L-{item['osm_id']}",
            "osm_type": "way",
            "osm_id": item["osm_id"],
            "osm_url": osm_url("way", item["osm_id"]),
            "voltage_kv": 110.0,
            "voltage_tag": item["props"].get("tags.voltage"),
            "circuits_tag": tags.get("tags.circuits"),
            "name": tags.get("tags.name"),
            "from": [round(start[0], 7), round(start[1], 7)],
            "to": [round(end[0], 7), round(end[1], 7)],
            "raw_path_km": item["raw_path_km"],
            "chord_km": item["chord_km"],
            "collapsed_segment_ids": item["collapsed_segment_ids"],
            "collapsed_bus_ids": item["collapsed_bus_ids"],
            "nearest_map_bus": {"from": nearest_bus(start, buses), "to": nearest_bus(end, buses)},
            "duplicate_check": {
                "status": "not_duplicate",
                "max_existing_overlap": item["existing_overlap"],
                "merged_osm_ids": [m["osm_id"] for m in item.get("merged", [])],
            },
            "source": "OSM raw way (PyPSA-Earth MN_35k all_raw_lines.geojson)",
            "display": True,
        })
    return lines, excluded


def nearest_bus(point, buses: list[dict]) -> dict:
    distance, bus = min((haversine_km(point, (b["longitude"], b["latitude"])), b) for b in buses)
    return {"bus_id": bus["bus_id"], "voltage_kv": bus.get("voltage_kv"), "distance_km": round(distance, 3)}


# ---------------------------------------------------------------- selection: substations
def select_substations(lineage_rows: list[dict], raw_subs: dict[str, dict], buses: list[dict],
                       country_polygons: list) -> tuple[list[dict], list[dict]]:
    accepted, duplicates, excluded = [], [], []
    for row in sorted(lineage_rows, key=lambda r: int(r["osm_id"])):
        osm_id = str(row["osm_id"])
        feature = raw_subs.get(osm_id)
        if not feature or not feature.get("geometry"):
            excluded.append({"osm_id": osm_id, "osm_type": row["osm_type"], "reason": "no_raw_geometry"})
            continue
        props = feature["properties"]
        tags = parse_other_tags(props.get("other_tags"))
        volts = voltages(props.get("tags.voltage"))
        sub_tag = props.get("tags.substation")
        location = feature_location(feature["geometry"])
        base = {"osm_id": osm_id, "osm_type": row["osm_type"], "voltage_tag": props.get("tags.voltage"),
                "substation_tag": sub_tag, "name": tags.get("tags.name"),
                "longitude": round(location[0], 7), "latitude": round(location[1], 7)}
        if not volts and sub_tag != "transmission":
            excluded.append({**base, "reason": "no_voltage_or_transmission_type_evidence"})
            continue
        if volts and max(volts) < SUBSTATION_MIN_VOLTAGE_V:
            excluded.append({**base, "reason": "below_35kV"})
            continue
        if not inside_country(location, country_polygons):
            excluded.append({**base, "reason": "outside_country"})
            continue
        model_bus_ids = [b.removeprefix("B_") for b in row.get("final_bus_ids", "").split(";") if b.startswith("B_")]
        near = nearest_bus(location, buses)
        record = {
            "ref_id": f"REF-S-{osm_id}",
            "osm_type": row["osm_type"],
            "osm_id": osm_id,
            "osm_url": osm_url("node" if row["osm_type"] == "node" else "way", osm_id),
            "voltage_kv": [v / 1000 for v in volts],
            "voltage_tag": props.get("tags.voltage"),
            "substation_tag": sub_tag,
            "name": tags.get("tags.name"),
            "name_en": tags.get("tags.name:en"),
            "operator": tags.get("tags.operator"),
            "longitude": round(location[0], 7),
            "latitude": round(location[1], 7),
            "nearest_map_bus": near,
            "model_bus_ids": model_bus_ids,
            "pipeline_stage": row.get("reason"),
            "source": "OSM raw substation (PyPSA-Earth MN_35k all_raw_substations.geojson)",
        }
        if near["distance_km"] <= SUBSTATION_SAME_AS_BUS_KM:
            duplicates.append({**record, "display": False,
                               "duplicate_check": {"status": "same_location_as_map_bus", **near}})
            continue
        twin = next((a for a in accepted if haversine_km(location, (a["longitude"], a["latitude"])) <= SUBSTATION_DUPLICATE_KM), None)
        if twin is not None:
            twin["duplicate_check"]["merged_osm_ids"].append(osm_id)
            excluded.append({**base, "reason": "duplicate_osm_object", "merged_into_osm_id": twin["osm_id"]})
            continue
        accepted.append({**record, "display": True,
                         "duplicate_check": {"status": "not_duplicate", "merged_osm_ids": []}})
    return accepted + duplicates, excluded


# ---------------------------------------------------------------- region labels (review summary only)
def region_lookup(gadm: Path, names_csv: Path):
    names = {r["GID_1"]: r["NAME_1"] for r in read_csv(names_csv)}
    regions = []
    for feature in read_features(gadm):
        gid = feature["properties"]["GADM_ID"].replace("MN.", "MNG.", 1)
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        regions.append((names.get(gid, gid), polygons))

    def lookup(point) -> str:
        for name, polygons in regions:
            if any(point_in_polygon(point, polygon) for polygon in polygons):
                return name
        return "UNKNOWN"
    return lookup


def build(earth: Path, audit: Path, map_file: Path) -> dict:
    raw_line_path = earth / "resources/MN_35k/osm/raw/all_raw_lines.geojson"
    raw_sub_path = earth / "resources/MN_35k/osm/raw/all_raw_substations.geojson"
    build_lines_path = earth / "resources/MN_35k/base_network/all_lines_build_network.geojson"
    gadm_path = earth / "resources/MN/shapes/gadm_shapes.geojson"
    country_path = earth / "resources/MN_35k/shapes/country_shapes.geojson"
    inputs = {
        "collapsed_lines": audit / "collapsed_lines.csv",
        "osm_line_lineage": audit / "osm_line_lineage.csv",
        "osm_substation_lineage": audit / "osm_substation_lineage.csv",
        "region_names": audit / "region_names.csv",
        "raw_lines": raw_line_path,
        "raw_substations": raw_sub_path,
        "build_lines": build_lines_path,
        "gadm_shapes": gadm_path,
        "country_shapes": country_path,
        "base_map": map_file,
    }
    base_map = json.loads(map_file.read_text(encoding="utf-8"))
    buses = base_map["buses"]
    map_line_ids = {line["line_id"] for line in base_map["lines"]}
    existing = {}
    for feature in read_features(build_lines_path):
        line_id = feature["properties"]["line_id"]
        if line_id in map_line_ids:
            existing[line_id] = line_parts(feature["geometry"])
    if set(existing) != map_line_ids:
        raise ValueError("stored build geometry does not cover every map line")

    raw_lines = {str(f["properties"]["id"]): f for f in read_features(raw_line_path)}
    raw_subs = {str(f["properties"]["id"]): f for f in read_features(raw_sub_path)}
    lineage = {r["osm_id"]: r for r in read_csv(inputs["osm_line_lineage"])}
    collapsed = read_csv(inputs["collapsed_lines"])
    # Unsimplified run shape: the map outline is simplified (~0.025 deg) and must not decide inside/outside.
    country = []
    for feature in read_features(country_path):
        geometry = feature["geometry"]
        country += [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    lines, excluded_lines = select_lines(collapsed, raw_lines, lineage, existing, buses, country)
    substations, excluded_subs = select_substations(read_csv(inputs["osm_substation_lineage"]), raw_subs, buses, country)
    region = region_lookup(gadm_path, inputs["region_names"])
    for line in lines:
        mid = ((line["from"][0] + line["to"][0]) / 2, (line["from"][1] + line["to"][1]) / 2)
        line["region"] = region(mid)
    for sub in substations:
        sub["region"] = region((sub["longitude"], sub["latitude"]))

    shown_subs = [s for s in substations if s["display"]]
    collapsed_110 = [r for r in collapsed if float(r["voltage_kv"]) == 110.0]
    return {
        "schema": SCHEMA,
        "grid_id": GRID_ID,
        "reference_only": True,
        "base_map_sha256": sha256(map_file),
        "source": {
            "osm_extract": "Geofabrik mongolia-latest.osm.pbf, replication 2026-10-15T20:21:00Z (sequence 4877)",
            "audit": "mongolia-grid-poc docs/audits/2026-10-20 (branch docs/mn-coverage-audit-20261020)",
            "inputs_sha256": {name: sha256(path) for name, path in inputs.items()},
        },
        "rules": {
            "lines": {
                "scope": "110 kV segments removed as self-loops by the 5 km EPSG:3857 station merge",
                "unit": "one reference line per original OSM way (split segments of a way are not repeated)",
                "drawn_as": "straight segment between the first and last vertex of the raw OSM way; no bus connection",
                "min_path_km": MIN_PATH_KM,
                "min_chord_ratio": MIN_CHORD_RATIO,
                "country": "both way ends inside PyPSA-Earth MN_35k country_shapes.geojson",
                "existing_overlap": {"corridor_km": EXISTING_OVERLAP_KM, "max_share": EXISTING_OVERLAP_SHARE},
                "candidate_overlap": {"corridor_km": CANDIDATE_OVERLAP_KM, "max_share": CANDIDATE_OVERLAP_SHARE},
                "excluded_tags": ["demolished:*", "disused:*", "abandoned:*"],
            },
            "substations": {
                "scope": "all 355 raw OSM substation objects",
                "evidence": f"explicit voltage tag >= {SUBSTATION_MIN_VOLTAGE_V // 1000} kV, or substation=transmission",
                "location": "raw OSM node or polygon centroid; not snapped to model buses",
                "country": "inside PyPSA-Earth MN_35k country_shapes.geojson",
                "same_as_map_bus_km": SUBSTATION_SAME_AS_BUS_KM,
                "duplicate_object_km": SUBSTATION_DUPLICATE_KM,
                "names": "OSM name tags only; missing names and voltages are left empty",
            },
        },
        "summary": {
            "collapsed_110kv_segments": len(collapsed_110),
            "collapsed_110kv_ways": len({r["osm_id"] for r in collapsed_110}),
            "reference_lines": len(lines),
            "excluded_line_ways": dict(sorted(Counter(e["reason"] for e in excluded_lines).items())),
            "raw_substations": len(raw_subs),
            "reference_substations": len(shown_subs),
            "same_location_as_map_bus": len(substations) - len(shown_subs),
            "excluded_substations": dict(sorted(Counter(e["reason"] for e in excluded_subs).items())),
            "lines_by_region": dict(sorted(Counter(line["region"] for line in lines).items())),
            "substations_by_region": dict(sorted(Counter(s["region"] for s in shown_subs).items())),
        },
        "lines": lines,
        "substations": substations,
        "excluded": {"lines": excluded_lines, "substations": excluded_subs},
    }


def write_review_tables(payload: dict, directory: Path) -> None:
    """One row per collapsed 110 kV way and per raw substation, with the decision and its reason."""
    directory.mkdir(parents=True, exist_ok=True)
    line_rows = []
    for line in payload["lines"]:
        overlap = line["duplicate_check"]["max_existing_overlap"]
        line_rows.append({
            "osm_id": line["osm_id"], "decision": "reference_line", "reason": "",
            "region": line["region"], "raw_path_km": line["raw_path_km"], "chord_km": line["chord_km"],
            "circuits_tag": line["circuits_tag"] or "", "collapsed_segment_ids": ";".join(line["collapsed_segment_ids"]),
            "collapsed_bus_ids": ";".join(line["collapsed_bus_ids"]),
            "related_osm_or_line_id": ";".join(line["duplicate_check"]["merged_osm_ids"]),
            "existing_overlap_share": overlap["share"], "osm_url": line["osm_url"],
        })
    for item in payload["excluded"]["lines"]:
        line_rows.append({
            "osm_id": item["osm_id"], "decision": "excluded", "reason": item["reason"], "region": "",
            "raw_path_km": item.get("raw_path_km", ""), "chord_km": item.get("chord_km", ""), "circuits_tag": "",
            "collapsed_segment_ids": ";".join(item["collapsed_segment_ids"]),
            "collapsed_bus_ids": ";".join(item["collapsed_bus_ids"]),
            "related_osm_or_line_id": item.get("merged_into_osm_id") or item.get("existing_map_line_id")
            or ";".join(item.get("existing_map_line_ids", [])),
            "existing_overlap_share": item.get("overlap_share", ""), "osm_url": osm_url("way", item["osm_id"]),
        })
    sub_rows = []
    for sub in payload["substations"]:
        sub_rows.append({
            "osm_id": sub["osm_id"], "osm_type": sub["osm_type"],
            "decision": "reference_substation" if sub["display"] else "hidden_same_site_as_map_bus",
            "reason": "" if sub["display"] else "same_location_as_map_bus", "region": sub["region"],
            "voltage_tag": sub["voltage_tag"] or "", "substation_tag": sub["substation_tag"] or "", "name": sub["name"] or "",
            "longitude": sub["longitude"], "latitude": sub["latitude"],
            "nearest_map_bus_id": sub["nearest_map_bus"]["bus_id"], "nearest_map_bus_km": sub["nearest_map_bus"]["distance_km"],
            "model_bus_ids": ";".join(sub["model_bus_ids"]), "osm_url": sub["osm_url"],
        })
    for item in payload["excluded"]["substations"]:
        sub_rows.append({
            "osm_id": item["osm_id"], "osm_type": item["osm_type"], "decision": "excluded", "reason": item["reason"],
            "region": "", "voltage_tag": item.get("voltage_tag") or "", "substation_tag": item.get("substation_tag") or "",
            "name": item.get("name") or "", "longitude": item.get("longitude", ""), "latitude": item.get("latitude", ""),
            "nearest_map_bus_id": "", "nearest_map_bus_km": "", "model_bus_ids": "",
            "osm_url": osm_url("node" if item["osm_type"] == "node" else "way", item["osm_id"]),
        })
    for name, rows in (("reference_lines_review.csv", line_rows), ("reference_substations_review.csv", sub_rows)):
        rows.sort(key=lambda r: (r["decision"] == "excluded", r.get("reason", ""), int(r["osm_id"])))
        with (directory / name).open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pypsa-earth", type=Path, default=DEFAULT_EARTH, help="PyPSA-Earth checkout (read-only)")
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT, help="2026-10-20 coverage audit directory")
    parser.add_argument("--map", type=Path, default=Path("data/grids/mn-osm-35k-map.json"), help="Active base map JSON")
    parser.add_argument("--output", type=Path, default=Path("data/grids/mn-osm-35k-reference-layers.json"))
    parser.add_argument("--review-dir", type=Path, help="Also write per-candidate decision CSVs here")
    args = parser.parse_args()
    payload = build(args.pypsa_earth, args.audit_dir, args.map)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.review_dir:
        write_review_tables(payload, args.review_dir)
    summary = payload["summary"]
    print(f"Wrote {args.output}: {summary['reference_lines']} reference lines "
          f"(from {summary['collapsed_110kv_ways']} collapsed 110 kV ways), "
          f"{summary['reference_substations']} reference substations "
          f"(+{summary['same_location_as_map_bus']} hidden as same site as a map bus)")


if __name__ == "__main__":
    main()
