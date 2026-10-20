"""Mongolia post-contingency AC branch-flow sensitivity (real OpenLoadFlow runs).

Every generator is cross-checked against central finite differences computed with
independent AC PF runs on a separate network object.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

import pypowsybl as pp
import pytest
from fastapi.testclient import TestClient

import web_app
from ai_ems import mongolia_security as ms
from ai_ems import mongolia_sensitivity as sens
from ai_ems.config import MONGOLIA_POWSYBL_REPORTS_DIR, MONGOLIA_XIIDM_FILE, PROJECT_ROOT
from ai_ems.network import LOADFLOW_PARAMETERS

PREVIEW = PROJECT_ROOT / "data/grids/mn35k-synthetic-pf-preview.json"
MAP = PROJECT_ROOT / "data/grids/mn-osm-35k-map.json"
pytestmark = pytest.mark.skipif(
    not (MONGOLIA_XIIDM_FILE.is_file() and (MONGOLIA_POWSYBL_REPORTS_DIR / "element_mapping.csv").is_file()
         and PREVIEW.is_file() and MAP.is_file()),
    reason="Mongolia XIIDM / conversion reports / PF preview not available",
)
OUTAGE = "751261406-1_0"
RADIAL_MONITOR = "303374638-1_0"
client = TestClient(web_app.app)


def files(xiidm=MONGOLIA_XIIDM_FILE):
    return ms.ModelFiles(xiidm, MONGOLIA_POWSYBL_REPORTS_DIR, PREVIEW, MAP)


@pytest.fixture(scope="module")
def service():
    svc = ms.MongoliaSecurityService(files())
    status = svc.status()
    assert status["available"] and status["sensitivity_available"], status
    return svc


def finite_differences(line_id: str, outage: str = OUTAGE) -> dict[str, float]:
    """Central differences of side-1 P of `line_id` for every generator, independent network."""
    model, net = ms.load_validated_model(files())
    mon = model.mapping["Line"][line_id]["iidm_id"]
    net.update_lines(id=[model.mapping["Line"][outage]["iidm_id"]], connected1=[False], connected2=[False])
    params = ms.loadflow_parameters()
    out = {}
    for g in net.get_generators().index:
        target = float(net.get_generators().at[g, "target_p"])
        flows = []
        for step in (0.1, -0.1):
            net.update_generators(id=[g], target_p=[target + step])
            assert pp.loadflow.run_ac(net, parameters=params)[0].status.name == "CONVERGED"
            flows.append(float(net.get_lines().at[mon, "p1"]))
        net.update_generators(id=[g], target_p=[target])
        out[model.iidm_to_original[g][1]] = (flows[0] - flows[1]) / 0.2
    return out


@pytest.mark.parametrize("monitored", [RADIAL_MONITOR, "484276912-1_0", "259474322-1_0"])
def test_every_generator_matches_finite_differences(service, monitored):
    result = service.run_line_sensitivity(OUTAGE, monitored, top_n=45)
    assert result["analysis_status"] == "COMPLETED"
    fd = finite_differences(monitored)
    assert len(result["candidates"]) == 44                      # 45 generators minus the slack
    for c in result["candidates"]:
        assert math.isfinite(c["sensitivity"])
        assert abs(c["sensitivity"] - fd[c["generator_id"]]) <= sens.FD_ABS_TOL + sens.FD_REL_TOL * abs(fd[c["generator_id"]])
    assert abs(fd[ms.SLACK_GENERATOR_ID]) < 1e-9                # slack absorbs the injection: 0 by definition
    assert result["finite_difference_check"]["passed"] is True
    assert len(result["finite_difference_check"]["results"]) >= 2


def test_definition_sign_units_and_reference_flow(service):
    result = service.run_line_sensitivity(OUTAGE, RADIAL_MONITOR, top_n=5)
    assert result["unit"] == "MW/MW"
    assert "BRANCH_ACTIVE_POWER_1" in result["function"] and "INJECTION_ACTIVE_POWER" in result["variable"]
    assert result["monitored_line"]["bus0"] == "83" and result["monitored_line"]["bus1"] == "13"
    check = result["reference_check"]
    assert check["post_reference_flow_mw"] == pytest.approx(check["post_independent_ac_pf_mw"], abs=1e-6)
    assert check["pre_reference_flow_mw"] == pytest.approx(check["pre_independent_ac_pf_mw"], abs=1e-6)
    # Matches the validated N-1 flow change on this line (+0.3455 MW at bus0).
    assert result["post_flow_mw"] - result["base_flow_mw"] == pytest.approx(0.345546654, abs=1e-6)
    # Radial after the contingency: downstream injections reduce P entering at bus 83 one-for-one.
    assert result["monitored_line_radial_after_contingency"] is True
    assert result["candidates"][0]["sensitivity"] == pytest.approx(-1.0133, abs=1e-3)
    assert {c["generator_id"] for c in result["candidates"]}.isdisjoint({ms.SLACK_GENERATOR_ID})
    assert all(c["synthetic_generator"] is True for c in result["candidates"])
    ranks = [abs(c["sensitivity"]) for c in result["candidates"]]
    assert ranks == sorted(ranks, reverse=True)


def test_auto_selection_prefers_meshed_line_with_largest_dp(service):
    result = service.run_line_sensitivity(OUTAGE, None, top_n=3)
    assert result["monitored_line_selection"]["mode"] == "auto"
    assert result["monitored_line_id"] == "484276912-1_0"
    assert result["monitored_line_radial_after_contingency"] is False
    alternatives = result["monitored_line_selection"]["alternatives"]
    assert alternatives and alternatives[0]["line_id"] == RADIAL_MONITOR
    assert alternatives[0]["radial_after_contingency"] is True


@pytest.mark.parametrize("outage", ["222087807-1_0", "484333456-1_0"])
def test_islanding_contingencies_are_not_evaluated(service, outage):
    result = service.run_line_sensitivity(outage, None)
    assert result["analysis_status"] == "NOT_EVALUATED_ISLANDING"
    assert result["sensitivity_available"] is False
    assert result["candidates"] == [] and result["monitored_line_id"] is None
    assert result["isolated_bus_count"] > 0


def test_invalid_inputs(service):
    with pytest.raises(ms.UnknownOutageLine):
        service.run_line_sensitivity("no-such-line")
    with pytest.raises(sens.InvalidMonitoredLine):
        service.run_line_sensitivity(OUTAGE, "no-such-line")
    with pytest.raises(sens.InvalidMonitoredLine):
        service.run_line_sensitivity(OUTAGE, OUTAGE)
    excluded = sorted(service._model.excluded_line_ids)[0]
    with pytest.raises(sens.InvalidMonitoredLine):
        service.run_line_sensitivity(OUTAGE, excluded)


def test_nonconverged_post_contingency_returns_no_ranking(service, monkeypatch):
    model, net = ms.load_validated_model(files())
    real = ms.loadflow_parameters

    def one_iteration():
        params = real()
        params.provider_parameters = {**params.provider_parameters, "maxNewtonRaphsonIterations": "1"}
        return params

    monkeypatch.setattr(ms, "loadflow_parameters", one_iteration)
    result = sens.analyze_line_sensitivity(net, model, OUTAGE, RADIAL_MONITOR)
    assert result["analysis_status"] == "POST_CONTINGENCY_NOT_CONVERGED"
    assert result["candidates"] == [] and result["sensitivity_available"] is False


def test_nonfinite_sensitivity_is_reported_as_verification_failure(service, monkeypatch):
    model, net = ms.load_validated_model(files())
    real_run = pp.sensitivity.AcSensitivityAnalysis.run

    def poisoned(self, *args, **kwargs):
        result = real_run(self, *args, **kwargs)
        original = result.get_sensitivity_matrix

        def matrix(matrix_id="default", contingency_id=None):
            frame = original(matrix_id, contingency_id)
            if contingency_id is not None:
                frame = frame.copy()
                frame.iloc[0, 0] = float("nan")
            return frame

        result.get_sensitivity_matrix = matrix
        return result

    monkeypatch.setattr(pp.sensitivity.AcSensitivityAnalysis, "run", poisoned)
    with pytest.raises(sens.SensitivityVerificationFailed, match="NaN"):
        sens.analyze_line_sensitivity(net, model, OUTAGE, RADIAL_MONITOR)


def test_base_network_restored_and_concurrent_requests_independent(service):
    model, net = ms.load_validated_model(files())
    before = net.get_generators()[["target_p"]].copy()
    sens.analyze_line_sensitivity(net, model, OUTAGE, RADIAL_MONITOR)
    assert net.get_variant_ids() == ["InitialState"]
    assert net.get_generators()[["target_p"]].equals(before)
    assert bool(net.get_lines().loc[model.mapping["Line"][OUTAGE]["iidm_id"], "connected1"])
    reference = {m: service.run_line_sensitivity(OUTAGE, m, top_n=3) for m in (RADIAL_MONITOR, "484276912-1_0")}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda m: service.run_line_sensitivity(OUTAGE, m, top_n=3),
                                [RADIAL_MONITOR, "484276912-1_0"] * 2))
    for result in results:
        expected = reference[result["monitored_line_id"]]
        assert [c["generator_id"] for c in result["candidates"]] == [c["generator_id"] for c in expected["candidates"]]
        assert result["candidates"][0]["sensitivity"] == pytest.approx(expected["candidates"][0]["sensitivity"], abs=1e-12)


def test_kpg_parameters_untouched(service):
    before = str(LOADFLOW_PARAMETERS)
    service.run_line_sensitivity(OUTAGE, RADIAL_MONITOR, top_n=1)
    assert str(LOADFLOW_PARAMETERS) == before


def test_missing_xiidm_disables_sensitivity_only(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "MONGOLIA_XIIDM_FILE", tmp_path / "missing.xiidm")
    response = client.get("/api/grids/mn-osm-35k/sensitivity-analysis", params={"outage_line_id": OUTAGE})
    assert response.status_code == 503
    grid = next(g for g in client.get("/api/grids").json()["grids"] if g["id"] == "mn-osm-35k")
    assert grid["sensitivity_analysis_available"] is False and grid["security_analysis_available"] is False
    assert grid["chat_available"] is True and grid["pf_preview_available"] is True


def test_api_returns_real_ranking_and_errors():
    url = "/api/grids/mn-osm-35k/sensitivity-analysis"
    body = client.get(url, params={"outage_line_id": OUTAGE, "monitored_line_id": RADIAL_MONITOR, "top_n": 5}).json()
    assert body["analysis_status"] == "COMPLETED" and len(body["candidates"]) == 5
    assert body["candidates"][0]["generator_id"] == "SYNTHETIC_PV_7"
    island = client.get(url, params={"outage_line_id": "222087807-1_0"}).json()
    assert island["analysis_status"] == "NOT_EVALUATED_ISLANDING" and island["candidates"] == []
    assert client.get(url, params={"outage_line_id": "no-such-line"}).status_code == 404
    assert client.get(url, params={"outage_line_id": OUTAGE, "monitored_line_id": "x"}).status_code == 422
    grid = next(g for g in client.get("/api/grids").json()["grids"] if g["id"] == "mn-osm-35k")
    assert grid["sensitivity_analysis_available"] is True and grid["generator_contingency_available"] is False


def test_kpg_sensitivity_api_contract_unchanged():
    body = client.get("/api/sensitivity-analysis", params={"outage_line_id": "LINE-16-28", "top_n": 3}).json()
    assert body["analysis_status"] == "COMPLETED"
    assert body["monitored_line_id"] == "LINE-16-22"
    assert set(body) == {"outage_line_id", "monitored_line_id", "candidate_count", "candidates",
                         "target_selection", "analysis_status"}
