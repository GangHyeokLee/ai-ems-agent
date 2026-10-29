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


@pytest.fixture(scope='module')
def settings(source_paths):
    return read_settings(source_paths, source_paths.source_project / 'configs/mn35k_data_informed_scenario.json')


@pytest.fixture(scope='module')
def audit_data(settings, source_paths):
    _, base_config, _ = settings
    plants = pd.read_csv(logical_input(source_paths, 'pypsa-earth/resources/MN/powerplants.csv'))
    network = pypsa.Network(logical_input(source_paths, base_config['input_model']))
    return build_audit(plants, network, base_config, paths=source_paths)


def test_identity_review_preserves_duplicate_and_ambiguous_rows(source_paths):
    plants = pd.read_csv(logical_input(source_paths, 'pypsa-earth/resources/MN/powerplants.csv'))
    statuses, groups, _ = _identity_classifications(plants)
    assert statuses[1] == 'DISTINCT_CONFIRMED'
    assert statuses[4] == 'MULTI_UNIT_CANDIDATE'
    assert statuses[6:12] == ['DUPLICATE_CANDIDATE'] * 6
    assert statuses[12] == statuses[18] == 'DUPLICATE_CANDIDATE'
    assert statuses[21] == statuses[22] == 'IDENTITY_UNRESOLVED'
    assert groups[4] == groups[6] == 'ulaanbaatar_coal_oil_locality'
    assert len(plants) == 27


def test_bus_audit_ignores_legacy_numeric_bus_mapping_and_never_asserts_tie(audit_data):
    _, candidates = audit_data
    dornod = candidates.loc[candidates.record_id.eq(1) & candidates.candidate_rank_all_voltages.eq(1)].iloc[0]
    erdenet = candidates.loc[candidates.record_id.eq(5) & candidates.candidate_rank_all_voltages.eq(1)].iloc[0]
    assert dornod.candidate_bus == '46'
    assert dornod.distance_km == pytest.approx(0.030923, abs=1e-05)
    assert erdenet.candidate_bus == '16'
    assert dornod.in_166_bus_main_component
    assert not candidates.osm_electrical_connection_evidence.any()
    assert set(candidates.connection_status) <= {'SPATIAL_CANDIDATE', 'UNRESOLVED'}


def test_only_distinct_2013_spatial_candidates_selected_as_assumed_connections(settings, audit_data):
    identity, buses = audit_data
    data, candidates = _candidate_rows(settings[0], identity, buses)
    assert set(data.record_id) == {1, 5}
    assert {item['bus'] for item in candidates} == {'46', '16'}
    assert all((item['connection_status'] == 'ASSUMED_CONNECTION' for item in candidates))
    assert sum((item['capacity_mw'] for item in candidates)) == pytest.approx(71.0)
    assert all((item['capacity_classification'] == 'PUBLIC_DATA_ESTIMATE' for item in candidates))
    assert all((item['p_dispatch_classification'] == 'SYNTHETIC_ASSUMPTION' for item in candidates))


def test_duplicate_tes_capacity_is_not_aggregated(source_paths):
    plants = pd.read_csv(logical_input(source_paths, 'pypsa-earth/resources/MN/powerplants.csv'))
    statuses, _, _ = _identity_classifications(plants)
    tes_rows = plants.iloc[6:12]
    assert statuses[6:12] == ['DUPLICATE_CANDIDATE'] * 6
    assert tes_rows.Capacity.sum() > 0
    assert len(tes_rows.projectID.unique()) == 1


def test_base_model_is_read_only_source(source_paths):
    _, base_config, _ = read_settings(source_paths, source_paths.source_project / 'configs/mn35k_data_informed_scenario.json')
    source = pypsa.Network(logical_input(source_paths, base_config['input_model']))
    assert len(source.buses) == 173
    assert len(source.generators) == 0
    assert len(source.loads) == 0


def test_existing_220kv_audit_outputs_cover_nineteen_lines_and_reproduce_total(source_paths):
    inventory = pd.read_csv(source_paths.source_project / 'reports/220kv_line_audit/220kv_line_inventory.csv')
    charging = pd.read_csv(source_paths.source_project / 'reports/220kv_line_audit/220kv_charging_validation.csv')
    length = pd.read_csv(source_paths.source_project / 'reports/220kv_line_audit/220kv_length_evidence.csv')
    assert len(inventory) == len(charging) == len(length) == 19
    assert math.isclose(charging.q_1pu_mvar_independent.sum(), 713.98017837238, abs_tol=2e-06)
    assert charging.difference_vs_existing_mvar.abs().max() < 1e-07


def test_221951632_is_not_marked_as_a_safe_geodesic_replacement(source_paths):
    length = pd.read_csv(source_paths.source_project / 'reports/220kv_line_audit/220kv_length_evidence.csv').set_index('line_id')
    inventory = pd.read_csv(source_paths.source_project / 'reports/220kv_line_audit/220kv_line_inventory.csv').set_index('line_id')
    candidates = pd.read_csv(source_paths.source_project / 'reports/220kv_line_audit/220kv_correction_candidates.csv')
    row = length.loc['221951632-1_0']
    assert row.geometry_endpoint_branch_nodes == 1
    assert row.geometry_endpoint_cycle_rank == 1
    assert not bool(row.geometry_simple_chain)
    assert row.build_internal_duplicate_overlap_km < 1e-09
    assert row.build_cross_line_overlap_km < 1e-09
    assert inventory.at['221951632-1_0', 'model_length_verdict'] == 'UNRESOLVED'
    assert not bool(candidates.loc[0, 'apply_now'])
    assert 'Unnamed: 0' not in candidates.columns


def test_detected_cross_line_overlap_is_traced_outside_source_geometry(source_paths):
    length = pd.read_csv(source_paths.source_project / 'reports/220kv_line_audit/220kv_length_evidence.csv').set_index('line_id')
    details = json.loads(length.at['237132377-1_0', 'build_cross_line_overlap_details_json'])
    overlap = details['484276912-1_0']
    assert math.isclose(overlap['shared_km'], 0.13189714958, abs_tol=1e-08)
    assert overlap['in_source_km'] == 0
    assert math.isclose(overlap['outside_source_km'], overlap['shared_km'], abs_tol=1e-10)


def test_all_outputs_confirm_assumptions_are_not_field_verified(source_paths):
    circuit = pd.read_csv(source_paths.source_project / 'reports/220kv_line_audit/220kv_circuit_type_review.csv')
    charging = pd.read_csv(source_paths.source_project / 'reports/220kv_line_audit/220kv_charging_validation.csv')
    assert not circuit.field_verified.any()
    assert not charging.overall_field_verified.any()
    assert not charging.terminal_sum_is_no_load_charging.any()
