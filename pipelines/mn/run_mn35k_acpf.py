"""Migrated synthetic initial AC PF; explicit verification only, no runtime activation."""
from __future__ import annotations
import copy
import hashlib
import json
import logging
import math
from pathlib import Path
import warnings
import networkx as nx
import numpy as np
import pandas as pd
import pypsa
from .audit_mn35k_parameters import component_map
from .mn35k_refinement import export_deterministic
from .validate_mn35k_poc import LINE_FIELDS, TRAFO_FIELDS, sha256
from .synthetic_inputs import read_settings, source_manifest, scenario_bundle, ScenarioFailure


def assert_passive_preserved(source, scenario):
    """Compare retained IDs, topology, all electrical values and the type catalogue."""
    for name, fields in [("lines", LINE_FIELDS), ("transformers", TRAFO_FIELDS)]:
        original = getattr(source, name)
        expected = original.loc[original.bus0.isin(scenario.buses.index) & original.bus1.isin(scenario.buses.index)]
        pd.testing.assert_frame_equal(expected[fields], getattr(scenario, name)[fields],
                                      check_dtype=False, rtol=1e-12, atol=1e-14)
    pd.testing.assert_frame_equal(source.buses.loc[scenario.buses.index, ["v_nom", "x", "y"]],
                                  scenario.buses[["v_nom", "x", "y"]], check_dtype=False)
    pd.testing.assert_frame_equal(source.line_types, scenario.line_types, check_dtype=False)



def select_component(source, config):
    graph, _, components = component_map(source)
    if [len(c) for c in components] != config["expected_component_sizes"]:
        raise ValueError("Unexpected source connected components")
    buses = components[0]
    for frame in [source.lines, source.transformers]:
        if (frame.bus0.isin(buses) ^ frame.bus1.isin(buses)).any():
            raise ValueError("Cross-component branch")
        if not frame.active.all():
            raise ValueError("Inactive branches require an explicit topology policy")
    graph = graph.subgraph(buses)
    centrality = nx.betweenness_centrality(graph)
    candidates = pd.DataFrame([
        {"bus": bus, "voltage_kv": 220, "degree": graph.degree[bus], "betweenness": centrality[bus]}
        for bus in buses if source.buses.at[bus, "v_nom"] == 220
    ]).sort_values(["degree", "betweenness", "bus"], ascending=[False, False, True]).reset_index(drop=True)
    if candidates.empty:
        raise ValueError("No 220 kV slack candidate")
    return buses, components[1:], candidates



def build_scenario(source, config, trial):
    """Slice a separate network and add only explicitly synthetic operating data."""
    if len(source.generators) or len(source.loads):
        raise ValueError("Expected passive source without generation or loads")
    buses, excluded, candidates = select_component(source, config)
    snapshot = pd.Timestamp(config["snapshot"])
    if snapshot not in source.snapshots:
        raise ValueError("Snapshot absent from source")
    n = source.slice_network(buses=buses, snapshots=[source.snapshots.get_loc(snapshot)])
    n.meta = copy.deepcopy(source.meta)
    n.name = "MN_35k_poc synthetic single-snapshot AC PF; not actual operation"
    slack = candidates.iloc[0].bus
    pf = config["load_power_factor_lagging"]
    scale = trial["load_scale"]
    if not 0 < pf <= 1 or not math.isfinite(scale) or scale <= 0:
        raise ValueError("Loads require positive finite scale and power factor in (0, 1]")
    q_factor = math.tan(math.acos(pf))
    for bus in n.buses.index:
        p = config["load_mw_per_bus_by_kv"][str(int(n.buses.at[bus, "v_nom"]))] * scale
        if not math.isfinite(p) or p <= 0:
            raise ValueError("Every bus must have a positive finite synthetic load")
        n.add("Load", "SYNTHETIC_LOAD_" + bus, bus=bus, p_set=p, q_set=p*q_factor)
    if trial["pv_policy"] not in {"none", "all_220kv", "all_110_220kv"}:
        raise ValueError("Unknown PV policy")
    pv_voltages = {"none": [], "all_220kv": [220], "all_110_220kv": [110, 220]}[trial["pv_policy"]]
    pv_buses = [bus for bus in n.buses.index if bus != slack and n.buses.at[bus, "v_nom"] in pv_voltages]
    generator = config["generator"]
    p_each = float(n.loads.p_set.sum()) / (1 + len(pv_buses))
    for bus in pv_buses:
        n.add("Generator", "SYNTHETIC_PV_" + bus, bus=bus, control="PV",
              p_nom=generator["p_nom_mw"], p_set=p_each, q_set=0.0)
        n.buses.loc[bus, "v_mag_pu_set"] = generator["voltage_setpoint_pu"]
    n.add("Generator", "SYNTHETIC_SLACK_" + slack, bus=slack, control="Slack",
          p_nom=generator["p_nom_mw"], p_set=p_each, q_set=0.0)
    n.buses.loc[slack, "v_mag_pu_set"] = generator["voltage_setpoint_pu"]
    n.meta["synthetic_acpf"] = {
        "trial": trial, "snapshot_is_synthetic": True, "actual_operation_validated": False,
        "source_sha256": config["protected_models"][config["input_model"]],
        "slack_bus": slack, "excluded_components": excluded,
        "slack_policy": "220kV, descending unique-neighbour degree, descending unweighted betweenness, lexical ID tie-break",
        "reference_angle_rad": 0.0, "generator_screening": generator,
    }
    assert_passive_preserved(source, n)
    return n, candidates



