"""Audit source evidence and charging assumptions for the 19 modeled 220 kV lines."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd
import pypsa
from pyproj import Transformer
from shapely import transform as transform_geometry
from shapely.geometry import shape
from scipy.spatial import cKDTree
from shapely.ops import unary_union

from .audit_mn35k_parameters import geodesic_length_km, source_way_id
from .mn35k_lengths import audit_line_lengths
from .diagnostic_inputs import (analysis_bundle, source_manifest, read_config, base_settings)
from .passive_inputs import logical_input

TO_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
LINE_TYPE_NAME = "Al/St 240/40 2-bundle 220.0"
TYPE_REFERENCE = (
    "PyPSA 1.2.4 standard line type library; reference stored in network.line_types"
)
FREQ_HZ = 50.0
VOLTAGE_KV = 220.0



def charging_mvar(
    length_km: float,
    c_nf_per_km: float,
    frequency_hz: float,
    circuits: float,
    voltage_kv: float,
) -> tuple[float, float]:
    """Return positive B(S) and 1 pu charging (MVAr) for parallel 3-phase circuits."""
    values = (length_km, c_nf_per_km, frequency_hz, circuits, voltage_kv)
    if not all(math.isfinite(float(value)) and float(value) > 0 for value in values):
        raise ValueError("Charging inputs must be finite and positive")
    b_siemens = (
        2 * math.pi * frequency_hz * c_nf_per_km * 1e-9 * length_km * circuits
    )
    return b_siemens, b_siemens * voltage_kv**2



def circuit_verdict(
    raw_circuits: Any, raw_cables: Any, raw_voltage: Any, model_parallel: float
) -> tuple[str, str]:
    """Classify the provenance of num_parallel without conflating bundle and circuit."""
    if ";" in str(raw_voltage):
        return "UNRESOLVED", "UNRESOLVED_MULTI_VOLTAGE_CIRCUITS"
    try:
        circuits = float(raw_circuits)
    except (TypeError, ValueError):
        circuits = math.nan
    if math.isfinite(circuits) and circuits > 0:
        if math.isclose(circuits, model_parallel, rel_tol=0, abs_tol=1e-9):
            return "SOURCE_SUPPORTED", "EXPLICIT_OSM_CIRCUITS"
        return "UNRESOLVED", "OSM_CIRCUITS_MODEL_MISMATCH"
    try:
        cables = float(raw_cables)
    except (TypeError, ValueError):
        cables = math.nan
    if math.isfinite(cables) and cables > 0:
        inferred = cables / 3
        if math.isclose(inferred, model_parallel, rel_tol=0, abs_tol=1e-9):
            return "STANDARD_ASSUMPTION", "INFERRED_CABLES_DIVIDED_BY_THREE"
        return "UNRESOLVED", "CABLE_TO_CIRCUIT_INFERENCE_MISMATCH"
    if math.isclose(model_parallel, 1.0):
        return "UNRESOLVED", "UNVERIFIED_CIRCUIT_COUNT"
    return "UNRESOLVED", "UNVERIFIED_CIRCUIT_COUNT"



def geometry_topology(geometry: Any, tolerance_m: float = 0.001) -> dict[str, Any]:
    """Describe the endpoint multigraph used by the existing chain validator."""
    parts = list(geometry.geoms) if geometry.geom_type == "MultiLineString" else [geometry]
    parts = [
        part
        for part in parts
        if part.geom_type == "LineString"
        and not part.is_empty
        and geodesic_length_km(part) > tolerance_m / 1000
    ]
    endpoints = [xy[:2] for part in parts for xy in (part.coords[0], part.coords[-1])]
    if not endpoints:
        return {
            "geometry_part_count": 0,
            "geometry_endpoint_node_count": 0,
            "geometry_endpoint_edge_count": 0,
            "geometry_endpoint_component_count": 0,
            "geometry_endpoint_branch_nodes": 0,
            "geometry_endpoint_cycle_rank": 0,
            "geometry_parts_simple": False,
            "topology_ignored_fragment_km": 0.0,
        }
    projected = np.asarray([TO_3857.transform(*point) for point in endpoints])
    parent = list(range(len(endpoints)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, right in cKDTree(projected).query_pairs(tolerance_m):
        union(left, right)
    graph = nx.MultiGraph()
    roots = [find(index) for index in range(len(endpoints))]
    for index in range(0, len(roots), 2):
        graph.add_edge(roots[index], roots[index + 1])
    component_count = nx.number_connected_components(graph)
    return {
        "geometry_part_count": len(parts),
        "geometry_endpoint_node_count": graph.number_of_nodes(),
        "geometry_endpoint_edge_count": graph.number_of_edges(),
        "geometry_endpoint_component_count": component_count,
        "geometry_endpoint_branch_nodes": sum(
            degree > 2 for _, degree in graph.degree
        ),
        "geometry_endpoint_cycle_rank": (
            graph.number_of_edges() - graph.number_of_nodes() + component_count
        ),
        "geometry_parts_simple": all(part.is_simple for part in parts),
        "topology_ignored_fragment_km": float(
            sum(
                geodesic_length_km(part)
                for part in (
                    list(geometry.geoms)
                    if geometry.geom_type == "MultiLineString"
                    else [geometry]
                )
                if part.geom_type == "LineString"
                and geodesic_length_km(part) <= tolerance_m / 1000
            )
        ),
    }



def _features(path: Path, key: str) -> dict[str, dict[str, Any]]:
    document = json.loads(path.read_text())
    result = {}
    for feature in document["features"]:
        props = feature["properties"]
        identifier = str(props[key])
        if identifier in result:
            raise ValueError(f"Duplicate source geometry ID {identifier} in {path}")
        result[identifier] = {
            "properties": props,
            "geometry": shape(feature["geometry"]),
        }
    return result



def _properties_tags(properties: dict[str, Any]) -> dict[str, Any]:
    other_tags = properties.get("other_tags")
    if isinstance(other_tags, dict):
        return other_tags
    if isinstance(other_tags, str):
        return dict(re.findall(r'"([^"]+)"=>"([^"]*)"', other_tags))
    return {}



def _projected_length_km(geometry: Any) -> float:
    projected = transform_geometry(geometry, TO_3857.transform, interleaved=False)
    return float(projected.length / 1000)



def geometry_overlap_metrics(
    geometry: Any,
    other_geometries: dict[str, Any],
    source_geometry: Any | None = None,
    other_source_geometries: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Measure repeated linework using geodesic lengths, without counting point crossings."""
    parts = list(geometry.geoms) if geometry.geom_type == "MultiLineString" else [geometry]
    parts = [part for part in parts if part.geom_type == "LineString" and not part.is_empty]
    union = unary_union(parts)
    internal_overlap = max(
        0.0,
        sum(geodesic_length_km(part) for part in parts) - geodesic_length_km(union),
    )
    cross_overlaps = {}
    cross_source_overlaps = {}
    for line_id, other in other_geometries.items():
        intersection = geometry.intersection(other)
        overlap_km = geodesic_length_km(intersection) if not intersection.is_empty else 0.0
        if overlap_km > 0.001:
            cross_overlaps[line_id] = overlap_km
            source_overlap_km = 0.0
            if source_geometry is not None and other_source_geometries is not None:
                source_route = unary_union(
                    [source_geometry, other_source_geometries[line_id]]
                )
                source_overlap = intersection.intersection(source_route)
                if not source_overlap.is_empty:
                    source_overlap_km = geodesic_length_km(source_overlap)
            cross_source_overlaps[line_id] = source_overlap_km
    return {
        "build_internal_duplicate_overlap_km": internal_overlap,
        "build_cross_line_overlap_km": sum(cross_overlaps.values()),
        "build_cross_line_overlap_in_source_km": sum(cross_source_overlaps.values()),
        "build_cross_line_overlap_outside_source_km": max(
            0.0, sum(cross_overlaps.values()) - sum(cross_source_overlaps.values())
        ),
        "build_cross_line_overlap_ids": ";".join(sorted(cross_overlaps)),
        "build_cross_line_overlap_details_json": json.dumps(
            {
                line_id: {
                    "shared_km": cross_overlaps[line_id],
                    "in_source_km": cross_source_overlaps[line_id],
                    "outside_source_km": max(
                        0.0,
                        cross_overlaps[line_id] - cross_source_overlaps[line_id],
                    ),
                }
                for line_id in sorted(cross_overlaps)
            },
            sort_keys=True,
        ),
    }



