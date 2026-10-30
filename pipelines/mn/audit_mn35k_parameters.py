#!/usr/bin/env python3
"""Audit MN_35k line, transformer, length, and PF-readiness data.

This script is read-only with respect to source data and NetCDF models. It writes
only new MN_35k parameter-review deliverables under the injected output_dir.
Original reports/expansion remains an explicitly selected read-only input.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import pypsa
import yaml
from pyproj import Geod


from dataclasses import dataclass

from .artifacts import new_artifact_directory
from .paths import Paths


@dataclass(frozen=True)
class AuditInputs:
    network: Path
    raw_lines: Path
    clean_lines: Path
    build_lines_geo: Path
    build_lines_csv: Path
    build_buses_csv: Path
    transformers_csv: Path
    plants_csv: Path
    plant_mapping_csv: Path
    bus_mapping_csv: Path
    config_default: Path

    @classmethod
    def from_paths(cls, paths: Paths):
        paths.validate()
        earth = paths.earth_checkout
        base = earth / "resources/MN_35k"
        expansion = paths.source_project / "reports/expansion"
        return cls(
            earth / "networks/MN_35k/base.nc",
            base / "osm/raw/all_raw_lines.geojson",
            base / "osm/clean/all_clean_lines.geojson",
            base / "base_network/all_lines_build_network.geojson",
            base / "base_network/all_lines_build_network.csv",
            base / "base_network/all_buses_build_network.csv",
            base / "base_network/all_transformers_build_network.csv",
            earth / "resources/MN/powerplants.csv",
            expansion / "mn_35k_plant_bus_mapping.csv",
            expansion / "mn_35k_bus_mapping.csv",
            earth / "config.default.yaml",
        )


LINE_LIBRARY_RELATIVE = Path("pypsa/data/standard_types/line_types.csv")
GEOD = Geod(ellps="WGS84")

LINE_DOCS = "https://docs.pypsa.org/latest/user-guide/components/lines/"
TRANSFORMER_DOCS = "https://docs.pypsa.org/latest/user-guide/components/transformers/"
PANDAPOWER_STDTYPES = "https://pandapower.readthedocs.io/en/latest/std_types.html"
PANDAPOWER_LINE_TYPES = "https://pandapower.readthedocs.io/en/latest/std_types/basic.html"


def require_files(paths: list[Path]) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Required audit inputs are missing: " + ", ".join(missing))


def geodesic_length_km(geometry: Any) -> float:
    if geometry is None or geometry.is_empty:
        return 0.0
    return float(GEOD.geometry_length(geometry) / 1000.0)


def metric_length_km(frame: gpd.GeoDataFrame) -> float:
    if frame.empty:
        return 0.0
    projected = frame.to_crs("EPSG:3857")
    return float(projected.geometry.length.sum() / 1000.0)


def source_way_id(value: Any) -> str:
    text = str(value)
    if text.lower() in {"nan", "none", ""}:
        return ""
    return text.split("-", maxsplit=1)[0].split("_", maxsplit=1)[0]


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    longitude1, latitude1, longitude2, latitude2 = map(
        math.radians, (lon1, lat1, lon2, lat2)
    )
    delta_longitude = longitude2 - longitude1
    delta_latitude = latitude2 - latitude1
    a = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude1)
        * math.cos(latitude2)
        * math.sin(delta_longitude / 2) ** 2
    )
    return float(2 * 6_371_008.8 * math.asin(math.sqrt(a)))


def voltage_mask(frame: pd.DataFrame, column: str, raw: bool = False) -> pd.Series:
    if raw:
        return frame[column].fillna("").astype(str).map(
            lambda value: "35000" in value.replace(" ", "").split(";")
        )
    return pd.to_numeric(frame[column], errors="coerce").eq(35000)


def component_map(network: pypsa.Network) -> tuple[nx.Graph, dict[str, str], list[list[str]]]:
    graph = nx.Graph()
    graph.add_nodes_from(network.buses.index.astype(str))
    for frame in (network.lines, network.transformers):
        graph.add_edges_from(
            (str(row.bus0), str(row.bus1)) for row in frame.itertuples()
        )
    components = [
        sorted(component, key=lambda item: (not item.isdigit(), int(item) if item.isdigit() else item))
        for component in nx.connected_components(graph)
    ]
    components.sort(key=lambda component: (-len(component), component[0]))
    bus_component = {bus: component[0] for component in components for bus in component}
    return graph, bus_component, components


def build_length_audit(network: pypsa.Network, paths: Paths) -> tuple[pd.DataFrame, dict[str, Any]]:
    inputs = AuditInputs.from_paths(paths)
    raw = gpd.read_file(inputs.raw_lines)
    cleaned = gpd.read_file(inputs.clean_lines)
    build_geo = gpd.read_file(inputs.build_lines_geo)
    build_csv = pd.read_csv(inputs.build_lines_csv)
    raw_35 = raw.loc[voltage_mask(raw, "tags.voltage", raw=True)].copy()
    clean_35 = cleaned.loc[voltage_mask(cleaned, "voltage")].copy()
    build_geo_35 = build_geo.loc[voltage_mask(build_geo, "voltage")].copy()
    build_csv_35 = build_csv.loc[pd.to_numeric(build_csv["voltage"], errors="coerce").eq(35000)].copy()
    final_35 = network.lines.loc[network.lines.v_nom.eq(35.0)].copy()

    raw_ids = set(raw_35["id"].astype(str))
    clean_ids = {source_way_id(value) for value in clean_35["line_id"]}
    build_ids = {source_way_id(value) for value in build_csv_35["line_id"]}

    stages = [
        {
            "stage": "OSM raw",
            "object_count": len(raw_35),
            "unique_source_way_count": raw_35["id"].nunique(),
            "geometry_geodesic_km": sum(
                geodesic_length_km(geometry) for geometry in raw_35.geometry
            ),
            "geometry_epsg3857_km": metric_length_km(raw_35),
            "stored_length_km": np.nan,
            "notes": "Original 35 kV-tagged OSM ways; lengths recalculated from geometry.",
        },
        {
            "stage": "OSM cleaned",
            "object_count": len(clean_35),
            "unique_source_way_count": len(clean_ids),
            "geometry_geodesic_km": sum(
                geodesic_length_km(geometry) for geometry in clean_35.geometry
            ),
            "geometry_epsg3857_km": metric_length_km(clean_35),
            "stored_length_km": np.nan,
            "notes": "After tag parsing, voltage/frequency/circuit/geometry/region filters.",
        },
        {
            "stage": "Network build geometry",
            "object_count": len(build_geo_35),
            "unique_source_way_count": len(build_ids),
            "geometry_geodesic_km": sum(
                geodesic_length_km(geometry) for geometry in build_geo_35.geometry
            ),
            "geometry_epsg3857_km": metric_length_km(build_geo_35),
            "stored_length_km": float(build_csv_35["length"].sum() / 1000.0),
            "notes": "48 branch records from 44 source way IDs; geometry includes bus-endpoint snapping.",
        },
        {
            "stage": "base.nc Line.length",
            "object_count": len(final_35),
            "unique_source_way_count": len(build_ids),
            "geometry_geodesic_km": np.nan,
            "geometry_epsg3857_km": np.nan,
            "stored_length_km": float(final_35["length"].sum()),
            "notes": "PyPSA length in km copied from network-build CSV; not recalculated from geometry.",
        },
    ]
    frame = pd.DataFrame(stages)
    raw_count = len(raw_35)
    clean_count = len(clean_35)
    build_count = len(build_csv_35)
    raw_missing_clean = sorted(raw_ids - clean_ids)
    clean_missing_build = sorted(clean_ids - build_ids)
    split_records = build_count - len(build_ids)
    info = {
        "raw_count": raw_count,
        "clean_count": clean_count,
        "build_count": build_count,
        "raw_missing_clean": raw_missing_clean,
        "clean_missing_build": clean_missing_build,
        "split_records": split_records,
        "raw_ids": raw_ids,
        "clean_ids": clean_ids,
        "build_ids": build_ids,
    }
    frame["geodesic_delta_from_previous_stage_km"] = frame[
        "geometry_geodesic_km"
    ].diff()
    frame["build_geometry_epsg3857_minus_stored_km"] = np.nan
    frame.loc[2, "build_geometry_epsg3857_minus_stored_km"] = (
        frame.loc[2, "geometry_epsg3857_km"] - frame.loc[2, "stored_length_km"]
    )
    frame["final_minus_build_stored_km"] = np.nan
    frame.loc[3, "final_minus_build_stored_km"] = (
        frame.loc[3, "stored_length_km"] - frame.loc[2, "stored_length_km"]
    )
    return frame, info


def line_parameter_rows(network: pypsa.Network) -> pd.DataFrame:
    line_types = network.line_types
    derived = network.copy()
    derived.calculate_dependent_values()
    lines = network.lines.loc[network.lines.v_nom.eq(35.0)]
    derived_lines = derived.lines.loc[derived.lines.v_nom.eq(35.0)]
    source_library = f"PyPSA package data/{LINE_LIBRARY_RELATIVE.as_posix()}"
    candidates = [
        "70-AL1/11-ST1A 20.0",
        "94-AL1/15-ST1A 20.0",
        "149-AL1/24-ST1A 20.0",
        "184-AL1/30-ST1A 20.0",
        "243-AL1/39-ST1A 20.0",
    ]
    rows: list[dict[str, Any]] = []

    for name, line in lines.iterrows():
        derived_line = derived_lines.loc[name]
        line_type = str(line["type"])
        params = line_types.loc[line_type] if line_type in line_types.index else pd.Series(dtype=float)
        rows.append(
            {
                "record_kind": "existing_35kv_line",
                "line_id": str(name),
                "bus0": str(line["bus0"]),
                "bus1": str(line["bus1"]),
                "line_voltage_kv": float(line["v_nom"]),
                "length_km": float(line["length"]),
                "line_type": line_type,
                "type_nominal_voltage_kv": 110.0 if "110.0" in line_type else np.nan,
                "r_ohm_per_km": params.get("r_per_length", np.nan),
                "x_ohm_per_km": params.get("x_per_length", np.nan),
                "c_nf_per_km": params.get("c_per_length", np.nan),
                "i_nom_ka": params.get("i_nom", np.nan),
                "frequency_hz": params.get("f_nom", np.nan),
                "r_ohm_calculated": float(derived_line["r"]),
                "x_ohm_calculated": float(derived_line["x"]),
                "b_siemens_calculated": float(derived_line["b"]),
                "s_nom_mva_calculated": float(derived_line["s_nom"]),
                "source_or_basis": "Current PyPSA-Earth default fallback; not a validated 35 kV type.",
                "type_reference": params.get("references", ""),
                "confidence": "LOW / voltage mismatch",
                "model_change_recommended": False,
            }
        )

    for name in candidates:
        if name not in line_types.index:
            continue
        params = line_types.loc[name]
        rows.append(
            {
                "record_kind": "candidate_only_not_applied",
                "line_id": "",
                "bus0": "",
                "bus1": "",
                "line_voltage_kv": 35.0,
                "length_km": np.nan,
                "line_type": name,
                "type_nominal_voltage_kv": float(name.rsplit(" ", maxsplit=1)[-1]),
                "r_ohm_per_km": float(params["r_per_length"]),
                "x_ohm_per_km": float(params["x_per_length"]),
                "c_nf_per_km": float(params["c_per_length"]),
                "i_nom_ka": float(params["i_nom"]),
                "frequency_hz": float(params["f_nom"]),
                "r_ohm_calculated": np.nan,
                "x_ohm_calculated": np.nan,
                "b_siemens_calculated": np.nan,
                "s_nom_mva_calculated": np.nan,
                "source_or_basis": f"PyPSA bundled standard type: {params['references']}; see {source_library}",
                "type_reference": params["references"],
                "confidence": "Candidate values only; conductor and 35 kV geometry not verified.",
                "model_change_recommended": False,
            }
        )
    return pd.DataFrame(rows)


def transformer_parameter_rows(network: pypsa.Network) -> pd.DataFrame:
    rows = []
    for name, transformer in network.transformers.iterrows():
        bus0 = str(transformer["bus0"])
        bus1 = str(transformer["bus1"])
        v0 = float(network.buses.at[bus0, "v_nom"])
        v1 = float(network.buses.at[bus1, "v_nom"])
        if {v0, v1} != {35.0, 110.0}:
            continue
        rows.append(
            {
                "transformer_id": str(name),
                "bus0": bus0,
                "bus0_voltage_kv": v0,
                "bus1": bus1,
                "bus1_voltage_kv": v1,
                "s_nom_current_mva": float(transformer["s_nom"]),
                "r_current_pu_on_s_nom_base": float(transformer["r"]),
                "x_current_pu_on_s_nom_base": float(transformer["x"]),
                "tap_ratio_current_pu": float(transformer["tap_ratio"]),
                "tap_side_current": int(transformer["tap_side"]),
                "phase_shift_current_deg": float(transformer["phase_shift"]),
                "x_pu_1mva_base_calculated": float(transformer["x"] / transformer["s_nom"]),
                "s_nom_proposed_mva": np.nan,
                "r_proposed_pu": np.nan,
                "x_proposed_pu": np.nan,
                "tap_ratio_proposed_pu": np.nan,
                "phase_shift_proposed_deg": np.nan,
                "reference": "No OSM equipment/nameplate data found; generated by station-id voltage pairing.",
                "confidence": "UNVERIFIED / retain current values only for structural model",
                "action": "Do not use for PF until nameplate or operator data confirm rating, impedance, and tap settings.",
            }
        )
    return pd.DataFrame(rows)


def plant_and_bus_audit(network: pypsa.Network, paths: Paths) -> tuple[pd.DataFrame, dict[str, Any]]:
    inputs = AuditInputs.from_paths(paths)
    plants = pd.read_csv(inputs.plants_csv)
    plant_map = pd.read_csv(inputs.plant_mapping_csv)
    bus_map = pd.read_csv(inputs.bus_mapping_csv)
    graph, bus_components, components = component_map(network)

    candidate_by_old = {
        str(int(row.old_bus_id)): row for row in bus_map.itertuples(index=False)
    }
    plant_results = []
    for index, plant in plants.iterrows():
        old_bus = str(int(plant["bus"])) if pd.notna(plant["bus"]) else ""
        previous = plant_map.iloc[index] if index < len(plant_map) else None
        candidate = candidate_by_old.get(old_bus)
        mapped_candidate_id = (
            str(candidate.matched_mn35k_bus_id) if candidate is not None else ""
        )
        valid_candidate = mapped_candidate_id in network.buses.index.astype(str)
        candidate_voltage = (
            float(network.buses.at[mapped_candidate_id, "v_nom"])
            if valid_candidate
            else np.nan
        )
        candidate_lon = (
            float(network.buses.at[mapped_candidate_id, "x"])
            if valid_candidate
            else np.nan
        )
        candidate_lat = (
            float(network.buses.at[mapped_candidate_id, "y"])
            if valid_candidate
            else np.nan
        )
        plant_lon = float(plant["lon"]) if pd.notna(plant.get("lon")) else np.nan
        plant_lat = float(plant["lat"]) if pd.notna(plant.get("lat")) else np.nan
        plant_candidate_distance = (
            haversine_m(plant_lon, plant_lat, candidate_lon, candidate_lat)
            if valid_candidate
            else np.nan
        )
        old_to_new_bus_drift = (
            float(candidate.coordinate_match_distance_m)
            if candidate is not None
            else np.nan
        )
        expected_voltage = candidate_voltage if valid_candidate else np.nan
        same_voltage = network.buses.loc[
            network.buses.v_nom.eq(expected_voltage)
        ]
        nearest_id = ""
        nearest_distance = np.nan
        nearest_lon = nearest_lat = nearest_component = ""
        nearest_connected = False
        if not same_voltage.empty and pd.notna(plant_lon) and pd.notna(plant_lat):
            distances = same_voltage.apply(
                lambda bus: haversine_m(
                    plant_lon, plant_lat, float(bus["x"]), float(bus["y"])
                ),
                axis=1,
            )
            nearest_id = str(distances.idxmin())
            nearest_distance = float(distances.min())
            nearest_lon = float(network.buses.at[nearest_id, "x"])
            nearest_lat = float(network.buses.at[nearest_id, "y"])
            nearest_component = bus_components.get(nearest_id, "")
            nearest_connected = bool(graph.degree[nearest_id] > 0)
        row = {
            "plant_record": int(index),
            "plant_name": str(plant.get("Name", "")),
            "capacity_mw": plant.get("Capacity", np.nan),
            "plant_lon": plant_lon,
            "plant_lat": plant_lat,
            "old_bus_id_reference_only": old_bus,
            "mn35k_bus_mapped_from_old_bus_id": mapped_candidate_id,
            "mapped_candidate_voltage_kv": candidate_voltage,
            "mapped_candidate_lon": candidate_lon,
            "mapped_candidate_lat": candidate_lat,
            "old_bus_to_mn35k_bus_coordinate_drift_m": old_to_new_bus_drift,
            "plant_to_mapped_candidate_distance_m": plant_candidate_distance,
            "mapped_candidate_component_min_bus": (
                bus_components.get(mapped_candidate_id, "")
                if valid_candidate
                else ""
            ),
            "mapped_candidate_bus_connected": (
                bool(graph.degree[mapped_candidate_id] > 0)
                if valid_candidate
                else False
            ),
            "nearest_same_voltage_bus_candidate": nearest_id,
            "nearest_same_voltage_bus_voltage_kv": expected_voltage,
            "nearest_same_voltage_bus_lon": nearest_lon,
            "nearest_same_voltage_bus_lat": nearest_lat,
            "plant_to_nearest_same_voltage_bus_distance_m": nearest_distance,
            "nearest_candidate_component_min_bus": nearest_component,
            "nearest_candidate_bus_connected": nearest_connected,
            "spatial_screening_status": (
                "REVIEW_MAPPED_CANDIDATE_GT_10KM"
                if pd.notna(plant_candidate_distance)
                and plant_candidate_distance > 10_000
                else "CANDIDATE_ONLY_NOT_ELECTRICALLY_VERIFIED"
            ),
            "previous_mapping_note": (
                str(previous.get("note", "")) if previous is not None else ""
            ),
        }
        plant_results.append(row)

    isolated = sorted(
        [bus for bus, degree in graph.degree if degree == 0],
        key=lambda item: (not item.isdigit(), int(item) if item.isdigit() else item),
    )
    buses_csv = pd.read_csv(inputs.build_buses_csv)
    isolated_detail = []
    for bus in isolated:
        row = buses_csv.loc[pd.to_numeric(buses_csv["bus_id"], errors="coerce").eq(int(bus))].iloc[0]
        isolated_detail.append(
            {
                "bus_id": bus,
                "voltage_kv": float(row["voltage"]) / 1000.0,
                "station_id": row["station_id"],
                "lon": float(row["lon"]),
                "lat": float(row["lat"]),
                "degree_with_lines_and_transformers": int(graph.degree[bus]),
            }
        )
    bus_summary = {
        "components": [len(component) for component in components],
        "component_count": len(components),
        "isolated": isolated_detail,
        "plants_count": len(plant_results),
        "plants_over_10km": [
            result
            for result in plant_results
            if pd.notna(result["plant_to_mapped_candidate_distance_m"])
            and result["plant_to_mapped_candidate_distance_m"] > 10_000
        ],
        "plant_records_with_candidate": sum(
            bool(result["mn35k_bus_mapped_from_old_bus_id"])
            for result in plant_results
        ),
        "plant_records_mapped_candidate_within_1km": sum(
            pd.notna(result["plant_to_mapped_candidate_distance_m"])
            and result["plant_to_mapped_candidate_distance_m"] <= 1000
            for result in plant_results
        ),
        "plant_mapped_candidate_distance_median": float(
            np.nanmedian(
                [
                    result["plant_to_mapped_candidate_distance_m"]
                    for result in plant_results
                ]
            )
        ),
        "plant_mapped_candidate_distance_max": max(
            plant_results,
            key=lambda result: (
                result["plant_to_mapped_candidate_distance_m"]
                if pd.notna(result["plant_to_mapped_candidate_distance_m"])
                else -1
            ),
        ),
    }
    return pd.DataFrame(plant_results), bus_summary


def finite_sign_summary(network: pypsa.Network) -> dict[str, Any]:
    derived = network.copy()
    derived.calculate_dependent_values()
    lines35 = derived.lines.loc[derived.lines.v_nom.eq(35.0)]
    result = {}
    for column in ("r", "x", "b", "s_nom"):
        values = pd.to_numeric(lines35[column], errors="coerce")
        result[column] = {
            "finite": int(np.isfinite(values).sum()),
            "positive": int((values > 0).sum()),
            "negative": int((values < 0).sum()),
            "zero": int((values == 0).sum()),
            "min": float(values.min()),
            "max": float(values.max()),
        }
    result["stored_r_all_zero"] = bool((network.lines["r"] == 0).all())
    result["stored_x_all_zero"] = bool((network.lines["x"] == 0).all())
    result["transformer_x_pu_1mva"] = float(
        derived.transformers.loc[
            derived.transformers.bus0.map(derived.buses.v_nom).isin([35.0, 110.0])
            & derived.transformers.bus1.map(derived.buses.v_nom).isin([35.0, 110.0]),
            "x_pu",
        ].iloc[0]
    )
    return result


def write_reports(
    network: pypsa.Network,
    line_parameters: pd.DataFrame,
    transformer_parameters: pd.DataFrame,
    length_audit: pd.DataFrame,
    length_info: dict[str, Any],
    plant_mapping: pd.DataFrame,
    bus_info: dict[str, Any],
    parameter_summary: dict[str, Any],
    *,
    paths: Paths,
    report_dir: Path | None = None,
) -> None:
    inputs = AuditInputs.from_paths(paths)
    destination = Path(report_dir) if report_dir is not None else paths.output_dir / "parameter_review"
    with new_artifact_directory(paths, destination, expected_files=REPORT_FILES) as staged:
        _render_reports(network, line_parameters, transformer_parameters, length_audit,
                        length_info, plant_mapping, bus_info, parameter_summary,
                        inputs=inputs, report_dir=staged)
        validate_outputs(network, length_audit, staged)


REPORT_FILES = (
    "mn_35k_line_parameters.csv", "mn_35k_transformer_parameters.csv",
    "mn_35k_length_audit.csv", "mn_35k_plant_bus_audit.csv",
    "mn_35k_parameter_review.md", "mn_35k_pf_readiness.md",
)


def _render_reports(network, line_parameters, transformer_parameters, length_audit,
                    length_info, plant_mapping, bus_info, parameter_summary,
                    *, inputs: AuditInputs, report_dir: Path):
    """Render unchanged report contents only into the private staging directory."""
    lines_path = report_dir / "mn_35k_line_parameters.csv"
    transformers_path = report_dir / "mn_35k_transformer_parameters.csv"
    length_path = report_dir / "mn_35k_length_audit.csv"
    review_path = report_dir / "mn_35k_parameter_review.md"
    readiness_path = report_dir / "mn_35k_pf_readiness.md"

    line_parameters.to_csv(lines_path, index=False, encoding="utf-8-sig")
    transformer_parameters.to_csv(transformers_path, index=False, encoding="utf-8-sig")
    length_audit.to_csv(length_path, index=False, encoding="utf-8-sig")
    plant_mapping.to_csv(report_dir / "mn_35k_plant_bus_audit.csv", index=False, encoding="utf-8-sig")

    current35 = line_parameters.loc[line_parameters.record_kind.eq("existing_35kv_line")]
    candidates = line_parameters.loc[
        line_parameters.record_kind.eq("candidate_only_not_applied")
    ]
    current_type_summary = (
        current35.groupby("line_type").size().sort_values(ascending=False).to_dict()
    )
    type_text = ", ".join(
        f"`{name}` ({count})" for name, count in current_type_summary.items()
    )
    candidate_table = "\n".join(
        "| "
        + " | ".join(
            [
                f"`{row.line_type}`",
                f"{row.type_nominal_voltage_kv:g}",
                f"{row.r_ohm_per_km:.6g}",
                f"{row.x_ohm_per_km:.6g}",
                f"{row.c_nf_per_km:.6g}",
                f"{row.i_nom_ka:.6g}",
                f"{row.frequency_hz:.6g}",
            ]
        )
        + " |"
        for row in candidates.itertuples(index=False)
    )

    raw_length = float(length_audit.iloc[0]["geometry_geodesic_km"])
    clean_length = float(length_audit.iloc[1]["geometry_geodesic_km"])
    build_geom_length = float(length_audit.iloc[2]["geometry_geodesic_km"])
    build_epsg_length = float(length_audit.iloc[2]["geometry_epsg3857_km"])
    input_length = float(length_audit.iloc[2]["stored_length_km"])
    final_length = float(length_audit.iloc[3]["stored_length_km"])
    effective_factor = float(
        yaml.safe_load(inputs.config_default.read_text(encoding="utf-8"))["lines"]["length_factor"]
    )
    line_stats = parameter_summary
    isolated_text = ", ".join(
        f"`{entry['bus_id']}` ({entry['voltage_kv']:g} kV, station_id {entry['station_id']}, "
        f"{entry['lon']:.4f},{entry['lat']:.4f})"
        for entry in bus_info["isolated"]
    ) or "none"
    plants_over = bus_info["plants_over_10km"]
    plants_over_text = ", ".join(
        f"{entry['plant_name']} ({entry['plant_to_mapped_candidate_distance_m'] / 1000:.1f} km; "
        f"old-ID-mapped candidate bus {entry['mn35k_bus_mapped_from_old_bus_id']})"
        for entry in plants_over
    ) or "none"
    plant_distance_table = "\n".join(
        "| "
        + " | ".join(
            [
                str(entry["plant_record"]),
                entry["plant_name"],
                entry["old_bus_id_reference_only"],
                entry["mn35k_bus_mapped_from_old_bus_id"],
                f"{entry['mapped_candidate_voltage_kv']:g}",
                f"{entry['plant_to_mapped_candidate_distance_m'] / 1000:.2f}",
                f"{entry['mapped_candidate_lon']:.4f}, {entry['mapped_candidate_lat']:.4f}",
                entry["nearest_same_voltage_bus_candidate"],
                f"{entry['plant_to_nearest_same_voltage_bus_distance_m'] / 1000:.2f}",
                str(entry["mapped_candidate_component_min_bus"]),
            ]
        )
        + " |"
        for entry in plants_over
    )
    murun = plant_mapping.loc[plant_mapping.plant_name.eq("Murun Solar")].iloc[0]
    farthest_plant = bus_info["plant_mapped_candidate_distance_max"]
    components = ", ".join(map(str, bus_info["components"]))
    transform_default = transformer_parameters.iloc[0]
    md_review = f"""# MN_35k 전기 파라미터 검토

