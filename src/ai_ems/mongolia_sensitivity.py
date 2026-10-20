"""Post-contingency AC branch-flow sensitivity for the Mongolia synthetic PyPowSyBl model.

Definition (OpenLoadFlow AC sensitivity, verified by central finite differences):

- function: ``BRANCH_ACTIVE_POWER_1`` of the monitored line, i.e. active power entering
  the line at side 1 (= original ``bus0`` terminal), MW, after the line contingency;
- variable: ``INJECTION_ACTIVE_POWER`` of a synthetic generator, MW;
- value: d(P_side1) / d(P_injection) in MW/MW at the post-contingency operating point;
- balance: ``distributed_slack=False``, so every injection change is absorbed by the slack
  bus 25 (``SYNTHETIC_SLACK_25``); the slack generator's own sensitivity is 0 by definition
  and it is left out of the ranking.

This is a synthetic *injection* sensitivity, not a redispatch recommendation, and the 45
synthetic generators are not real Mongolian power plants. Contingencies that split the
grid are not evaluated (no policy for islands yet).
"""

from __future__ import annotations

import math
from typing import Any

import networkx as nx
import pypowsybl as pp

from ai_ems import mongolia_security as ms

FD_STEP_MW = 0.1
FD_ABS_TOL = 1e-4          # MW/MW, pre-registered before the first full comparison
FD_REL_TOL = 0.01          # 1 % of |finite difference|
REFERENCE_FLOW_TOL_MW = 1e-6
MAX_TOP_N = 45
SELF_CHECK = ("751261406-1_0", "303374638-1_0")

SENSITIVITY_CAVEATS = [
    "합성 발전기 유효전력 '주입' 민감도이며 실제 발전기 재급전 권고가 아닙니다.",
    "합성 발전기 45대는 실제 몽골 발전소가 아니며, 실제 재급전 후보로 해석하지 않습니다.",
    "주입 변화는 Slack 모선 25가 모두 흡수한다고 가정합니다(distributed_slack=False).",
    "사고 후 운전점에서의 선형화 값이므로 큰 출력 변화에는 그대로 적용되지 않습니다.",
]


class SensitivityVerificationFailed(RuntimeError):
    """The computed sensitivity is non-finite or disagrees with its independent checks."""


class InvalidMonitoredLine(ValueError):
    pass


def _bridge_lines(net, outage_iidm_id: str, model: ms.ValidatedModel) -> set[str]:
    """Original line IDs that are bridges (radial) in the post-contingency topology."""
    multiplicity: dict[frozenset, int] = {}
    edge_lines: dict[frozenset, list[str]] = {}
    for frame in (net.get_lines(), net.get_2_windings_transformers()):
        for iidm_id, row in frame.iterrows():
            if iidm_id == outage_iidm_id:
                continue
            key = frozenset((row.voltage_level1_id[3:], row.voltage_level2_id[3:]))
            multiplicity[key] = multiplicity.get(key, 0) + 1
            kind, original = model.iidm_to_original[iidm_id]
            if kind == "Line":
                edge_lines.setdefault(key, []).append(original)
    graph = nx.Graph()
    graph.add_nodes_from(model.mapping["Bus"])
    graph.add_edges_from(tuple(k) for k in multiplicity)
    bridges = set()
    for u, v in nx.bridges(graph):
        key = frozenset((u, v))
        if multiplicity[key] == 1:
            bridges.update(edge_lines.get(key, []))
    return bridges


def _line_info(model: ms.ValidatedModel, net, line_id: str) -> dict[str, Any]:
    row = model.mapping["Line"][line_id]
    limits = net.get_loading_limits().reset_index()
    limits = limits.loc[(limits.element_id == row["iidm_id"]) & (limits.acceptable_duration == -1)]
    return {"line_id": line_id, "bus0": row["original_bus0"], "bus1": row["original_bus1"],
            "voltage_kv": float(row["nominal_kv"]), "rating_mva": float(limits.value.min())}