def _lineage_audit(
    network: pypsa.Network, length_source: pypsa.Network, paths
) -> tuple[pd.DataFrame, dict[str, dict]]:
    # Reuse the existing exact-ID audit, which never alters the supplied Network.
    length_audit = audit_line_lengths(length_source, paths)
    saved_length = pd.read_csv(logical_input(paths, "reports/model_refinement/line_length_changes.csv"), encoding="utf-8-sig").set_index("line_id")
    raw = _features(logical_input(paths, "pypsa-earth/resources/MN_35k/osm/raw/all_raw_lines.geojson"), "id")
    clean = _features(logical_input(paths, "pypsa-earth/resources/MN_35k/osm/clean/all_clean_lines.geojson"), "line_id")
    build = _features(logical_input(paths, "pypsa-earth/resources/MN_35k/base_network/all_lines_build_network.geojson"), "line_id")
    build_geometries = {
        str(line_id): build[str(line_id)]["geometry"]
        for line_id in network.lines.index[network.lines.v_nom.eq(220)]
        if str(line_id) in build
    }
    build_source_geometries = {
        line_id: raw[source_way_id(line_id)]["geometry"]
        for line_id in build_geometries
        if source_way_id(line_id) in raw
    }
    rows = []
    for line_id, line in network.lines.loc[network.lines.v_nom.eq(220)].iterrows():
        line_id = str(line_id)
        way_id = source_way_id(line_id)
        clean_id = re.sub(r"_\d+$", "", line_id)
        if way_id not in raw or clean_id not in clean or line_id not in build:
            raise ValueError(f"Incomplete exact OSM geometry lineage for {line_id}")
        record = length_audit.loc[line_id].to_dict()
        if line_id not in saved_length.index:
            raise ValueError(f"Existing length report lacks {line_id}")
        prior_length = saved_length.loc[line_id]
        for field in (
            "length_before_km",
            "length_after_km",
            "build_stored_km",
            "final_geometry_geodesic_km",
            "final_geometry_epsg3857_km",
            "raw_source_geodesic_km",
            "clean_route_geodesic_km",
            "virtual_connector_geodesic_km",
        ):
            if not np.isclose(
                float(record[field]),
                float(prior_length[field]),
                rtol=1e-9,
                atol=1e-8,
                equal_nan=True,
            ):
                raise AssertionError(
                    f"Recomputed length audit differs from the saved report for {line_id}:{field}"
                )
        if str(record["status"]) != str(prior_length["status"]):
            raise AssertionError(f"Recomputed length status differs for {line_id}")
        record["saved_length_audit_status"] = str(prior_length.status)
        record["saved_length_audit_after_km"] = float(prior_length.length_after_km)
        record["saved_length_audit_matches_recomputed"] = True
        raw_geometry = raw[way_id]["geometry"]
        clean_geometry = clean[clean_id]["geometry"]
        build_geometry = build[line_id]["geometry"]
        raw_properties = raw[way_id]["properties"]
        topology = geometry_topology(build_geometry)
        overlap = geometry_overlap_metrics(
            build_geometry,
            {
                other_id: other_geometry
                for other_id, other_geometry in build_geometries.items()
                if other_id != line_id
            },
            raw_geometry,
            {
                other_id: source_geometry
                for other_id, source_geometry in build_source_geometries.items()
                if other_id != line_id
            },
        )
        raw_tags = _properties_tags(raw_properties)
        raw_circuits = raw_tags.get("tags.circuits", raw_properties.get("tags.circuits"))
        raw_wires = raw_tags.get("tags.wires", raw_properties.get("tags.wires"))
        props = build[line_id]["properties"]
        record.update(
            {
                "line_id": line_id,
                "poc_model_length_km": float(network.lines.at[line_id, "length"]),
                "source_way_id": way_id,
                "clean_line_id": clean_id,
                "build_line_id": line_id,
                "bus0": str(line.bus0),
                "bus1": str(line.bus1),
                "raw_refs_count": len(raw_properties.get("refs") or []),
                "raw_voltage_tag": raw_properties.get("tags.voltage"),
                "raw_frequency_tag_hz": raw_properties.get("tags.frequency"),
                "raw_cables_tag": raw_properties.get("tags.cables"),
                "raw_circuits_tag": raw_circuits,
                "raw_wires_tag": raw_wires,
                "clean_circuits": clean[clean_id]["properties"].get("circuits"),
                "build_circuits": props.get("circuits"),
                "raw_geometry_simple": bool(raw_geometry.is_simple),
                "clean_geometry_simple": bool(clean_geometry.is_simple),
                "raw_geometry_geodesic_km": geodesic_length_km(raw_geometry),
                "raw_geometry_epsg3857_km": _projected_length_km(raw_geometry),
                "clean_geometry_geodesic_km": geodesic_length_km(clean_geometry),
                "clean_geometry_epsg3857_km": _projected_length_km(clean_geometry),
                "build_geometry_geodesic_km": geodesic_length_km(build_geometry),
                "build_geometry_epsg3857_km": _projected_length_km(build_geometry),
                "epsg3857_to_geodesic_ratio_build": (
                    _projected_length_km(build_geometry)
                    / geodesic_length_km(build_geometry)
                    if geodesic_length_km(build_geometry)
                    else np.nan
                ),
                "raw_geometry_exact_id_source": "OSM raw way ID",
                "clean_geometry_exact_id_source": "OSM cleaned way ID",
                "build_geometry_exact_id_source": "network-build line ID",
                "source_lineage": (
                    f"OSM way {way_id} → clean {clean_id} → build/model {line_id}"
                ),
                **topology,
                **overlap,
            }
        )
        rows.append(record)
    return pd.DataFrame(rows).set_index("line_id").sort_index(), raw