## 결론

35 kV 전용 도체와 35/110 kV 변압기의 실제 설비 사양을 확인하지 못했습니다. 따라서 현재 데이터만으로는 결과 해석에 쓸 수 있는 가정 기반 `MN_35k_poc/base.nc`를 정당화하기 어렵고, 이 작업에서는 실험 NetCDF를 만들거나 기존 모델을 수정하지 않았습니다. 대신 타입 후보와 결측 요구자료를 기록했습니다. 제안표 수치는 실제 몽골 설비값이 아닙니다.

## 1. 35 kV 선로 타입 원인 및 후보

PyPSA-Earth의 `get_linetype_by_voltage_and_country()`는 `lines.ac_types`에서 실제 선로 전압과 가장 가까운 mapping key를 고릅니다. 기본 AC mapping에는 35 kV key가 없고, 132 kV key가 `243-AL1/39-ST1A 110.0` 타입을 가리킵니다. 따라서 35 kV 48개 모두 해당 110 kV 이름의 타입을 받았습니다. 알고리즘은 line type의 nominal voltage와 실선로 전압을 별도로 검증하지 않습니다.

현재 타입 분포: {type_text}.

### PyPSA 라이브러리의 비교 후보 (적용하지 않음)

| LineType | Nominal type voltage | R (Ω/km) | X (Ω/km) | C (nF/km) | I (kA) | f (Hz) |
|---|---:|---:|---:|---:|---:|---:|
{candidate_table}

