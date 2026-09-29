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

from ai_ems import load_network
from ai_ems.agent.graph import SYSTEM_PROMPT, create_agent_graph
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
)

UI_DIR = Path("ui")

network = load_network(CASE_FILE)
agent_graph = create_agent_graph(network)
direct_agent_tools = {
    tool.name: tool
    for tool in create_agent_tools(network)
}
agent_sessions: dict[str, list[Any]] = {}

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

    for bus_id, location in bus_location_map.items():
        buses.append(
            {
                "bus_id": bus_id,
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
            }
        )

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

    return {
        "buses": buses,
        "lines": lines,
        "generators": generators,
    }


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