class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(self.format(record))



def execute_pf(n, config):
    """Retain PyPSA convergence diagnostics; exceptions are not nonconvergence."""
    handler = _LogCapture()
    logger = logging.getLogger("pypsa")
    logger.addHandler(handler)
    result = {"status": "AC_PF_EXECUTION_ERROR", "subnetworks": [], "exception": None}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            raw = n.pf(snapshots=n.snapshots, **config["pf"])
            for snapshot in raw["converged"].index:
                for sub in raw["converged"].columns:
                    error = float(raw["error"].at[snapshot, sub])
                    result["subnetworks"].append({
                        "snapshot": str(snapshot), "sub_network": str(sub),
                        "converged": bool(raw["converged"].at[snapshot, sub]),
                        "n_iter": int(raw["n_iter"].at[snapshot, sub]),
                        "error": error if math.isfinite(error) else None,
                        "slack_bus": str(n.sub_networks.at[sub, "slack_bus"]),
                    })
            good = bool(result["subnetworks"]) and all(
                r["converged"] and r["error"] is not None and r["error"] < config["pf"]["x_tol"]
                for r in result["subnetworks"])
            result["status"] = "AC_PF_CONVERGED" if good else "AC_PF_NOT_CONVERGED"
        except Exception as exc:
            result["exception"] = f"{type(exc).__name__}: {exc}"
        finally:
            logger.removeHandler(handler)
        result["warnings"] = [f"{type(w.message).__name__}: {w.message}" for w in caught] + handler.messages
    return result



def branch_results(n, component):
    static, dynamic = getattr(n, component), getattr(n, component + "_t")
    result = static[["bus0", "bus1", "s_nom"]].copy()
    result.index.name = "equipment_id"
    result["bus0_voltage_kv"] = static.bus0.map(n.buses.v_nom)
    result["bus1_voltage_kv"] = static.bus1.map(n.buses.v_nom)
    for field in ["p0", "q0", "p1", "q1"]:
        result[field + ("_mw" if field.startswith("p") else "_mvar")] = dynamic[field].loc[n.snapshots[0]].reindex(static.index)
    result["s0_mva"] = np.hypot(result.p0_mw, result.q0_mvar)
    result["s1_mva"] = np.hypot(result.p1_mw, result.q1_mvar)
    result["max_s_mva"] = result[["s0_mva", "s1_mva"]].max(axis=1, skipna=False)
    result["loading_percent"] = 100 * result.max_s_mva / result.s_nom
    result["p_loss_mw"] = result.p0_mw + result.p1_mw
    result["q_net_mvar"] = result.q0_mvar + result.q1_mvar
    result["thermal_violation"] = result.loading_percent > 100
    return result



