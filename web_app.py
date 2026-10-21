import json
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import BaseModel

from ai_ems.grid_catalog import (
    KPG_GRID_ID, MONGOLIA_GRID_ID, list_grids, load_mongolia_map,
)
from ai_ems.pf_preview import load_mongolia_pf_preview
from ai_ems.reference_layers import load_mongolia_reference_layers
from ai_ems.kpg_pf_preview import (
    KpgPreviewUnavailable,
    get_kpg_pf_preview,
    validate_kpg_pf_preview,
)
from ai_ems import load_network
from ai_ems.agent.graph import SYSTEM_PROMPT, create_agent_graph
from ai_ems.agent.mongolia_graph import (
    MONGOLIA_SYSTEM_PROMPT,
    DisallowedMongoliaTool,
    requests_analysis,
    create_mongolia_agent_graph,
    detect_unsupported_request,
    unsupported_request_answer,
)
from ai_ems.agent.mongolia_tools import ANALYSIS_TOOL_TYPES, MONGOLIA_TOOL_NAMES
from ai_ems.agent.tools import create_agent_tools
from ai_ems.tools.security_tools import (
    get_limit_unit,
    run_line_contingency,
    select_most_severe_violated_line,
)
from ai_ems.tools.sensitivity_tools import rank_generator_sensitivities
from ai_ems.config import (
    BUS_LOCATION_FILE,
    CASE_FILE,
    MONGOLIA_POWSYBL_REPORTS_DIR,
    MONGOLIA_XIIDM_FILE,
    PROJECT_ROOT,
)
from ai_ems.mongolia_security import (
    ExcludedOutageLine,
    ModelFiles,
    MongoliaModelUnavailable,
    MongoliaSecurityService,
    UnknownOutageLine,
)
from ai_ems.mongolia_sensitivity import InvalidMonitoredLine, SensitivityVerificationFailed

UI_DIR = Path("ui")
MONGOLIA_MAP_FILE = PROJECT_ROOT / "data" / "grids" / "mn-osm-35k-map.json"
MONGOLIA_PF_PREVIEW_FILE = PROJECT_ROOT / "data" / "grids" / "mn35k-synthetic-pf-preview.json"
# Reference-only display layers; deliberately separate from the hash-bound base map.
MONGOLIA_REFERENCE_LAYERS_FILE = PROJECT_ROOT / "data" / "grids" / "mn-osm-35k-reference-layers.json"
KPG_GEOGRAPHY_FILE = PROJECT_ROOT / "data" / "grids" / "kr-geography.json"

network = load_network(CASE_FILE)
agent_graph = create_agent_graph(network)
direct_agent_tools = {
    tool.name: tool
    for tool in create_agent_tools(network)
}
agent_sessions: dict[str, list[Any]] = {}


def _load_validated_mongolia_pf() -> dict[str, Any]:
    # Resolve module paths at call time so the configured assets are always re-validated.
    return load_mongolia_pf_preview(MONGOLIA_PF_PREVIEW_FILE, MONGOLIA_MAP_FILE)


mongolia_agent_sessions: dict[str, list[Any]] = {}

# Mongolia PyPowSyBl Security Analysis: separate model, never the KPG `network`.
_mongolia_security_services: dict[tuple, MongoliaSecurityService] = {}


def _mongolia_security() -> MongoliaSecurityService:
    files = ModelFiles(
        MONGOLIA_XIIDM_FILE,
        MONGOLIA_POWSYBL_REPORTS_DIR,
        MONGOLIA_PF_PREVIEW_FILE,
        MONGOLIA_MAP_FILE,
    )
    key = (files.xiidm, files.reports_dir, files.pf_preview, files.map_file)
    if key not in _mongolia_security_services:
        _mongolia_security_services[key] = MongoliaSecurityService(files)
    return _mongolia_security_services[key]

# Resolve the service lazily; each domain call uses its own validated network.
mongolia_agent_graph = create_mongolia_agent_graph(
    _load_validated_mongolia_pf, load_security=_mongolia_security,
)

