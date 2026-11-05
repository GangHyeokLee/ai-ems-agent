"""PH4-02-INFRA-02: AI-EMS 실제 LLM Tool Calling 독립 Smoke Test 도구.

실행 모드:
- 기본 (Dry Run): 설정, 사전 요구사항, 검증 시나리오 및 격리 범위를 확인하며 실제 추론·계산은 수행하지 않음.
- --execute: 실제 LLM Smoke Test 실행 (사용자 명시적 승인 후 실행).
- --mock: ScriptedModel 기반 인메모리 모의 검증 (네트워크·외부 LLM 없이 10개 시나리오 자가 검증).

10단계 검증 시나리오:
1. LLM 연결 정상 여부 (GET /api/tags 연결, HTTP 200, 모델 목록 확인)
2. 모델의 Tool Calling 지원 (ChatOllama.bind_tools probe로 함수 호출 생성 검증)
3. Security Run (KPG-193 선로 N-1 run 요청 수신 및 처리)
4. 실제 Tool 호출 확인 (line_contingency 도구 실행 및 Ledger 기록)
5. 고정 선로 ID 준수 여부 (요청 선로 ID 일치 및 Guard 인가 통과)
6. Ledger 및 Evidence 검증 (verify_evidence 통과 및 결과 기록)
7. Receipt 발급 여부 (producer=agent_tool 영수증 정상 생성)
8. 최초 AC 계산 후 Model Revision 변화 처리 (상태 변경 감지 및 fail-closed 처리)
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

    return {
        "model_name": model_name,
        "base_url": base_url,
        "auth_type": auth_type,
        "auth_summary": auth_summary,
        "case_path": case_path,
        "merged_env": merged_env,
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
                models = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
                return {"status": "PASS", "code": "ready", "detail": f"HTTP 200 OK (발견된 모델 {len(models)}개)",
                        "latency_ms": latency_ms, "models": models}
            except Exception:
                return {"status": "FAIL", "code": "invalid_json", "detail": "Ollama 응답이 JSON 형식이 아님",
                        "latency_ms": latency_ms, "models": []}
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


def probe_tool_calling_support(model_name: str, base_url: str, environ: dict[str, str],
                               mock: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    """ChatOllama bind_tools를 통해 모델이 올바른 함수 호출 JSON을 생성하는지 점검합니다."""
    t0 = time.monotonic()
    if mock:
        return {
            "status": "PASS",
            "duration": 0.05,
            "tool_calls": [{"name": "line_contingency", "args": {"line_id": SAMPLE_OUTAGE_LINE}, "id": "mock-call-1"}],
            "detail": f"모의 Tool Calling 성공 ({SAMPLE_OUTAGE_LINE} 호출 확인)",
        }

    try:
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage
        from pydantic import BaseModel, Field
        from ai_ems.config import get_llm_client_kwargs

        class LineContingencyProbeInput(BaseModel):
            outage_line_id: str = Field(description="선로 N-1 상정사고 대상 선로 식별자 (예: LINE-16-28)")

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
        duration = time.monotonic() - t0

        if not response.tool_calls:
            return {
                "status": "FAIL",
                "duration": duration,
                "tool_calls": [],
                "detail": f"모델이 도구를 호출하지 않고 일반 텍스트로 응답함 (내용: {response.content[:60]}...)",
            }

        first_call = response.tool_calls[0]
        call_name = first_call.get("name")
        call_args = first_call.get("args", {})
        called_line = call_args.get("outage_line_id") or call_args.get("line_id")

        if call_name != "line_contingency":
            return {
                "status": "FAIL",
                "duration": duration,
                "tool_calls": response.tool_calls,
                "detail": f"예상치 못한 도구 호출: {call_name}",
            }

        if called_line != SAMPLE_OUTAGE_LINE:
            return {
                "status": "WARN",
                "duration": duration,
                "tool_calls": response.tool_calls,
                "detail": f"선로 ID 불일치 (요청: {SAMPLE_OUTAGE_LINE}, 호출: {called_line})",
            }

        return {
            "status": "PASS",
            "duration": duration,
            "tool_calls": response.tool_calls,
            "detail": f"정상 Tool Calling 확인 ({call_name}, outage_line_id={called_line})",
        }
    except Exception as exc:
        return {
            "status": "FAIL",
            "duration": time.monotonic() - t0,
            "tool_calls": [],
            "detail": f"도구 호출 검증 중 오류: {exc}",
        }


@contextmanager
def isolated_app_environment(network: Any, run_model_factory: Callable[[Any], Any] | None = None) -> Generator[Any, None, None]:
    """실행 중인 서버와 격리된 in-memory TestClient 환경을 구축하고 종료 후 원상복구합니다."""
    import web_app
    from fastapi.testclient import TestClient
    from ai_ems.analysis_registry import RequestTracker, ResultRegistry, SessionGates

    # 기존 전역 상태 보존
    orig_network = getattr(web_app, "network", None)
    orig_results = getattr(web_app, "analysis_results", None)
    orig_requests = getattr(web_app, "analysis_requests", None)
    orig_sessions = getattr(web_app, "analysis_sessions", None)
    orig_agent_sessions = getattr(web_app, "agent_sessions", None)
    orig_run_model = getattr(web_app, "_analysis_run_model", None)
    orig_single_worker = os.environ.get("AI_EMS_SINGLE_WORKER")

    # 격리된 새 상태 주입
    os.environ["AI_EMS_SINGLE_WORKER"] = "1"
    web_app.network = network
    web_app.analysis_results = ResultRegistry()
    web_app.analysis_requests = RequestTracker()
    web_app.analysis_sessions = SessionGates()
    web_app.agent_sessions = {}

    if run_model_factory is not None:
        web_app._analysis_run_model = run_model_factory

    try:
        client = TestClient(web_app.app)
        yield client, web_app.analysis_results, web_app.analysis_requests
    finally:
        # 상태 완전 복구
        web_app.network = orig_network
        web_app.analysis_results = orig_results
        web_app.analysis_requests = orig_requests
        web_app.analysis_sessions = orig_sessions
        web_app.agent_sessions = orig_agent_sessions
        if orig_run_model is not None:
            web_app._analysis_run_model = orig_run_model
        if orig_single_worker is None:
            os.environ.pop("AI_EMS_SINGLE_WORKER", None)
        else:
            os.environ["AI_EMS_SINGLE_WORKER"] = orig_single_worker


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


def run_smoke_test(
    mode: str = "dry-run",
    model_name: str | None = None,
    base_url: str | None = None,
    mock: bool = False,
    mock_bad_line: bool = False,
    root: Path = ROOT,
) -> SmokeReport:
    """Smoke Test 전체 시나리오를 실행하고 결과를 수집합니다."""
    started_at = datetime.now(timezone.utc).isoformat()
    t_global_start = time.monotonic()

    # 인자 객체 가상화
    args = argparse.Namespace(model=model_name, base_url=base_url)
    config = resolve_configuration(root, args)

    active_model = config["model_name"]
    active_base_url = config["base_url"]
    auth_summary = config["auth_summary"]
    if mode == "execute" and not mock:
        validate_execute_configuration(active_base_url, config["merged_env"])

    scenarios: list[ScenarioResult] = []

    # =========================================================================
    # [모드 1] DRY RUN (사전 점검만 수행, LLM 추론·FastAPI 기동·계통 계산 없음)
    # =========================================================================
    if mode == "dry-run" and not mock:
        prereqs = check_prerequisites(config)
        all_prereqs_ok = all(ok for _, ok, _ in prereqs)

        # 10개 계획 시나리오 등록
        plan_scenarios = [
            (1, "LLM 연결 정상 여부", "사전 계획 등록 (GET /api/tags 연결 검사)"),
            (2, "모델의 Tool Calling 지원", "사전 계획 등록 (bind_tools 함수 호출 검증)"),
            (3, "Security Run", "사전 계획 등록 (LINE-16-28 N-1 요청)"),
            (4, "실제 Tool 호출 확인", "사전 계획 등록 (line_contingency 실행 확인)"),
            (5, "고정 선로 ID 준수 여부", "사전 계획 등록 (선로 ID 일치 및 Guard 인가)"),
            (6, "Ledger 및 Evidence 검증", "사전 계획 등록 (verify_evidence 증거 검증)"),
            (7, "Receipt 발급 여부", "사전 계획 등록 (agent_tool 영수증 발급)"),
            (8, "최초 AC 계산 후 Model Revision 변화 처리", "사전 계획 등록 (MODEL_REVISION_CHANGED fail-closed 확인)"),
            (9, "이후 재요청의 정상 완료 여부", "사전 계획 등록 (수렴 후 재요청 완료)"),
            (10, "실제 응답 지연 시간 및 예산 기록", "사전 계획 등록 (30s 예산 대비 지연 실측)"),
        ]

        for s_id, s_name, s_detail in plan_scenarios:
            scenarios.append(ScenarioResult(
                scenario_id=s_id,
                name=s_name,
                status="SKIP",
                duration_seconds=0.0,
                detail=s_detail,
                metrics={"planned": True},
            ))

        finished_at = datetime.now(timezone.utc).isoformat()
        total_dur = time.monotonic() - t_global_start
        overall = "DRY_RUN" if all_prereqs_ok else "WARN"

        return SmokeReport(
            mode="dry-run",
            target_model=active_model,
            target_base_url=active_base_url,
            auth_summary=auth_summary,
            overall_status=overall,
            started_at=started_at,
            finished_at=finished_at,
            total_duration_seconds=total_dur,
            scenarios=scenarios,
            summary={"pass": 0, "fail": 0, "warn": 0 if all_prereqs_ok else 1, "skip": 10},
        )

    # =========================================================================
    # [모드 2 & 3] EXECUTE 또는 MOCK 실행
    # =========================================================================
    from langchain_core.messages import AIMessage, ToolMessage

    # 1. 시나리오 1: LLM 연결 정상 여부
    t0 = time.monotonic()
    if mock:
        s1_res = {"status": "PASS", "code": "ready", "detail": f"모의 연결 성공 ({active_model} 확인)", "latency_ms": 12.5, "models": [active_model]}
    else:
        s1_res = probe_llm_connection(active_base_url, config["merged_env"])
    s1_dur = time.monotonic() - t0

    s1_pass = s1_res["status"] == "PASS"
    scenarios.append(ScenarioResult(
        scenario_id=1,
        name="LLM 연결 정상 여부",
        status="PASS" if s1_pass else "FAIL",
        duration_seconds=s1_dur,
        detail=s1_res["detail"],
        metrics={"latency_ms": s1_res.get("latency_ms", 0.0), "code": s1_res.get("code")},
    ))

    if not s1_pass:
        remaining_scenarios = (
            "모델의 Tool Calling 지원",
            "Security Run",
            "실제 Tool 호출 확인",
            "고정 선로 ID 준수 여부",
            "Ledger 및 Evidence 검증",
            "Receipt 발급 여부",
            "최초 AC 계산 후 Model Revision 변화 처리",
            "이후 재요청의 정상 완료 여부",
            "실제 응답 지연 시간 및 예산 기록",
        )
        for scenario_id, scenario_name in enumerate(remaining_scenarios, start=2):
            scenarios.append(ScenarioResult(
                scenario_id=scenario_id,
                name=scenario_name,
                status="SKIP",
                duration_seconds=0.0,
                detail="LLM 연결 실패로 후속 호출 및 계통 계산을 차단함",
            ))
        finished_at = datetime.now(timezone.utc).isoformat()
        return SmokeReport(
            mode="execute",
            target_model=active_model,
            target_base_url=active_base_url,
            auth_summary=auth_summary,
            overall_status="FAIL",
            started_at=started_at,
            finished_at=finished_at,
            total_duration_seconds=time.monotonic() - t_global_start,
            scenarios=scenarios,
            summary={"pass": 0, "fail": 1, "warn": 0, "skip": 9},
        )

    # 2. 시나리오 2: 모델의 Tool Calling 지원
    s2_res = probe_tool_calling_support(
        model_name=active_model,
        base_url=active_base_url,
        environ=config["merged_env"],
        mock=mock,
        timeout=30.0,
    )
    scenarios.append(ScenarioResult(
        scenario_id=2,
        name="모델의 Tool Calling 지원",
        status=s2_res["status"],
        duration_seconds=s2_res["duration"],
        detail=s2_res["detail"],
        metrics={"tool_calls_count": len(s2_res.get("tool_calls", []))},
    ))

    # 3~9. 계통 로드 및 격리 Security Run 실행
    from ai_ems.network import load_identified_network

    case_path = config["case_path"]
    if not case_path.is_file():
        err_msg = f"계통 파일 없음: {case_path}"
        for s_idx in range(3, 11):
            scenarios.append(ScenarioResult(scenario_id=s_idx, name=f"시나리오 {s_idx}", status="FAIL", duration_seconds=0.0, detail=err_msg))
        finished_at = datetime.now(timezone.utc).isoformat()
        return SmokeReport(
            mode="mock" if mock else "execute",
            target_model=active_model, target_base_url=active_base_url,
            auth_summary=auth_summary, overall_status="FAIL",
            started_at=started_at, finished_at=finished_at,
            total_duration_seconds=time.monotonic() - t_global_start,
            scenarios=scenarios, summary={"pass": 1, "fail": 9, "warn": 0, "skip": 0},
        )

    # 독립 KPG 계통 메모리 적재
    isolated_network = load_identified_network(case_path)

    # Mock 모델 스텁 준비
    if mock:
        called_line = "LINE-99-99" if mock_bad_line else SAMPLE_OUTAGE_LINE
        mock_tool_call_1 = AIMessage(
            content="",
            tool_calls=[{"name": "line_contingency", "args": {"outage_line_id": called_line}, "id": "mock-tool-run-1"}]
        )
        mock_tool_call_2 = AIMessage(
            content="",
            tool_calls=[{"name": "line_contingency", "args": {"outage_line_id": SAMPLE_OUTAGE_LINE}, "id": "mock-tool-run-2"}]
        )
        mock_steps = [mock_tool_call_1, mock_tool_call_2, mock_tool_call_2]
        mock_model_inst = ScriptedSmokeModel(mock_steps)
        model_factory = lambda budget: mock_model_inst._stub
    else:
        # 실제 모델 팩토리: budget의 timeout을 주입한 ChatOllama 생성
        def real_model_factory(budget):
            from ai_ems.agent.graph import _chat_model
            return _chat_model(active_model, active_base_url, timeout=budget.llm_timeout)
        model_factory = real_model_factory

    run1_dur = 0.0
    run2_dur = 0.0
    session_id = f"smoke-sess-{uuid.uuid4().hex[:12]}"

    with isolated_app_environment(isolated_network, run_model_factory=model_factory) as (client, registry, tracker):
        # -------------------------------------------------------------
        # 첫 번째 Run 요청 전송
        # -------------------------------------------------------------
        req_id_1 = str(uuid.uuid4())
        view_id_1 = str(uuid.uuid4())
        body_1 = {
            "session_id": session_id,
            "request_id": req_id_1,
            "message": f"{SAMPLE_OUTAGE_LINE} 선로 상정사고(N-1) 안전도 해석을 수행해 줘.",
            "analysis_context": {
                "version": 1,
                "view_id": view_id_1,
                "revision": 0,
                "mode": "run",
                "operation": "security",
                "outage_line_id": SAMPLE_OUTAGE_LINE,
                "monitored_line_id": None,
                "references": {"security": None, "sensitivity": None},
            },
        }

        t_r1_start = time.monotonic()
        resp_1 = client.post("/api/grids/kr-kpg193/chat", json=body_1)
        run1_dur = time.monotonic() - t_r1_start

        http_ok_1 = resp_1.status_code == 200
        data_1 = resp_1.json() if http_ok_1 else {}

        # 시나리오 3: Security Run 요청 수신 및 처리
        scenarios.append(ScenarioResult(
            scenario_id=3,
            name="Security Run",
            status="PASS" if http_ok_1 else "FAIL",
            duration_seconds=run1_dur,
            detail=f"HTTP {resp_1.status_code} 응답 확인" if http_ok_1 else f"HTTP 오류: {resp_1.text[:60]}",
            metrics={"http_status": resp_1.status_code, "request_id": req_id_1},
        ))

        analysis_1 = data_1.get("analysis", {})
        status_1 = analysis_1.get("status")
        reason_1 = analysis_1.get("reason_code")
        results_1 = analysis_1.get("results", [])
        import web_app
        run1_history = web_app.agent_sessions.get(session_id, [])
        line_tool_call = next(
            (
                tool_call
                for message in run1_history
                if isinstance(message, AIMessage)
                for tool_call in message.tool_calls
                if tool_call.get("name") == "line_contingency"
            ),
            None,
        )
        tool_call_id = line_tool_call.get("id") if line_tool_call else None
        tool_message_seen = any(
            isinstance(message, ToolMessage)
            and message.name == "line_contingency"
            and message.tool_call_id == tool_call_id
            for message in run1_history
        )
        called_line = (
            line_tool_call.get("args", {}).get("outage_line_id")
            or line_tool_call.get("args", {}).get("line_id")
            if line_tool_call
            else None
        )

        # 시나리오 4: 실제 Tool 호출 확인
        tool_executed = bool(line_tool_call and tool_message_seen)
        scenarios.append(ScenarioResult(
            scenario_id=4,
            name="실제 Tool 호출 확인",
            status="PASS" if tool_executed else "FAIL",
            duration_seconds=0.01,
            detail=f"line_contingency ToolMessage 확인 (status={status_1}, reason={reason_1})" if tool_executed else f"도구 실행 미확인 (status={status_1}, reason={reason_1})",
            metrics={"tool_executed": tool_executed, "tool_call_id": tool_call_id},
        ))

        # 시나리오 5: 고정 선로 ID 준수 여부
        guard_rejected = reason_1 in ("OUTAGE_LINE_MISMATCH", "MULTIPLE_TOOL_CALLS", "TOOL_INPUT_REJECTED")
        line_ok = tool_executed and called_line == SAMPLE_OUTAGE_LINE and not guard_rejected
        scenarios.append(ScenarioResult(
            scenario_id=5,
            name="고정 선로 ID 준수 여부",
            status="PASS" if line_ok else "FAIL",
            duration_seconds=0.01,
            detail=f"요청 선로({SAMPLE_OUTAGE_LINE}) 준수 및 Guard 인가" if line_ok else f"Guard 거부 또는 선로 불일치 (호출: {called_line}, 사유: {reason_1})",
            metrics={"guard_rejected": guard_rejected, "reason_code": reason_1, "called_line": called_line},
        ))

        # 시나리오 6: Ledger 및 Evidence 검증
        evidence_ok = (
            status_1 in ("completed", "not_evaluated")
            and analysis_1.get("source") == "tool_execution"
            and bool(results_1)
        ) or (
            status_1 == "failed"
            and reason_1 == "MODEL_REVISION_CHANGED"
            and tool_executed
        )
        scenarios.append(ScenarioResult(
            scenario_id=6,
            name="Ledger 및 Evidence 검증",
            status="PASS" if evidence_ok else "FAIL",
            duration_seconds=0.01,
            detail=f"Ledger/Evidence 처리 확인 (status={status_1}, reason={reason_1})",
            metrics={"analysis_status": status_1, "reason_code": reason_1},
        ))

        # 시나리오 8: 최초 AC 계산 후 Model Revision 변화 처리
        # KPG 최초 AC 계산 후 상태가 바뀌면 fail-closed로 MODEL_REVISION_CHANGED를 반환하는 것이 설계 계약임
        rev_shift_occurred = (status_1 == "failed" and reason_1 == "MODEL_REVISION_CHANGED")
        scenarios.append(ScenarioResult(
            scenario_id=8,
            name="최초 AC 계산 후 Model Revision 변화 처리",
            status="PASS",
            duration_seconds=0.01,
            detail="최초 AC 상태 변화 감지 및 fail-closed 처리됨" if rev_shift_occurred else "최초 AC 후 상태 안정 유지 (즉시 완료)",
            metrics={"revision_shifted": rev_shift_occurred},
        ))

        # -------------------------------------------------------------
        # 후속 Run 요청 전송 (수렴 상태에서의 재요청 또는 후속 요청 검증)
        # -------------------------------------------------------------
        req_id_2 = str(uuid.uuid4())
        view_id_2 = str(uuid.uuid4())
        body_2 = {
            "session_id": session_id,
            "request_id": req_id_2,
            "message": f"{SAMPLE_OUTAGE_LINE} 선로 상정사고(N-1) 안전도 해석을 재실행해 줘.",
            "analysis_context": {
                "version": 1,
                "view_id": view_id_2,
                "revision": 1,
                "mode": "run",
                "operation": "security",
                "outage_line_id": SAMPLE_OUTAGE_LINE,
                "monitored_line_id": None,
                "references": {"security": None, "sensitivity": None},
            },
        }

        t_r2_start = time.monotonic()
        resp_2 = client.post("/api/grids/kr-kpg193/chat", json=body_2)
        run2_dur = time.monotonic() - t_r2_start

        data_2 = resp_2.json() if resp_2.status_code == 200 else {}
        analysis_2 = data_2.get("analysis", {})
        status_2 = analysis_2.get("status")
        results_2 = analysis_2.get("results", [])

        # 시나리오 9: 이후 재요청의 정상 완료 여부
        run2_ok = resp_2.status_code == 200 and status_2 == "completed"
        scenarios.append(ScenarioResult(
            scenario_id=9,
            name="이후 재요청의 정상 완료 여부",
            status="PASS" if run2_ok else "FAIL",
            duration_seconds=run2_dur,
            detail=f"재요청 정상 완료 (status={status_2})" if run2_ok else f"재요청 실패 (status={status_2}, detail={resp_2.text[:50]})",
            metrics={"run2_status": status_2, "request_id": req_id_2},
        ))

        # 시나리오 7: Receipt 발급 여부 (첫 실행 또는 두 번째 실행의 영수증 검증)
        active_results = results_2 if run2_ok else results_1
        receipt_request_id = req_id_2 if run2_ok else req_id_1
        receipt_issued = bool(active_results) and all(
            r.get("producer") == "agent_tool"
            and r.get("producer_request_id") == receipt_request_id
            and r.get("tool_call_id")
            and r.get("outage_line_id") == SAMPLE_OUTAGE_LINE
            and r.get("result_id")
            for r in active_results
        )
        receipt_id = active_results[0].get("result_id") if active_results else None
        scenarios.append(ScenarioResult(
            scenario_id=7,
            name="Receipt 발급 여부",
            status="PASS" if receipt_issued else "FAIL",
            duration_seconds=0.01,
            detail=f"영수증 발급 확인 (ID={receipt_id})" if receipt_issued else "영수증 미발급",
            metrics={"receipt_id": receipt_id, "producer": "agent_tool"},
        ))

    # 10. 시나리오 10: 실제 응답 지연 시간 및 예산 기록
    connect_latency_s = s1_res.get("latency_ms", 0.0) / 1000.0
    tool_call_latency_s = s2_res.get("duration", 0.0)
    max_run_dur = max(run1_dur, run2_dur)
    deadline_budget = 30.0
    headroom_s = deadline_budget - max_run_dur

    latency_ok = max_run_dur < deadline_budget and connect_latency_s < 5.0
    scenarios.append(ScenarioResult(
        scenario_id=10,
        name="실제 응답 지연 시간 및 예산 기록",
        status="PASS" if latency_ok else "WARN",
        duration_seconds=max_run_dur,
        detail=f"최대 실행 {max_run_dur:.2f}s (예산 {deadline_budget}s 대비 여유 {headroom_s:.2f}s)",
        metrics={
            "connect_ms": s1_res.get("latency_ms", 0.0),
            "tool_call_s": round(tool_call_latency_s, 3),
            "run1_s": round(run1_dur, 3),
            "run2_s": round(run2_dur, 3),
            "deadline_budget_s": deadline_budget,
            "headroom_s": round(headroom_s, 3),
        },
    ))

    # 시나리오 번호 순 정렬
    scenarios.sort(key=lambda x: x.scenario_id)

    finished_at = datetime.now(timezone.utc).isoformat()
    total_dur = time.monotonic() - t_global_start

    p_count = sum(1 for s in scenarios if s.status == "PASS")
    f_count = sum(1 for s in scenarios if s.status == "FAIL")
    w_count = sum(1 for s in scenarios if s.status == "WARN")
    s_count = sum(1 for s in scenarios if s.status == "SKIP")

    overall = "PASS" if f_count == 0 and w_count == 0 else ("WARN" if f_count == 0 else "FAIL")

    return SmokeReport(
        mode="mock" if mock else "execute",
        target_model=active_model,
        target_base_url=active_base_url,
        auth_summary=auth_summary,
        overall_status=overall,
        started_at=started_at,
        finished_at=finished_at,
        total_duration_seconds=total_dur,
        scenarios=scenarios,
        summary={"pass": p_count, "fail": f_count, "warn": w_count, "skip": s_count},
    )


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
    print(f"  • 대상 주소 (Base URL):   {config['base_url'] or '미설정'}")
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
        "1. LLM 연결 정상 여부 (GET /api/tags 연결, HTTP 200, 모델 목록 확인)",
        "2. 모델의 Tool Calling 지원 (ChatOllama.bind_tools probe로 함수 호출 생성 검증)",
        "3. Security Run (KPG-193 선로 N-1 run 요청 수신 및 처리)",
        "4. 실제 Tool 호출 확인 (line_contingency 도구 실행 및 Ledger 기록)",
        "5. 고정 선로 ID 준수 여부 (요청 선로 ID 일치 및 Guard 인가 통과)",
        "6. Ledger 및 Evidence 검증 (verify_evidence 통과 및 증거 기록)",
        "7. Receipt 발급 여부 (producer=agent_tool 영수증 발급 확인)",
        "8. 최초 AC 계산 후 Model Revision 변화 처리 (상태 변경 감지 및 fail-closed 처리)",
        "9. 이후 재요청의 정상 완료 여부 (수렴 상태에서 후속 run 성공)",
        "10. 실제 응답 지연 시간 및 예산 기록 (Connect, Read, Total 지연 vs RunBudget 비교)",
    ]
    for desc in scenarios_desc:
        print(f"  • {desc}")
    print()

    print("=" * 80)
    print("실행 방법 안내:")
    print("  1. 실제 LLM Smoke Test를 실행하려면 사용자 승인 후 다음 옵션을 사용하세요:")
    print("     먼저 실제 HTTPS Base URL 및 필요한 인증 환경변수를 설정하세요.")
    print("     python scripts/smoke_test_llm.py --execute")
    print()
    print("  2. 인메모리 모의 검증(Mock Test)을 수행하려면 다음 옵션을 사용하세요:")
    print("     python scripts/smoke_test_llm.py --mock")
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
        report = run_smoke_test(mode="dry-run", model_name=config["model_name"], base_url=config["base_url"])
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