def build_audit_tables(paths) -> dict[str, pd.DataFrame | dict]:
    if pypsa.__version__ != "1.2.4":
        raise ValueError(f"Expected PyPSA 1.2.4, got {pypsa.__version__}")
    network = pypsa.Network(logical_input(paths, "pypsa-earth/networks/MN_35k_poc/base.nc"))
    if (len(network.buses), len(network.lines), len(network.transformers)) != (
        173,
        159,
        27,
    ):
        raise ValueError("Unexpected MN_35k_poc source topology")
    graph = nx.Graph()
    graph.add_nodes_from(network.buses.index.astype(str))
    for frame in (network.lines, network.transformers):
        graph.add_edges_from(
            (str(row.bus0), str(row.bus1)) for row in frame.itertuples()
        )
    components = sorted(nx.connected_components(graph), key=len, reverse=True)
    main_buses = components[0]
    target = network.lines.loc[
        network.lines.v_nom.eq(220)
        & network.lines.bus0.astype(str).isin(main_buses)
        & network.lines.bus1.astype(str).isin(main_buses)
    ].copy()
    if len(main_buses) != 166 or len(target) != 19:
        raise ValueError("Expected 19 220 kV lines in the 166-bus main component")

    length_source = pypsa.Network(logical_input(paths, "pypsa-earth/networks/MN_35k/base.nc"))
    length_audit, raw_features = _lineage_audit(network, length_source, paths)
    if set(length_audit.index) != set(target.index.astype(str)):
        raise AssertionError("Length audit does not exactly cover the 19 target lines")
    prior_charging = pd.read_csv(logical_input(paths, "reports/reactive_diagnostics/line_charging_analysis.csv")).set_index("line_id")
    prior_circuits = pd.read_csv(logical_input(paths, "reports/model_refinement/osm_line_evidence.csv"), encoding="utf-8-sig").set_index("line_id")
    prior_parameters = pd.read_csv(logical_input(paths, "reports/model_refinement/line_parameter_changes.csv"), encoding="utf-8-sig").set_index("line_id")
    if not set(target.index.astype(str)).issubset(prior_charging.index):
        raise ValueError("Existing charging audit lacks a target line")
    if not set(target.index.astype(str)).issubset(prior_circuits.index):
        raise ValueError("Existing OSM circuit evidence lacks a target line")
    if not set(target.index.astype(str)).issubset(prior_parameters.index):
        raise ValueError("Existing parameter audit lacks a target line")

    type_row = network.line_types.loc[LINE_TYPE_NAME]
    c_nf_km = float(type_row.c_per_length)
    f_hz = float(type_row.f_nom)
    type_current_ka = float(type_row.i_nom)
    circuit_rows = []
    inventory_rows = []
    charging_rows = []
    sensitivity_rows = []
    correction_rows = []
    for line_id, line in target.sort_index().iterrows():
        line_id = str(line_id)
        length = float(line.length)
        circuits = float(line.num_parallel)
        old_charge = prior_charging.loc[line_id]
        circuit_evidence = prior_circuits.loc[line_id]
        parameter_evidence = prior_parameters.loc[line_id]
        length_row = length_audit.loc[line_id]
        if not math.isclose(
            float(circuit_evidence.model_num_parallel),
            float(line.num_parallel),
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise AssertionError(f"Stored OSM circuit evidence differs from model for {line_id}")
        raw_props = raw_features[str(source_way_id(line_id))]["properties"]
        tags = _properties_tags(raw_props)
        raw_circuits = tags.get("tags.circuits", raw_props.get("tags.circuits"))
        raw_cables = raw_props.get("tags.cables")
        raw_wires = tags.get("tags.wires", raw_props.get("tags.wires"))
        circuit_status, circuit_reason = circuit_verdict(
            raw_circuits, raw_cables, raw_props.get("tags.voltage"), circuits
        )
        if circuit_status == "UNRESOLVED":
            circuit_reason = (
                "UNVERIFIED_CIRCUIT_COUNT"
                if circuit_reason == "UNVERIFIED_CIRCUIT_COUNT"
                else circuit_reason
            )
        if str(line.type) != LINE_TYPE_NAME:
            raise ValueError(f"Unexpected LineType for 220 kV line {line_id}: {line.type}")
        r_calc = float(type_row.r_per_length) * length / circuits
        x_calc = float(type_row.x_per_length) * length / circuits
        b_siemens, q_mvar = charging_mvar(
            length, c_nf_km, f_hz, circuits, VOLTAGE_KV
        )
        q_model = float(line.b) * VOLTAGE_KV**2
        q_prior = float(old_charge.charging_q_1pu_mvar)
        if not math.isclose(q_mvar, q_model, rel_tol=1e-10, abs_tol=1e-8):
            raise AssertionError(f"Independent Q does not reproduce PyPSA for {line_id}")
        if not math.isclose(q_mvar, q_prior, rel_tol=1e-9, abs_tol=1e-7):
            raise AssertionError(f"Independent Q does not reproduce existing audit for {line_id}")
        if not math.isclose(r_calc, float(line.r), rel_tol=1e-9, abs_tol=1e-9):
            raise AssertionError(f"Independent R does not reproduce PyPSA for {line_id}")
        if not math.isclose(x_calc, float(line.x), rel_tol=1e-9, abs_tol=1e-9):
            raise AssertionError(f"Independent X does not reproduce PyPSA for {line_id}")
        s_nom_calc = math.sqrt(3) * VOLTAGE_KV * type_current_ka * circuits
        if not math.isclose(s_nom_calc, float(line.s_nom), rel_tol=1e-9, abs_tol=1e-8):
            raise AssertionError(f"Independent thermal rating does not reproduce for {line_id}")

        length_status = (
            "UNRESOLVED"
            if str(length_row.status) == "UNRESOLVED"
            else "STANDARD_ASSUMPTION"
        )
        length_reason = (
            str(length_row.unresolved_reason)
            if length_status == "UNRESOLVED"
            else "OSM geometry-derived model length; virtual connectors and field route unverified"
        )
        type_status = "STANDARD_ASSUMPTION"
        circuit_rows.append(
            {
                "line_id": line_id,
                "osm_way_id": source_way_id(line_id),
                "raw_voltage_tag": raw_props.get("tags.voltage"),
                "raw_frequency_tag_hz": raw_props.get("tags.frequency"),
                "raw_cables_tag": raw_cables,
                "raw_circuits_tag": raw_circuits,
                "raw_wires_tag": raw_wires,
                "clean_circuits": length_row.clean_circuits,
                "build_circuits": length_row.build_circuits,
                "model_num_parallel": circuits,
                "circuit_source_evidence": circuit_evidence.circuit_evidence,
                "circuit_verdict": circuit_status,
                "circuit_unresolved_reason": circuit_reason,
                "line_type": str(line.type),
                "line_type_bundle_conductors_per_phase": 2,
                "bundle_is_num_parallel": False,
                "line_type_capacitance_nf_per_km": c_nf_km,
                "line_type_r_ohm_per_km": float(type_row.r_per_length),
                "line_type_x_ohm_per_km": float(type_row.x_per_length),
                "line_type_i_nom_ka": type_current_ka,
                "line_type_reference": str(type_row.references),
                "type_verdict": type_status,
                "bundle_circuit_overlap_review_required": bool(
                    pd.notna(raw_cables)
                    and int(float(raw_cables)) > 0
                    and "2-bundle" in str(line.type)
                ),
                "field_verified": False,
                "interpretation": (
                    "2-bundle describes subconductors per phase in the standard LineType; "
                    "num_parallel describes separate 3-phase circuits"
                ),
            }
        )
        inventory_rows.append(
            {
                "line_id": line_id,
                "bus0": str(line.bus0),
                "bus1": str(line.bus1),
                "voltage_kv": float(line.v_nom),
                "main_component_bus_count": len(main_buses),
                "model_length_km": length,
                "model_length_verdict": length_status,
                "model_length_basis": length_reason,
                "num_parallel": circuits,
                "circuit_verdict": circuit_status,
                "line_type": str(line.type),
                "line_type_verdict": type_status,
                "line_type_bundle_conductors_per_phase": 2,
                "c_per_length_nf_per_km": c_nf_km,
                "r_per_length_ohm_per_km": float(type_row.r_per_length),
                "x_per_length_ohm_per_km": float(type_row.x_per_length),
                "i_nom_ka": type_current_ka,
                "s_nom_mva": float(line.s_nom),
                "r_ohm": float(line.r),
                "x_ohm": float(line.x),
                "b_siemens": float(line.b),
                "charging_q_1pu_mvar": q_mvar,
                "existing_charging_q_1pu_mvar": q_prior,
                "q_difference_mvar": q_mvar - q_prior,
                "line_length_status": str(length_row.status),
                "circuit_evidence": circuit_evidence.circuit_evidence,
                "parameter_class": str(parameter_evidence.parameter_class),
                "field_verified": False,
            }
        )
        charging_rows.append(
            {
                "line_id": line_id,
                "bus0": str(line.bus0),
                "bus1": str(line.bus1),
                "voltage_kv": VOLTAGE_KV,
                "length_km": length,
                "length_source": "MN_35k_poc/base.nc; geometry lineage in 220kv_length_evidence.csv",
                "num_parallel_circuits": circuits,
                "num_parallel_source": circuit_reason,
                "line_type": str(line.type),
                "line_type_bundle_conductors_per_phase": 2,
                "c_nf_per_km": c_nf_km,
                "capacitance_source": TYPE_REFERENCE,
                "frequency_hz": f_hz,
                "b_siemens_model": float(line.b),
                "b_siemens_independent": b_siemens,
                "q_1pu_mvar_model": q_model,
                "q_1pu_mvar_independent": q_mvar,
                "existing_audit_q_1pu_mvar": q_prior,
                "difference_vs_existing_mvar": q_mvar - q_prior,
                "q_pf_terminal_sum_mvar": float(old_charge.q_terminal_net_candidate_mvar),
                "terminal_sum_is_no_load_charging": False,
                "length_verdict": length_status,
                "circuit_verdict": circuit_status,
                "capacitance_verdict": type_status,
                "overall_field_verified": False,
            }
        )

        route_length = float(length_row.clean_route_geodesic_km)
        route_available = math.isfinite(route_length) and route_length > 0
        route_adoptable = bool(
            route_available
            and bool(length_row.geometry_simple_chain)
            and bool(length_row.endpoint_match)
            and bool(length_row.exact_id_match)
        )
        route_q = (
            charging_mvar(route_length, c_nf_km, f_hz, circuits, VOLTAGE_KV)[1]
            if route_available
            else np.nan
        )
        q_one_circuit = charging_mvar(
            length, c_nf_km, f_hz, 1.0, VOLTAGE_KV
        )[1]
        sensitivity_rows.append(
            {
                "line_id": line_id,
                "baseline_length_km": length,
                "baseline_num_parallel": circuits,
                "baseline_c_nf_per_km": c_nf_km,
                "baseline_q_mvar": q_mvar,
                "source_route_only_length_km": route_length if route_available else np.nan,
                "source_route_only_q_mvar": route_q,
                "source_route_only_length_delta_q_mvar": route_q - q_mvar,
                "source_route_only_status": (
                    "GEOMETRY_ONLY_NOT_ADOPTABLE"
                    if route_available and not route_adoptable
                    else "GEOMETRY_ONLY_CANDIDATE_NOT_FIELD_VERIFIED"
                    if route_adoptable
                    else "UNAVAILABLE"
                ),
                "q_if_num_parallel_1_mvar": q_one_circuit,
                "q_if_num_parallel_1_delta_mvar": q_one_circuit - q_mvar,
                "q_if_capacitance_minus_20_percent_mvar": q_mvar * 0.8,
                "q_if_capacitance_plus_20_percent_mvar": q_mvar * 1.2,
                "sensitivity_values_are_hypothetical": True,
                "no_model_change_applied": True,
            }
        )
        if line_id == "221951632-1_0":
            correction_rows.append(
                {
                    "line_id": line_id,
                    "parameter": "length_km",
                    "observed_issue": (
                        "Stored length equals the source way's EPSG:3857 length, "
                        "while EPSG:3857 distorts Mongolia distances and the built route "
                        "is branched/looped."
                    ),
                    "current_value": length,
                    "geometric_comparison_value": float(length_row.raw_source_geodesic_km),
                    "geometric_comparison_unit": "km",
                    "correction_status": "CORRECTION_CANDIDATE_BLOCKED_BY_TOPOLOGY",
                    "apply_now": False,
                    "blocker": (
                        "Built geometry endpoint graph has branch/cycle and does not validate "
                        "as a simple bus0-to-bus1 route; the raw way length is not a safe replacement."
                    ),
                    "recommendation": (
                        "Resolve source way-to-bus connectivity and conductor route before "
                        "considering a geodesic length replacement."
                    ),
                }
            )

    charging = pd.DataFrame(charging_rows).set_index("line_id").sort_index()
    if len(charging) != 19 or not math.isclose(
        float(charging.q_1pu_mvar_independent.sum()),
        713.98017837238,
        rel_tol=0,
        abs_tol=2e-6,
    ):
        raise AssertionError("220 kV charging total did not reproduce 713.980 MVAr")
    return {
        "inventory": pd.DataFrame(inventory_rows).set_index("line_id").sort_index(),
        "length": length_audit,
        "circuits": pd.DataFrame(circuit_rows).set_index("line_id").sort_index(),
        "charging": charging,
        "sensitivity": pd.DataFrame(sensitivity_rows).set_index("line_id").sort_index(),
        "corrections": pd.DataFrame(correction_rows),
        "main_bus_count": len(main_buses),
    }



def _write_report(tables: dict, protected_ok: bool, protected_count: int, output: Path) -> None:
    inventory = tables["inventory"]
    length = tables["length"]
    circuit = tables["circuits"]
    charging = tables["charging"]
    sensitivity = tables["sensitivity"]
    assert isinstance(inventory, pd.DataFrame)
    assert isinstance(length, pd.DataFrame)
    assert isinstance(circuit, pd.DataFrame)
    assert isinstance(charging, pd.DataFrame)
    assert isinstance(sensitivity, pd.DataFrame)
    q_total = float(charging.q_1pu_mvar_independent.sum())
    frequency_hz = float(charging.frequency_hz.iloc[0])
    source_route_ok = sensitivity.source_route_only_status.eq(
        "GEOMETRY_ONLY_CANDIDATE_NOT_FIELD_VERIFIED"
    )
    q_route_hybrid = float(
        sensitivity.loc[source_route_ok, "source_route_only_q_mvar"].sum()
        + sensitivity.loc[~source_route_ok, "baseline_q_mvar"].sum()
    )
    overlap_pairs = {}
    for line_id, row in length.iterrows():
        for other_id, metrics in json.loads(
            row.build_cross_line_overlap_details_json
        ).items():
            pair = tuple(sorted((line_id, other_id)))
            overlap_pairs.setdefault(pair, metrics)
    overlap_pairwise_total_km = sum(
        metrics["shared_km"] for metrics in overlap_pairs.values()
    )
    overlap_outside_source_km = sum(
        metrics["outside_source_km"] for metrics in overlap_pairs.values()
    )
    overlap_table = [
        "| 선로 쌍 | build 공통 길이(km) | 원본 OSM way 내(km) | 원본 외(km) |",
        "|---|---:|---:|---:|",
    ]
    overlap_table.extend(
        f"| {left} / {right} | {metrics['shared_km']:.6f} | "
        f"{metrics['in_source_km']:.6f} | {metrics['outside_source_km']:.6f} |"
        for (left, right), metrics in sorted(overlap_pairs.items())
    )
    q_n1 = float(sensitivity.q_if_num_parallel_1_mvar.sum())
    explicit = int(circuit.circuit_verdict.eq("SOURCE_SUPPORTED").sum())
    inferred = int(circuit.circuit_verdict.eq("STANDARD_ASSUMPTION").sum())
    unresolved_circuits = int(circuit.circuit_verdict.eq("UNRESOLVED").sum())
    q_shares = (
        100 * charging.q_1pu_mvar_independent / q_total
    ).sort_values(ascending=False)
    worst_id = str(q_shares.index[0])
    report = [
        "# MN_35k PoC 220 kV 송전선 19개 입력 데이터 감사",
        "",
        "> 목적은 전기정수의 신뢰성을 평가하는 것이며, 선로·NetCDF를 수정하거나 AC PF를 실행하지 않았다.",
        "",
        "## 요약 판정",
        "",
        f"- 확인 대상은 166모선 주 연결요소의 **{len(inventory)}개 220 kV 선로**다.",
        f"- 저장 길이와 현재 표준 LineType·회선 가정으로 계산한 1 pu 충전 Q 합은 **{q_total:.3f} MVAr**로 기존 진단 CSV와 일치한다.",
        f"- 가장 큰 기여는 `{worst_id}`: **{charging.at[worst_id, 'q_1pu_mvar_independent']:.3f} MVAr**, "
        f"{q_shares.at[worst_id]:.2f}%다.",
        f"- 회선수 입력은 명시적 OSM `circuits` {explicit}개, `cables/3` 추정 {inferred}개, "
        f"미해결 {unresolved_circuits}개다. 명시 OSM 태그도 현장 설비 확인을 뜻하지 않는다.",
        "- 19개 모두 LineType/정전용량/정격은 PyPSA 표준 타입 가정이며 실제 도체·번들·전류 정격을 입증하지 못한다.",
        "- 길이 검토는 18개가 OSM geometry 기반 합성 추정, 1개(`221951632-1_0`)는 분기·루프 topology로 미해결이다.",
        "- 확인 가능한 즉시 적용 수정은 **없다**. `221951632-1_0`의 투영 길이 문제는 확인됐지만 실제 bus-to-bus route가 미확정이라 수정 후보는 보류다.",
        "",
        "## 데이터 계보와 범위",
        "",
        "원본 OSM way → 정제 `way-1` → build `way-1_0` → `MN_35k_poc/base.nc`의 정확 ID를 연결했다. "
        "전압 필터 단계 기록은 OSM raw 52 way, cleaned 41 way, build 19 branch다. 정제 과정은 OSM `circuits`·"
        "`cables`·frequency 태그와 geometry를 사용한다. 모델의 bus ID는 OSM 선로 geometry endpoint와 동일한 식별자가 아니다. "
        "build 과정에서 버스에 스냅하고 가상 connector를 union/linemerge한다. OSM extract의 시점·revision metadata는 "
        "이 입력 묶음에 기록되어 있지 않다.",
        "",
        "## 길이 근거와 EPSG:3857",
        "",
        "EPSG:3857은 몽골 위도대에서 동서/평면 길이를 확대한다. 따라서 원본 OSM에서 계산한 geodesic 길이와 "
        "3857 평면 길이는 다른 측정이다. build 단계 `fix_overpassing_lines`는 미터 좌표계에서 길이를 계산하고, "
        "`base_network._load_lines_from_osm`은 이를 km로 변환한다. 후속 PoC geometry audit는 topology가 허용될 때 "
        "geodesic 길이를 채택한다. 아래 CSV는 raw/clean/build 각각의 두 길이와 connector·topology 판정을 제공한다.",
        "",
        "| line_id | 모델 길이 km | raw geodesic km | raw EPSG:3857 km | 최종 geometry geodesic km | clean route km | connector km | build parts | branch nodes | cycle rank | 판정 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for line_id in inventory.index:
        row = length.loc[line_id]
        report.append(
            f"| {line_id} | {inventory.at[line_id, 'model_length_km']:.3f} | "
            f"{row.raw_geometry_geodesic_km:.3f} | "
            f"{row.raw_geometry_epsg3857_km:.3f} | {row.build_geometry_geodesic_km:.3f} | "
            f"{row.clean_route_geodesic_km:.3f} | {row.virtual_connector_geodesic_km:.3f} | "
            f"{int(row.geometry_part_count)} | {int(row.geometry_endpoint_branch_nodes)} | "
            f"{int(row.geometry_endpoint_cycle_rank)} | {inventory.at[line_id, 'model_length_verdict']} |"
            )
    report.extend(
            [
                "",
                "### 분할·가상 접속 및 중복 geometry",
                "",
                f"선로 내부 반복 구간은 19개에서 측정 허용오차 이내로 검출되지 않았다. 서로 다른 build 선로 간 "
                f"양의 길이 중첩은 {len(overlap_pairs)}개 쌍, pairwise 합계 {overlap_pairwise_total_km:.6f} km이며, "
                f"이 중 {overlap_outside_source_km:.6f} km는 해당 두 OSM raw-way geometry 바깥에 있다. "
                "이는 build/snap connector가 겹친 형상일 가능성이 높지만 실제 공통 도체 경로의 증거는 아니다. "
                "길이 correction으로 바로 반영하지 않으며, CSV에는 선로별 중첩 상대 ID와 상세 길이를 보존한다.",
                "",
                *overlap_table,
            ]
    )
    row221 = length.loc["221951632-1_0"]
    row484 = length.loc["484333456-1_0"]
    report.extend(
        [
            "",
            "### `221951632-1_0` — 수정 보류",
            "",
            f"원본 OSM way 221951632 및 clean way `221951632-1`은 각각 404개 ref의 단순 `LineString`이고 geodesic "
            f"길이는 {row221.raw_geometry_geodesic_km:.3f} km다. 이 way를 EPSG:3857로 투영한 길이는 "
            f"{row221.raw_geometry_epsg3857_km:.3f} km로, 모델 저장 길이 {row221.length_before_km:.3f} km와 일치한다. "
            f"기존과 최종 geometry의 geodesic 길이는 각각 {row221.raw_geometry_geodesic_km:.3f} km 및 "
            f"{row221.build_geometry_geodesic_km:.3f} km이며, 최종 geometry에는 {row221.virtual_connector_geodesic_km:.3f} km "
            f"가상 connector가 포함된다.",
            f"최종 build geometry는 {int(row221.geometry_part_count)}개 선분이고, endpoint graph에서 degree>2 노드 "
            f"{int(row221.geometry_endpoint_branch_nodes)}개 및 cycle rank {int(row221.geometry_endpoint_cycle_rank)}를 가져 "
            f"단일 bus0=5→bus1=17 경로로 검증되지 않았다. build 단계의 overpass 분할과 bus-endpoint connector 결합 뒤 "
            "형상이 원본 OSM 단순 선형과 달라졌음을 확인했으나, 실제 선로 도체가 어느 경로인지 알 수 없다. "
            f"반복 선분 중복은 내부 {row221.build_internal_duplicate_overlap_km:.6f} km, 다른 감사 선로와의 "
            f"공유구간 {row221.build_cross_line_overlap_km:.6f} km로 검출되지 않아, 관측된 cycle을 단순 중복선분으로 "
            "설명할 증거는 없다. "
            "따라서 155.253 km를 대체 길이로 사용하지 않으며, 234.885 km의 EPSG:3857 왜곡도 인지했으나 교체를 적용하지 않았다.",
            "",
            "### `484333456-1_0` — 가장 큰 charging 기여",
            "",
            f"원본 way 484333456은 835 refs의 단순 `LineString`; raw/clean geodesic 길이는 "
            f"{row484.raw_geometry_geodesic_km:.3f} km, EPSG:3857 길이는 "
            f"{row484.raw_geometry_epsg3857_km:.3f} km다. 최종 build geometry는 세 조각의 연속 단순 chain으로 "
            f"geodesic {row484.build_geometry_geodesic_km:.3f} km다. 그중 source route는 "
            f"{row484.clean_route_geodesic_km:.3f} km, 가상 connector는 {row484.virtual_connector_geodesic_km:.3f} km다. "
            "저장 모델 길이는 connector를 포함한 geodesic build 길이다. OSM `circuits`는 비어 있고 `cables=6`에서 "
            "50 Hz 3상 기준 `6/3=2`를 추정했다. 이 회선수 추정과 2-bundle LineType이 동시에 적용됐지만, OSM `cables` "
            "태그만으로 두 번들 도체의 개수를 포함하는지 확인할 수 없어 회선/번들 중복계산 여부는 미확정이다. "
            f"해당 build 선로 자체의 반복 선분 및 다른 220 kV 감사 선로와의 양의 길이 중첩은 각각 "
            f"{row484.build_internal_duplicate_overlap_km:.6f} km, "
            f"{row484.build_cross_line_overlap_km:.6f} km로 검출되지 않았다.",
            "",
            "## 회선수·LineType·C 검토",
            "",
            "`Al/St 240/40 2-bundle 220.0`의 `2-bundle`은 표준 타입의 상별 bundle 도체 구성을 뜻하고, "
            "`num_parallel`은 별개의 3상 회선수를 뜻한다. 두 수를 같은 변수로 취급하지 않았다. 표준 타입 값은 "
            f"R={float(inventory.r_per_length_ohm_per_km.iloc[0]):g} Ω/km, "
            f"X={float(inventory.x_per_length_ohm_per_km.iloc[0]):g} Ω/km, "
            f"C={float(inventory.c_per_length_nf_per_km.iloc[0]):g} nF/km, "
            f"I_nom={float(inventory.i_nom_ka.iloc[0]):g} kA, f={frequency_hz:g} Hz다. "
            "이 값은 몽골 선로의 제작도·도체 배치·측정치가 아니다. `s_nom=√3·V·I_nom·num_parallel`도 "
            "타입 정격전류를 사용한 모델 정격이며 실제 열정격을 보증하지 않는다.",
            "",
            "| 회선수 판정 | 개수 | 의미 |",
            "|---|---:|---|",
            f"| SOURCE_SUPPORTED | {explicit} | 원본 OSM `circuits`가 있고 현재 model 값과 일치; field_verified=False |",
            f"| STANDARD_ASSUMPTION | {inferred} | OSM `cables`에서 50 Hz 3상 가정으로 회선수 추정 |",
            f"| UNRESOLVED | {unresolved_circuits} | circuit count가 없거나 multi-voltage mapping 불명확 |",
            "",
            "세부 tag·판정은 `220kv_circuit_type_review.csv`, 길이/ID 계보는 `220kv_length_evidence.csv`를 참조한다. "
            "raw OSM tags는 출처 레코드로 보존했지만 현장 설치상태·태그 최신성은 검증되지 않았다.",
            "",
            "## 1 pu 충전 Q 독립 검산",
            "",
            "PyPSA 1.2.4 LineType 단위는 `r_per_length`, `x_per_length`=Ω/km, `c_per_length`=nF/km, "
            "`f_nom`=Hz, `length`=km다. PyPSA 입력 계수 계산을 독립 재현한 식은 "
            "`B = 2π f C L n_parallel` (S), `Q_1pu = B·V_nom²` (MVAr)다. Series 값은 "
            "`R=r_per_length·L/n_parallel`, `X=x_per_length·L/n_parallel`; bundle 효과는 standard LineType의 "
            "R/X/C 가정에 내재하고 병렬 회선수와 별도다.",
            "",
            f"독립 합계 **{q_total:.6f} MVAr**, 기존 `line_charging_analysis.csv` 합계와 차이는 "
            f"{float(charging.difference_vs_existing_mvar.abs().max()):.3g} MVAr 이하, "
            f"계산상 susceptance 합은 {charging.b_siemens_independent.sum():.9f} S다. "
            "이 합은 1 pu 무부하 shunt charging 등가량이다. PF의 line terminal `q0+q1`은 전압·전력흐름·series "
            "리액턴스를 포함하므로 별도 열로만 기록했으며 이를 charging 총합으로 쓰지 않았다.",
            "",
            "선로별 비교: `220kv_charging_validation.csv`.",
            "",
            "## 가상 입력 민감도 — 실제 설비값이 아님",
            "",
            f"- 모델 회선수 값을 모두 1로 바꾼 산술 민감도: {q_n1:.3f} MVAr "
            f"({q_n1 - q_total:+.3f} MVAr). 특히 484333456의 모델 2회선을 1로 가정하면 "
            f"{sensitivity.at['484333456-1_0', 'q_if_num_parallel_1_mvar']:.3f} MVAr가 되지만 적용 근거는 없다.",
            "- LineType C를 기준의 0.8/1.2배로 가정하면 충전 Q도 선형으로 변한다. 이 계수는 불확실성 민감도이며 새 도체자료가 아니다.",
            f"- topology 검증된 18개에서만 OSM source route만 사용하고 connector 길이를 제외한 계산을 합산하면 "
            f"해당 18개 기준 Q는 {sensitivity.loc[source_route_ok, 'source_route_only_q_mvar'].sum():.3f} MVAr다. "
            f"미해결 `221951632-1_0`은 기준 Q를 유지한 혼합 합계는 {q_route_hybrid:.3f} MVAr다. "
            "이 역시 가상 길이 민감도이며 현장 길이 대체안이 아니다.",
            "- `221951632-1_0` raw-way geodesic 길이만 수식에 넣은 Q는 산술 예시로만 산출하고, branched topology 때문에 채택하지 않는다.",
            "",
            "세부 line × scenario 결과는 `220kv_parameter_sensitivity.csv`다. 민감도 분석에서 network object나 원본 파일은 변경하지 않았다.",
            "",
            "## 수정 판단",
            "",
            "현재 line length·회선수·LineType 중 실제 몽골 설비를 근거로 바로 바꿀 수 있는 값은 없다. "
            "`221951632-1_0`은 EPSG:3857 거리 사용이 수치상 드러나는 correction review 후보지만 bus-to-bus route가 "
            "branched/looped라 후보 geodesic 길이를 대입하면 안 된다. 이를 위한 검토 행만 "
            "`220kv_correction_candidates.csv`에 저장했고 `apply_now=False`다. 484333456의 connector 제외 또는 "
            "회선수 1 변경도 실제 접속·회선 자료 없이는 적용하지 않는다.",
            "",
            "우선 확보할 자료는 (1) 운영자 선로 목록과 단선도 및 정확한 양단 설비 ID, (2) 해당 19개 회선수·도체 "
            "형식·bundle 구성·상배열, (3) 준공 도면 또는 GIS centerline/측량 길이, (4) 온도·풍속 등 정격 조건과 "
            "실제 ampacity, (5) OSM extract 날짜 및 way revision 이력이다. 이 자료로 OSM bus snap/connectors를 "
            "검증한 뒤 길이와 LineType을 개별 갱신해야 한다.",
            "",
            f"보호 대상 모델 및 기존 보고서 SHA-256 보존: **{protected_ok}** "
            f"({protected_count}개 파일). NetCDF·기존 보고서 변경과 AC PF 실행은 없었다.",
            "",
        ]
    )
    (output / "220kv_line_audit_report.md").write_text("\n".join(report))



def _generate(paths, output):
    protected_before = source_manifest(paths)
    tables = build_audit_tables(paths)
    length = tables["length"]
    circuits = tables["circuits"]
    inventory = tables["inventory"]
    charging = tables["charging"]
    sensitivity = tables["sensitivity"]
    corrections = tables["corrections"]
    for table, filename in (
        (inventory, "220kv_line_inventory.csv"),
        (length, "220kv_length_evidence.csv"),
        (circuits, "220kv_circuit_type_review.csv"),
        (charging, "220kv_charging_validation.csv"),
        (sensitivity, "220kv_parameter_sensitivity.csv"),
        (corrections, "220kv_correction_candidates.csv"),
    ):
        table.to_csv(
            output / filename,
            index=table.index.name == "line_id",
            float_format="%.12g",
        )
    protected_after = source_manifest(paths)
    preserved = protected_before == protected_after
    if not preserved:
        changed = sorted(
            name
            for name in protected_before.keys() | protected_after.keys()
            if protected_before.get(name) != protected_after.get(name)
        )
        raise AssertionError(f"Protected input changed: {changed}")
    _write_report(tables, preserved, len(protected_before), output)
    return {
        "line_count": len(inventory),
        "charging_total_mvar": float(charging.q_1pu_mvar_independent.sum()),
        "protected_file_count": len(protected_before),
        "protected_files_unchanged": preserved,
        "ac_power_flow_executed": False,
        "corrections_applied": False,
    }


EXPECTED_FILES = ('220kv_charging_validation.csv', '220kv_circuit_type_review.csv', '220kv_correction_candidates.csv', '220kv_length_evidence.csv', '220kv_line_audit_report.md', '220kv_line_inventory.csv', '220kv_parameter_sensitivity.csv')


def analyze_for_verification(paths, destination):
    return analysis_bundle(paths, destination, EXPECTED_FILES, lambda output: _generate(paths, output))
