"""PyPSA -> PyPowSyBl conversion rules checked on 2-3 bus models with both real AC PF engines.

Run in the dedicated environment (PyPSA from Pixi + PyPowSyBl), see
reports/pypowsybl_conversion/pypowsybl_conversion_report.md.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

pypowsybl = pytest.importorskip("pypowsybl")
pypsa = pytest.importorskip("pypsa")

from pipelines.mn import mn35k_powsybl as conv

logging.getLogger("pypsa").setLevel(logging.WARNING)
TOL = 1e-7          # both engines solved to 1e-10 MVA mismatch on these tiny models
SNAP = pd.Timestamp("2013-01-01")


def network(*buses):
    n = pypsa.Network()
    n.set_snapshots([SNAP])
    for name, v_nom, v_set in buses:
        n.add("Bus", name, v_nom=v_nom, v_mag_pu_set=v_set)
    return n


def solve_both(n, slack_bus, q_limits=(-100.0, 100.0)):
    raw = n.pf(x_tol=1e-10, use_seed=False, distribute_slack=False)
    assert bool(raw["converged"].values.all())
    model = conv.extract_model(n, q_limits=q_limits)
    net = conv.build_network(model)
    lf = conv.run_ac(net, "VL_" + slack_bus, conv_eps_per_eq=1e-12)
    assert lf["converged"]
    slack_gen = model.generators.loc[model.generators.control == "Slack", "generator_id"].iloc[0]
    return model, net, lf, conv.pypsa_results(n), conv.powsybl_results(
        net, model.mapping, slack_gen, lf["slack_active_power_mismatch_mw"]
    )


def assert_same(ref, cand, component, key, fields, tol=TOL):
    frame = conv.compare(ref[component], cand[component], key, fields)
    assert frame.comparable.all(), frame
    for f in fields:
        worst = frame[f + "_diff"].abs().max()
        assert worst <= tol, (component, f, worst, frame)
    return frame


def two_bus_line(b_s=1.912e-3, load=(0.0, 0.0)):
    n = network(("A", 220.0, 1.0), ("B", 220.0, 1.0))
    n.add("Line", "AB", bus0="A", bus1="B", r=7.302, x=36.633, b=b_s, g=0.0, s_nom=983.0)
    n.add("Generator", "GA", bus="A", control="Slack", p_set=0.0, p_nom=100)
    n.add("Load", "LB", bus="B", p_set=load[0], q_set=load[1])
    return n


def three_bus():
    n = network(("A", 220.0, 1.0), ("B", 220.0, 1.02), ("C", 110.0, 1.0))
    n.add("Line", "AB", bus0="A", bus1="B", r=7.3, x=36.6, b=1.9e-3, s_nom=983.0)
    n.add("Line", "BA2", bus0="B", bus1="A", r=10.0, x=40.0, b=1.0e-3, s_nom=500.0)
    n.add("Transformer", "TBC", bus0="B", bus1="C", r=0.0026, x=0.12, s_nom=100.0, model="t")
    n.add("Generator", "GA", bus="A", control="Slack", p_set=1.0, p_nom=100)
    n.add("Generator", "GB", bus="B", control="PV", p_set=5.0, p_nom=100)
    n.add("Generator", "GC", bus="C", control="PQ", p_set=2.0, q_set=-3.0, p_nom=100)
    n.add("Load", "LC", bus="C", p_set=8.0, q_set=2.6)
    n.add("ShuntImpedance", "RB", bus="B", g=0.0, b=-20.0 / 220**2, sign=-1.0)
    return n


def test_line_series_impedance_and_charging_are_split_half_per_end():
    n = two_bus_line()
    model, net, _, ref, cand = solve_both(n, "A")
    line = net.get_lines().loc["L_AB"]
    assert line.r == pytest.approx(7.302) and line.x == pytest.approx(36.633)
    assert line.b1 == pytest.approx(1.912e-3 / 2) and line.b2 == pytest.approx(1.912e-3 / 2)
    assert line.g1 == 0 and line.g2 == 0
    assert_same(ref, cand, "lines", "line_id", ["p0", "q0", "p1", "q1"])
    assert_same(ref, cand, "buses", "bus_id", ["v_mag_pu", "v_ang_deg"])
    # unloaded line: net reactive into the line is negative (charging is generated)
    flows = cand["lines"].iloc[0]
    assert flows.q0 + flows.q1 < -0.9 * 1.912e-3 * 220**2


def test_branch_flow_sign_is_power_entering_branch_at_each_terminal():
    n = two_bus_line(load=(10.0, 3.0))
    n.add("Line", "BA", bus0="B", bus1="A", r=7.302, x=36.633, b=0.0, s_nom=983.0)
    _, _, _, ref, cand = solve_both(n, "A")
    frame = assert_same(ref, cand, "lines", "line_id", ["p0", "q0", "p1", "q1"])
    by_id = frame.set_index("line_id")
    assert by_id.at["AB", "p0_powsybl"] > 0 > by_id.at["AB", "p1_powsybl"]   # A -> B
    assert by_id.at["BA", "p0_powsybl"] < 0 < by_id.at["BA", "p1_powsybl"]   # reversed orientation


def test_load_pq_is_consumption():
    n = two_bus_line(b_s=0.0, load=(10.0, 3.0))
    _, net, _, ref, cand = solve_both(n, "A")
    load = net.get_loads().loc["D_LB"]
    assert (load.p0, load.q0) == (10.0, 3.0)
    assert load.p == pytest.approx(10.0) and load.q == pytest.approx(3.0)
    gen = assert_same(ref, cand, "generators", "generator_id", ["p_mw", "q_mvar"])
    assert gen.p_mw_powsybl.iloc[0] > 10.0                      # slack supplies load + losses


def test_slack_and_pv_voltage_control_and_generator_q_sign():
    model, net, lf, ref, cand = solve_both(three_bus(), "A")
    buses = assert_same(ref, cand, "buses", "bus_id", ["v_mag_pu", "v_ang_deg"]).set_index("bus_id")
    assert buses.at["A", "v_mag_pu_powsybl"] == pytest.approx(1.0, abs=1e-9)
    assert buses.at["B", "v_mag_pu_powsybl"] == pytest.approx(1.02, abs=1e-9)
    assert buses.at["A", "v_ang_deg_powsybl"] == pytest.approx(0.0, abs=1e-12)
    gens = assert_same(ref, cand, "generators", "generator_id", ["p_mw", "q_mvar"]).set_index("generator_id")
    assert gens.at["GC", "q_mvar_powsybl"] == pytest.approx(-3.0)   # PQ: fixed injection
    assert net.get_generators().loc["G_GC", "q"] == pytest.approx(3.0)  # IIDM terminal = load sign
    assert lf["components"][0]["reference_bus_id"] == "VL_A_0"
    assert abs(lf["slack_active_power_mismatch_mw"]) > 0.1          # residual reported, not written back


def test_inductive_reactor_absorbs_b_times_v_squared_in_both_engines():
    for v_set in (1.0, 1.05):
        n = network(("A", 220.0, v_set), ("B", 220.0, v_set))
        n.add("Line", "AB", bus0="A", bus1="B", r=1.0, x=10.0, b=0.0, s_nom=983.0)
        n.add("Generator", "GA", bus="A", control="Slack", p_set=0.0, p_nom=100)
        n.add("Generator", "GB", bus="B", control="PV", p_set=0.0, p_nom=100)
        n.add("ShuntImpedance", "R", bus="B", g=0.0, b=-20.0 / 220**2, sign=-1.0)
        _, net, _, ref, cand = solve_both(n, "A")
        assert_same(ref, cand, "shunts", "shunt_id", ["q_absorbed_mvar"])
        assert cand["shunts"].q_absorbed_mvar.iloc[0] == pytest.approx(20.0 * v_set**2, rel=1e-9)
        assert n.shunt_impedances_t.q.iloc[0, 0] == pytest.approx(-20.0 * v_set**2, rel=1e-9)
        sections = net.get_linear_shunt_compensator_sections().loc["SH_R"]
        assert sections.b_per_section == pytest.approx(-20.0 / 220**2)  # negative S = inductive
        assert net.get_shunt_compensators().loc["SH_R", "q"] == pytest.approx(20.0 * v_set**2)


def test_two_winding_transformer_between_voltage_levels():
    model, net, _, ref, cand = solve_both(three_bus(), "A")
    t = net.get_2_windings_transformers().loc["T_TBC"]
    assert (t.rated_u1, t.rated_u2, t.rated_s) == (220.0, 110.0, 100.0)
    assert t.r == pytest.approx(0.0026 * 110**2 / 100) and t.x == pytest.approx(0.12 * 110**2 / 100)
    assert_same(ref, cand, "transformers", "transformer_id", ["p0", "q0", "p1", "q1"])
    assert_same(ref, cand, "lines", "line_id", ["p0", "q0", "p1", "q1"])
    vls = net.get_voltage_levels()
    assert vls.at["VL_B", "substation_id"] == vls.at["VL_C", "substation_id"]
    assert vls.at["VL_A", "substation_id"] != vls.at["VL_B", "substation_id"]


def test_ids_are_preserved_in_mapping_and_iidm_names():
    model, net, *_ = solve_both(three_bus(), "A")
    mapping = model.mapping
    assert set(mapping.loc[mapping.component == "Line", "original_id"]) == {"AB", "BA2"}
    assert not mapping.iidm_id.duplicated().any()
    lines = net.get_lines()
    assert set(lines.name) == {"AB", "BA2"}
    assert lines.loc["L_BA2", "bus1_id"] == "VL_B_0"                 # orientation preserved
    assert net.get_loading_limits().reset_index().query("element_id == 'L_AB'").value.tolist() == [983.0, 983.0]


@pytest.mark.parametrize("attr, value", [("tap_ratio", 1.05), ("phase_shift", 5.0), ("b", 0.01)])
def test_unsupported_transformer_parameters_are_rejected(attr, value):
    n = three_bus()
    n.transformers.at["TBC", attr] = value
    with pytest.raises(conv.UnsupportedConversion):
        conv.extract_model(n)


def test_unknown_slack_id_is_not_silently_replaced():
    n = three_bus()
    model = conv.extract_model(n)
    net = conv.build_network(model)
    with pytest.raises(RuntimeError, match="did not use the requested slack"):
        conv.run_ac(net, "B_A")


def test_nonfinite_parameter_is_rejected():
    n = two_bus_line()
    n.lines.at["AB", "type"] = ""
    n.lines.at["AB", "b"] = np.nan
    with pytest.raises(ValueError):
        conv.extract_model(n)


def test_xiidm_roundtrip_keeps_parameters_and_results(tmp_path):
    model, net, lf, _, before = solve_both(three_bus(), "A")
    target = tmp_path / "mini.xiidm"
    net.save(str(target), format="XIIDM")
    loaded = pypowsybl.network.load(str(target))
    pd.testing.assert_frame_equal(net.get_lines()[["r", "x", "b1", "b2", "g1", "g2"]],
                                  loaded.get_lines()[["r", "x", "b1", "b2", "g1", "g2"]])
    lf2 = conv.run_ac(loaded, "VL_A", conv_eps_per_eq=1e-12)
    after = conv.powsybl_results(loaded, model.mapping, "GA", lf2["slack_active_power_mismatch_mw"])
    for component, key, fields in (("buses", "bus_id", ["v_mag_pu", "v_ang_deg"]),
                                   ("lines", "line_id", ["p0", "q0", "p1", "q1"])):
        assert_same(before, after, component, key, fields, tol=1e-9)
