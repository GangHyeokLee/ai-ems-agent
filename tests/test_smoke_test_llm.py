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
