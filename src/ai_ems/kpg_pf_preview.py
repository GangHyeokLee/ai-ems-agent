"""Isolated, cached KPG-193 base-case AC load-flow preview."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
from pathlib import Path
from threading import Lock
from typing import Any

import pandas as pd
import pypowsybl as pp

from ai_ems.config import BUS_LOCATION_FILE, CASE_FILE, PROJECT_ROOT
from ai_ems.network import LOADFLOW_PARAMETERS, load_network

SCHEMA_VERSION = "1.0"
GRID_ID = "kr-kpg193"

_cache_lock = Lock()
_cached_preview: dict[str, Any] | None = None
_cached_error: str | None = None
logger = logging.getLogger(__name__)


class KpgPreviewUnavailable(RuntimeError):
    """The isolated KPG base-case load flow did not produce a valid result."""


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _map_source_provenance() -> tuple[str | None, dict[str, str | None]]:
    sources = {
        "bus_locations_sha256": (
            _sha256(Path(BUS_LOCATION_FILE))
            if Path(BUS_LOCATION_FILE).is_file()
            else None
        ),
        "geography_sha256": None,
    }
    geography_file = PROJECT_ROOT / "data" / "grids" / "kr-geography.json"
    if geography_file.is_file():
        sources["geography_sha256"] = _sha256(geography_file)
    present_sources = {key: value for key, value in sources.items() if value is not None}
    if not present_sources:
        return None, sources
    serialized = json.dumps(present_sources, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest(), sources


def bus_number(value: Any) -> int:
    text = str(value)
    if text.startswith("VL-"):
        text = text[3:]
    try:
        return int(text.split("_", 1)[0])
    except ValueError as exc:
        raise ValueError(f"Unsupported KPG bus identifier: {value}") from exc


def _line_ratings(network: Any) -> dict[str, float]:
    limits = network.get_loading_limits(all_attributes=True)
    if limits.empty:
        return {}

    records = limits.reset_index()
    records = records.loc[
        records["type"].astype(str).eq("APPARENT_POWER")
        & records["name"].astype(str).eq("permanent_limit")
        & records["selected"].fillna(False).astype(bool)
    ]
    ratings: dict[str, float] = {}
    for line_id, rows in records.groupby("element_id"):
        valid = [
            value
            for value in (_finite(raw) for raw in rows["value"])
            if value is not None and value > 0
        ]
        if valid:
            ratings[str(line_id)] = min(valid)
    return ratings


def _control_buses(
    network: Any,
    load_flow_results: list[Any],
) -> dict[int, str]:
    controls: dict[int, str] = {}
    for result in load_flow_results:
        reference_id = getattr(result, "reference_bus_id", None)
        if reference_id:
            try:
                controls[bus_number(reference_id)] = "Slack"
            except ValueError:
                continue

    generators = network.get_generators(all_attributes=True)
    if not generators.empty:
        for row in generators.itertuples():
            if not bool(getattr(row, "connected", False)) or not bool(
                getattr(row, "voltage_regulator_on", False)
            ):
                continue
            try:
                bus_id = bus_number(row.voltage_level_id)
            except (TypeError, ValueError):
                continue
            controls.setdefault(bus_id, "PV")
    return controls


def build_kpg_pf_preview(
    network: Any,
    *,
    source_model: str,
    source_sha256: str,
    map_model_sha256: str | None = None,
    map_source_provenance: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Run AC PF on the supplied isolated model and adapt its solved state."""
    results = pp.loadflow.run_ac(network, parameters=LOADFLOW_PARAMETERS)
    converged = bool(results) and all(
        item.status.name == "CONVERGED" for item in results
    )
    if not converged:
        statuses = ", ".join(item.status.name for item in results) or "NO_RESULT"
        raise KpgPreviewUnavailable(f"AC Power Flow did not converge ({statuses}).")

    voltage_levels = network.get_voltage_levels(all_attributes=True)
    buses = network.get_buses(all_attributes=True)
    line_frame = network.get_lines(all_attributes=True)
    transformers_frame = network.get_2_windings_transformers(all_attributes=True)
    hvdc_frame = network.get_hvdc_lines(all_attributes=True)
    controls = _control_buses(network, results)

    bus_results: list[dict[str, Any]] = []
    for row in buses.itertuples():
        voltage_level = voltage_levels.loc[row.voltage_level_id]
        nominal_v = _finite(voltage_level.nominal_v)
        voltage_kv = _finite(row.v_mag)
        v_pu = (
            voltage_kv / nominal_v
            if voltage_kv is not None and nominal_v is not None and nominal_v > 0
            else None
        )
        low_pu = (
            _finite(voltage_level.low_voltage_limit) / nominal_v
            if _finite(voltage_level.low_voltage_limit) is not None
            and nominal_v is not None
            and nominal_v > 0
            else None
        )
        high_pu = (
            _finite(voltage_level.high_voltage_limit) / nominal_v
            if _finite(voltage_level.high_voltage_limit) is not None
            and nominal_v is not None
            and nominal_v > 0
            else None
        )
        if None in (v_pu, low_pu, high_pu):
            status = "NOT_ANALYZED"
            voltage_violation = None
        else:
            status = "ANALYZED"
            voltage_violation = v_pu < low_pu or v_pu > high_pu
        try:
            bus_id = bus_number(row.voltage_level_id)
        except ValueError:
            continue
        bus_results.append({
            "bus_id": str(bus_id),
            "voltage_kv": nominal_v,
            "v_mag_pu": v_pu,
            "v_ang_deg": _finite(row.v_angle),
            "voltage_min_pu": low_pu,
            "voltage_max_pu": high_pu,
            "control": controls.get(bus_id, "PQ"),
            "voltage_violation": voltage_violation,
            "analysis_status": status,
        })

    ratings = _line_ratings(network)
    line_results: list[dict[str, Any]] = []
    for line_id, row in line_frame.iterrows():
        try:
            bus1_id = str(bus_number(row["voltage_level1_id"]))
            bus2_id = str(bus_number(row["voltage_level2_id"]))
        except (TypeError, ValueError):
            continue
        p0, q0, p1, q1 = (
            _finite(row.get(column)) for column in ("p1", "q1", "p2", "q2")
        )
        connected = bool(row.get("connected1", False)) and bool(
            row.get("connected2", False)
        )
        analyzed = connected and all(value is not None for value in (p0, q0, p1, q1))
        rating = ratings.get(str(line_id))
        loading = None
        if analyzed and rating is not None:
            loading = max(math.hypot(p0, q0), math.hypot(p1, q1)) / rating * 100
        line_results.append({
            "line_id": str(line_id),
            "bus1_id": bus1_id,
            "bus2_id": bus2_id,
            "p0_mw": p0 if analyzed else None,
            "q0_mvar": q0 if analyzed else None,
            "p1_mw": p1 if analyzed else None,
            "q1_mvar": q1 if analyzed else None,
            "rating_mva": rating,
            "loading_percent": loading,
            "thermal_violation": loading > 100 if loading is not None else None,
            "analysis_status": "ANALYZED" if analyzed else "NOT_ANALYZED",
        })

    transformer_results: list[dict[str, Any]] = []
    transformer_violations = 0
    for transformer_id, row in transformers_frame.iterrows():
        try:
            bus1_id = str(bus_number(row["voltage_level1_id"]))
            bus2_id = str(bus_number(row["voltage_level2_id"]))
        except (TypeError, ValueError):
            continue
        p0, q0, p1, q1 = (
            _finite(row.get(column)) for column in ("p1", "q1", "p2", "q2")
        )
        rated_s = _finite(row.get("rated_s"))
        analyzed = all(value is not None for value in (p0, q0, p1, q1))
        loading = (
            max(math.hypot(p0, q0), math.hypot(p1, q1)) / rated_s * 100
            if analyzed and rated_s is not None and rated_s > 0
            else None
        )
        transformer_violations += int(loading is not None and loading > 100)
        transformer_results.append({
            "transformer_id": str(transformer_id),
            "bus1_id": bus1_id,
            "bus2_id": bus2_id,
            "bus1_voltage_kv": _finite(
                voltage_levels.loc[row["voltage_level1_id"], "nominal_v"]
            ),
            "bus2_voltage_kv": _finite(
                voltage_levels.loc[row["voltage_level2_id"], "nominal_v"]
            ),
            "p0_mw": p0 if analyzed else None,
            "q0_mvar": q0 if analyzed else None,
            "p1_mw": p1 if analyzed else None,
            "q1_mvar": q1 if analyzed else None,
            "rating_mva": rated_s,
            "loading_percent": loading,
            "thermal_violation": loading > 100 if loading is not None else None,
            "analysis_status": "ANALYZED" if analyzed else "NOT_ANALYZED",
        })

    analyzed_voltages = [
        item["v_mag_pu"] for item in bus_results
        if item["analysis_status"] == "ANALYZED"
    ]
    known_line_loadings = [
        item["loading_percent"] for item in line_results
        if item["loading_percent"] is not None
    ]
    known_transformer_loadings = [
        item["loading_percent"] for item in transformer_results
        if item["loading_percent"] is not None
    ]
    loads = network.get_loads(all_attributes=True)
    connected_loads = loads.loc[loads["connected"].fillna(False).astype(bool)]
    load_mw = sum(_finite(value) or 0.0 for value in connected_loads["p0"])
    load_mvar = sum(_finite(value) or 0.0 for value in connected_loads["q0"])

    return {
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "grid_id": GRID_ID,
            "scenario_id": "kpg193_base_ac_load_flow",
            "snapshot": "steady_state_base_case",
            "source_model": source_model,
            "source_sha256": source_sha256,
            "map_model_sha256": map_model_sha256,
            "pf_status": "AC_PF_CONVERGED",
            "screening_status": "MODEL_LIMITS_ONLY_NOT_OPERATIONAL_SCREENING",
            "synthetic_scenario": True,
            "actual_operating_data": False,
            "source_provenance": {
                "case_model_sha256": source_sha256,
                **(map_source_provenance or {}),
            },
            "generator_count": len(network.get_generators()),
            "load_mw": load_mw,
            "load_mvar": load_mvar,
            "analysis_bus_count": len(bus_results),
            "analysis_line_count": len(line_results),
            "analysis_transformer_count": len(transformer_results),
            "non_ac_equipment": {
                "hvdc_lines": {
                    "count": len(hvdc_frame),
                    "ids": [str(identifier) for identifier in hvdc_frame.index],
                    "analysis_status": "NOT_INCLUDED_IN_AC_LINE_RESULTS",
                },
            },
            "voltage_limit_source": "KPG voltage-level low/high limits",
        },
        "summary": {
            "voltage_min_pu": min(analyzed_voltages) if analyzed_voltages else None,
            "voltage_max_pu": max(analyzed_voltages) if analyzed_voltages else None,
            "line_max_loading_percent": max(known_line_loadings) if known_line_loadings else None,
            "transformer_max_loading_percent": max(known_transformer_loadings) if known_transformer_loadings else None,
            "pf_converged": True,
            "screening_violations": {
                "bus_voltage": sum(item["voltage_violation"] is True for item in bus_results),
                "line_thermal": sum(item["thermal_violation"] is True for item in line_results),
                "transformer_thermal": transformer_violations,
            },
            "power_balance_residual": None,
            "model_reliability_status": "SYNTHETIC_TEST_CASE_NOT_OPERATIONAL_DATA",
            "iteration_count": max(
                (int(getattr(item, "iteration_count", 0) or 0) for item in results),
                default=0,
            ),
        },
        "buses": bus_results,
        "lines": line_results,
        "transformers": transformer_results,
    }


