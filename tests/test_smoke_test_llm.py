"""Unit and regression tests for scripts/smoke_test_llm.py.

Verifies:
- Safe import with zero side-effects
- Secret masking and redaction
- Dry run mode (no LLM, no server, no calculation)
- In-memory mock mode passing all 10 scenarios
- Detection of guard rejections and line mismatch
- Direct completion vs initial revision shift handling
- CLI parsing and JSON report generation
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.smoke_test_llm import (
    DEFAULT_MODEL,
    SAMPLE_OUTAGE_LINE,
    build_cli_parser,
    check_prerequisites,
    mask_secret,
    resolve_configuration,
    run_smoke_test,
    validate_execute_configuration,
)


def test_import_has_no_side_effects():
    """Importing smoke_test_llm must not start servers, call LLM, or compute power flow."""
    import scripts.smoke_test_llm as mod
    assert hasattr(mod, "run_smoke_test")
    assert hasattr(mod, "main")


def test_secret_masking():
    """Secrets must never be printed or logged in raw form."""
    assert mask_secret(None) == "미설정 (None)"
    assert mask_secret("") == "미설정 (None)"
    assert mask_secret("   ") == "미설정 (None)"

    token_64 = "a" * 64
    masked_64 = mask_secret(token_64)
    assert token_64 not in masked_64
    assert "64-hex token" in masked_64

    generic_secret = "my-super-secret-password-12345"
    masked_gen = mask_secret(generic_secret)
    assert generic_secret not in masked_gen
    assert "[보호됨]" in masked_gen


def test_configuration_resolution(monkeypatch):
    """Configuration resolution prioritizes CLI > environ > defaults and masks secrets."""
    monkeypatch.delenv("AI_EMS_MODEL", raising=False)
    monkeypatch.delenv("AI_EMS_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AI_EMS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AI_EMS_CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("AI_EMS_CF_ACCESS_CLIENT_SECRET", raising=False)

    parser = build_cli_parser()
    args = parser.parse_args([])
    config = resolve_configuration(ROOT, args)

    assert config["model_name"] == DEFAULT_MODEL
    assert config["base_url"] == ""
    assert config["auth_type"] == "None"

    # CLI override
    args_override = parser.parse_args(["--model", "custom-model:latest", "--base-url", "https://ollama.local:8443"])
    config_override = resolve_configuration(ROOT, args_override)
    assert config_override["model_name"] == "custom-model:latest"
    assert config_override["base_url"] == "https://ollama.local:8443"


def test_prerequisites_check(monkeypatch):
    """Validates prerequisite checks on existing project files."""
    monkeypatch.setenv("AI_EMS_LLM_BASE_URL", "https://ollama.example.invalid:8443")
    monkeypatch.delenv("AI_EMS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AI_EMS_CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("AI_EMS_CF_ACCESS_CLIENT_SECRET", raising=False)
    parser = build_cli_parser()
    config = resolve_configuration(ROOT, parser.parse_args([]))
    prereqs = check_prerequisites(config)

    assert len(prereqs) == 4
    for name, ok, desc in prereqs:
        assert ok is True, f"Prerequisite failed: {name} ({desc})"


def test_execute_configuration_requires_explicit_secure_origin():
    for base_url in (
        "",
        "http://127.0.0.1:11434",
        "https://ollama.example.invalid/api",
        "https://ollama.example.invalid/?token=not-for-logs",
        "https://user@ollama.example.invalid:8443",
    ):
        with pytest.raises(ValueError):
            validate_execute_configuration(base_url, {})

    validate_execute_configuration("https://ollama.example.invalid:8443", {})


def test_execute_configuration_rejects_incomplete_authentication():
    with pytest.raises(ValueError):
        validate_execute_configuration(
            "https://ollama.example.invalid:8443",
            {"AI_EMS_CF_ACCESS_CLIENT_ID": "configured-id"},
        )


def test_cli_blocks_execute_without_url(monkeypatch, capsys):
    import scripts.smoke_test_llm as smoke_test_llm

    monkeypatch.delenv("AI_EMS_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AI_EMS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AI_EMS_CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("AI_EMS_CF_ACCESS_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(sys, "argv", ["smoke_test_llm.py", "--execute"])
    monkeypatch.setattr(
        smoke_test_llm,
        "run_smoke_test",
        lambda **kwargs: pytest.fail("Unsafe execute configuration must stop before running"),
    )

    assert smoke_test_llm.main() == 2
    assert "명시해야 합니다" in capsys.readouterr().err


def test_execute_stops_after_failed_connection_probe(monkeypatch):
    monkeypatch.delenv("AI_EMS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AI_EMS_CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("AI_EMS_CF_ACCESS_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(
        "scripts.smoke_test_llm.probe_llm_connection",
        lambda *args, **kwargs: {
            "status": "FAIL",
            "code": "unreachable",
            "detail": "simulated connection failure",
            "latency_ms": 0.0,
            "models": [],
        },
    )
    monkeypatch.setattr(
        "scripts.smoke_test_llm.probe_tool_calling_support",
        lambda *args, **kwargs: pytest.fail("Tool Calling must not run after connection failure"),
    )

    report = run_smoke_test(
        mode="execute",
        base_url="https://ollama.example.invalid:8443",
        root=ROOT,
    )

    assert report.overall_status == "FAIL"
    assert report.summary == {"pass": 0, "fail": 1, "warn": 0, "skip": 9}
    assert report.scenarios[0].status == "FAIL"
    assert all(s.status == "SKIP" for s in report.scenarios[1:])


def test_dry_run_mode_does_not_execute(monkeypatch):
    """Dry run mode registers 10 planned scenarios as SKIP and executes zero calculations."""
    monkeypatch.delenv("AI_EMS_LLM_BASE_URL", raising=False)
    report = run_smoke_test(mode="dry-run", root=ROOT)

    assert report.mode == "dry-run"
    assert report.overall_status in ("DRY_RUN", "WARN")
    assert len(report.scenarios) == 10
    assert all(s.status == "SKIP" for s in report.scenarios)
    assert report.summary["skip"] == 10
    assert report.summary["pass"] == 0
    assert report.summary["fail"] == 0

    # Ensure formatted text output works
    formatted = report.format_console()
    assert "Dry Run" in formatted or "DRY-RUN" in formatted
    assert "LLM 연결 정상 여부" in formatted


def test_mock_mode_all_10_scenarios_pass():
    """Mock mode verifies that the full 10-step pipeline completes with PASS."""
    report = run_smoke_test(mode="mock", mock=True, root=ROOT)

    assert report.mode == "mock"
    assert report.overall_status == "PASS"
    assert len(report.scenarios) == 10
    assert report.summary["pass"] == 10
    assert report.summary["fail"] == 0

    scenarios_by_id = {s.scenario_id: s for s in report.scenarios}

    # Scenario 1: LLM Connection
    assert scenarios_by_id[1].status == "PASS"

    # Scenario 2: Tool calling
    assert scenarios_by_id[2].status == "PASS"

    # Scenario 3: Security run HTTP 200
    assert scenarios_by_id[3].status == "PASS"

    # Scenario 4: Actual tool executed
    assert scenarios_by_id[4].status == "PASS"

    # Scenario 5: Line ID compliance
    assert scenarios_by_id[5].status == "PASS"

    # Scenario 6: Ledger evidence verified
    assert scenarios_by_id[6].status == "PASS"

    # Scenario 7: Receipt issued
    assert scenarios_by_id[7].status == "PASS"
    assert scenarios_by_id[7].metrics.get("receipt_id") is not None

    # Scenario 8: Initial AC revision handling
    assert scenarios_by_id[8].status == "PASS"

    # Scenario 9: Subsequent request normal completion
    assert scenarios_by_id[9].status == "PASS"

    # Scenario 10: Timing and budget
    assert scenarios_by_id[10].status == "PASS"
    assert scenarios_by_id[10].metrics.get("headroom_s", 0) > 0


def test_mock_mode_detects_guard_rejection_on_wrong_line():
    """When a model generates an incorrect line ID, Guard properly rejects it."""
    report = run_smoke_test(mode="mock", mock=True, mock_bad_line=True, root=ROOT)

    assert report.overall_status == "FAIL"
    scenarios_by_id = {s.scenario_id: s for s in report.scenarios}

    # Scenario 5 should fail due to OUTAGE_LINE_MISMATCH
    assert scenarios_by_id[5].status == "FAIL"
    assert "Guard 거부" in scenarios_by_id[5].detail


def test_json_export_and_file_output(tmp_path):
    """JSON output and file saving generate valid structured data."""
    report = run_smoke_test(mode="mock", mock=True, root=ROOT)
    data = report.to_dict()

    assert "smoke_test_report" in data
    rep = data["smoke_test_report"]
    assert rep["mode"] == "mock"
    assert len(rep["scenarios"]) == 10
    assert rep["overall_status"] == "PASS"

    out_file = tmp_path / "smoke_report.json"
    out_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    loaded = json.loads(out_file.read_text(encoding="utf-8"))
    assert loaded["smoke_test_report"]["overall_status"] == "PASS"


# --- PH4-02-INFRA-03: fail-closed hardening (no real LLM, no external connection) ---------------

import copy
import dataclasses
import os
import socket
import uuid

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import scripts.smoke_test_llm as smoke
from scripts.smoke_test_llm import (
    AUTH_KEYS,
    EVIDENCE_LIMITS,
    ISOLATED_APP_ATTRIBUTES,
    agent_auth_matches,
    display_url,
    isolated_app_environment,
    judge_initial_revision,
    judge_tool_execution,
    model_listed,
    verify_published_evidence,
    verify_receipts,
)

HOST = "ollama.example.invalid"
BASE = f"https://{HOST}:8443"
MODEL = "smoke-model:1b"
TOKEN = "0123456789abcdef" * 4  # Caddy format (64 lowercase hex); test-only value
CASE = ROOT / "data" / "KPG193_ver2_0_powsybl_full.mat"
REV_A, REV_B = "a" * 64, "b" * 64


@pytest.fixture
def clean_llm_env(monkeypatch):
    """Hermetic settings: nothing from the developer's shell or an import-time .env load leaks in."""
    for key in (*AUTH_KEYS, "OLLAMA_API_KEY", "AI_EMS_LLM_BASE_URL", "AI_EMS_MODEL", "AI_EMS_SINGLE_WORKER",
                "AI_EMS_CASE_FILE", "AI_EMS_RUN_LLM_CONNECT_TIMEOUT", "AI_EMS_RUN_LLM_READ_TIMEOUT",
                "AI_EMS_RUN_DEADLINE_SECONDS"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def no_network(monkeypatch):
    """Any real socket connect or HTTP transport call is recorded and refused."""
    attempts = []

    def blocked(*args, **kwargs):
        attempts.append("socket")
        raise ConnectionRefusedError("external connection blocked by test")

    def blocked_http(self, request):
        attempts.append(f"{request.method} {request.url.host}")
        raise httpx.ConnectError("external HTTP blocked by test")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked_http)
    return attempts


