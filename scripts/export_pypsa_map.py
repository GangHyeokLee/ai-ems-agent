"""Export PyPSA-Earth network and public power-plant *records* for a geographic map.

Use the PyPSA-Earth Pixi environment, not the AI-EMS runtime venv.
Matched plant rows and mapped buses are NOT independently verified operating units
or electrical connections. The output is NOT suitable for AC load flow.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def _coordinate(value, *, limit: float) -> float | None:
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(coordinate) or abs(coordinate) > limit:
        return None
    return coordinate


def _capacity(value) -> float | None:
    try:
        capacity = float(value)
    except (TypeError, ValueError):
        return None
    return capacity if math.isfinite(capacity) and capacity >= 0 else None


def _positive_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def read_powerplant_records(csv_path: Path, bus_ids: set[str], *, include_bus_mapping: bool = True) -> list[dict]:
    """Keep one marker record per CSV row, including potential duplicate units.

    source_record_id identifies a source row, NOT a unique physical power plant.
    """
    with csv_path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        expected = {"Name", "Fueltype", "Capacity", "lat", "lon", "bus"}
        if not reader.fieldnames or not expected.issubset(reader.fieldnames):
            raise ValueError(
                "powerplants.csv needs Name, Fueltype, Capacity, lat, lon, bus columns"
            )
        records = []
        for row_number, row in enumerate(reader, start=1):
            latitude = _coordinate(row.get("lat"), limit=90)
            longitude = _coordinate(row.get("lon"), limit=180)
            if latitude is None or longitude is None:
                continue  # Without geographic coordinates a row cannot be mapped.
            raw_bus = (row.get("bus") or "").strip()
            name = (row.get("Name") or "").strip()
            fueltype = (row.get("Fueltype") or "").strip()
            technology = (row.get("Technology") or "").strip()
            records.append(
                {
                    "record_id": f"mn-powerplant-row-{row_number}",
                    "name": name if name and name.lower() != "nan" else "Unknown",
                    "fueltype": fueltype if fueltype and fueltype.lower() != "nan" else "Unknown",
                    "technology": technology if technology and technology.lower() != "nan" else None,
                    "capacity_mw": _capacity(row.get("Capacity")),
                    "latitude": latitude,
                    "longitude": longitude,
                    # The bus number is an *inferred mapping*, not a validated connection.
                    "mapped_bus_id": raw_bus if include_bus_mapping and raw_bus in bus_ids else None,
                }
            )
    return records




def _simplify_line(points: list[list[float]], tolerance: float) -> list[list[float]]:
    """Iterative Douglas-Peucker simplification, using lon/lat degree tolerance."""
    if len(points) < 3:
        return points
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    tolerance_squared = tolerance * tolerance

    while stack:
        start, end = stack.pop()
        ax, ay = points[start]
        bx, by = points[end]
        length_squared = (bx - ax) ** 2 + (by - ay) ** 2
        best_distance = 0.0
        best_index = None

        for index in range(start + 1, end):
            px, py = points[index]
            if length_squared == 0:
                distance = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0, min(1, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / length_squared))
                distance = (px - (ax + t * (bx - ax))) ** 2 + (py - (ay + t * (by - ay))) ** 2
            if distance > best_distance:
                best_distance, best_index = distance, index

        if best_index is not None and best_distance > tolerance_squared:
            keep.add(best_index)
            stack.extend([(start, best_index), (best_index, end)])

    return [points[index] for index in sorted(keep)]


def _simplify_ring(raw_points, tolerance: float) -> list[list[float]]:
    """Simplify a closed border without degenerating it to the same endpoints."""
    points = []
    for raw in raw_points:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            raise ValueError("Invalid country polygon coordinate")
        lon = _coordinate(raw[0], limit=180)
        lat = _coordinate(raw[1], limit=90)
        if lon is None or lat is None:
            raise ValueError("Invalid country polygon coordinate")
        point = [round(lon, 5), round(lat, 5)]
        if not points or points[-1] != point:
            points.append(point)
    if len(points) < 4:
        raise ValueError("Country polygon has too few coordinates")
    if points[0] == points[-1]:
        points.pop()
    if len(points) < 3:
        raise ValueError("Country polygon has too few unique vertices")

    # Split the ring opposite its first vertex so both edges survive RDP.
    ax, ay = points[0]
    split = max(
        range(1, len(points)),
        key=lambda i: (points[i][0] - ax) ** 2 + (points[i][1] - ay) ** 2,
    )
    left = _simplify_line(points[:split + 1], tolerance)
    right = _simplify_line(points[split:] + [points[0]], tolerance)
    simplified = left[:-1] + right
    if len(simplified) < 4:
        simplified = points + [points[0]]
    return simplified


def _polygons_from_geojson(geojson, tolerance: float) -> list:
    """Parse GeoJSON Polygon, MultiPolygon and collections to simplified rings."""
    def geometries(item):
        if not isinstance(item, dict):
            raise ValueError("Invalid geographic GeoJSON")
        kind = item.get("type")
        if kind == "FeatureCollection":
            for feature in item.get("features", []):
                yield from geometries(feature)
        elif kind == "Feature":
            yield from geometries(item.get("geometry"))
        elif kind == "GeometryCollection":
            for geometry in item.get("geometries", []):
                yield from geometries(geometry)
        elif kind == "Polygon":
            yield item.get("coordinates", [])
        elif kind == "MultiPolygon":
            for polygon in item.get("coordinates", []):
                yield polygon
        else:
            raise ValueError(f"Unsupported geographic geometry type: {kind}")

    polygons = []
    for polygon in geometries(geojson):
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("Invalid geographic polygon")
        polygons.append([_simplify_ring(ring, tolerance) for ring in polygon])
    return polygons


def _read_geojson(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read geographic GeoJSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("GeoJSON document must be an object")
    return value


def read_country_polygons(country_shapes: Path, tolerance: float = 0.025) -> list:
    """Read actual geographic national boundary from PyPSA-Earth GeoJSON."""
    polygons = _polygons_from_geojson(_read_geojson(country_shapes), tolerance)
    if not polygons:
        raise ValueError("No polygon geometry in country GeoJSON")
    return polygons


def read_admin_regions(admin_shapes: Path, tolerance: float = 0.035) -> list[dict]:
    """Read GADM subnational region boundaries, preserving the source names.

    This only displays regional shapes; it does not assign equipment to regions
    or assert any relationship between mapped plants and grid topology.
    """
    geojson = _read_geojson(admin_shapes)
    if geojson.get("type") != "FeatureCollection":
        raise ValueError("Expected FeatureCollection for regional GADM geometry")
    regions = []
    features = geojson.get("features", [])
    if not isinstance(features, list):
        raise ValueError("Invalid regional GeoJSON feature collection")
    for idx, feature in enumerate(features):
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            properties = {}
        # GADM NAME_1 is the usual province/aimag name; other sources may
        # use name or shapeName. Never silently call country-level shapes aimags.
        name = next(
            (str(properties[key]).strip() for key in
             ("NAME_1", "name_1", "shapeName", "name", "NAME_EN", "NAME")
             if properties.get(key) is not None and str(properties[key]).strip()),
            f"행정구역 {idx + 1}",
        )
        regions.append({
            "name": name,
            "polygons": _polygons_from_geojson(feature, tolerance),
        })
    regions = [region for region in regions if region["polygons"]]
    if len(regions) < 2:
        raise ValueError(
            "GADM GeoJSON has fewer than 2 regional features; "
            "verify that it contains Mongolia's subnational boundaries"
        )
    return regions


def build_preview(
    network,
    powerplants_csv: Path | None = None,
    country_shapes: Path | None = None,
    admin_shapes: Path | None = None,
    *,
    include_plant_bus_mapping: bool = True,
) -> dict:
    buses = []
    available: set[str] = set()
    for bus_id, row in network.buses.iterrows():
        lat = _coordinate(row["y"], limit=90)
        lon = _coordinate(row["x"], limit=180)
        if lat is None or lon is None:
            continue
        identifier = str(bus_id)
        available.add(identifier)
        buses.append(
            {
                "bus_id": identifier,
                "latitude": lat,
                "longitude": lon,
                "name": str(row.get("name", identifier) or identifier),
                "voltage_kv": _positive_number(row.get("v_nom")),
            }
        )

    lines = []
    for line_id, row in network.lines.iterrows():
        start, end = str(row["bus0"]), str(row["bus1"])
        if start in available and end in available:
            lines.append(
                {
                    "line_id": str(line_id),
                    "bus1_id": start,
                    "bus2_id": end,
                    "voltage_kv": _positive_number(row.get("v_nom")),
                    "length_km": _positive_number(row.get("length")),
                }
            )

    if not buses:
        raise ValueError("좌표가 있는 모선이 없습니다. 원본 base.nc를 확인하세요.")

    transformers = []
    transformer_frame = getattr(network, "transformers", None)
    if transformer_frame is not None:
        for transformer_id, row in transformer_frame.iterrows():
            start, end = str(row["bus0"]), str(row["bus1"])
            if start in available and end in available:
                transformers.append({
                    "transformer_id": str(transformer_id),
                    "bus1_id": start,
                    "bus2_id": end,
                    "inferred": True,
                })
    powerplants = (
        read_powerplant_records(powerplants_csv, available, include_bus_mapping=include_plant_bus_mapping)
        if powerplants_csv is not None
        else []
    )
    country_polygons = read_country_polygons(country_shapes) if country_shapes else []
    admin_regions = read_admin_regions(admin_shapes) if admin_shapes else []
    return {
        "buses": buses,
        "lines": lines,
        "transformers": transformers,
        "powerplants": powerplants,
        "country_polygons": country_polygons,
        "admin_regions": admin_regions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("network", type=Path, help="PyPSA-Earth networks/MN/base.nc")
    parser.add_argument("output", type=Path, help="Destination JSON map file")
    parser.add_argument(
        "--powerplants",
        type=Path,
        help="Optional PyPSA-Earth resources/MN/powerplants.csv; records only",
    )
    parser.add_argument(
        "--country-shapes",
        type=Path,
        help="PyPSA-Earth resources/MN/shapes/country_shapes.geojson",
    )
    parser.add_argument(
        "--admin-shapes",
        type=Path,
        help="PyPSA-Earth resources/MN/shapes/gadm_shapes.geojson (aimag regions)",
    )
    parser.add_argument(
        "--no-plant-bus-mapping", action="store_true",
        help="For MN_35k, show plant locations but do not reuse incompatible old numeric bus IDs",
    )
    args = parser.parse_args()

    import pypsa  # import from PyPSA-Earth's Pixi environment

    network = pypsa.Network(args.network)
    payload = build_preview(
        network, args.powerplants, args.country_shapes, args.admin_shapes,
        include_plant_bus_mapping=not args.no_plant_bus_mapping,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        f"Map preview: {len(payload['buses'])} buses, "
        f"{len(payload['lines'])} lines, "
        f"{len(payload['transformers'])} transformers, "
        f"{len(payload['powerplants'])} plant data rows (not unique plants), "
        f"{len(payload['country_polygons'])} country polygons, "
        f"{len(payload['admin_regions'])} administrative regions"
    )
    print(f"Saved: {args.output.resolve()}")


if __name__ == "__main__":
    main()
