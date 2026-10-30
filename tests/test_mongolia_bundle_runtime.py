"""Bundle-only routing, fail-closed integrity, cache ownership and error handling."""
import json
import os
from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient

import web_app
from ai_ems import mongolia_runtime as runtime
from ai_ems import mongolia_security as ms
from ai_ems.mongolia_sensitivity import SensitivityVerificationFailed
from mongolia_bundle_server_probe import BUNDLES, ROOT


@pytest.fixture
def bundle_copy(tmp_path):
    name, digest = BUNDLES["historical"]
    root = tmp_path/"bundle"
    shutil.copytree(ROOT/"outputs/mn_stage3b/bundles"/name, root)
    return root, digest


@pytest.mark.parametrize("directory,digest,selected", [(None, None, False), ("", None, True), (None, "0"*64, True), ("/missing-bundle", None, True)])
def test_explicit_partial_or_empty_settings_never_use_legacy(directory, digest, selected):
    choice = runtime.MongoliaBundleRuntime(directory, digest)
    assert choice.selected is selected
    if selected:
        assert choice.status()["available"] is False
        assert choice.catalog_paths() == (None, None)
        assert choice.diagnostics()["legacy_fallback_allowed"] is False
    else:
        assert choice.diagnostics()["mode"] == "legacy"


@pytest.mark.parametrize("failure", ["manifest", "unauthorized-id", "missing-map", "missing-mapping", "model",
                                     "pf-source", "scenario", "mapping", "symlink", "hardlink", "escape", "mixed-reports"])
def test_corruption_rejected_before_model_execution(bundle_copy, tmp_path, monkeypatch, failure):
    root, digest = bundle_copy
    target = root/"assets/map.json"
    if failure == "manifest":
        digest = "0"*64
    elif failure == "unauthorized-id":
        path = root/"manifest.json"
        value = json.loads(path.read_text()); value["bundle_id"] = "unapproved-version"
        path.write_text(json.dumps(value))
    elif failure == "missing-map":
        target.unlink()
    elif failure == "missing-mapping":
        (root/"reports/element_mapping.csv").unlink()
    elif failure in ("symlink", "hardlink"):
        original = tmp_path/"original.json"
        shutil.copyfile(target, original)
        target.unlink()
        target.symlink_to(original) if failure == "symlink" else os.link(original, target)
    elif failure == "model":
        path = root/"models/mn35k_synthetic_powsybl.xiidm"
        path.write_text(path.read_text().replace('r="', 'r="1', 1))
    elif failure == "pf-source":
        path = root/"assets/pf_preview.json"
        value = json.loads(path.read_text()); value["metadata"]["map_json_sha256"] = "0"*64
        path.write_text(json.dumps(value))
    elif failure == "scenario":
        path = root/"reports/conversion_validation.json"
        value = json.loads(path.read_text()); value["input_scenario"]["meta_scenario_id"] = "data_informed"
        path.write_text(json.dumps(value))
    elif failure == "mapping":
        path = root/"reports/element_mapping.csv"
        path.write_text(path.read_text().replace("VL_25", "VL_WRONG", 1))
    elif failure == "escape":
        path = root/"manifest.json"
        value = json.loads(path.read_text()); value["runtime_paths"]["xiidm"] = "../../legacy.xiidm"
        path.write_text(json.dumps(value))
    else:
        name = BUNDLES["stage3a"][0]
        shutil.copyfile(ROOT/"outputs/mn_stage3b/bundles"/name/"reports/conversion_validation.json", root/"reports/conversion_validation.json")
    monkeypatch.setattr(runtime, "observe", lambda *args: pytest.fail("An untrusted model was executed"))
    choice = runtime.MongoliaBundleRuntime(str(root), digest)
    assert choice.status()["available"] is False
    assert choice.diagnostics()["validation_status"] == "UNAVAILABLE"
    with pytest.raises(ms.MongoliaModelUnavailable, match="no legacy fallback"):
        choice.run_line_contingency("751261406-1_0")


def test_symlink_bundle_root_not_silently_resolved(bundle_copy, tmp_path):
    root, digest = bundle_copy
    alias = tmp_path/"alias"
    alias.symlink_to(root, target_is_directory=True)
    choice = runtime.MongoliaBundleRuntime(str(alias), digest)
    assert choice.status()["available"] is False
    assert "Symlink" in choice.diagnostics()["error"]


@pytest.mark.parametrize("failure", ["load", "nonconverged", "sensitivity-self-check", "runtime-library"])
def test_live_startup_failures_are_unavailable(bundle_copy, monkeypatch, failure):
    root, digest = bundle_copy
    def fail(*args, **kwargs):
        raise ms.MongoliaModelUnavailable("injected "+failure)
    if failure == "load":
        monkeypatch.setattr(ms, "_load_network", fail)
    elif failure == "nonconverged":
        monkeypatch.setattr(ms, "_run_base_lf", fail)
    elif failure == "sensitivity-self-check":
        monkeypatch.setattr(ms, "_sensitivity_self_check", lambda *args: {"passed": False, "reason": "injected sensitivity"})
    else:
        monkeypatch.setattr(runtime.MongoliaBundleRuntime, "_runtime_environment", staticmethod(fail))
    choice = runtime.MongoliaBundleRuntime(str(root), digest)
    assert not choice.status()["available"]
    assert choice.diagnostics()["validation_status"] == "UNAVAILABLE"


