"""Convert the pinned historical 45-generator model into an isolated verified bundle.

Explicit verification library only. It neither installs models nor changes EMS
runtime paths. The electrical algorithms are retained from the source converter.
"""
from __future__ import annotations
import hashlib
import json
import logging
import math
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from . import mn35k_powsybl as conv
from .artifacts import artifact_destination, new_artifact_directory
from .passive_inputs import logical_input
from .conversion_inputs import read_settings, source_manifest, owned_code_hashes, environment
from .conversion_checks import assert_mapping, assert_structure, runtime_observation, runtime_comparison, assert_valid_result, xiidm_identity
BRANCH_FIELDS = ["p0", "q0", "p1", "q1"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()



def write_json(path: Path, value) -> None:
    def default(obj):
        if isinstance(obj, (np.floating, np.integer)):
            return obj.item()
        if isinstance(obj, np.bool_):
            return bool(obj)
        raise TypeError(type(obj))

    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=default)
    path.write_text(text + "\n", encoding="utf-8")



def _clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value



def load_pypsa(path: Path):
    import pypsa

    logging.getLogger("pypsa").setLevel(logging.WARNING)
    return pypsa.Network(path)



def verify_scenario(n, config: dict, *, paths) -> dict:
    meta = n.meta.get("synthetic_reactive_support", {})
    scenario_id = meta.get("scenario", {}).get("id")
    reactive_cfg = json.loads((logical_input(paths, config["scenario_source_config"])).read_text(encoding="utf-8"))
    counts = {c: len(getattr(n, c)) for c in config["expected_counts"]}
    controls = n.buses.control.value_counts().to_dict()
    checks = {
        "input_sha256_matches": sha256(logical_input(paths, config["input_model"])) == config["input_model_sha256"],
        "meta_scenario_id": scenario_id,
        "meta_scenario_matches": scenario_id == config["expected_scenario_id"],
        "source_config_output_model_matches": reactive_cfg["output_model"] == config["input_model"],
        "source_config_selected_last_scenario": reactive_cfg["scenarios"][-1]["id"] == config["expected_scenario_id"],
        "counts": counts,
        "counts_match": counts == config["expected_counts"],
        "bus_controls": {k: int(v) for k, v in controls.items()},
        "bus_controls_match": {k: int(v) for k, v in controls.items()} == config["expected_bus_controls"],
        "slack_bus": str(n.sub_networks.slack_bus.iloc[0]),
        "slack_matches": str(n.sub_networks.slack_bus.iloc[0]) == config["expected_slack_bus"],
        "q_limit_switches": meta.get("q_limit_switches"),
        "actual_operation_validated": meta.get("actual_operation_validated"),
        "snapshot": str(n.snapshots[0]),
    }
    required = ["input_sha256_matches", "meta_scenario_matches", "source_config_output_model_matches",
                "source_config_selected_last_scenario", "counts_match", "bus_controls_match", "slack_matches"]
    checks["passed"] = all(checks[k] for k in required)
    if not checks["passed"]:
        raise RuntimeError(f"Input scenario verification failed: {checks}")
    return checks



def pypsa_rerun(config: dict, stored: dict, *, paths) -> dict:
    """Recompute PyPSA PF from the file (in memory) to confirm the stored results are reproducible."""
    n = load_pypsa(logical_input(paths, config["input_model"]))
    raw = n.pf(snapshots=n.snapshots, **config["pypsa_pf"])
    rerun = conv.pypsa_results(n)
    diffs = {}
    for comp, key, fields in (("buses", "bus_id", ["v_mag_pu", "v_ang_deg"]),
                              ("lines", "line_id", BRANCH_FIELDS),
                              ("transformers", "transformer_id", BRANCH_FIELDS),
                              ("generators", "generator_id", ["p_mw", "q_mvar"]),
                              ("shunts", "shunt_id", ["q_absorbed_mvar"])):
        frame = conv.compare(stored[comp], rerun[comp], key, fields)
        diffs[comp] = max(float(frame[f + "_diff"].abs().max()) for f in fields)
    return {
        "converged": bool(raw["converged"].values.all()),
        "n_iter": int(raw["n_iter"].values.max()),
        "error": float(raw["error"].values.max()),
        "max_abs_diff_vs_stored": diffs,
    }



def with_relative_angle(buses: pd.DataFrame, slack: str) -> pd.DataFrame:
    out = buses.copy()
    reference = float(out.loc[out.bus_id == slack, "v_ang_deg"].iloc[0])
    out["v_ang_deg"] = out["v_ang_deg"] - reference
    return out



def add_loading(frame: pd.DataFrame, s_nom: pd.Series) -> pd.DataFrame:
    out = frame.copy()
    s0 = np.hypot(out.p0, out.q0)
    s1 = np.hypot(out.p1, out.q1)
    out["loading_percent"] = 100 * np.maximum(s0, s1) / out.iloc[:, 0].map(s_nom).to_numpy()
    return out



