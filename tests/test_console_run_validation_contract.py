"""PH4-02C-2b static scope of the console run validators (no browser).

Behavior is executed in Chrome on real B3-2 responses (tests/test_console_run_validation_browser.py).
These checks pin what the validators may touch and that C-2b does not wire them into the console yet.
"""
from __future__ import annotations

import re
from pathlib import Path

from test_console_commit_contract import body, code

CONSOLE = Path(__file__).resolve().parents[1] / "ui" / "console"
ANALYSIS = (CONSOLE / "api-analysis.js").read_text(encoding="utf-8")
BRIDGE = (CONSOLE / "api-bridge.js").read_text(encoding="utf-8")
CHAT = (CONSOLE / "api-chat.js").read_text(encoding="utf-8")


def test_run_validators_touch_nothing_but_their_arguments():
    for name in ("runResponse", "agentReceipt", "receiptTimes"):
        fn = body(ANALYSIS, name)
        assert not re.search(r"\b(document|window|fetch|localStorage|sessionStorage|XMLHttpRequest|setTimeout|"
                             r"state|view|apiChat|apiMap|KemsApiChat|KemsApiMap)\b", fn), name
    run = body(ANALYSIS, "runResponse")
    # No assignment into the response, the snapshot or any part of them.
    assert not re.search(r"\b(?:payload|snapshot|context|analysis|update|updates|results|r)\.[\w.\[\]]+\s*(?:\+=|-=|=(?!=))", run)
    # Returned payloads are copies, never the caller's objects.
    assert "deepFreeze(structuredClone(updates[i].result))" in run


def test_run_response_reuses_the_receipt_and_payload_checks_and_names_expiry_last():
    run = body(ANALYSIS, "runResponse")
    for reused in ("agentReceipt(", "securityCommon(", "sensitivityCommon(", "sameJson(payload.analysis_context, context)"):
        assert reused in run
    assert run.rindex("reject(") < run.index("RECEIPT_EXPIRED")


def test_manual_and_agent_receipts_share_the_core_checks():
    manual, agent = body(ANALYSIS, "manualReceipt"), body(ANALYSIS, "agentReceipt")
    for shared in ("receiptShape(r)", "assessmentMatches(r, payload)", "receiptTimes(r)"):
        assert shared in manual and shared in agent
    assert "r.producer !== 'manual'" in manual and "r.tool_call_id !== null" in manual
    assert "r.producer !== 'agent_tool'" in agent and "typeof r.tool_call_id !== 'string' || !r.tool_call_id" in agent


def test_validators_are_exported_and_wired_only_through_apply_run():
    assert "manualReceipt, referenceResponse, agentReceipt, runResponse," in ANALYSIS
    # C-2c replaced the C-2b "not wired yet" guard: the chat never validates, the bridge only in applyRun.
    assert not re.search(r"\b(runResponse|agentReceipt)\b", code(CHAT))
    bridge = code(BRIDGE)
    assert len(re.findall(r"\brunResponse\b", bridge)) == 1 and "agentReceipt" not in bridge
    apply = body(BRIDGE, "applyRun")
    order = [apply.index(token) for token in ("KemsApiAnalysis.runResponse(", "runCurrent(snapshot)", "prepareAnalysisResults(view, run.items)",
                                               "supersedeAnalysisRequests(a)", "commitAnalysisResults(view, prepared, {epoch: bumpRevision")]
    assert order == sorted(order)
    assert not re.search(r"\b(advanceAnalysis|invalidateAnalysisContext)\b", apply)