def chat_reply(message: dict) -> httpx.Response:
    """Ollama /api/chat streaming reply (NDJSON) carrying one assistant message."""
    lines = [
        {"model": MODEL, "created_at": "2026-11-05T00:00:00Z", "message": {"role": "assistant", "content": "", **message}, "done": False},
        {"model": MODEL, "created_at": "2026-11-05T00:00:00Z", "message": {"role": "assistant", "content": ""},
         "done": True, "done_reason": "stop"},
    ]
    return httpx.Response(200, content="\n".join(json.dumps(line) for line in lines).encode())


def tool_reply(*calls: tuple[str, dict]) -> httpx.Response:
    return chat_reply({"tool_calls": [{"function": {"name": name, "arguments": args}} for name, args in calls]})


class FakeCaddy:
    """In-memory stand-in for Tailscale HTTPS -> Caddy 18080 -> Ollama (README contract).

    Only GET /api/tags and POST /api/chat; a missing or wrong Bearer gets 401 before any upstream
    work. Records (method, path, authorized) only, never header values.
    """

    def __init__(self, monkeypatch, *, models=(MODEL,), chat=None):
        self.models, self.requests = list(models), []
        self.chat = chat or (lambda body: tool_reply(("line_contingency", {"outage_line_id": SAMPLE_OUTAGE_LINE})))
        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda transport, request: self.handle(request))

    def handle(self, request):
        authorized = request.headers.get("authorization") == f"Bearer {TOKEN}"
        route = (request.method, request.url.path)
        self.requests.append((*route, authorized))
        if request.url.host != HOST:
            return httpx.Response(502)
        if route not in {("GET", "/api/tags"), ("POST", "/api/chat")}:
            return httpx.Response(403)
        if not authorized:
            return httpx.Response(401)
        if route == ("GET", "/api/tags"):
            return httpx.Response(200, json={"models": [{"name": name, "model": name} for name in self.models]})
        return self.chat(json.loads(request.content))

    def chats(self):
        return [entry for entry in self.requests if entry[1] == "/api/chat"]


