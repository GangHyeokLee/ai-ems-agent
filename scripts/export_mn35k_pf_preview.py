#!/usr/bin/env python3
"""Export one validated, read-only MN_35k synthetic AC PF result as JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

SCENARIO_ID = "reduced_27_q_limited_with_reactor"
GRID_ID = "mn-osm-35k"
SCHEMA_VERSION = "1.0"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value in (0, 1):
        return bool(value)
    raise ValueError(f"{label} 값이 boolean이 아닙니다.")


def _number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 값이 숫자가 아닙니다.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 값이 유한하지 않습니다.")
    return number


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _unique_index(frame: pd.DataFrame, label: str) -> None:
    _check(frame.index.is_unique, f"{label} ID가 중복됩니다.")


def _network_ids(network: Any, component: str) -> dict[str, Any]:
    frame = getattr(network, component)
    return {str(identifier): row for identifier, row in frame.iterrows()}


def _indexed_map(
    records: list[dict[str, Any]], key: str, label: str
) -> dict[str, dict[str, Any]]:
    result = {}
    for record in records:
        identifier = str(record.get(key, "")).strip()
        if not identifier or identifier in result:
            raise ValueError(f"{label} ID가 비어 있거나 중복됩니다.")
        result[identifier] = record
    return result


def _validate_map_against_network(
    map_data: dict[str, Any],
    network: Any,
    label: str,
    *,
    coordinate_tolerance: float = 1e-6,
) -> None:
    buses = _network_ids(network, "buses")
    lines = _network_ids(network, "lines")
    transformers = _network_ids(network, "transformers")
    map_buses = _indexed_map(map_data["buses"], "bus_id", "지도 모선")
    map_lines = _indexed_map(map_data["lines"], "line_id", "지도 선로")
    map_transformers = _indexed_map(
        map_data["transformers"], "transformer_id", "지도 변압기"
    )
    _check(set(buses) == set(map_buses), f"{label} 모선 ID가 지도와 다릅니다.")
    _check(set(lines) == set(map_lines), f"{label} 선로 ID가 지도와 다릅니다.")
    _check(set(transformers) == set(map_transformers), f"{label} 변압기 ID가 지도와 다릅니다.")

    for bus_id, bus in buses.items():
        mapped = map_buses[bus_id]
        _check(
            math.isclose(
                _number(bus.x, f"{label} 모선 {bus_id} 경도"),
                _number(mapped["longitude"], f"지도 모선 {bus_id} 경도"),
                rel_tol=0,
                abs_tol=coordinate_tolerance,
            )
            and math.isclose(
                _number(bus.y, f"{label} 모선 {bus_id} 위도"),
                _number(mapped["latitude"], f"지도 모선 {bus_id} 위도"),
                rel_tol=0,
                abs_tol=coordinate_tolerance,
            ),
            f"{label} 모선 {bus_id} 좌표가 지도와 다릅니다.",
        )
        _check(
            math.isclose(
                _number(bus.v_nom, f"{label} 모선 {bus_id} 전압"),
                _number(mapped["voltage_kv"], f"지도 모선 {bus_id} 전압"),
                rel_tol=0,
                abs_tol=1e-6,
            ),
            f"{label} 모선 {bus_id} 정격전압이 지도와 다릅니다.",
        )

    for line_id, line in lines.items():
        mapped = map_lines[line_id]
        _check(
            (str(line.bus0), str(line.bus1))
            == (str(mapped["bus1_id"]), str(mapped["bus2_id"])),
            f"{label} 선로 {line_id}의 양단 모선 방향이 지도와 다릅니다.",
        )
    for transformer_id, transformer in transformers.items():
        mapped = map_transformers[transformer_id]
        _check(
            (str(transformer.bus0), str(transformer.bus1))
            == (str(mapped["bus1_id"]), str(mapped["bus2_id"])),
            f"{label} 변압기 {transformer_id}의 양단 모선 방향이 지도와 다릅니다.",
        )


def build_preview(
    *,
    map_data: dict[str, Any],
    map_json_sha256: str,
    map_model: Any,
    poc_model: Any,
    pf_model: Any,
    scenario_comparison: pd.DataFrame,
    bus_voltage_results: pd.DataFrame,
    branch_loading_results: pd.DataFrame,
    generator_reactive_results: pd.DataFrame,
    shunt_reactor_results: pd.DataFrame,
    power_balance_validation: pd.DataFrame,
    nodal_balance_validation: pd.DataFrame,
    result_validation: dict[str, Any],
    source_preservation: dict[str, Any],
    acpf_config: dict[str, Any],
    synthetic_limits: dict[str, Any],
    source_paths: dict[str, str],
    source_hashes: dict[str, str],
) -> dict[str, Any]:
    """Build JSON from one exact scenario row and its matching stored result rows."""
    _validate_map_against_network(map_data, map_model, "지도 원본")
    _validate_map_against_network(map_data, poc_model, "MN_35k_poc")

    scenario_buses = _network_ids(pf_model, "buses")
    scenario_lines = _network_ids(pf_model, "lines")
    scenario_transformers = _network_ids(pf_model, "transformers")
    full_buses = _network_ids(poc_model, "buses")
    full_lines = _network_ids(poc_model, "lines")
    full_transformers = _network_ids(poc_model, "transformers")
    _check(len(scenario_buses) == 166, "PF 시나리오 모선 수가 166개가 아닙니다.")
    _check(len(scenario_lines) == 156, "PF 시나리오 선로 수가 156개가 아닙니다.")
    _check(len(scenario_transformers) == 27, "PF 시나리오 변압기 수가 27개가 아닙니다.")
    _check(set(scenario_buses).issubset(full_buses), "PF 모선이 전체 POC 모델에 없습니다.")
    _check(set(scenario_lines).issubset(full_lines), "PF 선로가 전체 POC 모델에 없습니다.")
    _check(set(scenario_transformers) == set(full_transformers), "PF 변압기 구성이 전체 POC 모델과 다릅니다.")
    excluded_buses = sorted(set(full_buses) - set(scenario_buses))
    excluded_lines = sorted(set(full_lines) - set(scenario_lines))
    _check(len(excluded_buses) == 7 and len(excluded_lines) == 3, "PF 제외 설비 수가 예상과 다릅니다.")
    _check(set(full_buses) == {str(row["bus_id"]) for row in map_data["buses"]}, "지도 전체 모선 coverage가 다릅니다.")
    _check(set(full_lines) == {str(row["line_id"]) for row in map_data["lines"]}, "지도 전체 선로 coverage가 다릅니다.")
    _check(set(full_transformers) == {str(row["transformer_id"]) for row in map_data["transformers"]}, "지도 전체 변압기 coverage가 다릅니다.")
    for component, records, map_key in (
        ("lines", full_lines, "line_id"),
        ("transformers", full_transformers, "transformer_id"),
    ):
        map_records = _indexed_map(map_data[component], map_key, f"지도 {component}")
        model_records = _network_ids(poc_model, component)
        for equipment_id, row in records.items():
            mapped = map_records[equipment_id]
            model = row
            _check(
                (str(model.bus0), str(model.bus1))
                == (str(mapped["bus1_id"]), str(mapped["bus2_id"])),
                f"전체 POC {component} {equipment_id}의 지도 연결이 다릅니다.",
            )

    target_rows = scenario_comparison.loc[
        scenario_comparison["scenario_id"].astype(str).eq(SCENARIO_ID)
    ]
    _check(len(target_rows) == 1, "기준 PF 시나리오가 정확히 한 행이어야 합니다.")
    scenario = target_rows.iloc[0]
    _check(_as_bool(scenario["converged"], "PF 수렴"), "기준 PF 시나리오가 수렴하지 않았습니다.")
    _check(scenario["pf_status"] == "AC_PF_CONVERGED", "기준 PF 상태가 수렴이 아닙니다.")
    _check(
        scenario["operating_screen_status"] == "SYNTHETIC_SCENARIO_WITHIN_SCREENING_LIMITS",
        "기준 시나리오가 스크리닝을 통과하지 않았습니다.",
    )
    _check(_number(scenario["q_violation_count"], "발전기 Q 위반") == 0, "기준 시나리오에 발전기 Q 위반이 있습니다.")
    _check(int(scenario["generator_count"]) == 45, "합성 발전기 수가 예상과 다릅니다.")
    _check(int(scenario["shunt_count"]) == 1, "합성 리액터 수가 예상과 다릅니다.")
    _check(_number(scenario["shunt_nominal_mvar"], "리액터 정격") == 20, "합성 리액터 정격이 20 MVAr가 아닙니다.")
    _check(int(scenario["pv_bus_count"]) == 43 and int(scenario["pq_bus_count"]) == 122 and int(scenario["slack_bus_count"]) == 1, "버스 제어 구성이 예상과 다릅니다.")
    _check(_number(scenario["load_mw"], "부하 MW") == 25.6, "합성 부하 P가 예상과 다릅니다.")
    _check(_number(scenario["load_mvar"], "합성 부하 MVAr") > 0, "합성 부하 Q가 유효하지 않습니다.")

    screening = acpf_config["screening"]
    v_min_limit = _number(screening["v_min_pu"], "전압 하한")
    v_max_limit = _number(screening["v_max_pu"], "전압 상한")
    _check(result_validation.get("finite_results") is True, "원본 검증에서 유한 결과가 확인되지 않았습니다.")
    for key in ("controls_verified", "loads_verified", "balance_verified", "power_balance_verified", "nodal_balance_verified", "operating_constraints_satisfied"):
        _check(result_validation.get(key) is True, f"원본 검증의 {key}가 통과하지 않았습니다.")
    _check(result_validation.get("screening_status") == scenario["operating_screen_status"], "원본 validation과 scenario screening 결과가 다릅니다.")
    _check(result_validation.get("model_reliability_status") == "MODEL_DATA_REQUIRES_REVIEW", "모델 신뢰성 경고가 예상과 다릅니다.")
    violations = result_validation.get("violations")
    expected_violations = {
        "bus_voltage": 0,
        "line_thermal": 0,
        "transformer_thermal": 0,
        "generator_p": 0,
        "generator_q": 0,
        "generator_s": 0,
    }
    _check(violations == expected_violations, "원본 validation에 스크리닝 위반이 있습니다.")
    _check(source_preservation.get("unchanged") is True, "원본 source preservation 검증이 통과하지 않았습니다.")

    def select_scenario(frame: pd.DataFrame, label: str) -> pd.DataFrame:
        _check("scenario_id" in frame, f"{label}에 scenario_id가 없습니다.")
        result = frame.loc[frame["scenario_id"].astype(str).eq(SCENARIO_ID)].copy()
        _check(not result.empty, f"{label}에 기준 시나리오 결과가 없습니다.")
        return result

    buses_df = select_scenario(bus_voltage_results, "모선 전압 결과")
    _check(buses_df["bus"].astype(str).is_unique, "모선 전압 결과에 중복 모선이 있습니다.")
    _check(set(buses_df["bus"].astype(str)) == set(scenario_buses), "모선 전압 결과와 NetCDF 모선 ID가 다릅니다.")
    map_bus_by_id = {str(row["bus_id"]): row for row in map_data["buses"]}
    buses = []
    for record in buses_df.to_dict("records"):
        bus_id = str(record["bus"])
        model_bus = scenario_buses[bus_id]
        voltage_kv = _number(record["voltage_kv"], f"모선 {bus_id} kV")
        _check(math.isclose(voltage_kv, _number(model_bus.v_nom, "NetCDF 모선 kV"), abs_tol=1e-6), f"모선 {bus_id} 정격전압 불일치")
        _check(math.isclose(voltage_kv, _number(map_bus_by_id[bus_id]["voltage_kv"], "지도 모선 kV"), abs_tol=1e-6), f"모선 {bus_id} 지도 정격전압 불일치")
        v_mag = _number(record["v_mag_pu"], f"모선 {bus_id} 전압 pu")
        expected_violation = v_mag < v_min_limit or v_mag > v_max_limit
        violation = _as_bool(record["voltage_violation"], f"모선 {bus_id} 위반")
        _check(violation == expected_violation, f"모선 {bus_id} 위반 판정이 설정 한계와 다릅니다.")
        buses.append({
            "bus_id": bus_id,
            "voltage_kv": voltage_kv,
            "v_mag_pu": v_mag,
            "v_ang_deg": _number(record["v_ang_deg"], f"모선 {bus_id} 위상각"),
            "control": str(record["control"]),
            "voltage_violation": violation,
            "analysis_status": "ANALYZED",
        })
    control_counts = pd.Series([item["control"] for item in buses]).value_counts().to_dict()
    _check(
        control_counts.get("PV", 0) == int(scenario["pv_bus_count"])
        and control_counts.get("PQ", 0) == int(scenario["pq_bus_count"])
        and control_counts.get("Slack", 0) == int(scenario["slack_bus_count"]),
        "모선 전압 결과의 PV/PQ/Slack 제어 수가 시나리오 요약과 다릅니다.",
    )
    _check(
        sum(item["voltage_violation"] for item in buses)
        == int(result_validation["violations"]["bus_voltage"]),
        "모선 전압 위반 CSV와 validation 집계가 다릅니다.",
    )

    branches_df = select_scenario(branch_loading_results, "선로·변압기 결과")
    _check(branches_df["equipment_id"].astype(str).is_unique, "선로·변압기 결과 ID가 중복됩니다.")
    lines_df = branches_df.loc[branches_df["component"].eq("line")]
    trafos_df = branches_df.loc[branches_df["component"].eq("transformer")]
    _check(set(lines_df["equipment_id"].astype(str)) == set(scenario_lines), "선로 결과 ID가 NetCDF와 다릅니다.")
    _check(set(trafos_df["equipment_id"].astype(str)) == set(scenario_transformers), "변압기 결과 ID가 NetCDF와 다릅니다.")
    lines = []
    for record in lines_df.to_dict("records"):
        line_id = str(record["equipment_id"])
        model_line = scenario_lines[line_id]
        bus1, bus2 = str(record["bus0"]), str(record["bus1"])
        _check((bus1, bus2) == (str(model_line.bus0), str(model_line.bus1)), f"선로 {line_id} 결과 연결이 NetCDF와 다릅니다.")
        _check(
            math.isclose(
                _number(record["s_nom"], f"선로 {line_id} 정격"),
                _number(model_line.s_nom, f"NetCDF 선로 {line_id} 정격"),
                rel_tol=1e-8,
                abs_tol=1e-6,
            ),
            f"선로 {line_id} 정격이 NetCDF와 다릅니다.",
        )
        _check(
            math.isclose(
                _number(record["bus0_voltage_kv"], f"선로 {line_id} bus1 kV"),
                _number(pf_model.buses.at[bus1, "v_nom"], "NetCDF 모선 정격전압"),
                abs_tol=1e-6,
            )
            and math.isclose(
                _number(record["bus1_voltage_kv"], f"선로 {line_id} bus2 kV"),
                _number(pf_model.buses.at[bus2, "v_nom"], "NetCDF 모선 정격전압"),
                abs_tol=1e-6,
            ),
            f"선로 {line_id} 단자 전압 등급이 NetCDF와 다릅니다.",
        )
        mapped = {str(row["line_id"]): row for row in map_data["lines"]}[line_id]
        _check((bus1, bus2) == (str(mapped["bus1_id"]), str(mapped["bus2_id"])), f"선로 {line_id} 결과 연결이 지도와 다릅니다.")
        lines.append({
            "line_id": line_id,
            "bus1_id": bus1,
            "bus2_id": bus2,
            "p0_mw": _number(record["p0_mw"], f"선로 {line_id} p0"),
            "q0_mvar": _number(record["q0_mvar"], f"선로 {line_id} q0"),
            "p1_mw": _number(record["p1_mw"], f"선로 {line_id} p1"),
            "q1_mvar": _number(record["q1_mvar"], f"선로 {line_id} q1"),
            "loading_percent": _number(record["loading_percent"], f"선로 {line_id} 부하율"),
            "thermal_violation": _as_bool(record["thermal_violation"], f"선로 {line_id} 열적 위반"),
            "analysis_status": "ANALYZED",
        })
    _check(
        sum(item["thermal_violation"] for item in lines)
        == int(result_validation["violations"]["line_thermal"]),
        "선로 열적 위반 CSV와 validation 집계가 다릅니다.",
    )

    transformers = []
    for record in trafos_df.to_dict("records"):
        transformer_id = str(record["equipment_id"])
        model_transformer = scenario_transformers[transformer_id]
        bus1, bus2 = str(record["bus0"]), str(record["bus1"])
        _check((bus1, bus2) == (str(model_transformer.bus0), str(model_transformer.bus1)), f"변압기 {transformer_id} 결과 연결이 NetCDF와 다릅니다.")
        _check(
            math.isclose(
                _number(record["s_nom"], f"변압기 {transformer_id} 정격"),
                _number(model_transformer.s_nom, f"NetCDF 변압기 {transformer_id} 정격"),
                rel_tol=1e-8,
                abs_tol=1e-6,
            ),
            f"변압기 {transformer_id} 정격이 NetCDF와 다릅니다.",
        )
        _check(
            math.isclose(
                _number(record["bus0_voltage_kv"], f"변압기 {transformer_id} bus1 kV"),
                _number(pf_model.buses.at[bus1, "v_nom"], "NetCDF 모선 정격전압"),
                abs_tol=1e-6,
            )
            and math.isclose(
                _number(record["bus1_voltage_kv"], f"변압기 {transformer_id} bus2 kV"),
                _number(pf_model.buses.at[bus2, "v_nom"], "NetCDF 모선 정격전압"),
                abs_tol=1e-6,
            ),
            f"변압기 {transformer_id} 단자 전압 등급이 NetCDF와 다릅니다.",
        )
        mapped = {str(row["transformer_id"]): row for row in map_data["transformers"]}[transformer_id]
        _check((bus1, bus2) == (str(mapped["bus1_id"]), str(mapped["bus2_id"])), f"변압기 {transformer_id} 결과 연결이 지도와 다릅니다.")
        transformers.append({
            "transformer_id": transformer_id,
            "bus1_id": bus1,
            "bus2_id": bus2,
            "bus1_voltage_kv": _number(record["bus0_voltage_kv"], f"변압기 {transformer_id} bus1 kV"),
            "bus2_voltage_kv": _number(record["bus1_voltage_kv"], f"변압기 {transformer_id} bus2 kV"),
            "p0_mw": _number(record["p0_mw"], f"변압기 {transformer_id} p0"),
            "q0_mvar": _number(record["q0_mvar"], f"변압기 {transformer_id} q0"),
            "p1_mw": _number(record["p1_mw"], f"변압기 {transformer_id} p1"),
            "q1_mvar": _number(record["q1_mvar"], f"변압기 {transformer_id} q1"),
            "loading_percent": _number(record["loading_percent"], f"변압기 {transformer_id} 부하율"),
            "thermal_violation": _as_bool(record["thermal_violation"], f"변압기 {transformer_id} 열적 위반"),
            "analysis_status": "ANALYZED",
        })
    _check(
        sum(item["thermal_violation"] for item in transformers)
        == int(result_validation["violations"]["transformer_thermal"]),
        "변압기 열적 위반 CSV와 validation 집계가 다릅니다.",
    )

    generators_df = select_scenario(generator_reactive_results, "발전기 결과")
    _check(generators_df["generator"].astype(str).is_unique, "발전기 결과 ID가 중복됩니다.")
    _check(len(generators_df) == int(scenario["generator_count"]), "발전기 결과 수가 시나리오 요약과 다릅니다.")
    _check(generators_df["generator"].astype(str).str.startswith("SYNTHETIC_").all(), "합성 발전기 ID 접두사가 올바르지 않습니다.")
    for column in (
        "p_nom", "p_set", "p_mw", "q_mvar", "s_mva", "p_min_mw", "q_min_mvar",
        "q_max_mvar", "s_screening_mva",
    ):
        for value in generators_df[column]:
            _number(value, f"발전기 결과 {column}")
    limit_columns = {
        "p_min_mw": "generator_p_min_mw",
        "p_nom": "generator_p_max_mw",
        "q_min_mvar": "generator_q_min_mvar",
        "q_max_mvar": "generator_q_max_mvar",
        "s_screening_mva": "generator_s_screening_mva",
    }
    for column, config_key in limit_columns.items():
        _check(config_key in synthetic_limits, f"합성 발전기 한도 설정 {config_key}가 없습니다.")
        for value in generators_df[column]:
            _check(
                math.isclose(
                    _number(value, f"발전기 설정 {column}"),
                    _number(synthetic_limits[config_key], f"합성 한도 {config_key}"),
                    rel_tol=0,
                    abs_tol=1e-8,
                ),
                f"발전기 결과 {column}이 설정 한도와 다릅니다.",
            )
    p_min = _number(synthetic_limits["generator_p_min_mw"], "발전기 P 하한")
    p_max = _number(synthetic_limits["generator_p_max_mw"], "발전기 P 상한")
    q_min = _number(synthetic_limits["generator_q_min_mvar"], "발전기 Q 하한")
    q_max = _number(synthetic_limits["generator_q_max_mvar"], "발전기 Q 상한")
    s_max = _number(synthetic_limits["generator_s_screening_mva"], "발전기 S 한도")
    for record in generators_df.to_dict("records"):
        _check(
            p_min - 1e-8 <= _number(record["p_mw"], "발전기 P") <= p_max + 1e-8
            and q_min - 1e-8 <= _number(record["q_mvar"], "발전기 Q") <= q_max + 1e-8
            and _number(record["s_mva"], "발전기 S") <= s_max + 1e-8,
            f"발전기 {record['generator']}가 설정 한도를 벗어났습니다.",
        )
    for column in ("p_limit_violation", "q_limit_violation", "s_limit_violation"):
        _check(
            not any(_as_bool(value, f"발전기 {column}") for value in generators_df[column]),
            f"발전기 결과에 {column} 위반이 있습니다.",
        )
    _check(
        sum(_as_bool(value, "발전기 P 위반") for value in generators_df["p_limit_violation"])
        == int(result_validation["violations"]["generator_p"])
        and sum(_as_bool(value, "발전기 Q 위반") for value in generators_df["q_limit_violation"])
        == int(result_validation["violations"]["generator_q"])
        and sum(_as_bool(value, "발전기 S 위반") for value in generators_df["s_limit_violation"])
        == int(result_validation["violations"]["generator_s"]),
        "발전기 한도 위반 CSV와 validation 집계가 다릅니다.",
    )
    reactor_df = select_scenario(shunt_reactor_results, "분로 리액터 결과")
    _check(len(reactor_df) == 1, "분로 리액터 결과가 한 대가 아닙니다.")
    reactor = reactor_df.iloc[0]
    _check(str(reactor["shunt"]).startswith("SYNTHETIC_SHUNT_REACTOR_"), "리액터 ID가 합성 접두사를 사용하지 않습니다.")
    _check(math.isclose(_number(reactor["nominal_q_absorption_mvar"], "리액터 정격"), 20, abs_tol=1e-8), "리액터 CSV 정격이 20 MVAr가 아닙니다.")
    _check(
        math.isclose(
            _number(reactor["q_mvar"], "리액터 단자 Q"),
            _number(scenario["shunt_actual_q_mvar"], "시나리오 리액터 Q"),
            abs_tol=1e-8,
        ),
        "리액터 Q 결과와 시나리오 요약이 다릅니다.",
    )

    balance_df = power_balance_validation.loc[
        power_balance_validation["scenario_id"].astype(str).eq(SCENARIO_ID)
    ]
    _check(set(balance_df["quantity"]) == {"P", "Q"}, "P/Q 전력수지 결과가 완전하지 않습니다.")
    balances = {str(row.quantity): row for row in balance_df.itertuples()}
    p_residual = _number(balances["P"].residual, "P 수지 잔차")
    q_residual = _number(balances["Q"].residual, "Q 수지 잔차")
    _check(all(_as_bool(value, "전력수지 passed") for value in balance_df["passed"]), "전력수지 검증이 실패했습니다.")
    _check(
        math.isclose(p_residual, _number(scenario["p_balance_residual_mw"], "요약 P 잔차"), abs_tol=1e-8)
        and math.isclose(q_residual, _number(scenario["q_balance_residual_mvar"], "요약 Q 잔차"), abs_tol=1e-8),
        "전력수지 CSV와 시나리오 요약의 잔차가 다릅니다.",
    )
    nodal_df = nodal_balance_validation.loc[
        nodal_balance_validation["scenario_id"].astype(str).eq(SCENARIO_ID)
    ]
    _check(set(nodal_df["bus"].astype(str)) == set(scenario_buses), "모선별 잔차와 NetCDF 모선 ID가 다릅니다.")
    nodal_max = max(
        math.hypot(_number(row.p_residual_mw, "모선 P 잔차"), _number(row.q_residual_mvar, "모선 Q 잔차"))
        for row in nodal_df.itertuples()
    )
    _check(math.isclose(nodal_max, _number(scenario["nodal_max_residual_mva"], "최대 모선 잔차"), abs_tol=1e-7), "최대 모선 수지 잔차가 시나리오 요약과 다릅니다.")
    _check(
        nodal_max <= _number(screening["balance_tolerance_mva"], "전력수지 허용오차"),
        "최대 모선 전력수지 잔차가 설정 허용오차를 초과합니다.",
    )

    snapshot_values = list(pf_model.snapshots)
    _check(len(snapshot_values) == 1, "PF 시나리오는 하나의 snapshot이어야 합니다.")
    snapshot = str(snapshot_values[0])
    _check(snapshot == "2013-01-01 00:00:00", "PF 시나리오 snapshot이 예상과 다릅니다.")
    bus_min = min(item["v_mag_pu"] for item in buses)
    bus_max = max(item["v_mag_pu"] for item in buses)
    line_max = max(item["loading_percent"] for item in lines)
    transformer_max = max(item["loading_percent"] for item in transformers)
    _check(math.isclose(bus_min, _number(scenario["v_min_pu"], "최소 전압"), abs_tol=1e-8), "최소 전압이 시나리오 요약과 다릅니다.")
    _check(math.isclose(bus_max, _number(scenario["v_max_pu"], "최대 전압"), abs_tol=1e-8), "최대 전압이 시나리오 요약과 다릅니다.")
    _check(math.isclose(line_max, _number(scenario["line_max_loading_percent"], "최대 선로 부하율"), abs_tol=1e-7), "최대 선로 부하율이 시나리오 요약과 다릅니다.")
    _check(math.isclose(transformer_max, _number(scenario["transformer_max_loading_percent"], "최대 변압기 부하율"), abs_tol=1e-7), "최대 변압기 부하율이 시나리오 요약과 다릅니다.")

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "grid_id": GRID_ID,
        "scenario_id": SCENARIO_ID,
        "snapshot": snapshot,
        "source_model": source_paths["pf_model"],
        "source_sha256": source_hashes["pf_model"],
        "map_model": source_paths["map_model"],
        "map_model_sha256": source_hashes["map_model"],
        "map_json_sha256": map_json_sha256,
        "poc_model_sha256": source_hashes["poc_model"],
        "pf_status": str(scenario["pf_status"]),
        "screening_status": str(scenario["operating_screen_status"]),
        "synthetic_scenario": True,
        "actual_operating_data": False,
        "source_provenance": source_hashes,
        "generator_count": len(generators_df),
        "load_mw": _number(scenario["load_mw"], "합성 부하 MW"),
        "load_mvar": _number(scenario["load_mvar"], "합성 부하 MVAr"),
        "reactor_count": len(reactor_df),
        "reactor_mvar": _number(reactor["nominal_q_absorption_mvar"], "리액터 정격"),
        "reactor_actual_q_mvar": _number(reactor["q_mvar"], "리액터 단자 Q"),
        "analysis_bus_count": len(buses),
        "analysis_line_count": len(lines),
        "analysis_transformer_count": len(transformers),
        "map_bus_count": len(map_data["buses"]),
        "map_line_count": len(map_data["lines"]),
        "map_transformer_count": len(map_data["transformers"]),
        "excluded_bus_ids": excluded_buses,
        "excluded_line_ids": excluded_lines,
        "voltage_limits_pu": {"min": v_min_limit, "max": v_max_limit},
    }
    summary = {
        "voltage_min_pu": bus_min,
        "voltage_max_pu": bus_max,
        "line_max_loading_percent": line_max,
        "transformer_max_loading_percent": transformer_max,
        "pf_converged": True,
        "screening_violations": violations,
        "power_balance_residual": {
            "p_mw": p_residual,
            "q_mvar": q_residual,
            "max_nodal_residual_mva": nodal_max,
        },
        "model_reliability_status": result_validation["model_reliability_status"],
    }
    output = {
        "metadata": metadata,
        "summary": summary,
        "buses": sorted(buses, key=lambda item: item["bus_id"]),
        "lines": sorted(lines, key=lambda item: item["line_id"]),
        "transformers": sorted(transformers, key=lambda item: item["transformer_id"]),
    }
    json.dumps(output, allow_nan=False)
    return output


def export_preview(mongolia_root: Path, map_file: Path, output_file: Path) -> dict[str, Any]:
    try:
        import pypsa
    except ImportError as exc:
        raise RuntimeError("Export requires PyPSA 1.2.4 in the source Pixi environment.") from exc
    if pypsa.__version__ != "1.2.4":
        raise RuntimeError(f"Expected PyPSA 1.2.4, found {pypsa.__version__}.")

    network_root = mongolia_root / "pypsa-earth" / "networks"
    reports = mongolia_root / "reports" / "reactive_support"
    config_root = mongolia_root / "configs"
    paths = {
        "map_model": network_root / "MN_35k" / "base.nc",
        "poc_model": network_root / "MN_35k_poc" / "base.nc",
        "pf_model": network_root / "MN_35k_poc" / "scenarios" / "synthetic_reactive_support_base.nc",
        "scenario_comparison": reports / "scenario_comparison.csv",
        "bus_voltage_results": reports / "bus_voltage_results.csv",
        "branch_loading_results": reports / "branch_loading_results.csv",
        "generator_reactive_results": reports / "generator_reactive_results.csv",
        "shunt_reactor_results": reports / "shunt_reactor_results.csv",
        "power_balance_validation": reports / "power_balance_validation.csv",
        "nodal_balance_validation": reports / "nodal_balance_validation.csv",
        "result_validation": reports / "result_validation.json",
        "source_preservation": reports / "source_preservation.json",
        "acpf_config": config_root / "mn35k_acpf.json",
        "reactive_support_config": config_root / "mn35k_reactive_support.json",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if not map_file.is_file():
        missing.append(str(map_file))
    if missing:
        raise FileNotFoundError("필수 PF 미리보기 입력 파일 누락: " + ", ".join(missing))

    map_data = json.loads(map_file.read_text(encoding="utf-8"))
    hashes = {key: _sha256(path) for key, path in paths.items()}
    hashes["map_json"] = _sha256(map_file)
    acpf_config = json.loads(paths["acpf_config"].read_text(encoding="utf-8"))
    reactive_config = json.loads(paths["reactive_support_config"].read_text(encoding="utf-8"))
    protected_models = acpf_config.get("protected_models", {})
    _check(
        hashes["map_model"] == protected_models.get("pypsa-earth/networks/MN_35k/base.nc"),
        "지도 원본 NetCDF가 검증된 source-preservation hash와 다릅니다.",
    )
    _check(
        hashes["poc_model"] == protected_models.get("pypsa-earth/networks/MN_35k_poc/base.nc"),
        "PF 원본 NetCDF가 검증된 source-preservation hash와 다릅니다.",
    )
    expected_pf_model = str(reactive_config.get("output_model", ""))
    _check(
        expected_pf_model
        == "pypsa-earth/networks/MN_35k_poc/scenarios/synthetic_reactive_support_base.nc",
        "합성 PF NetCDF 경로가 예상한 고정 시나리오와 다릅니다.",
    )
    result_validation = json.loads(paths["result_validation"].read_text(encoding="utf-8"))
    source_preservation = json.loads(paths["source_preservation"].read_text(encoding="utf-8"))

    model_paths = {
        "map_model": paths["map_model"],
        "poc_model": paths["poc_model"],
        "pf_model": paths["pf_model"],
    }
    hashes.update({key: _sha256(path) for key, path in model_paths.items()})
    networks = {key: pypsa.Network(path) for key, path in model_paths.items()}
    source_paths = {
        "map_model": model_paths["map_model"].relative_to(mongolia_root).as_posix(),
        "poc_model": model_paths["poc_model"].relative_to(mongolia_root).as_posix(),
        "pf_model": model_paths["pf_model"].relative_to(mongolia_root).as_posix(),
    }
    output = build_preview(
        map_data=map_data,
        map_json_sha256=hashes["map_json"],
        map_model=networks["map_model"],
        poc_model=networks["poc_model"],
        pf_model=networks["pf_model"],
        scenario_comparison=pd.read_csv(paths["scenario_comparison"]),
        bus_voltage_results=pd.read_csv(paths["bus_voltage_results"]),
        branch_loading_results=pd.read_csv(paths["branch_loading_results"]),
        generator_reactive_results=pd.read_csv(paths["generator_reactive_results"]),
        shunt_reactor_results=pd.read_csv(paths["shunt_reactor_results"]),
        power_balance_validation=pd.read_csv(paths["power_balance_validation"]),
        nodal_balance_validation=pd.read_csv(paths["nodal_balance_validation"]),
        result_validation=result_validation,
        source_preservation=source_preservation,
        acpf_config=acpf_config,
        synthetic_limits=reactive_config["synthetic_limits"],
        source_paths=source_paths,
        source_hashes=hashes,
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    output_file.write_text(serialized, encoding="utf-8")
    return {
        "scenario_id": SCENARIO_ID,
        "buses": len(output["buses"]),
        "lines": len(output["lines"]),
        "transformers": len(output["transformers"]),
        "output": str(output_file),
        "source_sha256": hashes["pf_model"],
        "map_json_sha256": hashes["map_json"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mongolia-root",
        type=Path,
        default=Path.home() / "Projects" / "mongolia-grid-poc",
    )
    parser.add_argument(
        "--map-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "grids" / "mn-osm-35k-map.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "grids" / "mn35k-synthetic-pf-preview.json",
    )
    args = parser.parse_args()
    print(json.dumps(export_preview(args.mongolia_root, args.map_file, args.output), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
