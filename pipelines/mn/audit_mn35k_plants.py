"""Audit plant identities and spatial bus candidates without changing source data."""
from __future__ import annotations

import ast
import json
import math
import re
import unicodedata
from pathlib import Path

import pandas as pd
import pypsa
from pyproj import CRS, Transformer
from shapely import transform as shapely_transform
from shapely.geometry import Point, shape

from .run_mn35k_acpf import select_component
from .diagnostic_inputs import (analysis_bundle, source_manifest, read_config, base_settings)
from .passive_inputs import logical_input

EARTH_RADIUS_KM = 6371.0088


def normalize_name(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", normalized.casefold()).strip()



def parse_project_ids(value: object) -> dict[str, list[str]]:
    if pd.isna(value):
        return {}
    try:
        parsed = ast.literal_eval(str(value))
    except (ValueError, SyntaxError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    result = {}
    for source, identifiers in parsed.items():
        if isinstance(identifiers, (set, list, tuple)):
            result[str(source)] = sorted(str(identifier) for identifier in identifiers)
        elif identifiers is not None:
            result[str(source)] = [str(identifiers)]
    return result



def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    lon1_rad, lat1_rad, lon2_rad, lat2_rad = map(
        math.radians, (lon1, lat1, lon2, lat2)
    )
    delta_lon = lon2_rad - lon1_rad
    delta_lat = lat2_rad - lat1_rad
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, value)))



def _identity_classifications(plants: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
    normalized = plants.Name.map(normalize_name)
    project_ids = plants.projectID.map(parse_project_ids)
    statuses = ["IDENTITY_UNRESOLVED"] * len(plants)
    groups = [""] * len(plants)
    reasons = ["Project identity is not confirmed by the available row-level evidence."] * len(plants)

    grouped_by_name: dict[str, list[int]] = {}
    for index, name in normalized.items():
        grouped_by_name.setdefault(name, []).append(index)

    for name, indexes in grouped_by_name.items():
        if len(indexes) < 2:
            continue
        coordinates = {
            (round(float(plants.at[index, "lat"]), 5), round(float(plants.at[index, "lon"]), 5))
            for index in indexes
        }
        if name == "ulaanbaatar tes chp mongolia":
            for index in indexes:
                statuses[index] = "DUPLICATE_CANDIDATE"
                groups[index] = "ulaanbaatar_tes_repeated_project_id"
                reasons[index] = (
                    "Six rows share the same normalized name, coordinates, and GEO project ID; "
                    "three repeat 92.6335 MW and three repeat 74.1068 MW with different dates. "
                    "Could be duplicate source snapshots or units; do not sum without unit records."
                )
        elif name == "ulaanbaatar solar" and len(coordinates) > 1:
            for index in indexes:
                statuses[index] = "DISTINCT_CONFIRMED"
                groups[index] = "ulaanbaatar_solar_distinct_source_ids"
                reasons[index] = (
                    "Two records have different coordinates (~44 km apart), years, and GEM IDs; "
                    "treated as distinct source projects, not proof of current operation."
                )
        elif name in {"khushight kundi", "khushig khundii solar"}:
            for index in indexes:
                statuses[index] = "DUPLICATE_CANDIDATE"
                groups[index] = "khushig_khundii_name_variant"
                reasons[index] = (
                    "The names are close variants, locations are ~6.4 km apart, capacity is 15 MW "
                    "in both rows, and project IDs/commissioning years differ. Alias versus "
                    "separate projects is unresolved; capacity is not aggregated."
                )

    for left, right, group, reason in [
        (
            21,
            22,
            "serven_taishir_coordinate_collision",
            "Different names and GEM IDs but coordinates are ~14 m apart with different capacities "
            "and commissioning years; likely a location collision or alias, identity unresolved.",
        ),
        (
            12,
            18,
            "khushig_khundii_name_variant",
            "Khushight Kundi and Khushig Khundii are close name variants with 15 MW each, "
            "~6.4 km apart, separate source IDs and different commissioning years. "
            "Alias versus separate projects is unresolved; capacity is not aggregated.",
        ),
        (
            4,
            6,
            "ulaanbaatar_coal_oil_locality",
            "Lignite/steam-turbine Ulaanbaatar (839 MW) and Oil/CHP-labelled TES records are "
            "~0.57 km apart. They may be separate facilities or overlapping reports; the TES "
            "technology is missing and no source proves fuel/unit relationships.",
        ),
    ]:
        for index in range(len(plants)):
            if index in {left, right} or (
                group == "ulaanbaatar_coal_oil_locality" and index in range(6, 12)
            ):
                groups[index] = group
                if group == "ulaanbaatar_coal_oil_locality" and index == 4:
                    statuses[index] = "MULTI_UNIT_CANDIDATE"
                elif group == "khushig_khundii_name_variant":
                    statuses[index] = "DUPLICATE_CANDIDATE"
                elif statuses[index] != "DUPLICATE_CANDIDATE":
                    statuses[index] = "IDENTITY_UNRESOLVED"
                reasons[index] = reason

    for index, identifiers in project_ids.items():
        if statuses[index] != "IDENTITY_UNRESOLVED" or groups[index]:
            continue
        flattened = [item for values in identifiers.values() for item in values]
        if not flattened:
            reasons[index] = "No parseable source project ID; identity cannot be established."
        elif any(len(set(values)) > 1 for values in identifiers.values()):
            statuses[index] = "MULTI_UNIT_CANDIDATE"
            reasons[index] = (
                "A single source namespace carries multiple project identifiers on this row; "
                "this may represent multiple units or source project associations, but the "
                "identifiers are not proven to be generator IDs and the record is not split."
            )
        else:
            statuses[index] = "DISTINCT_CONFIRMED"
            reasons[index] = (
                "One or more source IDs are unique within their own source namespace and no "
                "unresolved same-name/coordinate collision was found; confirms a distinct "
                "source record, not independent generator units or operating status."
            )
    return statuses, groups, reasons



def _geojson_features(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text())
    return data.get("features", [])



def _osm_evidence(
    lon: float, lat: float, features: dict[str, list[dict]]
) -> dict[str, object]:
    local = CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs"
    )
    project = Transformer.from_crs("EPSG:4326", local, always_xy=True).transform
    origin = shapely_transform(
        Point(lon, lat), project, interleaved=False
    )
    result = {}
    for layer, items in features.items():
        distances = []
        for item in items:
            geometry = item.get("geometry")
            if not geometry:
                continue
            projected = shapely_transform(
                shape(geometry), project, interleaved=False
            )
            distances.append((origin.distance(projected) / 1000, item.get("properties", {})))
        distances.sort(key=lambda pair: pair[0])
        result[f"osm_{layer}_nearest_km"] = distances[0][0] if distances else None
        result[f"osm_{layer}_within_1km"] = sum(distance <= 1 for distance, _ in distances)
        result[f"osm_{layer}_within_5km"] = sum(distance <= 5 for distance, _ in distances)
        result[f"osm_{layer}_nearest_voltage_kv"] = (
            float(distances[0][1]["voltage"]) / 1000
            if distances and pd.notna(distances[0][1].get("voltage"))
            else None
        )
    return result



