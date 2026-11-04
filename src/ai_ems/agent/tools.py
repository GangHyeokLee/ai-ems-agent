from pathlib import Path
from typing import Annotated, Any, Literal

from langchain_core.tools import InjectedToolCallId, tool

from ai_ems.config import CASE_FILE
from ai_ems.network_access import ModelIdentityUnavailable, access_for, guarded, network_access
from ai_ems.tools.control_tools import validate_balanced_redispatch
from ai_ems.tools.network_tools import get_line, get_network_summary, list_generators, list_lines
from ai_ems.tools.security_tools import get_limit_unit, run_line_contingency, select_most_severe_violated_line, select_primary_violation
from ai_ems.tools.sensitivity_tools import rank_generator_sensitivities
from ai_ems.tools.workflow_tools import analyze_contingency_response, analyze_generator_outage_response
from ai_ems.tools.generator_contingency_tools import analyze_generator_contingency
from ai_ems.tools.generator_screening_tools import run_generator_contingency_screening
from ai_ems.tools.generator_risk_tools import analyze_generator_risk_response


def summarize_line_contingency(result: dict[str, Any]) -> dict[str, Any]:
    """Legacy compact Agent summary of one full Security result (pure, no engine call)."""
    equipment = select_primary_violation(result)
    branch = result["monitored_branches"][0] if result["monitored_branches"] else None
    return {
        "analysis_type": "AC Security Analysis",
        "outage_line_id": result["outage_line_id"],
        "base_converged": result["base_converged"],
        "post_status": result["post_status"],
        "violated_equipment_count": result["violated_equipment_count"],
        "violation": ({
            "equipment_id": equipment["equipment_id"],
            "limit_type": equipment.get("limit_type"),
            "unit": equipment.get("unit") or get_limit_unit(equipment.get("limit_type")),
            "limit": equipment.get("limit"),
            "post_value": equipment.get("value"),
            "violation_amount": equipment.get("violation_amount"),
            "violation_direction": equipment.get("violation_direction"),
            "loading_percent": equipment.get("loading_percent"),
        } if equipment is not None else None),
        "monitored_branch": ({
            "line_id": branch["line_id"],
            "base_apparent_power_flow_mva": branch["base"]["apparent_power_mva"],
            "post_apparent_power_flow_mva": branch["apparent_power_mva"],
        } if branch is not None else None),
        "pre_violated_equipment_count": result["pre_violated_equipment_count"],
        "violation_comparison": result["violation_comparison"],
    }