def _not_evaluated(model, outage_line_id: str, status: str, message: str, **extra) -> dict[str, Any]:
    return {
        "grid_id": ms.GRID_ID, "scenario_id": model.scenario_id,
        "synthetic_scenario": True, "actual_operating_data": False,
        "outage_line_id": outage_line_id, "analysis_status": status,
        "sensitivity_available": False, "message": message,
        "monitored_line_id": None, "candidate_count": 0, "candidates": [],
        "model_caveats": ms.MODEL_CAVEATS, **extra,
    }


def analyze_line_sensitivity(
    net,
    model: ms.ValidatedModel,
    outage_line_id: str,
    monitored_line_id: str | None = None,
    top_n: int = 10,
    fd_count: int = 2,
) -> dict[str, Any]:
    """Compute, verify and rank generator sensitivities on a base-solved independent network."""
    lines = model.mapping["Line"]
    outage_iidm = lines[outage_line_id]["iidm_id"]
    isolated = ms.topology_islands(net, outage_iidm, model)
    if isolated:
        return _not_evaluated(
            model, outage_line_id, "NOT_EVALUATED_ISLANDING",
            "이 사고는 계통을 분리합니다. 분리 섬 처리 정책이 정해지지 않아 민감도를 평가하지 않았습니다(순위 없음).",
            isolated_buses=isolated, isolated_bus_count=len(isolated),
        )
    if monitored_line_id is not None:
        monitored_line_id = monitored_line_id.strip()
        if monitored_line_id in model.excluded_line_ids:
            raise InvalidMonitoredLine(f"감시 선로 {monitored_line_id}은(는) 기준 PF 해석 대상에서 제외된 선로입니다.")
        if monitored_line_id not in lines:
            raise InvalidMonitoredLine(f"몽골 PF 해석 대상에 없는 감시 선로 ID입니다: {monitored_line_id}")
        if monitored_line_id == outage_line_id:
            raise InvalidMonitoredLine("감시 선로는 사고 선로와 달라야 합니다.")

    top_n = max(1, min(int(top_n), MAX_TOP_N))
    params = ms.loadflow_parameters()
    base_flows = {o: float(net.get_lines().at[r["iidm_id"], "p1"]) for o, r in lines.items()}
    base_variant = net.get_working_variant_id()
    variant = "SENS_" + outage_line_id
    net.clone_variant(base_variant, variant)
    try:
        net.set_working_variant(variant)
        net.update_lines(id=[outage_iidm], connected1=[False], connected2=[False])
        post_lf = pp.loadflow.run_ac(net, parameters=params)
        if not post_lf or post_lf[0].status.name != "CONVERGED":
            return _not_evaluated(
                model, outage_line_id, "POST_CONTINGENCY_NOT_CONVERGED",
                "사고 후 AC PF가 수렴하지 않아 민감도를 계산하지 않았습니다.",
                post_status=post_lf[0].status.name if post_lf else "NO_RESULT",
            )
        post_flows = {o: float(net.get_lines().at[r["iidm_id"], "p1"]) for o, r in lines.items() if o != outage_line_id}
        bridges = _bridge_lines(net, outage_iidm, model)
        dp = {o: post_flows[o] - base_flows[o] for o in post_flows if math.isfinite(post_flows[o])}
        ranked_lines = sorted(dp, key=lambda o: (-abs(dp[o]), o))
        meshed = [o for o in ranked_lines if o not in bridges]
        if monitored_line_id is None:
            monitored_line_id = meshed[0] if meshed else ranked_lines[0]
            selection = {
                "mode": "auto",
                "rule": "사고 후 주 계통에서 |ΔP|가 가장 큰 비방사형(메시) 선로. 없으면 |ΔP| 최대 선로.",
            }
        else:
            selection = {"mode": "user", "rule": "사용자가 선택한 감시 선로"}
        selection["alternatives"] = [
            {"line_id": o, "dp_mw": dp[o], "radial_after_contingency": o in bridges}
            for o in ranked_lines[:5] if o != monitored_line_id
        ][:4]
        if not math.isfinite(post_flows.get(monitored_line_id, math.nan)):
            raise InvalidMonitoredLine(f"감시 선로 {monitored_line_id}의 사고 후 조류가 없습니다.")
        mon_iidm = lines[monitored_line_id]["iidm_id"]

        # AC sensitivity on the base variant (the analysis applies the contingency itself).
        net.set_working_variant(base_variant)
        gens = net.get_generators()
        variables = [g for g in gens.index if bool(gens.at[g, "connected"])]
        analysis = pp.sensitivity.create_ac_analysis()
        contingency_id = "OUT_" + outage_line_id
        analysis.add_single_element_contingency(outage_iidm, contingency_id)
        analysis.add_postcontingency_branch_flow_factor_matrix(
            branches_ids=[mon_iidm], variables_ids=variables, contingencies_ids=[contingency_id], matrix_id="post")
        analysis.add_precontingency_branch_flow_factor_matrix(
            branches_ids=[mon_iidm], variables_ids=variables, matrix_id="pre")
        result = analysis.run(net, parameters=pp.sensitivity.Parameters(load_flow_parameters=params))
        post_matrix = result.get_sensitivity_matrix("post", contingency_id)
        pre_matrix = result.get_sensitivity_matrix("pre")
        if post_matrix is None or pre_matrix is None:
            raise SensitivityVerificationFailed("민감도 결과 행렬이 없습니다.")
        s_post = post_matrix[mon_iidm].astype(float)
        s_pre = pre_matrix[mon_iidm].astype(float)
        ref_post = float(result.get_reference_matrix("post", contingency_id).at["reference_values", mon_iidm])
        ref_pre = float(result.get_reference_matrix("pre").at["reference_values", mon_iidm])
        if not (s_post.map(math.isfinite).all() and s_pre.map(math.isfinite).all()
                and math.isfinite(ref_post) and math.isfinite(ref_pre)):
            raise SensitivityVerificationFailed("민감도 또는 기준 조류에 NaN/Infinity가 있습니다.")
        reference_check = {
            "post_reference_flow_mw": ref_post, "post_independent_ac_pf_mw": post_flows[monitored_line_id],
            "pre_reference_flow_mw": ref_pre, "pre_independent_ac_pf_mw": base_flows[monitored_line_id],
        }
        if (abs(ref_post - post_flows[monitored_line_id]) > REFERENCE_FLOW_TOL_MW
                or abs(ref_pre - base_flows[monitored_line_id]) > REFERENCE_FLOW_TOL_MW):
            raise SensitivityVerificationFailed(f"민감도 기준 조류가 독립 AC PF와 다릅니다: {reference_check}")

        slack_iidm = "G_" + ms.SLACK_GENERATOR_ID
        ranked = sorted((g for g in variables if g != slack_iidm), key=lambda g: (-abs(s_post[g]), g))

        # Central finite differences on the post-contingency variant (independent AC PF runs).
        net.set_working_variant(variant)
        checks = []
        for g in ranked[:max(2, fd_count)]:
            target = float(net.get_generators().at[g, "target_p"])
            values = []
            for step in (FD_STEP_MW, -FD_STEP_MW):
                net.update_generators(id=[g], target_p=[target + step])
                lf = pp.loadflow.run_ac(net, parameters=params)
                if not lf or lf[0].status.name != "CONVERGED":
                    net.update_generators(id=[g], target_p=[target])
                    raise SensitivityVerificationFailed(f"유한차분 AC PF가 수렴하지 않았습니다: {g}")
                values.append(float(net.get_lines().at[mon_iidm, "p1"]))
            net.update_generators(id=[g], target_p=[target])
            fd = (values[0] - values[1]) / (2 * FD_STEP_MW)
            diff = abs(float(s_post[g]) - fd)
            checks.append({
                "generator_id": model.iidm_to_original[g][1], "ac_sensitivity": float(s_post[g]),
                "finite_difference": fd, "abs_diff": diff,
                "within_tolerance": diff <= FD_ABS_TOL + FD_REL_TOL * abs(fd),
            })
        if not all(item["within_tolerance"] for item in checks):
            raise SensitivityVerificationFailed(f"AC 민감도가 유한차분과 허용오차 밖에서 다릅니다: {checks}")
    finally:
        net.set_working_variant(base_variant)
        net.remove_variant(variant)

    vls = net.get_voltage_levels()
    candidates = []
    for rank, g in enumerate(ranked[:top_n], start=1):
        original = model.iidm_to_original[g][1]
        row = gens.loc[g]
        bus = model.mapping["Generator"][original]["original_bus0"]
        candidates.append({
            "rank": rank, "generator_id": original, "bus_id": bus,
            "voltage_kv": float(vls.at[row.voltage_level_id, "nominal_v"]),
            "control": "PV" if bool(row.voltage_regulator_on) else "PQ",
            "target_p_mw": float(row.target_p),
            "sensitivity": float(s_post[g]), "abs_sensitivity": abs(float(s_post[g])),
            "pre_contingency_sensitivity": float(s_pre[g]),
            "synthetic_generator": True,
        })
    rating = _line_info(model, net, monitored_line_id)["rating_mva"]
    post_flow = post_flows[monitored_line_id]
    return {
        "grid_id": ms.GRID_ID, "scenario_id": model.scenario_id,
        "synthetic_scenario": True, "actual_operating_data": False,
        "engine": "PyPowSyBl AC Sensitivity Analysis (OpenLoadFlow)",
        "contingency_id": contingency_id,
        "outage_line_id": outage_line_id,
        "outage_line": _line_info(model, net, outage_line_id),
        "analysis_status": "COMPLETED",
        "sensitivity_available": True,
        "monitored_line_id": monitored_line_id,
        "monitored_line": _line_info(model, net, monitored_line_id),
        "monitored_line_selection": selection,
        "monitored_line_radial_after_contingency": monitored_line_id in bridges,
        "radial_note": (
            "사고 후 감시 선로가 방사형(bridge)이라 하류 발전기 민감도는 약 ±1, 상류는 0이 됩니다."
            if monitored_line_id in bridges else None
        ),
        "function": "BRANCH_ACTIVE_POWER_1: 감시 선로 bus0(side 1) 단자에서 선로로 들어가는 유효전력 [MW]",
        "variable": "INJECTION_ACTIVE_POWER: 합성 발전기 유효전력 주입 [MW]",
        "unit": "MW/MW",
        "slack_balance": "distributed_slack=False: 주입 변화는 Slack 모선 25(SYNTHETIC_SLACK_25)가 흡수",
        "base_flow_mw": base_flows[monitored_line_id],
        "post_flow_mw": post_flow,
        "post_loading_percent": 100.0 * abs(post_flow) / rating if rating else None,
        "reference_check": reference_check,
        "candidate_count": len(candidates),
        "evaluated_generator_count": len(ranked),
        "zero_sensitivity_count": sum(1 for g in ranked if abs(s_post[g]) < 1e-6),
        "candidates": candidates,
        "excluded_from_ranking": [{"generator_id": ms.SLACK_GENERATOR_ID,
                                   "reason": "Slack 기준 발전기(정의상 민감도 0)"}],
        "finite_difference_check": {
            "step_mw": FD_STEP_MW, "tolerance": f"|AC-FD| <= {FD_ABS_TOL} + {FD_REL_TOL}*|FD|",
            "method": "사고 후 variant에서 발전기 target_p ±0.1MW 중심차분, 독립 AC PF",
            "results": checks, "passed": True,
        },
        "model_caveats": ms.MODEL_CAVEATS + SENSITIVITY_CAVEATS,
    }


def run_self_check(net, model: ms.ValidatedModel) -> dict[str, Any]:
    """Validation-time gate: the non-islanding reference case must compute and verify."""
    outage, monitored = SELF_CHECK
    try:
        result = analyze_line_sensitivity(net, model, outage, monitored, top_n=3, fd_count=2)
    except (SensitivityVerificationFailed, InvalidMonitoredLine, KeyError) as exc:
        return {"passed": False, "reason": f"Sensitivity 자체 검증 실패: {exc}"}
    if result["analysis_status"] != "COMPLETED":
        return {"passed": False, "reason": f"Sensitivity 자체 검증 실패: {result['analysis_status']}"}
    return {"passed": True, "reason": None, "outage_line_id": outage, "monitored_line_id": monitored,
            "finite_difference_check": result["finite_difference_check"]["results"]}
