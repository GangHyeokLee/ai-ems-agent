"""Original calculation regressions; actual-data fixtures and fixed shunt samples."""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
import pytest
pypsa = pytest.importorskip("pypsa", reason="Synthetic PF requires separate build environment")
from pipelines.mn.passive_inputs import logical_input
from pipelines.mn.synthetic_inputs import read_settings, source_manifest
from pipelines.mn.run_mn35k_acpf import (assert_passive_preserved, branch_results, build_scenario, execute_pf, select_component, validate_results, write_report)


@pytest.fixture(scope="module")
def config(source_paths):
    return read_settings(source_paths)[0]


@pytest.fixture(scope="module")
def source(config, source_paths):
    return pypsa.Network(logical_input(source_paths, config["input_model"]))


@pytest.fixture(scope="module")
def solved(source, config):
    n, _ = build_scenario(source, config, config["trials"][-1])
    result = execute_pf(n, config)
    assert result["status"] == "AC_PF_CONVERGED", result
    return n, result



def test_actual_ids_topology_and_source_unchanged(source, config, solved):
    n, _ = solved
    buses, excluded, candidates = select_component(source, config)
    assert len(buses) == 166
    assert [len(c) for c in excluded] == [3, 2, 1, 1]
    assert {"118", "124"}.isdisjoint(n.buses.index)
    assert set(n.buses.index) == set(buses)
    assert n.buses.v_nom.value_counts().to_dict() == {110: 90, 35: 58, 220: 18}
    assert n.lines.v_nom.value_counts().to_dict() == {110: 91, 35: 46, 220: 19}
    assert len(n.transformers) == 27
    assert candidates.iloc[0].bus == "25"
    assert candidates.iloc[0].degree == 4
    assert len(source.buses) == 173 and len(source.snapshots) == 8760
    assert source.generators.empty and source.loads.empty
    assert source.buses.control.eq("PQ").all()
    assert_passive_preserved(source, n)



def test_meaningful_synthetic_inputs_and_controls(config, solved):
    n, result = solved
    assert list(n.snapshots) == [pd.Timestamp("2013-01-01")]
    assert n.loads.p_set.sum() == pytest.approx(25.6)
    assert n.loads.q_set.sum() == pytest.approx(8.4143130925789)
    assert len(n.loads.bus.unique()) == 166
    assert n.loads.p_set.gt(0).all() and n.loads.q_set.gt(0).all()
    assert n.generators.index.str.startswith("SYNTHETIC_").all()
    assert n.buses.control.value_counts().to_dict() == {"PV": 107, "PQ": 58, "Slack": 1}
    assert n.buses.loc[n.buses.v_nom.eq(35), "control"].eq("PQ").all()
    assert n.buses_t.v_mag_pu.iloc[0].at["25"] == pytest.approx(1)
    assert n.buses_t.v_ang.iloc[0].at["25"] == pytest.approx(0)
    assert result["subnetworks"][0]["n_iter"] == 3
    assert result["subnetworks"][0]["error"] < config["pf"]["x_tol"]
    assert result["subnetworks"][0]["sub_network"] == "0"



def test_independent_power_balance_and_q_limit_violation(config, solved):
    n, _ = solved
    tables, result = validate_results(n, config)
    assert result["balance_verified"]
    assert result["controls_verified"] and result["loads_verified"]
    assert result["max_nodal_residual_mva"] < 1e-6
    assert tables["power_balance_validation"].residual.abs().max() < 1e-6
    assert tables["power_balance_validation"].at["Q", "branch_net"] < 0
    assert result["screening_status"] == "SYNTHETIC_SCENARIO_HAS_VIOLATIONS"
    assert result["model_reliability_status"] == "MODEL_DATA_REQUIRES_REVIEW"
    assert result["violations"] == {"bus_voltage": 0, "line_thermal": 0, "transformer_thermal": 0,
                                     "generator_p": 0, "generator_q": 1, "generator_s": 0}
    gen = tables["generator_dispatch_results"]
    assert list(gen.index[gen.q_limit_violation]) == ["SYNTHETIC_PV_10"]
    assert gen.at["SYNTHETIC_PV_10", "q_mvar"] < -111
    assert gen.at["SYNTHETIC_PV_10", "control"] == "PV"  # Q limit is screened, not silently enforced.