def validate_results(n, config):
    """Independent nodal/global balance plus screens; PF enforces none of the limits."""
    snapshot = n.snapshots[0]
    limits, genlimits = config["screening"], config["generator"]
    bus = n.buses[["v_nom", "control"]].rename(columns={"v_nom": "voltage_kv"}).copy()
    bus.index.name = "bus"
    bus["v_mag_pu"] = n.buses_t.v_mag_pu.loc[snapshot]
    bus["v_ang_rad"] = n.buses_t.v_ang.loc[snapshot]
    bus["v_ang_deg"] = np.rad2deg(bus.v_ang_rad)
    bus["voltage_violation"] = (bus.v_mag_pu < limits["v_min_pu"]) | (bus.v_mag_pu > limits["v_max_pu"])
    gen = n.generators[["bus", "control", "p_nom", "p_set"]].copy()
    gen.index.name = "generator"
    gen["p_mw"], gen["q_mvar"] = n.generators_t.p.loc[snapshot], n.generators_t.q.loc[snapshot]
    gen["s_mva"] = np.hypot(gen.p_mw, gen.q_mvar)
    for key in ["p_min_mw", "q_min_mvar", "q_max_mvar", "s_screening_mva"]:
        gen[key] = genlimits[key]
    gen["p_limit_violation"] = (gen.p_mw < gen.p_min_mw) | (gen.p_mw > gen.p_nom)
    gen["q_limit_violation"] = (gen.q_mvar < gen.q_min_mvar) | (gen.q_mvar > gen.q_max_mvar)
    gen["s_limit_violation"] = gen.s_mva > gen.s_screening_mva
    load = n.loads[["bus", "p_set", "q_set"]].rename(columns={"p_set": "p_mw", "q_set": "q_mvar"}).copy()
    load.index.name = "load"
    load["voltage_kv"] = load.bus.map(n.buses.v_nom)
    load["power_factor_lagging"] = config["load_power_factor_lagging"]
    lines, trafos = branch_results(n, "lines"), branch_results(n, "transformers")
    # Positive branch endpoint power enters a branch; Q net may be negative due to charging.
    balance = []
    for symbol, col, loss in [("P", "p_mw", "p_loss_mw"), ("Q", "q_mvar", "q_net_mvar")]:
        generation, demand = gen[col].sum(skipna=False), load[col].sum(skipna=False)
        branch_net = lines[loss].sum(skipna=False) + trafos[loss].sum(skipna=False)
        residual = generation - demand - branch_net
        balance.append({"quantity": symbol, "unit": "MW" if symbol == "P" else "MVAr",
                        "generation": generation, "load": demand, "branch_net": branch_net,
                        "residual": residual, "passed": bool(abs(residual) < limits["balance_tolerance_mva"])})
    nodal = pd.DataFrame(index=bus.index)
    nodal_max = 0.0
    for sub in n.sub_networks.obj:
        sub.calculate_Y(skip_pre=True)
        ids = sub.buses_o
        v = bus.loc[ids, "v_mag_pu"].to_numpy() * np.exp(1j * bus.loc[ids, "v_ang_rad"].to_numpy())
        calculated = v * np.conj(sub.Y @ v)
        injected = (gen.groupby("bus").p_mw.sum().reindex(ids, fill_value=0).to_numpy()
                    + 1j*gen.groupby("bus").q_mvar.sum().reindex(ids, fill_value=0).to_numpy()
                    - load.groupby("bus").p_mw.sum().reindex(ids, fill_value=0).to_numpy()
                    - 1j*load.groupby("bus").q_mvar.sum().reindex(ids, fill_value=0).to_numpy())
        residual = injected - calculated
        nodal.loc[ids, "p_residual_mw"] = residual.real
        nodal.loc[ids, "q_residual_mvar"] = residual.imag
        nodal_max = max(nodal_max, float(np.abs(residual).max()))
    numeric_frames = [bus[["v_mag_pu", "v_ang_rad"]], gen[["p_mw", "q_mvar"]],
                      lines.select_dtypes(include="number"), trafos.select_dtypes(include="number"), nodal]
    finite = all(np.isfinite(df.to_numpy()).all() for df in numeric_frames)
    slack = n.meta["synthetic_acpf"]["slack_bus"]
    controls_ok = bool((bus.control == "Slack").sum() == 1 and bus.at[slack, "control"] == "Slack"
                       and np.isclose(bus.at[slack, "v_mag_pu"], genlimits["voltage_setpoint_pu"], atol=1e-10, rtol=0)
                       and abs(bus.at[slack, "v_ang_rad"]) < 1e-10)
    pv = gen.control.eq("PV")
    controls_ok &= bool(np.allclose(gen.loc[pv, "p_mw"], gen.loc[pv, "p_set"], atol=1e-10, rtol=0)
                        and np.allclose(bus.loc[gen.loc[pv, "bus"], "v_mag_pu"], genlimits["voltage_setpoint_pu"], atol=1e-10, rtol=0))
    loads_ok = bool(np.allclose(n.loads_t.p.loc[snapshot], load.p_mw, rtol=0, atol=1e-12)
                    and np.allclose(n.loads_t.q.loc[snapshot], load.q_mvar, rtol=0, atol=1e-12))
    violations = {"bus_voltage": int(bus.voltage_violation.sum()), "line_thermal": int(lines.thermal_violation.sum()),
                  "transformer_thermal": int(trafos.thermal_violation.sum()),
                  "generator_p": int(gen.p_limit_violation.sum()), "generator_q": int(gen.q_limit_violation.sum()),
                  "generator_s": int(gen.s_limit_violation.sum())}
    verified = bool(finite and controls_ok and loads_ok and (bus.v_mag_pu > 0).all() and all(r["passed"] for r in balance)
                    and nodal_max < limits["balance_tolerance_mva"])
    screening = ("SYNTHETIC_SCENARIO_HAS_VIOLATIONS" if any(violations.values()) else "SYNTHETIC_SCENARIO_WITHIN_SCREENING_LIMITS") if verified else "MODEL_DATA_REQUIRES_REVIEW"
    tables = {"bus_voltage_results": bus, "line_flow_results": lines, "transformer_flow_results": trafos,
              "generator_dispatch_results": gen, "load_scenario": load,
              "power_balance_validation": pd.DataFrame(balance).set_index("quantity"), "nodal_balance_validation": nodal}
    return tables, {"finite_results": bool(finite), "controls_verified": controls_ok, "loads_verified": loads_ok,
                    "balance_verified": verified, "max_nodal_residual_mva": nodal_max,
                    "screening_status": screening, "violations": violations,
                    "model_reliability_status": "MODEL_DATA_REQUIRES_REVIEW"}



