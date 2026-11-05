"""PH4-02-INFRA-02/03: AI-EMS 실제 LLM Tool Calling 독립 Smoke Test 도구.

실행 모드는 `mode` 하나로 정한다. 함수 호출의 `mock` 인자는 호환용이며 mode와 다르면 거부한다.
- dry-run (기본): 설정, 사전 요구사항, 검증 시나리오 및 격리 범위만 확인한다. 외부 연결·추론·계산 없음.
- execute (--execute): 실제 LLM Smoke Test 실행 (사용자 명시적 승인 후 실행).
- mock (--mock): LLM만 고정 응답(ScriptedSmokeModel)으로 바꾼다. Ollama·Caddy에 접속하지 않으며
  KPG-193 계산은 이 프로세스의 로컬 PyPowSyBl로 실제 수행한다. Mock 결과는 실제 LLM 결과가 아니다.

10단계 검증 시나리오 (앞 단계가 실패하면 뒤 단계는 SKIP하고 실제 Run을 시작하지 않는다):
1. LLM 연결 정상 여부 (GET /api/tags HTTP 200, 지정 모델이 목록에 있음)
2. 모델의 Tool Calling 지원 (도구 1개, 이름, 서버 guard와 같은 인자 규칙)
3. Security Run (KPG-193 선로 N-1 run 요청 수신 및 처리)
4. 실제 Tool 호출 확인 (이번 요청의 AI 호출과 성공 ToolMessage; guard 거부는 실행이 아님)
5. 고정 선로 ID 준수 여부 (요청 선로 ID 일치 및 Guard 인가 통과)
6. Ledger 및 Evidence 검증 (응답·세션 이력·Registry evidence 대조; 확인 못 한 범위는 metrics에 기록)
7. Receipt 발급 여부 (producer=agent_tool, 요청 ID, 세션 소유, 현재 revision)
8. 최초 AC 계산 후 Model Revision 변화 처리 (실제 revision 전후 비교로 fail-closed 또는 정상 공개 판정)
9. 이후 재요청의 정상 완료 여부 (수렴 상태에서 후속 run 성공)
10. 실제 응답 지연 시간 및 예산 기록 (Connect, Read, Total 지연 vs RunBudget 비교)
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Generator
from urllib.parse import urlsplit
import uuid

# 프로젝트 루트 경로 (scripts/의 부모 디렉터리)
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

DEFAULT_MODEL = "qwen3.8:27b-q4_K_M"
DEFAULT_BASE_URL = ""
DEFAULT_CASE_FILE = "data/KPG193_ver2_0_powsybl_full.mat"
SAMPLE_OUTAGE_LINE = "LINE-16-28"
MODES = ("dry-run", "mock", "execute")
# Inputs of ai_ems.config.get_llm_client_kwargs: the probes and the actual Agent must use the same values.
AUTH_KEYS = ("AI_EMS_LLM_API_KEY", "AI_EMS_CF_ACCESS_CLIENT_ID", "AI_EMS_CF_ACCESS_CLIENT_SECRET")
AUTH_HEADERS = ("authorization", "cf-access-client-id", "cf-access-client-secret")
KPG_GRID_ID = "kr-kpg193"
TOOL_BY_KIND = {"security": "line_contingency", "sensitivity": "generator_sensitivity"}
SCENARIO_NAMES = {
    1: "LLM 연결 정상 여부",
    2: "모델의 Tool Calling 지원",
    3: "Security Run",
    4: "실제 Tool 호출 확인",
    5: "고정 선로 ID 준수 여부",
    6: "Ledger 및 Evidence 검증",
    7: "Receipt 발급 여부",
    8: "최초 AC 계산 후 Model Revision 변화 처리",
    9: "이후 재요청의 정상 완료 여부",
    10: "실제 응답 지연 시간 및 예산 기록",
}
# The server contract does not expose these after a run; scenario 6 never claims them.
EVIDENCE_LIMITS = (
    "ExecutionLedger 원본 기록은 요청 안에서만 존재해 직접 조회하지 않음 (Registry evidence의 job_id·execution_id·artifact_sha256으로 간접 확인)",
    "ToolMessage artifact 원문은 세션 이력에 저장되지 않아 artifact_sha256을 다시 계산하지 않음",
    "verify_evidence 내부 issues 목록은 응답에 없어 status·reason_code만 확인함",
)
_MISSING = object()


@dataclass
class ScenarioResult:
    scenario_id: int
    name: str
    status: str  # "PASS", "FAIL", "SKIP", "WARN"
    duration_seconds: float
    detail: str
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class SmokeReport:
    mode: str  # "dry-run", "execute", "mock"
    target_model: str
    target_base_url: str
    auth_summary: str
    overall_status: str  # "PASS", "FAIL", "WARN", "DRY_RUN"
    started_at: str
    finished_at: str
    total_duration_seconds: float
    scenarios: list[ScenarioResult] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "smoke_test_report": {
                "mode": self.mode,
                "target_model": self.target_model,
                "target_base_url": self.target_base_url,
                "auth_summary": self.auth_summary,
                "overall_status": self.overall_status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "total_duration_seconds": round(self.total_duration_seconds, 3),
                "summary": self.summary,
                "notes": list(self.notes),
                "scenarios": [asdict(s) for s in self.scenarios],
            }
        }

    def format_console(self) -> str:
        lines = []
        lines.append("=" * 80)
        lines.append(f" AI-EMS 실제 LLM Smoke Test 보고서 [{self.mode.upper()}]")
        lines.append("=" * 80)
        lines.append(f"• 대상 모델: {self.target_model}")
        lines.append(f"• 접속 주소: {self.target_base_url}")
        lines.append(f"• 인증 상태: {self.auth_summary}")
        lines.append(f"• 종합 판정: {self.overall_status}")
        lines.append(f"• 실행 시간: {self.started_at} ~ {self.finished_at} ({self.total_duration_seconds:.2f}s)")
        for note in self.notes:
            lines.append(f"• 비고: {note}")
        lines.append("-" * 80)
        lines.append(f"| {'No':^4} | {'검증 시나리오':<30} | {'결과':^6} | {'지연(s)':^8} | {'세부 내용':<24} |")
        lines.append("-" * 80)
        for s in self.scenarios:
            status_mark = f"[{s.status}]"
            lines.append(f"| {s.scenario_id:^4} | {s.name:<30} | {status_mark:^6} | {s.duration_seconds:^8.2f} | {s.detail:<24} |")
        lines.append("-" * 80)
        lines.append(f"요약: PASS={self.summary.get('pass', 0)}, FAIL={self.summary.get('fail', 0)}, "
                     f"WARN={self.summary.get('warn', 0)}, SKIP={self.summary.get('skip', 0)}")
        lines.append("=" * 80)
        return "\n".join(lines)


def mask_secret(value: str | None) -> str:
    """비밀값 원문을 은닉하고 설정 여부와 안전한 메타정보만 반환합니다."""
    if not value or not value.strip():
        return "미설정 (None)"
    v = value.strip()
    is_hex64 = len(v) == 64 and all(c in "0123456789abcdefABCDEF" for c in v)
    if is_hex64:
        return "설정됨 (Bearer 64-hex token, [보호됨])"
    return f"설정됨 ({len(v)}자 문자열, [보호됨])"


def display_url(base_url: str | None) -> str:
    """Origin only: user info, path, query and fragment never reach the console or JSON."""
    if not base_url:
        return ""
    try:
        parsed = urlsplit(base_url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return "(URL 형식 오류 — 표시 생략)"
    if not parsed.scheme or not host:
        return "(URL 형식 오류 — 표시 생략)"
    origin = f"{parsed.scheme}://{f'[{host}]' if ':' in host else host}{f':{port}' if port else ''}"
    hidden = (parsed.username is not None or parsed.password is not None
              or parsed.path not in ("", "/") or parsed.query or parsed.fragment)
    return origin + (" (사용자 정보·경로·쿼리 생략)" if hidden else "")


def redact(value: Any, secrets: list[str]) -> Any:
    """Replace every configured secret value in report text (defense in depth)."""
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[보호됨]")
        return value
    if isinstance(value, dict):
        return {key: redact(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(item, secrets) for item in value)
    return value


def read_dotenv_safely(root: Path) -> dict[str, str]:
    """dotenv_values를 사용해 쉘 실행 없이 안전하게 .env를 파싱합니다."""
    path = root / ".env"
    if not path.is_file():
        return {}
    try:
        from dotenv import dotenv_values
        values = dotenv_values(path, interpolate=False)
        return {k: v for k, v in values.items() if v is not None}
    except Exception:
        return {}


def resolve_configuration(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """CLI 인자 > 프로세스 환경변수 > .env 우선순위로 설정을 결정하되 비밀값은 보호합니다."""
    env_file = read_dotenv_safely(root)

    # 1. 모델명 결정
    model_name = (
        getattr(args, "model", None)
        or os.environ.get("AI_EMS_MODEL")
        or env_file.get("AI_EMS_MODEL")
        or DEFAULT_MODEL
    )

    # 2. Base URL 결정
    base_url = (
        getattr(args, "base_url", None)
        or os.environ.get("AI_EMS_LLM_BASE_URL")
        or env_file.get("AI_EMS_LLM_BASE_URL")
        or DEFAULT_BASE_URL
    )

    # 3. 인증 요약 (비밀값 은닉)
    api_key = os.environ.get("AI_EMS_LLM_API_KEY", env_file.get("AI_EMS_LLM_API_KEY", ""))
    cf_id = os.environ.get("AI_EMS_CF_ACCESS_CLIENT_ID", env_file.get("AI_EMS_CF_ACCESS_CLIENT_ID", ""))
    cf_secret = os.environ.get("AI_EMS_CF_ACCESS_CLIENT_SECRET", env_file.get("AI_EMS_CF_ACCESS_CLIENT_SECRET", ""))

    if api_key.strip() and (cf_id.strip() or cf_secret.strip()):
        auth_type = "Invalid (conflicting credentials)"
        auth_summary = "인증 설정 충돌"
    elif api_key.strip():
        auth_type = "Bearer"
        auth_summary = mask_secret(api_key)
    elif cf_id.strip() and cf_secret.strip():
        auth_type = "Cloudflare Access"
        auth_summary = f"CF-Access-Client (ID={len(cf_id)}자, Secret=[보호됨])"
    elif cf_id.strip() or cf_secret.strip():
        auth_type = "Invalid (incomplete Cloudflare Access credentials)"
        auth_summary = "Cloudflare Access 인증 설정 불완전"
    else:
        auth_type = "None"
        auth_summary = (
            "추가 인증 헤더 없음 (HTTPS/Tailscale 접근 정책은 실행 환경에서 별도 확인)"
            if base_url.startswith("https://")
            else "인증 헤더 없음"
        )

    # 4. 계통 파일
    case_file = (
        os.environ.get("AI_EMS_CASE_FILE")
        or env_file.get("AI_EMS_CASE_FILE")
        or DEFAULT_CASE_FILE
    )
    case_path = Path(case_file)
    if not case_path.is_absolute():
        case_path = root / case_path

    # 합산된 환경 딕셔너리 (인증 검증용)
    merged_env = {**env_file, **os.environ}
    if getattr(args, "model", None):
        merged_env["AI_EMS_MODEL"] = args.model
    if getattr(args, "base_url", None):
        merged_env["AI_EMS_LLM_BASE_URL"] = args.base_url
    # The single authentication source for the probes AND the actual Agent run (scoped into
    # os.environ only inside isolated_app_environment). Never printed.
    auth_env = {key: merged_env[key] for key in AUTH_KEYS if key in merged_env}

    return {
        "model_name": model_name,
        "base_url": base_url,
        "auth_type": auth_type,
        "auth_summary": auth_summary,
        "case_path": case_path,
        "merged_env": merged_env,
        "auth_env": auth_env,
        "secrets": sorted({value.strip() for value in auth_env.values() if value and value.strip()},
                          key=len, reverse=True),
    }


def validate_execute_configuration(base_url: str, environ: dict[str, str]) -> None:
    """Reject implicit, insecure, or ambiguous endpoints before any real work starts."""
    if not base_url or base_url != base_url.strip():
        raise ValueError("실행하려면 AI_EMS_LLM_BASE_URL 또는 --base-url을 명시해야 합니다.")

    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("LLM Base URL 형식이 올바르지 않습니다.") from exc

    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("실행에는 사용자 정보·경로·쿼리 없는 HTTPS origin URL이 필요합니다.")

    from ai_ems.config import get_llm_client_kwargs

    get_llm_client_kwargs(base_url, environ=environ)
    # ollama-python adds `Authorization: Bearer $OLLAMA_API_KEY` to the Agent's client when no
    # explicit Authorization header is configured; the /api/tags probe would not send it.
    if (environ.get("OLLAMA_API_KEY") or "").strip() and not (environ.get("AI_EMS_LLM_API_KEY") or "").strip():
        raise ValueError(
            "OLLAMA_API_KEY가 설정되어 있으면 ollama 클라이언트가 Agent 요청에만 별도 Bearer를 붙여 "
            "연결 점검과 인증이 달라집니다. 실행 전에 해제하거나 AI_EMS_LLM_API_KEY를 사용하세요."
        )


def check_prerequisites(config: dict[str, Any]) -> list[tuple[str, bool, str]]:
    """테스트 실행 전 필수 파일 및 의존성 정합성을 점검합니다."""
    checks = []

    # 1. Python 버전
    py_ok = sys.version_info >= (3, 11)
    checks.append(("Python 3.11+ 환경", py_ok, f"현재: {sys.version.split()[0]}"))

    # 2. 계통 파일 존재
    case_path = config["case_path"]
    case_ok = case_path.is_file()
    checks.append(("KPG-193 계통 데이터 파일", case_ok, str(case_path)))

    # 3. 필수 라이브러리 import 가능 여부
    imports_ok = True
    missing = []
    for pkg in ("pypowsybl", "fastapi", "httpx", "langchain_core", "langgraph"):
        try:
            __import__(pkg)
        except ImportError:
            imports_ok = False
            missing.append(pkg)
    checks.append(("핵심 라이브러리 설치 (pypowsybl, fastapi 등)", imports_ok,
                   "모두 설치됨" if imports_ok else f"누락: {', '.join(missing)}"))

    # 4. URL 및 인증 계약 정합성 검사
    auth_ok = True
    auth_msg = "정상"
    try:
        validate_execute_configuration(config["base_url"], config["merged_env"])
    except ValueError as exc:
        auth_ok = False
        auth_msg = str(exc)
    checks.append(("LLM URL 및 인증 계약 정합성", auth_ok, auth_msg))

    return checks


def probe_llm_connection(base_url: str, environ: dict[str, str], timeout: float = 8.0) -> dict[str, Any]:
    """Ollama/Caddy의 /api/tags 엔드포인트에 인증 헤더를 포함해 접근성을 점검합니다."""
    import httpx
    from ai_ems.config import get_llm_client_kwargs

    try:
        kwargs = get_llm_client_kwargs(base_url, environ=environ)
    except (ValueError, TypeError) as exc:
        return {"status": "FAIL", "code": "invalid_auth_config", "detail": f"인증 설정 오류: {exc}", "latency_ms": 0.0, "models": []}

    url = base_url.rstrip("/") + "/api/tags"
    t0 = time.monotonic()
    try:
        response = httpx.get(url, **kwargs, timeout=timeout, follow_redirects=False)
        latency_ms = (time.monotonic() - t0) * 1000.0

        if response.status_code == 200:
            try:
                data = response.json()
            except Exception:
                return {"status": "FAIL", "code": "invalid_json", "detail": "Ollama 응답이 JSON 형식이 아님",
                        "latency_ms": latency_ms, "models": []}
            if not isinstance(data, dict) or not isinstance(data.get("models"), list):
                return {"status": "FAIL", "code": "invalid_response", "detail": "HTTP 200이지만 Ollama models 목록이 없음",
                        "latency_ms": latency_ms, "models": []}
            # Ollama reports both `name` and `model`; either is an exact, official identifier.
            models = sorted({value for entry in data["models"] if isinstance(entry, dict)
                             for value in (entry.get("name"), entry.get("model")) if isinstance(value, str)})
            return {"status": "PASS", "code": "ready", "detail": f"HTTP 200 OK (발견된 모델 {len(data['models'])}개)",
                    "latency_ms": latency_ms, "models": models}
        elif response.status_code in (401, 403):
            return {"status": "FAIL", "code": "auth_error", "detail": f"인증/접근 거부 (HTTP {response.status_code})",
                    "latency_ms": latency_ms, "models": []}
        elif response.status_code == 502:
            return {"status": "FAIL", "code": "upstream_502", "detail": "HTTP 502 (Caddy 또는 Ollama 프로세스 미기동)",
                    "latency_ms": latency_ms, "models": []}
        else:
            return {"status": "FAIL", "code": "http_error", "detail": f"HTTP {response.status_code}",
                    "latency_ms": latency_ms, "models": []}
    except httpx.TimeoutException:
        return {"status": "FAIL", "code": "timeout", "detail": f"{timeout}초 연결 시간 초과", "latency_ms": 0.0, "models": []}
    except httpx.RequestError as exc:
        return {"status": "FAIL", "code": "unreachable", "detail": f"엔드포인트 연결 실패 ({exc.__class__.__name__})",
                    "latency_ms": 0.0, "models": []}


def model_listed(model_name: str, models: list[str]) -> bool:
    """Exact match only; a name without a tag means ':latest' (Ollama's documented default tag)."""
    names = {name for name in models if isinstance(name, str)}
    if model_name in names:
        return True
    return ":" not in model_name.rsplit("/", 1)[-1] and f"{model_name}:latest" in names


def probe_tool_calling_support(model_name: str, base_url: str, environ: dict[str, str],
                               mock: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    """ChatOllama bind_tools를 통해 모델이 올바른 함수 호출 JSON을 생성하는지 점검합니다.

    PASS는 도구 호출이 정확히 1개, 이름이 line_contingency, 인자가 서버 guard(normalize_arguments)를
    통과할 때뿐이다. 그 밖의 경우와 오류·timeout은 FAIL이며, 실행기는 이후 실제 Run을 시작하지 않는다.
    모델·provider의 원문은 결과에 옮기지 않는다.
    """
    t0 = time.monotonic()
    if mock:
        return {
            "status": "PASS",
            "code": "mock",
            "duration": 0.0,
            "tool_calls": [{"name": "line_contingency", "args": {"outage_line_id": SAMPLE_OUTAGE_LINE}, "id": "mock-call-1"}],
            "detail": f"[MOCK] 고정 응답 — 실제 모델 Tool Calling 점검 아님 ({SAMPLE_OUTAGE_LINE})",
        }

    def failed(code: str, detail: str, tool_calls: list | None = None) -> dict[str, Any]:
        return {"status": "FAIL", "code": code, "duration": time.monotonic() - t0,
                "tool_calls": tool_calls or [], "detail": detail}

    try:
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage
        from ai_ems.analysis_context import AnalysisJob, ToolCallRejected, normalize_arguments
        from ai_ems.config import get_llm_client_kwargs

        def line_contingency(outage_line_id: str) -> str:
            """전력계통 선로 N-1 상정사고 계산을 수행하는 도구."""
            return f"EXECUTED: {outage_line_id}"

        client_kwargs = get_llm_client_kwargs(base_url, environ=environ)
        client_kwargs["timeout"] = timeout
        chat_model = ChatOllama(
            model=model_name,
            base_url=base_url,
            temperature=0,
            **({"client_kwargs": client_kwargs} if client_kwargs else {}),
        )
        bound = chat_model.bind_tools([line_contingency])
        prompt = (f"{SAMPLE_OUTAGE_LINE} 선로가 탈락했을 때 계통 N-1 안전도 해석을 수행해야 합니다. "
                  f"반드시 line_contingency 도구를 호출하고 outage_line_id로 '{SAMPLE_OUTAGE_LINE}'을 지정하세요.")

        response = bound.invoke([HumanMessage(content=prompt)])
        calls = list(response.tool_calls or [])
    except Exception as exc:
        import httpx

        status = getattr(exc, "status_code", None)
        if isinstance(exc, httpx.TimeoutException):
            return failed("timeout", f"Tool Calling 응답 시간 초과 ({timeout:g}s)")
        return failed("model_error", f"Tool Calling 점검 중 모델 응답 오류 ({type(exc).__name__}"
                                     f"{f', HTTP {status}' if isinstance(status, int) else ''}) — 원문 미기록")

    if not calls:
        return failed("tool_call_missing", f"모델이 도구를 호출하지 않고 텍스트로 응답함 ({len(str(response.content))}자, 원문 미기록)")
    if len(calls) != 1:
        return failed("multiple_tool_calls", f"도구 호출 {len(calls)}개 (서버 guard는 정확히 1개만 허용)", calls)
    call = calls[0]
    if call.get("name") != "line_contingency":
        return failed("tool_name_mismatch", f"예상치 못한 도구 호출: {call.get('name')}", calls)
    called_line = call.get("args", {}).get("outage_line_id") if isinstance(call.get("args"), dict) else None
    try:
        # The same argument rule the run guard applies before any execution.
        normalize_arguments(AnalysisJob(KPG_GRID_ID, "security", SAMPLE_OUTAGE_LINE), "security", call.get("args"))
    except ToolCallRejected as exc:
        return failed("arguments_rejected", f"서버 guard 기준 인자 거부 ({exc.code}; 요청: {SAMPLE_OUTAGE_LINE}, 호출: {called_line})", calls)
    return {
        "status": "PASS",
        "code": "ready",
        "duration": time.monotonic() - t0,
        "tool_calls": calls,
        "detail": f"정상 Tool Calling 확인 (line_contingency, outage_line_id={called_line})",
    }


ISOLATED_APP_ATTRIBUTES = ("network", "analysis_results", "analysis_requests", "analysis_sessions",
                           "agent_sessions", "_analysis_run_model")


@contextmanager
def isolated_app_environment(network: Any, run_model_factory: Callable[[Any], Any] | None = None,
                             auth_env: dict[str, str] | None = None) -> Generator[Any, None, None]:
    """실행 중인 서버와 격리된 in-memory TestClient 환경을 구축하고 종료 후 원상복구합니다.

    모든 변경은 try 안에서 하며, 성공·예외·설정 중 실패 모두 원래 값으로 되돌린다. 원래 없던 속성과
    환경변수는 다시 지운다. ``auth_env``가 주어지면 이 범위에서만 프로젝트 인증 변수(AUTH_KEYS)를
    그 값으로 맞춰, 실제 Agent(_chat_model, os.environ 사용)가 연결 점검과 같은 인증을 쓰게 한다.
    """
    import web_app
    from fastapi.testclient import TestClient
    from ai_ems.analysis_registry import RequestTracker, ResultRegistry, SessionGates
    import ai_ems.config  # noqa: F401  Its import-time .env load must finish before keys are scoped.

    saved_attributes = {name: getattr(web_app, name, _MISSING) for name in ISOLATED_APP_ATTRIBUTES}
    env_keys = ("AI_EMS_SINGLE_WORKER", *(AUTH_KEYS if auth_env is not None else ()))
    saved_env = {key: os.environ.get(key) for key in env_keys}
    client = None
    try:
        os.environ["AI_EMS_SINGLE_WORKER"] = "1"
        if auth_env is not None:
            for key in AUTH_KEYS:
                if key in auth_env:
                    os.environ[key] = auth_env[key]
                else:
                    os.environ.pop(key, None)
        web_app.network = network
        web_app.analysis_results = ResultRegistry()
        web_app.analysis_requests = RequestTracker()
        web_app.analysis_sessions = SessionGates()
        web_app.agent_sessions = {}
        if run_model_factory is not None:
            web_app._analysis_run_model = run_model_factory
        client = TestClient(web_app.app)  # No lifespan: no background runtime is started.
        yield client, web_app.analysis_results, web_app.analysis_requests
    finally:
        try:
            if client is not None:
                client.close()
        finally:
            for name, value in saved_attributes.items():
                if value is _MISSING:
                    if name in vars(web_app):
                        delattr(web_app, name)
                else:
                    setattr(web_app, name, value)
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def agent_auth_matches(model_name: str, base_url: str, auth_env: dict[str, str]) -> bool:
    """Inside the scoped environment: the actual Agent client's auth headers equal the probe's.

    Builds the real ``_chat_model`` client (no request is sent) and compares only authentication
    headers in memory. Anything that cannot be compared counts as a mismatch (fail closed).
    """
    from ai_ems.agent.graph import _chat_model
    from ai_ems.config import get_llm_client_kwargs

    def auth_only(headers: Any) -> dict[str, str]:
        return {str(key).lower(): value for key, value in headers.items() if str(key).lower() in AUTH_HEADERS}

    try:
        probe = auth_only(get_llm_client_kwargs(base_url, environ=auth_env).get("headers", {}))
        agent_client = _chat_model(model_name, base_url)._client
        agent = auth_only(agent_client._client.headers)
        agent_client.close()
    except Exception:
        return False
    return probe == agent


class ScriptedSmokeModel:
    """Mock 테스트를 위한 BaseChatModel 호환 스텁."""
    def __init__(self, steps: list[Any]):
        from pydantic import BaseModel, Field
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.outputs import ChatGeneration, ChatResult

        class _InnerStub(BaseChatModel):
            steps_list: list = Field(default_factory=list)
            calls_list: list = Field(default_factory=list)

            @property
            def _llm_type(self) -> str:
                return "scripted-smoke-stub"

            def bind_tools(self, tools, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                self.calls_list.append(list(messages))
                from langchain_core.messages import AIMessage
                step = self.steps_list.pop(0) if self.steps_list else AIMessage(content="종료")
                if callable(step):
                    step = step(messages)
                if isinstance(step, BaseException):
                    raise step
                return ChatResult(generations=[ChatGeneration(message=step)])

        self._stub = _InnerStub(steps_list=list(steps))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stub, name)

    def bind_tools(self, tools, **kwargs):
        return self._stub.bind_tools(tools, **kwargs)

    def invoke(self, messages, **kwargs):
        return self._stub.invoke(messages, **kwargs)


def judge_tool_execution(segment: list[Any], tool_name: str = "line_contingency") -> dict[str, Any]:
    """This request's AI tool call and its ToolMessage; a guard rejection or Tool error is not an execution."""
    from langchain_core.messages import AIMessage, ToolMessage

    calls = [call for message in segment if isinstance(message, AIMessage) for call in message.tool_calls]
    answers = {message.tool_call_id: message for message in segment if isinstance(message, ToolMessage)}
    call = calls[0] if len(calls) == 1 else None
    message = answers.get(call.get("id")) if call else None
    if not calls:
        problem = "TOOL_CALL_MISSING"
    elif call is None:
        problem = "MULTIPLE_TOOL_CALLS"
    elif call.get("name") != tool_name:
        problem = "TOOL_NAME_MISMATCH"
    elif message is None:
        problem = "TOOL_MESSAGE_MISSING"
    elif message.name != tool_name:
        problem = "TOOL_MESSAGE_NAME_MISMATCH"
    elif message.status != "success":
        problem = "TOOL_NOT_EXECUTED"  # e.g. "Rejected before execution" from the guard
    else:
        problem = None
    return {"executed": problem is None, "problem": problem, "call": calls[0] if calls else None,
            "tool_call_id": call.get("id") if call else None}


def judge_initial_revision(before: str | None, after: str | None, envelope: dict[str, Any], *,
                           registered_before: int, registered_after: int,
                           tool_executed: bool) -> tuple[str, str, dict[str, Any]]:
    """Scenario 8 from the model revision actually captured around the first run.

    changed + failed/MODEL_REVISION_CHANGED with nothing published -> PASS (fail-closed);
    unchanged + published receipts at the current revision -> PASS; changed + any result
    accepted -> FAIL; missing evidence never passes.
    """
    analysis = envelope.get("analysis") or {}
    status, reason = analysis.get("status"), analysis.get("reason_code")
    results = analysis.get("results") or []
    metrics = {"revision_before": before[:12] if before else None, "revision_after": after[:12] if after else None,
               "analysis_status": status, "reason_code": reason}
    if not tool_executed:
        return "SKIP", "Tool이 실행되지 않아 revision 처리를 판정할 수 없음 (PASS 아님, 시나리오 4 참조)", metrics
    if before is None or after is None:
        return "FAIL", "판정 근거 부족: 실행 전후 모델 revision을 식별하지 못함", metrics
    changed = before != after
    metrics["revision_changed"] = changed
    published = (bool(results) or bool(envelope.get("ui_updates")) or registered_after != registered_before
                 or status in ("completed", "not_evaluated"))
    if changed:
        if published:
            return "FAIL", f"revision이 바뀌었는데 결과를 수용함 (status={status}, 등록 {registered_after - registered_before}건)", metrics
        if status == "failed" and reason == "MODEL_REVISION_CHANGED":
            return "PASS", "최초 AC가 모델 상태를 바꿈 → MODEL_REVISION_CHANGED로 결과 미공개 (fail-closed)", metrics
        return "FAIL", f"revision 변경 후 규정 외 응답 (status={status}, reason={reason})", metrics
    if reason == "MODEL_REVISION_CHANGED":
        return "FAIL", "revision 변화가 관측되지 않았는데 MODEL_REVISION_CHANGED 응답", metrics
    if (status in ("completed", "not_evaluated") and results
            and all(receipt.get("model_revision") == after for receipt in results)
            and registered_after - registered_before == len(results)):
        return "PASS", "revision 변화 없음 → 결과 공개, receipt revision이 현재 모델과 일치", metrics
    return "FAIL", f"revision 변화가 없는데 정상 공개 응답이 아님 (status={status}, reason={reason})", metrics


def verify_published_evidence(envelope: dict[str, Any], segment: list[Any], registry: Any, *,
                              operation: str = "security") -> list[str]:
    """Scenario 6 on server-contract evidence: response, this request's history and Registry evidence."""
    from langchain_core.messages import AIMessage, ToolMessage

    analysis = envelope.get("analysis") or {}
    receipts = analysis.get("results") or []
    updates = envelope.get("ui_updates") or []
    if analysis.get("status") not in ("completed", "not_evaluated") or analysis.get("source") != "tool_execution" or not receipts:
        return ["NOT_PUBLISHED"]
    problems = []
    if len(updates) != len(receipts) or any(
            update.get("result_id") != receipt.get("result_id") or update.get("type") != receipt.get("kind")
            or update.get("grid_id") != KPG_GRID_ID for update, receipt in zip(updates, receipts)):
        problems.append("UI_UPDATE_MISMATCH")
    calls = {call.get("id"): call for message in segment if isinstance(message, AIMessage) for call in message.tool_calls}
    answers = {message.tool_call_id: message for message in segment if isinstance(message, ToolMessage)}
    if set(calls) != {receipt.get("tool_call_id") for receipt in receipts}:
        problems.append("TOOL_CALL_RECEIPT_MISMATCH")
    jobs, executions = set(), []
    for receipt, update in zip(receipts, updates):
        tool = TOOL_BY_KIND.get(receipt.get("kind"))
        call, answer = calls.get(receipt.get("tool_call_id")), answers.get(receipt.get("tool_call_id"))
        if call is None or call.get("name") != tool:
            problems.append("TOOL_CALL_NOT_IN_REQUEST")
        if answer is None or answer.name != tool or answer.status != "success":
            problems.append("TOOL_MESSAGE_MISSING")
        entry = registry.lookup(receipt.get("result_id"))
        if entry is None:
            problems.append("RECEIPT_NOT_REGISTERED")
            continue
        evidence = json.loads(entry.evidence_json)
        if set(evidence) != {"job_id", "execution_id", "artifact_sha256", "operation"}:
            problems.append("EVIDENCE_SHAPE")
        if evidence.get("operation") != operation:
            problems.append("EVIDENCE_OPERATION_MISMATCH")
        try:
            uuid.UUID(str(evidence.get("execution_id")))
        except ValueError:
            problems.append("EXECUTION_ID_INVALID")
        if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("artifact_sha256"))):
            problems.append("ARTIFACT_DIGEST_INVALID")
        if entry.payload() != update.get("result"):
            problems.append("PAYLOAD_MISMATCH")
        jobs.add(evidence.get("job_id"))
        executions.append(evidence.get("execution_id"))
    if len(jobs) > 1:
        problems.append("JOB_MISMATCH")
    if len(set(executions)) != len(executions):
        problems.append("EXECUTION_ID_REUSED")
    return list(dict.fromkeys(problems))