def env_root(tmp_path, text: str) -> Path:
    """A project root whose .env alone holds the settings (the process environment does not)."""
    (tmp_path / ".env").write_text(f"AI_EMS_CASE_FILE={CASE}\n{text}", encoding="utf-8")
    return tmp_path


def by_id(report):
    return {scenario.scenario_id: scenario for scenario in report.scenarios}


def statuses(report):
    return {scenario.scenario_id: scenario.status for scenario in report.scenarios}


@pytest.fixture
def no_kpg_load(monkeypatch):
    import ai_ems.network as network_module

    monkeypatch.setattr(network_module, "load_identified_network",
                        lambda *args, **kwargs: pytest.fail("KPG must not be loaded after a failed preflight"))


# 1. Dry Run --------------------------------------------------------------------------------

def test_dry_run_cli_and_function_make_no_external_connection(monkeypatch, clean_llm_env, no_network, capsys):
    monkeypatch.setenv("AI_EMS_LLM_BASE_URL", BASE)
    monkeypatch.setenv("AI_EMS_LLM_API_KEY", TOKEN)
    monkeypatch.setattr(sys, "argv", ["smoke_test_llm.py", "--json"])
    assert smoke.main() == 0
    report = run_smoke_test(mode="dry-run", root=ROOT)
    assert report.mode == "dry-run" and all(s.status == "SKIP" for s in report.scenarios)
    assert no_network == []
    assert TOKEN not in capsys.readouterr().out


# 2·3. Mock --------------------------------------------------------------------------------

def test_mock_runs_ten_scenarios_without_any_external_connection(clean_llm_env, no_network):
    report = run_smoke_test(mode="mock", mock=True, root=ROOT)
    assert report.overall_status == "PASS" and statuses(report) == {sid: "PASS" for sid in range(1, 11)}
    assert no_network == []
    scenarios = by_id(report)
    # Simulated steps are labelled; the run used the scripted LLM with a real local calculation.
    assert scenarios[1].metrics["simulated"] and scenarios[2].metrics["simulated"]
    assert scenarios[1].detail.startswith("[MOCK]") and scenarios[2].detail.startswith("[MOCK]")
    assert scenarios[3].metrics["source"] == "mock: scripted LLM + local PyPowSyBl"
    assert any("실제 LLM Smoke Test 결과가 아님" in note for note in report.notes)
    assert report.target_base_url.startswith("(mock") and "notes" in report.to_dict()["smoke_test_report"]
    # A freshly loaded KPG: the first AC changed the model and the run was refused (fail-closed).
    assert scenarios[8].metrics["revision_changed"] is True and "fail-closed" in scenarios[8].detail


