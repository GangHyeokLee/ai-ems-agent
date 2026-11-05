"""Stage 2D original calculation regressions with explicit source fixtures."""
import math
import json
from pathlib import Path
import pandas as pd
import pytest
pypsa = pytest.importorskip("pypsa", reason="Stage 2D requires the separate build environment")
from pipelines.mn.passive_inputs import logical_input
from pipelines.mn.audit_mn35k_plants import (_identity_classifications, build_audit, normalize_name, parse_project_ids)
from pipelines.mn.run_mn35k_data_informed_pf import _candidate_rows, _power_factor, read_settings
from pipelines.mn.audit_mn35k_220kv_lines import (charging_mvar, circuit_verdict, geometry_overlap_metrics, geometry_topology)
from pipelines.mn.diagnose_mn35k_reactive import (_line_charging_sensitivity, charging_values, voltage_class_summary, voltage_classification)


def test_name_normalization_and_project_id_namespaces():
    assert normalize_name('Khushig Khündii Solar') == 'khushig khundii solar'
    ids = parse_project_ids("{'GEM': {'G1', 'G2'}, 'GPD': {'P1'}}")
    assert ids == {'GEM': ['G1', 'G2'], 'GPD': ['P1']}


def test_load_power_factor_is_computed_from_p_and_q():
    assert _power_factor(25.6, 25.6 * 0.3286841051789) == pytest.approx(0.95)


def test_line_charging_units_and_voltage_square_scaling():
    b_siemens, q_1pu = charging_values(length_km=250.0, c_nf_per_km=12.5, frequency_hz=50.0, num_parallel=1.0, voltage_kv=220.0)
    assert math.isclose(b_siemens, 2 * math.pi * 50 * 1.25e-08 * 250)
    assert math.isclose(q_1pu, b_siemens * 220 ** 2)
    assert math.isclose(charging_values(250, 12.5, 50, 1, 220, 1.05)[1] / q_1pu, 1.05 ** 2)


def test_voltage_class_summary_separates_unresolved_length_effect():
    lines = pd.DataFrame([{'line_id': 'a', 'voltage_kv': 110.0, 'length_km': 100.0, 'b_siemens': 0.001, 'charging_q_1pu_mvar': 12.1, 'length_status': 'CORRECTED'}, {'line_id': 'b', 'voltage_kv': 110.0, 'length_km': 50.0, 'b_siemens': 0.0005, 'charging_q_1pu_mvar': 6.05, 'length_status': 'UNRESOLVED'}, {'line_id': 'c', 'voltage_kv': 220.0, 'length_km': 25.0, 'b_siemens': 0.0001, 'charging_q_1pu_mvar': 4.84, 'length_status': 'UNCHANGED'}])
    result = voltage_class_summary(lines).set_index('voltage_kv')
    assert result.at[110.0, 'line_count'] == 2
    assert result.at[110.0, 'unresolved_line_count'] == 1
    assert math.isclose(result.at[110.0, 'unresolved_charging_q_1pu_mvar'], 6.05)
    assert math.isclose(result.charging_share_percent.sum(), 100.0)


def test_voltage_classification_uses_configured_screening_bounds():
    assert voltage_classification(0.9, 0.9, 1.1) == 'WITHIN_LIMITS'
    assert voltage_classification(0.899, 0.9, 1.1) == 'UNDER_VOLTAGE'
    assert voltage_classification(1.101, 0.9, 1.1) == 'OVER_VOLTAGE'
    assert voltage_classification(float('nan'), 0.9, 1.1) == 'NUMERICAL_DIAGNOSTIC_ONLY'


def test_line_b_sensitivity_persists_after_dependent_values_recalculation():
    network = pypsa.Network()
    network.add('Bus', 'a', v_nom=110.0)
    network.add('Bus', 'b', v_nom=110.0)
    network.add('LineType', 'test_type', r_per_length=0.1, x_per_length=0.3, c_per_length=10.0, i_nom=1.0, f_nom=50.0, v_nom=110.0)
    network.add('Line', 'line', bus0='a', bus1='b', type='test_type', length=100.0, num_parallel=1, s_nom=100.0)
    network.calculate_dependent_values()
    original_x = float(network.lines.at['line', 'x'])
    original_b = float(network.lines.at['line', 'b'])
    _line_charging_sensitivity(network, 110.0, 0.0)
    assert float(network.lines.at['line', 'b']) == 0.0
    assert float(network.lines.at['line', 'x']) == original_x
    assert float(network.lines.at['line', 'b_pu']) == 0.0
    assert original_b > 0
    assert network.lines.at['line', 'type'] == ''


def test_220kv_charging_uses_siemens_nf_km_and_circuit_count():
    b_siemens, q_mvar = charging_mvar(length_km=250.982318926, c_nf_per_km=12.5, frequency_hz=50, circuits=2, voltage_kv=220)
    assert math.isclose(b_siemens, 2 * math.pi * 50 * 1.25e-08 * 250.982318926 * 2, rel_tol=1e-12)
    assert math.isclose(q_mvar, b_siemens * 220 ** 2, rel_tol=1e-12)
    assert math.isclose(q_mvar, 95.4065893277, abs_tol=1e-08)


@pytest.mark.parametrize(('circuits', 'cables', 'voltage', 'model', 'expected'), [('2', '6', '220000', '2', ('SOURCE_SUPPORTED', 'EXPLICIT_OSM_CIRCUITS')), ('', '6', '220000', '2', ('STANDARD_ASSUMPTION', 'INFERRED_CABLES_DIVIDED_BY_THREE')), ('', '', '220000', '1', ('UNRESOLVED', 'UNVERIFIED_CIRCUIT_COUNT')), ('', '6', '220000;110000', '3', ('UNRESOLVED', 'UNRESOLVED_MULTI_VOLTAGE_CIRCUITS')), ('1', '6', '220000', '2', ('UNRESOLVED', 'OSM_CIRCUITS_MODEL_MISMATCH'))])
def test_circuit_evidence_is_not_conflated_with_bundle(circuits, cables, voltage, model, expected):
    assert circuit_verdict(circuits, cables, voltage, float(model)) == expected


def test_geometry_overlap_metrics_identifies_repeated_linework():
    from shapely.geometry import LineString, MultiLineString
    line = LineString([(105.0, 48.0), (105.01, 48.0)])
    repeated = MultiLineString([list(line.coords), list(line.coords)])
    result = geometry_overlap_metrics(repeated, {'other-line': line})
    assert result['build_internal_duplicate_overlap_km'] > 0.5
    assert result['build_cross_line_overlap_km'] > 0.5
    assert result['build_cross_line_overlap_ids'] == 'other-line'


def test_geometry_topology_detects_a_cycle():
    from shapely.geometry import MultiLineString
    geometry = MultiLineString([[(105.0, 48.0), (105.01, 48.0)], [(105.01, 48.0), (105.0, 48.0)]])
    result = geometry_topology(geometry)
    assert result['geometry_endpoint_cycle_rank'] == 1
    assert result['geometry_endpoint_branch_nodes'] == 0