def verify_receipts(envelope: dict[str, Any], registry: Any, *, session_id: str, request_id: str,
                    current_revision: str | None, outage_line_id: str = SAMPLE_OUTAGE_LINE) -> list[str]:
    """Scenario 7: agent_tool receipts of THIS request, registered as returned and owned by this session."""
    receipts = (envelope.get("analysis") or {}).get("results") or []
    if not receipts:
        return ["RECEIPT_MISSING"]
    problems = []
    for receipt in receipts:
        if receipt.get("producer") != "agent_tool":
            problems.append("PRODUCER_MISMATCH")
        if receipt.get("producer_request_id") != request_id:
            problems.append("REQUEST_ID_MISMATCH")
        if not receipt.get("tool_call_id") or not receipt.get("result_id"):
            problems.append("RECEIPT_ID_MISSING")
        if receipt.get("grid_id") != KPG_GRID_ID or receipt.get("outage_line_id") != outage_line_id:
            problems.append("CONDITION_MISMATCH")
        if current_revision is None or receipt.get("model_revision") != current_revision:
            problems.append("MODEL_REVISION_MISMATCH")
        entry = registry.lookup(receipt.get("result_id"))
        if entry is None:
            problems.append("RECEIPT_NOT_REGISTERED")
            continue
        if entry.receipt.as_dict() != receipt:
            problems.append("RECEIPT_REGISTRY_MISMATCH")
        if entry.session_id != session_id:
            problems.append("RECEIPT_OWNER_MISMATCH")
    return list(dict.fromkeys(problems))