def test_version_is_fixed_and_mutation_invalidates_cached_service_until_restart(bundle_copy, monkeypatch):
    root, digest = bundle_copy
    choice = runtime.MongoliaBundleRuntime(str(root), digest)
    assert choice.status()["available"]
    original_service = choice._service
    monkeypatch.setenv("AI_EMS_MN_BUNDLE_DIR", "/different-version")
    monkeypatch.setenv("AI_EMS_MN_BUNDLE_MANIFEST_SHA256", "0"*64)
    assert choice.diagnostics()["bundle_id"] == BUNDLES["historical"][0]
    assert choice._service is original_service
    path = root/"reports/element_mapping.csv"
    before = path.read_bytes()
    path.write_bytes(before+b"\n")
    assert not choice.status()["available"]
    assert choice._service is None
    path.write_bytes(before)
    assert not choice.status()["available"], "Failure must not silently reactivate cached data"
    restarted = runtime.MongoliaBundleRuntime(str(root), digest)
    assert restarted.status()["available"] and restarted._service is not original_service


def test_http_bundle_failure_disables_only_mongolia(bundle_copy, monkeypatch):
    root, _ = bundle_copy
    choice = runtime.MongoliaBundleRuntime(str(root), "0"*64)
    monkeypatch.setattr(web_app, "_mongolia_bundle_runtime", choice)
    with TestClient(web_app.app) as client:
        grids = {g["id"]: g for g in client.get("/api/grids").json()["grids"]}
        assert not grids["mn-osm-35k"]["map_available"]
        assert grids["kr-kpg193"]["map_available"] and grids["kr-osm-transmission"]["map_available"]
        for path in ("map", "pf-preview", "reference-layers", "security-analysis?outage_line_id=751261406-1_0"):
            assert client.get("/api/grids/mn-osm-35k/"+path).status_code == 503
        assert client.get("/api/grids/mn-osm-35k/security-status").json()["available"] is False
        assert client.post("/api/grids/mn-osm-35k/chat", json={"session_id": "bad-bundle", "message": "PF 조회"}).status_code == 503
        # The existing KPG-only endpoint keeps rejecting Mongolia with 409.
        assert client.post("/api/chat", json={"session_id": "bad-bundle", "grid_id": "mn-osm-35k", "message": "PF 조회"}).status_code == 409
        diagnostic = client.get("/api/grids/mn-osm-35k/runtime-status").json()
        assert diagnostic["mode"] == "bundle" and not diagnostic["legacy_fallback_allowed"]


@pytest.mark.parametrize("operation,exception,expected", [
    ("run_line_contingency", RuntimeError("injected N-1 exception"), 500),
    ("run_line_sensitivity", SensitivityVerificationFailed("injected Sensitivity error"), 422),
])
def test_operation_exceptions_never_return_success(bundle_copy, monkeypatch, operation, exception, expected):
    root, digest = bundle_copy
    choice = runtime.MongoliaBundleRuntime(str(root), digest)
    assert choice.status()["available"]
    def fail(*args, **kwargs):
        raise exception
    monkeypatch.setattr(choice._service, operation, fail)
    monkeypatch.setattr(web_app, "_mongolia_bundle_runtime", choice)
    endpoint = "security-analysis" if operation == "run_line_contingency" else "sensitivity-analysis"
    with TestClient(web_app.app) as client:
        response = client.get(f"/api/grids/mn-osm-35k/{endpoint}?outage_line_id=751261406-1_0")
        assert response.status_code == expected
        assert "injected" in response.json()["detail"]
        assert client.get("/api/grids/mn-osm-35k/map").status_code == 200


def test_request_paths_cannot_select_another_bundle_and_no_secrets_in_diagnostic(bundle_copy, monkeypatch):
    root, digest = bundle_copy
    choice = runtime.MongoliaBundleRuntime(str(root), digest)
    monkeypatch.setattr(web_app, "_mongolia_bundle_runtime", choice)
    monkeypatch.setenv("STAGE3C_TEST_SECRET", "not-for-diagnostics")
    with TestClient(web_app.app) as client:
        response = client.get("/api/grids/mn-osm-35k/runtime-status?bundle=/other&manifest_sha256=bad")
        assert response.json()["bundle_id"] == BUNDLES["historical"][0]
        assert "not-for-diagnostics" not in response.text
        assert client.post("/api/grids/mn-osm-35k/runtime-status", json={"bundle": "/other"}).status_code == 405