def validate_kpg_pf_preview(
    payload: dict[str, Any],
    map_data: dict[str, Any],
) -> dict[str, Any]:
    """Reject cached results that no longer match the current KPG map."""
    metadata = payload.get("metadata")
    summary = payload.get("summary")
    if not isinstance(metadata, dict) or not isinstance(summary, dict):
        raise ValueError("KPG PF 결과의 metadata 또는 summary가 없습니다.")
    if (
        metadata.get("schema_version") != SCHEMA_VERSION
        or metadata.get("grid_id") != GRID_ID
        or metadata.get("scenario_id") != "kpg193_base_ac_load_flow"
        or metadata.get("synthetic_scenario") is not True
        or metadata.get("actual_operating_data") is not False
        or metadata.get("pf_status") != "AC_PF_CONVERGED"
        or summary.get("pf_converged") is not True
    ):
        raise ValueError("KPG PF 결과가 검증된 합성 기본 시나리오가 아닙니다.")
    source_sha256 = metadata.get("source_sha256")
    map_sha256 = metadata.get("map_model_sha256")
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(char not in "0123456789abcdef" for char in source_sha256.lower())
        or (
            map_sha256 is not None
            and (
                not isinstance(map_sha256, str)
                or len(map_sha256) != 64
                or any(char not in "0123456789abcdef" for char in map_sha256.lower())
            )
        )
        or not isinstance(metadata.get("source_provenance"), dict)
    ):
        raise ValueError("KPG PF 출처 해시 정보가 올바르지 않습니다.")
    if not all(isinstance(payload.get(key), list) for key in ("buses", "lines", "transformers")):
        raise ValueError("KPG PF 설비 목록 형식이 잘못되었습니다.")
    if not _is_finite_tree(payload):
        raise ValueError("KPG PF 결과에 NaN, Infinity 또는 지원하지 않는 값이 있습니다.")

    def unique_map(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f"KPG PF {key} 항목 형식이 잘못되었습니다.")
            identifier = str(record.get(key, "")).strip()
            if not identifier or identifier in result:
                raise ValueError(f"KPG PF {key}가 비어 있거나 중복됩니다.")
            result[identifier] = record
        return result

    result_buses = unique_map(payload["buses"], "bus_id")
    result_lines = unique_map(payload["lines"], "line_id")
    result_transformers = unique_map(payload["transformers"], "transformer_id")
    map_buses = unique_map(map_data.get("buses", []), "bus_id")
    map_lines = unique_map(map_data.get("lines", []), "line_id")
    map_transformers = unique_map(map_data.get("transformers", []), "transformer_id")
    if (
        metadata.get("analysis_bus_count") != len(result_buses)
        or metadata.get("analysis_line_count") != len(result_lines)
        or metadata.get("analysis_transformer_count") != len(result_transformers)
    ):
        raise ValueError("KPG PF 결과 개수가 metadata와 일치하지 않습니다.")
    if set(result_buses) != set(map_buses):
        raise ValueError("KPG PF 모선 ID가 지도와 일치하지 않습니다.")
    if set(result_lines) != set(map_lines):
        raise ValueError("KPG PF 선로 ID가 지도와 일치하지 않습니다.")
    if set(result_transformers) != set(map_transformers):
        raise ValueError("KPG PF 변압기 ID가 지도와 일치하지 않습니다.")
    for line_id, item in result_lines.items():
        mapped = map_lines[line_id]
        if (
            str(item["bus1_id"]) != str(mapped["bus1_id"])
            or str(item["bus2_id"]) != str(mapped["bus2_id"])
        ):
            raise ValueError(f"KPG PF 선로 {line_id}의 연결 모선이 지도와 다릅니다.")
        if item.get("loading_percent") is not None and (
            _finite(item.get("rating_mva")) is None
            or float(item["rating_mva"]) <= 0
        ):
            raise ValueError(f"KPG PF 선로 {line_id}의 부하율 정격이 올바르지 않습니다.")
    for bus_id, item in result_buses.items():
        mapped = map_buses[bus_id]
        if not math.isclose(
            float(item["voltage_kv"]), float(mapped["voltage_kv"]), abs_tol=1e-8
        ):
            raise ValueError(f"KPG PF 모선 {bus_id}의 정격전압이 지도와 다릅니다.")
    for transformer_id, item in result_transformers.items():
        mapped = map_transformers[transformer_id]
        if (
            str(item["bus1_id"]) != str(mapped["bus1_id"])
            or str(item["bus2_id"]) != str(mapped["bus2_id"])
        ):
            raise ValueError(
                f"KPG PF 변압기 {transformer_id}의 연결 모선이 지도와 다릅니다."
            )
    return payload


def _is_finite_tree(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool)):
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


def get_kpg_pf_preview() -> dict[str, Any]:
    """Load a private model instance once and cache its validated PF output."""
    global _cached_preview, _cached_error
    with _cache_lock:
        if _cached_preview is not None:
            return copy.deepcopy(_cached_preview)
        if _cached_error is not None:
            raise KpgPreviewUnavailable(_cached_error)
        try:
            model_path = Path(CASE_FILE).expanduser().resolve()
            if not model_path.is_file():
                raise FileNotFoundError(f"KPG model is missing: {model_path}")
            isolated_network = load_network(model_path)
            map_model_sha256, map_sources = _map_source_provenance()
            _cached_preview = build_kpg_pf_preview(
                isolated_network,
                source_model=model_path.name,
                source_sha256=_sha256(model_path),
                map_model_sha256=map_model_sha256,
                map_source_provenance=map_sources,
            )
            return copy.deepcopy(_cached_preview)
        except Exception as exc:
            logger.exception("KPG isolated AC Power Flow preview is unavailable")
            _cached_error = str(exc) or exc.__class__.__name__
            raise KpgPreviewUnavailable(_cached_error) from exc
