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


def test_analysis_buttons_stay_tied_to_analysis_capability():
    body = function_body("updateGridCapabilities")
    assert 'for (const id of ["analyze-button", "generator-analyze-button"])' in body
    assert "document.getElementById(id).disabled = !available;" in body
    assert 'document.getElementById("chat-send").disabled = !chatAvailable || chatBusy;' in body
    assert 'document.getElementById("chat-message").disabled = !chatAvailable;' in body


def test_grid_switch_resets_chat_session_and_log():
    reset = function_body("resetChatForGrid")
    assert "sessionStorage.removeItem(SESSION_STORAGE_KEY)" in reset
    assert 'getElementById("chat-log").replaceChildren()' in reset
    assert "합성 PF 조회 전용" in reset
    assert "resetChatForGrid();" in INDEX_HTML.split('select.addEventListener("change"', 1)[1]
