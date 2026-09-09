import json
from pathlib import Path
from typing import Any

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

        bus1_id = int(voltage_level1_id.replace("VL-", ""))
        bus2_id = int(voltage_level2_id.replace("VL-", ""))

        if bus1_id not in bus_location_map or bus2_id not in bus_location_map:
            continue

        lines.append(
            {
                "line_id": line_id,
                "bus1_id": bus1_id,
                "bus2_id": bus2_id,
            }
        )

    return {
        "buses": buses,
        "lines": lines,
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
                "message": "No overloaded transmission line found.",
            }

        monitored_line_id = selected["equipment_id"]

    result = rank_generator_sensitivities(
        network,
        outage_line_id=outage_line_id,
        monitored_line_id=monitored_line_id,
        top_n=top_n,
    )

    return _sensitivity_result_for_ui(result)


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

    return {
        "outage_line_id": result["outage_line_id"],
        "base_converged": result["base_converged"],
        "post_status": result["post_status"],
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

    return {
        "outage_line_id": result.get("outage_line_id"),
        "base_converged": result.get("base_converged"),
        "post_status": result.get("post_status"),
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
        bus_id = int(voltage_level_id.replace("VL-", ""))
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
