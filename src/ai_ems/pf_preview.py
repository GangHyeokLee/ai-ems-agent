"""Validation for the read-only synthetic Mongolia PF preview contract."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from ai_ems.grid_catalog import load_mongolia_map

SCHEMA_VERSION = "1.0"
EXPECTED_GRID_ID = "mn-osm-35k"
EXPECTED_SCENARIO_ID = "reduced_27_q_limited_with_reactor"
EXPECTED_PF_STATUS = "AC_PF_CONVERGED"
EXPECTED_SCREENING_STATUS = "SYNTHETIC_SCENARIO_WITHIN_SCREENING_LIMITS"


def _is_finite_tree(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, list):
        return all(_is_finite_tree(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_finite_tree(item)
            for key, item in value.items()
        )
    return False


def _id_map(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        identifier = str(item.get(key, "")).strip()
        if not identifier or identifier in result:
            raise ValueError(f"{label} ID가 비어 있거나 중복됩니다.")
        result[identifier] = item
    return result


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 값이 숫자가 아닙니다.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 값이 유한하지 않습니다.")
    return number


def validate_pf_preview(
    payload: Any,
    map_data: dict[str, Any],
    map_json_sha256: str,
) -> dict[str, Any]:
    """Validate the fixed scenario, values, counts, and structural map correspondence."""
    if not isinstance(payload, dict):
        raise ValueError("PF 미리보기는 JSON 객체여야 합니다.")
    if not _is_finite_tree(payload):
        raise ValueError("PF 미리보기에 유한하지 않거나 지원하지 않는 값이 있습니다.")
    metadata = payload.get("metadata")
    summary = payload.get("summary")
    if not isinstance(metadata, dict) or not isinstance(summary, dict):
        raise ValueError("PF 미리보기의 metadata 또는 summary가 없습니다.")
    metadata_fields = {
        "schema_version",
        "grid_id",
        "scenario_id",
        "snapshot",
        "source_model",
        "source_sha256",
        "map_model_sha256",
        "map_json_sha256",
        "pf_status",
        "screening_status",
        "synthetic_scenario",
        "actual_operating_data",
        "source_provenance",
        "generator_count",
        "load_mw",
        "load_mvar",
        "reactor_mvar",
        "analysis_bus_count",
        "analysis_line_count",
        "analysis_transformer_count",
        "map_bus_count",
        "map_line_count",
        "map_transformer_count",
        "excluded_bus_ids",
        "excluded_line_ids",
        "voltage_limits_pu",
    }
    summary_fields = {
        "voltage_min_pu",
        "voltage_max_pu",
        "line_max_loading_percent",
        "transformer_max_loading_percent",
        "pf_converged",
        "screening_violations",
        "power_balance_residual",
        "model_reliability_status",
    }
    if not metadata_fields.issubset(metadata) or not summary_fields.issubset(summary):
        raise ValueError("PF 미리보기 필수 metadata 또는 summary 필드가 누락되었습니다.")
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("지원하지 않는 PF 미리보기 schema_version입니다.")
    if metadata.get("grid_id") != EXPECTED_GRID_ID:
        raise ValueError("PF 미리보기 grid_id가 몽골 지도와 일치하지 않습니다.")
    if metadata.get("scenario_id") != EXPECTED_SCENARIO_ID:
        raise ValueError("허용되지 않은 몽골 PF 시나리오입니다.")
    if metadata.get("snapshot") != "2013-01-01 00:00:00":
        raise ValueError("PF 미리보기 snapshot이 검증된 단일 시점과 다릅니다.")
    if metadata.get("synthetic_scenario") is not True or metadata.get("actual_operating_data") is not False:
        raise ValueError("PF 미리보기의 합성 데이터 출처 표시가 올바르지 않습니다.")
    if (
        metadata.get("generator_count") != 45
        or metadata.get("reactor_count") != 1
        or not math.isclose(_finite_number(metadata.get("reactor_mvar"), "리액터 정격"), 20.0, abs_tol=1e-8)
        or not math.isclose(_finite_number(metadata.get("load_mw"), "부하 MW"), 25.6, abs_tol=1e-8)
        or not math.isclose(_finite_number(metadata.get("load_mvar"), "부하 MVAr"), 8.41431309258, abs_tol=1e-8)
    ):
        raise ValueError("PF 미리보기의 합성 설비 또는 부하 메타데이터가 검증값과 다릅니다.")
    for hash_name in ("source_sha256", "map_model_sha256", "map_json_sha256"):
        value = metadata.get(hash_name)
        if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.lower()):
            raise ValueError(f"PF 미리보기 {hash_name} 형식이 잘못되었습니다.")
    if not isinstance(metadata.get("source_provenance"), dict):
        raise ValueError("PF 미리보기 출처 해시 목록이 올바르지 않습니다.")
    if metadata.get("map_json_sha256") != map_json_sha256:
        raise ValueError("PF 미리보기와 지도 JSON의 fingerprint가 일치하지 않습니다.")
    if metadata.get("pf_status") != EXPECTED_PF_STATUS or summary.get("pf_converged") is not True:
        raise ValueError("검증된 수렴 PF 결과가 아닙니다.")
    if metadata.get("screening_status") != EXPECTED_SCREENING_STATUS:
        raise ValueError("가정된 운전 스크리닝을 통과한 시나리오가 아닙니다.")
    if summary.get("screening_violations") != {
        "bus_voltage": 0,
        "line_thermal": 0,
        "transformer_thermal": 0,
        "generator_p": 0,
        "generator_q": 0,
        "generator_s": 0,
    }:
        raise ValueError("PF 미리보기의 스크리닝 위반 집계가 0이 아닙니다.")
    if summary.get("model_reliability_status") != "MODEL_DATA_REQUIRES_REVIEW":
        raise ValueError("모델 데이터 신뢰성 경고가 누락되었습니다.")

    buses = payload.get("buses")
    lines = payload.get("lines")
    transformers = payload.get("transformers")
    if not all(isinstance(items, list) for items in (buses, lines, transformers)):
        raise ValueError("PF 미리보기 설비 목록 형식이 올바르지 않습니다.")
    required_rows = {
        "buses": {
            "bus_id", "voltage_kv", "v_mag_pu", "v_ang_deg", "control",
            "voltage_violation", "analysis_status",
        },
        "lines": {
            "line_id", "bus1_id", "bus2_id", "p0_mw", "q0_mvar", "p1_mw",
            "q1_mvar", "rating_mva", "loading_percent", "thermal_violation",
            "analysis_status",
        },
        "transformers": {
            "transformer_id", "bus1_id", "bus2_id", "bus1_voltage_kv",
            "bus2_voltage_kv", "p0_mw", "q0_mvar", "p1_mw", "q1_mvar",
            "rating_mva", "loading_percent", "thermal_violation", "analysis_status",
        },
    }
    for label, records in (("buses", buses), ("lines", lines), ("transformers", transformers)):
        if any(not isinstance(item, dict) or not required_rows[label].issubset(item) for item in records):
            raise ValueError(f"PF 미리보기 {label} 필수 필드가 누락되었습니다.")
    map_buses = _id_map(map_data["buses"], "bus_id", "지도 모선")
    map_lines = _id_map(map_data["lines"], "line_id", "지도 선로")
    map_transformers = _id_map(map_data["transformers"], "transformer_id", "지도 변압기")
    result_buses = _id_map(buses, "bus_id", "PF 모선")
    result_lines = _id_map(lines, "line_id", "PF 선로")
    result_transformers = _id_map(transformers, "transformer_id", "PF 변압기")

    if not set(result_buses).issubset(map_buses):
        raise ValueError("PF 모선 ID가 지도 설비와 일치하지 않습니다.")
    if not set(result_lines).issubset(map_lines):
        raise ValueError("PF 선로 ID가 지도 설비와 일치하지 않습니다.")
    if set(result_transformers) != set(map_transformers):
        raise ValueError("PF 변압기 ID가 지도 설비와 일치하지 않습니다.")
    if not isinstance(metadata.get("excluded_bus_ids"), list) or not isinstance(metadata.get("excluded_line_ids"), list):
        raise ValueError("해석 대상 제외 설비 목록 형식이 올바르지 않습니다.")
    if set(metadata["excluded_bus_ids"]) != set(map_buses) - set(result_buses):
        raise ValueError("해석 대상 제외 모선 목록이 지도와 일치하지 않습니다.")
    if set(metadata["excluded_line_ids"]) != set(map_lines) - set(result_lines):
        raise ValueError("해석 대상 제외 선로 목록이 지도와 일치하지 않습니다.")

    for bus_id, result in result_buses.items():
        mapped = map_buses[bus_id]
        if not math.isclose(
            _finite_number(result["voltage_kv"], f"모선 {bus_id} 전압"),
            _finite_number(mapped["voltage_kv"], f"지도 모선 {bus_id} 전압"),
            rel_tol=0,
            abs_tol=1e-6,
        ):
            raise ValueError(f"모선 {bus_id} 정격전압이 지도와 일치하지 않습니다.")
        if result.get("analysis_status") != "ANALYZED":
            raise ValueError(f"모선 {bus_id}의 분석 상태가 올바르지 않습니다.")
        if not isinstance(result.get("voltage_violation"), bool):
            raise ValueError(f"모선 {bus_id}의 전압 위반 상태가 올바르지 않습니다.")
        limits = metadata.get("voltage_limits_pu")
        if (
            not isinstance(limits, dict)
            or not isinstance(limits.get("min"), (int, float))
            or not isinstance(limits.get("max"), (int, float))
            or result["voltage_violation"]
            != (
                _finite_number(result["v_mag_pu"], f"모선 {bus_id} pu")
                < _finite_number(limits["min"], "전압 하한")
                or _finite_number(result["v_mag_pu"], f"모선 {bus_id} pu")
                > _finite_number(limits["max"], "전압 상한")
            )
        ):
            raise ValueError(f"모선 {bus_id} 전압 위반이 설정 한계와 다릅니다.")

    for line_id, result in result_lines.items():
        mapped = map_lines[line_id]
        if (str(result.get("bus1_id")), str(result.get("bus2_id"))) != (
            str(mapped["bus1_id"]),
            str(mapped["bus2_id"]),
        ):
            raise ValueError(f"선로 {line_id}의 연결 모선이 지도와 일치하지 않습니다.")
        if str(result["bus1_id"]) not in result_buses or str(result["bus2_id"]) not in result_buses:
            raise ValueError(f"선로 {line_id}가 해석 대상 외 모선에 연결됩니다.")
        if result.get("analysis_status") != "ANALYZED":
            raise ValueError(f"선로 {line_id}의 분석 상태가 올바르지 않습니다.")
        if _finite_number(result["rating_mva"], f"선로 {line_id} 정격") <= 0:
            raise ValueError(f"선로 {line_id}의 MVA 정격이 올바르지 않습니다.")
        if not isinstance(result.get("thermal_violation"), bool):
            raise ValueError(f"선로 {line_id}의 열적 위반 상태가 올바르지 않습니다.")

    for transformer_id, result in result_transformers.items():
        mapped = map_transformers[transformer_id]
        if (str(result.get("bus1_id")), str(result.get("bus2_id"))) != (
            str(mapped["bus1_id"]),
            str(mapped["bus2_id"]),
        ):
            raise ValueError(f"변압기 {transformer_id}의 연결 모선이 지도와 일치하지 않습니다.")
        if str(result["bus1_id"]) not in result_buses or str(result["bus2_id"]) not in result_buses:
            raise ValueError(f"변압기 {transformer_id}가 해석 대상 외 모선에 연결됩니다.")
        if result.get("analysis_status") != "ANALYZED":
            raise ValueError(f"변압기 {transformer_id}의 분석 상태가 올바르지 않습니다.")
        if _finite_number(result["rating_mva"], f"변압기 {transformer_id} 정격") <= 0:
            raise ValueError(f"변압기 {transformer_id}의 MVA 정격이 올바르지 않습니다.")
        if not isinstance(result.get("thermal_violation"), bool):
            raise ValueError(f"변압기 {transformer_id}의 열적 위반 상태가 올바르지 않습니다.")
    actual_violations = {
        "bus_voltage": sum(bool(item["voltage_violation"]) for item in buses),
        "line_thermal": sum(bool(item["thermal_violation"]) for item in lines),
        "transformer_thermal": sum(bool(item["thermal_violation"]) for item in transformers),
        "generator_p": 0,
        "generator_q": 0,
        "generator_s": 0,
    }
    if summary["screening_violations"] != actual_violations:
        raise ValueError("요약과 개별 설비의 스크리닝 위반이 일치하지 않습니다.")
    if (
        not isinstance(metadata["voltage_limits_pu"], dict)
        or metadata["voltage_limits_pu"].get("min") != 0.9
        or metadata["voltage_limits_pu"].get("max") != 1.1
    ):
        raise ValueError("전압 스크리닝 기준이 검증된 설정과 다릅니다.")
    controls = [str(item["control"]) for item in buses]
    if controls.count("PV") != 43 or controls.count("PQ") != 122 or controls.count("Slack") != 1:
        raise ValueError("PV/PQ/Slack 제어 모선 수가 검증 결과와 다릅니다.")
    if not math.isclose(_finite_number(summary["line_max_loading_percent"], "최대 선로 부하율"), max(_finite_number(item["loading_percent"], "선로 부하율") for item in lines), abs_tol=1e-8):
        raise ValueError("최대 선로 부하율 요약이 결과 목록과 다릅니다.")
    if not math.isclose(_finite_number(summary["transformer_max_loading_percent"], "최대 변압기 부하율"), max(_finite_number(item["loading_percent"], "변압기 부하율") for item in transformers), abs_tol=1e-8):
        raise ValueError("최대 변압기 부하율 요약이 결과 목록과 다릅니다.")
    power_balance = summary.get("power_balance_residual")
    if not isinstance(power_balance, dict) or not {
        "p_mw", "q_mvar", "max_nodal_residual_mva"
    }.issubset(power_balance):
        raise ValueError("전력수지 요약이 누락되었습니다.")
    if abs(_finite_number(power_balance["max_nodal_residual_mva"], "모선 전력수지 잔차")) > 0.0001:
        raise ValueError("모선 전력수지 잔차가 합성 PF 검증 허용오차를 초과합니다.")
    if not math.isclose(
        min(        _finite_number(item["v_mag_pu"], "모선 전압") for item in buses),
        float(summary["voltage_min_pu"]),
        rel_tol=0,
        abs_tol=1e-8,
    ) or not math.isclose(
        max(        _finite_number(item["v_mag_pu"], "모선 전압") for item in buses),
        float(summary["voltage_max_pu"]),
        rel_tol=0,
        abs_tol=1e-8,
    ):
        raise ValueError("요약과 모선 전압 범위가 일치하지 않습니다.")

    expected_counts = {
        "analysis_bus_count": len(result_buses),
        "analysis_line_count": len(result_lines),
        "analysis_transformer_count": len(result_transformers),
        "map_bus_count": len(map_buses),
        "map_line_count": len(map_lines),
        "map_transformer_count": len(map_transformers),
    }
    if any(metadata.get(key) != value for key, value in expected_counts.items()):
        raise ValueError("PF 미리보기 설비 수가 실제 목록과 일치하지 않습니다.")
    if len(result_buses) != 166 or len(result_lines) != 156 or len(result_transformers) != 27:
        raise ValueError("PF 미리보기의 해석 대상 topology 수가 예상과 다릅니다.")
    return payload


def load_mongolia_pf_preview(
    preview_file: Path,
    map_file: Path,
) -> dict[str, Any]:
    """Load only the configured JSON assets and validate before serving them."""
    if not preview_file.is_file():
        raise FileNotFoundError("몽골 합성 PF 미리보기 데이터가 아직 생성되지 않았습니다.")
    map_data = load_mongolia_map(map_file)
    try:
        raw = preview_file.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("몽골 PF 미리보기 JSON을 읽을 수 없습니다.") from exc
    map_hash = hashlib.sha256(map_file.read_bytes()).hexdigest()
    return validate_pf_preview(payload, map_data, map_hash)