bus_locations = pd.read_csv(BUS_LOCATION_FILE)

bus_location_map = {
    int(row["bus_id"]): {
        "latitude": float(row["Latitude"]),
        "longitude": float(row["Longitude"]),
        "name_korean": row["name_Korean"],
        "name_english": row["name_English"],
    }
    for _, row in bus_locations.iterrows()
}

app = FastAPI(
    title="AI-EMS Agent",
)


class ChatRequest(BaseModel):
    session_id: str
    message: str


class GeneratorContingencyRequest(BaseModel):
    generator_id: str
    slack_mode: Literal["single", "distributed"] = "single"


@app.get("/")
def index():
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
    }


@app.get("/api/grids")
def grid_catalog():
    return {
        "grids": list_grids(
            MONGOLIA_MAP_FILE,
            MONGOLIA_PF_PREVIEW_FILE,
            _mongolia_security().status(),
        ),
        "default_grid_id": KPG_GRID_ID,
    }


@app.get("/api/grids/{grid_id}/map")
def grid_map(grid_id: str):
    if grid_id == KPG_GRID_ID:
        return network_map()
    if grid_id == MONGOLIA_GRID_ID:
        try:
            return load_mongolia_map(MONGOLIA_MAP_FILE)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=404, detail="Unknown grid ID")


@app.get("/api/grids/mn-osm-35k/pf-preview")
def mongolia_pf_preview():
    try:
        return load_mongolia_pf_preview(
            MONGOLIA_PF_PREVIEW_FILE,
            MONGOLIA_MAP_FILE,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/grids/mn-osm-35k/reference-layers")
def mongolia_reference_layers():
    try:
        return load_mongolia_reference_layers(MONGOLIA_REFERENCE_LAYERS_FILE, MONGOLIA_MAP_FILE)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/grids/mn-osm-35k/security-status")
def mongolia_security_status():
    return {"grid_id": MONGOLIA_GRID_ID, **_mongolia_security().status()}


@app.get("/api/grids/mn-osm-35k/security-analysis")
def mongolia_security_analysis(outage_line_id: str):
    try:
        return _mongolia_security().run_line_contingency(outage_line_id)
    except MongoliaModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"몽골 Security Analysis를 사용할 수 없습니다: {exc}") from exc
    except UnknownOutageLine as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExcludedOutageLine as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"몽골 Security Analysis 실행 실패: {exc}") from exc


@app.get("/api/grids/mn-osm-35k/sensitivity-analysis")
def mongolia_sensitivity_analysis(
    outage_line_id: str,
    monitored_line_id: str | None = None,
    top_n: int = 10,
):
    try:
        return _mongolia_security().run_line_sensitivity(
            outage_line_id,
            monitored_line_id or None,
            top_n=top_n,
        )
    except MongoliaModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"몽골 Sensitivity Analysis를 사용할 수 없습니다: {exc}") from exc
    except UnknownOutageLine as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ExcludedOutageLine, InvalidMonitoredLine) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SensitivityVerificationFailed as exc:
        raise HTTPException(status_code=422, detail=f"민감도 검증 실패(결과를 반환하지 않음): {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"몽골 Sensitivity Analysis 실행 실패: {exc}") from exc


@app.get("/api/grids/kr-kpg193/pf-preview")
def kpg_pf_preview():
    try:
        return validate_kpg_pf_preview(get_kpg_pf_preview(), network_map())
    except KpgPreviewUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"KPG 기본 PF 미리보기를 사용할 수 없습니다: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/network-summary")
def network_summary():
    return {
        "buses": len(network.get_buses()),
        "lines": len(network.get_lines()),
        "generators": len(network.get_generators()),
        "loads": len(network.get_loads()),
    }