def system_balance(res: dict, model: conv.ConversionModel) -> dict:
    gen_p = float(res["generators"].p_mw.sum())
    gen_q = float(res["generators"].q_mvar.sum())
    load_p = float(model.loads.p_mw.sum())
    load_q = float(model.loads.q_mvar.sum())
    shunt_q = float(res["shunts"].q_absorbed_mvar.sum())
    line_p_loss = float((res["lines"].p0 + res["lines"].p1).sum())
    line_q_net = float((res["lines"].q0 + res["lines"].q1).sum())
    trafo_p_loss = float((res["transformers"].p0 + res["transformers"].p1).sum())
    trafo_q_net = float((res["transformers"].q0 + res["transformers"].q1).sum())
    slack = res["generators"].loc[res["generators"].generator_id.str.startswith("SYNTHETIC_SLACK_")]
    return {
        "generator_p_mw": gen_p, "generator_q_mvar": gen_q,
        "load_p_mw": load_p, "load_q_mvar": load_q, "shunt_q_absorbed_mvar": shunt_q,
        "line_p_loss_mw": line_p_loss, "line_q_net_mvar": line_q_net,
        "transformer_p_loss_mw": trafo_p_loss, "transformer_q_net_mvar": trafo_q_net,
        "total_p_loss_mw": line_p_loss + trafo_p_loss,
        "p_balance_residual_mw": gen_p - load_p - line_p_loss - trafo_p_loss,
        "q_balance_residual_mvar": gen_q - load_q - shunt_q - line_q_net - trafo_q_net,
        "slack_p_mw": float(slack.p_mw.iloc[0]), "slack_q_mvar": float(slack.q_mvar.iloc[0]),
    }



def comparison_tables(ref: dict, cand: dict, model: conv.ConversionModel) -> dict[str, pd.DataFrame]:
    slack = model.slack_bus
    line_s = model.lines.set_index("line_id").s_nom_mva
    trafo_s = model.transformers.set_index("transformer_id").s_nom_mva
    return {
        "buses": conv.compare(with_relative_angle(ref["buses"], slack), with_relative_angle(cand["buses"], slack),
                              "bus_id", ["v_mag_pu", "v_ang_deg"]),
        "lines": conv.compare(add_loading(ref["lines"], line_s), add_loading(cand["lines"], line_s),
                              "line_id", BRANCH_FIELDS + ["loading_percent"]),
        "transformers": conv.compare(add_loading(ref["transformers"], trafo_s), add_loading(cand["transformers"], trafo_s),
                                     "transformer_id", BRANCH_FIELDS + ["loading_percent"]),
        "generators": conv.compare(ref["generators"], cand["generators"], "generator_id", ["p_mw", "q_mvar"]),
        "shunts": conv.compare(ref["shunts"], cand["shunts"], "shunt_id", ["q_absorbed_mvar"]),
    }



def stats_and_verdict(tables: dict, bal_ref: dict, bal_cand: dict, tol: dict) -> dict:
    spec = {
        "buses": ("bus_id", {"v_mag_pu": tol["v_mag_pu"], "v_ang_deg": tol["v_ang_deg"]}),
        "lines": ("line_id", {f: tol["branch_mw_mvar"] for f in BRANCH_FIELDS} | {"loading_percent": None}),
        "transformers": ("transformer_id", {f: tol["branch_mw_mvar"] for f in BRANCH_FIELDS} | {"loading_percent": None}),
        "generators": ("generator_id", {"p_mw": tol["generator_mw_mvar"], "q_mvar": tol["generator_mw_mvar"]}),
        "shunts": ("shunt_id", {"q_absorbed_mvar": tol["shunt_mvar"]}),
    }
    stats, passed = {}, True
    units = {"v_mag_pu": "pu", "v_ang_deg": "deg", "p0": "MW", "p1": "MW", "q0": "MVAr", "q1": "MVAr",
             "p_mw": "MW", "q_mvar": "MVAr", "q_absorbed_mvar": "MVAr", "loading_percent": "%"}
    for comp, (key, fields) in spec.items():
        stats[comp] = {}
        for f, limit in fields.items():
            s = conv.error_stats(tables[comp], key, f)
            s["unit"] = units[f]
            s["tolerance"] = limit
            s["within_tolerance"] = None if limit is None else (
                s["count"] > 0 and s["non_comparable"] == 0 and s["max_abs_error"] <= limit)
            if limit is not None and not s["within_tolerance"]:
                passed = False
            stats[comp][f] = s
    balance = {}
    for k in bal_ref:
        diff = bal_cand[k] - bal_ref[k]
        limit = tol["system_balance_mw_mvar"] if k not in ("load_p_mw", "load_q_mvar") else 0.0
        ok = abs(diff) <= max(limit, 1e-12)
        passed &= ok
        balance[k] = {"pypsa": bal_ref[k], "powsybl": bal_cand[k], "diff": diff, "tolerance": limit, "within_tolerance": ok}
    return {"element_stats": stats, "system_balance": balance, "within_all_tolerances": bool(passed)}