@pytest.mark.parametrize("trial_index", [0, 1])
def test_record_actual_nonconvergent_conditions(source, config, trial_index):
    n, _ = build_scenario(source, config, config["trials"][trial_index])
    result = execute_pf(n, config)
    assert result["status"] == "AC_PF_NOT_CONVERGED"
    assert result["exception"] is None
    assert result["subnetworks"][0]["n_iter"] == 100
    assert result["subnetworks"][0]["converged"] is False
    assert result["warnings"]
    assert n.loads.p_set.sum() == pytest.approx(25.6)



def test_exception_distinguished_from_nonconvergence(config, monkeypatch):
    n = pypsa.Network()
    def fail(**kwargs):
        raise RuntimeError("deliberate solver error")
    monkeypatch.setattr(n, "pf", fail)
    result = execute_pf(n, config)
    assert result["status"] == "AC_PF_EXECUTION_ERROR"
    assert result["exception"] == "RuntimeError: deliberate solver error"
    assert result["subnetworks"] == []



def test_nan_error_is_never_success(config, solved, monkeypatch):
    n = solved[0].copy()
    raw = {"converged": pd.DataFrame(True, index=n.snapshots, columns=["0"]),
           "n_iter": pd.DataFrame(1, index=n.snapshots, columns=["0"]),
           "error": pd.DataFrame(np.nan, index=n.snapshots, columns=["0"])}
    monkeypatch.setattr(n, "pf", lambda **kwargs: raw)
    result = execute_pf(n, config)
    assert result["status"] == "AC_PF_NOT_CONVERGED"
    assert result["subnetworks"][0]["error"] is None
    json.dumps(result, allow_nan=False)



def test_larger_receiving_end_loading_is_used():
    n = pypsa.Network()
    n.add("Bus", ["a", "b"], v_nom=110)
    n.add("Line", "branch", bus0="a", bus1="b", r=1, x=1, s_nom=8)
    for field, value in {"p0": 3, "q0": 4, "p1": -6, "q1": -8}.items():
        n.lines_t[field] = pd.DataFrame({"branch": [value]}, index=n.snapshots)
    result = branch_results(n, "lines").loc["branch"]
    assert result.s0_mva == 5 and result.s1_mva == 10
    assert result.loading_percent == 125
    assert result.thermal_violation



@pytest.mark.parametrize("corruption", ["generation", "angle_reference", "nonfinite", "load"])
def test_independent_checks_reject_corrupted_results(config, solved, corruption):
    n = solved[0].copy()
    if corruption == "generation":
        n.generators_t.q.iloc[0, 0] += 1
    elif corruption == "angle_reference":
        n.buses_t.v_ang += 0.1  # Global phase shift preserves balance but violates slack reference.
    elif corruption == "nonfinite":
        n.buses_t.v_mag_pu.iloc[0, 0] = np.nan
    else:
        n.loads_t.p.iloc[0, 0] += 1
    _, result = validate_results(n, config)
    assert result["balance_verified"] is False
    assert result["screening_status"] == "MODEL_DATA_REQUIRES_REVIEW"



@pytest.mark.parametrize("scale", [0, -1, float("nan")])
def test_invalid_or_zero_load_not_allowed(source, config, scale):
    trial = dict(config["trials"][0], load_scale=scale)
    with pytest.raises(ValueError, match="Loads require"):
        build_scenario(source, config, trial)



def test_no_success_claim_or_result_hash_in_failure_report(tmp_path, source, config):
    n, candidates = build_scenario(source, config, config["trials"][0])
    attempts = [{"status": "AC_PF_EXECUTION_ERROR"}]
    write_report(tmp_path, config, source, n, candidates, attempts, None,
                 {"screening_status": "MODEL_DATA_REQUIRES_REVIEW"}, True, output=tmp_path / "absent.nc")
    record = json.loads((tmp_path / "scenario_assumptions.json").read_text())
    assert record["result_model_sha256"] is None
    assert record["numerical_status"] == "AC_PF_EXECUTION_ERROR"
    assert "AC_PF_CONVERGED" not in (tmp_path / "acpf_execution_report.md").read_text()