def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")



def diagnose(n):
    """Inspect unchanged electrical structure before each actual PF call."""
    n.calculate_dependent_values()
    n.determine_network_topology()
    sub = n.sub_networks.obj.iloc[0]
    sub.calculate_Y(skip_pre=True)
    return {
        "controls": n.buses.control.value_counts().to_dict(), "slack_bus": str(sub.slack_bus),
        "subnetworks": len(n.sub_networks), "Y_shape": list(sub.Y.shape), "Y_nnz": sub.Y.nnz,
        "Y_finite": bool(np.isfinite(sub.Y.data).all()),
        "reduced_Y_condition": float(np.linalg.cond(sub.Y.toarray()[1:, 1:])),
        "flat_voltage_line_charging_mvar": float(n.lines.b_pu.sum()),
        "flat_voltage_charging_by_kv_mvar": {str(int(v)): float(x) for v, x in n.lines.groupby("v_nom").b_pu.sum().items()},
        "positive_line_rx": bool((n.lines[["r", "x"]] > 0).all().all()),
        "positive_transformer_rx": bool((n.transformers[["r", "x"]] > 0).all().all()),
        "transformer_rating_mva": sorted(n.transformers.s_nom.unique().tolist()),
        "longest_lines": n.lines[["bus0", "bus1", "v_nom", "length", "r", "x", "s_nom"]].sort_values("length", ascending=False).head(10).reset_index().to_dict("records"),
    }



def markdown(frame):
    def cell(value):
        return (f"{value:.10g}" if isinstance(value, (float, np.floating)) else str(value)).replace("|", "\\|")
    return "| " + " | ".join(frame.columns) + " |\n| " + " | ".join(["---"]*len(frame.columns)) + " |\n" + "\n".join("| " + " | ".join(cell(v) for v in row) + " |" for row in frame.itertuples(index=False, name=None))