def conversion_parameters(model: conv.ConversionModel, net) -> pd.DataFrame:
    """Original PyPSA effective parameters next to the values read back from the IIDM network."""
    rows = []
    lines = net.get_lines()
    for row in model.lines.itertuples():
        iidm = lines.loc["L_" + row.line_id]
        for name, src, unit, dst in (("r", row.r_ohm, "Ohm", iidm.r), ("x", row.x_ohm, "Ohm", iidm.x),
                                     ("b_total", row.b_s, "S", iidm.b1 + iidm.b2), ("g_total", row.g_s, "S", iidm.g1 + iidm.g2),
                                     ("b_side1", row.b_s / 2, "S", iidm.b1), ("b_side2", row.b_s / 2, "S", iidm.b2)):
            rows.append({"component": "Line", "original_id": row.line_id, "parameter": name, "unit": unit,
                         "pypsa_value": src, "iidm_value": dst, "rule": "IIDM pi: b1=b2=b/2, g1=g2=g/2"})
    trafos = net.get_2_windings_transformers()
    for row in model.transformers.itertuples():
        iidm = trafos.loc["T_" + row.transformer_id]
        for name, src, unit, dst, rule in (
            ("r", row.r_ohm_side2, "Ohm (side 2)", iidm.r, "r_pu(s_nom) * rated_u2^2 / s_nom"),
            ("x", row.x_ohm_side2, "Ohm (side 2)", iidm.x, "x_pu(s_nom) * rated_u2^2 / s_nom"),
            ("rated_u1", row.rated_u1_kv, "kV", iidm.rated_u1, "= v_nom(bus0)"),
            ("rated_u2", row.rated_u2_kv, "kV", iidm.rated_u2, "= v_nom(bus1)"),
            ("rated_s", row.s_nom_mva, "MVA", iidm.rated_s, "= s_nom"),
            ("g", 0.0, "S", iidm.g, "PyPSA g=b=0, T-model reduces to series impedance"),
            ("b", 0.0, "S", iidm.b, "PyPSA g=b=0, T-model reduces to series impedance"),
            ("tap_ratio", row.tap_ratio, "-", 1.0, "no tap changer created (PyPSA tap_ratio=1)"),
            ("phase_shift", row.phase_shift_deg, "deg", 0.0, "no phase tap changer created"),
        ):
            rows.append({"component": "Transformer", "original_id": row.transformer_id, "parameter": name,
                         "unit": unit, "pypsa_value": src, "iidm_value": dst, "rule": rule})
    gens = net.get_generators(all_attributes=True)
    for row in model.generators.itertuples():
        iidm = gens.loc["G_" + row.generator_id]
        for name, src, unit, dst in (("target_p", row.p_set_mw, "MW", iidm.target_p),
                                     ("target_q", row.q_set_mvar, "MVAr", iidm.target_q),
                                     ("target_v", row.v_set_pu * row.v_nom_kv, "kV", iidm.target_v),
                                     ("voltage_regulator_on", float(row.control != "PQ"), "bool", float(iidm.voltage_regulator_on)),
                                     ("min_p", row.p_min_mw, "MW", iidm.min_p), ("max_p", row.p_max_mw, "MW", iidm.max_p),
                                     ("min_q", row.q_min_mvar, "MVAr", iidm.min_q),
                                     ("max_q", row.q_max_mvar, "MVAr", iidm.max_q)):
            rows.append({"component": "Generator", "original_id": row.generator_id, "parameter": name, "unit": unit,
                         "pypsa_value": src, "iidm_value": dst, "rule": f"PyPSA control={row.control}"})
    loads = net.get_loads()
    for row in model.loads.itertuples():
        iidm = loads.loc["D_" + row.load_id]
        rows.append({"component": "Load", "original_id": row.load_id, "parameter": "p0", "unit": "MW",
                     "pypsa_value": row.p_mw, "iidm_value": iidm.p0, "rule": "constant P (consumption)"})
        rows.append({"component": "Load", "original_id": row.load_id, "parameter": "q0", "unit": "MVAr",
                     "pypsa_value": row.q_mvar, "iidm_value": iidm.q0, "rule": "constant Q (consumption)"})
    sections = net.get_linear_shunt_compensator_sections()
    for row in model.shunts.itertuples():
        iidm = sections.loc["SH_" + row.shunt_id]
        rows.append({"component": "Shunt", "original_id": row.shunt_id, "parameter": "b", "unit": "S",
                     "pypsa_value": row.b_s, "iidm_value": iidm.b_per_section,
                     "rule": "linear shunt, 1/1 section; b<0 inductive in both"})
        rows.append({"component": "Shunt", "original_id": row.shunt_id, "parameter": "g", "unit": "S",
                     "pypsa_value": row.g_s, "iidm_value": iidm.g_per_section, "rule": "linear shunt"})
    frame = pd.DataFrame(rows)
    frame["abs_diff"] = (frame.iidm_value.astype(float) - frame.pypsa_value.astype(float)).abs()
    return frame



def iidm_inventory(net) -> dict:
    return {
        "buses_bus_breaker": len(net.get_bus_breaker_view_buses()),
        "lines": len(net.get_lines()),
        "transformers": len(net.get_2_windings_transformers()),
        "generators": len(net.get_generators()),
        "loads": len(net.get_loads()),
        "shunt_compensators": len(net.get_shunt_compensators()),
        "voltage_levels": len(net.get_voltage_levels()),
        "substations": len(net.get_substations()),
    }



def parameter_frames(net) -> dict[str, pd.DataFrame]:
    return {
        "lines": net.get_lines()[["r", "x", "g1", "b1", "g2", "b2", "voltage_level1_id", "bus1_id", "voltage_level2_id", "bus2_id", "name"]],
        "transformers": net.get_2_windings_transformers()[["r", "x", "g", "b", "rated_u1", "rated_u2", "rated_s", "bus1_id", "bus2_id", "name"]],
        "generators": net.get_generators()[["target_p", "target_q", "target_v", "voltage_regulator_on", "min_p", "max_p", "bus_id", "name"]],
        "loads": net.get_loads()[["p0", "q0", "bus_id", "name"]],
        "shunts": net.get_linear_shunt_compensator_sections()[["g_per_section", "b_per_section", "max_section_count"]],
    }