def create_agent_tools(network, case_path: str | Path = CASE_FILE):
    def build_generator_contingency_payload(generator_id: str, slack_mode: Literal["single", "distributed"], balance_type: str | None = None, top_n_overloads: int = 5) -> dict[str, Any]:
        result = analyze_generator_contingency(case_path=case_path, generator_id=generator_id, slack_mode=slack_mode, balance_type=balance_type, top_n_overloads=top_n_overloads)
        generator_changes = sorted(result.get("generator_changes", []), key=lambda item: abs(item.get("delta_generation_mw") or 0.0), reverse=True)
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
                "pre_violated_equipment_count": security.get("pre_violated_equipment_count", 0),
                "post_violated_equipment_count": security.get("violated_equipment_count", 0),
                "pre_violations": security.get("pre_violated_equipment", []),
                "post_violations": security.get("violated_equipment", []),
            },
        }

    @tool
    @guarded(lambda: network)
    def network_summary() -> dict[str, Any]:
        """Get a summary of the current power network."""
        return get_network_summary(network)

    @tool
    @guarded(lambda: network)
    def line_list(limit: int = 10) -> list[dict[str, Any]]:
        """List transmission lines in the current power network."""
        return list_lines(network, limit=limit)

    @tool
    @guarded(lambda: network)
    def line_detail(line_id: str) -> dict[str, Any]:
        """Get detailed information for one transmission line."""
        return get_line(network, line_id)

    @tool
    @guarded(lambda: network)
    def generator_list(limit: int = 10, connected_only: bool = True) -> list[dict[str, Any]]:
        """List generators in the current power network."""
        return list_generators(network, limit=limit, connected_only=connected_only)

    @tool
    @guarded(lambda: network)
    def line_contingency(outage_line_id: str, monitored_line_id: str | None = None) -> dict[str, Any]:
        """Analyze a transmission line outage and optionally monitor one line."""
        monitored_line_ids = [monitored_line_id] if monitored_line_id is not None else None
        result = run_line_contingency(network, outage_line_id=outage_line_id, monitored_line_ids=monitored_line_ids)
        return summarize_line_contingency(result)

    @tool
    def generator_contingency_analysis(generator_id: str, slack_mode: Literal["single", "distributed"] = "single", balance_type: str | None = None, top_n_overloads: int = 5) -> dict[str, Any]:
        """Analyze one generator outage with AC Load Flow and Security Analysis."""
        return build_generator_contingency_payload(generator_id, slack_mode, balance_type, top_n_overloads)

    @tool
    def generator_contingency_comparison(generator_id: str, top_n_overloads: int = 5) -> dict[str, Any]:
        """Compare single-slack and distributed-slack results for one generator outage."""
        single = build_generator_contingency_payload(generator_id, "single", top_n_overloads=top_n_overloads)
        distributed = build_generator_contingency_payload(generator_id, "distributed", top_n_overloads=top_n_overloads)
        return {"analysis_type": "Generator Contingency Comparison", "generator_id": generator_id, "single": single, "distributed": distributed}

    @tool
    def generator_contingency_screening(slack_mode: Literal["single", "distributed"] = "single", top_n: int = 5) -> dict[str, Any]:
        """Screen all connected generator N-1 contingencies without corrective-action analysis."""
        if top_n < 1 or top_n > 20:
            raise ValueError("top_n must be between 1 and 20.")
        return run_generator_contingency_screening(case_path=case_path, slack_mode=slack_mode, top_n=top_n)

    @tool
    def generator_risk_response_analysis(slack_mode: Literal["single", "distributed"] = "distributed", screening_top_n: int = 5, response_top_n: int = 3, delta_mw: float = 10.0, search_mode: Literal["fixed", "adaptive"] = "fixed", max_deltas_per_pair: int = 5) -> dict[str, Any]:
        """Find the riskiest generator N-1 outage and analyze its corrective response.

        Use this tool when one request asks BOTH to find/rank the riskiest
        generator outage(s) AND to analyze corrective action, mitigation,
        response measures, or Redispatch for the top-ranked outage.
        Use search_mode='adaptive' only when the user explicitly requests
        adaptive or variable-sized Redispatch. Otherwise keep 'fixed'.
        """
        if max_deltas_per_pair < 1 or max_deltas_per_pair > 10:
            raise ValueError("max_deltas_per_pair must be between 1 and 10.")
        return analyze_generator_risk_response(
            case_path=case_path, slack_mode=slack_mode,
            screening_top_n=screening_top_n, response_top_n=response_top_n,
            delta_mw=delta_mw,
            **({"search_mode": "adaptive", "max_deltas_per_pair": max_deltas_per_pair}
               if search_mode == "adaptive" else {}),
        )

    @tool
    @guarded(lambda: network)
    def generator_sensitivity(outage_line_id: str, monitored_line_id: str | None = None, top_n: int = 5) -> dict[str, Any]:
        """Rank generators by post-contingency branch-flow sensitivity."""
        auto_selected = monitored_line_id is None
        if monitored_line_id is None:
            security_result = run_line_contingency(network, outage_line_id=outage_line_id)
            selected = select_most_severe_violated_line(network, security_result)
            if selected is None:
                raise ValueError("No overloaded transmission line found. Please specify monitored_line_id.")
            monitored_line_id = selected["equipment_id"]
        result = rank_generator_sensitivities(network, outage_line_id=outage_line_id, monitored_line_id=monitored_line_id, top_n=top_n)
        return {**result, "target_selection": "most_severe_violation" if auto_selected else "user_specified"}

    @tool
    def balanced_redispatch_validation(outage_line_id: str, monitored_line_id: str, up_generator_id: str, down_generator_id: str, delta_mw: float) -> dict[str, Any]:
        """Validate one explicitly specified balanced redispatch with AC power flow."""
        return validate_balanced_redispatch(case_path=case_path, outage_line_id=outage_line_id, monitored_line_id=monitored_line_id, up_generator_id=up_generator_id, down_generator_id=down_generator_id, delta_mw=delta_mw)

    @tool
    def contingency_response_analysis(outage_line_id: str, delta_mw: float = 10.0, top_n: int = 3) -> dict[str, Any]:
        """High-level line-outage corrective-action analysis."""
        return analyze_contingency_response(case_path=case_path, outage_line_id=outage_line_id, delta_mw=delta_mw, top_n=top_n)

    @tool
    def generator_outage_response_analysis(generator_id: str, slack_mode: Literal["single", "distributed"] = "distributed", balance_type: str | None = None, delta_mw: float = 10.0, top_n: int = 3, search_mode: Literal["fixed", "adaptive"] = "fixed", max_deltas_per_pair: int = 5) -> dict[str, Any]:
        """Analyze corrective-action candidates after a specific generator outage.

        Choose search_mode='adaptive' for an explicit adaptive/variable-magnitude
        Redispatch request. The fixed 10-MW search remains the default.
        Adaptive results are verified with AC Load Flow and Security Analysis;
        improvement without full violation clearance is NOT a secure end state.
        """
        if top_n < 1 or top_n > 10:
            raise ValueError("top_n must be between 1 and 10.")
        if max_deltas_per_pair < 1 or max_deltas_per_pair > 10:
            raise ValueError("max_deltas_per_pair must be between 1 and 10.")
        return analyze_generator_outage_response(
            case_path=case_path, outage_generator_id=generator_id,
            slack_mode=slack_mode, balance_type=balance_type,
            delta_mw=delta_mw, top_n=top_n,
            **({"search_mode": "adaptive", "max_deltas_per_pair": max_deltas_per_pair}
               if search_mode == "adaptive" else {}),
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
        generator_risk_response_analysis,
        generator_sensitivity,
        balanced_redispatch_validation,
        contingency_response_analysis,
        generator_outage_response_analysis,
    ]


