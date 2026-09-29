from pathlib import Path
from typing import Any, Literal

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
from ai_ems.tools.workflow_tools import (
    analyze_contingency_response,
    analyze_generator_outage_response,
)
from ai_ems.tools.generator_contingency_tools import (
    analyze_generator_contingency,
)
from ai_ems.tools.generator_screening_tools import (
    run_generator_contingency_screening,
)


def create_agent_tools(
    network,
    case_path: str | Path = CASE_FILE,
):
    def build_generator_contingency_payload(
        generator_id: str,
        slack_mode: Literal["single", "distributed"],
        balance_type: str | None = None,
        top_n_overloads: int = 5,
    ) -> dict[str, Any]:
        result = analyze_generator_contingency(
            case_path=case_path,
            generator_id=generator_id,
            slack_mode=slack_mode,
            balance_type=balance_type,
            top_n_overloads=top_n_overloads,
        )

        generator_changes = sorted(
            result.get("generator_changes", []),
            key=lambda item: abs(item.get("delta_generation_mw") or 0.0),
            reverse=True,
        )

        security = result["security_analysis"]

        return {
            "analysis_type": "Generator Contingency Analysis",
            "generator_id": result["generator_id"],
            "contingency_id": result["contingency_id"],
            "slack_mode": result["slack_mode"],
            "balance_type": result.get("balance_type"),
            "balancing_interpretation": result["balancing_interpretation"],
            "outage_generator": result["outage_generator"],
            "base_converged": result["base_converged"],
            "post_contingency_converged": result["post_contingency_converged"],
            "base_loadflow": result["base"]["loadflow"],
            "post_loadflow": result["post_contingency"]["loadflow"],
            "base_power_balance": result["base"]["power_balance"],
            "post_power_balance": result["post_contingency"].get("power_balance"),
            "loss_change": result["loss_change"],
            "generator_change_count": len(generator_changes),
            "top_generator_changes": generator_changes[:5],
            "major_overloads": result.get("major_overloads", []),
            "security": {
                "pre_status": security.get("pre_status"),
                "post_status": security.get("post_status"),
                "pre_violated_equipment_count": security.get(
                    "pre_violated_equipment_count",
                    0,
                ),
                "post_violated_equipment_count": security.get(
                    "violated_equipment_count",
                    0,
                ),
                "pre_violations": security.get(
                    "pre_violated_equipment",
                    [],
                ),
                "post_violations": security.get(
                    "violated_equipment",
                    [],
                ),
            },
        }

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
    def generator_contingency_analysis(
        generator_id: str,
        slack_mode: Literal["single", "distributed"] = "single",
        balance_type: str | None = None,
        top_n_overloads: int = 5,
    ) -> dict[str, Any]:
        """Analyze one generator outage with AC Load Flow and Security Analysis.

        Use this tool when the user asks for one specific slack mode for a
        generator outage. If the user asks to compare both single and
        distributed slack, use generator_contingency_comparison instead.

        slack_mode can be "single" or "distributed".
        Distributed slack is a load-flow balancing assumption, not operator
        corrective redispatch.

        balance_type should normally be omitted. It is only applicable when
        slack_mode is "distributed".
        """
        return build_generator_contingency_payload(
            generator_id=generator_id,
            slack_mode=slack_mode,
            balance_type=balance_type,
            top_n_overloads=top_n_overloads,
        )

    @tool
    def generator_contingency_comparison(
        generator_id: str,
        top_n_overloads: int = 5,
    ) -> dict[str, Any]:
        """Compare single-slack and distributed-slack results for one generator outage.

        Use this tool when the user explicitly asks for both slack modes, a
        single-vs-distributed comparison, or asks how the slack assumption
        changes the same generator-outage result. The tool runs the physical
        generator-contingency analysis twice: once with single slack and once
        with distributed slack.
        """
        single = build_generator_contingency_payload(
            generator_id=generator_id,
            slack_mode="single",
            top_n_overloads=top_n_overloads,
        )
        distributed = build_generator_contingency_payload(
            generator_id=generator_id,
            slack_mode="distributed",
            top_n_overloads=top_n_overloads,
        )

        return {
            "analysis_type": "Generator Contingency Comparison",
            "generator_id": generator_id,
            "single": single,
            "distributed": distributed,
        }

    @tool
    def generator_contingency_screening(
        slack_mode: Literal["single", "distributed"] = "single",
        top_n: int = 5,
    ) -> dict[str, Any]:
        """Screen all connected generator N-1 contingencies.

        Use this tool when the user asks to analyze all generator outages,
        perform generator N-1 screening, find risky generator contingencies,
        or rank the most severe generator outages.

        slack_mode can be "single" or "distributed".

        This tool runs AC Security Analysis for all currently connected
        generators and returns an overall classification summary plus the
        highest-priority contingencies.

        This is screening/ranking, not corrective redispatch or dynamic
        stability analysis.
        """
        if top_n < 1 or top_n > 20:
            raise ValueError("top_n must be between 1 and 20.")

        return run_generator_contingency_screening(
            case_path=case_path,
            slack_mode=slack_mode,
            top_n=top_n,
        )

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

    @tool
    def generator_outage_response_analysis(
        generator_id: str,
        slack_mode: Literal["single", "distributed"] = "distributed",
        balance_type: str | None = None,
        delta_mw: float = 10.0,
        top_n: int = 3,
    ) -> dict[str, Any]:
        """Analyze corrective-action candidates after a generator outage.

        Use this high-level tool when the user asks for response measures,
        corrective action, redispatch candidates, or mitigation after a
        specific generator outage.

        The workflow performs generator-outage Security Analysis, automatically
        selects the most severe overloaded line, calculates post-contingency
        generator sensitivities, generates balanced redispatch candidates, and
        physically revalidates the tested candidates with AC Load Flow and
        whole-network Security Analysis.

        slack_mode can be "single" or "distributed". Distributed slack is the
        default Load Flow balancing assumption for this corrective-action
        workflow.

        The returned best_tested_candidate is only the best among the tested
        candidates. It is not an OPF/SCED optimum.
        """
        if top_n < 1 or top_n > 10:
            raise ValueError("top_n must be between 1 and 10.")

        return analyze_generator_outage_response(
            case_path=case_path,
            outage_generator_id=generator_id,
            slack_mode=slack_mode,
            balance_type=balance_type,
            delta_mw=delta_mw,
            top_n=top_n,
        )

    return [
        network_summary,
        line_list,
        line_detail,
        generator_list,
        line_contingency,
        generator_contingency_analysis,
        generator_contingency_comparison,
        generator_contingency_screening,
        generator_sensitivity,
        balanced_redispatch_validation,
        contingency_response_analysis,
        generator_outage_response_analysis,
    ]