def branch_graph(model: conv.ConversionModel, skip: str | None = None):
    import networkx as nx

    g = nx.MultiGraph()
    g.add_nodes_from(model.buses.bus_id)
    for row in model.lines.itertuples():
        if row.line_id != skip:
            g.add_edge(row.bus0, row.bus1, key=row.line_id)
    for row in model.transformers.itertuples():
        g.add_edge(row.bus0, row.bus1, key=row.transformer_id)
    return g



def select_contingencies(model: conv.ConversionModel, base_lines: pd.DataFrame) -> list[dict]:
    import networkx as nx

    loading = add_loading(base_lines, model.lines.set_index("line_id").s_nom_mva).set_index("line_id")
    lines = model.lines.set_index("line_id")
    islands = {}
    for line_id in lines.index:
        comps = list(nx.connected_components(branch_graph(model, skip=line_id)))
        islands[line_id] = sorted((sorted(c, key=conv._bus_sort_key) for c in comps), key=len)
    is_bridge = {k: len(v) > 1 for k, v in islands.items()}
    chosen: list[dict] = []

    def pick(line_id, rule):
        row = lines.loc[line_id]
        chosen.append({
            "line_id": line_id, "selection_rule": rule, "bus0": row.bus0, "bus1": row.bus1,
            "v_nom_kv": float(row.v_nom_kv), "length_km": float(row.length_km),
            "base_loading_percent": float(loading.at[line_id, "loading_percent"]),
            "charging_mvar_1pu": float(row.b_s * row.v_nom_kv**2),
            "is_bridge": bool(is_bridge[line_id]),
            "isolated_buses_if_out": [b for c in islands[line_id][:-1] for b in c],
        })

    pick(loading.loading_percent.idxmax(), "highest base-case MVA loading")
    hv = lines.loc[(lines.v_nom_kv == 220.0) & ~lines.index.map(is_bridge)]
    hv = hv.drop([c["line_id"] for c in chosen], errors="ignore")
    if len(hv):
        pick((hv.b_s * hv.v_nom_kv**2).idxmax(), "220 kV non-bridge line with largest charging")
    bridges = [k for k, v in is_bridge.items() if v and k not in {c["line_id"] for c in chosen}]
    if bridges:
        s = loading.loc[bridges, ["p0", "q0"]]
        pick(np.hypot(s.p0, s.q0).idxmax(), "bridge line with largest base-case MVA flow")
    return chosen