@app.get("/api/network-map")
def network_map():
    buses = []
    voltage_levels = network.get_voltage_levels()
    nominal_voltage_by_bus = {
        _bus_number(voltage_level_id): _optional_float(row["nominal_v"])
        for voltage_level_id, row in voltage_levels.iterrows()
    }

    for bus_id, location in bus_location_map.items():
        buses.append(
            {
                "bus_id": bus_id,
                "voltage_kv": nominal_voltage_by_bus.get(bus_id),
                **location,
            }
        )

    lines_df = network.get_lines()
    lines = []

    for line_id, row in lines_df.iterrows():
        voltage_level1_id = row["voltage_level1_id"]
        voltage_level2_id = row["voltage_level2_id"]

        bus1_id = _bus_number(voltage_level1_id)
        bus2_id = _bus_number(voltage_level2_id)

        if bus1_id not in bus_location_map or bus2_id not in bus_location_map:
            continue

        lines.append(
            {
                "line_id": line_id,
                "bus1_id": bus1_id,
                "bus2_id": bus2_id,
                "voltage_kv": nominal_voltage_by_bus.get(
                    _bus_number(voltage_level1_id)
                ),
            }
        )

    transformers_df = network.get_2_windings_transformers()
    transformers = []
    for transformer_id, row in transformers_df.iterrows():
        bus1_id = _bus_number(row["voltage_level1_id"])
        bus2_id = _bus_number(row["voltage_level2_id"])
        if bus1_id not in bus_location_map or bus2_id not in bus_location_map:
            continue
        transformers.append({
            "transformer_id": transformer_id,
            "bus1_id": bus1_id,
            "bus2_id": bus2_id,
            "bus1_voltage_kv": nominal_voltage_by_bus.get(bus1_id),
            "bus2_voltage_kv": nominal_voltage_by_bus.get(bus2_id),
        })

    generators_df = network.get_generators(all_attributes=True)
    generators = []

    for generator_id, row in generators_df.iterrows():
        bus_id = _bus_number(row["voltage_level_id"])
        location = bus_location_map.get(bus_id)
        if location is None:
            continue

        generators.append(
            {
                "generator_id": generator_id,
                "bus_id": bus_id,
                "connected": bool(row.get("connected", True)),
                "target_p_mw": _optional_float(row.get("target_p")),
                "min_p_mw": _optional_float(row.get("min_p")),
                "max_p_mw": _optional_float(row.get("max_p")),
                **location,
            }
        )

    result = {
        "buses": buses,
        "lines": lines,
        "transformers": transformers,
        "generators": generators,
    }
    if KPG_GEOGRAPHY_FILE.is_file():
        try:
            geography = json.loads(KPG_GEOGRAPHY_FILE.read_text(encoding="utf-8"))
            if (
                isinstance(geography, dict)
                and isinstance(geography.get("country_polygons"), list)
                and isinstance(geography.get("admin_regions"), list)
            ):
                result["country_polygons"] = geography["country_polygons"]
                result["admin_regions"] = geography["admin_regions"]
                result["geography_attribution"] = geography.get("attribution", "")
        except (OSError, ValueError):
            pass  # Missing/invalid optional basemap must not break KPG analysis.
    return result


@app.get("/api/security-analysis")
def security_analysis(
    outage_line_id: str,
    monitored_line_id: str | None = None,
):
    monitored_line_ids = [monitored_line_id] if monitored_line_id is not None else None

    result = run_line_contingency(
        network,
        outage_line_id=outage_line_id,
        monitored_line_ids=monitored_line_ids,
    )

    return _security_result_for_ui(result)