def test_function_call_mock_is_safe_even_with_a_configured_llm(monkeypatch, clean_llm_env, no_network):
    monkeypatch.setenv("AI_EMS_LLM_BASE_URL", BASE)
    monkeypatch.setenv("AI_EMS_LLM_API_KEY", TOKEN)
    monkeypatch.setattr(smoke, "probe_llm_connection", lambda *a, **k: pytest.fail("mock must not probe the LLM"))
    report = run_smoke_test(mode="mock", root=ROOT)  # no mock=True
    assert report.mode == "mock" and report.overall_status == "PASS"
    assert no_network == [] and TOKEN not in json.dumps(report.to_dict(), ensure_ascii=False)


@pytest.mark.parametrize("kwargs", [
    {"mode": "execute", "mock": True},
    {"mode": "mock", "mock": False},
    {"mode": "dry-run", "mock": True},
    {"mode": "mock", "mock": "yes"},
    {"mode": "Mock"},
    {"mode": "bogus"},
    {"mode": "execute", "mock_bad_line": True},
    {"mode": "dry-run", "mock_bad_line": True},
])
def test_inconsistent_mode_arguments_are_rejected_before_anything_runs(monkeypatch, clean_llm_env, no_network, kwargs):
    monkeypatch.setenv("AI_EMS_LLM_BASE_URL", BASE)
    monkeypatch.setattr(smoke, "resolve_configuration", lambda *a, **k: pytest.fail("rejected before configuration"))
    with pytest.raises(ValueError):
        run_smoke_test(root=ROOT, **kwargs)
    assert no_network == []


# 4·5·6. URL and authentication preflight ----------------------------------------------------

def test_execute_without_url_is_rejected_before_any_connection(clean_llm_env, no_network):
    with pytest.raises(ValueError, match="명시해야 합니다"):
        run_smoke_test(mode="execute", root=ROOT)
    assert no_network == []


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://127.0.0.1:18080", "http://host.docker.internal:11434",
                                 f"http://{HOST}:8443"])
def test_execute_rejects_non_https_urls(monkeypatch, clean_llm_env, no_network, url):
    monkeypatch.setenv("AI_EMS_LLM_API_KEY", TOKEN)
    with pytest.raises(ValueError):
        run_smoke_test(mode="execute", base_url=url, root=ROOT)
    assert no_network == []


@pytest.mark.parametrize("environ", [
    {"AI_EMS_CF_ACCESS_CLIENT_SECRET": "secret-only"},
    {"AI_EMS_LLM_API_KEY": TOKEN, "AI_EMS_CF_ACCESS_CLIENT_ID": "id", "AI_EMS_CF_ACCESS_CLIENT_SECRET": "secret"},
    {"AI_EMS_LLM_API_KEY": TOKEN[:32] + "\r\n" + TOKEN[32:]},
    {"OLLAMA_API_KEY": "hidden-provider-key"},
    {"OLLAMA_API_KEY": "hidden-provider-key", "AI_EMS_CF_ACCESS_CLIENT_ID": "id", "AI_EMS_CF_ACCESS_CLIENT_SECRET": "secret"},
])
def test_incomplete_conflicting_or_hidden_credentials_are_rejected(environ):
    with pytest.raises(ValueError) as error:
        validate_execute_configuration(BASE, environ)
    for value in environ.values():
        assert value.strip() not in str(error.value)


def test_project_bearer_takes_precedence_over_ollama_api_key():
    validate_execute_configuration(BASE, {"AI_EMS_LLM_API_KEY": TOKEN, "OLLAMA_API_KEY": "other"})


def test_hidden_ollama_api_key_makes_the_agent_auth_differ(monkeypatch, clean_llm_env):
    assert agent_auth_matches(MODEL, BASE, {}) is True
    monkeypatch.setenv("OLLAMA_API_KEY", "hidden-provider-key")
    assert agent_auth_matches(MODEL, BASE, {}) is False


# 7. .env authentication reaches the actual Agent run ----------------------------------------

def test_dotenv_bearer_is_used_by_probes_and_the_actual_agent_run(tmp_path, monkeypatch, clean_llm_env, no_network):
    root = env_root(tmp_path, f"AI_EMS_LLM_API_KEY={TOKEN}\nAI_EMS_LLM_BASE_URL={BASE}\n")
    caddy = FakeCaddy(monkeypatch)
    report = run_smoke_test(mode="execute", model_name=MODEL, root=root)
    assert report.overall_status == "PASS", json.dumps(report.to_dict(), ensure_ascii=False, indent=1)
    # Tool probe + run 1 + run 2, all through the real ChatOllama/_chat_model, all authorized.
    assert len(caddy.chats()) == 3 and all(authorized for _, _, authorized in caddy.requests)
    assert {(method, path) for method, path, _ in caddy.requests} == {("GET", "/api/tags"), ("POST", "/api/chat")}
    assert not any(key in os.environ for key in (*AUTH_KEYS, "AI_EMS_SINGLE_WORKER"))  # scope restored
    assert no_network == [] and TOKEN not in json.dumps(report.to_dict(), ensure_ascii=False)


# 8. Connection and authentication failures stop everything ----------------------------------

