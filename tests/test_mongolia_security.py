"""Mongolia PyPowSyBl model loader and line N-1 Security Analysis (real OpenLoadFlow runs).

Regression baseline: mongolia-grid-poc reports/pypowsybl_conversion/n1_feasibility_results.csv.
The API must compute these results; the CSV is only used here as the expected value.
"""
from __future__ import annotations

import csv
import shutil
from concurrent.futures import ThreadPoolExecutor

import pypowsybl as pp
import pytest
from fastapi.testclient import TestClient

import web_app
from ai_ems import mongolia_security as ms
from ai_ems.config import MONGOLIA_POWSYBL_REPORTS_DIR, MONGOLIA_XIIDM_FILE, PROJECT_ROOT
from ai_ems.network import LOADFLOW_PARAMETERS

PREVIEW = PROJECT_ROOT / "data/grids/mn35k-synthetic-pf-preview.json"
MAP = PROJECT_ROOT / "data/grids/mn-osm-35k-map.json"
BASELINE = MONGOLIA_POWSYBL_REPORTS_DIR / "n1_feasibility_results.csv"
pytestmark = pytest.mark.skipif(
    not (MONGOLIA_XIIDM_FILE.is_file() and BASELINE.is_file() and PREVIEW.is_file() and MAP.is_file()),
    reason="Mongolia XIIDM / conversion reports / PF preview not available",
)
CONTINGENCIES = ["222087807-1_0", "751261406-1_0", "484333456-1_0"]
client = TestClient(web_app.app)


def files(xiidm=MONGOLIA_XIIDM_FILE, reports=MONGOLIA_POWSYBL_REPORTS_DIR):
    return ms.ModelFiles(xiidm, reports, PREVIEW, MAP)


@pytest.fixture(scope="module")
def service():
    svc = ms.MongoliaSecurityService(files())
    assert svc.status()["available"], svc.status()
    return svc


@pytest.fixture(scope="module")
def baseline():
    with BASELINE.open(encoding="utf-8") as handle:
        return {row["outage_line_id"]: row for row in csv.DictReader(handle)}


def test_model_loads_and_reproduces_validated_pf(service):
    check = service.status()["base_check"]
    assert check["provenance"]["conversion_scenario_id"] == "reduced_27_q_limited_with_reactor"
    assert check["provenance"]["source_sha256_matches_conversion_input"] is True
    assert check["structure"]["inventory"] == ms.EXPECTED_COUNTS
    assert check["loadflow"]["status"] == "CONVERGED"
    assert check["loadflow"]["reference_bus_id"] == "VL_25_0"
    assert check["loadflow"]["slack_bus_ids"] == ["VL_25_0"]
    comparison = check["comparison"]
    assert comparison["within_tolerance"]
    for key, value in comparison["max_abs_error"].items():
        assert value <= comparison["tolerances"][key]
    assert comparison["slack_p_mw"] == pytest.approx(1.352550547678, abs=1e-6)   # target_p + mismatch
    assert comparison["slack_q_mvar"] == pytest.approx(-93.419196819919, abs=1e-6)
    assert comparison["reactor_q_absorbed_mvar"] == pytest.approx(20.0, abs=1e-6)


def test_mongolia_parameters_are_explicit_and_kpg_parameters_untouched(service):
    before = str(LOADFLOW_PARAMETERS)
    params = ms.loadflow_parameters()
    assert params.distributed_slack is False and params.use_reactive_limits is False
    assert params.voltage_init_mode == pp.loadflow.VoltageInitMode.UNIFORM_VALUES
    assert params.provider_parameters["slackBusSelectionMode"] == "NAME"
    assert params.provider_parameters["slackBusesIds"] == "VL_25"
    assert params.provider_parameters["referenceBusSelectionMode"] == "FIRST_SLACK"
    service.run_line_contingency(CONTINGENCIES[0])
    assert str(LOADFLOW_PARAMETERS) == before
    assert LOADFLOW_PARAMETERS.provider_parameters.get("slackBusSelectionMode") == "MOST_MESHED"
    assert LOADFLOW_PARAMETERS.provider_parameters.get("slackBusesIds") == ""


