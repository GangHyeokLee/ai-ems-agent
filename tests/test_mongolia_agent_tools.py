import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from ai_ems.agent.mongolia_graph import (
    MONGOLIA_SYSTEM_PROMPT,
    detect_unsupported_request,
    unsupported_request_answer,
)
from ai_ems.agent.mongolia_tools import (
    DATA_UNAVAILABLE,
    EXCLUDED,
    MAX_TOP_N,
    MONGOLIA_TOOL_NAMES,
    NOT_FOUND,
    bus_pf,
    create_mongolia_tools,
    line_pf,
    pf_ranking,
    pf_summary,
    transformer_pf,
)
from ai_ems.grid_catalog import MONGOLIA_GRID_ID, list_grids
from ai_ems.pf_preview import load_mongolia_pf_preview

ROOT = Path(__file__).resolve().parents[1]
EXPORT_TEST = ROOT / "tests" / "test_mn35k_pf_export.py"
spec = importlib.util.spec_from_file_location("mn_agent_fixture", EXPORT_TEST)
export_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_fixture)

KPG_TOOL_NAMES = {
    "network_summary",
    "line_list",
    "line_detail",
    "generator_list",
    "line_contingency",
    "generator_contingency_analysis",
    "generator_contingency_comparison",
    "generator_contingency_screening",
    "generator_risk_response_analysis",
    "generator_sensitivity",
    "balanced_redispatch_validation",
    "contingency_response_analysis",
    "generator_outage_response_analysis",
}


def write_assets(tmp_path):
    inputs = export_fixture.fixture_inputs()
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps(inputs["map_data"]), encoding="utf-8")
    inputs["map_json_sha256"] = hashlib.sha256(map_file.read_bytes()).hexdigest()
    preview = export_fixture.module.build_preview(**inputs)
    preview_file = tmp_path / "preview.json"
    preview_file.write_text(json.dumps(preview, allow_nan=False), encoding="utf-8")
    return map_file, preview_file


@pytest.fixture
def assets(tmp_path):
    return write_assets(tmp_path)


@pytest.fixture
def preview(assets):
    map_file, preview_file = assets
    return load_mongolia_pf_preview(preview_file, map_file)


def tools_by_name(map_file, preview_file):
    tools = create_mongolia_tools(lambda: load_mongolia_pf_preview(preview_file, map_file))
    return {item.name: item for item in tools}


def test_tool_set_is_mongolia_only_and_takes_no_paths(assets):
    tools = tools_by_name(*assets)
    assert set(tools) == MONGOLIA_TOOL_NAMES
    assert not set(tools) & KPG_TOOL_NAMES
    for item in tools.values():
        properties = item.args_schema.model_json_schema().get("properties", {})
        assert not any("path" in name or "file" in name for name in properties)


def test_existing_bus_returns_validated_values(preview):
    expected = next(bus for bus in preview["buses"] if bus["bus_id"] == "137")
    result = bus_pf(preview, "Bus 137")
    assert result["status"] == "ANALYZED"
    assert result["bus_id"] == "137"
    assert result["v_mag_pu"] == expected["v_mag_pu"]
    assert result["v_ang_deg"] == expected["v_ang_deg"]
    assert result["control"] == expected["control"]
    assert result["voltage_kv"] == expected["voltage_kv"]
    assert result["voltage_violation"] is expected["voltage_violation"]
    assert result["scenario"]["synthetic_scenario"] is True
    assert result["scenario"]["actual_operating_data"] is False


def test_excluded_bus_is_distinct_from_unknown_bus_and_has_no_values(preview):
    excluded_id = preview["metadata"]["excluded_bus_ids"][0]
    excluded = bus_pf(preview, excluded_id)
    unknown = bus_pf(preview, "99999")
    assert excluded["status"] == EXCLUDED
    assert unknown["status"] == NOT_FOUND
    for result in (excluded, unknown):
        assert "v_mag_pu" not in result
        assert "v_ang_deg" not in result


def test_line_preserves_original_pq_signs(preview):
    original = preview["lines"][0]
    result = line_pf(preview, original["line_id"])
    assert result["status"] == "ANALYZED"
    for key in ("p0_mw", "q0_mvar", "p1_mw", "q1_mvar", "rating_mva", "loading_percent"):
        assert result[key] == original[key]
    assert (result["bus1_id"], result["bus2_id"]) == (str(original["bus1_id"]), str(original["bus2_id"]))
    assert "모선에서 설비" in result["sign_convention"]


