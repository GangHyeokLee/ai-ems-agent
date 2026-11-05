"""PH4-02C-2c static contract of the console AI run path (no browser).

Behavior runs in Chrome on the real FastAPI/registry/PyPowSyBl paths with a scripted model
(tests/test_console_run_browser.py). These checks pin the structure the review relies on.
"""
from __future__ import annotations

import re
from pathlib import Path

from test_console_commit_contract import STATE_WRITE, body, code

CONSOLE = Path(__file__).resolve().parents[1] / "ui" / "console"
BRIDGE = (CONSOLE / "api-bridge.js").read_text(encoding="utf-8")
CHAT = (CONSOLE / "api-chat.js").read_text(encoding="utf-8")
ANALYSIS = (CONSOLE / "api-analysis.js").read_text(encoding="utf-8")


def test_apply_run_validates_everything_before_the_first_state_write():
    apply = body(BRIDGE, "applyRun")
    head = apply[:apply.index("supersedeAnalysisRequests(a)")]
    # Response, currency and map view models are settled before anything on screen changes.
    assert not STATE_WRITE.search(head)
    assert not re.search(r"\b(await|fetch|fetchJson|analysisChanged|render|storeReceipt|bumpRevision)\b", head)
    tail = apply[apply.index("supersedeAnalysisRequests(a)"):]
    assert "origin: 'run'" in tail and tail.rstrip().endswith("return Object.freeze({applied: true, label: runLabel(run)});")
    assert tail.count("analysisChanged()") == 1


def test_apply_run_leaves_failed_and_not_run_responses_untouched():
    apply = body(BRIDGE, "applyRun")
    early = apply[:apply.index("runCurrent(snapshot)")]
    assert "run.status === 'failed' || run.status === 'not_run'" in early and "applied: false" in early


def test_a_new_outage_drops_the_previous_sensitivity_and_receipts():
    apply = body(BRIDGE, "applyRun")
    block = apply[apply.index("if (newOutage)"):apply.index("commitAnalysisResults(")]
    for statement in ("sensitivity: {status: 'idle'}", "view.selectedOutageLineId = c.outage_line_id",
                      "view.reference.receipts = {security: null, sensitivity: null}", "view.reference.notices = {}"):
        assert statement in block


def test_sensitivity_alone_requires_the_n1_result_on_screen():
    assert "a.security.status !== 'ready' || !a.line" in body(BRIDGE, "runOption")
    assert "(c.operation !== 'sensitivity' || (a.security.status === 'ready' && a.line === c.outage_line_id))" in body(BRIDGE, "runCurrent")


def test_manual_actions_wait_for_a_pending_run():
    for name in ("runSecurity", "runSensitivity", "setMonitored"):
        fn = body(BRIDGE, name)
        assert fn.index("runPending(view)") < fn.index("advanceAnalysis(view)"), name
    assert "locked: runPending(view) ? runLock(pendingRun(view)) : null" in body(BRIDGE, "analysisInfo")
    for control in ("disabled: Boolean(info.locked) || (mine && sec.status === 'loading')", "'aria-label': '감시 선로', disabled: Boolean(info.locked)",
                    "disabled: Boolean(info.locked) || sens.status === 'loading'", "disabled: Boolean(info.locked), title: info.locked || null, onclick: () => act.setMonitored(alt.line)"):
        assert control in ANALYSIS, control


def test_run_context_is_frozen_with_null_references():
    capture = body(BRIDGE, "captureRun")
    assert "references: Object.freeze({security: null, sensitivity: null})" in capture
    assert "view_id: view.viewId, revision: view.analysisRevision" in capture


def test_rejected_or_expired_run_responses_keep_the_session():
    chat = code(CHAT)
    branch = chat[chat.index("if (request.analysis?.context.mode === 'run') {"):chat.index("if (request.analysis) {  ")]
    assert "hooks.applyRun(payload, request)" in branch and "retire(" not in branch
    assert "RECEIPT_EXPIRED" in branch
    # Transport failures and timeouts keep the existing C-1 policy (the shared catch below retires).
    assert "retire(state, `${description} 다음 질문은 새 서버 대화로 시작합니다.`)" in chat
