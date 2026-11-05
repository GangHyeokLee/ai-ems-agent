from __future__ import annotations

import copy

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import web_app
from ai_ems import kpg_pf_preview
from ai_ems.config import CASE_FILE
from ai_ems.network import load_network


client = TestClient(web_app.app)


def test_kpg_preview_runs_on_a_separate_model_and_maps_real_results():
    original_buses = web_app.network.get_buses(all_attributes=True).copy(deep=True)
    original_lines = web_app.network.get_lines(all_attributes=True).copy(deep=True)
    original_generators = web_app.network.get_generators(all_attributes=True).copy(deep=True)

    preview = kpg_pf_preview.get_kpg_pf_preview()
    result = kpg_pf_preview.validate_kpg_pf_preview(preview, web_app.network_map())

    assert result["metadata"]["grid_id"] == "kr-kpg193"
    assert result["metadata"]["synthetic_scenario"] is True
    assert result["metadata"]["actual_operating_data"] is False
    assert result["metadata"]["pf_status"] == "AC_PF_CONVERGED"
    assert result["summary"]["iteration_count"] == 3
    assert len(result["buses"]) == 193
    assert len(result["lines"]) == 385
    assert len(result["transformers"]) == 0
    assert result["metadata"]["non_ac_equipment"]["hvdc_lines"]["count"] == 2
    assert all(line["rating_mva"] > 0 for line in result["lines"])
    assert all(line["loading_percent"] is not None for line in result["lines"])
    assert 0.95 < result["summary"]["voltage_min_pu"] < 1.0
    assert 1.0 < result["summary"]["voltage_max_pu"] < 1.05

    pd.testing.assert_frame_equal(
        web_app.network.get_buses(all_attributes=True), original_buses
    )
    pd.testing.assert_frame_equal(
        web_app.network.get_lines(all_attributes=True), original_lines
    )
    pd.testing.assert_frame_equal(
        web_app.network.get_generators(all_attributes=True), original_generators
    )


def test_kpg_preview_endpoint_and_error_leave_map_available(monkeypatch):
    payload = kpg_pf_preview.get_kpg_pf_preview()
    response = client.get("/api/grids/kr-kpg193/pf-preview")
    catalog = {item["id"]: item for item in client.get("/api/grids").json()["grids"]}

    assert response.status_code == 200
    assert response.json() == payload
    assert catalog["kr-kpg193"]["pf_preview_available"] is True
    assert catalog["kr-kpg193"]["analysis_available"] is True

    invalid = copy.deepcopy(payload)
    invalid["lines"][0]["bus2_id"] = "unknown"
    monkeypatch.setattr(web_app, "get_kpg_pf_preview", lambda: invalid)
    assert client.get("/api/grids/kr-kpg193/pf-preview").status_code == 422

    def unavailable():
        raise kpg_pf_preview.KpgPreviewUnavailable("load flow did not converge")

    monkeypatch.setattr(web_app, "get_kpg_pf_preview", unavailable)
    failed = client.get("/api/grids/kr-kpg193/pf-preview")
    assert failed.status_code == 503
    assert client.get("/api/grids/kr-kpg193/map").status_code == 200


def test_kpg_preview_validation_rejects_topology_mismatch():
    payload = kpg_pf_preview.get_kpg_pf_preview()
    changed = {**payload, "lines": [dict(row) for row in payload["lines"]]}
    changed["lines"][0]["bus2_id"] = "999999"

    with pytest.raises(ValueError, match="연결 모선"):
        kpg_pf_preview.validate_kpg_pf_preview(changed, web_app.network_map())


def test_kpg_nonconvergence_does_not_return_success_shaped_results(monkeypatch):
    failed = type("Result", (), {"status": type("Status", (), {"name": "FAILED"})()})()
    monkeypatch.setattr(
        kpg_pf_preview.pp.loadflow,
        "run_ac",
        lambda *_args, **_kwargs: [failed],
    )

    with pytest.raises(kpg_pf_preview.KpgPreviewUnavailable, match="FAILED"):
        kpg_pf_preview.build_kpg_pf_preview(
            object(),
            source_model="case.mat",
            source_sha256="a" * 64,
        )


def test_kpg_loader_uses_configured_case_without_mutating_shared_network(monkeypatch):
    isolated = load_network(CASE_FILE)
    shared_before = web_app.network.get_buses(all_attributes=True).copy(deep=True)
    observed: list[object] = []

    def fake_build(
        network,
        *,
        source_model,
        source_sha256,
        map_model_sha256,
        map_source_provenance,
    ):
        observed.append(network)
        return {"network_id": id(network), "source_model": source_model}

    monkeypatch.setattr(kpg_pf_preview, "load_network", lambda _: isolated)
    monkeypatch.setattr(kpg_pf_preview, "build_kpg_pf_preview", fake_build)
    monkeypatch.setattr(kpg_pf_preview, "_sha256", lambda _: "a" * 64)
    monkeypatch.setattr(kpg_pf_preview, "_cached_preview", None)
    monkeypatch.setattr(kpg_pf_preview, "_cached_error", None)
    monkeypatch.setattr(kpg_pf_preview, "CASE_FILE", CASE_FILE)

    first = kpg_pf_preview.get_kpg_pf_preview()
    second = kpg_pf_preview.get_kpg_pf_preview()

    assert first == second
    assert len(observed) == 1
    assert observed[0] is isolated
    assert observed[0] is not web_app.network
    pd.testing.assert_frame_equal(
        web_app.network.get_buses(all_attributes=True), shared_before
    )

    monkeypatch.setattr(kpg_pf_preview, "_cached_preview", None)
    monkeypatch.setattr(kpg_pf_preview, "_cached_error", None)