후보 값은 로컬 PyPSA {pypsa.__version__} 표준 유형 라이브러리에서 확인했습니다. 각 row의 `type_reference` 열 및 라이브러리 `references` 필드는 `pandapower`, Heuck et al., *Elektrische Energieversorgung*, 8판 (2010), Vierweg+Teubner (2013)를 가리킵니다. pandapower 문서는 표준 유형 라이브러리가 유형별 파라미터 데이터베이스임을 설명합니다. 이 근거는 후보값의 출처 계보이지, 몽골 35 kV 선로의 도체·배열을 증명하지 않습니다. 특히 20 kV 변형 타입을 35 kV에 쓰는 것은 고유한 PoC 가정이며, 이번에는 선택하지 않았습니다.

**단위와 계산:** PyPSA `LineType`에서 `r_per_length`, `x_per_length`는 Ω/km, `c_per_length`는 nF/km, `i_nom`은 kA, `f_nom`은 Hz입니다. `type`이 지정되면 PyPSA는 타입의 길이당 임피던스/정전용량을 line `length` (km)와 `num_parallel`에 적용해 총 `r` (Ω), `x` (Ω), `b` (S)를 계산합니다. 용량은 PyPSA-Earth 빌더에서 `sqrt(3) × v_nom(kV) × i_nom(kA) × num_parallel` MVA로 설정됩니다. 현재 모델 35 kV 선로의 계산된 양(원본 netCDF 저장 전 값)은 모두 finite·positive이나, 잘못된 타입이므로 전기적 정확성을 뜻하지 않습니다.