@pytest.mark.parametrize("dotenv", [f"AI_EMS_LLM_API_KEY={'f' * 64}\n", ""], ids=["wrong-bearer", "no-bearer"])
def test_caddy_401_stops_before_inference_and_calculation(tmp_path, monkeypatch, clean_llm_env, no_network, no_kpg_load, dotenv):
    root = env_root(tmp_path, f"AI_EMS_LLM_BASE_URL={BASE}\n{dotenv}")
    caddy = FakeCaddy(monkeypatch)
    report = run_smoke_test(mode="execute", model_name=MODEL, root=root)
    scenarios = by_id(report)
    assert report.overall_status == "FAIL" and scenarios[1].metrics["code"] == "auth_error"
    assert [s.status for s in report.scenarios[1:]] == ["SKIP"] * 9
    assert caddy.chats() == []


# 9. Tool Calling failures block the actual run ----------------------------------------------

def _raise_timeout(body):
    raise httpx.ReadTimeout("simulated provider timeout")


@pytest.mark.parametrize("reply, code", [
    (lambda body: chat_reply({"content": "도구 없이 답합니다."}), "tool_call_missing"),
    (lambda body: tool_reply(("generator_sensitivity", {"outage_line_id": SAMPLE_OUTAGE_LINE})), "tool_name_mismatch"),
    (lambda body: tool_reply(("line_contingency", {"outage_line_id": "LINE-99-99"})), "arguments_rejected"),
    (lambda body: tool_reply(("line_contingency", {"outage_line_id": SAMPLE_OUTAGE_LINE, "line_id": SAMPLE_OUTAGE_LINE})), "arguments_rejected"),
    (lambda body: tool_reply(("line_contingency", {"outage_line_id": SAMPLE_OUTAGE_LINE}),
                             ("line_contingency", {"outage_line_id": SAMPLE_OUTAGE_LINE})), "multiple_tool_calls"),
    (lambda body: httpx.Response(500, text="upstream failure"), "model_error"),
    (_raise_timeout, "timeout"),
], ids=["no-tool", "wrong-name", "wrong-line", "extra-argument", "two-calls", "model-error", "timeout"])
def test_tool_calling_failure_blocks_the_actual_run(tmp_path, monkeypatch, clean_llm_env, no_network, no_kpg_load, reply, code):
    root = env_root(tmp_path, f"AI_EMS_LLM_API_KEY={TOKEN}\nAI_EMS_LLM_BASE_URL={BASE}\n")
    caddy = FakeCaddy(monkeypatch, chat=reply)
    report = run_smoke_test(mode="execute", model_name=MODEL, root=root)
    scenarios = by_id(report)
    assert report.overall_status == "FAIL"
    assert (scenarios[1].status, scenarios[2].status, scenarios[2].metrics["code"]) == ("PASS", "FAIL", code)
    assert [scenarios[sid].status for sid in range(3, 11)] == ["SKIP"] * 8
    assert all("차단" in scenarios[sid].detail for sid in range(3, 11))
    assert len(caddy.chats()) == 1  # the probe only; no Agent run


# 10. The configured model must be listed ----------------------------------------------------

def test_missing_model_stops_before_any_inference(tmp_path, monkeypatch, clean_llm_env, no_network, no_kpg_load):
    root = env_root(tmp_path, f"AI_EMS_LLM_API_KEY={TOKEN}\nAI_EMS_LLM_BASE_URL={BASE}\n")
    caddy = FakeCaddy(monkeypatch, models=("smoke-model:7b", "other:1b"))
    report = run_smoke_test(mode="execute", model_name=MODEL, root=root)
    assert by_id(report)[1].metrics["code"] == "model_not_found" and report.overall_status == "FAIL"
    assert caddy.chats() == []


def test_model_names_follow_ollama_rules_without_similarity_matching():
    assert model_listed("qwen3:8b", ["qwen3:8b"])
    assert model_listed("llama3", ["llama3:latest"])  # Ollama's default tag
    assert model_listed("team/model", ["team/model:latest"])
    assert not model_listed("qwen3:8b", ["qwen3:8b-q4_K_M", "qwen3:14b"])
    assert not model_listed("Qwen3:8b", ["qwen3:8b"])
    assert not model_listed("qwen3:latest", ["qwen3:8b"])
    assert not model_listed("qwen3", [])


# 11. Initial AC revision judgement ----------------------------------------------------------

def envelope(status, reason=None, results=()):
    return {"analysis": {"status": status, "reason_code": reason, "results": list(results)},
            "ui_updates": [{"result_id": r["result_id"]} for r in results]}