def run_n1(net, model: conv.ConversionModel, config: dict, base_res: dict) -> tuple[pd.DataFrame, list[dict], dict]:
    import pypowsybl as pp

    lf_cfg = config["openloadflow"]
    slack_vl = "VL_" + model.slack_bus
    slack_gen = model.generators.loc[model.generators.control == "Slack", "generator_id"].iloc[0]
    vmin, vmax = config["n1"]["voltage_screening_pu"]
    line_s = model.lines.set_index("line_id").s_nom_mva
    trafo_s = model.transformers.set_index("transformer_id").s_nom_mva
    base_lines = base_res["lines"]
    selected = select_contingencies(model, base_lines)
    base_variant = net.get_working_variant_id()
    base_connected = net.get_lines()[["connected1", "connected2"]].copy()
    base_bus = base_res["buses"].set_index("bus_id")
    base_line = add_loading(base_lines, line_s).set_index("line_id")
    rows, ui_results, sa_check = [], [], {}

    # Security Analysis API (same pattern as the KPG tool), one contingency per selected line.
    analysis = pp.security.create_analysis()
    for c in selected:
        analysis.add_single_element_contingency("L_" + c["line_id"], "N1_" + c["line_id"])
    analysis.add_monitored_elements(branch_ids=list(net.get_lines().index) + list(net.get_2_windings_transformers().index))
    sa = analysis.run_ac(net, parameters=conv.loadflow_parameters(
        slack_vl, use_reactive_limits=lf_cfg["use_reactive_limits"],
        conv_eps_per_eq=lf_cfg["conv_eps_per_eq"], max_iterations=lf_cfg["max_iterations"]))
    violations = sa.limit_violations.reset_index() if len(sa.limit_violations) else pd.DataFrame(
        columns=["contingency_id", "subject_id", "limit_type", "limit", "value", "side", "limit_name"])
    branch_sa = sa.branch_results.reset_index()
    back = {r.iidm_id: r.original_id for r in model.mapping.itertuples()}

    for c in selected:
        cid = "N1_" + c["line_id"]
        variant = cid
        net.clone_variant(base_variant, variant)
        net.set_working_variant(variant)
        try:
            net.update_lines(id=["L_" + c["line_id"]], connected1=[False], connected2=[False])
            lf = conv.run_ac(net, slack_vl, use_reactive_limits=lf_cfg["use_reactive_limits"],
                             conv_eps_per_eq=lf_cfg["conv_eps_per_eq"], max_iterations=lf_cfg["max_iterations"])
            post = conv.powsybl_results(net, model.mapping, slack_gen, lf["slack_active_power_mismatch_mw"])
        finally:
            net.set_working_variant(base_variant)
            net.remove_variant(variant)
        post_bus = post["buses"].set_index("bus_id")
        post_line = add_loading(post["lines"], line_s).set_index("line_id")
        post_trafo = add_loading(post["transformers"], trafo_s).set_index("transformer_id")
        dv = (post_bus.v_mag_pu - base_bus.v_mag_pu)
        energized = post_bus.v_mag_pu.notna()
        others = post_line.drop(index=c["line_id"])
        dp = (others.p0 - base_line.drop(index=c["line_id"]).p0)
        dq = (others.q0 - base_line.drop(index=c["line_id"]).q0)
        v_viol = post_bus.loc[energized & ((post_bus.v_mag_pu < vmin) | (post_bus.v_mag_pu > vmax))]
        th_viol = pd.concat([others.loc[others.loading_percent > 100], post_trafo.loc[post_trafo.loading_percent > 100]])
        sa_rows = violations.loc[violations.contingency_id == cid]
        sa_status = sa.post_contingency_results[cid].status.name if cid in sa.post_contingency_results else None
        # Cross-check SA branch flows against the manual variant run.
        sa_b = branch_sa.loc[branch_sa.contingency_id == cid].set_index("branch_id")
        common = [i for i in sa_b.index if back.get(i) in post_line.index and back.get(i) != c["line_id"]]
        if common:
            manual_p1 = post_line.loc[[back[i] for i in common], "p0"].to_numpy()
            sa_check[cid] = float(np.nanmax(np.abs(sa_b.loc[common, "p1"].to_numpy() - manual_p1)))
        isolated = sorted(post_bus.index[~energized], key=conv._bus_sort_key)
        gq = post["generators"].merge(model.generators[["generator_id", "control", "q_min_mvar", "q_max_mvar"]], on="generator_id")
        gq = gq.loc[gq.q_mvar.notna() & ~model.generators.set_index("generator_id").bus.reindex(gq.generator_id).isin(isolated).to_numpy()]
        q_viol = gq.loc[(gq.q_mvar < gq.q_min_mvar - 1e-6) | (gq.q_mvar > gq.q_max_mvar + 1e-6)]
        rows.append({
            "contingency_id": cid, "outage_line_id": c["line_id"], "selection_rule": c["selection_rule"],
            "bus0": c["bus0"], "bus1": c["bus1"], "v_nom_kv": c["v_nom_kv"], "length_km": c["length_km"],
            "base_loading_percent": c["base_loading_percent"], "charging_mvar_1pu": c["charging_mvar_1pu"],
            "is_bridge": c["is_bridge"],
            "manual_variant_status": lf["components"][0]["status"] if lf["components"] else "NO_RESULT",
            "manual_variant_iterations": lf["components"][0]["iteration_count"] if lf["components"] else None,
            "security_analysis_status": sa_status,
            "isolated_bus_count": len(isolated), "isolated_buses": " ".join(isolated),
            "isolated_load_mw": float(model.loads.loc[model.loads.bus.isin(isolated), "p_mw"].sum()),
            "isolated_generators": " ".join(model.generators.loc[model.generators.bus.isin(isolated), "generator_id"]),
            "post_v_min_pu": float(post_bus.v_mag_pu.min()), "post_v_max_pu": float(post_bus.v_mag_pu.max()),
            "max_abs_dv_pu": float(dv.abs().max()), "max_abs_dv_bus": str(dv.abs().idxmax()),
            "max_abs_dp_line_mw": float(dp.abs().max()), "max_abs_dp_line_id": str(dp.abs().idxmax()),
            "max_abs_dq_line_mvar": float(dq.abs().max()), "max_abs_dq_line_id": str(dq.abs().idxmax()),
            "post_line_max_loading_percent": float(others.loading_percent.max()),
            "post_slack_p_mw": float(post["generators"].set_index("generator_id").at[slack_gen, "p_mw"]),
            "post_slack_q_mvar": float(post["generators"].set_index("generator_id").at[slack_gen, "q_mvar"]),
            "screening_voltage_violations": len(v_viol),
            "screening_voltage_violation_buses": " ".join(sorted(v_viol.index, key=conv._bus_sort_key)),
            "screening_thermal_violations": len(th_viol),
            "screening_generator_q_violations": len(q_viol),
            "screening_generator_q_violation_ids": " ".join(f"{r.generator_id}({r.q_mvar:.3f})" for r in q_viol.itertuples()),
            "post_generator_q_min_mvar": float(gq.q_mvar.min()), "post_generator_q_max_mvar": float(gq.q_mvar.max()),
            "isolation_matches_graph": sorted(c["isolated_buses_if_out"], key=conv._bus_sort_key) == isolated,
            "sa_limit_violation_count": len(sa_rows),
            "sa_vs_manual_max_abs_p_diff_mw": sa_check.get(cid),
        })
        ui_results.append({
            "outage_line_id": c["line_id"],
            "base_converged": True,
            "post_status": sa_status,
            "analysis_status": "COMPLETED" if sa_status == "CONVERGED" else "SECURITY_NOT_CONVERGED",
            "violation_assessment_available": sa_status == "CONVERGED",
            "violated_equipment_count": int(sa_rows.subject_id.nunique()) if len(sa_rows) else 0,
            "violations": [
                {
                    "equipment_id": back.get(r.subject_id, r.subject_id),
                    "limit_type": r.limit_type, "limit_name": getattr(r, "limit_name", None),
                    "unit": "kV" if "VOLTAGE" in str(r.limit_type) else "MVA",
                    "limit": _clean(float(r.limit)), "value": _clean(float(r.value)),
                }
                for r in sa_rows.itertuples()
            ],
            "isolated_buses": isolated,
            "synthetic_scenario": True,
        })
    restored = net.get_working_variant_id() == base_variant and net.get_variant_ids() == [base_variant] and \
        net.get_lines()[["connected1", "connected2"]].equals(base_connected)
    lf_again = conv.run_ac(net, slack_vl, use_reactive_limits=lf_cfg["use_reactive_limits"],
                           conv_eps_per_eq=lf_cfg["conv_eps_per_eq"], max_iterations=lf_cfg["max_iterations"])
    again = conv.powsybl_results(net, model.mapping, slack_gen, lf_again["slack_active_power_mismatch_mw"])
    restore_dv = float((again["buses"].set_index("bus_id").v_mag_pu - base_bus.v_mag_pu).abs().max())
    restore_dp = float((again["lines"].set_index("line_id").p0 - base_lines.set_index("line_id").p0).abs().max())
    restoration = {"variants_cleaned_and_lines_reconnected": bool(restored),
                   "base_rerun_max_abs_dv_pu": restore_dv, "base_rerun_max_abs_dp_mw": restore_dp,
                   "sa_pre_contingency_status": sa.pre_contingency_result.status.name,
                   "sa_vs_manual_max_abs_p_diff_mw": sa_check}
    return pd.DataFrame(rows), ui_results, restoration