@app.get("/api/sensitivity-analysis")
def sensitivity_analysis(
    outage_line_id: str,
    monitored_line_id: str | None = None,
    top_n: int = 5,
):
    if monitored_line_id is None:
        security_result = run_line_contingency(
            network,
            outage_line_id=outage_line_id,
        )

        if security_result["post_status"] != "CONVERGED":
            return {
                "analysis_status": "SECURITY_NOT_CONVERGED",
                "outage_line_id": outage_line_id,
                "monitored_line_id": None,
                "candidate_count": 0,
                "candidates": [],
                "message": (
                    "상정사고 분석이 수렴하지 않아 " "민감도 분석을 수행할 수 없습니다."
                ),
            }

        selected = select_most_severe_violated_line(
            network,
            security_result,
        )

        if selected is None:
            return {
                "outage_line_id": outage_line_id,
                "monitored_line_id": None,
                "candidate_count": 0,
                "candidates": [],
                "analysis_status": "NO_OVERLOAD",
                "message": "과부하 선로가 확인되지 않아 민감도 분석 대상을 선택할 수 없습니다.",
            }

        monitored_line_id = selected["equipment_id"]

    result = rank_generator_sensitivities(
        network,
        outage_line_id=outage_line_id,
        monitored_line_id=monitored_line_id,
        top_n=top_n,
    )

    return _sensitivity_result_for_ui(result)


@app.post("/api/generator-contingency-analysis")
def generator_contingency_analysis(request: GeneratorContingencyRequest):
    generator_id = request.generator_id.strip()
    if not generator_id:
        raise HTTPException(
            status_code=400,
            detail="generator_id is required.",
        )

    tool = direct_agent_tools["generator_contingency_analysis"]

    try:
        result = tool.invoke(
            {
                "generator_id": generator_id,
                "slack_mode": request.slack_mode,
                "top_n_overloads": 5,
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Generator contingency analysis failed: {exc}",
        ) from exc

    return _generator_contingency_result_for_ui(result)


@app.post("/api/chat")
def chat(request: ChatRequest):
    session_id = request.session_id.strip()
    user_message = request.message.strip()

    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="session_id is required.",
        )

    if not user_message:
        raise HTTPException(
            status_code=400,
            detail="message is required.",
        )

    messages = agent_sessions.get(session_id)
    if messages is None:
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
        ]

    input_messages = [
        *messages,
        HumanMessage(content=user_message),
    ]

    try:
        result = agent_graph.invoke(
            {
                "messages": input_messages,
            }
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Agent execution failed: {exc}",
        ) from exc

    output_messages = result["messages"]
    agent_sessions[session_id] = output_messages

    generated_messages = output_messages[len(input_messages) :]

    answer = _final_ai_answer(output_messages)
    ui_updates = _extract_ui_updates(generated_messages)

    return {
        "session_id": session_id,
        "answer": answer,
        "ui_updates": ui_updates,
    }


@app.post("/api/grids/{grid_id}/chat")
def grid_chat(grid_id: str, request: ChatRequest):
    if grid_id == KPG_GRID_ID:
        return {**chat(request), "grid_id": KPG_GRID_ID}
    if grid_id != MONGOLIA_GRID_ID:
        raise HTTPException(status_code=404, detail="Unknown grid ID")
    return mongolia_chat(request)