@pytest.mark.parametrize("before, after, env, registered, executed, expected", [
    (REV_A, REV_B, envelope("failed", "MODEL_REVISION_CHANGED"), 0, True, "PASS"),
    (REV_A, REV_A, envelope("completed", None, [{"result_id": "r", "model_revision": REV_A}]), 1, True, "PASS"),
    (REV_A, REV_B, envelope("completed", None, [{"result_id": "r", "model_revision": REV_B}]), 1, True, "FAIL"),
    (REV_A, REV_B, envelope("failed", "MODEL_REVISION_CHANGED"), 1, True, "FAIL"),
    (REV_A, REV_B, envelope("failed", "ANALYSIS_BUSY"), 0, True, "FAIL"),
    (REV_A, REV_A, envelope("failed", "MODEL_REVISION_CHANGED"), 0, True, "FAIL"),
    (REV_A, REV_A, envelope("completed", None, [{"result_id": "r", "model_revision": REV_B}]), 1, True, "FAIL"),
    (REV_A, REV_A, envelope("completed", None, [{"result_id": "r", "model_revision": REV_A}]), 0, True, "FAIL"),
    (REV_A, REV_A, envelope("failed", "RESULT_NOT_REGISTERED"), 0, True, "FAIL"),
    (None, REV_B, envelope("failed", "MODEL_REVISION_CHANGED"), 0, True, "FAIL"),
    (REV_A, None, envelope("completed", None, [{"result_id": "r", "model_revision": REV_A}]), 1, True, "FAIL"),
    (REV_A, REV_B, envelope("failed", "OUTAGE_LINE_MISMATCH"), 0, False, "SKIP"),
], ids=["changed-fail-closed", "unchanged-published", "changed-but-accepted", "changed-but-registered",
        "changed-other-failure", "unchanged-but-mrc", "unchanged-stale-receipt", "unchanged-unregistered",
        "unchanged-not-published", "before-unknown", "after-unknown", "tool-not-executed"])
def test_initial_revision_judgement(before, after, env, registered, executed, expected):
    status, detail, metrics = judge_initial_revision(before, after, env, registered_before=0,
                                                     registered_after=registered, tool_executed=executed)
    assert status == expected, detail
    if expected != "PASS":
        assert status in ("FAIL", "SKIP")


def test_mock_run_on_an_already_converged_model_publishes_without_revision_change(monkeypatch, clean_llm_env, no_network):
    import ai_ems.network as network_module
    from ai_ems.tools.security_tools import run_line_contingency

    real = network_module.load_identified_network

    def prepared(path):
        model = real(path)
        run_line_contingency(model, outage_line_id=SAMPLE_OUTAGE_LINE)  # the first AC happens outside the smoke run
        return model

    monkeypatch.setattr(network_module, "load_identified_network", prepared)
    report = run_smoke_test(mode="mock", root=ROOT)
    scenarios = by_id(report)
    assert report.overall_status == "PASS", json.dumps(report.to_dict(), ensure_ascii=False, indent=1)
    assert scenarios[8].metrics["revision_changed"] is False and "변화 없음" in scenarios[8].detail


# 12. Tool / Ledger / Evidence / Receipt verification ----------------------------------------

@pytest.fixture(scope="module")
def published_run():
    """One real verified security run: scripted LLM, local PyPowSyBl, isolated app state."""
    import web_app
    from ai_ems.network import load_identified_network
    from ai_ems.tools.security_tools import run_line_contingency

    model = load_identified_network(CASE)
    run_line_contingency(model, outage_line_id=SAMPLE_OUTAGE_LINE)
    stub = smoke.ScriptedSmokeModel([AIMessage(content="", tool_calls=[
        {"name": "line_contingency", "args": {"outage_line_id": SAMPLE_OUTAGE_LINE}, "id": "call-evidence-1"}])])
    session, request_id = "smoke-sess-evidence", str(uuid.uuid4())
    with isolated_app_environment(model, run_model_factory=lambda budget: stub._stub) as (client, registry, _):
        response = client.post("/api/grids/kr-kpg193/chat", json=smoke._run_body(session, request_id, 0, "N-1 해석"))
        segment = list(web_app.agent_sessions[session])
        revision = smoke._current_revision(model)
    assert response.status_code == 200 and response.json()["analysis"]["status"] == "completed"
    return {"envelope": response.json(), "segment": segment, "registry": registry, "session": session,
            "request_id": request_id, "revision": revision}


def receipt_problems(run, **changes):
    arguments = {"session_id": run["session"], "request_id": run["request_id"], "current_revision": run["revision"]}
    return verify_receipts(changes.pop("envelope", run["envelope"]), changes.pop("registry", run["registry"]),
                           **{**arguments, **changes})


def test_verified_run_passes_evidence_and_receipt_checks(published_run):
    assert verify_published_evidence(published_run["envelope"], published_run["segment"], published_run["registry"]) == []
    assert receipt_problems(published_run) == []


def test_tool_execution_requires_a_successful_tool_message_of_this_request():
    call = {"name": "line_contingency", "args": {"outage_line_id": SAMPLE_OUTAGE_LINE}, "id": "c1"}
    ok = [HumanMessage(content="q"), AIMessage(content="", tool_calls=[call]),
          ToolMessage(content="{}", tool_call_id="c1", name="line_contingency", status="success")]
    assert judge_tool_execution(ok)["executed"] is True
    rejected = [*ok[:2], ToolMessage(content="Rejected before execution: OUTAGE_LINE_MISMATCH", tool_call_id="c1",
                                     name="line_contingency", status="error")]
    assert judge_tool_execution(rejected)["problem"] == "TOOL_NOT_EXECUTED"
    assert judge_tool_execution(ok[:2])["problem"] == "TOOL_MESSAGE_MISSING"
    assert judge_tool_execution([ok[0]])["problem"] == "TOOL_CALL_MISSING"
    two = AIMessage(content="", tool_calls=[call, {**call, "id": "c2"}])
    assert judge_tool_execution([two, ok[2]])["problem"] == "MULTIPLE_TOOL_CALLS"
    other = AIMessage(content="", tool_calls=[{**call, "name": "generator_sensitivity"}])
    assert judge_tool_execution([other, ok[2]])["problem"] == "TOOL_NAME_MISMATCH"


