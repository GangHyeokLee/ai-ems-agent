"""Static checks that the shared AI Assistant UI routes chat by grid without enabling analysis."""
from pathlib import Path

INDEX_HTML = (Path(__file__).resolve().parents[1] / "ui" / "index.html").read_text(encoding="utf-8")


def function_body(name):
    return INDEX_HTML.split(f"function {name}(", 1)[1].split("\n    function ", 1)[0]


def test_chat_uses_chat_capability_and_grid_specific_endpoint():
    body = function_body("sendChatMessage")
    assert "if (!gridSupportsChat()) return;" in body
    assert "fetch(chatEndpoint(requestGridId)" in body
    assert 'fetch("/api/chat"' not in body
    endpoint = function_body("chatEndpoint")
    assert '"/api/chat"' in endpoint
    assert "/api/grids/${encodeURIComponent(gridId)}/chat" in endpoint


def test_late_or_mongolia_answers_never_apply_kpg_overlays():
    body = function_body("sendChatMessage")
    assert "selectedGridId !== requestGridId" in body
    guard = body.index('if (requestGridId !== "kr-kpg193" || !gridSupportsAnalysis()) return;')
    assert guard < body.index("applySecurityResult(")


def test_analysis_buttons_follow_their_own_capabilities():
    body = function_body("updateGridCapabilities")
    assert 'document.getElementById("analyze-button").disabled = !securityAvailable;' in body
    assert 'document.getElementById("generator-analyze-button").disabled = !generatorAvailable;' in body
    assert "entry?.security_analysis_available === true" in body
    assert "entry?.generator_contingency_available === true" in body
    assert 'document.getElementById("chat-send").disabled = !chatAvailable || chatBusy;' in body
    assert 'document.getElementById("chat-message").disabled = !chatAvailable;' in body


def test_grid_switch_resets_chat_session_and_log():
    reset = function_body("resetChatForGrid")
    assert "sessionStorage.removeItem(SESSION_STORAGE_KEY)" in reset
    assert 'getElementById("chat-log").replaceChildren()' in reset
    assert "합성 PF 조회 전용" in reset
    assert "resetChatForGrid();" in INDEX_HTML.split('select.addEventListener("change"', 1)[1]


def test_security_analysis_routes_by_grid_and_ignores_late_answers():
    body = function_body("runSecurityAnalysis")
    assert 'if (!gridCapability("security_analysis_available")) return;' in body
    assert '"/api/grids/mn-osm-35k/security-analysis?outage_line_id="' in body
    assert '"/api/security-analysis?outage_line_id="' in body
    assert "selectedGridId !== requestGridId" in body
    assert "if (isMongoliaRequest) applyMongoliaSecurityResult(result);" in body


def test_isolated_bus_highlight_is_scoped_to_visible_analysis_layer():
    assert ":where(#map:not(.analysis-hidden)) .network-bus.isolated-bus" in INDEX_HTML
    assert ":where(#map:not(.analysis-hidden)) .network-line.isolated-line" in INDEX_HTML
    assert "#map.analysis-hidden .network-bus" not in INDEX_HTML
    assert 'bus.classList.remove("isolated-bus")' in function_body("resetSecurityHighlight")


def test_mongolia_result_states_island_pf_not_computed():
    body = function_body("renderMongoliaSecurityResult")
    assert "주 계통만 수렴" in body
    assert "분리된 섬의 PF는 실행하지 않았습니다" in body
    assert "실제 몽골 운영 데이터가 아닙니다" in body


def test_mongolia_sensitivity_routing_and_request_sequence_guards():
    body = function_body("runMongoliaSensitivityAnalysis")
    assert 'gridCapability("sensitivity_analysis_available")' in body
    assert '"/api/grids/mn-osm-35k/sensitivity-analysis?"' in body
    assert "seq !== sensitivityRequestSeq" in body
    assert "latestSecurityResult.island_detected" in body
    assert "if (isMongoliaGrid()) return runMongoliaSensitivityAnalysis();" in function_body("runSensitivityAnalysis")
    security = function_body("runSecurityAnalysis")
    assert "const seq = ++securityRequestSeq;" in security and "seq !== securityRequestSeq" in security
    assert "sensitivityRequestSeq++" in security          # a new Security run discards pending Sensitivity


def test_mongolia_sensitivity_display_rules():
    assert ":where(#map:not(.analysis-hidden)) .network-line.monitored-line" in INDEX_HTML
    apply = function_body("applyMongoliaSensitivityResult")
    assert 'result.analysis_status !== "COMPLETED"' in apply
    assert "실제 발전소 아님" in apply and "재급전 권고가 아닙니다" in apply
    security = function_body("applyMongoliaSecurityResult")
    assert "!result.island_detected" in security and "미평가" in security
