"""PH4-02C-2a structural contract of the console analysis state path (no browser).

C-2 will commit a verified AI run from inside its own pending chat request. That commit must be able
to start a new analysis epoch without cancelling the request (and so without retiring its session or
receipts). These checks pin the separation in the console sources; behavior is covered by the Chrome
case ``commit_contract`` in tests/test_console_reference_browser.py.
"""
from __future__ import annotations

import re
from pathlib import Path

CONSOLE = Path(__file__).resolve().parents[1] / "ui" / "console"
BRIDGE = (CONSOLE / "api-bridge.js").read_text(encoding="utf-8")
CHAT = (CONSOLE / "api-chat.js").read_text(encoding="utf-8")


def code(source: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)


def body(source: str, name: str) -> str:
    """The braces-delimited body of `function name(...)` (balanced braces; comments stripped)."""
    text = code(source)
    match = re.search(r"\bfunction %s\(" % re.escape(name), text)
    assert match, name
    start = text.index("{", text.index(")", match.end()))
    depth = 0
    for index in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[start + 1:index]
    raise AssertionError(f"unbalanced body: {name}")


def statements(fragment: str) -> list[str]:
    return [part.strip() for part in fragment.split(";") if part.strip()]


EPOCH_WRITE = re.compile(r"\b(?:analysisRevision|viewId)\s*(?:\+=|-=|=(?!=))")
STATE_WRITE = re.compile(r"\b(?:view|a|state)\.[\w.\[\]']+\s*(?:\+=|-=|=(?!=))")


def test_bump_revision_is_the_only_epoch_writer_and_cancels_nothing():
    bump = body(BRIDGE, "bumpRevision")
    assert "view.analysisRevision" in bump and "view.viewId = KemsApiChat.uuid()" in bump
    assert not re.search(r"apiChat|invalidate|retire|receipts|notices|sessionRetired|advanceAnalysis", bump)
    # Outside bumpRevision no statement writes the epoch (openView initializes it in an object literal).
    assert len(EPOCH_WRITE.findall(bump)) == 3
    assert len(EPOCH_WRITE.findall(code(BRIDGE))) == 3


def test_advance_analysis_is_cancel_then_bump():
    assert statements(body(BRIDGE, "advanceAnalysis")) == ["apiChat.invalidateAnalysisContext()", "bumpRevision(view)"]


def test_session_retirement_bumps_without_recursion():
    retired = body(BRIDGE, "sessionRetired")
    assert "bumpRevision(view)" in retired
    assert not re.search(r"advanceAnalysis|invalidateAnalysisContext", retired)


def test_prepare_validates_into_view_models_and_writes_nothing():
    prepare = body(BRIDGE, "prepareAnalysisResults")
    assert "KemsApiAnalysis.security(" in prepare and "KemsApiAnalysis.sensitivity(" in prepare
    assert not STATE_WRITE.search(prepare)
    assert not re.search(r"\b(await|fetch|fetchJson|analysisChanged|render|paintMap|bumpRevision|advanceAnalysis|storeReceipt)\b", prepare)


def test_commit_is_synchronous_and_orders_results_epoch_receipts_mode():
    commit = body(BRIDGE, "commitAnalysisResults")
    assert not re.search(r"\b(await|fetch|fetchJson|KemsApiAnalysis|advanceAnalysis|bumpRevision|invalidateAnalysisContext)\b", commit)
    order = [commit.index(token) for token in ("a.security =", "a.sensitivity =", "epoch(view)", "storeReceipts()", "view.mode = 'analysis'")]
    assert order == sorted(order)
    # A ready view model is assigned nowhere else.
    assert len(re.findall(r"status: 'ready', vm", code(BRIDGE))) == 2
    assert len(re.findall(r"status: 'ready', vm", commit)) == 2


def test_manual_results_keep_the_c1_epoch_and_receipt_path():
    for name, kind in (("runSecurity", "security"), ("runSensitivity", "sensitivity")):
        run = body(BRIDGE, name)
        assert f"prepareAnalysisResults(view, [{{kind: '{kind}', payload, line" in run
        assert ("commitAnalysisResults(view, prepared, {epoch: advanceAnalysis,\n"
                f"        storeReceipts: () => acceptManualReceipt(view, '{kind}', payload, optIn)}})") in run
        assert run.index("if (!current()) return;\n    let prepared") < run.index("commitAnalysisResults(")


def test_receipt_slots_have_one_writer():
    indexed = re.findall(r"view\.reference\.receipts\[\w+\]\s*=(?!=)", code(BRIDGE))
    assert indexed and len(indexed) == len(re.findall(r"view\.reference\.receipts\[\w+\]\s*=(?!=)", body(BRIDGE, "storeReceipt")))
    accept = body(BRIDGE, "acceptManualReceipt")
    assert "view.reference" not in accept and accept.count("storeReceipt(view, kind,") == 5


def test_clear_supersedes_every_request_of_the_view():
    assert statements(body(BRIDGE, "supersedeAnalysisRequests")) == ["abortAnalysis(a)", "a.seq += 1", "a.sensSeq += 1"]
    assert "supersedeAnalysisRequests(a)" in body(BRIDGE, "clearAnalysis")


def test_chat_tracks_one_pending_v1_snapshot_and_never_offers_run():
    chat = code(CHAT)
    assert "request.reference" not in chat and "pending?.reference" not in chat
    assert "request.analysis = hooks.captureReference()" in chat
    assert chat.count("state?.pending?.analysis") == 2  # selector lock and invalidation
    assert "analysis_context: request.analysis.context" in chat
    # C-2a adds no run mode: no 'run' option and no run context is ever built.
    for source in (chat, code(BRIDGE)):
        assert not re.search(r"""['"]run['"]""", source)
    assert "mode: 'reference'" in code(BRIDGE)
