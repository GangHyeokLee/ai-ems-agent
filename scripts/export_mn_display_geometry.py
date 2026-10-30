"""Export display-only OSM portions of existing MN branches; never export a model.

Run in the existing Mongolia GIS environment. The runtime needs only the resulting
JSON and its reviewed SHA256, not PyPSA, GIS libraries or the source checkout.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_parts(geometry, raw, clean, project, *, tolerance_m=0.001):
    """Keep whole branch segments supported by BOTH exact-ID source geometries.

    Never interpolate a missing connection or concatenate across an omitted
    segment. Build-only additions are measured and omitted, not rendered.
    """
    from shapely.geometry import LineString
    from shapely import transform
    from pyproj import Geod

    geod = Geod(ellps="WGS84")
    support = [transform(g, project.transform, interleaved=False).buffer(tolerance_m) for g in (raw, clean)]
    parts, omitted, supported = [], 0.0, 0.0
    geometries = [geometry] if geometry.geom_type == "LineString" else list(geometry.geoms)
    for part in geometries:
        run = []
        coordinates = list(part.coords)
        for a, b in zip(coordinates, coordinates[1:]):
            segment = LineString([a, b])
            length = abs(geod.geometry_length(segment)) / 1000
            if all(s.covers(transform(segment, project.transform, interleaved=False)) for s in support):
                if not run:
                    run.append(list(a))
                run.append(list(b))
                supported += length
            else:
                if len(run) > 1:
                    parts.append(run)
                run = []
                omitted += length
        if len(run) > 1:
            parts.append(run)
    return parts, supported, omitted


def build(earth: Path, audit: Path, map_file: Path):
    import xarray as xr
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely import wkt
    from pipelines.mn.mn35k_lengths import inspect_chain_geometry

    inputs = {
        "raw": earth / "resources/MN_35k/osm/raw/all_raw_lines.geojson",
        "clean": earth / "resources/MN_35k/osm/clean/all_clean_lines.geojson",
        "build": earth / "resources/MN_35k/base_network/all_lines_build_network.geojson",
        "network": earth / "networks/MN_35k/base.nc",
        "length_audit": audit,
        "map": map_file,
    }
    hashes = {key: sha(path) for key, path in inputs.items()}
    def features(key, id_key):
        rows = json.loads(inputs[key].read_text())["features"]
        result = {str(f["properties"][id_key]): f for f in rows}
        if len(result) != len(rows):
            raise ValueError(f"Duplicate {key} IDs")
        return result

    raw, clean, built = features("raw", "id"), features("clean", "line_id"), features("build", "line_id")
    base = json.loads(map_file.read_text())
    buses = {b["bus_id"]: b for b in base["buses"]}
    with audit.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    audits = {r["line_id"]: r for r in rows}
    ids = {l["line_id"] for l in base["lines"]}
    if len(ids) != 159 or len(buses) != 173 or set(built) != ids or set(audits) != ids:
        raise ValueError("Map/build/audit equipment sets differ")
    project = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    lines, excluded = [], []
    with xr.open_dataset(inputs["network"]) as network:
        if set(map(str, network.lines_i.values)) != ids:
            raise ValueError("NetCDF/map line IDs differ")
        for line in base["lines"]:
            identifier = line["line_id"]
            audit_row = audits[identifier]
            parent = re.sub(r"_\d+$", "", identifier)
            way = identifier.split("-")[0]
            geometry = shape(built[identifier]["geometry"])
            nc = network.sel(lines_i=identifier)
            if (str(nc.lines_bus0.item()), str(nc.lines_bus1.item())) != (line["bus1_id"], line["bus2_id"]):
                raise ValueError(f"NetCDF endpoints differ: {identifier}")
            if not geometry.equals_exact(wkt.loads(nc.lines_geometry.item()), 1e-9):
                raise ValueError(f"NetCDF geometry differs: {identifier}")
            points = []
            for key in ("bus1_id", "bus2_id"):
                bus = buses[line[key]]
                nb = network.sel(buses_i=line[key])
                point = (bus["longitude"], bus["latitude"])
                if point != (float(nb.buses_x.item()), float(nb.buses_y.item())):
                    raise ValueError(f"Map bus coordinates differ: {identifier}")
                points.append(point)
            check = inspect_chain_geometry(geometry, *points)
            reason = check["geometry_issue"]
            if reason != audit_row["unresolved_reason"]:
                raise ValueError(f"Chain check differs from stored audit: {identifier}")
            if reason:
                excluded.append({"line_id": identifier, "reason": reason})
                continue
            if audit_row["correction_applied"] != "True" or parent not in clean or way not in raw:
                excluded.append({"line_id": identifier, "reason": "source_or_audit_unverified"})
                continue
            raw_geometry, clean_geometry = shape(raw[way]["geometry"]), shape(clean[parent]["geometry"])
            parts, supported, omitted = source_parts(geometry, raw_geometry, clean_geometry, project)
            if not parts or abs(supported-float(audit_row["clean_route_geodesic_km"])) > 1e-7 or abs(omitted-float(audit_row["virtual_connector_geodesic_km"])) > 1e-7:
                excluded.append({"line_id": identifier, "reason": "raw_clean_decomposition_unverified"})
                continue
            lines.append({"line_id": identifier, "bus1_id": line["bus1_id"], "bus2_id": line["bus2_id"],
                          "osm_way_id": way, "clean_line_id": parent, "osm_parts": parts,
                          "osm_route_km": supported, "omitted_connector_km": omitted})
    if hashes != {key: sha(path) for key, path in inputs.items()}:
        raise ValueError("Inputs changed during export")
    return {"schema": "mn-display-geometry/v1", "grid_id": "mn-osm-35k", "display_only": True,
            "base_map_sha256": hashes["map"], "source_sha256": hashes,
            "method": {"support_tolerance_epsg3857_m": 0.001, "connectors": "omitted", "fallback": "original_bus_chord"},
            "lines": lines, "excluded": excluded}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--earth-checkout", type=Path, required=True)
    parser.add_argument("--length-audit", type=Path, required=True)
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    display_directory = Path(__file__).resolve().parents[1] / "data/grids"
    if output.parent != display_directory.resolve():
        raise ValueError("Display output must be a new file in EMS data/grids")
    if output.is_relative_to(args.earth_checkout.resolve()) or output == args.map.resolve() or output == args.length_audit.resolve():
        raise ValueError("Output overlaps protected inputs")
    payload = build(args.earth_checkout, args.length_audit, args.map)
    content = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+"\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as file:
        file.write(content)
    print(json.dumps({"applied": len(payload["lines"]), "fallback": len(payload["excluded"]),
                      "sha256": hashlib.sha256(content).hexdigest(), "output": str(output)}))


if __name__ == "__main__":
    main()