def _scenario(scenario_id: int, status: str, detail: str, duration: float = 0.0, **metrics: Any) -> ScenarioResult:
    return ScenarioResult(scenario_id, SCENARIO_NAMES[scenario_id], status, duration, detail, metrics)


def _skip_remaining(scenarios: list[ScenarioResult], reason: str) -> None:
    done = {scenario.scenario_id for scenario in scenarios}
    scenarios.extend(_scenario(sid, "SKIP", reason) for sid in SCENARIO_NAMES if sid not in done)


def _current_revision(network: Any) -> str | None:
    from ai_ems.network_access import access_for

    try:
        return access_for(network).capture_revision().revision
    except Exception:  # unidentified model, busy guard or a non-native object: no evidence
        return None


def _error_code(response: Any) -> str:
    """Fixed server code only (v1 detail.code or the legacy string); never a body excerpt."""
    try:
        detail = response.json().get("detail")
    except Exception:
        return "응답 본문 해석 불가"
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        return detail["code"]
    return detail if isinstance(detail, str) and len(detail) <= 40 else "detail 생략"


def _run_body(session_id: str, request_id: str, revision: int, message: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "request_id": request_id,
        "message": message,
        "analysis_context": {
            "version": 1,
            "view_id": str(uuid.uuid4()),
            "revision": revision,
            "mode": "run",
            "operation": "security",
            "outage_line_id": SAMPLE_OUTAGE_LINE,
            "monitored_line_id": None,
            "references": {"security": None, "sensitivity": None},
        },
    }