## 2. 변압기 17개

이 17개는 OSM transformer feature가 아니라 동일 station ID의 여러 AC 전압 버스를 연결하도록 PyPSA-Earth가 합성한 topology branch입니다. OSM 원본에는 이 변압기들의 정격 MVA, 시험 임피던스, 부하손/무부하손, 탭 범위·중성 위치, 현재 탭, 벡터 그룹 자료가 없습니다.

| 필드 | 현재 생성값 | 해석 |
|---|---:|---|
| `s_nom` | {transform_default['s_nom_current_mva']:.0f} MVA | `config.default.yaml` 공통 기본값; 17개 개별 정격 자료 아님 |
| `r` | {transform_default['r_current_pu_on_s_nom_base']:.3g} pu | 손실 근거 없이 0으로 입력되어 있어 물리 변압기 손실을 나타내지 않음 |
| `x` | {transform_default['x_current_pu_on_s_nom_base']:.3g} pu | 기본값이며 단락시험/명판 근거 없음 |
| 탭비 / 측 | {transform_default['tap_ratio_current_pu']:.3g} pu / {int(transform_default['tap_side_current'])} | PyPSA default; 실제 무부하비·OLTC 설정 확인 필요 |
| 위상변위 | {transform_default['phase_shift_current_deg']:.1f}° | PyPSA default, phase-shifting 설비 확인 자료 없음 |
| 1 MVA 기준으로 재표현된 `x_pu` | {parameter_summary['transformer_x_pu_1mva']:.8g} pu | `x_pu = x / s_nom`; 원본 입력 `x`의 기준 용량을 유의 |

