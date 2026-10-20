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
    reset = INDEX_HTML.split("function resetChatForGrid(", 1)[1].split("\n    }\n", 1)[0]
    assert "sessionStorage.removeItem(SESSION_STORAGE_KEY)" in reset
    assert 'getElementById("chat-log").replaceChildren()' in reset
    assert "appendChatMessage" not in reset                  # guide moved to the help popover (Issue #6)
    assert "renderAssistantHelp(" in reset
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
    body = function_body("mongoliaSecurityViewModel")
    assert "주 계통만 수렴" in body
    assert "분리 섬 PF 미실행" in body
    assert "실제 몽골 운영 데이터가 아닙니다" in INDEX_HTML.split("const GRID_RESULT_TEXT", 1)[1].split("};", 1)[0]


def test_sensitivity_routing_and_request_sequence_guards():
    body = function_body("runSensitivityAnalysis")
    assert 'gridCapability("sensitivity_analysis_available")' in body
    assert '"/api/grids/mn-osm-35k/sensitivity-analysis?"' in body and '"/api/sensitivity-analysis?"' in body
    assert "seq !== sensitivityRequestSeq" in body and "selectedGridId !== requestGridId" in body
    assert "latestSecurityView?.sensitivity?.enabled !== true" in body     # island / non-converged gate
    security = function_body("runSecurityAnalysis")
    assert "const seq = ++securityRequestSeq;" in security and "seq !== securityRequestSeq" in security
    assert "sensitivityRequestSeq++" in security          # a new Security run discards pending Sensitivity


def test_mongolia_sensitivity_display_rules():
    assert ":where(#map:not(.analysis-hidden)) .network-line.monitored-line" in INDEX_HTML
    view = function_body("mongoliaSensitivityViewModel")
    assert 'result.analysis_status === "COMPLETED"' in view and "미평가 (계통 분리)" in view
    texts = INDEX_HTML.split("const GRID_RESULT_TEXT", 1)[1].split("};", 1)[0]
    assert "실제 발전소·재급전 권고가 아닙니다" in texts and "실제 발전소·재급전 후보가 아님" in texts
    security = function_body("mongoliaSecurityViewModel")
    assert "sensitivityCapable && assessed && !island" in security and "미평가" in security


def test_input_changes_invalidate_in_flight_results():
    invalidate = function_body("invalidateSecurityResult")
    assert "securityRequestSeq++; sensitivityRequestSeq++;" in invalidate
    assert "resetSecurityHighlight();" in invalidate and "resetSensitivityMarkers();" in invalidate
    assert "latestSecurityView = null;" in invalidate
    monitored = INDEX_HTML.split('getElementById("monitored-line").addEventListener("input"', 1)[1].split("});", 1)[0]
    assert "sensitivityRequestSeq++;" in monitored and "resetSensitivityMarkers();" in monitored


def test_kpg_sensitivity_error_path_checks_request_sequence():
    body = function_body("runSensitivityAnalysis")
    assert body.index("const seq = ++sensitivityRequestSeq;") < body.index("try {")
    assert "if (token === gridSwitchToken && seq === sensitivityRequestSeq) resultElement.textContent = `민감도 분석 실패" in body


# ---------------------------------------------------------------- Issue #5: one UI for every grid

def test_no_grid_specific_result_renderers_remain():
    for removed in ("function renderSecurityResult(", "function renderMongoliaSecurityResult(",
                    "function renderSensitivityMarkers(", "function renderMongoliaSensitivityMarkers(",
                    "function runMongoliaSensitivityAnalysis(", "mn-synthetic-gen", "mn-monitored"):
        assert removed not in INDEX_HTML, removed
    assert INDEX_HTML.count("function renderResultCard(") == 1


def test_grid_adapters_feed_the_same_view_and_renderer():
    for adapter, view in (("applySecurityResult", "kpgSecurityViewModel"),
                          ("applyMongoliaSecurityResult", "mongoliaSecurityViewModel")):
        assert f"applySecurityView({view}(result)" in function_body(adapter)
    for adapter, view in (("applySensitivityResult", "kpgSensitivityViewModel"),
                          ("applyMongoliaSensitivityResult", "mongoliaSensitivityViewModel")):
        assert f"applySensitivityView({view}(result))" in function_body(adapter)
    assert 'renderResultCard(document.getElementById("security-result"), vm);' in function_body("applySecurityView")
    assert 'renderResultCard(document.getElementById("sensitivity-result"), vm);' in function_body("applySensitivityView")
    assert "applySecurityOverlay(vm.overlay);" in function_body("applySecurityView")
    assert "applySensitivityOverlay(vm);" in function_body("applySensitivityView")


