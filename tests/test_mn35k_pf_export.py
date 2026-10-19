import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_mn35k_pf_preview.py"
spec = importlib.util.spec_from_file_location("export_mn35k_pf_preview", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_network(bus_ids, line_ids, transformer_ids, snapshots=("2013-01-01 00:00:00",)):
    buses = pd.DataFrame(
        [
            {
                "x": 105.0 + int(bus_id) / 1000,
                "y": 47.0 + int(bus_id) / 1000,
                "v_nom": 220.0 if int(bus_id) % 3 == 0 else 110.0,
                "v_mag_pu_min": 0.9,
                "v_mag_pu_max": 1.1,
            }
            for bus_id in bus_ids
        ],
        index=bus_ids,
    )
    lines = pd.DataFrame(
        [
            {
                "bus0": str(index),
                "bus1": str(index + 1),
                "s_nom": 100.0,
            }
            for index in range(len(line_ids))
        ],
        index=line_ids,
    )
    transformers = pd.DataFrame(
        [
            {
                "bus0": str(index),
                "bus1": str(index + 1),
                "s_nom": 100.0,
            }
            for index in range(len(transformer_ids))
        ],
        index=transformer_ids,
    )
    return SimpleNamespace(
        buses=buses,
        lines=lines,
        transformers=transformers,
        snapshots=list(snapshots),
    )


def fixture_inputs():
    full_bus_ids = [str(i) for i in range(173)]
    full_line_ids = [f"line-{i:03}" for i in range(159)]
    transformer_ids = [f"trafo-{i:02}" for i in range(27)]
    map_model = make_network(full_bus_ids, full_line_ids, transformer_ids)
    poc_model = make_network(full_bus_ids, full_line_ids, transformer_ids)
    pf_model = make_network(
        [str(i) for i in range(166)],
        full_line_ids[:156],
        transformer_ids,
    )
    map_data = {
        "buses": [
            {
                "bus_id": bus_id,
                "latitude": row.y,
                "longitude": row.x,
                "voltage_kv": row.v_nom,
                "name": bus_id,
            }
            for bus_id, row in map_model.buses.iterrows()
        ],
        "lines": [
            {
                "line_id": line_id,
                "bus1_id": row.bus0,
                "bus2_id": row.bus1,
                "voltage_kv": 220.0,
            }
            for line_id, row in map_model.lines.iterrows()
        ],
        "transformers": [
            {
                "transformer_id": identifier,
                "bus1_id": row.bus0,
                "bus2_id": row.bus1,
            }
            for identifier, row in map_model.transformers.iterrows()
        ],
    }

    buses = []
    for index, bus_id in enumerate(pf_model.buses.index):
        voltage = pf_model.buses.at[bus_id, "v_nom"]
        buses.append({
            "bus": int(bus_id),
            "voltage_kv": voltage,
            "control": "Slack" if index == 0 else "PV" if index <= 43 else "PQ",
            "v_mag_pu": 0.99920143362 if index == 0 else 1.09403127279 if index == 1 else 1.02,
            "v_ang_deg": -0.1,
            "voltage_violation": False,
            "scenario_id": module.SCENARIO_ID,
        })
    bus_results = pd.DataFrame(buses)

    branch_rows = []
    for component, frame in (("line", pf_model.lines), ("transformer", pf_model.transformers)):
        for index, (identifier, row) in enumerate(frame.iterrows()):
            branch_rows.append({
                "equipment_id": identifier,
                "bus0": row.bus0,
                "bus1": row.bus1,
                "s_nom": 100.0,
                "bus0_voltage_kv": pf_model.buses.at[row.bus0, "v_nom"],
                "bus1_voltage_kv": pf_model.buses.at[row.bus1, "v_nom"],
                "p0_mw": 0.1,
                "q0_mvar": -0.2,
                "p1_mw": -0.1,
                "q1_mvar": -0.3,
                "loading_percent": (
                    10.3671235628 if component == "line" and index == 0
                    else 12.0941783156 if component == "transformer" and index == 0
                    else 1.0
                ),
                "thermal_violation": False,
                "component": component,
                "scenario_id": module.SCENARIO_ID,
            })
    branches = pd.DataFrame(branch_rows)

    comparison = pd.DataFrame([{
        "scenario_id": module.SCENARIO_ID,
        "load_mw": 25.6,
        "load_mvar": 8.41431309258,
        "generator_count": 45,
        "pv_bus_count": 43,
        "pq_bus_count": 122,
        "slack_bus_count": 1,
        "shunt_count": 1,
        "shunt_nominal_mvar": 20.0,
        "pf_status": "AC_PF_CONVERGED",
        "converged": True,
        "operating_screen_status": "SYNTHETIC_SCENARIO_WITHIN_SCREENING_LIMITS",
        "q_violation_count": 0,
        "v_min_pu": 0.99920143362,
        "v_max_pu": 1.09403127279,
        "line_max_loading_percent": 10.3671235628,
        "transformer_max_loading_percent": 12.0941783156,
        "p_balance_residual_mw": -1.34955477682e-7,
        "q_balance_residual_mvar": -3.6850878038e-7,
        "shunt_actual_q_mvar": -20.0,
        "nodal_max_residual_mva": 2.58703832552e-7,
    }])
    generator_results = pd.DataFrame([
        {
            "generator": f"SYNTHETIC_{index}",
            "p_nom": 100.0,
            "p_set": 0.1,
            "p_mw": 0.1,
            "q_mvar": -1.0,
            "s_mva": 1.1,
            "p_min_mw": 0.0,
            "q_min_mvar": -100.0,
            "q_max_mvar": 100.0,
            "s_screening_mva": 150.0,
            "p_limit_violation": False,
            "q_limit_violation": False,
            "s_limit_violation": False,
            "scenario_id": module.SCENARIO_ID,
        }
        for index in range(45)
    ])
    shunt_results = pd.DataFrame([{
        "shunt": "SYNTHETIC_SHUNT_REACTOR_25",
        "nominal_q_absorption_mvar": 20.0,
        "q_absorbed_mvar": -20.0,
        "q_mvar": -20.0,
        "scenario_id": module.SCENARIO_ID,
    }])
    power_balance = pd.DataFrame([
        {"quantity": "P", "residual": -1.34955477682e-7, "passed": True, "scenario_id": module.SCENARIO_ID},
        {"quantity": "Q", "residual": -3.6850878038e-7, "passed": True, "scenario_id": module.SCENARIO_ID},
    ])
    nodal_balance = pd.DataFrame([
        {
            "bus": int(bus_id),
            "p_residual_mw": 2.58703832552e-7 if bus_id == "0" else 0.0,
            "q_residual_mvar": 0.0,
            "scenario_id": module.SCENARIO_ID,
        }
        for bus_id in pf_model.buses.index
    ])
    return {
        "map_data": map_data,
        "map_json_sha256": "a" * 64,
        "map_model": map_model,
        "poc_model": poc_model,
        "pf_model": pf_model,
        "scenario_comparison": comparison,
        "bus_voltage_results": bus_results,
        "branch_loading_results": branches,
        "generator_reactive_results": generator_results,
        "shunt_reactor_results": shunt_results,
        "power_balance_validation": power_balance,
        "nodal_balance_validation": nodal_balance,
        "result_validation": {
            "finite_results": True,
            "controls_verified": True,
            "loads_verified": True,
            "balance_verified": True,
            "power_balance_verified": True,
            "nodal_balance_verified": True,
            "operating_constraints_satisfied": True,
            "screening_status": "SYNTHETIC_SCENARIO_WITHIN_SCREENING_LIMITS",
            "violations": {
                "bus_voltage": 0,
                "line_thermal": 0,
                "transformer_thermal": 0,
                "generator_p": 0,
                "generator_q": 0,
                "generator_s": 0,
            },
            "model_reliability_status": "MODEL_DATA_REQUIRES_REVIEW",
        },
        "source_preservation": {"unchanged": True},
        "acpf_config": {"screening": {
            "v_min_pu": 0.9,
            "v_max_pu": 1.1,
            "balance_tolerance_mva": 0.0001,
        }},
        "synthetic_limits": {
            "generator_p_min_mw": 0.0,
            "generator_p_max_mw": 100.0,
            "generator_q_min_mvar": -100.0,
            "generator_q_max_mvar": 100.0,
            "generator_s_screening_mva": 150.0,
        },
        "source_paths": {
            "pf_model": "networks/MN_35k_poc/scenarios/synthetic_reactive_support_base.nc",
            "map_model": "networks/MN_35k/base.nc",
        },
        "source_hashes": {
            "pf_model": "b" * 64,
            "map_model": "c" * 64,
            "poc_model": "d" * 64,
        },
    }


def run_build(**overrides):
    values = fixture_inputs()
    values.update(overrides)
    return module.build_preview(**values)


def test_exports_only_the_approved_scenario_and_marks_excluded_topology():
    result = run_build()

    assert result["metadata"]["scenario_id"] == module.SCENARIO_ID
    assert result["metadata"]["analysis_bus_count"] == 166
    assert result["metadata"]["map_bus_count"] == 173
    assert result["metadata"]["analysis_line_count"] == 156
    assert result["metadata"]["excluded_bus_ids"] == [str(i) for i in range(166, 173)]
    assert result["metadata"]["excluded_line_ids"] == [f"line-{i:03}" for i in range(156, 159)]
    assert len(result["transformers"]) == 27
    assert all(row["analysis_status"] == "ANALYZED" for row in result["lines"])
    assert result["summary"]["screening_violations"]["generator_q"] == 0
    assert result["summary"]["model_reliability_status"] == "MODEL_DATA_REQUIRES_REVIEW"


def test_scenario_rows_are_filtered_and_serialization_is_deterministic():
    first = fixture_inputs()
    other_scenario = first["bus_voltage_results"].iloc[[0]].copy()
    other_scenario.loc[:, "scenario_id"] = "reference_108_generators"
    first["bus_voltage_results"] = pd.concat(
        [first["bus_voltage_results"], other_scenario],
        ignore_index=True,
    )
    one = module.build_preview(**first)
    two = module.build_preview(**first)

    assert len(one["buses"]) == 166
    assert json.dumps(one, sort_keys=True, allow_nan=False) == json.dumps(
        two, sort_keys=True, allow_nan=False
    )


@pytest.mark.parametrize(
    ("key", "mutate", "message"),
    [
        ("bus_voltage_results", lambda frame: frame.__setitem__("v_mag_pu", float("nan")), "유한"),
        ("branch_loading_results", lambda frame: frame.__setitem__("loading_percent", float("inf")), "유한"),
        ("scenario_comparison", lambda frame: frame.__setitem__("converged", False), "수렴하지"),
        ("result_validation", lambda value: value.__setitem__("violations", {"bus_voltage": 1}), "위반"),
        ("source_preservation", lambda value: value.__setitem__("unchanged", False), "preservation"),
    ],
)
def test_rejects_invalid_or_unverified_source_results(key, mutate, message):
    values = fixture_inputs()
    mutate(values[key])

    with pytest.raises(ValueError, match=message):
        module.build_preview(**values)


def test_rejects_map_endpoint_mismatch_instead_of_guessing_identity():
    values = fixture_inputs()
    values["map_data"]["lines"][0]["bus2_id"] = "10"

    with pytest.raises(ValueError, match="양단 모선"):
        module.build_preview(**values)


def test_rejects_nonfinite_output_and_wrong_scenario_control_counts():
    values = fixture_inputs()
    values["scenario_comparison"].loc[0, "pv_bus_count"] = 42

    with pytest.raises(ValueError, match="제어 구성"):
        module.build_preview(**values)

    values = fixture_inputs()
    values["bus_voltage_results"].loc[0, "v_mag_pu"] = 1.2
    with pytest.raises(ValueError, match="판정"):
        module.build_preview(**values)

    values = fixture_inputs()
    values["synthetic_limits"]["generator_q_min_mvar"] = -90
    with pytest.raises(ValueError, match="설정 한도"):
        module.build_preview(**values)


def test_pf_netlist_endpoint_mismatch_is_rejected():
    values = fixture_inputs()
    values["pf_model"].lines.loc["line-000", "bus1"] = "10"

    with pytest.raises(ValueError, match="결과 연결"):
        module.build_preview(**values)