def run_smoke_test(
    mode: str = "dry-run",
    model_name: str | None = None,
    base_url: str | None = None,
    mock: bool | None = None,
    mock_bad_line: bool = False,
    root: Path = ROOT,
) -> SmokeReport:
    """Smoke Test 전체 시나리오를 실행하고 결과를 수집합니다.

    모드는 ``mode``로만 정한다. ``mock``은 호환용 인자이며 주어지면 ``mode == "mock"``과 같아야 한다.
    잘못된 mode·조합은 연결·계산 전에 ValueError로 거부한다. 보고서의 문자열에서는 설정된 인증 값을 가린다.
    """
    if mode not in MODES:
        raise ValueError(f"지원하지 않는 mode입니다: {mode!r} (dry-run·mock·execute 중 하나)")
    if mock is not None and (not isinstance(mock, bool) or mock != (mode == "mock")):
        raise ValueError("mode와 mock 인자가 일치하지 않습니다. 모드는 mode 하나로 지정하세요.")
    if mock_bad_line and mode != "mock":
        raise ValueError("mock_bad_line은 mock 모드에서만 사용할 수 있습니다.")

    started_at = datetime.now(timezone.utc).isoformat()
    t_global_start = time.monotonic()
    config = resolve_configuration(root, argparse.Namespace(model=model_name, base_url=base_url))
    if mode == "execute":
        validate_execute_configuration(config["base_url"], config["merged_env"])

    scenarios: list[ScenarioResult] = []
    if mode == "dry-run":
        notes = ["Dry Run: 외부 연결·LLM 추론·서버 기동·계통 계산을 수행하지 않음"]
        prereqs_ok = _plan_scenarios(config, scenarios)
    elif mode == "mock":
        notes = ["LLM: ScriptedSmokeModel 고정 응답. 실제 Ollama·Caddy·Tailscale에 접속하지 않음",
                 "계통 계산: 이 프로세스에 격리 적재한 KPG-193으로 로컬 PyPowSyBl 계산을 실제 수행함 (시나리오 3~9)",
                 "Mock 결과는 실제 LLM Smoke Test 결과가 아님"]
        _run_scenarios(config, scenarios, mock=True, mock_bad_line=mock_bad_line)
    else:
        notes = ["LLM: 실제 provider 호출. 시나리오 1·2를 통과한 경우에만 Agent Run을 시작함",
                 "계통 계산: 이 프로세스에 격리 적재한 KPG-193 (실행 중 서버·세션·Registry와 분리)"]
        _run_scenarios(config, scenarios, mock=False, mock_bad_line=False)

    scenarios.sort(key=lambda scenario: scenario.scenario_id)
    counts = {key: sum(1 for s in scenarios if s.status == key.upper()) for key in ("pass", "fail", "warn", "skip")}
    if mode == "dry-run":
        overall = "DRY_RUN" if prereqs_ok else "WARN"
        counts["warn"] = 0 if prereqs_ok else 1
    else:
        # A SKIP is never a pass: only ten PASS results make the run PASS.
        overall = "FAIL" if counts["fail"] else ("PASS" if counts["pass"] == len(SCENARIO_NAMES) else "WARN")
    secrets = config["secrets"]
    return SmokeReport(
        mode=mode,
        target_model=config["model_name"],
        target_base_url="(mock: 외부 LLM 미접속)" if mode == "mock" else display_url(config["base_url"]),
        auth_summary="(mock: 인증 미사용)" if mode == "mock" else config["auth_summary"],
        overall_status=overall,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
        total_duration_seconds=time.monotonic() - t_global_start,
        scenarios=[ScenarioResult(s.scenario_id, s.name, s.status, s.duration_seconds, redact(s.detail, secrets),
                                  redact(s.metrics, secrets)) for s in scenarios],
        summary=counts,
        notes=redact(notes, secrets),
    )