PyPSA 입력 `Transformer.x`/`r`는 `s_nom`과 버스 nominal voltage 기준 pu이고 `s_nom`은 MVA입니다. PyPSA의 dependent-value representation은 1 MVA 기반 `x_pu = x / s_nom`, `r_pu = r / s_nom`입니다. Transformer tap ratio는 pu, `tap_side`는 0/1, `phase_shift`는 degree입니다. type이 비어 있으면 이 수동 입력값을 사용합니다.

**2,000 MVA가 적절하다고 확인할 근거는 없습니다.** 이는 공통 소프트웨어 기본값일 뿐 각 변압기 정격이 아니며, 계통 한계/부하 검증에 쓰면 용량을 과대평가할 수 있습니다. `s_nom`, `r`, `x`, 탭/위상변위는 17개 모두 미확정으로 남겼습니다. 명판·운영자 자료나 목적이 명시된 민감도 시나리오 없이 숫자를 바꾸지 않습니다.

## 3. 35 kV 선로 길이 단계 감사

| 단계 | 35 kV 레코드 | 원본 OSM way ID 수 | geometry 측지거리 (km) | EPSG:3857 geometry 길이 (km) | stored/network length (km) |
|---|---:|---:|---:|---:|---:|
"""
    for row in length_audit.itertuples(index=False):
        def fmt_metric(value: Any) -> str:
            return "—" if pd.isna(value) else f"{float(value):,.3f}"

        md_review += (
            f"| {row.stage} | {row.object_count} | {row.unique_source_way_count} | "
            f"{fmt_metric(row.geometry_geodesic_km)} | {fmt_metric(row.geometry_epsg3857_km)} | "
            f"{fmt_metric(row.stored_length_km)} |\n"
        )
    md_review += f"""