def test_line_with_mixed_signs_is_not_normalized(preview):
    changed = copy.deepcopy(preview)
    changed["lines"][0].update({"p0_mw": -3.5, "q0_mvar": 1.25, "p1_mw": 3.4, "q1_mvar": -1.5})
    result = line_pf(changed, changed["lines"][0]["line_id"])
    assert (result["p0_mw"], result["q0_mvar"], result["p1_mw"], result["q1_mvar"]) == (-3.5, 1.25, 3.4, -1.5)


def test_excluded_and_unknown_lines(preview):
    excluded = line_pf(preview, preview["metadata"]["excluded_line_ids"][0])
    unknown = line_pf(preview, "no-such-line")
    assert excluded["status"] == EXCLUDED
    assert unknown["status"] == NOT_FOUND
    assert "p0_mw" not in excluded and "p0_mw" not in unknown


def test_transformer_lookup_and_unverified_note(preview):
    original = preview["transformers"][0]
    result = transformer_pf(preview, original["transformer_id"])
    assert result["status"] == "ANALYZED"
    assert result["bus1_voltage_kv"] == original["bus1_voltage_kv"]
    assert result["p0_mw"] == original["p0_mw"]
    assert result["loading_percent"] == original["loading_percent"]
    assert "검증되지 않았습니다" in result["model_note"]
    assert transformer_pf(preview, "transf_missing")["status"] == NOT_FOUND


def test_summary_keeps_scenario_metadata_and_caveats(preview):
    result = pf_summary(preview)
    metadata, summary = preview["metadata"], preview["summary"]
    assert result["scenario"]["scenario_id"] == metadata["scenario_id"]
    assert result["scenario"]["snapshot"] == metadata["snapshot"]
    assert result["pf_converged"] is True
    assert result["analysis_counts"] == {"buses": 166, "lines": 156, "transformers": 27}
    assert result["excluded"]["bus_count"] == 7
    assert result["excluded"]["line_count"] == 3
    assert result["voltage_pu"]["min"] == summary["voltage_min_pu"]
    assert result["voltage_pu"]["max"] == summary["voltage_max_pu"]
    assert result["line_max_loading_percent"] == summary["line_max_loading_percent"]
    assert result["assumed_screening_violations"] == summary["screening_violations"]
    assert result["voltage_violation_bus_ids"] == []
    assert result["synthetic_equipment"]["generator_count"] == 45
    assert result["model_reliability_status"] == "MODEL_DATA_REQUIRES_REVIEW"
    assert any("45곳이 아닙니다" in caveat for caveat in result["caveats"])


@pytest.mark.parametrize(
    ("metric", "collection", "field", "id_key", "descending"),
    [
        ("bus_voltage_high", "buses", "v_mag_pu", "bus_id", True),
        ("bus_voltage_low", "buses", "v_mag_pu", "bus_id", False),
        ("line_loading_high", "lines", "loading_percent", "line_id", True),
        ("transformer_loading_high", "transformers", "loading_percent", "transformer_id", True),
    ],
)
def test_ranking_matches_independent_sort(preview, metric, collection, field, id_key, descending):
    changed = copy.deepcopy(preview)
    for index, item in enumerate(changed[collection]):
        item[field] = 0.95 + ((index * 37) % 101) / 1000 if field == "v_mag_pu" else ((index * 53) % 97) / 3
    result = pf_ranking(changed, metric, 5)
    expected = sorted(changed[collection], key=lambda item: item[field], reverse=descending)[:5]
    assert [row["value"] for row in result["ranking"]] == [item[field] for item in expected]
    assert [row["rank"] for row in result["ranking"]] == [1, 2, 3, 4, 5]
    assert all(row[id_key] for row in result["ranking"])
    excluded = set(preview["metadata"]["excluded_bus_ids"]) | set(preview["metadata"]["excluded_line_ids"])
    assert not {row[id_key] for row in result["ranking"]} & excluded


def test_ranking_caps_top_n_and_rejects_unknown_metric(preview):
    assert pf_ranking(preview, "line_loading_high", 50)["top_n"] == MAX_TOP_N
    assert len(pf_ranking(preview, "line_loading_high", 50)["ranking"]) == MAX_TOP_N
    assert pf_ranking(preview, "line_loading_high", 0)["top_n"] == 1
    assert pf_ranking(preview, "generator_output_high", 5)["status"] == "INVALID_METRIC"