def build_audit(
    plants: pd.DataFrame, network: pypsa.Network, base_config: dict, *, paths
) -> tuple[pd.DataFrame, pd.DataFrame]:
    plants = plants.copy().reset_index(drop=True)
    plants["record_id"] = plants.index
    plants["normalized_name"] = plants.Name.map(normalize_name)
    plants["parsed_project_ids"] = plants.projectID.map(parse_project_ids)
    plants["project_id_sources"] = plants.parsed_project_ids.map(
        lambda value: ";".join(sorted(value))
    )
    plants["project_ids"] = plants.parsed_project_ids.map(
        lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    plants["identity_status"], plants["identity_group"], plants[
        "identity_reason"
    ] = _identity_classifications(plants)

    main_buses, excluded, _ = select_component(network, base_config)
    main_set = set(main_buses)
    components = {}
    for component_index, buses in enumerate([main_buses, *excluded]):
        for bus in buses:
            components[str(bus)] = component_index

    osm = {
        "generator": _geojson_features(logical_input(paths, "pypsa-earth/resources/MN/osm/clean/all_clean_generators.geojson")),
        "substation": _geojson_features(logical_input(paths, "pypsa-earth/resources/MN/osm/clean/all_clean_substations.geojson")),
        "line": _geojson_features(logical_input(paths, "pypsa-earth/resources/MN/osm/clean/all_clean_lines.geojson")),
    }
    identity_rows = []
    bus_rows = []
    for row in plants.itertuples(index=False):
        lon, lat = float(row.lon), float(row.lat)
        osm_result = _osm_evidence(lon, lat, osm)
        project_ids = row.parsed_project_ids
        identity_rows.append(
            {
                "record_id": row.record_id,
                "name": row.Name,
                "normalized_name": row.normalized_name,
                "project_id_sources": row.project_id_sources,
                "project_ids": row.project_ids,
                "fuel_type": row.Fueltype,
                "technology": row.Technology,
                "capacity_mw": float(row.Capacity),
                "latitude": lat,
                "longitude": lon,
                "date_in": row.DateIn,
                "date_retrofit": row.DateRetrofit,
                "date_out": row.DateOut,
                "source_record_classification": "SOURCE_VERIFIED",
                "plant_attribute_classification": "PUBLIC_DATA_ESTIMATE",
                "operational_status_classification": "UNRESOLVED",
                "date_reliability": "UNRESOLVED_SOURCE_YEAR_FIELD_NOT_OPERATIONAL_STATUS",
                "identity_status": row.identity_status,
                "identity_group": row.identity_group,
                "evidence_and_reason": row.identity_reason,
                "row_source_file": "pypsa-earth/resources/MN/powerplants.csv",
                "row_project_id_count": sum(len(values) for values in project_ids.values()),
                "project_id_namespace_count": len(project_ids),
                "maximum_ids_within_one_namespace": max(
                    (len(values) for values in project_ids.values()), default=0
                ),
                "capacity_aggregation_allowed": row.identity_status
                == "DISTINCT_CONFIRMED",
                **osm_result,
            }
        )

        buses = network.buses.copy()
        buses["distance_km"] = buses.apply(
            lambda bus: haversine_km(lon, lat, float(bus.x), float(bus.y)), axis=1
        )
        buses["bus_id"] = buses.index.astype(str)
        buses["component_id"] = buses.bus_id.map(components)
        buses["in_main_component"] = buses.bus_id.isin(main_set)
        all_nearest_main = buses.loc[buses.in_main_component].sort_values(
            "distance_km"
        )
        candidates = []
        for rank, candidate in enumerate(all_nearest_main.head(5).itertuples(), start=1):
            candidates.append((candidate, rank, None))
        main_bus_frame = buses.loc[buses.in_main_component]
        voltage_groups = main_bus_frame.groupby("v_nom").indices
        for positions in voltage_groups.values():
            group = main_bus_frame.iloc[positions]
            for rank, candidate in enumerate(
                group.sort_values("distance_km").head(3).itertuples(), start=1
            ):
                candidates.append((candidate, None, rank))
        seen = set()
        for candidate, overall_rank, voltage_rank in candidates:
            key = candidate.bus_id
            if key in seen:
                continue
            seen.add(key)
            row_status = (
                "SPATIAL_CANDIDATE"
                if candidate.distance_km <= 25
                else "UNRESOLVED"
            )
            bus_rows.append(
                {
                    "record_id": row.record_id,
                    "plant_name": row.Name,
                    "identity_status": row.identity_status,
                    "candidate_rank_all_voltages": overall_rank,
                    "candidate_rank_within_voltage": voltage_rank,
                    "candidate_bus": candidate.bus_id,
                    "candidate_voltage_kv": float(candidate.v_nom),
                    "distance_km": float(candidate.distance_km),
                    "component_id": int(candidate.component_id),
                    "in_166_bus_main_component": True,
                    "osm_electrical_connection_evidence": False,
                    "connection_status": row_status,
                    "connection_assumption_required": True,
                    "selection_note": (
                        "Nearest geographic candidates across or within each voltage level. "
                        "No source numeric bus mapping was used; OSM proximity is not proof "
                        "of an electrical tie."
                    ),
                }
            )
    return pd.DataFrame(identity_rows), pd.DataFrame(bus_rows)


def audit_tables(paths):
    """Read pinned evidence; never write into the original data project."""
    base_config, _ = base_settings(paths)
    network = pypsa.Network(logical_input(paths, base_config["input_model"]))
    plants = pd.read_csv(logical_input(paths, "pypsa-earth/resources/MN/powerplants.csv"), keep_default_na=True)
    if len(plants) != 27:
        raise ValueError(f"Expected the audited 27 plant records, found {len(plants)}")
    return build_audit(plants, network, base_config, paths=paths)


def _generate(paths, output):
    identity, buses = audit_tables(paths)
    identity.to_csv(output / "plant_identity_audit.csv", index=False)
    buses.to_csv(output / "plant_bus_candidates.csv", index=False)
    return {"classification": "PUBLIC_RECORD_AND_SPATIAL_CANDIDATE_AUDIT",
            "plant_rows": len(identity), "record_ids": identity.record_id.tolist(),
            "identity_status_counts": identity.identity_status.value_counts().to_dict(),
            "bus_candidate_rows": len(buses),
            "electrical_connections_found": int(buses.osm_electrical_connection_evidence.sum())}


EXPECTED_FILES = ("plant_identity_audit.csv", "plant_bus_candidates.csv")


def analyze_for_verification(paths, destination):
    return analysis_bundle(paths, destination, EXPECTED_FILES, lambda output: _generate(paths, output))
