from typing import Any
from langchain_core.tools import tool
from ai_ems.tools.network_tools import (
    get_line,
    get_network_summary,
    list_generators,
    list_lines,
)
from ai_ems.tools.security_tools import run_line_contingency
from ai_ems.tools.sensitivity_tools import rank_generator_sensitivities


def create_agent_tools(network):
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
        """Get detailed information for a transmission line."""
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

        result = run_line_contingency(...)

        return {
            "analysis_type": "AC Security Analysis",
            "outage_line_id": result["outage_line_id"],
            "base_converged": result["base_converged"],
            "post_status": result["post_status"],
            "violated_equipment_count": result["violated_equipment_count"],
            "violated_equipment": result["violated_equipment"],
            "monitored_branches": [
                {
                    "line_id": item["line_id"],
                    "base_apparent_power_mva": item["base"]["apparent_power_mva"],
                    "post_apparent_power_mva": item["apparent_power_mva"],
                }
                for item in result["monitored_branches"]
            ],
        }

    @tool
    def generator_sensitivity(
        outage_line_id: str,
        monitored_line_id: str,
        top_n: int = 5,
    ) -> dict[str, Any]:
        """Rank generators by post-contingency branch-flow sensitivity.

        top_n is the number of generators requested by the user.
        """
        return rank_generator_sensitivities(
            network,
            outage_line_id=outage_line_id,
            monitored_line_id=monitored_line_id,
            top_n=top_n,
        )

    return [
        network_summary,
        line_list,
        line_detail,
        generator_list,
        line_contingency,
        generator_sensitivity,
    ]
