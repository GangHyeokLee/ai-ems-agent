"""Original calculation regressions; actual-data fixtures and fixed shunt samples."""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
import pytest
pypsa = pytest.importorskip("pypsa", reason="Synthetic PF requires separate build environment")
from pipelines.mn.passive_inputs import logical_input
from pipelines.mn.synthetic_inputs import read_settings, source_manifest
from pipelines.mn.run_mn35k_acpf import assert_passive_preserved
from pipelines.mn.run_mn35k_reactive_support import (build_reactive_scenario, execute_with_q_limits, validate_reactive_results)


@pytest.fixture(scope="module")
def settings(source_paths):
    return read_settings(source_paths)[1]


@pytest.fixture(scope="module")
def base_config(source_paths):
    return read_settings(source_paths)[0]


@pytest.fixture(scope="module")
def source(base_config, source_paths):
    return pypsa.Network(logical_input(source_paths, base_config["input_model"]))


def _scenario(settings, scenario_id):
    return next(item for item in settings["scenarios"] if item["id"] == scenario_id)



def _unit_shunt(b_siemens, voltage_setpoint_pu):
    network = pypsa.Network()
    network.add("Bus", ["slack", "pv"], v_nom=110)
    network.add(
        "Line", "link", bus0="slack", bus1="pv", r=0.1, x=1.0, b=0.0, s_nom=100
    )
    network.add(
        "Generator", "slack", bus="slack", control="Slack", p_nom=100, p_set=10
    )
    network.add(
        "Generator", "pv", bus="pv", control="PV", p_nom=100, p_set=10
    )
    network.buses.at["pv", "v_mag_pu_set"] = voltage_setpoint_pu
    network.add("Load", "load", bus="pv", p_set=10, q_set=2)
    network.add(
        "ShuntImpedance",
        "shunt",
        bus="pv",
        g=0.0,
        b=b_siemens,
        sign=-1.0,
    )
    result = network.pf(x_tol=1e-8, use_seed=False, distribute_slack=False)
    assert bool(result["converged"].iloc[0, 0])
    return network



@pytest.mark.parametrize(
    ("b_sign", "expected_sign"),
    [(-1.0, -1.0), (1.0, 1.0)],
)
def test_shunt_siemens_sign_and_voltage_squared(b_sign, expected_sign):
    b_siemens = b_sign / 110**2
    at_nominal = _unit_shunt(b_siemens, 1.0)
    at_high_voltage = _unit_shunt(b_siemens, 1.1)

    assert at_nominal.shunt_impedances.at["shunt", "b_pu"] == pytest.approx(b_sign)
    assert at_nominal.shunt_impedances_t.q.iloc[0, 0] == pytest.approx(expected_sign)
    assert at_high_voltage.shunt_impedances_t.q.iloc[0, 0] == pytest.approx(
        expected_sign * 1.1**2
    )
    assert at_nominal.shunt_impedances_t.q.iloc[0, 0] * expected_sign > 0



def test_q_limited_reactor_scenario_and_nodal_balance(settings, base_config, source):
    reference = _scenario(settings, "reference_108_generators")
    original = source.copy()
    baseline, _ = build_reactive_scenario(source, base_config, settings, reference)
    baseline_result, _, _ = execute_with_q_limits(baseline, base_config, False)
    assert baseline_result["status"] == "AC_PF_CONVERGED"
    assert len(baseline.generators) == 108
    assert baseline.buses.control.value_counts().to_dict() == {
        "PV": 107,
        "PQ": 58,
        "Slack": 1,
    }
    base_tables, base_validation = validate_reactive_results(baseline, base_config)
    assert base_validation["violations"]["generator_q"] == 1
    assert base_tables["generator_dispatch_results"].q_limit_violation.sum() == 1

    limited = _scenario(settings, "reduced_27_q_limited_with_reactor")
    network, _ = build_reactive_scenario(source, base_config, settings, limited)
    outcome, attempts, switches = execute_with_q_limits(
        network, base_config, limited["enforce_pv_q_limits"]
    )
    assert outcome["status"] == "AC_PF_CONVERGED", outcome
    assert len(attempts) == 2
    assert [item["generator"] for item in switches if "generator" in item] == [
        "SYNTHETIC_PV_10"
    ]
    assert network.generators.at["SYNTHETIC_PV_10", "control"] == "PQ"
    assert network.generators.at["SYNTHETIC_PV_10", "q_set"] == -100
    assert network.generators_t.q.at[network.snapshots[0], "SYNTHETIC_PV_10"] == pytest.approx(
        -100
    )
    assert len(network.generators) == 45
    assert len(network.shunt_impedances) == 1
    assert network.shunt_impedances.index.tolist() == [
        "SYNTHETIC_SHUNT_REACTOR_25"
    ]

    tables, validation = validate_reactive_results(network, base_config)
    assert validation["operating_constraints_satisfied"]
    assert validation["violations"] == {
        "bus_voltage": 0,
        "line_thermal": 0,
        "transformer_thermal": 0,
        "generator_p": 0,
        "generator_q": 0,
        "generator_s": 0,
    }
    assert validation["power_balance_verified"]
    assert validation["nodal_balance_verified"]
    assert tables["power_balance_validation"].residual.abs().max() < 1e-6
    assert validation["max_nodal_residual_mva"] < 1e-6
    assert tables["shunt_reactor_results"].at[
        "SYNTHETIC_SHUNT_REACTOR_25", "q_mvar"
    ] == pytest.approx(-20)
    assert 0.9 <= tables["bus_voltage_results"].v_mag_pu.min()
    assert tables["bus_voltage_results"].v_mag_pu.max() <= 1.1
    assert_passive_preserved(original, network)



def test_q_limit_recalculation_without_reactor_is_not_accepted(
    settings, base_config, source
):
    spec = _scenario(settings, "reduced_27_q_limited_no_reactor")
    network, _ = build_reactive_scenario(source, base_config, settings, spec)
    outcome, attempts, switches = execute_with_q_limits(
        network, base_config, spec["enforce_pv_q_limits"]
    )
    assert outcome["status"] == "AC_PF_CONVERGED"
    assert len(attempts) == 2
    assert [item["generator"] for item in switches if "generator" in item] == [
        "SYNTHETIC_PV_10"
    ]
    _, validation = validate_reactive_results(network, base_config)
    assert validation["violations"]["generator_q"] == 1
    assert not validation["operating_constraints_satisfied"]



def test_limited_220kv_pv_does_not_turn_nonconvergence_into_success(
    settings, base_config, source
):
    spec = _scenario(settings, "limited_220kv_pv")
    network, _ = build_reactive_scenario(source, base_config, settings, spec)
    outcome, attempts, _ = execute_with_q_limits(
        network, base_config, spec["enforce_pv_q_limits"]
    )
    assert outcome["status"] == "AC_PF_NOT_CONVERGED"
    assert attempts[-1]["result"]["subnetworks"][0]["n_iter"] == 100
    assert not network.shunt_impedances.shape[0]