def _plan_scenarios(config: dict[str, Any], scenarios: list[ScenarioResult]) -> bool:
    """Dry Run: register the ten planned scenarios as SKIP; nothing is contacted or computed."""
    prereqs_ok = all(ok for _, ok, _ in check_prerequisites(config))
    plan = {
        1: "사전 계획 등록 (GET /api/tags 연결 및 지정 모델 존재 검사)",
        2: "사전 계획 등록 (bind_tools 함수 호출 1개·이름·guard 인자 검증)",
        3: f"사전 계획 등록 ({SAMPLE_OUTAGE_LINE} N-1 요청)",
        4: "사전 계획 등록 (이번 요청의 성공 ToolMessage 확인)",
        5: "사전 계획 등록 (선로 ID 일치 및 Guard 인가)",
        6: "사전 계획 등록 (응답·세션 이력·Registry evidence 대조)",
        7: "사전 계획 등록 (agent_tool 영수증·세션 소유·revision)",
        8: "사전 계획 등록 (실제 revision 전후 비교: fail-closed 또는 정상 공개)",
        9: "사전 계획 등록 (수렴 후 재요청 완료)",
        10: "사전 계획 등록 (RunBudget 대비 지연 실측)",
    }
    scenarios.extend(_scenario(sid, "SKIP", detail, planned=True) for sid, detail in plan.items())
    return prereqs_ok