@pytest.mark.parametrize("line_id", CONTINGENCIES)
def test_reference_contingencies_match_validated_n1_results(service, baseline, line_id):
    result = service.run_line_contingency(line_id)
    expected = baseline[line_id]
    assert result["outage_line_id"] == line_id
    assert result["contingency_id"] == "OUT_" + line_id
    assert result["post_status"] == expected["security_analysis_status"] == "CONVERGED"
    assert result["main_component_converged"] is True
    assert result["isolated_buses"] == expected["isolated_buses"].split()
    assert result["isolated_bus_count"] == int(expected["isolated_bus_count"])
    assert result["island_detected"] is (int(expected["isolated_bus_count"]) > 0)
    assert result["island_pf_computed"] is False
    assert result["island_check"]["consistent"] is True
    assert result["isolated_load_mw"] == pytest.approx(float(expected["isolated_load_mw"]), abs=1e-9)
    assert result["isolated_generator_ids"] == sorted(expected["isolated_generators"].split())
    assert result["violated_equipment_count"] == int(expected["sa_limit_violation_count"]) == 0
    assert result["violations"] == []
    for key, csv_key, tol in (
        ("post_voltage_min_pu", "post_v_min_pu", 1e-8), ("post_voltage_max_pu", "post_v_max_pu", 1e-8),
        ("max_voltage_change_pu", "max_abs_dv_pu", 1e-8), ("max_line_p_change_mw", "max_abs_dp_line_mw", 1e-5),
        ("max_line_q_change_mvar", "max_abs_dq_line_mvar", 1e-5),
        ("post_max_line_loading_percent", "post_line_max_loading_percent", 1e-5),
    ):
        assert result[key] == pytest.approx(float(expected[csv_key]), abs=tol), key
    assert result["max_voltage_change_bus_id"] == expected["max_abs_dv_bus"]
    assert result["max_line_p_change_id"] == expected["max_abs_dp_line_id"]
    assert result["max_line_q_change_id"] == expected["max_abs_dq_line_id"]
    assert result["generator_q_screening"]["violations"] == []
    assert result["synthetic_scenario"] is True and result["actual_operating_data"] is False
    assert result["model_caveats"]


def test_isolated_buses_never_get_numeric_voltages(service):
    result = service.run_line_contingency("484333456-1_0")
    isolated = set(result["isolated_buses"])
    assert {"SYNTHETIC_PV_19", "SYNTHETIC_PV_34", "SYNTHETIC_PV_35", "SYNTHETIC_PV_90"} == set(result["isolated_generator_ids"])
    assert not isolated & {item["bus_id"] for item in result["top_voltage_changes"]}
    lines = service._model.mapping["Line"]
    assert result["isolated_line_ids"]
    for line_id in result["isolated_line_ids"]:
        assert {lines[line_id]["original_bus0"], lines[line_id]["original_bus1"]} <= isolated
    assert "484333456-1_0" not in result["isolated_line_ids"]
    assert set(result["isolated_line_ids"]) <= set(result["disconnected_elements"])
    excluded_base_buses = set(service._model.preview["metadata"]["excluded_bus_ids"])
    assert not isolated & excluded_base_buses          # contingency islands != base-case exclusions


def test_unknown_and_excluded_lines_are_rejected(service):
    with pytest.raises(ms.UnknownOutageLine):
        service.run_line_contingency("no-such-line")
    excluded = sorted(service._model.excluded_line_ids)[0]
    with pytest.raises(ms.ExcludedOutageLine):
        service.run_line_contingency(excluded)


def test_requests_are_isolated_and_concurrent_runs_do_not_contaminate(service):
    sequential = {line: service.run_line_contingency(line) for line in CONTINGENCIES}
    with ThreadPoolExecutor(max_workers=6) as pool:
        parallel = list(pool.map(service.run_line_contingency, CONTINGENCIES * 2))
    for result in parallel:
        reference = sequential[result["outage_line_id"]]
        assert result["isolated_buses"] == reference["isolated_buses"]
        assert result["max_voltage_change_pu"] == pytest.approx(reference["max_voltage_change_pu"], abs=1e-12)
    # The base model on disk is unchanged: a fresh validation still matches PyPSA.
    model, net = ms.load_validated_model(files())
    assert all(net.get_lines()[["connected1", "connected2"]].all())
    assert net.get_variant_ids() == ["InitialState"]


def test_nonconverged_post_contingency_returns_no_numbers(service, monkeypatch):
    model, net = ms.load_validated_model(files())
    lf = model.base_check["loadflow"]
    real = ms.loadflow_parameters

    def one_iteration():
        params = real()
        params.provider_parameters = {**params.provider_parameters, "maxNewtonRaphsonIterations": "1"}
        return params

    monkeypatch.setattr(ms, "loadflow_parameters", one_iteration)
    result = ms.analyze_line_outage(net, model, "751261406-1_0", lf)
    assert result["post_status"] != "CONVERGED"
    assert result["pre_status"] != "CONVERGED"          # 1 iteration: SA base state fails too
    assert result["analysis_status"] == "SECURITY_NOT_CONVERGED"
    assert result["violation_assessment_available"] is False
    assert result["violated_equipment_count"] is None
    for key in ("post_voltage_min_pu", "max_voltage_change_pu", "max_line_p_change_mw", "post_max_line_loading_percent"):
        assert result[key] is None