원본 62 ways 중 정제 단계에 남은 것은 {length_info['clean_count']}개입니다. 정제에서 제외된 35 kV 원본 ID: {', '.join(length_info['raw_missing_clean']) or 'none'}. 정제된 {length_info['clean_count']} ways 중 네트워크 입력에는 {len(length_info['build_ids'])}개 원본 ID에서 {length_info['build_count']} branch records가 만들어졌습니다. 빌드 단계에서 제외된 clean IDs ({len(length_info['clean_missing_build'])}): {', '.join(length_info['clean_missing_build']) or 'none'}. branch 수와 원본 way 수의 차이에는 라인 분할이 포함됩니다.

차이는 단일 보정계수로 설명되지 않습니다.

1. raw → cleaned 측지거리: {raw_length:,.3f} → {clean_length:,.3f} km. OSM way 559232557 한 건이 남지 않았고, 전압·주파수·회선·geometry·확장 국경 필터가 적용됩니다.
2. cleaned geometry → network build: 61 way → 44 source IDs, 48 branch records. 선로 끝점을 가장 가까운 동일전압 버스에 붙이고, 같은 버스에 붙은 선로를 제거하며, overpassing line 분할 옵션도 적용될 수 있습니다. 48 build geometry의 측지거리는 {build_geom_length:,.3f} km입니다.
3. `all_lines_build_network.csv` 저장 길이는 {input_length:,.3f} km입니다. 빌더의 `fix_overpassing_lines()`는 `to_crs(distance_crs).geometry.length` (미터)를 `length`로 저장합니다. 이 값은 overpassing line 분할 직후, `set_lines_ids()`가 line endpoints를 버스 좌표로 연결하기 전 단계에서 계산됩니다. 설정 거리 CRS EPSG:3857의 투영길이는 측지거리와 다릅니다. 이후 geometry는 MultiLineString 형태가 될 수 있지만 저장된 length는 다시 계산되지 않습니다.
4. `base_network.py`는 build CSV의 length m를 1,000으로 나눠 PyPSA line `length` km로 가져오므로 `base.nc`는 {final_length:,.3f} km를 유지합니다. 설정 `lines.length_factor={effective_factor:g}`은 `base_network` 생성이나 이 길이 임포트에 곱해지지 않습니다. PyPSA-Earth downstream transmission-cost 계산에서만 cost 용도로 적용됩니다. 따라서 {input_length:,.3f} km의 차이를 1.25로 보정한 결과로 해석하면 안 됩니다.

길이를 OSM raw geometry와 일치시키도록 덮어쓰지 않았습니다. CRS 기반 길이 정의, snapping/분할에 따른 선분 추적, 저하된 OSM way의 필터링을 추가 확인해야 합니다.

## 4. 발전소 매핑 및 토폴로지

이전 숫자 `bus` 값은 기존 모델 내 참조일 뿐 MN_35k bus identity로 재사용하지 않았습니다. 전압 제약 일대일 공간 대응 후보를 다시 검증했고, 상세는 [mn_35k_plant_bus_audit.csv](./mn_35k_plant_bus_audit.csv)에 있습니다.

