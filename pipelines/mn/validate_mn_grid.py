#!/usr/bin/env python3
"""Validate the Mongolia PyPSA-Earth network and plant source data."""

from __future__ import annotations

import json
import math
import re
import warnings
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd
import pypsa
from scipy.sparse.linalg import MatrixRankWarning
from shapely.geometry import Point, shape
from shapely.ops import unary_union

from .artifacts import new_artifact_directory
from .paths import Paths
from .inventory import sha256


EARTH_RADIUS_KM = 6371.0088
EXPECTED_FUELS = {
    "Bioenergy",
    "Geothermal",
    "Hard Coal",
    "Hydro",
    "Lignite",
    "Natural Gas",
    "Nuclear",
    "Oil",
    "Other",
    "Solar",
    "Waste",
    "Wind",
}
PLANT_COLUMNS = [
    "plant_row",
    "Name",
    "Fueltype",
    "Technology",
    "Capacity",
    "DateIn",
    "DateOut",
    "lat",
    "lon",
    "bus",
    "coordinate_in_mongolia",
    "capacity_status",
    "fuel_status",
    "technology_status",
    "duplicate_candidate",
    "osm_nearest_distance_km",
    "osm_within_5km",
    "osm_within_25km",
    "osm_nearby_source_types",
    "validation_findings",
]
BUS_MAPPING_COLUMNS = [
    "plant_row",
    "Name",
    "plant_lat",
    "plant_lon",
    "assigned_bus",
    "assigned_bus_exists",
    "assigned_bus_lat",
    "assigned_bus_lon",
    "assigned_bus_v_nom_kv",
    "assigned_bus_distance_km",
    "nearest_bus",
    "nearest_bus_distance_km",
    "assigned_bus_is_nearest",
    "distance_gap_to_nearest_km",
    "line_only_component",
    "line_transformer_component",
    "mapping_status",
]