def test_missing_xiidm_disables_only_security(tmp_path):
    status = ms.MongoliaSecurityService(files(xiidm=tmp_path / "missing.xiidm")).status()
    assert status["available"] is False and "XIIDM" in status["reason"]


def test_corrupt_mapping_is_rejected(tmp_path):
    reports = tmp_path / "reports"
    shutil.copytree(MONGOLIA_POWSYBL_REPORTS_DIR, reports)
    mapping = reports / "element_mapping.csv"
    text = mapping.read_text(encoding="utf-8").replace("L_222087807-1_0", "L_RENAMED", 1)
    mapping.write_text(text, encoding="utf-8")
    status = ms.MongoliaSecurityService(files(reports=reports)).status()
    assert status["available"] is False and "Line" in status["reason"]


def test_modified_electrical_parameters_fail_base_pf_validation(tmp_path):
    net = pp.network.load(str(MONGOLIA_XIIDM_FILE))
    line = net.get_lines().loc["L_484333456-1_0"]
    net.update_lines(id=["L_484333456-1_0"], b1=[line.b1 * 0.5], b2=[line.b2 * 0.5])
    tampered = tmp_path / "tampered.xiidm"
    net.save(str(tampered), format="XIIDM")
    status = ms.MongoliaSecurityService(files(xiidm=tampered)).status()
    assert status["available"] is False and "허용오차" in status["reason"]


# ---------------------------------------------------------------- API and capabilities

def test_api_runs_real_mongolia_security_analysis():
    response = client.get("/api/grids/mn-osm-35k/security-analysis", params={"outage_line_id": "222087807-1_0"})
    assert response.status_code == 200
    body = response.json()
    for field in ("outage_line_id", "base_converged", "pre_status", "post_status", "analysis_status",
                  "violation_assessment_available", "violated_equipment_count", "violations", "grid_id",
                  "scenario_id", "synthetic_scenario", "actual_operating_data", "main_component_converged",
                  "island_detected", "isolated_bus_count", "isolated_buses", "isolated_load_mw",
                  "isolated_generator_ids", "post_voltage_min_pu", "post_voltage_max_pu", "max_voltage_change_pu",
                  "max_voltage_change_bus_id", "max_line_p_change_mw", "max_line_p_change_id",
                  "max_line_q_change_mvar", "max_line_q_change_id", "post_max_line_loading_percent",
                  "screening_limits", "model_caveats"):
        assert field in body, field
    assert body["isolated_bus_count"] == 8 and body["grid_id"] == "mn-osm-35k"


def test_api_errors():
    url = "/api/grids/mn-osm-35k/security-analysis"
    assert client.get(url, params={"outage_line_id": "no-such-line"}).status_code == 404
    assert client.get(url, params={"outage_line_id": "236319607-1_0"}).status_code == 422


def test_api_reports_unavailable_model_without_breaking_map_and_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "MONGOLIA_XIIDM_FILE", tmp_path / "missing.xiidm")
    response = client.get("/api/grids/mn-osm-35k/security-analysis", params={"outage_line_id": "222087807-1_0"})
    assert response.status_code == 503
    grid = next(g for g in client.get("/api/grids").json()["grids"] if g["id"] == "mn-osm-35k")
    assert grid["security_analysis_available"] is False and "XIIDM" in grid["security_analysis_note"]
    assert grid["map_available"] is True and grid["pf_preview_available"] is True and grid["chat_available"] is True
    assert client.get("/api/grids/mn-osm-35k/map").status_code == 200
    assert client.get("/api/grids/mn-osm-35k/pf-preview").status_code == 200


def test_capabilities_are_split_per_feature():
    grids = {g["id"]: g for g in client.get("/api/grids").json()["grids"]}
    kpg, mn = grids["kr-kpg193"], grids["mn-osm-35k"]
    assert kpg["analysis_available"] is True
    assert kpg["security_analysis_available"] is kpg["sensitivity_analysis_available"] is True
    assert kpg["generator_contingency_available"] is True and kpg["chat_available"] is True
    assert mn["analysis_available"] is False
    assert mn["security_analysis_available"] is True
    assert mn["sensitivity_analysis_available"] is True     # enabled only after the FD self-check passes
    assert mn["generator_contingency_available"] is False
    assert mn["chat_available"] is True and mn["chat_mode"] == "mongolia_analysis"


def test_kpg_security_api_contract_unchanged():
    body = client.get("/api/security-analysis", params={"outage_line_id": "LINE-16-28"}).json()
    assert set(body) == {"outage_line_id", "base_converged", "post_status", "analysis_status",
                         "violation_assessment_available", "violated_equipment_count", "violations"}
    assert body["violated_equipment_count"] == 1
    assert body["violations"][0]["equipment_id"] == "LINE-16-22"