def _with_evidence(registry, **fields):
    """A registry view whose entries carry modified server evidence."""
    class View:
        def lookup(self, result_id):
            entry = registry.lookup(result_id)
            evidence = {**json.loads(entry.evidence_json), **fields}
            return dataclasses.replace(entry, evidence_json=json.dumps(evidence))
    return View()


def test_evidence_errors_are_reported_not_passed(published_run):
    from ai_ems.analysis_registry import ResultRegistry

    env, segment, registry = published_run["envelope"], published_run["segment"], published_run["registry"]
    errored = [ToolMessage(content=m.content, tool_call_id=m.tool_call_id, name=m.name, status="error")
               if isinstance(m, ToolMessage) else m for m in segment]
    assert "TOOL_MESSAGE_MISSING" in verify_published_evidence(env, errored, registry)
    other_request = [m for m in segment if not (isinstance(m, AIMessage) and m.tool_calls)]
    assert {"TOOL_CALL_RECEIPT_MISMATCH", "TOOL_CALL_NOT_IN_REQUEST"} <= set(verify_published_evidence(env, other_request, registry))
    changed = copy.deepcopy(env)
    changed["ui_updates"][0]["result"]["outage_line_id"] = "LINE-1-2"
    assert "PAYLOAD_MISMATCH" in verify_published_evidence(changed, segment, registry)
    changed = copy.deepcopy(env)
    changed["ui_updates"][0]["result_id"] = str(uuid.uuid4())
    assert "UI_UPDATE_MISMATCH" in verify_published_evidence(changed, segment, registry)
    assert verify_published_evidence(env, segment, ResultRegistry()) == ["RECEIPT_NOT_REGISTERED"]
    assert "EVIDENCE_OPERATION_MISMATCH" in verify_published_evidence(env, segment, _with_evidence(registry, operation="sensitivity"))
    assert "ARTIFACT_DIGEST_INVALID" in verify_published_evidence(env, segment, _with_evidence(registry, artifact_sha256="x"))
    assert "EXECUTION_ID_INVALID" in verify_published_evidence(env, segment, _with_evidence(registry, execution_id="not-a-uuid"))
    assert "EVIDENCE_SHAPE" in verify_published_evidence(env, segment, _with_evidence(registry, verified=True))
    failed = copy.deepcopy(env)
    failed["analysis"].update(status="failed", source="none")
    assert verify_published_evidence(failed, segment, registry) == ["NOT_PUBLISHED"]


def test_receipt_errors_are_reported_not_passed(published_run):
    from ai_ems.analysis_registry import ResultRegistry

    assert receipt_problems(published_run, session_id="smoke-sess-other") == ["RECEIPT_OWNER_MISMATCH"]
    assert receipt_problems(published_run, request_id=str(uuid.uuid4())) == ["REQUEST_ID_MISMATCH"]
    assert receipt_problems(published_run, current_revision=REV_B) == ["MODEL_REVISION_MISMATCH"]
    assert receipt_problems(published_run, current_revision=None) == ["MODEL_REVISION_MISMATCH"]
    assert receipt_problems(published_run, registry=ResultRegistry()) == ["RECEIPT_NOT_REGISTERED"]
    forged = copy.deepcopy(published_run["envelope"])
    forged["analysis"]["results"][0]["producer"] = "manual"
    assert receipt_problems(published_run, envelope=forged) == ["PRODUCER_MISMATCH", "RECEIPT_REGISTRY_MISMATCH"]
    empty = copy.deepcopy(published_run["envelope"])
    empty["analysis"]["results"] = []
    assert receipt_problems(published_run, envelope=empty) == ["RECEIPT_MISSING"]


def test_scenario_six_records_what_it_could_not_verify(clean_llm_env, no_network):
    scenario = by_id(run_smoke_test(mode="mock", root=ROOT))[6]
    assert scenario.status == "PASS" and scenario.metrics["limitations"] == list(EVIDENCE_LIMITS)
    assert "Ledger 원본은 확인 범위 밖" in scenario.detail


def test_a_skipped_scenario_never_makes_the_run_pass(monkeypatch, clean_llm_env, no_network):
    def nine_passes_and_a_skip(config, scenarios, *, mock, mock_bad_line):
        scenarios.extend(smoke._scenario(sid, "PASS", "ok") for sid in range(1, 10))
        scenarios.append(smoke._scenario(10, "SKIP", "not measured"))

    monkeypatch.setattr(smoke, "_run_scenarios", nine_passes_and_a_skip)
    report = run_smoke_test(mode="mock", root=ROOT)
    assert report.summary == {"pass": 9, "fail": 0, "warn": 0, "skip": 1} and report.overall_status == "WARN"

# 13. Isolated environment restoration -------------------------------------------------------

def _assert_restored(web_app, before, process_key):
    for name, value in before.items():
        if value is None:
            assert getattr(web_app, name) is None, name
        elif value is smoke._MISSING:
            assert name not in vars(web_app), name
        else:
            assert getattr(web_app, name) is value, name
    assert os.environ.get("AI_EMS_LLM_API_KEY") == process_key
    assert "AI_EMS_CF_ACCESS_CLIENT_ID" not in os.environ and "AI_EMS_SINGLE_WORKER" not in os.environ