def _normalise_name(value: Any) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip().casefold())


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance between two longitude/latitude points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(
        d_lambda / 2
    ) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def _country_geometry(path: Path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    geometries = [
        shape(feature["geometry"])
        for feature in raw.get("features", [])
        if feature.get("geometry")
    ]
    if not geometries:
        raise ValueError(f"No country polygons found in {path}")
    return unary_union(geometries)


def _osm_points(osm: pd.DataFrame) -> list[dict[str, Any]]:
    points = []
    for row in osm.to_dict("records"):
        match = re.search(
            r"POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)",
            str(row.get("geometry", "")),
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        lon, lat = float(match.group(1)), float(match.group(2))
        points.append(
            {
                "lon": lon,
                "lat": lat,
                "source": row.get("tags.generator:source"),
                "output_mw": _number(row.get("power_output_MW")),
            }
        )
    return points


def _component_map(
    buses: list[str],
    lines: pd.DataFrame,
    transformers: pd.DataFrame | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    graph = nx.Graph()
    graph.add_nodes_from(buses)
    for frame in (lines, transformers):
        if frame is None:
            continue
        active = frame
        if "active" in frame.columns:
            active = frame[frame["active"].fillna(False).astype(bool)]
        for bus0, bus1 in active[["bus0", "bus1"]].itertuples(index=False, name=None):
            graph.add_edge(str(bus0), str(bus1))
    components = sorted(
        (sorted(component) for component in nx.connected_components(graph)),
        key=lambda component: (-len(component), component[0] if component else ""),
    )
    detail = []
    mapping: dict[str, int] = {}
    for component_id, component in enumerate(components, start=1):
        for bus in component:
            mapping[bus] = component_id
        detail.append(
            {
                "component_id": component_id,
                "bus_count": len(component),
                "isolated_bus_count": sum(graph.degree(bus) == 0 for bus in component),
                "buses": ";".join(component),
            }
        )
    return detail, mapping


def _duplicate_candidates(plants: pd.DataFrame) -> tuple[pd.DataFrame, dict[int, str]]:
    groups: dict[str, list[int]] = {}
    for index, row in plants.iterrows():
        name = _normalise_name(row.get("Name"))
        if name:
            groups.setdefault(name, []).append(index)

    rows = []
    labels: dict[int, str] = {}
    repeated_names = [item for item in groups.items() if len(item[1]) > 1]
    for number, (_, indices) in enumerate(repeated_names, start=1):
        members = plants.loc[indices]
        coordinates = []
        for row in members.to_dict("records"):
            lat, lon = _number(row.get("lat")), _number(row.get("lon"))
            if lat is not None and lon is not None:
                coordinates.append((round(lat, 5), round(lon, 5)))
        coordinate_counts = Counter(coordinates)
        has_same_coordinate = any(count > 1 for count in coordinate_counts.values())
        candidate_type = (
            "same_name_and_coordinates"
            if has_same_coordinate
            else "same_name_different_coordinates"
        )
        group_id = f"dup-{number:03d}"
        evidence = "동일 정규화 이름 레코드 반복; " + (
            "동일 좌표도 반복됨"
            if has_same_coordinate
            else "좌표가 달라 동명이 발전소/잘못된 위치 여부 확인 필요"
        )
        evidence += "; 용량·운전연도·projectID를 보존하여 중복/복수 유닛 여부 확인 필요"
        rows.append(
            {
                "candidate_group": group_id,
                "candidate_type": candidate_type,
                "name": members.iloc[0].get("Name"),
                "record_count": len(indices),
                "row_indices_zero_based": ";".join(map(str, indices)),
                "coordinates": ";".join(
                    f"{lat},{lon}" for lat, lon in sorted(set(coordinates))
                ),
                "capacities_mw": ";".join(
                    "" if pd.isna(value) else str(value)
                    for value in members.get("Capacity", pd.Series(dtype=object))
                ),
                "commissioning_years": ";".join(
                    "" if pd.isna(value) else str(value)
                    for value in members.get("DateIn", pd.Series(dtype=object))
                ),
                "project_ids": ";".join(
                    sorted(
                        {
                            str(value)
                            for value in members.get(
                                "projectID", pd.Series(dtype=object)
                            )
                            if not pd.isna(value)
                        }
                    )
                ),
                "assessment": "검증 필요",
                "evidence": evidence,
            }
        )
        for index in indices:
            labels[index] = group_id
    return pd.DataFrame(rows), labels


def _write_csv(frame: pd.DataFrame, path: Path, columns: list[str] | None = None) -> None:
    if columns is not None:
        frame = frame.reindex(columns=columns)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _probe_empty_network_pf(network: pypsa.Network) -> str:
    if not network.generators.empty or not network.loads.empty:
        return "자동 실행 생략: 발전기 또는 부하가 있어 운영점 가정 없이 PF를 실행하지 않음"
    if len(network.snapshots) == 0:
        return "자동 실행 생략: snapshot 없음"

    probe_network = network.copy(snapshots=[network.snapshots[0]])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", MatrixRankWarning)
        try:
            result = probe_network.pf()
        except KeyError as error:
            if error.args != (None,):
                raise
            singular = any(isinstance(item.message, MatrixRankWarning) for item in caught)
            return (
                "단일 snapshot에서 MatrixRankWarning(특이 행렬)과 KeyError(None)로 종료"
                if singular
                else "단일 snapshot에서 KeyError(None)로 종료"
            )

    converged = result.get("converged")
    if converged is not None and bool(converged.to_numpy().all()):
        return "단일 snapshot PF가 수렴함 (운영 타당성은 별도 검증 필요)"
    return "단일 snapshot PF 결과가 비수렴 또는 불완전"


VALIDATION_FILES = ("network_components.csv", "suspected_duplicates.csv",
                    "plant_validation.csv", "bus_mapping_validation.csv", "validation_report.md")


def validate(paths: Paths, report_dir: Path | None = None) -> dict[str, Any]:
    """Inspect source data without PF; publish only a complete new report bundle."""
    earth = paths.earth_checkout
    inputs = [earth / relative for relative in (
        "networks/MN/base.nc", "resources/MN/powerplants.csv",
        "resources/MN/powerplants_osm2pm.csv", "resources/MN/osm/clean/all_clean_generators.csv",
        "resources/MN/shapes/country_shapes.geojson")]
    missing = [str(p) for p in inputs if not p.is_file()]
    if missing:
        raise FileNotFoundError("Required validation inputs missing: " + ", ".join(missing))
    before = {p: sha256(p) for p in inputs}
    destination = report_dir if report_dir is not None else paths.output_dir / "grid_validation"
    with new_artifact_directory(paths, destination, expected_files=VALIDATION_FILES) as staged:
        result = _validate(paths, staged)
        for name in VALIDATION_FILES:
            if name.endswith(".csv"):
                pd.read_csv(staged / name)
        if before != {p: sha256(p) for p in inputs}:
            raise ValueError("Validation inputs changed during preparation")
    result["output_dir"] = paths.safe_destination(destination)
    return result


def _validate(paths: Paths, output_dir: Path) -> dict[str, Any]:
    earth = paths.earth_checkout
    network_path = earth / "networks/MN/base.nc"
    plants_path = earth / "resources/MN/powerplants.csv"
    osm_pm_path = earth / "resources/MN/powerplants_osm2pm.csv"
    osm_clean_path = earth / "resources/MN/osm/clean/all_clean_generators.csv"
    country_shape_path = earth / "resources/MN/shapes/country_shapes.geojson"

    network = pypsa.Network(network_path)
    pf_probe_status = "NOT_EXECUTED_STAGE2B_NO_PF"
    plants = _read_csv(plants_path)
    osm_pm = _read_csv(osm_pm_path)
    osm_clean = _read_csv(osm_clean_path)
    country = _country_geometry(country_shape_path)
    osm_points = _osm_points(osm_clean)

    buses = network.buses.copy()
    bus_ids = [str(bus) for bus in buses.index]
    bus_lookup = {str(bus): row for bus, row in buses.iterrows()}
    bus_coordinates: dict[str, tuple[float, float]] = {}
    for bus_id, row in bus_lookup.items():
        lat = _number(row.get("lat", row.get("y")))
        lon = _number(row.get("lon", row.get("x")))
        if lat is not None and lon is not None:
            bus_coordinates[bus_id] = (lat, lon)

    line_components, line_component_map = _component_map(
        bus_ids, network.lines, None
    )
    combined_components, combined_component_map = _component_map(
        bus_ids, network.lines, network.transformers
    )
    component_rows = [
        {
            "topology": topology,
            **component,
        }
        for topology, details in (
            ("line_only", line_components),
            ("line_and_transformer", combined_components),
        )
        for component in details
    ]
    _write_csv(
        pd.DataFrame(component_rows),
        output_dir / "network_components.csv",
        ["topology", "component_id", "bus_count", "isolated_bus_count", "buses"],
    )

    duplicate_frame, duplicate_map = _duplicate_candidates(plants)
    _write_csv(
        duplicate_frame,
        output_dir / "suspected_duplicates.csv",
        [
            "candidate_group",
            "candidate_type",
            "name",
            "record_count",
            "row_indices_zero_based",
            "coordinates",
            "capacities_mw",
            "commissioning_years",
            "project_ids",
            "assessment",
            "evidence",
        ],
    )

    plant_rows = []
    bus_rows = []
    for index, row in plants.iterrows():
        lat, lon = _number(row.get("lat")), _number(row.get("lon"))
        bus_raw = row.get("bus")
        assigned_bus = "" if pd.isna(bus_raw) else str(bus_raw).strip()
        exists = assigned_bus in bus_lookup
        capacity = _number(row.get("Capacity"))
        fuel = "" if pd.isna(row.get("Fueltype")) else str(row.get("Fueltype"))
        technology = (
            "" if pd.isna(row.get("Technology")) else str(row.get("Technology"))
        )
        findings = []
        if lat is None or lon is None:
            in_country: str | bool = "unknown"
            findings.append("좌표 결측 또는 비수치")
        else:
            in_country = country.covers(Point(lon, lat))
            if not in_country:
                findings.append("몽골 국경 폴리곤 밖")
        capacity_status = (
            "missing"
            if capacity is None
            else "nonpositive"
            if capacity <= 0
            else "valid_positive"
        )
        if capacity_status != "valid_positive":
            findings.append(f"설비용량 {capacity_status}")
        fuel_status = "known_category" if fuel in EXPECTED_FUELS else "review"
        if fuel_status == "review":
            findings.append("Fueltype 미분류 또는 결측")
        technology_status = "present" if technology else "missing"
        if technology_status == "missing":
            findings.append("Technology 결측")
        if not exists:
            findings.append("지정 bus가 base.nc에 없음")
        duplicate_group = duplicate_map.get(index, "")
        if duplicate_group:
            findings.append(f"{duplicate_group}: 발전 유닛/중복 검증 필요")

        plant_osm_distances = []
        osm_sources: Counter[str] = Counter()
        if lat is not None and lon is not None:
            for point in osm_points:
                distance = haversine_km(lat, lon, point["lat"], point["lon"])
                plant_osm_distances.append((distance, point))
                if distance <= 25:
                    source = str(point["source"] or "unknown")
                    osm_sources[source] += 1
        plant_osm_distances.sort(key=lambda item: item[0])
        near_5 = sum(distance <= 5 for distance, _ in plant_osm_distances)
        near_25 = sum(distance <= 25 for distance, _ in plant_osm_distances)
        nearest_osm = (
            plant_osm_distances[0][0] if plant_osm_distances else None
        )
        osm_types = ";".join(f"{key}:{value}" for key, value in sorted(osm_sources.items()))

        plant_rows.append(
            {
                "plant_row": index,
                "Name": row.get("Name"),
                "Fueltype": row.get("Fueltype"),
                "Technology": row.get("Technology"),
                "Capacity": row.get("Capacity"),
                "DateIn": row.get("DateIn"),
                "DateOut": row.get("DateOut"),
                "lat": row.get("lat"),
                "lon": row.get("lon"),
                "bus": row.get("bus"),
                "coordinate_in_mongolia": in_country,
                "capacity_status": capacity_status,
                "fuel_status": fuel_status,
                "technology_status": technology_status,
                "duplicate_candidate": duplicate_group,
                "osm_nearest_distance_km": nearest_osm,
                "osm_within_5km": near_5,
                "osm_within_25km": near_25,
                "osm_nearby_source_types": osm_types,
                "validation_findings": "; ".join(findings),
            }
        )

        assigned_distance = None
        assigned_lat = assigned_lon = assigned_voltage = None
        line_comp = combined_comp = None
        if exists:
            bus_row = bus_lookup[assigned_bus]
            assigned_lat = _number(bus_row.get("lat", bus_row.get("y")))
            assigned_lon = _number(bus_row.get("lon", bus_row.get("x")))
            assigned_voltage = _number(bus_row.get("v_nom"))
            line_comp = line_component_map.get(assigned_bus)
            combined_comp = combined_component_map.get(assigned_bus)
            if None not in (lat, lon, assigned_lat, assigned_lon):
                assigned_distance = haversine_km(
                    lat, lon, assigned_lat, assigned_lon
                )

        nearest_bus = None
        nearest_distance = None
        if lat is not None and lon is not None and bus_coordinates:
            nearest_bus, (nearest_lat, nearest_lon) = min(
                bus_coordinates.items(),
                key=lambda item: haversine_km(lat, lon, item[1][0], item[1][1]),
            )
            nearest_distance = haversine_km(lat, lon, nearest_lat, nearest_lon)
        is_nearest = (
            exists
            and assigned_distance is not None
            and nearest_distance is not None
            and abs(assigned_distance - nearest_distance) < 1e-6
        )
        mapping_status = (
            "missing_bus_reference"
            if not exists
            else "missing_coordinate"
            if assigned_distance is None or nearest_distance is None
            else "closer_bus_exists"
            if not is_nearest
            else "assigned_bus_is_nearest"
        )
        bus_rows.append(
            {
                "plant_row": index,
                "Name": row.get("Name"),
                "plant_lat": lat,
                "plant_lon": lon,
                "assigned_bus": assigned_bus,
                "assigned_bus_exists": exists,
                "assigned_bus_lat": assigned_lat,
                "assigned_bus_lon": assigned_lon,
                "assigned_bus_v_nom_kv": assigned_voltage,
                "assigned_bus_distance_km": assigned_distance,
                "nearest_bus": nearest_bus,
                "nearest_bus_distance_km": nearest_distance,
                "assigned_bus_is_nearest": is_nearest,
                "distance_gap_to_nearest_km": (
                    assigned_distance - nearest_distance
                    if assigned_distance is not None and nearest_distance is not None
                    else None
                ),
                "line_only_component": line_comp,
                "line_transformer_component": combined_comp,
                "mapping_status": mapping_status,
            }
        )

    plant_validation = pd.DataFrame(plant_rows)
    bus_validation = pd.DataFrame(bus_rows)
    _write_csv(plant_validation, output_dir / "plant_validation.csv", PLANT_COLUMNS)
    _write_csv(
        bus_validation,
        output_dir / "bus_mapping_validation.csv",
        BUS_MAPPING_COLUMNS,
    )

    line_endpoint_errors = [
        str(name)
        for name, row in network.lines.iterrows()
        if str(row["bus0"]) not in bus_lookup or str(row["bus1"]) not in bus_lookup
    ]
    transformer_endpoint_errors = [
        str(name)
        for name, row in network.transformers.iterrows()
        if str(row["bus0"]) not in bus_lookup or str(row["bus1"]) not in bus_lookup
    ]
    line_parameter_stats = {}
    for column in ("r", "x", "b", "s_nom", "length"):
        values = pd.to_numeric(network.lines[column], errors="coerce")
        line_parameter_stats[column] = {
            "missing": int(values.isna().sum()),
            "zero": int(values.eq(0).sum()),
            "negative": int(values.lt(0).sum()),
            "min": _number(values.min()),
            "median": _number(values.median()),
            "max": _number(values.max()),
        }
    transformer_parameter_stats = {}
    for column in ("r", "x", "b", "s_nom"):
        values = pd.to_numeric(network.transformers[column], errors="coerce")
        transformer_parameter_stats[column] = {
            "missing": int(values.isna().sum()),
            "zero": int(values.eq(0).sum()),
            "negative": int(values.lt(0).sum()),
            "min": _number(values.min()),
            "median": _number(values.median()),
            "max": _number(values.max()),
        }
    line_type_catalogue = set(map(str, network.line_types.index))
    line_types_used = network.lines["type"].fillna("").astype(str)
    line_type_matches = int(line_types_used.isin(line_type_catalogue).sum())
    line_type_r_missing = int(network.line_types["r_per_length"].isna().sum())
    line_type_x_missing = int(network.line_types["x_per_length"].isna().sum())

    distance_valid = bus_validation["assigned_bus_distance_km"].dropna()
    distant = bus_validation.sort_values(
        "assigned_bus_distance_km", ascending=False
    ).head(8)
    closer = bus_validation[
        bus_validation["mapping_status"].eq("closer_bus_exists")
    ].sort_values("distance_gap_to_nearest_km", ascending=False)
    voltage_counts = buses["v_nom"].value_counts(dropna=False).sort_index()
    isolated_line = sum(item["isolated_bus_count"] for item in line_components)
    isolated_combined = sum(
        item["isolated_bus_count"] for item in combined_components
    )
    duplicate_count = len(duplicate_frame)
    same_coordinate_duplicate_count = int(
        duplicate_frame.get("candidate_type", pd.Series(dtype=str))
        .eq("same_name_and_coordinates")
        .sum()
    )
    different_coordinate_name_count = int(
        duplicate_frame.get("candidate_type", pd.Series(dtype=str))
        .eq("same_name_different_coordinates")
        .sum()
    )
    invalid_bus_refs = int((~bus_validation["assigned_bus_exists"]).sum())
    in_country_count = int(
        plant_validation["coordinate_in_mongolia"].map(
            lambda value: value is True or value == True
        ).sum()
    )
    fuel_counts = plants["Fueltype"].fillna("(결측)").value_counts().to_dict()
    capacity_sum = pd.to_numeric(plants["Capacity"], errors="coerce").sum()
    capacity_missing = int(pd.to_numeric(plants["Capacity"], errors="coerce").isna().sum())
    current_year = datetime.now().year
    date_out = pd.to_numeric(plants.get("DateOut"), errors="coerce")
    dateout_at_or_before_current_year = int(date_out.le(current_year).sum())
    technology_missing = int(
        plants.get("Technology", pd.Series(dtype=object)).isna().sum()
    )
    technology_missing_by_fuel = (
        plants.loc[plants["Technology"].isna(), "Fueltype"]
        .fillna("(결측)")
        .value_counts()
        .to_dict()
    )
    osm_plant_rows_nearby = int(plant_validation["osm_within_25km"].gt(0).sum())
    mapped_plant_components = (
        bus_validation["line_transformer_component"]
        .value_counts(dropna=False)
        .sort_index()
        .to_dict()
    )
    osm_source_counts = (
        osm_clean["tags.generator:source"].fillna("(결측)").value_counts().to_dict()
        if "tags.generator:source" in osm_clean
        else {}
    )
    osm_output = (
        pd.to_numeric(osm_clean.get("power_output_MW"), errors="coerce")
        if "power_output_MW" in osm_clean
        else pd.Series(dtype=float)
    )

    report = f"""# 몽골 EMS PoC 데이터 검증 보고서

> 생성: 재실행 시각 기준. 입력 원본은 읽기 전용으로 사용했으며 `base.nc`와 CSV를 변경하지 않았습니다.
> 이는 공개·가공 데이터의 정합성 점검이며, 실제 운영계통 검증이나 운전가능성 판정이 아닙니다.

## 요약

| 항목 | 관측값 |
|---|---:|
| 네트워크 | pypsa-earth/networks/MN/base.nc |
| 모선 / 선로 / 변압기 | {len(network.buses)} / {len(network.lines)} / {len(network.transformers)} |
| 발전기 / 부하 | {len(network.generators)} / {len(network.loads)} |
| snapshot 기간 | {len(network.snapshots)}개 ({network.snapshots[0]} – {network.snapshots[-1]}) |
| 모선 전압 | {', '.join(f'{value:g} kV: {int(count)}' for value, count in voltage_counts.items())} |
| 발전소 CSV 행 수 | {len(plants)} (고유 발전소 수라고 간주하지 않음) |
| CSV 설비용량 합계 | {capacity_sum:,.1f} MW (이 CSV의 합계이며 국가 총량 아님) |
| OSM 정제 발전기 객체 | {len(osm_clean)}; `power_output_MW`가 있는 행 {int(osm_output.notna().sum())}, 합계 {osm_output.sum():,.1f} MW (개별 객체 태그 합계) |
| OSM→PPM 중간 파일 | {len(osm_pm)} 행; 파일 크기 {osm_pm_path.stat().st_size if osm_pm_path.exists() else 0} byte |

## 정상적으로 확인된 사항

- 발전소 좌표의 몽골 국경 포함 여부를 국가 경계 GeoJSON 기준으로 판정했습니다. 경계 안쪽 행은 {in_country_count}/{len(plants)}입니다. 용량 결측 {capacity_missing}행, 연료 미분류 {len(plants) - int(plant_validation["fuel_status"].eq("known_category").sum())}행, 기술 분류 결측 {technology_missing}행입니다.
- 네트워크 모선에는 위치와 전압 속성이 있고, 주어진 CSV의 bus 매핑은 {len(bus_validation) - invalid_bus_refs}/{len(bus_validation)}개가 실제 모선 인덱스에 존재합니다.
- 선로·변압기를 합친 연결성을 선로만의 연결성과 별도로 계산했습니다. 아래 결과는 이 파일에 담긴 그래프의 연결성일 뿐 운영상 동기화 여부를 보증하지 않습니다.
- 선로 종단 참조 오류: {len(line_endpoint_errors)}; 변압기 종단 참조 오류: {len(transformer_endpoint_errors)}.

## 등급별 발견 사항

### Critical — 현재 형태로는 AC PF 검증 불가

- `base.nc`에 발전기 0개, 부하 0개, 슬랙/외부계통 공급원이 없습니다. 모든 모선은 현재 PQ 제어입니다.
- 선로 `r`와 `x`가 각각 0인 행은 {line_parameter_stats['r']['zero']}/{len(network.lines)}, {line_parameter_stats['x']['zero']}/{len(network.lines)}입니다. AC 야코비안이 특이해질 수 있고 물리적인 전압강하·손실을 표현할 수 없습니다.
- 빈 발전원·부하 상태에서 단일 snapshot `n.pf()` 진단: {pf_probe_status}. 따라서 기본 파일에 PF를 바로 실행해도 의미 있는 해를 얻을 수 없습니다.

### Warning — 전기 파라미터 및 토폴로지

- 선로 `r/x/b` 결측 {line_parameter_stats['r']['missing']}/{line_parameter_stats['x']['missing']}/{line_parameter_stats['b']['missing']}, 0 값 {line_parameter_stats['r']['zero']}/{line_parameter_stats['x']['zero']}/{line_parameter_stats['b']['zero']}; `s_nom` 결측 {line_parameter_stats['s_nom']['missing']}, 비양수 {line_parameter_stats['s_nom']['negative'] + line_parameter_stats['s_nom']['zero']}; 길이 결측 {line_parameter_stats['length']['missing']}, 비양수 {line_parameter_stats['length']['negative'] + line_parameter_stats['length']['zero']}.
- 변압기 10대의 `r`는 {transformer_parameter_stats['r']['zero']}개가 0, `x`는 범위 {transformer_parameter_stats['x']['min']}–{transformer_parameter_stats['x']['max']} pu, `s_nom`은 {transformer_parameter_stats['s_nom']['min']}–{transformer_parameter_stats['s_nom']['max']} MVA입니다. `r=0`, `x=0.1 pu`, `s_nom=2000`이 실제 시험성적/정격인지 확인해야 합니다.
- 연결요소: 선로만 {len(line_components)}개 (크기 {', '.join(str(item['bus_count']) for item in line_components)}), 선로+변압기 {len(combined_components)}개 (크기 {', '.join(str(item['bus_count']) for item in combined_components)}). 고립 모선은 각각 {isolated_line}개, {isolated_combined}개입니다.
- 선로 type 카탈로그 {len(network.line_types)}개 중 실제 선로 type 참조 {line_type_matches}/{len(network.lines)}개가 매칭되고, 카탈로그 `r_per_length/x_per_length` 결측은 {line_type_r_missing}/{line_type_x_missing}개입니다. 다만 `base.nc`의 line별 `r/x`가 0인 사실은 바뀌지 않습니다. 카탈로그 값·도체 type·길이의 실제 적용 여부와 단위를 확인한 뒤 추정 정수로 재산출할 수 있지만, 그 자체가 운영자 실측값이라는 뜻은 아닙니다.
- 발전소 매핑 모선의 선로+변압기 연결요소별 행 수: {', '.join(f'요소 {component}: {count}행' for component, count in mapped_plant_components.items())}. 발전소가 복수 연결요소에 걸쳐 배치되어 있어 각 component의 전원·부하 balance와 동기/계통경계 확인이 필요합니다.
- 선로 `s_nom` 및 `s_max_pu`는 모델 속성으로 제공되지만, 운영자 정격/계절 한계인지 근거 자료가 필요합니다. 현재 선로 표의 `r/x/b` 값은 물리 파라미터로 사용할 수 없습니다.

### Warning — 발전소 자료 품질 및 매핑

- 동일 정규화 발전소명이 반복된 후보 그룹 {duplicate_count}개입니다. 이 중 동일 좌표 반복 그룹 {same_coordinate_duplicate_count}개, 서로 다른 좌표의 동명 후보는 {different_coordinate_name_count}개입니다. 행은 제거하거나 합치지 않았습니다. 상세 후보와 원본 row index는 `suspected_duplicates.csv`를 확인하십시오.
- 발전소 연료 분류: {', '.join(f'{key} {value}행' for key, value in fuel_counts.items())}. `Oil` {fuel_counts.get('Oil', 0)}행은 기술 분류가 비어 있어 열병합 여부와 실제 연료 확인이 필요합니다.
- 기술 분류 결측 {technology_missing}행 (연료별: {', '.join(f'{fuel} {count}행' for fuel, count in technology_missing_by_fuel.items())}); `Technology`가 비어 있는 태양광도 있어 기술 매핑을 원자료와 확인해야 합니다.
- 용량 결측 {capacity_missing}행; 양수 용량 이외 값 {int((pd.to_numeric(plants['Capacity'], errors='coerce').fillna(0) <= 0).sum())}행. 용량 합계는 파일 내 행의 단순 합계이며 중복·운영상태 검증 전 총 설비용량으로 쓰면 안 됩니다.
- `DateOut`이 현재 연도({current_year}) 이내인 행 {dateout_at_or_before_current_year}개입니다. 이 필드는 예상 종료연도일 수 있어 실제 폐지·운전 여부를 뜻하지 않으며 운영자 확인이 필요합니다.
- 지정 bus 미존재/미해석 {invalid_bus_refs}행. 지정 모선까지 거리의 min/median/mean/max는 {distance_valid.min():.1f}/{distance_valid.median():.1f}/{distance_valid.mean():.1f}/{distance_valid.max():.1f} km입니다.
- 버스가 존재하면서 가장 가까운 모선과 다른 배정 {len(closer)}행입니다. 최단거리 모선은 실제 전기적 접속점이라는 증거가 아니므로 매핑 오류를 확정하지 않습니다.

#### 먼 매핑 (상위 최대 8개)

| 발전소 행 | 발전소명 | 지정 버스 | 거리 km | 가장 가까운 버스 | 최단거리 km | 차이 km |
|---:|---|---|---:|---|---:|---:|
{chr(10).join(f"| {int(r.plant_row)} | {r.Name} | {r.assigned_bus} | {r.assigned_bus_distance_km:.1f} | {r.nearest_bus} | {r.nearest_bus_distance_km:.1f} | {r.distance_gap_to_nearest_km:.1f} |" for r in distant.itertuples())}

#### 지정 모선보다 더 가까운 모선이 있는 발전소

{chr(10).join(f"- 행 {int(r.plant_row)} `{r.Name}`: 지정 {r.assigned_bus}까지 {r.assigned_bus_distance_km:.1f} km, 최근접 {r.nearest_bus}까지 {r.nearest_bus_distance_km:.1f} km (차이 {r.distance_gap_to_nearest_km:.1f} km). 접속 관계는 운영자 자료 확인 필요." for r in closer.itertuples()) if len(closer) else "- 해당 없음."}

### Info — OSM / Powerplantmatching 비교

- `powerplants.csv`는 PyPSA-Earth의 `build_powerplants` 작업 결과입니다. 작업 문서에 따르면 기본 Powerplantmatching 데이터에 커스텀 입력을 merge/replace할 수 있고, OSM 입력도 PPM 형식으로 변환될 수 있습니다. 이 checkout의 `powerplants_osm2pm.csv`는 {osm_pm_path.stat().st_size if osm_pm_path.exists() else 0} byte로 비어 있어, 현재 27행에 대해 OSM 변환 중간산출물 기여를 확인할 수 없습니다.
- `all_clean_generators.csv`는 OSM 발전기 객체의 정제 결과로 {len(osm_clean)}개 node 객체가 있습니다. OSM source 태그 분포: {', '.join(f'{key} {value}' for key, value in osm_source_counts.items())}. 이 객체 수는 발전소 수와 같지 않으며, 여러 풍력 터빈이 한 풍력단지를 나타낼 수 있습니다.
- 정제 CSV의 `power_output_MW`는 OSM generator tag에서 옮긴 값이고, clean-up/매핑 로직에서 파생될 수도 있습니다. 해당 열의 합계 {osm_output.sum():.1f} MW를 국가 설비용량으로 해석하지 않습니다.
- 25 km 안에 OSM 발전기 객체가 하나 이상 있는 발전소 행은 {osm_plant_rows_nearby}/{len(plants)}개입니다. 나머지 행은 OSM 누락 후보일 수 있지만, OSM의 불완전성 때문에 실제 발전소 누락이라고 단정할 수 없습니다. 근접 객체도 발전소와의 동일성을 확정하지 않습니다.
- 이번 로컬 입력만으로는 특정 기준연도 공식 발전소 명부와 대조하지 않았습니다. 몽골 에너지부/규제위원회/국가급 전력사업자(Dispatching Center 등)의 기준연도별 발전소·호기·정격 자료를 확보한 뒤 누락·폐지 후보를 비교해야 합니다. 외부 공식 수치가 검증되지 않은 상태에서 국가 총량을 추정하지 않았습니다.

## AC Power Flow 준비에 필요한 자료

1. **전기적 정수**: 선로별 양/음 sequence 또는 AC `r/x/b`와 단위·기준용량, 실제 길이/회선수/도체, 변압기 정격용량·단락전압·동손/무부하손·탭범위/현재 tap·위상변위. OSM 도형과 전압·회로 수는 실측 임피던스 대체가 아닙니다.
2. **운전점**: 동시점별 발전기 P/Q 또는 P와 제어모드, 가용용량·최소/최대 P/Q, 부하 P/Q, 부하/발전 시간대, 조류 방향 및 기준 시각.
3. **전압 및 기준기기**: 운영자 지정 slack/reference bus, generator voltage setpoint, PV/PQ 버스 타입, 무효전력 한계, 변압기 제어 및 bus-to-bus 접속 검증.
4. **계통경계와 운전 상태**: 실제 투입/개방 상태, 계통분리 및 tie-line 교환, 계획정전·폐지/신설, 보호·운영 한계. 각 connected component에는 유효한 기준전압원과 유효전력 balance가 필요합니다.
5. **데이터 계보**: 운영자 승인/기준연도/단위/정격 정의 및 출처별 timestamp. 이 자료 없이 `base.nc`의 capacity와 자동 추정을 실제 한계처럼 쓰지 말 것.

## 공개 데이터 신뢰성 구분

| 구분 | 이번 분석에서 확인된 것 | 한계 |
|---|---|---|
| OpenStreetMap 직접 데이터 | 정제 발전기·선로·모선 입력 파일에 OSM ID/태그/기하가 포함됨 | 자발적 입력, 커버리지·정확도·운전상태 불확실 |
| Powerplantmatching 통합 정보 | 발전소 이름·연료·용량·연도·좌표·식별자·bus가 있는 CSV; build 단계는 PPM·커스텀 파일 병합을 지원 | 행별 출처/버전/갱신일이 불충분. 27행이 27개 실제 발전소라는 보장 없음 |
| PyPSA-Earth 자동 생성/추정 | bus 할당, 네트워크 단순화, 선로 정격 속성 및 정적 네트워크 | PF 필수 선로 `r/x`는 전부 0; 생성 모델 값을 현장 측정치로 취급 불가 |
| 계통운영자 자료 필요 | 발전기별 운전상태·P/Q·전압제어·슬랙, 부하, 계통전기정수·정격·스위칭 상태 | 본 입력에는 없으며 공식 운영계통 타당성 검증 불가 |

## 다음 작업 우선순위

1. **P0 — 운영자/공식 자료 요청**: 발전소와 호기 단위 명부·연료·정격·상태·좌표, 대표 스냅샷의 발전/부하 P/Q, 실제 모선/전압 및 slack 지정.
2. **P0 — 전기정수 보강**: 선로 임피던스와 변압기 nameplate 정수 확보. 자동 산출값은 출처/단위가 확인될 때까지 대체값으로만 분리.
3. **P1 — 발전소 중복 및 매핑 검토**: `suspected_duplicates.csv`의 동일 이름/좌표 그룹(특히 `Ulaanbaatar Tes Chp Mongolia`)을 원본 PPM 식별자/호기자료와 대조; 가까운 모선이 아니라 접속 도면/운영자 bus ID로 확정.
4. **P1 — 토폴로지 정합성**: 별도 connected component가 의도된 계통구역/분리망인지 확인하고, 각 component의 기준발전기와 tie-line 처리 정의.
5. **P2 — 모델 조립 및 비교**: 아래 권고안으로 검증자료 한 시점의 작은 case를 만들고, 독립 툴/운영자 기준 결과와 전압·선로 조류·손실을 비교한 뒤 기간별 모델로 확대.

## 한 달 PoC 구현방안 비교

| 기준 | 방안 A: PyPSA-Earth 확장 | 방안 B: PyPowSyBl 변환 |
|---|---|---|
| 데이터 요구 | 현재 PyPSA 모델에 발전기·부하·slack·운전점 및 올바른 AC 정수 보강 | A와 동일한 원천 자료에 더해 변환 매핑(버스/branch/trafo/generator/load/switch/control)과 단위·제어 모드 동등성 검증 |
| 구현 난이도 | 낮음~중간: 이미 PyPSA 네트워크/CSV/환경이 있음. 데이터가 확보되면 단계별 case 제작 용이 | 중간~높음: 모델 변환·스키마 매핑·제어 semantics와 결과 상호검증이 추가됨 |
| 주요 위험 | 잘못된/0 임피던스와 임의 slack·부하 분배로 수렴해도 물리적 의미가 없는 결과 | 변환 누락/단위 오류/버스 타입 차이, 두 엔진 결과 차이, 이번 PoC 범위 초과 |
| 한 달 PoC 실현성 | 데이터 접근이 되면 단일 snapshot 검증까지 가장 현실적. 자료 미확보 시에도 품질검증·민감도 case까지 완료 가능 | 기존 AI-EMS가 PyPowSyBl을 사용해 장기 통합에는 매력 있으나, 완전한 PF 모델 변환과 검증까지 한 달은 데이터·테스트 범위상 위험 |

**추천:** 방안 A를 우선 선택해 PyPSA 안에서 데이터 계약과 AC PF 기준 case를 검증하십시오. 단, 임의의 발전량/부하/슬랙을 실측인 것처럼 채우지 말고 운영자 입력을 기다리거나 명시적인 synthetic scenario로 별도 라벨링해야 합니다. 이후 검증된 PyPSA snapshot을 교차검증 기준으로 삼아 방안 B 변환을 진행하십시오. AI-EMS 연동 우선순위가 높더라도 변환은 검증된 모델 이후의 후속 단계가 안전합니다.

## 생성 파일

- `plant_validation.csv`
- `suspected_duplicates.csv`
- `bus_mapping_validation.csv`
- `network_components.csv`
"""
    (output_dir / "validation_report.md").write_text(report, encoding="utf-8")
    return {
        "output_dir": output_dir,
        "plant_rows": len(plants),
        "duplicate_groups": duplicate_count,
        "line_components": len(line_components),
        "combined_components": len(combined_components),
        "invalid_bus_refs": invalid_bus_refs,
        "line_parameter_stats": line_parameter_stats,
        "transformer_parameter_stats": transformer_parameter_stats,
    }