def _capture_revision(network):
    """Bound model revision; callers hold the engine guard around it."""
    try:
        return access_for(network).capture_revision()
    except TypeError as exc:  # Not a native model: nothing can be attributed.
        raise ModelIdentityUnavailable("MODEL_IDENTITY_UNAVAILABLE") from exc


def _outcome(assessment: str, reason_code: str | None, outage_line_id: str,
             monitored_line_id: str | None = None, monitored_selection: str | None = None,
             top_n: int | None = None) -> dict[str, Any]:
    return {"assessment": assessment, "reason_code": reason_code,
            "outage_line_id": outage_line_id, "monitored_line_id": monitored_line_id,
            "monitored_selection": monitored_selection, "top_n": top_n}


def create_line_analysis_tools(network, ledger):
    """PH4-02B2 v1 KPG line-analysis Tools bound to one validated job ledger.

    Separate from ``create_agent_tools``, whose Tools, schemas and returns stay
    unchanged. A call runs only after the job guard authorized its exact
    ToolCall. It computes once under the shared network guard and returns the
    legacy compact content plus a full artifact sealed in ``ledger``. There is
    no hidden preparation, re-execution or projection-time calculation.
    """

    def execute(tool_call_id, name, arguments, compute):
        ledger.begin(tool_call_id, name, arguments)  # Unauthorized calls compute nothing.
        try:
            with network_access(network):
                before = _capture_revision(network)  # Unidentified model: fail before computing.
                content, outcome, result = compute()
                try:
                    after = _capture_revision(network)
                except ModelIdentityUnavailable:
                    after = None
            artifact = ledger.complete(tool_call_id, revision_before=before,
                                       revision_after=after, outcome=outcome, result=result)
        except BaseException as exc:
            ledger.fail(tool_call_id, exc)
            raise
        return content, artifact

    @tool("line_contingency", response_format="content_and_artifact")
    def line_contingency(outage_line_id: str, monitored_line_id: str | None = None, *,
                         tool_call_id: Annotated[str, InjectedToolCallId]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Analyze a transmission line outage and optionally monitor one line."""

        def compute():
            monitored_line_ids = [monitored_line_id] if monitored_line_id is not None else None
            result = run_line_contingency(network, outage_line_id=outage_line_id,
                                          monitored_line_ids=monitored_line_ids)
            evaluated = result["base_converged"] is True and result["post_status"] == "CONVERGED"
            outcome = _outcome("completed" if evaluated else "not_evaluated",
                               None if evaluated else "SECURITY_NOT_CONVERGED", result["outage_line_id"])
            return summarize_line_contingency(result), outcome, result

        return execute(tool_call_id, "line_contingency",
                       {"outage_line_id": outage_line_id, "monitored_line_id": monitored_line_id}, compute)

    @tool("generator_sensitivity", response_format="content_and_artifact")
    def generator_sensitivity(outage_line_id: str, monitored_line_id: str | None = None, top_n: int = 5, *,
                              tool_call_id: Annotated[str, InjectedToolCallId]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Rank generators by post-contingency branch-flow sensitivity."""

        def compute():
            security = selected = status = None
            if monitored_line_id is None:
                # The REST automatic-target decision; the legacy Tool keeps its ValueError.
                security = run_line_contingency(network, outage_line_id=outage_line_id)
                if security["post_status"] != "CONVERGED":
                    status = "SECURITY_NOT_CONVERGED"
                else:
                    selected = select_most_severe_violated_line(network, security)
                    status = None if selected is not None else "NO_OVERLOAD"
                target = selected["equipment_id"] if selected is not None else None
            else:
                target = monitored_line_id
            selection = "auto" if monitored_line_id is None else "explicit"
            ranking, metadata = None, {}
            if target is not None:
                ranking = rank_generator_sensitivities(network, outage_line_id=outage_line_id,
                                                       monitored_line_id=target, top_n=top_n)
                generators = network.get_generators(all_attributes=True)
                # Metadata of THIS execution: mapping never re-reads the live model later.
                metadata = {item["generator_id"]: {
                    "voltage_level_id": str(generators.loc[item["generator_id"]]["voltage_level_id"]),
                } for item in ranking["candidates"]}
                content = {**ranking, "target_selection": ("most_severe_violation" if selection == "auto"
                                                           else "user_specified")}
            else:
                # Actual unevaluated domain outcome; no candidate is invented.
                content = {"outage_line_id": outage_line_id, "monitored_line_id": None,
                           "candidate_count": 0, "candidates": [],
                           "target_selection": "most_severe_violation", "analysis_status": status}
            outcome = _outcome("completed" if ranking is not None else "not_evaluated", status,
                               outage_line_id, target, selection, top_n)
            result = {"selection": {"mode": selection, "status": status, "security": security,
                                    "selected": selected},
                      "ranking": ranking, "generator_metadata": metadata}
            return content, outcome, result

        return execute(tool_call_id, "generator_sensitivity",
                       {"outage_line_id": outage_line_id, "monitored_line_id": monitored_line_id,
                        "top_n": top_n}, compute)

    return [line_contingency, generator_sensitivity]
