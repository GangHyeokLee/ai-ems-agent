"""Read-only AI tools over the validated synthetic Mongolia AC PF preview.

These tools never recalculate power flow and never accept file paths. Every call
reloads the preview through ``load_mongolia_pf_preview()`` (via the injected
loader) so the fixed-scenario, fingerprint, convergence, screening and
finite-number checks cannot be bypassed.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Literal

from langchain_core.tools import tool

PreviewLoader = Callable[[], dict[str, Any]]

DATA_SOURCE = "검증된 합성 AC PF 미리보기 JSON (PyPSA 사전 계산 결과, 웹 요청 시 재계산하지 않음)"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
NOT_FOUND = "NOT_FOUND"
EXCLUDED = "EXCLUDED_FROM_ANALYSIS"
ANALYZED = "ANALYZED"
MAX_TOP_N = 10

MODEL_CAVEATS = [
    "합성 AC PF 시나리오 결과이며 실제 몽골 운전·측정 데이터가 아닙니다.",
    "snapshot 2013-01-01 00:00:00은 모델 시점 표기이며 실제 측정 날짜가 아닙니다.",
    "PF 수렴은 수치해가 존재한다는 뜻이며 실제 계통 운영 적합성을 의미하지 않습니다.",
    "합성 발전기 45대는 실제 몽골 발전소 45곳이 아닙니다.",
    "지도에 표시된 발전소 데이터 27건은 실제 계통 접속이 검증되지 않았습니다.",
    "선로·변압기 전기정수와 일부 길이·회선수는 실설비로 검증되지 않았습니다.",
    "이 합성 PF 결과에는 N-1, Redispatch, OPF, 동특성 해석 결과가 포함되지 않습니다(선로 N-1은 화면 Security Analysis 패널에서 별도 실행).",
]

SIGN_CONVENTION = (
    "P0/Q0는 bus1 단자, P1/Q1은 bus2 단자 값입니다. 각 단자에서 모선에서 설비(선로/변압기) "
    "방향으로 들어가는 조류가 양수입니다. 값은 원본 부호 그대로입니다."
)

RANKING_METRICS: dict[str, dict[str, Any]] = {
    "bus_voltage_high": {
        "label": "모선 전압 높은 순",
        "collection": "buses",
        "field": "v_mag_pu",
        "unit": "pu",
        "descending": True,
    },
    "bus_voltage_low": {
        "label": "모선 전압 낮은 순",
        "collection": "buses",
        "field": "v_mag_pu",
        "unit": "pu",
        "descending": False,
    },
    "line_loading_high": {
        "label": "선로 부하율 높은 순",
        "collection": "lines",
        "field": "loading_percent",
        "unit": "%",
        "descending": True,
    },
    "transformer_loading_high": {
        "label": "변압기 부하율 높은 순",
        "collection": "transformers",
        "field": "loading_percent",
        "unit": "%",
        "descending": True,
    },
}

RankingMetric = Literal[
    "bus_voltage_high",
    "bus_voltage_low",
    "line_loading_high",
    "transformer_loading_high",
]


def _scenario(preview: dict[str, Any]) -> dict[str, Any]:
    metadata = preview["metadata"]
    return {
        "grid_id": metadata["grid_id"],
        "scenario_id": metadata["scenario_id"],
        "snapshot": metadata["snapshot"],
        "synthetic_scenario": metadata["synthetic_scenario"],
        "actual_operating_data": metadata["actual_operating_data"],
    }


def _base(preview: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "status": status,
        "data_source": DATA_SOURCE,
        "scenario": _scenario(preview),
    }


def _normalize_id(value: Any, prefix: str | None = None) -> str:
    text = str(value).strip()
    if prefix:
        match = re.fullmatch(rf"{prefix}\s*[-_#]?\s*(\S+)", text, flags=re.IGNORECASE)
        if match:
            text = match.group(1)
    return text


def _find(items: list[dict[str, Any]], key: str, identifier: str) -> dict[str, Any] | None:
    return next((item for item in items if str(item[key]) == identifier), None)


def pf_summary(preview: dict[str, Any]) -> dict[str, Any]:
    metadata = preview["metadata"]
    summary = preview["summary"]
    controls = [str(bus["control"]) for bus in preview["buses"]]
    return {
        **_base(preview, ANALYZED),
        "pf_status": metadata["pf_status"],
        "pf_converged": summary["pf_converged"],
        "screening_status": metadata["screening_status"],
        "analysis_counts": {
            "buses": metadata["analysis_bus_count"],
            "lines": metadata["analysis_line_count"],
            "transformers": metadata["analysis_transformer_count"],
        },
        "map_counts": {
            "buses": metadata["map_bus_count"],
            "lines": metadata["map_line_count"],
            "transformers": metadata["map_transformer_count"],
        },
        "excluded": {
            "bus_count": len(metadata["excluded_bus_ids"]),
            "bus_ids": list(metadata["excluded_bus_ids"]),
            "line_count": len(metadata["excluded_line_ids"]),
            "line_ids": list(metadata["excluded_line_ids"]),
        },
        "bus_control_counts": {
            "PV": controls.count("PV"),
            "PQ": controls.count("PQ"),
            "Slack": controls.count("Slack"),
        },
        "voltage_pu": {
            "min": summary["voltage_min_pu"],
            "max": summary["voltage_max_pu"],
            "screening_limits": metadata["voltage_limits_pu"],
        },
        "line_max_loading_percent": summary["line_max_loading_percent"],
        "transformer_max_loading_percent": summary["transformer_max_loading_percent"],
        "assumed_screening_violations": summary["screening_violations"],
        "voltage_violation_bus_ids": [
            str(bus["bus_id"]) for bus in preview["buses"] if bus["voltage_violation"]
        ],
        "thermal_violation_line_ids": [
            str(line["line_id"]) for line in preview["lines"] if line["thermal_violation"]
        ],
        "thermal_violation_transformer_ids": [
            str(item["transformer_id"]) for item in preview["transformers"] if item["thermal_violation"]
        ],
        "synthetic_equipment": {
            "generator_count": metadata["generator_count"],
            "load_mw": metadata["load_mw"],
            "load_mvar": metadata["load_mvar"],
            "reactor_count": metadata.get("reactor_count"),
            "reactor_rating_mvar": metadata["reactor_mvar"],
            "reactor_actual_q_mvar": metadata.get("reactor_actual_q_mvar"),
        },
        "power_balance_residual": summary["power_balance_residual"],
        "model_reliability_status": summary["model_reliability_status"],
        "caveats": MODEL_CAVEATS,
    }


def bus_pf(preview: dict[str, Any], bus_id: str) -> dict[str, Any]:
    identifier = _normalize_id(bus_id, prefix="bus")
    bus = _find(preview["buses"], "bus_id", identifier)
    if bus is None:
        if identifier in {str(item) for item in preview["metadata"]["excluded_bus_ids"]}:
            return {
                **_base(preview, EXCLUDED),
                "bus_id": identifier,
                "message": (
                    f"모선 {identifier}은(는) 지도에는 있으나 합성 AC PF 해석 대상(166모선)에서 "
                    "제외되었습니다. 전압 계산값이 없습니다."
                ),
            }
        return {
            **_base(preview, NOT_FOUND),
            "bus_id": identifier,
            "message": f"모선 {identifier}은(는) 몽골 지도 및 PF 결과에 존재하지 않는 ID입니다.",
        }
    return {
        **_base(preview, ANALYZED),
        "bus_id": identifier,
        "voltage_kv": bus["voltage_kv"],
        "v_mag_pu": bus["v_mag_pu"],
        "v_ang_deg": bus["v_ang_deg"],
        "control": bus["control"],
        "voltage_violation": bus["voltage_violation"],
        "screening_limits_pu": preview["metadata"]["voltage_limits_pu"],
        "analysis_status": bus["analysis_status"],
    }


def line_pf(preview: dict[str, Any], line_id: str) -> dict[str, Any]:
    identifier = _normalize_id(line_id)
    line = _find(preview["lines"], "line_id", identifier)
    if line is None:
        if identifier in {str(item) for item in preview["metadata"]["excluded_line_ids"]}:
            return {
                **_base(preview, EXCLUDED),
                "line_id": identifier,
                "message": (
                    f"선로 {identifier}은(는) 지도에는 있으나 합성 AC PF 해석 대상에서 제외되었습니다. "
                    "조류 계산값이 없습니다."
                ),
            }
        return {
            **_base(preview, NOT_FOUND),
            "line_id": identifier,
            "message": f"선로 {identifier}은(는) 몽골 지도 및 PF 결과에 존재하지 않는 ID입니다.",
        }
    return {
        **_base(preview, ANALYZED),
        "line_id": identifier,
        "bus1_id": str(line["bus1_id"]),
        "bus2_id": str(line["bus2_id"]),
        "p0_mw": line["p0_mw"],
        "q0_mvar": line["q0_mvar"],
        "p1_mw": line["p1_mw"],
        "q1_mvar": line["q1_mvar"],
        "rating_mva": line["rating_mva"],
        "loading_percent": line["loading_percent"],
        "assumed_thermal_violation": line["thermal_violation"],
        "sign_convention": SIGN_CONVENTION,
        "analysis_status": line["analysis_status"],
        "model_note": "선로 전기정수·정격과 일부 길이·회선수는 실설비로 검증되지 않았습니다.",
    }


def transformer_pf(preview: dict[str, Any], transformer_id: str) -> dict[str, Any]:
    identifier = _normalize_id(transformer_id)
    item = _find(preview["transformers"], "transformer_id", identifier)
    if item is None:
        return {
            **_base(preview, NOT_FOUND),
            "transformer_id": identifier,
            "message": f"변압기 {identifier}은(는) 몽골 지도 및 PF 결과에 존재하지 않는 ID입니다.",
        }
    return {
        **_base(preview, ANALYZED),
        "transformer_id": identifier,
        "bus1_id": str(item["bus1_id"]),
        "bus2_id": str(item["bus2_id"]),
        "bus1_voltage_kv": item["bus1_voltage_kv"],
        "bus2_voltage_kv": item["bus2_voltage_kv"],
        "p0_mw": item["p0_mw"],
        "q0_mvar": item["q0_mvar"],
        "p1_mw": item["p1_mw"],
        "q1_mvar": item["q1_mvar"],
        "rating_mva": item["rating_mva"],
        "loading_percent": item["loading_percent"],
        "assumed_thermal_violation": item["thermal_violation"],
        "sign_convention": SIGN_CONVENTION,
        "analysis_status": item["analysis_status"],
        "model_note": (
            "변압기는 지도 데이터에서 추정된 설비이며 전기정수(임피던스·정격·탭)는 실설비로 검증되지 않았습니다."
        ),
    }


def pf_ranking(preview: dict[str, Any], metric: str, top_n: int = 5) -> dict[str, Any]:
    config = RANKING_METRICS.get(metric)
    if config is None:
        return {
            **_base(preview, "INVALID_METRIC"),
            "metric": metric,
            "supported_metrics": {key: value["label"] for key, value in RANKING_METRICS.items()},
        }
    requested = int(top_n)
    applied = max(1, min(requested, MAX_TOP_N))
    collection = config["collection"]
    id_key = {"buses": "bus_id", "lines": "line_id", "transformers": "transformer_id"}[collection]
    violation_key = "voltage_violation" if collection == "buses" else "thermal_violation"
    rows = sorted(
        preview[collection],
        key=lambda item: (
            -float(item[config["field"]]) if config["descending"] else float(item[config["field"]]),
            str(item[id_key]),
        ),
    )[:applied]
    ranking = []
    for rank, item in enumerate(rows, start=1):
        entry = {
            "rank": rank,
            id_key: str(item[id_key]),
            "value": item[config["field"]],
            "unit": config["unit"],
            "screening_violation": item[violation_key],
        }
        if collection == "buses":
            entry["voltage_kv"] = item["voltage_kv"]
            entry["control"] = item["control"]
        else:
            entry["bus1_id"] = str(item["bus1_id"])
            entry["bus2_id"] = str(item["bus2_id"])
            entry["rating_mva"] = item["rating_mva"]
        ranking.append(entry)
    return {
        **_base(preview, ANALYZED),
        "metric": metric,
        "metric_label": config["label"],
        "field": config["field"],
        "unit": config["unit"],
        "requested_top_n": requested,
        "top_n": applied,
        "population": len(preview[collection]),
        "ranking": ranking,
        "note": "정렬은 검증된 PF 결과에서 Python 코드로 수행했습니다. 해석 제외 설비는 순위에 포함되지 않습니다.",
    }


def _unavailable(error: Exception) -> dict[str, Any]:
    return {
        "status": DATA_UNAVAILABLE,
        "error": str(error),
        "message": "검증된 몽골 합성 PF 데이터를 사용할 수 없어 결과를 제공하지 않습니다.",
    }


def create_mongolia_tools(load_preview: PreviewLoader) -> list:
    """Build the Mongolia-only read-only tool set around a validated-preview loader."""

    def run(query: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        try:
            preview = load_preview()
        except (FileNotFoundError, ValueError) as exc:
            return _unavailable(exc)
        return query(preview)

    @tool
    def get_mongolia_pf_summary() -> dict[str, Any]:
        """몽골 합성 AC PF 결과 전체 요약을 조회한다. 시나리오, 수렴 상태, 해석 대상/제외 설비 수,
        전압 최소/최대(pu), 최대 선로·변압기 부하율, 가정 스크리닝 위반 건수와 위반 설비 목록,
        합성 발전기·부하·리액터 정보, 모델 신뢰성 상태와 주의사항을 반환한다."""
        return run(pf_summary)

    @tool
    def get_mongolia_bus_pf(bus_id: str) -> dict[str, Any]:
        """몽골 모선 1개의 합성 PF 결과(정격전압 kV, 전압 pu, 위상각 deg, PV/PQ/Slack,
        전압 스크리닝 위반 여부)를 조회한다. bus_id는 '137'처럼 모선 번호 문자열이다.
        status가 EXCLUDED_FROM_ANALYSIS이면 해석 대상 제외, NOT_FOUND이면 존재하지 않는 ID이다."""
        return run(lambda preview: bus_pf(preview, bus_id))

    @tool
    def get_mongolia_line_pf(line_id: str) -> dict[str, Any]:
        """몽골 송전선 1개의 합성 PF 결과(양단 모선, P0/Q0, P1/Q1, 정격 MVA, 부하율 %,
        가정된 열적 스크리닝 위반 여부)를 조회한다. line_id 예: '255335646-1_0'."""
        return run(lambda preview: line_pf(preview, line_id))

    @tool
    def get_mongolia_transformer_pf(transformer_id: str) -> dict[str, Any]:
        """몽골 변압기 1개의 합성 PF 결과(양단 모선과 정격전압, P/Q, 정격 MVA, 부하율 %)를 조회한다.
        transformer_id 예: 'transf_10_0'. 변압기는 추정 설비이며 전기정수는 미검증이다."""
        return run(lambda preview: transformer_pf(preview, transformer_id))

    @tool
    def get_mongolia_pf_ranking(metric: RankingMetric, top_n: int = 5) -> dict[str, Any]:
        """몽골 합성 PF 결과에서 설비 순위를 조회한다. metric은 bus_voltage_high(전압 높은 순),
        bus_voltage_low(전압 낮은 순), line_loading_high(선로 부하율 높은 순),
        transformer_loading_high(변압기 부하율 높은 순) 중 하나다. top_n 기본 5, 최대 10."""
        return run(lambda preview: pf_ranking(preview, metric, top_n))

    return [
        get_mongolia_pf_summary,
        get_mongolia_bus_pf,
        get_mongolia_line_pf,
        get_mongolia_transformer_pf,
        get_mongolia_pf_ranking,
    ]


MONGOLIA_TOOL_NAMES = frozenset(
    {
        "get_mongolia_pf_summary",
        "get_mongolia_bus_pf",
        "get_mongolia_line_pf",
        "get_mongolia_transformer_pf",
        "get_mongolia_pf_ranking",
    }
)
