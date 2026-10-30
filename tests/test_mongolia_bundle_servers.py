"""Actual mount-isolated EMS servers, with process-start version selection."""
import json
import os
from pathlib import Path

import pytest

from mongolia_bundle_server_probe import BUNDLES, ROOT, collect_api, get, isolated_server, prepare_workspace, scripted_llm
from pipelines.mn.bundle import tree_files

pytestmark = pytest.mark.skipif(os.environ.get("AI_EMS_STAGE3C_TESTS") != "1",
                                reason="Stage 3C real server test needs supplied bundles+bwrap; set AI_EMS_STAGE3C_TESTS=1")


@pytest.fixture(scope="module")
def sequence(tmp_path_factory):
    area = tmp_path_factory.mktemp("stage3c-independent-servers")
    # Must remain outside the development checkout that the namespace masks.
    assert not area.is_relative_to(ROOT), "Use a /tmp --basetemp for Stage 3C server tests"
    root = prepare_workspace(area)
    before = {source: tree_files(root/"bundles"/source) for source in BUNDLES}
    phases = []
    with scripted_llm() as (llm, calls):
        for index, source in enumerate(("historical", "stage3a", "historical")):
            phase = {"source": source}
            with isolated_server(root, source, llm, area/f"{index}-physics.log", app="ai_ems.api.app:app") as (physics, physics_info):
                phase["physics_health"] = get(physics, "/api/health")
                with isolated_server(root, source, llm, area/f"{index}-web.log") as (base, web_info):
                    phase.update(collect_api(base))
                    if os.environ.get("AI_EMS_BROWSER_TESTS") == "1":
                        from browser.chrome_cdp import run_probe
                        from mongolia_bundle_server_probe import free_port
                        chrome = Path(os.environ["AI_EMS_CHROME"])
                        phase["chrome"] = run_probe((chrome, False), base+"/", ROOT/"tests/browser/mongolia_bundle_probe.js",
                                                    area/f"chrome-{index}", free_port)
                        assert all(r["pass"] for r in phase["chrome"]["results"]), phase["chrome"]
                phase["web_process"] = web_info
            phase["physics_process"] = physics_info
            assert web_info["stopped"] and physics_info["stopped"]
            assert web_info["graceful_shutdown"] and physics_info["graceful_shutdown"]
            assert web_info["port_closed"] and physics_info["port_closed"]
            assert not web_info.get("forced_shutdown") and not physics_info.get("forced_shutdown")
            phases.append(phase)
    assert before == {source: tree_files(root/"bundles"/source) for source in BUNDLES}
    result = {"phases": phases, "scripted_provider_calls": calls, "bundles_unchanged": True,
              "workspace": str(root), "production_env_copied": False}
    (area/"sequence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


@pytest.mark.parametrize("index", [0, 1, 2])
def test_real_http_models_n1_sensitivity_and_agent(sequence, index):
    phase = sequence["phases"][index]
    source = phase["source"]
    assert phase["diagnostic"]["bundle_id"] == BUNDLES[source][0]
    assert phase["diagnostic"]["manifest_sha256"] == BUNDLES[source][1]
    assert phase["diagnostic"]["mode"] == "bundle"
    assert phase["diagnostic"]["numerical_checks"] == 186
    assert all(c["passed"] for c in phase["numeric_checks"])
    assert phase["map_counts"]["mn-osm-35k"] == {"buses": 173, "lines": 159, "transformers": 27}
    assert phase["pf_metadata"]["generator_count"] == 45
    assert sum(r["candidate_count"] for r in phase["numerical"]["sensitivity"]) == 132
    assert [r["isolated_bus_count"] for r in phase["numerical"]["n1"]] == [8, 0, 11]
    assert all(r["finite_difference_check"]["passed"] for r in phase["numerical"]["sensitivity"])


def test_original_paths_unreadable_in_every_actual_server(sequence):
    for phase in sequence["phases"]:
        for key in ("web_process", "physics_process"):
            proof = phase[key]["isolation"]
            assert len(proof["isolation_proof"]) == 9
            assert all(r["read_blocked"] for r in proof["isolation_proof"])
            assert proof["pypsa_available"] is False and proof["pixi_available"] is False


def test_return_to_historical_with_clean_process_and_session(sequence):
    first, other, restored = sequence["phases"]
    assert len({p["web_process"]["pid"] for p in sequence["phases"]}) == 3
    assert first["diagnostic"]["bundle_id"] != other["diagnostic"]["bundle_id"]
    assert first["diagnostic"]["bundle_id"] == restored["diagnostic"]["bundle_id"]
    assert first["numerical"] == restored["numerical"]
    assert first["chat"] == restored["chat"]
    for phase in sequence["phases"]:
        assert all(str(Path(path)).startswith(str(Path(sequence["workspace"])/"bundles"/phase["source"]))
                   for path in phase["diagnostic"]["paths"].values())
    assert sequence["bundles_unchanged"]


def test_corrupt_explicit_bundle_keeps_kpg_and_korea_and_never_falls_back(tmp_path):
    assert not tmp_path.is_relative_to(ROOT)
    root = prepare_workspace(tmp_path)
    with scripted_llm() as (llm, _):
        with isolated_server(root, "historical", llm, tmp_path/"bad-trust-web.log",
                             override={"AI_EMS_MN_BUNDLE_MANIFEST_SHA256": "0"*64}) as (base, info):
            diagnostic = get(base, "/api/grids/mn-osm-35k/runtime-status")
            assert diagnostic["validation_status"] == "UNAVAILABLE" and diagnostic["legacy_fallback_allowed"] is False
            catalog = get(base, "/api/grids")["grids"]
            mn = next(g for g in catalog if g["id"] == "mn-osm-35k")
            assert not mn["map_available"] and not mn["security_analysis_available"] and not mn["chat_available"]
            for grid in ("kr-kpg193", "kr-osm-transmission"):
                assert get(base, f"/api/grids/{grid}/map")["buses"]
            from mongolia_bundle_server_probe import request
            for path in ("map", "pf-preview", "reference-layers", "security-analysis?outage_line_id=751261406-1_0", "sensitivity-analysis?outage_line_id=751261406-1_0"):
                assert request(base+"/api/grids/mn-osm-35k/"+path)[0] == 503
            assert get(base, "/api/grids/mn-osm-35k/security-status")["available"] is False
    assert info["stopped"] and not info.get("forced_shutdown")
    assert info["graceful_shutdown"] and info["port_closed"]