- 발전소 27행은 후보 모선을 모두 찾았으나 이는 지리 후보일 뿐 접속 증거가 아닙니다.
- old bus ID를 공간 대응한 확장 버스와 발전소 좌표 사이의 거리가 10 km를 초과하는 후보: {plants_over_text}. 이 거리는 실제 발전소 위치→후보 버스 거리이며, 기존 버스와 확장 버스 좌표 간 drift와는 별도입니다. 가장 먼 후보는 {farthest_plant['plant_name']} ({farthest_plant['plant_to_mapped_candidate_distance_m'] / 1000:.1f} km)입니다.

| Plant row | Plant | Old bus ID (reference only) | Spatially mapped MN_35k bus | kV | Plant-to-mapped bus km | Mapped bus lon, lat | Nearest same-kV bus candidate | Distance km | Component |
|---:|---|---:|---:|---:|---:|---|---:|---:|---:|
{plant_distance_table}

- 공간 대응된 후보 모선과 동일한 nominal voltage 내에서 최근접 버스 후보를 별도 계산했습니다. 확장 모델에서 old bus 숫자 ID를 찾아 전압을 추론하지 않습니다. 이 역시 공간 후보일 뿐 실제 전기 접속 모선이라는 뜻은 아닙니다. {bus_info['plant_records_mapped_candidate_within_1km']}/27개 old-ID-mapped 후보만 발전소 좌표 1 km 안에 있고, mapped 후보 거리 중앙값은 {bus_info['plant_mapped_candidate_distance_median'] / 1000:.2f} km입니다.
- Murun Solar의 old-ID-mapped candidate는 bus {murun['mn35k_bus_mapped_from_old_bus_id']}입니다. 발전소-후보 버스 거리는 {murun['plant_to_mapped_candidate_distance_m'] / 1000:.2f} km이고, old bus 12에서 new bus 15로의 버스간 좌표 drift는 {murun['old_bus_to_mn35k_bus_coordinate_drift_m']:.1f} m입니다. 기존 1.25 km 수치는 버스간 drift이지 발전소-버스 거리가 아닙니다.
- 선로+변압기 그래프는 {bus_info['component_count']}개 연결요소 ({components})이며, 고립 버스는 {isolated_text}입니다.
- 버스 118/124는 `all_buses_build_network.csv`에 builder-generated `station_id` 93/98의 35 kV transmission-tagged bus로 남아 있지만 선로나 생성 변압기 어느 쪽에도 연결되지 않았습니다. PyPSA-Earth의 nearest same-voltage branch-end assignment가 두 버스를 사용하지 않은 결과입니다. 이 `station_id`는 OSM source object ID가 아닙니다. OSM 개체 ID/개폐장치/운전상태가 없어 미연결 이유를 실제 설비 기준으로 더 확정할 수 없습니다. 임의 연결은 하지 않았습니다.

## 5. 후속 파라미터 제안 및 출처

| 항목 | 지금의 안전한 조치 | 확정에 필요한 자료 |
|---|---|---|
| 35 kV line type | CSV의 후보만 민감도 전 단계 후보로 보유; 어느 것도 자동 적용하지 않음 | 도체 종류/단면적, 가닥·번들, 상간/수직 배치, 회선수, 온도 정격, 송전선 또는 케이블 구분 |
| 변압기 17개 | 2,000 MVA/0.1 pu/0 pu를 실측값으로 사용하지 않음; 수정 금지 | 각 변압기 명판, 정격, 시험 `uk%`/부하손, 무부하손, 탭·중성위치, 결선/벡터 그룹 |
| 모델 승인 | `MN_35k_poc` 미생성 | 위 값의 출처 확인 뒤 별도 scenario로 생성하고 결과 검토 |

참고자료 (2026-10-19 확인):

1. [PyPSA Line component 문서]({LINE_DOCS}) — length km, 선로 r/x/b 단위·type 처리 및 nominal rating.
2. [PyPSA Transformer component 문서]({TRANSFORMER_DOCS}) — `s_nom` MVA, r/x pu 기준, tap/phase-shift 단위 및 1 MVA dependent-value conversion.
3. [pandapower Standard Type Libraries]({PANDAPOWER_STDTYPES}) 및 [기본 표준 유형]({PANDAPOWER_LINE_TYPES}) — 표준 유형 라이브러리 의미와 일반 타입 계보. 몽골 장비를 지정하지는 않습니다.
4. 로컬 PyPSA `{LINE_LIBRARY_RELATIVE.as_posix()}` line type register의 `references` 필드 — pandapower; Heuck et al., *Elektrische Energieversorgung* (2010); Vierweg+Teubner (2013). 이는 도체 후보의 출처만 가리키며 35 kV Mongolian geometry를 입증하지 않습니다.
5. PyPSA-Earth local source: `scripts/_helpers.py:get_linetype_by_voltage_and_country`, `scripts/base_network.py`, `scripts/build_osm_network.py`, `scripts/clean_osm_data.py`.

"""
    review_path.write_text(md_review, encoding="utf-8")

    pf_md = f"""# MN_35k 단일 시점 AC Power Flow 준비도

## 판정: 아직 실행 불가

`MN_35k/base.nc`는 공개 OSM topology를 가진 연구용 입력 데이터 모델입니다. 실제 운영망 자료로 검증되지 않았으며, 이 감사에서는 발전기·부하 추가나 AC Power Flow를 수행하지 않았습니다. 35 kV 파라미터의 핵심 근거가 없으므로 신뢰 가능한 `MN_35k_poc/base.nc`도 생성하지 않았습니다.

| 요구 항목 | 확인 상태 | 다음 작업 |
|---|---|---|
| 발전기 | 0대 (base.nc) | 발전소 27행 중 실재·운전상태·용량·P/Q·제어모드·전압 설정을 검증하고 접속 bus 확정 |
| 부하 | 0개 | 특정 날짜·시간의 유효전력/무효전력 또는 검증된 부하 분배 규칙 확보 |
| 슬랙/전압 제어 | 지정된 발전기 없음 | 운전 자료에 맞춰 slack bus, PV/PQ, 전압 setpoint 선택 |
| 35 kV 선로 | 48개에 110 kV type fallback | 도체/기하/회선/온도 정격 자료로 35 kV 타입 파라미터 확인 |
| 35/110 kV 변압기 | 17개 synthetic; rating/impedance unknown | 단위별 명판 및 시험자료 확보; transformer pairing을 실제 변전소 자료로 검증 |
| topology | 연결요소 {components}; 고립 {isolated_text} | bus 118/124의 OSM 및 운영상 존재/연결 확인, 분리망 처리 결정 |
| 발전소 접속 | 기존 숫자 bus ID 사용 불가 | [plant candidate map](./mn_35k_plant_bus_audit.csv)의 좌표 후보를 운영자/원본 자료로 재검증; {plants_over_text} |
| line length | raw/clean/build/final의 metric 정의가 다름 | OSM way→분할 선분 ID 추적 및 길이 기준 확정; length_factor는 자동 보정값 아님 |
| 라인 r/x 저장값 | netCDF 로드 시 모두 0; dependent values는 copy에서 계산 가능 | PF 전 PyPSA에서 계산한 값을 검증; 35 kV fallback을 해결하기 전 결과 해석 금지 |