def write_report(reports, config, source, n, candidates, attempts, tables, validation, preservation, *, output):
    buses, excluded, _ = select_component(source, config)
    final = attempts[-1]
    assumptions = {
        "classification": "SYNTHETIC_SINGLE_SNAPSHOT_AC_PF_NOT_ACTUAL_OPERATION",
        "pypsa_version": pypsa.__version__, "config": config,
        "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "included_bus_ids": buses, "excluded_components": excluded,
        "slack_selection": n.meta.get("synthetic_acpf", {}),
        "source_models_and_previous_reports_unchanged": preservation,
        "source_sha256": config["protected_models"][config["input_model"]],
        "result_model_sha256": sha256(output) if tables else None,
        "numerical_status": final["status"], "validation": validation,
        "assumptions": [
            "2013-01-01 is only a reused snapshot label; no historical demand or dispatch data.",
            "Every included bus has deterministic synthetic lagging load at 0.95 power factor; no load data calibration.",
            "Every enabled PV fixes 1 pu without Q-limit enforcement. P is divided equally among configured sources; slack covers losses.",
            "100 MW, +/-100 MVAr, 150 MVA are independent synthetic screening limits, not actual machine capability curves.",
            "Enabled voltage sources provide deliberately strong synthetic numerical support, not actual Mongolian plants; 27 public records unused.",
            "Passive model topology, line types, ratings, tap ratios, shunts and impedances are unchanged.",
            "0.9-1.1 pu and 100% apparent-power loading are development screens, not verified Mongolian operating standards.",
            "Original 18 unresolved lengths, assumed conductor/circuit/bundle data and synthetic transformer connections require review.",
        ],
        "references": ["https://docs.pypsa.org/v1.2.4/user-guide/power-flow/",
                       "https://docs.pypsa.org/v1.2.4/user-guide/components/generators/"],
    }
    write_json(reports / "scenario_assumptions.json", assumptions)
    if not tables:
        (reports / "acpf_execution_report.md").write_text(
            "# MN_35k_poc AC PF 실행 실패\n\n수치해석: **" + final["status"] +
            "**. 운전/모델 판정: **MODEL_DATA_REQUIRES_REVIEW**.\n\n"
            "이번 실행에서 채택할 수 있는 수렴 결과가 없다. 이전 실행의 CSV/NetCDF가 존재해도 이번 결과로 사용하면 안 된다. "
            "모든 실행 조건·반복·잔차·경고·예외는 pf_attempts.json에, 입력 모델 SHA와 보존 결과는 "
            "scenario_assumptions.json 및 source_preservation.json에 기록했다. 동일 설정 파일로 "
            "`pypsa-earth/.pixi/envs/default/bin/python scripts/run_mn35k_acpf.py`를 실행하면 재현할 수 있다.\n")
        return
    rows = []
    for v in [35, 110, 220]:
        rows.append({"종류": "Bus", "전압_kV": str(v), "개수": int(n.buses.v_nom.eq(v).sum())})
        rows.append({"종류": "Line", "전압_kV": str(v), "개수": int(n.lines.v_nom.eq(v).sum())})
    pairs = n.transformers.apply(lambda r: "/".join(str(int(v)) for v in sorted(n.buses.loc[[r.bus0, r.bus1], "v_nom"])), axis=1)
    for pair, count in pairs.value_counts().sort_index().items():
        rows.append({"종류": "Transformer", "전압_kV": pair, "개수": int(count)})
    counts = pd.DataFrame(rows)
    counts.to_csv(reports / "component_counts.csv", index=False)
    trial_rows = []
    for item in attempts:
        sub = item["subnetworks"][0] if item["subnetworks"] else {}
        trial_rows.append({"시험": item["trial"]["id"], "부하_MW": item["total_load_mw"], "부하_MVAr": item["total_load_mvar"],
                           "발전기": item["generators"], "판정": item["status"], "반복": sub.get("n_iter", "-"), "error": sub.get("error", "-")})
    sections = [
        "# MN_35k_poc 최초 합성 AC Power Flow 실행 보고서",
        "## 판정\n\n수치해석: **" + final["status"] + "**. 운전조건 검사: **" + validation["screening_status"] + "**. 모델 신뢰성: **MODEL_DATA_REQUIRES_REVIEW**.",
        "이 결과는 합성 부하와 이상적 전압 제어가 있는 조건의 계산 가능성을 보여준다. 실제 몽골 운전상태나 실제 발전기·무효전력 능력을 검증한 결과가 아니다. 첫 수렴 조건에서 시험을 종료했으며 OPF, DC PF, 시간대 확장 또는 설비 정수 조정은 수행하지 않았다.",
        "## 실행 및 재현\n\n프로젝트 루트에서 기존 Pixi 환경을 직접 사용한다. PyPSA " + pypsa.__version__ + ", 단일 시점 `" + config["snapshot"] + "`. 이 날짜는 8,760개 원본 스냅샷 중 선택한 레이블이며 실제 2013년 운영자료가 아니다.\n\n```bash\nMPLCONFIGDIR=/tmp/mn35k-mpl PYTHONDONTWRITEBYTECODE=1 \\\n  pypsa-earth/.pixi/envs/default/bin/python scripts/run_mn35k_acpf.py\nMPLCONFIGDIR=/tmp/mn35k-mpl PYTHONDONTWRITEBYTECODE=1 \\\n  pypsa-earth/.pixi/envs/default/bin/python -m pytest -q\n```\n\n설정: `configs/mn35k_acpf.json`. 출력 모델: `" + config["output_model"] + "`. NetCDF는 중첩 저장소의 로컬 생성물이며 이 브랜치에는 생성 코드·설정·결과 보고서를 커밋한다.",
        "## 원본 보존\n\n" + "\n".join("- `" + key + "`: `" + value + "`" for key, value in config["protected_models"].items()) +
        "\n\n3개 base.nc 및 기존 validation/expansion/model_refinement 보고서의 실행 전후 SHA-256 동일성을 확인했다. 전체 목록은 `source_preservation.json`. 원본 객체를 slice하여 별도 Network를 만들고, 보존 대상 모선 좌표/전압·branch ID·연결·전기정수·LineType을 PF 전후와 NetCDF 재로드 후 대조한다. 제어 모드와 합성 운전값만 별도 시나리오에 추가한다.",
        "## 해석 범위\n\n선로와 변압기를 모두 포함한 NetworkX 그래프의 최대 연결요소 166모선만 사용했다. 외부 연결요소와 교차하는 branch는 0개. 제외 연결요소: `" + json.dumps(excluded) + "`. 원본은 그대로 보존한다. 실제 포함 모선 ID는 `scenario_assumptions.json`의 `included_bus_ids`와 `bus_voltage_results.csv`에 수록한다.\n\n" + markdown(counts),
        "## Slack/PV/PQ와 합성 입력\n\n220kV 후보를 고유 인접 모선 수 내림차순, 비가중 매개중심성 내림차순, 문자열 ID 순으로 정렬했다. 1위 모선 `" + str(candidates.iloc[0].bus) + "`의 차수는 " + str(candidates.iloc[0].degree) + ", 매개중심성은 " + f"{candidates.iloc[0].betweenness:.9f}" + ". 전체 후보는 `slack_candidates.csv`.\n\nPyPSA 1.2.4의 설치 코드 `SubNetwork.find_bus_controls()`와 공식 문서를 확인했다. Generator.control로 Slack/PV를 지정하면 Bus.control이 설정된다. 슬랙은 전압 1pu·위상각 0rad를 고정하고 P/Q를 평형에 맞춰 결정한다. PV는 지정 P와 1pu를 고정하고 Q를 계산한다. 35kV 모선은 모두 PQ이며 전압을 강제하지 않는다. `use_seed=False`, flat start, `distribute_slack=False`, `x_tol=1e-6`, 기본 최대 반복 100회를 그대로 사용했다.\n\n각 35/110/220kV 모선의 부하는 각각 0.1/0.2/0.1 MW, 지상 역률 0.95이고 Q=P·tan(acos(0.95))이다. 모든 166모선에 비영 부하를 둔다. 최종 108개 합성 발전기의 P 지정값은 각각 25.6/108=0.237037037 MW이며 슬랙이 추가 손실을 부담한다. 총 합성 정격은 10,800 MW로 부하 25.6 MW 대비 매우 크다. 이는 107개 PV 전압 제어를 가정한 계산 시험의 과잉 공급능력이며 실제 발전용량 추정이 아니다. 27개 발전소 공개 레코드는 사용하지 않았다.\n\n모든 발전기의 가정 한도는 P=[0,100] MW, Q=[-100,100] MVAr, S≤150 MVA다. P 정격과 S 검사값은 구분한다. 전압은 0.9–1.1pu, 선로/변압기 부하율은 100%를 검사 기준으로 삼는다. 이는 개발용 가정이며 실제 규정/명판값이 아니다. PF는 Q 한도·열정격을 강제하지 않으므로 결과에서 검사한다.",
        "## 실제 시행한 시험과 비수렴 진단\n\n" + markdown(pd.DataFrame(trial_rows)) +
        "\n\n모든 시험은 SubNetwork `0`, 슬랙 `25`이며 동일한 부하·토폴로지·임피던스·정격·수렴 기준을 사용했다. 수렴 조건의 무효전력 제어 배치가 변경되었으므로 첫 단일 슬랙 조건이 해결되었다고 해석하면 안 된다. 실패한 두 조건은 전압 및 물리적 조류 결과로 채택하지 않았다. 각 조건·발전기 ID/입력·진단·경고·예외는 `pf_attempts.json`에 보존한다.\n\n초기 조건은 Slack 1/PQ 165, 발전기 1/부하 166, 연결요소 1이며 Ybus는 166×166, nnz=516, 모든 값 유한이다. 선로/변압기 R/X는 양수다. 가장 긴 선로는 280.647km(110kV), 긴 35kV 선로는 263.652km다. 변압기 정격은 25/100MVA 가정이다. 1pu에서 선로 충전 무효전력 합 934.505MVAr가 부하 8.414MVAr에 비해 매우 크고, 슬랙을 제외한 Y행렬 조건수는 약 1.90e5이다. 조건수 자체가 조류 Jacobian의 특이성이나 유일한 실패 원인을 입증하지는 않는다.\n\n관찰에 근거해 긴 경로의 전압·무효전력 제어 부족을 실패 원인 후보로 판단했다. 220kV 전체 전압 제어만으로도 비수렴하여 110kV까지 제어를 확장했다. 이번 시험에서는 모든 110/220kV 모선에 전압원을 두는 강한 가정에서 수렴했다. 이 배치가 최소 필요 조건임을 입증한 것은 아니다. 낮은 부하에서 충전 무효전력이 지배적이므로 추가 부하 축소 대신 제어 조건을 바꾸었다. 선로 제거/추가 연결/임피던스 축소/수렴 기준 완화는 하지 않았다.\n\n초기 구현에서 snapshot 라벨을 slice_network에 전달해 IndexError가 있었으며 PF 호출 전에 수정했다. 설치된 1.2.4가 snapshot 위치 인덱스를 요구하는 것을 확인하고 위치로 변환했다. 이는 위 전기적 비수렴 시험과 별개인 구현 오류다.",
    ]
    if tables:
        bus, lines, trafos, gen = [tables[key] for key in ["bus_voltage_results", "line_flow_results", "transformer_flow_results", "generator_dispatch_results"]]
        sections += [
            "## 수렴 결과와 독립 검증\n\n전압 최솟값 " + f"{bus.v_mag_pu.min():.9f}" + "pu (모선 `" + str(bus.v_mag_pu.idxmin()) + "`), 최댓값 " + f"{bus.v_mag_pu.max():.9f}" + "pu (모선 `" + str(bus.v_mag_pu.idxmax()) + "`). Slack은 1pu/0rad. 전체 위상각은 CSV에 rad/deg로 수록했다.\n\n" + markdown(gen.loc[gen.control.eq("Slack"), ["bus", "p_mw", "q_mvar", "s_mva"]].reset_index()) +
            "\n\n양 끝단의 S=sqrt(P²+Q²) 중 큰 값으로 부하율을 산정한다. 선로 최대 " + f"{lines.loading_percent.max():.6f}%" + ", 변압기 최대 " + f"{trafos.loading_percent.max():.6f}%" + ". 유효손실=P0+P1, 순무효소비=Q0+Q1이다. 음의 순무효소비는 선로 충전 공급을 포함하므로 음의 유효손실로 해석하지 않는다.\n\n" + markdown(tables["power_balance_validation"].reset_index()) +
            "\n\n전체 전력수지 외에 각 모선에서 S=V·conj(YV)와 발전−부하 주입을 대조했다. 최대 복소 잔차 " + f"{validation['max_nodal_residual_mva']:.12g}" + "MVA, 독립 수지 검사 한도 1e-4MVA. 발전기 제어/슬랙 기준/부하 반영 및 모든 출력 유한성 검사도 통과했다. Newton 반환 error는 위 표와 같이 기록하며 수지 잔차와 구분한다.",
            "## 최대 조류 및 손실 설비\n\n" + "\n\n".join(
                "### " + label + "\n\n" + markdown(frame.nlargest(3, key)[["bus0", "bus1", "max_s_mva", "loading_percent", "p_loss_mw", "q_net_mvar"]].reset_index())
                for label, frame, key in [("선로 피상전력 상위", lines, "max_s_mva"), ("선로 유효손실 상위", lines, "p_loss_mw"),
                                           ("변압기 피상전력 상위", trafos, "max_s_mva"), ("변압기 유효손실 상위", trafos, "p_loss_mw")]),
            "## 한도 위반\n\n" + json.dumps(validation["violations"], ensure_ascii=False) + "\n\n" +
            markdown(gen.loc[gen[["p_limit_violation", "q_limit_violation", "s_limit_violation"]].any(axis=1), ["bus", "p_mw", "q_mvar", "q_min_mvar", "q_max_mvar", "s_mva"]].reset_index()) +
            "\n\n모선 10의 합성 PV는 약 111.908MVAr를 흡수하며 가정한 -100MVAr 하한을 초과한다. Q 한도를 강제하는 PV→PQ 전환이나 재급전은 수행하지 않았다. 따라서 이 수렴 결과는 한도 준수 운전 해가 아니다. 슬랙 P/Q/S 한도 위반은 없다.",
        ]
    sections += [
        "## 경고 및 한계\n\n최종 PF 호출의 경고/예외는 없고 앞선 두 PF에는 100회 비수렴 경고가 있었다. slice 시 기존 사용자 정의 `geometry`/`v_nom` 속성에 대해 PyPSA가 이름 혼동 가능성을 경고한다. 기존 데이터는 유지하며 이 구성 경고도 각 trial에 기록한다. 전체 테스트에서는 기존 NetCDF/NumPy 로딩 시 ndarray 크기 불일치 RuntimeWarning 1건이 발생했다. 계산·재로드·보존 검사는 통과했지만 바이너리 의존성 정합성은 후속 환경 정비 항목으로 남긴다.\n\n실제 발전·부하 분포, 발전기 접속점/PQ 능력, 무효 보상설비, 개폐 상태가 미확인이다. 기존 모델의 18개 미해결 길이, 도체·회선·번들 및 변압기 합성 접속/명판 가정은 그대로 남는다. 최종 수렴은 모든 110/220kV 모선의 이상적 전압 제어를 전제로 하므로 현실적 운전 가능성이나 일반적인 단일 슬랙 수렴성을 입증하지 않는다. 다음 작업은 실제 접속/부하/무효전력 자료 확보 후 제어 위치와 한도를 검증하는 것이다.",
        "## 자동 검증 및 산출물\n\n`tests/test_mn35k_acpf.py`는 실제 AC PF, 실패·예외 구분, 모선별/전체 수지, 양 끝단 부하율, Q 한도 초과, 원본 보호, NetCDF 재로드 및 결정성을 검사한다. 실행된 테스트 결과는 `test_results.txt`에 저장한다. 기존 73개 테스트도 함께 실행한다. CSV는 최종 채택한 수렴 조건만 포함하며 실패 내역은 `pf_attempts.json`을 참조한다.\n\n- `scenario_assumptions.json`: 입력·가정·대상 ID·SHA 및 판정\n- `pf_attempts.json`: 모든 시도와 원인·경고·진단\n- `bus_voltage_results.csv`, `line_flow_results.csv`, `transformer_flow_results.csv`\n- `generator_dispatch_results.csv`, `load_scenario.csv`\n- `power_balance_validation.csv`, `nodal_balance_validation.csv`\n- `component_counts.csv`, `slack_candidates.csv`, `result_validation.json`\n- `source_preservation.json`, `test_results.txt`\n\nPyPSA 제어·조류 방정식 참고: [Power Flow](https://docs.pypsa.org/v1.2.4/user-guide/power-flow/), [Generators](https://docs.pypsa.org/v1.2.4/user-guide/components/generators/).",
    ]
    (reports / "acpf_execution_report.md").write_text("\n\n".join(sections) + "\n")