def _run_scenarios(config: dict[str, Any], scenarios: list[ScenarioResult], *, mock: bool, mock_bad_line: bool) -> None:
    """Scenarios 1-10 for execute and mock. Every failure stops further real LLM calls and calculation."""
    from langchain_core.messages import AIMessage

    model, base_url, auth_env = config["model_name"], config["base_url"], config["auth_env"]
    source = "mock: scripted LLM + local PyPowSyBl" if mock else "real LLM + isolated local PyPowSyBl"

    # 1. LLM 연결 (+ 지정 모델 존재)
    t0 = time.monotonic()
    if mock:
        s1 = {"status": "PASS", "code": "mock", "latency_ms": 0.0, "models": [model],
              "detail": "[MOCK] 연결 점검 생략 — 실제 Ollama·Caddy에 접속하지 않음"}
    else:
        s1 = probe_llm_connection(base_url, auth_env)
        if s1["status"] == "PASS" and not model_listed(model, s1.get("models", [])):
            s1 = {**s1, "status": "FAIL", "code": "model_not_found",
                  "detail": f"지정 모델이 /api/tags 목록에 없음 (설정: {model}, 목록 {len(s1.get('models', []))}개) — 추론하지 않음"}
    scenarios.append(_scenario(1, s1["status"], s1["detail"], time.monotonic() - t0,
                               latency_ms=s1.get("latency_ms", 0.0), code=s1.get("code"), simulated=mock))
    if s1["status"] != "PASS":
        return _skip_remaining(scenarios, "LLM 연결·모델 확인 실패로 후속 호출 및 계통 계산을 차단함")

    # 2. Tool Calling (fail-closed: anything but PASS stops the real run)
    s2 = probe_tool_calling_support(model, base_url, auth_env, mock=mock, timeout=30.0)
    scenarios.append(_scenario(2, s2["status"], s2["detail"], s2["duration"], code=s2.get("code"),
                               tool_calls_count=len(s2.get("tool_calls", [])), simulated=mock))
    if s2["status"] != "PASS":
        return _skip_remaining(scenarios, "Tool Calling 점검 실패로 실제 Agent Run과 계통 계산을 차단함")

    case_path = config["case_path"]
    if not case_path.is_file():
        scenarios.append(_scenario(3, "FAIL", f"계통 파일 없음: {case_path.name}"))
        return _skip_remaining(scenarios, "계통 파일이 없어 Run을 실행하지 않음")

    from ai_ems.analysis_run import run_budget
    from ai_ems.network import load_identified_network

    isolated_network = load_identified_network(case_path)
    if mock:
        called_line = "LINE-99-99" if mock_bad_line else SAMPLE_OUTAGE_LINE
        stub = ScriptedSmokeModel([
            AIMessage(content="", tool_calls=[{"name": "line_contingency", "args": {"outage_line_id": called_line}, "id": "mock-tool-run-1"}]),
            AIMessage(content="", tool_calls=[{"name": "line_contingency", "args": {"outage_line_id": SAMPLE_OUTAGE_LINE}, "id": "mock-tool-run-2"}]),
        ])
        model_factory = lambda budget: stub._stub  # noqa: E731
    else:
        def model_factory(budget):
            from ai_ems.agent.graph import _chat_model
            return _chat_model(model, base_url, timeout=budget.llm_timeout)

    session_id = f"smoke-sess-{uuid.uuid4().hex[:12]}"
    run1_dur = run2_dur = None
    with isolated_app_environment(isolated_network, run_model_factory=model_factory,
                                  auth_env=None if mock else auth_env) as (client, registry, _):
        if not mock and not agent_auth_matches(model, base_url, auth_env):
            scenarios.append(_scenario(3, "FAIL", "실제 Agent 인증 헤더가 연결 점검과 달라 Run을 시작하지 않음"))
            return _skip_remaining(scenarios, "인증 설정 불일치로 실제 Run을 차단함")
        import web_app

        budget = run_budget()

        def history() -> list[Any]:
            return list(web_app.agent_sessions.get(session_id, []))

        # 3. 첫 Security Run (새로 적재한 KPG: 최초 AC)
        req_id_1 = str(uuid.uuid4())
        rev_0, mark = _current_revision(isolated_network), len(history())
        t_run = time.monotonic()
        resp_1 = client.post(f"/api/grids/{KPG_GRID_ID}/chat", json=_run_body(
            session_id, req_id_1, 0, f"{SAMPLE_OUTAGE_LINE} 선로 상정사고(N-1) 안전도 해석을 수행해 줘."))
        run1_dur = time.monotonic() - t_run
        rev_1, segment_1 = _current_revision(isolated_network), history()[mark:]
        try:
            data_1 = resp_1.json() if resp_1.status_code == 200 else None
        except ValueError:
            data_1 = None
        analysis_1 = (data_1 or {}).get("analysis")
        if not isinstance(analysis_1, dict):
            scenarios.append(_scenario(3, "FAIL", f"HTTP {resp_1.status_code} ({_error_code(resp_1)})", run1_dur,
                                       http_status=resp_1.status_code, request_id=req_id_1, source=source))
            return _skip_remaining(scenarios, "Security Run 실패로 후속 판정과 재요청을 생략함")
        status_1, reason_1 = analysis_1.get("status"), analysis_1.get("reason_code")
        scenarios.append(_scenario(3, "PASS", f"HTTP 200 응답 (status={status_1}, reason={reason_1})", run1_dur,
                                   http_status=200, request_id=req_id_1, source=source))

        # 4. 실제 Tool 실행 (guard 거부·Tool 오류는 실행이 아님)
        execution = judge_tool_execution(segment_1)
        scenarios.append(_scenario(
            4, "PASS" if execution["executed"] else "FAIL",
            f"line_contingency 성공 ToolMessage 확인 (status={status_1}, reason={reason_1})" if execution["executed"]
            else f"도구 실행 미확인: {execution['problem']} (status={status_1}, reason={reason_1})",
            tool_executed=execution["executed"], tool_call_id=execution["tool_call_id"], problem=execution["problem"]))

        # 5. 고정 선로 ID
        call = execution["call"] or {}
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        called_line = args.get("outage_line_id")
        line_ok = execution["executed"] and called_line == SAMPLE_OUTAGE_LINE and set(args) <= {"outage_line_id", "monitored_line_id"} \
            and args.get("monitored_line_id") is None
        scenarios.append(_scenario(
            5, "PASS" if line_ok else "FAIL",
            f"요청 선로({SAMPLE_OUTAGE_LINE}) 준수 및 Guard 인가" if line_ok
            else f"Guard 거부 또는 선로 불일치 (호출: {called_line}, 사유: {reason_1})",
            called_line=called_line, reason_code=reason_1))

        # 8. 최초 AC revision 처리 (실제 전후 revision 비교)
        s8_status, s8_detail, s8_metrics = judge_initial_revision(
            rev_0, rev_1, data_1, registered_before=0, registered_after=len(registry),
            tool_executed=execution["executed"])
        scenarios.append(_scenario(8, s8_status, s8_detail, **s8_metrics))

        published = None
        if status_1 in ("completed", "not_evaluated"):
            published = (data_1, segment_1, req_id_1, rev_1)

        # 9. 재요청: Run 1이 계약대로 끝난 경우에만 실제 LLM·계산을 한 번 더 호출한다.
        if execution["executed"] and line_ok and s8_status == "PASS":
            req_id_2 = str(uuid.uuid4())
            rev_b2, mark = _current_revision(isolated_network), len(history())
            t_run = time.monotonic()
            resp_2 = client.post(f"/api/grids/{KPG_GRID_ID}/chat", json=_run_body(
                session_id, req_id_2, 1, f"{SAMPLE_OUTAGE_LINE} 선로 상정사고(N-1) 안전도 해석을 재실행해 줘."))
            run2_dur = time.monotonic() - t_run
            rev_2, segment_2 = _current_revision(isolated_network), history()[mark:]
            try:
                data_2 = resp_2.json() if resp_2.status_code == 200 else {}
            except ValueError:
                data_2 = {}
            analysis_2 = data_2.get("analysis") or {}
            results_2 = analysis_2.get("results") or []
            run2_ok = (resp_2.status_code == 200 and analysis_2.get("status") == "completed" and bool(results_2)
                       and rev_2 is not None and rev_b2 == rev_2
                       and all(receipt.get("model_revision") == rev_2 for receipt in results_2))
            scenarios.append(_scenario(
                9, "PASS" if run2_ok else "FAIL",
                "재요청 정상 완료 (status=completed, revision 유지)" if run2_ok
                else f"재요청 실패 (HTTP {resp_2.status_code}, status={analysis_2.get('status')}, "
                     f"reason={analysis_2.get('reason_code') or _error_code(resp_2)}, revision 유지={rev_b2 == rev_2})",
                duration=run2_dur, run2_status=analysis_2.get("status"), request_id=req_id_2, source=source))
            if published is None and analysis_2.get("status") in ("completed", "not_evaluated"):
                published = (data_2, segment_2, req_id_2, rev_2)
        else:
            scenarios.append(_scenario(9, "SKIP", "Run 1이 계약대로 끝나지 않아 재요청을 생략함 (추가 LLM·계산 없음)"))

        # 6·7. 공개된 run의 Evidence와 Receipt
        if published is None:
            reason = "공개된 run 결과가 없어 판정할 수 없음 (PASS 아님, 앞선 실패 참조)"
            scenarios.append(_scenario(6, "SKIP", reason, limitations=list(EVIDENCE_LIMITS)))
            scenarios.append(_scenario(7, "SKIP", reason))
        else:
            envelope, segment, request_id, revision = published
            evidence_problems = verify_published_evidence(envelope, segment, registry)
            scenarios.append(_scenario(
                6, "FAIL" if evidence_problems else "PASS",
                f"검증 오류: {', '.join(evidence_problems)}" if evidence_problems
                else "응답·이번 요청 이력·Registry evidence 일치 (공개 증거 기준, Ledger 원본은 확인 범위 밖)",
                request_id=request_id, problems=evidence_problems, limitations=list(EVIDENCE_LIMITS),
                verified=["analysis.status·source", "ui_updates↔receipt", "AI tool_call ID↔성공 ToolMessage↔receipt",
                          "Registry evidence(job_id·execution_id·artifact_sha256 형식·operation)", "Registry payload↔ui_update"]))
            receipt_problems = verify_receipts(envelope, registry, session_id=session_id, request_id=request_id,
                                               current_revision=_current_revision(isolated_network))
            receipts = (envelope.get("analysis") or {}).get("results") or []
            scenarios.append(_scenario(
                7, "FAIL" if receipt_problems else "PASS",
                f"영수증 검증 오류: {', '.join(receipt_problems)}" if receipt_problems
                else f"영수증 발급 확인 (ID={receipts[0].get('result_id')})",
                receipt_id=receipts[0].get("result_id") if receipts else None, producer="agent_tool",
                problems=receipt_problems, request_id=request_id))

    # 10. 지연 시간과 RunBudget
    runs = [duration for duration in (run1_dur, run2_dur) if duration is not None]
    connect_latency_s = s1.get("latency_ms", 0.0) / 1000.0
    deadline_budget = budget.deadline_seconds
    max_run_dur = max(runs)
    headroom_s = deadline_budget - max_run_dur
    latency_ok = max_run_dur < deadline_budget and connect_latency_s < 5.0
    scenarios.append(_scenario(
        10, "PASS" if latency_ok else "WARN",
        f"최대 실행 {max_run_dur:.2f}s (예산 {deadline_budget:g}s 대비 여유 {headroom_s:.2f}s)", max_run_dur,
        connect_ms=s1.get("latency_ms", 0.0), tool_call_s=round(s2.get("duration", 0.0), 3),
        run1_s=round(run1_dur, 3), run2_s=round(run2_dur, 3) if run2_dur is not None else None,
        deadline_budget_s=deadline_budget, llm_read_timeout_s=budget.llm_timeout.read,
        headroom_s=round(headroom_s, 3), source=source))