### 모델 추천

현재 후보 중 다음 전기적 PoC의 출발점으로는 **MN (110/220 kV)**을 추천합니다. MN_35k는 추가된 35 kV branch에 전압 부적합 line type을 적용했고, synthetic 변압기 17개의 정격·임피던스도 미확정이며 고립 모선 2개가 있어, 현재 상태 그대로 PF 결과를 해석하기 어렵습니다. MN도 0 발전기/부하이며 topology·전기정수 검증이 필요하므로 PF-ready라는 뜻은 아닙니다.

권장 순서:

1. 발전소 레코드의 단위·운전상태·bus connection을 검증하고 별도 plant-to-bus mapping 승인.
2. 단일 시간대의 load P/Q와 source dispatch 조건을 정하고 slack/PV bus 규칙을 기록.
3. 기준 MN의 line/transformer parameter와 연결요소를 검증한 뒤 단일 시점 PF 구성.
4. 35 kV 도체/변압기 자료와 bus 118/124 접속 근거를 확보한 다음 MN_35k를 수정하지 않고 별도 `MN_35k_poc` scenario로 재생성.
5. `calculate_dependent_values()` 이후 r/x/b 부호·finite·정격 일관성을 검사하고, 구성된 PF를 별도 결과로 실행·보고.

**현재 수행하지 않은 작업:** 발전기·부하 임의 등록, 슬랙 추정, 고립 버스 임의 접속, AC Power Flow 실행.
"""
    readiness_path.write_text(pf_md, encoding="utf-8")


def validate_outputs(network: pypsa.Network, length_audit: pd.DataFrame, report_dir: Path) -> None:
    lines = pd.read_csv(report_dir / "mn_35k_line_parameters.csv")
    transformers = pd.read_csv(report_dir / "mn_35k_transformer_parameters.csv")
    plants = pd.read_csv(report_dir / "mn_35k_plant_bus_audit.csv")
    model_lines = network.lines.loc[network.lines.v_nom.eq(35.0)]
    assert len(model_lines) == 48
    assert len(lines.loc[lines.record_kind.eq("existing_35kv_line")]) == 48
    assert len(lines.loc[lines.record_kind.eq("candidate_only_not_applied")]) == 5
    assert not lines["model_change_recommended"].astype(bool).any()
    assert len(transformers) == 17
    assert transformers["transformer_id"].is_unique
    assert len(plants) == 27
    plant_distance_columns = (
        "plant_to_mapped_candidate_distance_m",
        "plant_to_nearest_same_voltage_bus_distance_m",
    )
    assert all(column in plants.columns for column in plant_distance_columns)
    assert plants[list(plant_distance_columns)].notna().all().all()
    assert plants["mapped_candidate_lon"].notna().all()
    assert plants["mapped_candidate_lat"].notna().all()
    murun = plants.loc[plants.plant_name.eq("Murun Solar")].iloc[0]
    assert math.isclose(
        float(murun["plant_to_mapped_candidate_distance_m"]),
        3695.0,
        rel_tol=0,
        abs_tol=10,
    )
    assert length_audit.loc[0, "object_count"] == 62
    assert length_audit.loc[1, "object_count"] == 61
    assert length_audit.loc[2, "object_count"] == 48
    assert math.isclose(
        float(length_audit.loc[2, "stored_length_km"]),
        float(length_audit.loc[3, "stored_length_km"]),
        rel_tol=0,
        abs_tol=1e-9,
    )
    assert (report_dir / "mn_35k_parameter_review.md").is_file()
    assert (report_dir / "mn_35k_pf_readiness.md").is_file()


def main(paths: Paths) -> None:
    """Library-only audit entry; no model generation or CLI activation."""
    inputs = AuditInputs.from_paths(paths)
    report_dir = paths.output_dir / "parameter_review"
    require_files(
        [
            inputs.network,
            inputs.raw_lines,
            inputs.clean_lines,
            inputs.build_lines_geo,
            inputs.build_lines_csv,
            inputs.build_buses_csv,
            inputs.transformers_csv,
            inputs.plants_csv,
            inputs.plant_mapping_csv,
            inputs.bus_mapping_csv,
            inputs.config_default,
        ]
    )
    network = pypsa.Network(inputs.network)
    lengths, length_info = build_length_audit(network, paths)
    lines = line_parameter_rows(network)
    transformers = transformer_parameter_rows(network)
    plants, bus_info = plant_and_bus_audit(network, paths)
    parameter_summary = finite_sign_summary(network)
    write_reports(
        network,
        lines,
        transformers,
        lengths,
        length_info,
        plants,
        bus_info,
        parameter_summary,
        paths=paths, report_dir=report_dir,
    )
    validate_outputs(network, lengths, report_dir)
    print(f"Wrote parameter review deliverables to {report_dir}")
    print(
        f"35 kV: {len(network.lines.loc[network.lines.v_nom.eq(35.0)])} lines; "
        f"length raw/clean/build/final = "
        f"{lengths.iloc[0].geometry_geodesic_km:.3f}/"
        f"{lengths.iloc[1].geometry_geodesic_km:.3f}/"
        f"{lengths.iloc[2].stored_length_km:.3f}/"
        f"{lengths.iloc[3].stored_length_km:.3f} km"
    )
    print(
        f"35/110 kV transformer parameters held unmodified for "
        f"{len(transformers)} synthetic branches; no PoC NetCDF generated."
    )