def test_tools_return_data_unavailable_when_preview_missing(tmp_path, assets):
    map_file, _ = assets
    tools = tools_by_name(map_file, tmp_path / "missing.json")
    for name, args in (
        ("get_mongolia_pf_summary", {}),
        ("get_mongolia_bus_pf", {"bus_id": "137"}),
        ("get_mongolia_pf_ranking", {"metric": "bus_voltage_low"}),
    ):
        result = tools[name].invoke(args)
        assert result["status"] == DATA_UNAVAILABLE
        assert "v_mag_pu" not in result and "ranking" not in result


@pytest.mark.parametrize(
    "mutate",
    [
        lambda preview: preview["metadata"].__setitem__("scenario_id", "other_scenario"),
        lambda preview: preview["metadata"].__setitem__("map_json_sha256", "0" * 64),
        lambda preview: preview["summary"].__setitem__("pf_converged", False),
        lambda preview: preview["buses"][0].__setitem__("v_mag_pu", "NaN"),
    ],
)
def test_tools_block_invalid_preview(assets, mutate):
    map_file, preview_file = assets
    payload = json.loads(preview_file.read_text(encoding="utf-8"))
    mutate(payload)
    preview_file.write_text(json.dumps(payload), encoding="utf-8")
    result = tools_by_name(map_file, preview_file)["get_mongolia_bus_pf"].invoke({"bus_id": "137"})
    assert result["status"] == DATA_UNAVAILABLE


def test_tools_via_langchain_invoke(assets):
    tools = tools_by_name(*assets)
    assert tools["get_mongolia_bus_pf"].invoke({"bus_id": "137"})["status"] == "ANALYZED"
    ranking = tools["get_mongolia_pf_ranking"].invoke({"metric": "line_loading_high", "top_n": 3})
    assert len(ranking["ranking"]) == 3


@pytest.mark.parametrize(
    "message",
    [
        "발전기 사고 분석해 줘.",
        "이 발전기의 출력을 20MW 늘려 줘.",
        "전압 문제 해결을 위한 Redispatch를 계산해 줘.",
        "OPF 돌려줘",
        "발전기 GEN-1 탈락 분석해 줘.",
    ],
)
def test_unsupported_calculation_requests_are_detected(message):
    labels = detect_unsupported_request(message)
    assert labels
    answer = unsupported_request_answer(labels)
    assert "지원하지 않습니다" in answer
    assert "계산을 수행하지 않았습니다" in answer


@pytest.mark.parametrize(
    "message",
    [
        "Bus 137 전압은?",
        "부하율이 가장 높은 선로 5개 알려줘.",
        "몽골 합성 PF 결과에서 전압 위반 모선이 있나?",
        "선로 255335646-1_0의 조류 알려줘.",
        "transf_10_0 변압기 부하율은?",
        "751261406-1_0 선로 N-1 후 민감도 Top 5",
        "선로 사고 후 발전기 주입 민감도",
        "이어서 민감도 후보 5개",
        "전압이 가장 낮은 모선 3개는?",
    ],
)
def test_supported_questions_are_not_blocked(message):
    assert detect_unsupported_request(message) == []


def test_system_prompt_keeps_synthetic_data_warnings():
    for phrase in (
        "Tool을 호출",
        "만들어 내지 않는다",
        "실제 몽골 운전계통",
        "운영 적합성",
        "2013-01-01",
        "45대",
        "27건",
        "전기정수",
        "Redispatch, OPF, 발전기 사고·탈락",
        "추가 데이터가 필요",
        "한국어",
        "EXCLUDED_FROM_ANALYSIS",
    ):
        assert phrase in MONGOLIA_SYSTEM_PROMPT


def test_catalog_chat_capability_is_separate_from_analysis(tmp_path, assets):
    map_file, preview_file = assets
    ready = {item["id"]: item for item in list_grids(map_file, preview_file)}
    assert ready["kr-kpg193"]["chat_available"] is True
    assert ready["kr-kpg193"]["analysis_available"] is True
    assert ready[MONGOLIA_GRID_ID]["chat_available"] is True
    assert ready[MONGOLIA_GRID_ID]["chat_mode"] == "mongolia_analysis"
    assert ready[MONGOLIA_GRID_ID]["analysis_available"] is False
    missing = {item["id"]: item for item in list_grids(map_file, tmp_path / "missing.json")}
    assert missing[MONGOLIA_GRID_ID]["chat_available"] is False