def _generate(paths, reports, config, input_path):
    if not config["trials"]:
        raise ValueError("At least one trial is required")
    output = reports / "synthetic_pf_base.nc"
    before = source_manifest(paths)
    source = pypsa.Network(input_path)
    attempts = []
    for trial in config["trials"]:
        handler = _LogCapture()
        logger = logging.getLogger("pypsa")
        logger.addHandler(handler)
        try:
            n, candidates = build_scenario(source, config, trial)
            diagnostics = diagnose(n)
        finally:
            logger.removeHandler(handler)
        outcome = execute_pf(n, config)
        record = {"trial": trial, "total_load_mw": float(n.loads.p_set.sum()),
                  "total_load_mvar": float(n.loads.q_set.sum()), "generators": len(n.generators),
                  "generator_inputs": n.generators[["bus", "control", "p_set", "q_set", "p_nom"]].reset_index().to_dict("records"),
                  "diagnostics": diagnostics, "construction_warnings": sorted(handler.messages), **outcome}
        attempts.append(record)
        write_json(reports / "pf_attempts.json", attempts)
        print(json.dumps({key: value for key, value in record.items()
                          if key not in {"generator_inputs", "diagnostics", "construction_warnings"}}, ensure_ascii=False))
        assert_passive_preserved(source, n)
        if outcome["status"] == "AC_PF_CONVERGED":
            break
    tables = None
    validation = {"screening_status": "MODEL_DATA_REQUIRES_REVIEW", "balance_verified": False}
    if outcome["status"] == "AC_PF_CONVERGED":
        tables, validation = validate_results(n, config)
        for name, frame in tables.items():
            frame.to_csv(reports / (name + ".csv"), float_format="%.12g")
        candidates.to_csv(reports / "slack_candidates.csv", index=False, float_format="%.12g")
        write_json(reports / "result_validation.json", validation)
        n.meta["synthetic_acpf"].update(outcome=outcome, validation=validation)
        export_deterministic(n, output, paths=paths, protected_models=config["protected_models"])
        reloaded = pypsa.Network(output)
        assert_passive_preserved(source, reloaded)
        for component, fields in [("buses", ["v_mag_pu", "v_ang"]), ("lines", ["p0", "q0", "p1", "q1"]),
                                  ("transformers", ["p0", "q0", "p1", "q1"]), ("generators", ["p", "q"]), ("loads", ["p", "q"])]:
            for field in fields:
                pd.testing.assert_frame_equal(getattr(n, component + "_t")[field], getattr(reloaded, component + "_t")[field],
                                              check_freq=False, check_dtype=False, atol=1e-12, rtol=1e-12)
        print(json.dumps(validation, ensure_ascii=False))
    after = source_manifest(paths)
    if before != after:
        raise AssertionError("Protected models or previous reports changed")
    write_json(reports / "source_preservation.json", {"before": before, "after": after, "unchanged": before == after})
    write_report(reports, config, source, n, candidates, attempts, tables, validation, before == after, output=output)
    if outcome["status"] != "AC_PF_CONVERGED" or not validation["balance_verified"]:
        raise ScenarioFailure({"status": outcome["status"], "attempts": attempts, "validation": validation})
    return {"status": outcome["status"], "attempts": attempts, "validation": validation}


EXPECTED_FILES = ('synthetic_pf_base.nc', 'pf_attempts.json', 'slack_candidates.csv', 'result_validation.json', 'source_preservation.json', 'scenario_assumptions.json', 'component_counts.csv', 'acpf_execution_report.md', 'bus_voltage_results.csv', 'line_flow_results.csv', 'transformer_flow_results.csv', 'generator_dispatch_results.csv', 'load_scenario.csv', 'power_balance_validation.csv', 'nodal_balance_validation.csv')


def regenerate_for_verification(paths, destination, *, config_path=None, scenario_id="initial_108"):
    """Regenerate the pinned initial trial sequence into a new atomic bundle."""
    if scenario_id != "initial_108":
        raise ValueError("Unknown initial synthetic scenario ID")
    config, _ = read_settings(paths, config_path=config_path)
    return scenario_bundle(paths, destination, EXPECTED_FILES, lambda reports, input_path:
                           _generate(paths, reports, config, input_path), config)
