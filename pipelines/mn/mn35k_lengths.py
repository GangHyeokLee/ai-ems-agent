"""Trace MN_35k lengths without changing source data or the supplied network.

The build CSV stores metres measured before endpoint connectors are added;
``base_network._load_lines_from_osm`` only divides this value by 1000.  Final
geometry can contain synthetic connectors, loops or disconnected pieces.  A
length correction is therefore allowed only for an exact-ID, endpoint-verified
simple chain.  Even an accepted length is model-geometry-derived, not a field
measurement of conductor length.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely import transform, wkt
from shapely.errors import GEOSException
from shapely.geometry import LineString

from .audit_mn35k_parameters import geodesic_length_km, source_way_id
from .paths import Paths


BUILD_LINEAGE = (
    "pypsa-earth/scripts/build_osm_network.py:fix_overpassing_lines "
    "(EPSG:3857 length before connectors; endpoint-coordinate update commented out); "
    "set_lines_ids (union endpoint connectors); "
    "pypsa-earth/scripts/base_network.py:_load_lines_from_osm (metres / 1000)"
)
MODEL_GEOMETRY_CAVEAT = (
    "Model geometry only; not field-verified conductor length. Connector segments "
    "are synthetic. Whole raw/clean source lengths can repeat after line splitting "
    "and must not be summed across final line IDs."
)
_PROJECT = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def _metric(geometry: Any) -> Any:
    return transform(geometry, _PROJECT.transform, interleaved=False)


def _parts(geometry: Any) -> list[Any]:
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "LineString":
        return [geometry]
    if geometry.geom_type == "MultiLineString":
        return list(geometry.geoms)
    return []


def inspect_chain_geometry(
    geometry: Any,
    bus0_xy: tuple[float, float],
    bus1_xy: tuple[float, float],
    endpoint_tolerance_m: float = 0.001,
) -> dict[str, Any]:
    """Require a connected, unbranched chain ending at the two model buses.

    Shapely union introduced sub-nanometre MultiLineString slivers in these
    inputs.  Small fragments are ignored *only for topology*, and endpoints are
    clustered within the explicit metre tolerance in EPSG:3857.  Lengths always
    use the complete unchanged geometry.  Real gaps, loops and branches fail.
    """
    if not np.isfinite(endpoint_tolerance_m) or endpoint_tolerance_m <= 0:
        raise ValueError("endpoint_tolerance_m must be finite and positive")
    result: dict[str, Any] = {
        "geometry_valid": False,
        "geometry_connected": False,
        "geometry_simple_chain": False,
        "endpoint_match": False,
        "endpoint_max_error_m": np.nan,
        "topology_ignored_fragment_km": 0.0,
        "geometry_issue": "missing_or_non_linear_geometry",
    }
    parts = _parts(geometry)
    if not parts:
        return result
    if not np.isfinite(np.asarray([bus0_xy, bus1_xy])).all():
        result["geometry_issue"] = "invalid_bus_coordinates"
        return result
    coordinates = np.concatenate([np.asarray(part.coords)[:, :2] for part in parts])
    if (
        not geometry.is_valid
        or not np.isfinite(coordinates).all()
        or np.any(np.abs(coordinates[:, 0]) > 180)
        or np.any(np.abs(coordinates[:, 1]) >= 90)
    ):
        result["geometry_issue"] = "invalid_geometry_or_wgs84_coordinates"
        return result
    result["geometry_valid"] = True
    endpoints = []
    for part in parts:
        length_km = geodesic_length_km(part)
        if length_km <= endpoint_tolerance_m / 1000:
            result["topology_ignored_fragment_km"] += length_km
            continue
        if not part.is_simple:
            result["geometry_issue"] = "self_intersecting_geometry"
            return result
        endpoints.extend([part.coords[0][:2], part.coords[-1][:2]])
    if not endpoints:
        result["geometry_issue"] = "no_nonzero_chain"
        return result
    if result["topology_ignored_fragment_km"] > endpoint_tolerance_m / 1000:
        result["geometry_issue"] = "excessive_small_fragments"
        return result
    metric_points = np.asarray([_PROJECT.transform(*point) for point in endpoints])
    clusters = nx.Graph()
    clusters.add_nodes_from(range(len(endpoints)))
    clusters.add_edges_from(cKDTree(metric_points).query_pairs(endpoint_tolerance_m))
    groups = list(nx.connected_components(clusters))
    point_group = {point: group for group, members in enumerate(groups) for point in members}
    graph = nx.MultiGraph()
    graph.add_edges_from(
        (point_group[index], point_group[index + 1])
        for index in range(0, len(endpoints), 2)
    )
    result["geometry_connected"] = nx.is_connected(graph)
    terminals = [node for node, degree in graph.degree if degree == 1]
    result["geometry_simple_chain"] = bool(
        result["geometry_connected"]
        and len(terminals) == 2
        and all(degree in (1, 2) for _, degree in graph.degree)
    )
    if not result["geometry_simple_chain"]:
        result["geometry_issue"] = (
            "branched_or_looped_geometry"
            if result["geometry_connected"]
            else "disconnected_geometry"
        )
        return result
    terminal_xy = [endpoints[min(groups[node])] for node in terminals]
    errors = [
        max(
            geodesic_length_km(LineString([terminal_xy[i], bus])) * 1000
            for i, bus in enumerate(order)
        )
        for order in ((bus0_xy, bus1_xy), (bus1_xy, bus0_xy))
    ]
    result["endpoint_max_error_m"] = min(errors)
    result["endpoint_match"] = min(errors) <= endpoint_tolerance_m
    result["geometry_issue"] = "" if result["endpoint_match"] else "bus_endpoint_mismatch"
    return result


def decompose_route_length(
    final_geometry: Any, clean_geometry: Any, route_tolerance_m: float = 0.001
) -> dict[str, float]:
    """Separate source-supported segments from geometry added by the build.

    Each segment must lie entirely in the cleaned source's metric buffer to
    count as source route.  Partial or nonmatching segments count as additions;
    this conservative geometric decomposition does not prove conductor identity.
    A split branch uses its exact cleaned parent ID, never a nearest spatial way.
    """
    if not np.isfinite(route_tolerance_m) or route_tolerance_m <= 0:
        raise ValueError("route_tolerance_m must be finite and positive")
    if not _parts(final_geometry) or not _parts(clean_geometry) or not clean_geometry.is_valid:
        return {"clean_route_geodesic_km": np.nan, "virtual_connector_geodesic_km": np.nan}
    support = _metric(clean_geometry).buffer(route_tolerance_m)
    route = connector = 0.0
    for part in _parts(final_geometry):
        coords = list(part.coords)
        for start, end in zip(coords, coords[1:]):
            segment = LineString([start, end])
            length = geodesic_length_km(segment)
            if support.covers(_metric(segment)):
                route += length
            else:
                connector += length
    return {"clean_route_geodesic_km": route, "virtual_connector_geodesic_km": connector}


def _geo(path: Path) -> gpd.GeoDataFrame:
    frame = gpd.read_file(path)
    if frame.crs is None:
        raise ValueError(f"Missing geometry CRS: {path}")
    return frame.to_crs("EPSG:4326")


def _inputs(paths: Paths) -> dict[str, pd.DataFrame]:
    base = paths.earth_checkout / "resources/MN_35k"
    return {
        "raw": _geo(base / "osm/raw/all_raw_lines.geojson"),
        "clean": _geo(base / "osm/clean/all_clean_lines.geojson"),
        "build": _geo(base / "base_network/all_lines_build_network.geojson"),
        "csv": pd.read_csv(
            base / "base_network/all_lines_build_network.csv",
            dtype={"line_id": str, "bus0": str, "bus1": str},
        ),
    }


def _one(frame: pd.DataFrame, column: str, value: str) -> pd.Series | None:
    selected = frame.loc[frame[column].astype(str).eq(value)]
    return selected.iloc[0] if len(selected) == 1 else None


def audit_line_lengths(
    network: Any,
    paths: Paths,
    *,
    endpoint_tolerance_m: float = 0.001,
    route_tolerance_m: float = 0.001,
    geometry_tolerance_degrees: float = 1e-9,
) -> pd.DataFrame:
    """Return one exact-ID audit row per line; never mutate ``network``.

    ``length_after_km`` is safe for the *synthetic PoC* only when
    ``correction_applied`` is true. Every UNRESOLVED row retains original length.
    ``status`` is GEOMETRY_DERIVED, SYNTHETIC_TOPOLOGY, or UNRESOLVED.
    """
    if not network.lines.index.is_unique:
        raise ValueError("Network line IDs must be unique")
    for name, value in (("endpoint_tolerance_m", endpoint_tolerance_m),
                        ("route_tolerance_m", route_tolerance_m),
                        ("geometry_tolerance_degrees", geometry_tolerance_degrees)):
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    data = _inputs(paths)
    rows = []
    for line_id, line in network.lines.iterrows():
        line_id = str(line_id)
        clean_id = re.sub(r"_\d+$", "", line_id)
        way_id = source_way_id(clean_id)
        row: dict[str, Any] = {
            "line_id": line_id,
            "bus0": str(line.bus0), "bus1": str(line.bus1),
            "voltage_kv": float(line.v_nom),
            "source_way_id": way_id, "clean_line_id": clean_id,
            "length_before_km": float(line.length),
            "length_after_km": float(line.length),
            "length_delta_km": 0.0,
            "build_stored_km": np.nan,
            "final_geometry_geodesic_km": np.nan,
            "final_geometry_epsg3857_km": np.nan,
            "raw_source_geodesic_km": np.nan,
            "clean_source_geodesic_km": np.nan,
            "clean_route_geodesic_km": np.nan,
            "virtual_connector_geodesic_km": np.nan,
            "decomposition_residual_km": np.nan,
            "status": "UNRESOLVED", "correction_applied": False,
            "length_physical_verified": False,
            "exact_id_match": False, "geometry_matches_model": False,
            "geometry_valid": False, "geometry_connected": False,
            "geometry_simple_chain": False, "endpoint_match": False,
            "endpoint_max_error_m": np.nan,
            "topology_ignored_fragment_km": 0.0,
            "endpoint_tolerance_m": endpoint_tolerance_m,
            "route_tolerance_m": route_tolerance_m,
            "geometry_tolerance_degrees": geometry_tolerance_degrees,
            "source": "OSM raw -> exact clean parent -> exact build line ID -> base.nc",
            "source_code_lineage": BUILD_LINEAGE,
            "caveat": MODEL_GEOMETRY_CAVEAT,
            "unresolved_reason": "",
        }
        issues = []
        built = _one(data["build"], "line_id", line_id)
        csv = _one(data["csv"], "line_id", line_id)
        clean = _one(data["clean"], "line_id", clean_id)
        raw = _one(data["raw"], "id", way_id)
        for stage, record in (("build_geojson", built), ("build_csv", csv), ("clean", clean), ("raw", raw)):
            if record is None:
                issues.append(f"missing_or_duplicate_{stage}_id")
        row["exact_id_match"] = not issues
        if raw is not None:
            row["raw_source_geodesic_km"] = geodesic_length_km(raw.geometry)
        if clean is not None:
            row["clean_source_geodesic_km"] = geodesic_length_km(clean.geometry)
        if built is not None and csv is not None:
            geometry = built.geometry
            row["build_stored_km"] = float(csv.length) / 1000.0
            try:
                row["geometry_matches_model"] = bool(
                    geometry.equals_exact(wkt.loads(str(csv.geometry)), geometry_tolerance_degrees)
                    and geometry.equals_exact(wkt.loads(str(line.geometry)), geometry_tolerance_degrees)
                )
            except (ValueError, TypeError, GEOSException):
                row["geometry_matches_model"] = False
            if not row["geometry_matches_model"]:
                issues.append("build_csv_geojson_model_geometry_disagree")
            if any(
                str(record[field]) != str(line[field])
                for record in (built, csv) for field in ("bus0", "bus1")
            ):
                issues.append("build_model_bus_ids_disagree")
            if any(
                not np.isclose(float(record.voltage) / 1000, float(line.v_nom))
                for record in (built, csv, clean) if record is not None
            ):
                issues.append("source_model_voltage_disagree")
            if not np.isclose(row["build_stored_km"], float(line.length), atol=1e-9, rtol=1e-10):
                issues.append("build_model_stored_length_disagree")
            if str(line.bus0) not in network.buses.index or str(line.bus1) not in network.buses.index:
                issues.append("missing_model_bus")
            else:
                bus_xy = [tuple(network.buses.loc[str(line[bus]), ["x", "y"]]) for bus in ("bus0", "bus1")]
                check = inspect_chain_geometry(geometry, *bus_xy, endpoint_tolerance_m)
                geometry_issue = check.pop("geometry_issue")
                row.update(check)
                if geometry_issue:
                    issues.append(geometry_issue)
            if row["geometry_valid"]:
                row["final_geometry_geodesic_km"] = geodesic_length_km(geometry)
                row["final_geometry_epsg3857_km"] = float(_metric(geometry).length / 1000)
                if clean is not None:
                    row.update(decompose_route_length(geometry, clean.geometry, route_tolerance_m))
                    row["decomposition_residual_km"] = (
                        row["final_geometry_geodesic_km"]
                        - row["clean_route_geodesic_km"]
                        - row["virtual_connector_geodesic_km"]
                    )
                    if not np.isfinite(row["decomposition_residual_km"]) or abs(row["decomposition_residual_km"]) > 1e-8:
                        issues.append("route_connector_decomposition_failed")
        if not issues:
            row["length_after_km"] = row["final_geometry_geodesic_km"]
            row["length_delta_km"] = row["length_after_km"] - row["length_before_km"]
            row["correction_applied"] = True
            row["status"] = (
                "SYNTHETIC_TOPOLOGY"
                if row["virtual_connector_geodesic_km"] > route_tolerance_m / 1000
                else "GEOMETRY_DERIVED"
            )
        row["unresolved_reason"] = ";".join(issues)
        rows.append(row)
    return pd.DataFrame(rows).set_index("line_id")


def summarize_length_stages(network: Any, paths: Paths) -> pd.DataFrame:
    """Summarize each voltage independently, without duplicating split parents."""
    data = _inputs(paths)
    rows = []
    for voltage in sorted(network.lines.v_nom.unique()):
        volts = str(round(float(voltage) * 1000))
        raw_mask = data["raw"]["tags.voltage"].fillna("").astype(str).map(
            lambda value: volts in value.replace(" ", "").split(";")
        )
        for stage, frame in (("OSM raw", data["raw"].loc[raw_mask]),
                             ("OSM cleaned", data["clean"].loc[data["clean"].voltage.eq(int(volts))]),
                             ("Network build geometry", data["build"].loc[data["build"].voltage.eq(int(volts))])):
            rows.append({
                "voltage_kv": float(voltage), "stage": stage,
                "object_count": len(frame),
                "geometry_geodesic_km": sum(geodesic_length_km(g) for g in frame.geometry),
                "geometry_epsg3857_km": sum(_metric(g).length / 1000 for g in frame.geometry),
                "stored_length_km": float(frame["length"].sum() / 1000) if stage == "Network build geometry" else np.nan,
            })
        lines = network.lines.loc[network.lines.v_nom.eq(voltage)]
        rows.append({
            "voltage_kv": float(voltage), "stage": "base.nc Line.length",
            "object_count": len(lines), "geometry_geodesic_km": np.nan,
            "geometry_epsg3857_km": np.nan, "stored_length_km": float(lines.length.sum()),
        })
    return pd.DataFrame(rows)