def test_isolated_environment_restores_after_success_error_and_setup_failure(monkeypatch, clean_llm_env):
    import web_app
    import ai_ems.analysis_registry as registry_module

    monkeypatch.setenv("AI_EMS_LLM_API_KEY", "process-only-value")
    monkeypatch.setattr(web_app, "agent_sessions", None)       # an original value of None
    monkeypatch.delattr(web_app, "_analysis_run_model")       # an attribute that did not exist
    before = {name: getattr(web_app, name, smoke._MISSING) for name in ISOLATED_APP_ATTRIBUTES}
    factory, seen = (lambda budget: None), {}
    scoped = {"AI_EMS_CF_ACCESS_CLIENT_ID": "id", "AI_EMS_CF_ACCESS_CLIENT_SECRET": "secret"}

    with isolated_app_environment("isolated-network", run_model_factory=factory, auth_env=scoped) as (client, registry, _):
        seen["client"] = client
        assert web_app.network == "isolated-network" and web_app._analysis_run_model is factory
        assert web_app.analysis_results is registry and web_app.agent_sessions == {}
        assert os.environ["AI_EMS_SINGLE_WORKER"] == "1" and "AI_EMS_LLM_API_KEY" not in os.environ
        assert os.environ["AI_EMS_CF_ACCESS_CLIENT_ID"] == "id"
    assert seen["client"].is_closed
    _assert_restored(web_app, before, "process-only-value")

    with pytest.raises(RuntimeError, match="inside"):
        with isolated_app_environment("isolated-network", run_model_factory=factory, auth_env=scoped) as (client, _, _):
            seen["client"] = client
            raise RuntimeError("inside")
    assert seen["client"].is_closed
    _assert_restored(web_app, before, "process-only-value")

    def broken():
        raise RuntimeError("setup")

    monkeypatch.setattr(registry_module, "SessionGates", broken)  # fails after network/env were replaced
    with pytest.raises(RuntimeError, match="setup"):
        with isolated_app_environment("isolated-network", run_model_factory=factory, auth_env=scoped):
            pytest.fail("the body must not run after a setup failure")
    _assert_restored(web_app, before, "process-only-value")


def test_isolated_environment_without_auth_scope_leaves_auth_untouched(monkeypatch, clean_llm_env):
    monkeypatch.setenv("AI_EMS_LLM_API_KEY", "process-only-value")
    with isolated_app_environment("isolated-network"):
        assert os.environ["AI_EMS_LLM_API_KEY"] == "process-only-value"
    assert os.environ["AI_EMS_LLM_API_KEY"] == "process-only-value"


# 14. Secrets never reach the console or JSON ------------------------------------------------

def test_secrets_never_reach_console_json_or_report_file(tmp_path, monkeypatch, clean_llm_env, no_network, capsys):
    root = env_root(tmp_path, f"AI_EMS_LLM_API_KEY={TOKEN}\nAI_EMS_LLM_BASE_URL={BASE}\n")
    monkeypatch.setattr(smoke, "ROOT", root)
    FakeCaddy(monkeypatch)
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["smoke_test_llm.py", "--execute", "--model", MODEL, "--json", "--output", str(output)])
    assert smoke.main() == 0
    printed = capsys.readouterr()
    assert json.loads(output.read_text(encoding="utf-8"))["smoke_test_report"]["overall_status"] == "PASS"
    for text in (printed.out, printed.err, output.read_text(encoding="utf-8")):
        assert TOKEN not in text, "token in output"
        assert f"Bearer {TOKEN}" not in text and "Authorization" not in text

    # A provider error that echoes the credential is neither copied nor printed.
    FakeCaddy(monkeypatch, chat=lambda body: httpx.Response(500, text=f"denied Authorization: Bearer {TOKEN}"))
    monkeypatch.setattr(sys, "argv", ["smoke_test_llm.py", "--execute", "--model", MODEL])
    assert smoke.main() == 1
    printed = capsys.readouterr()
    assert TOKEN not in printed.out + printed.err and "Tool Calling 점검 중 모델 응답 오류" in printed.out
    assert "Authorization" not in printed.out + printed.err  # the provider body is not copied at all


def test_redaction_and_url_display_hide_credentials(tmp_path, monkeypatch, clean_llm_env, no_network, capsys):
    assert display_url(f"https://user:pw@{HOST}:8443/api?token=abc#x") == f"https://{HOST}:8443 (사용자 정보·경로·쿼리 생략)"
    assert display_url(BASE) == BASE and display_url("") == ""
    assert smoke.redact({"a": [f"x{TOKEN}y"]}, [TOKEN]) == {"a": ["x[보호됨]y"]}
    root = env_root(tmp_path, f"AI_EMS_LLM_BASE_URL=https://user:pw@{HOST}:8443/?token=abc\nAI_EMS_LLM_API_KEY={TOKEN}\n")
    monkeypatch.setattr(smoke, "ROOT", root)
    monkeypatch.setattr(sys, "argv", ["smoke_test_llm.py", "--json"])
    assert smoke.main() == 0
    printed = capsys.readouterr().out
    assert "pw@" not in printed and "token=abc" not in printed and TOKEN not in printed