def _generate(paths, config, reports):
    import pypowsybl as pp
    import pypsa
    before = source_manifest(paths)
    tol = config["tolerances"]
    lf_cfg = config["openloadflow"]

    n = load_pypsa(logical_input(paths, config["input_model"]))
    scenario = verify_scenario(n, config, paths=paths)
    stored = conv.pypsa_results(n)        # reference = results stored in the selected NetCDF
    rerun = pypsa_rerun(config, stored, paths=paths)

    model = conv.extract_model(n, q_limits=tuple(config["synthetic_generator_q_limits_mvar"]))
    assert_mapping(n, model)
    net = conv.build_network(model, voltage_limits_pu=tuple(config["synthetic_voltage_screening_pu"]))
    assert_structure(n, model, net)
    slack_vl = "VL_" + model.slack_bus
    slack_gen = model.generators.loc[model.generators.control == "Slack", "generator_id"].iloc[0]

    lf_main = conv.run_ac(net, slack_vl, use_reactive_limits=lf_cfg["use_reactive_limits"],
                          conv_eps_per_eq=lf_cfg["conv_eps_per_eq"], max_iterations=lf_cfg["max_iterations"])
    if not lf_main["converged"]:
        raise RuntimeError(f"PyPowSyBl AC PF did not converge: {lf_main}")
    cand = conv.powsybl_results(net, model.mapping, slack_gen, lf_main["slack_active_power_mismatch_mw"])
    tables = comparison_tables(stored, cand, model)
    bal_ref, bal_cand = system_balance(stored, model), system_balance(cand, model)
    verdict = stats_and_verdict(tables, bal_ref, bal_cand, tol)
    params = conversion_parameters(model, net)

    # Sensitivity cases on independent copies of the converted model.
    sensitivity = {}
    net_q = conv.build_network(model, voltage_limits_pu=tuple(config["synthetic_voltage_screening_pu"]))
    lf_q = conv.run_ac(net_q, slack_vl, use_reactive_limits=True, conv_eps_per_eq=lf_cfg["conv_eps_per_eq"],
                       max_iterations=lf_cfg["max_iterations"])
    res_q = conv.powsybl_results(net_q, model.mapping, slack_gen, lf_q["slack_active_power_mismatch_mw"])
    gq = res_q["generators"].merge(model.generators[["generator_id", "control", "q_min_mvar", "q_max_mvar"]], on="generator_id")
    at_limit = gq.loc[(gq.q_mvar <= gq.q_min_mvar + 1e-6) | (gq.q_mvar >= gq.q_max_mvar - 1e-6)]
    sensitivity["use_reactive_limits_true"] = {
        "lf": lf_q,
        "max_abs_dv_pu_vs_main": float((res_q["buses"].v_mag_pu - cand["buses"].v_mag_pu).abs().max()),
        "max_abs_dq_gen_mvar_vs_main": float((res_q["generators"].q_mvar - cand["generators"].q_mvar).abs().max()),
        "generators_at_q_limit": at_limit[["generator_id", "control", "q_mvar"]].to_dict("records"),
    }
    net_d = conv.build_network(model, voltage_limits_pu=tuple(config["synthetic_voltage_screening_pu"]))
    lf_default = pp.loadflow.run_ac(net_d)   # PyPowSyBl defaults: MOST_MESHED slack, distributed slack
    res_d = conv.powsybl_results(net_d, model.mapping)
    sensitivity["pypowsybl_defaults"] = {
        "status": [r.status.name for r in lf_default],
        "reference_bus_id": [r.reference_bus_id for r in lf_default],
        "parameters": str(pp.loadflow.Parameters()),
        "max_abs_dv_pu_vs_pypsa": float((with_relative_angle(res_d["buses"], model.slack_bus).v_mag_pu
                                         - stored["buses"].v_mag_pu).abs().max()),
        "max_abs_dp_line_mw_vs_pypsa": float((res_d["lines"].p0 - stored["lines"].p0).abs().max()),
        "generator_p_range_mw": [float(res_d["generators"].p_mw.min()), float(res_d["generators"].p_mw.max())],
    }

    # XIIDM save / reload / rerun.
    xiidm = reports / Path(config["powsybl_output"]).name
    xiidm.parent.mkdir(parents=True, exist_ok=True)
    net.save(str(xiidm), format="XIIDM")
    loaded = pp.network.load(str(xiidm))
    frames_before, frames_after = parameter_frames(net), parameter_frames(loaded)
    param_equal = {}
    for k in frames_before:
        try:
            pd.testing.assert_frame_equal(frames_before[k].sort_index(), frames_after[k].sort_index(), check_dtype=False)
            param_equal[k] = True
        except AssertionError as exc:
            param_equal[k] = str(exc)[:500]
    lf_reload = conv.run_ac(loaded, slack_vl, use_reactive_limits=lf_cfg["use_reactive_limits"],
                            conv_eps_per_eq=lf_cfg["conv_eps_per_eq"], max_iterations=lf_cfg["max_iterations"])
    res_reload = conv.powsybl_results(loaded, model.mapping, slack_gen, lf_reload["slack_active_power_mismatch_mw"])
    reload_diff = {}
    for comp, key, fields in (("buses", "bus_id", ["v_mag_pu", "v_ang_deg"]), ("lines", "line_id", BRANCH_FIELDS),
                              ("transformers", "transformer_id", BRANCH_FIELDS), ("generators", "generator_id", ["p_mw", "q_mvar"]),
                              ("shunts", "shunt_id", ["q_absorbed_mvar"])):
        frame = conv.compare(cand[comp], res_reload[comp], key, fields)
        reload_diff[comp] = max(float(frame[f + "_diff"].abs().max()) for f in fields)
    names_kept = set(loaded.get_lines().name) == set(model.lines.line_id) and \
        set(loaded.get_2_windings_transformers().name) == set(model.transformers.transformer_id) and \
        set(loaded.get_generators().name) == set(model.generators.generator_id)
    reload = {
        "path": xiidm.name, "sha256": sha256(xiidm), "size_bytes": xiidm.stat().st_size,
        "inventory_before": iidm_inventory(net), "inventory_after": iidm_inventory(loaded),
        "inventory_equal": iidm_inventory(net) == iidm_inventory(loaded),
        "parameters_equal": param_equal, "original_ids_kept_as_names": bool(names_kept),
        "lf_after_reload": lf_reload, "max_abs_result_diff_after_reload": reload_diff,
    }

    assert_structure(n, model, loaded)
    validation_passed = (verdict["within_all_tolerances"] and lf_main["converged"] and rerun["converged"]
                         and reload["inventory_equal"] and all(v is True for v in param_equal.values())
                         and max(reload_diff.values()) <= 1e-9 and float(params.abs_diff.max()) <= 1e-9)

    n1_frame, n1_ui, restoration = pd.DataFrame(), [], {"skipped": True}
    if validation_passed:
        n1_frame, n1_ui, restoration = run_n1(net, model, config, cand)
    elif not validation_passed:
        restoration = {"skipped": True, "reason": "PyPowSyBl model not validated against PyPSA; N-1 not run"}

    # ---------------------------------------------------------------- outputs
    mapping = model.mapping.copy()
    mapping.to_csv(reports / "element_mapping.csv", index=False)
    params.to_csv(reports / "conversion_parameters.csv", index=False, float_format="%.12g")
    for comp, name in (("buses", "pf_bus_comparison"), ("lines", "pf_line_comparison"),
                       ("transformers", "pf_transformer_comparison"), ("generators", "pf_generator_comparison"),
                       ("shunts", "pf_shunt_comparison")):
        tables[comp].to_csv(reports / f"{name}.csv", index=False, float_format="%.12g")
    if len(n1_frame):
        n1_frame.to_csv(reports / "n1_feasibility_results.csv", index=False, float_format="%.12g")
        write_json(reports / "n1_ui_compatible_results.json", n1_ui)
    summary = {
        "synthetic_scenario_not_actual_operation": True,
        "scenario": scenario,
        "versions": {"pypsa": pypsa.__version__, "pypowsybl": pp.__version__, "python": sys.version.split()[0]},
        "pypsa_reference": "PF results stored in the selected NetCDF",
        "pypsa_rerun": rerun,
        "pypowsybl_lf": lf_main,
        "pypowsybl_lf_parameters": str(conv.loadflow_parameters(slack_vl, use_reactive_limits=lf_cfg["use_reactive_limits"],
                                                                 conv_eps_per_eq=lf_cfg["conv_eps_per_eq"],
                                                                 max_iterations=lf_cfg["max_iterations"])),
        "tolerances": tol,
        "comparison": verdict,
        "sensitivity_cases": sensitivity,
        "validation_passed": bool(validation_passed),
        "n1_restoration": restoration,
    }
    write_json(reports / "pf_summary.json", summary)
    write_json(reports / "conversion_validation.json", {
        "input_scenario": scenario,
        "iidm_inventory": iidm_inventory(net),
        "mapping_counts": mapping.component.value_counts().to_dict(),
        "mapping_complete": {
            "buses": set(mapping.loc[mapping.component == "Bus", "original_id"]) == set(n.buses.index.astype(str)),
            "lines": set(mapping.loc[mapping.component == "Line", "original_id"]) == set(n.lines.index.astype(str)),
            "transformers": set(mapping.loc[mapping.component == "Transformer", "original_id"]) == set(n.transformers.index.astype(str)),
            "generators": set(mapping.loc[mapping.component == "Generator", "original_id"]) == set(n.generators.index.astype(str)),
            "loads": set(mapping.loc[mapping.component == "Load", "original_id"]) == set(n.loads.index.astype(str)),
            "shunts": set(mapping.loc[mapping.component == "Shunt", "original_id"]) == set(n.shunt_impedances.index.astype(str)),
        },
        "max_parameter_abs_diff": float(params.abs_diff.max()),
        "xiidm_reload": reload,
    })
    after = source_manifest(paths)
    write_json(reports / "input_hashes.json", {
        "input_model": {config["input_model"]: sha256(logical_input(paths, config["input_model"]))},
        "config": {"configs/mn35k_powsybl_conversion.json": sha256(logical_input(paths, "configs/mn35k_powsybl_conversion.json"))},
        "source_scripts": {f"scripts/{p}": sha256(logical_input(paths, "scripts/" + p)) for p in ("mn35k_powsybl.py", "convert_mn35k_to_powsybl.py")},
        "scripts": owned_code_hashes(paths),
        "protected_before": before, "protected_after": after, "protected_unchanged": before == after,
        "xiidm_output": {xiidm.name: reload["sha256"]},
    })
    if before != after:
        raise AssertionError("A protected model or report changed")
    return summary


