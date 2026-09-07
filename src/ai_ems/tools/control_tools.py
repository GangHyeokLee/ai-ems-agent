from __future__ import annotations

from math import hypot
from pathlib import Path
from typing import Any

from ai_ems.network import load_network, run_ac_load_flow
from ai_ems.tools.security_tools import run_line_contingency


def validate_balanced_redispatch(
    case_path: str | Path,
    outage_line_id: str,
    monitored_line_id: str,
    up_generator_id: str,
    down_generator_id: str,
    delta_mw: float,
) -> dict[str, Any]:
    """Apply a balanced generator redispatch after a line outage and validate it with AC power flow.

    The up generator target is increased by ``delta_mw`` and the down generator
    target is decreased by the same amount, so the requested net active-power
    change is zero. A fresh network is loaded for the what-if calculation so the
    caller's base network state is not mutated.
    """
    if delta_mw <= 0:
        raise ValueError("delta_mw must be greater than zero.")
    if up_generator_id == down_generator_id:
        raise ValueError("up_generator_id and down_generator_id must be different.")

    base_network = load_network(case_path)
    lines = base_network.get_lines()
    generators = base_network.get_generators()

    _require_id(lines.index, outage_line_id, "Outage line")
    _require_id(lines.index, monitored_line_id, "Monitored line")
    _require_id(generators.index, up_generator_id, "Up generator")
    _require_id(generators.index, down_generator_id, "Down generator")

    security_result = run_line_contingency(
        base_network,
        outage_line_id=outage_line_id,
        monitored_line_ids=[monitored_line_id],
    )

    if security_result["post_status"] != "CONVERGED":
        raise RuntimeError(
            f"Post-contingency Security Analysis did not converge: "
            f"{security_result['post_status']}"
        )

    monitored_branches = security_result["monitored_branches"]
    if not monitored_branches:
        raise RuntimeError(f"No monitored result returned for {monitored_line_id}.")

    before_mva = float(monitored_branches[0]["apparent_power_mva"])
    limit_mva = _find_apparent_power_limit(
        security_result,
        monitored_line_id,
    )

    control_network = load_network(case_path)
    control_network.update_lines(
        id=outage_line_id,
        connected1=False,
        connected2=False,
    )

    control_generators = control_network.get_generators()
    up_before = float(control_generators.loc[up_generator_id, "target_p"])
    down_before = float(control_generators.loc[down_generator_id, "target_p"])
    up_after = up_before + float(delta_mw)
    down_after = down_before - float(delta_mw)

    _validate_generator_target(
        control_generators,
        up_generator_id,
        up_after,
    )
    _validate_generator_target(
        control_generators,
        down_generator_id,
        down_after,
    )

    control_network.update_generators(
        id=[up_generator_id, down_generator_id],
        target_p=[up_after, down_after],
    )

    loadflow_result = run_ac_load_flow(control_network)

    response: dict[str, Any] = {
        "analysis_type": "Balanced Redispatch AC Validation",
        "outage_line_id": outage_line_id,
        "monitored_line_id": monitored_line_id,
        "redispatch": {
            "up_generator_id": up_generator_id,
            "down_generator_id": down_generator_id,
            "delta_mw": float(delta_mw),
            "up_target_before_mw": up_before,
            "up_target_after_mw": up_after,
            "down_target_before_mw": down_before,
            "down_target_after_mw": down_after,
            "net_requested_change_mw": 0.0,
        },
        "post_contingency": {
            "converged": True,
            "apparent_power_mva": before_mva,
        },
        "after_redispatch": {
            "converged": loadflow_result["converged"],
            "apparent_power_mva": None,
        },
        "limit_mva": limit_mva,
        "apparent_power_change_mva": None,
        "improvement_mva": None,
        "improved": None,
        "loading_before_percent": (
            before_mva / limit_mva * 100.0
            if limit_mva is not None and limit_mva > 0
            else None
        ),
        "loading_after_percent": None,
        "violation_remaining": None,
    }

    if not loadflow_result["converged"]:
        return response

    line = control_network.get_lines().loc[monitored_line_id]
    after_mva = max(
        hypot(float(line["p1"]), float(line["q1"])),
        hypot(float(line["p2"]), float(line["q2"])),
    )

    change_mva = after_mva - before_mva
    response["after_redispatch"]["apparent_power_mva"] = after_mva
    response["apparent_power_change_mva"] = change_mva
    response["improvement_mva"] = before_mva - after_mva
    response["improved"] = after_mva < before_mva

    if limit_mva is not None and limit_mva > 0:
        response["loading_after_percent"] = after_mva / limit_mva * 100.0
        response["violation_remaining"] = after_mva > limit_mva

    return response


def _find_apparent_power_limit(
    security_result: dict[str, Any],
    monitored_line_id: str,
) -> float | None:
    for item in security_result.get("violated_equipment", []):
        if (
            item.get("equipment_id") == monitored_line_id
            and item.get("limit_type") == "APPARENT_POWER"
            and item.get("limit") is not None
        ):
            return float(item["limit"])
    return None


def _validate_generator_target(generators, generator_id: str, target_p: float) -> None:
    row = generators.loc[generator_id]

    if "min_p" in generators.columns:
        min_p = row.get("min_p")
        if min_p is not None and target_p < float(min_p):
            raise ValueError(
                f"Requested target_p {target_p:.3f} MW for {generator_id} "
                f"is below min_p {float(min_p):.3f} MW."
            )

    if "max_p" in generators.columns:
        max_p = row.get("max_p")
        if max_p is not None and target_p > float(max_p):
            raise ValueError(
                f"Requested target_p {target_p:.3f} MW for {generator_id} "
                f"is above max_p {float(max_p):.3f} MW."
            )


def _require_id(index, equipment_id: str, label: str) -> None:
    if equipment_id not in index:
        raise ValueError(f"{label} not found: {equipment_id}")
