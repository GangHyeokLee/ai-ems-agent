from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from ai_ems.config import CASE_FILE
from ai_ems.tools.control_tools import validate_balanced_redispatch
from ai_ems.tools.network_tools import (
    get_line,
    get_network_summary,
    list_generators,
    list_lines,
)
from ai_ems.tools.security_tools import (
    get_limit_unit,
    run_line_contingency,
    select_most_severe_violated_line,
    select_primary_violation,
)
from ai_ems.tools.sensitivity_tools import rank_generator_sensitivities
from ai_ems.tools.workflow_tools import analyze_contingency_response


def create_agent_tools(
    network,
    case_path: str | Path = CASE_FILE,
):
    @tool
    def network_summary() -> dict[str, Any]:
        """Get a summary of the current power network."""
        return get_network_summary(network)

    @tool
    def line_list(limit: int = 10) -> list[dict[str, Any]]:
        """List transmission lines in the current power network."""
        return list_lines(network, limit=limit)

    @tool
    def line_detail(line_id: str) -> dict[str, Any]:
        """Get detailed information for one transmission line."""
        return get_line(network, line_id)

    @tool
    def generator_list(
        limit: int = 10,
        connected_only: bool = True,
    ) -> list[dict[str, Any]]:
        """List generators in the current power network."""
        return list_generators(
            network,
            limit=limit,
            connected_only=connected_only,
        )

    @tool
    def line_contingency(
        outage_line_id: str,
        monitored_line_id: str | None = None,
    ) -> dict[str, Any]:
        """Analyze a transmission line outage and optionally monitor one line."""

        monitored_line_ids = (
            [monitored_line_id] if monitored_line_id is not None else None
        )

        result = run_line_contingency(
            network,
            outage_line_id=outage_line_id,
            monitored_line_ids=monitored_line_ids,
        )

        equipment = select_primary_violation(result)
        branch = (
            result["monitored_branches"][0] if result["monitored_branches"] else None
        )

        return {
            "analysis_type": "AC Security Analysis",
            "outage_line_id": result["outage_line_id"],
            "base_converged": result["base_converged"],
            "post_status": result["post_status"],
            "violated_equipment_count": result["violated_equipment_count"],
            "violation": (
                {
                    "equipment_id": equipment["equipment_id"],
                    "limit_type": equipment.get("limit_type"),
                    "unit": equipment.get("unit")
                    or get_limit_unit(equipment.get("limit_type")),
                    "limit": equipment.get("limit"),
                    "post_value": equipment.get("value"),
                    "violation_amount": equipment.get("violation_amount"),
                    "violation_direction": equipment.get("violation_direction"),
                    "loading_percent": equipment.get("loading_percent"),
                }
                if equipment is not None
                else None
            ),
            "monitored_branch": (
                {
                    "line_id": branch["line_id"],
                    "base_apparent_power_flow_mva": (
                        branch["base"]["apparent_power_mva"]
                    ),
                    "post_apparent_power_flow_mva": branch["apparent_power_mva"],
                }
                if branch is not None
                else None
            ),
            "pre_violated_equipment_count": result["pre_violated_equipment_count"],
            "violation_comparison": result["violation_comparison"],
        }

    @tool
    def generator_sensitivity(
        outage_line_id: str,
        monitored_line_id: str | None = None,
        top_n: int = 5,
    ) -> dict[str, Any]:
        """Rank generators by post-contingency branch-flow sensitivity.

        If monitored_line_id is omitted, select the most severely overloaded
        transmission line from Security Analysis.
        """

        auto_selected = monitored_line_id is None

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
                raise ValueError(
                    "No overloaded transmission line found. "
                    "Please specify monitored_line_id."
                )

            monitored_line_id = selected["equipment_id"]

        result = rank_generator_sensitivities(
            network,
            outage_line_id=outage_line_id,
            monitored_line_id=monitored_line_id,
            top_n=top_n,
        )

        return {
            **result,
            "target_selection": (
                "most_severe_violation" if auto_selected else "user_specified"
            ),
        }

    @tool
    def balanced_redispatch_validation(
        outage_line_id: str,
        monitored_line_id: str,
        up_generator_id: str,
        down_generator_id: str,
        delta_mw: float,
    ) -> dict[str, Any]:
        """Validate one explicitly specified balanced redispatch with AC power flow.

        Use this tool only when the up generator, down generator, and redispatch
        amount are explicitly specified. Do not use it to generate or choose
        corrective-action candidates. For contingency analysis with automatic
        response-candidate generation, use contingency_response_analysis.
        """
        return validate_balanced_redispatch(
            case_path=case_path,
            outage_line_id=outage_line_id,
            monitored_line_id=monitored_line_id,
            up_generator_id=up_generator_id,
            down_generator_id=down_generator_id,
            delta_mw=delta_mw,
        )

    @tool
    def contingency_response_analysis(
        outage_line_id: str,
        delta_mw: float = 10.0,
        top_n: int = 3,
    ) -> dict[str, Any]:
        """Preferred high-level tool for contingency analysis and corrective-action review.

        Given an outage line, automatically select the most severely overloaded line,
        generate sensitivity-based balanced redispatch candidates, and validate them
        with AC power flow. Use this when the user asks to analyze an outage and review
        or recommend response candidates. This is candidate analysis, not optimization.
        """

        return analyze_contingency_response(
            case_path=case_path,
            outage_line_id=outage_line_id,
            delta_mw=delta_mw,
            top_n=top_n,
        )

    return [
        network_summary,
        line_list,
        line_detail,
        generator_list,
        line_contingency,
        generator_sensitivity,
        balanced_redispatch_validation,
        contingency_response_analysis,
    ]