def mongolia_chat(request: ChatRequest) -> dict[str, Any]:
    session_id = request.session_id.strip()
    user_message = request.message.strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required.")
    if not user_message:
        raise HTTPException(status_code=400, detail="message is required.")

    # Refuse to start a conversation when the validated PF data is unavailable,
    # so the LLM is never asked to answer without verified numbers.
    try:
        _load_validated_mongolia_pf()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"몽골 합성 PF 데이터를 사용할 수 없습니다: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"몽골 합성 PF 데이터 검증에 실패했습니다: {exc}",
        ) from exc

    messages = mongolia_agent_sessions.get(session_id) or [
        SystemMessage(content=MONGOLIA_SYSTEM_PROMPT),
    ]
    input_messages = [*messages, HumanMessage(content=user_message)]

    unsupported = detect_unsupported_request(user_message)
    if unsupported:
        answer = unsupported_request_answer(unsupported)
        mongolia_agent_sessions[session_id] = [*input_messages, AIMessage(content=answer)]
        return {
            "session_id": session_id,
            "grid_id": MONGOLIA_GRID_ID,
            "answer": answer,
            "ui_updates": [],
            "tools_used": [],
            "unsupported_requests": unsupported,
        }

    try:
        result = mongolia_agent_graph.invoke({"messages": input_messages})
    except DisallowedMongoliaTool as exc:
        raise HTTPException(status_code=502, detail=f"몽골 Agent가 허용되지 않은 Tool 호출을 시도했습니다: {exc}") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"몽골 AI Agent 실행 실패(LLM 연결 또는 응답 오류): {exc}",
        ) from exc

    output_messages = result["messages"]
    generated_messages = output_messages[len(input_messages):]
    tools_used = [
        call["name"]
        for message in generated_messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    disallowed = sorted(set(tools_used) - MONGOLIA_TOOL_NAMES)
    if disallowed:
        raise HTTPException(
            status_code=502,
            detail=f"몽골 Agent가 허용되지 않은 Tool 호출을 시도했습니다: {', '.join(disallowed)}",
        )
    answer = _final_ai_answer(output_messages)
    if not answer.strip():
        raise HTTPException(status_code=502, detail="몽골 AI Agent가 빈 응답을 반환했습니다.")

    ui_updates, analysis_notes = _mongolia_analysis_evidence(generated_messages)
    if analysis_notes:
        # Analysis statements and numbers are derived from actual tool artifacts only.
        answer = "\n\n".join(analysis_notes) + "\n\n합성 모델 결과이며 실제 몽골 운영 데이터가 아닙니다."
    elif requests_analysis(user_message) or ("분석" in user_message and not tools_used):
        answer = "이번 요청에서 검증된 분석 Tool 결과가 없습니다. 계산 수행을 확인할 수 없습니다. 사고 선로의 원본 ID와 실행할 분석을 지정해 주세요."
    output_messages[-1] = AIMessage(content=answer, id=output_messages[-1].id)
    mongolia_agent_sessions[session_id] = output_messages
    return {
        "session_id": session_id,
        "grid_id": MONGOLIA_GRID_ID,
        "answer": answer,
        "ui_updates": ui_updates,
        "tools_used": tools_used,
        "unsupported_requests": [],
    }


def _mongolia_analysis_evidence(messages: list[Any]) -> tuple[list[dict], list[str]]:
    """Only paired, successful domain ToolMessage artifacts can update Mongolia UI.

    Do not pass these results through the KPG network/generator adapters.
    Failures override model claims even if it says the computation succeeded.
    """
    pending: dict[str, dict] = {}
    updates, notes = [], []
    for message in messages:
        if isinstance(message, AIMessage):
            pending.update({c["id"]: c for c in message.tool_calls if c["name"] in ANALYSIS_TOOL_TYPES})
        elif isinstance(message, ToolMessage) and message.tool_call_id in pending:
            call = pending.pop(message.tool_call_id)
            kind = ANALYSIS_TOOL_TYPES[call["name"]]
            payload = message.artifact
            valid = (
                message.name == call["name"] and message.status != "error"
                and isinstance(payload, dict) and payload.get("grid_id") == MONGOLIA_GRID_ID
                and payload.get("outage_line_id") == call["args"].get("outage_line_id", "").strip()
                and not payload.get("error")
            )
            if valid and kind == "security" and "post_status" in payload:
                updates.append({"type": kind, "grid_id": MONGOLIA_GRID_ID, "result": payload})
                note = f"선로 {payload['outage_line_id']} N-1: {payload['post_status']}."
                if payload.get("violation_assessment_available") is True:
                    note += f" 위반 {payload['violated_equipment_count']}건(합성 스크리닝 기준)."
                else:
                    note += " 사고 후 PF 비수렴: 위반 판정 불가."
                if payload.get("island_detected"):
                    note += f" 분리 {payload['isolated_bus_count']}모선, 주 계통만 계산하며 분리 섬 PF는 미실행."
                notes.append(note)
            elif valid and kind == "sensitivity" and payload.get("analysis_status") in {
                "COMPLETED", "NOT_EVALUATED_ISLANDING", "POST_CONTINGENCY_NOT_CONVERGED",
            }:
                updates.append({"type": kind, "grid_id": MONGOLIA_GRID_ID, "result": payload})
                if payload["analysis_status"] == "COMPLETED":
                    selection = payload["monitored_line_selection"]["mode"]
                    note = (f"사고 선로 {payload['outage_line_id']}의 주입 민감도: 감시 선로 "
                            f"{payload['monitored_line_id']} ({'자동 선택' if selection == 'auto' else '사용자 선택'}), "
                            f"후보 {payload['candidate_count']}개.")
                    for c in payload["candidates"]:
                        note += f"\n{c['rank']}. {c['generator_id']} (모선 {c['bus_id']}): {c['sensitivity']:.6f} MW/MW"
                    notes.append(note + "\n합성 발전기 주입 민감도이며 실제 발전소·재급전 권고가 아닙니다.")
                else:
                    notes.append(f"{payload['analysis_status']}: {payload['message']} 후보 0개, 순위 없음.")
            else:
                reason = payload.get("error") if isinstance(payload, dict) else "Tool 호출 또는 입력 검증 오류"
                notes.append(f"{call['name']} 실패: {reason or '결과 검증 실패'}. 검증된 분석 결과를 제공하지 않습니다.")
    if pending:
        notes.append("분석 Tool의 실제 실행 결과가 없어 성공을 확인할 수 없습니다.")
    return updates, notes


def _security_result_for_ui(
    result: dict[str, Any],
) -> dict[str, Any]:
    violations = []

    for item in result["violated_equipment"]:
        limit_type = item.get("limit_type")

        violations.append(
            {
                "equipment_id": item["equipment_id"],
                "limit_type": limit_type,
                "limit_name": item.get("limit_name"),
                "unit": item.get("unit") or get_limit_unit(limit_type),
                "limit": item.get("limit"),
                "value": item.get("value"),
                "violation_amount": item.get("violation_amount"),
                "violation_direction": item.get("violation_direction"),
                "loading_percent": item.get("loading_percent"),
            }
        )

    post_status = result["post_status"]
    violation_assessment_available = post_status == "CONVERGED"

    return {
        "outage_line_id": result["outage_line_id"],
        "base_converged": result["base_converged"],
        "post_status": post_status,
        "analysis_status": (
            "COMPLETED" if violation_assessment_available else "SECURITY_NOT_CONVERGED"
        ),
        "violation_assessment_available": violation_assessment_available,
        "violated_equipment_count": result["violated_equipment_count"],
        "violations": violations,
    }


def _agent_security_result_for_ui(
    result: dict[str, Any],
) -> dict[str, Any]:
    violation = result.get("violation")
    violations = []

    if violation is not None:
        violations.append(
            {
                "equipment_id": violation.get("equipment_id"),
                "limit_type": violation.get("limit_type"),
                "limit_name": None,
                "unit": violation.get("unit"),
                "limit": violation.get("limit"),
                "value": violation.get("post_value"),
                "violation_amount": violation.get("violation_amount"),
                "violation_direction": violation.get("violation_direction"),
                "loading_percent": violation.get("loading_percent"),
            }
        )

    post_status = result.get("post_status")
    violation_assessment_available = post_status == "CONVERGED"

    return {
        "outage_line_id": result.get("outage_line_id"),
        "base_converged": result.get("base_converged"),
        "post_status": post_status,
        "analysis_status": (
            "COMPLETED" if violation_assessment_available else "SECURITY_NOT_CONVERGED"
        ),
        "violation_assessment_available": violation_assessment_available,
        "violated_equipment_count": result.get(
            "violated_equipment_count",
            len(violations),
        ),
        "violations": violations,
    }


def _sensitivity_result_for_ui(
    result: dict[str, Any],
) -> dict[str, Any]:
    generators = network.get_generators(all_attributes=True)
    candidates = []

    for candidate in result.get("candidates", []):
        generator_id = candidate["generator_id"]
        row = generators.loc[generator_id]
        voltage_level_id = row["voltage_level_id"]
        bus_id = _bus_number(voltage_level_id)
        location = bus_location_map.get(bus_id)

        candidates.append(
            {
                **candidate,
                "bus_id": bus_id,
                "latitude": (location["latitude"] if location else None),
                "longitude": (location["longitude"] if location else None),
                "name_korean": (location["name_korean"] if location else None),
                "name_english": (location["name_english"] if location else None),
            }
        )

    return {
        "outage_line_id": result.get("outage_line_id"),
        "monitored_line_id": result.get("monitored_line_id"),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "target_selection": result.get("target_selection"),
        "analysis_status": "COMPLETED",
    }


def _generator_contingency_result_for_ui(
    result: dict[str, Any],
) -> dict[str, Any]:
    outage = result.get("outage_generator", {})
    outage_bus_id = _bus_number(outage.get("bus_id"))
    outage_location = bus_location_map.get(outage_bus_id)

    outage_generator = {
        **outage,
        "bus_id": outage_bus_id,
        "latitude": outage_location["latitude"] if outage_location else None,
        "longitude": outage_location["longitude"] if outage_location else None,
        "name_korean": outage_location["name_korean"] if outage_location else None,
        "name_english": outage_location["name_english"] if outage_location else None,
    }

    generator_changes = []
    for item in result.get("top_generator_changes", []):
        bus_id = _bus_number(item.get("bus_id"))
        location = bus_location_map.get(bus_id)
        generator_changes.append(
            {
                **item,
                "bus_id": bus_id,
                "latitude": location["latitude"] if location else None,
                "longitude": location["longitude"] if location else None,
                "name_korean": location["name_korean"] if location else None,
                "name_english": location["name_english"] if location else None,
            }
        )

    return {
        "generator_id": result.get("generator_id"),
        "slack_mode": result.get("slack_mode"),
        "balance_type": result.get("balance_type"),
        "base_converged": result.get("base_converged"),
        "post_contingency_converged": result.get("post_contingency_converged"),
        "outage_generator": outage_generator,
        "post_loadflow": result.get("post_loadflow", {}),
        "top_generator_changes": generator_changes,
        "major_overloads": result.get("major_overloads", []),
        "security": result.get("security", {}),
    }


def _extract_ui_updates(
    messages: list[Any],
) -> list[dict[str, Any]]:
    tool_names: dict[str, str] = {}
    updates: list[dict[str, Any]] = []

    for message in messages:
        if isinstance(message, AIMessage):
            for tool_call in message.tool_calls:
                tool_names[tool_call["id"]] = tool_call["name"]
            continue

        if not isinstance(message, ToolMessage):
            continue

        tool_name = getattr(message, "name", None) or tool_names.get(
            message.tool_call_id
        )
        payload = _parse_tool_payload(message.content)

        if not isinstance(payload, dict):
            continue

        if tool_name == "line_contingency":
            updates.append(
                {
                    "type": "security",
                    "result": (_agent_security_result_for_ui(payload)),
                }
            )

        elif tool_name == "generator_sensitivity":
            updates.append(
                {
                    "type": "sensitivity",
                    "result": (_sensitivity_result_for_ui(payload)),
                }
            )

        elif tool_name == "generator_contingency_analysis":
            updates.append(
                {
                    "type": "generator_contingency",
                    "result": _generator_contingency_result_for_ui(payload),
                }
            )

    return updates


def _parse_tool_payload(
    content: Any,
) -> Any:
    if isinstance(content, (dict, list)):
        return content

    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None

    return None


def _final_ai_answer(
    messages: list[Any],
) -> str:
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue

        if message.tool_calls:
            continue

        if isinstance(message.content, str):
            return message.content

        return str(message.content)

    return ""


def _bus_number(value: Any) -> int:
    text = str(value)
    if text.startswith("VL-"):
        text = text[3:]
    return int(text.split("_", 1)[0])


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