def test_result_card_uses_kpg_style_fields_and_collapsible_details():
    card = function_body("renderResultCard")
    for field in ('"Outage"', '"Status"', '"Violations"', '"Violated Equipment"', '"Loading"', '"Limit"',
                  '"Post-contingency"', '"Target Line"', '"Top Candidates"', "민감도: "):
        assert field in card, field
    assert '<details class="result-details"><summary>상세 결과</summary>' in function_body("renderDetails")
    # Missing values are dropped, never invented.
    assert "filter(([, value]) => hasValue(value))" in function_body("detailSection")


def test_monitored_line_selection_is_shared_by_both_grids():
    caps = function_body("updateGridCapabilities")
    assert 'document.getElementById("monitored-line").hidden = !gridCapability("sensitivity_analysis_available");' in caps
    assert 'params.set("monitored_line_id", monitoredLineId)' in function_body("runSensitivityAnalysis")


def test_legends_are_shared_components():
    assert 'id="analysis-legend"' in INDEX_HTML and "#map.analysis-hidden .analysis-legend" in INDEX_HTML
    legend = function_body("renderEquipmentLegend")
    assert "data.powerplants" in legend and "data.generators" in legend
    assert "renderEquipmentLegend(data);" in function_body("drawNetwork")


# ---------------------------------------------------------------- Issue #6: theme + help popovers

def test_theme_tokens_and_entry_points():
    assert ':root[data-theme="dark"]' in INDEX_HTML and "color-scheme: dark" in INDEX_HTML
    head = INDEX_HTML.split("<style>", 1)[0]
    assert 'localStorage.getItem("ai-ems-theme")' in head and "prefers-color-scheme: dark" in head   # before first paint
    for value in ("light", "dark", "system"):
        assert f'<input type="radio" name="theme" value="{value}" />' in INDEX_HTML
    apply = INDEX_HTML.split("function applyTheme(", 1)[1].split("\n    }\n", 1)[0]
    assert 'preference === "system" && themeMedia.matches' in apply and "refreshThemedDataColors();" in apply
    assert 'localStorage.setItem(THEME_STORAGE_KEY, preference)' in INDEX_HTML
    assert 'themeMedia.addEventListener("change"' in INDEX_HTML


def test_data_colours_keep_meaning_and_overlays_not_colour_only():
    assert "function dataColor(hex)" in INDEX_HTML and "DARK_DATA_TINTS" in INDEX_HTML
    for fn in ("voltageColor", "lineLoadingColor", "busVoltageColor", "powerplantColor"):
        assert "dataColor(" in INDEX_HTML.split(f"function {fn}(", 1)[1].split("\n    }\n", 1)[0], fn
    assert ".network-line.violation-line { stroke: #f59e0b !important; stroke-width: 5 !important; stroke-dasharray: 12 4;" in INDEX_HTML
    assert ".network-line.monitored-line { stroke: #db2777 !important; stroke-width: 5 !important; stroke-dasharray: 7 3;" in INDEX_HTML


def test_help_popovers_are_accessible_and_keep_original_text():
    for trigger, popover in (("model-help-trigger", "model-help"), ("assistant-help-trigger", "assistant-help")):
        assert f'id="{trigger}" aria-haspopup="dialog" aria-expanded="false" aria-controls="{popover}"' in INDEX_HTML
        assert f'id="{popover}" class="help-popover" role="dialog"' in INDEX_HTML
        assert f'setupHelp("{trigger}", "{popover}");' in INDEX_HTML
    setup = INDEX_HTML.split("function setupHelp(", 1)[1].split("\n    }\n    document", 1)[0]
    for needle in ('"mouseenter"', '"mouseleave"', '"click"', "window.innerWidth", "maxHeight"):
        assert needle in setup, needle
    assert 'event.key !== "Escape"' in INDEX_HTML
    # original banner fields still filled, now inside the popover; failures visible outside it
    for field in ("pf-preview-disclaimer", "pf-preview-status", "pf-preview-provenance", "pf-preview-limit-note", "grid-scope-note"):
        assert f'id="{field}"' in INDEX_HTML.split('id="model-help"', 1)[1].split("</div>", 1)[0], field
    assert 'id="pf-preview-alert" class="model-alert" role="alert"' in INDEX_HTML
    guides = INDEX_HTML.split("const ASSISTANT_GUIDES", 1)[1].split("};\n", 1)[0]
    for text in ("AI 채팅에서는 N-1 계산을 호출할 수 없습니다", "Redispatch는 지원하지 않습니다.", "Bus 137 전압은?",
                 "부하율이 가장 높은 선로 5개 알려줘.", "몽골 합성 PF 결과에서 전압 위반 모선이 있나?"):
        assert text in guides, text
    assert 'title="' not in INDEX_HTML.split('id="model-help-trigger"', 1)[1].split(">", 1)[0]   # not a bare title tooltip