EXPECTED_FILES = ('mn35k_synthetic_powsybl.xiidm', 'element_mapping.csv', 'conversion_parameters.csv', 'pf_bus_comparison.csv', 'pf_line_comparison.csv', 'pf_transformer_comparison.csv', 'pf_generator_comparison.csv', 'pf_shunt_comparison.csv', 'n1_feasibility_results.csv', 'n1_ui_compatible_results.json', 'pf_summary.json', 'conversion_validation.json', 'input_hashes.json', 'runtime_crosscheck.json', 'verification_report.md', 'conversion_provenance.json')


def convert_for_verification(paths, destination, *, config_path=None):
    """Convert only the pinned historical NetCDF, with complete isolated checks."""
    target = artifact_destination(paths, Path(destination))
    config = read_settings(paths, config_path)
    before = source_manifest(paths)
    reference = runtime_observation(paths,
        logical_input(paths, config["powsybl_output"]),
        logical_input(paths, config["reports_dir"]))
    provenance = {"purpose": "CONVERSION_VERIFICATION_ONLY", "activated": False,
                  "source_inputs": before, "owned_code_sha256": owned_code_hashes(paths),
                  "environment": environment(), "source_model_kind": "HISTORICAL_45_GENERATOR_BASELINE"}
    with new_artifact_directory(paths, target, expected_files=EXPECTED_FILES) as staged:
        summary = _generate(paths, config, staged)
        assert_valid_result(staged, summary, config)
        candidate = runtime_observation(paths, staged / "mn35k_synthetic_powsybl.xiidm", staged)
        crosscheck = runtime_comparison(candidate, reference, config)
        # Replace private temporary paths with bundle-relative identifiers; these
        # observations never change the EMS configuration or install a model.
        candidate["runtime_files"] = {"xiidm": "mn35k_synthetic_powsybl.xiidm", "reports": "."}
        write_json(staged / "runtime_crosscheck.json", {"candidate": candidate,
                   "reference": reference, "comparison": crosscheck})
        verdict = summary["comparison"]
        (staged / "verification_report.md").write_text(
            "# MN_35k PyPowSyBl verification\n\n"
            "Purpose: isolated conversion verification; EMS activation: false.\n\n"
            f"Input: `{config['input_model']}`\n\nSHA256: `{config['input_model_sha256']}`\n\n"
            f"PyPSA/OLF PF within original tolerances: {verdict['within_all_tolerances']}\n\n"
            f"EMS PF/N-1/Sensitivity comparison checks: {len(crosscheck['checks'])}, all passed.\n\n"
            "The model is synthetic and its plant placements/parameters are not verified operating data.\n")
        if source_manifest(paths) != before:
            raise ValueError("Conversion inputs changed before publication")
        provenance.update(conversion_verified=True, runtime_compatible=True,
                          xiidm_serialization=xiidm_identity(staged/"mn35k_synthetic_powsybl.xiidm"),
                          artifact_sha256={p.name: sha256(p) for p in sorted(staged.iterdir())})
        write_json(staged / "conversion_provenance.json", provenance)
    return {"artifact": str(target), "validation_passed": True, "activated": False,
            "runtime_checks": len(crosscheck["checks"]),
            "sha256": {p.name: sha256(p) for p in sorted(target.iterdir())}}