def print_dry_run_preamble(config: dict[str, Any], prereqs: list[tuple[str, bool, str]]) -> None:
    """Dry Run 모드 실행 시 안전 안내 및 사전 점검 결과를 출력합니다."""
    print("=" * 80)
    print(" AI-EMS 실제 LLM Smoke Test — 사전 환경 점검 (Dry Run Mode)")
    print("=" * 80)
    print("본 도구는 AI-EMS의 실제 LLM Tool Calling 및 KPG Agent Run을 안전하게 검증하기 위한")
    print("독립 Smoke Test 실행기입니다. 현재는 [Dry Run] 모드로, 실제 LLM 추론·서버 기동·계통 계산을")
    print("일체 수행하지 않습니다.\n")

    print("[1] 감지된 실행 환경 및 설정:")
    print(f"  • 대상 모델 (Model):      {config['model_name']}")
    print(f"  • 대상 주소 (Base URL):   {display_url(config['base_url']) or '미설정'}")
    print(f"  • 인증 방식 (Auth Type):  {config['auth_type']} — {config['auth_summary']}")
    print(f"  • 계통 데이터 (Case File): {config['case_path']}")
    print()

    print("[2] 사전 요구사항 점검 결과:")
    for name, ok, desc in prereqs:
        mark = "[PASS]" if ok else "[FAIL]"
        print(f"  {mark:<6} {name:<35} : {desc}")
    print()

    print("[3] 격리 보장 및 영향 범위 (Blast Radius):")
    print("  • 기존 실행 중인 AI-EMS 서비스(포트 8000/8001)의 세션·레지스트리·계통 상태를 절대 변경하지 않습니다.")
    print("  • 독립된 Python 프로세스 메모리(TestClient)에서 KPG-193을 별도로 로드하여 실행합니다.")
    print("  • 고유한 임시 세션 ID와 요청 ID를 사용하며 테스트 종료 시 모든 리소스가 정리됩니다.")
    print("  • 단일 worker(AI_EMS_SINGLE_WORKER=1) 조건이 강제 적용됩니다.")
    print()

    print("[4] 준비된 10단계 검증 시나리오:")
    scenarios_desc = [
        "1. LLM 연결 정상 여부 (GET /api/tags HTTP 200, 지정 모델이 목록에 있음)",
        "2. 모델의 Tool Calling 지원 (도구 1개·이름·서버 guard 인자 규칙; 실패 시 실제 Run 중단)",
        "3. Security Run (KPG-193 선로 N-1 run 요청 수신 및 처리)",
        "4. 실제 Tool 호출 확인 (이번 요청의 성공 ToolMessage; guard 거부는 실행 아님)",
        "5. 고정 선로 ID 준수 여부 (요청 선로 ID 일치 및 Guard 인가 통과)",
        "6. Ledger 및 Evidence 검증 (응답·세션 이력·Registry evidence 대조; Ledger 원본은 확인 범위 밖)",
        "7. Receipt 발급 여부 (producer=agent_tool, 요청 ID, 세션 소유, 현재 revision)",
        "8. 최초 AC 계산 후 Model Revision 변화 처리 (실제 revision 전후 비교로 판정, 근거 부족 시 PASS 금지)",
        "9. 이후 재요청의 정상 완료 여부 (수렴 상태에서 후속 run 성공)",
        "10. 실제 응답 지연 시간 및 예산 기록 (Connect, Read, Total 지연 vs RunBudget 비교)",
    ]
    for desc in scenarios_desc:
        print(f"  • {desc}")
    print()

    print("=" * 80)
    print("실행 방법 안내:")
    print("  1. 실제 LLM Smoke Test를 실행하려면 사용자 승인 후 다음 옵션을 사용하세요:")
    print("     먼저 실제 HTTPS origin Base URL과 필요한 인증 환경변수(.env 또는 프로세스)를 설정하세요.")
    print("     프로젝트 Caddy 경로(Tailscale HTTPS → Caddy 18080)는 Bearer(AI_EMS_LLM_API_KEY)가 없거나 틀리면 401입니다.")
    print("     python scripts/smoke_test_llm.py --execute")
    print()
    print("  2. 인메모리 모의 검증(Mock Test)을 수행하려면 다음 옵션을 사용하세요:")
    print("     python scripts/smoke_test_llm.py --mock")
    print("     (Mock은 LLM만 고정 응답이며 Ollama·Caddy에 접속하지 않고, KPG-193 계산은 로컬에서 실제 수행합니다.)")
    print("=" * 80)


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AI-EMS 실제 LLM Tool Calling 독립 Smoke Test 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--execute",
        action="store_true",
        help="사용자 승인 후 실제 LLM Smoke Test 실행 (지정하지 않으면 안전한 Dry Run 수행)",
    )
    group.add_argument(
        "--mock",
        action="store_true",
        help="ScriptedModel을 사용한 인메모리 모의 검증 실행 (네트워크/외부 LLM 불필요)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=f"테스트 대상 모델명 (기본값: AI_EMS_MODEL 환경변수 또는 {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="테스트 대상 LLM Base URL (기본값: AI_EMS_LLM_BASE_URL 환경변수; 미설정 시 실제 실행 거부)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="결과를 JSON 형식으로 콘솔에 출력",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="결과 JSON 보고서를 저장할 파일 경로",
    )
    return parser


def main() -> int:
    parser = build_cli_parser()
    args = parser.parse_args()

    # 모드 결정
    if args.execute:
        mode = "execute"
    elif args.mock:
        mode = "mock"
    else:
        mode = "dry-run"

    # 설정 및 사전 점검
    config = resolve_configuration(ROOT, args)
    prereqs = check_prerequisites(config)

    if mode == "dry-run":
        print_dry_run_preamble(config, prereqs)
        report = run_smoke_test(mode="dry-run", model_name=config["model_name"], base_url=config["base_url"], root=ROOT)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"[보고서 저장 완료] {out_path}")
        return 0

    if mode == "execute":
        try:
            validate_execute_configuration(config["base_url"], config["merged_env"])
        except ValueError as exc:
            print(f"[설정 오류] {exc}", file=sys.stderr)
            return 2

        failed_prereqs = [(name, detail) for name, ok, detail in prereqs if not ok]
        if failed_prereqs:
            for name, detail in failed_prereqs:
                print(f"[사전 점검 실패] {name}: {detail}", file=sys.stderr)
            return 2

    # Execute 또는 Mock 실행
    report = run_smoke_test(
        mode=mode,
        model_name=config["model_name"],
        base_url=config["base_url"],
        mock=(mode == "mock"),
        root=ROOT,  # the same .env root as the configuration printed and validated above
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.format_console())

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n[보고서 저장 완료] {out_path}")

    return 0 if report.overall_status in ("PASS", "DRY_RUN") else 1


if __name__ == "__main__":
    sys.exit(main())
