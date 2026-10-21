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
    guard = body.index('if (requestGridId === "mn-osm-35k")')
    assert guard < body.index("applyMongoliaSecurityResult(") < body.index("applySecurityResult(")
    assert "update.result?.grid_id !== requestGridId" in body


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
    for field in ("pf-preview-disclaimer", "pf-preview-status", "pf-preview-provenance", "pf-preview-limit-note",
                  "grid-status-detail", "grid-scope-note"):
        assert f'id="{field}"' in INDEX_HTML.split('id="model-help"', 1)[1].split("</div>", 1)[0], field
    assert 'id="pf-preview-alert" class="grid-alert" role="alert"' in INDEX_HTML
    guides = INDEX_HTML.split("const ASSISTANT_GUIDES", 1)[1].split("};\n", 1)[0]
    for text in ("합성 PF 조회와 선로 N-1 Security·Sensitivity를 지원합니다", "Redispatch·OPF·발전기 사고",
                 "Bus 137 전압은?", "751261406-1_0 선로 N-1 후 민감도 Top 5", "이어서 민감도 후보 5개"):
        assert text in guides, text
    assert 'title="' not in INDEX_HTML.split('id="model-help-trigger"', 1)[1].split(">", 1)[0]   # not a bare title tooltip


def test_no_always_on_notice_and_status_rules():
    """PR #8 review: no permanent synthetic-data banner or normal-status text; abnormal states stay visible."""
    for removed in ('id="pf-preview-notice"', 'id="pf-preview-panel"', "model-notice"):
        assert removed not in INDEX_HTML, removed
    toolbar = INDEX_HTML.split('<div class="grid-toolbar">', 1)[1].split('<div id="grid-capability-note"', 1)[0]
    assert 'id="model-help-trigger"' in toolbar and 'id="grid-status"' in toolbar
    status = function_body("setGridStatus")
    assert 'status.textContent = abnormal ? text : "";' in status and "status.hidden = !abnormal;" in status
    assert 'getElementById("grid-status-detail").textContent = text;' in status
    assert INDEX_HTML.count('getElementById("grid-status").textContent') == 0      # every write goes through setGridStatus
    for abnormal in ('setGridStatus("계통 목록 오류: " + error.message, true);', 'setGridStatus("지도 오류: " + error.message, true);'):
        assert abnormal in INDEX_HTML


def test_help_popover_wraps_korean_by_word():
    css = INDEX_HTML.split(".help-popover { position: fixed;", 1)[1].split("}", 1)[0]
    assert "word-break: keep-all;" in css and "overflow-wrap: break-word;" in css and "width: max-content;" in css
    assert ".help-popover > :not(.help-strong) { contain: inline-size; }" in INDEX_HTML
    assert ".help-popover .help-strong { white-space: nowrap; }" in INDEX_HTML
    assert 'popover.classList.add("wrap-first")' in INDEX_HTML.split("function setupHelp(", 1)[1]


def test_quiet_ui_keeps_warnings_and_moves_guidance_to_help():
    """Always-on guidance/notices removed; errors, non-convergence, islanding and unsupported states kept."""
    for removed in ('id="pf-overlay-note"', 'class="pf-legend-note"', '<p class="result-notice">',
                    "지도에서 발전기 행을 클릭하면 자동으로 입력됩니다.</span>", "몽골 합성 시나리오 검증 사례",
                    '"사고 선로를 입력하고 분석하세요."'):
        assert removed not in INDEX_HTML, removed
    help_block = INDEX_HTML.split('id="model-help"', 1)[1].split("</div>", 1)[0]
    for field in ('id="pf-display-criteria"', 'id="analysis-interpretation"', 'id="equipment-data-note"'):
        assert field in help_block, field
    criteria = function_body("renderDisplayCriteria")
    assert "PoC 시연용 시각화 구간이며 운영사의 공식 경보 기준이 아닙니다." in criteria
    assert "사고 결과 강조가 PF 색상보다 우선 표시됩니다" in criteria
    kept = ("현재 계통은 발전기 탈락 분석을 지원하지 않습니다.", "상정사고 분석이 수렴하지 않아 민감도 분석을 수행할 수 없습니다.",
            "사고 후 PF가 수렴하지 않아 민감도를 계산할 수 없습니다.", "분리 섬 처리 정책이 정해지지 않아 민감도는 미평가입니다",
            "분리 섬 PF 미실행", "사고 후 PF 비수렴 · 위반 판정 불가", "PF 미리보기 오류")
    for text in kept:
        assert text in INDEX_HTML, text


def test_map_tooltips_do_not_repeat_model_origin_disclaimers():
    """Model provenance belongs in ⓘ model info, not each equipment tooltip."""
    assert "pfSourceLabel" not in INDEX_HTML
    assert 'id="pf-preview-disclaimer"' in INDEX_HTML
    for tooltip in ("showBusTooltip", "showLineTooltip", "showTransformerTooltip"):
        body = function_body(tooltip)
        assert 'class="map-tooltip-title"' in body
        assert "pfResultState" in body or "formatPfNumber" in body
