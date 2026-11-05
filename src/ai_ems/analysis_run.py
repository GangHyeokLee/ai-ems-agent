"""PH4-02B3-2 run budgets, single-worker assertion and the provider model for v1 runs.

The budgets bound how long a run waits for the LLM provider and when it stops
starting new model turns. None of them cancels a native PowSyBl calculation that
is already running; locks and ledgers stay held until that call returns.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Mapping

import httpx
from langgraph.errors import GraphRecursionError
from ollama import ResponseError

from ai_ems.agent.graph import RunDeadlineExceeded, _chat_model

SINGLE_WORKER_ENV = "AI_EMS_SINGLE_WORKER"
# Defaults fit two model turns inside the console's existing 60 s wait. They are
# configurable starting points, not measured latencies or service levels.
BUDGET_DEFAULTS = {
    "AI_EMS_RUN_LLM_CONNECT_TIMEOUT": 5.0,
    "AI_EMS_RUN_LLM_READ_TIMEOUT": 30.0,
    "AI_EMS_RUN_DEADLINE_SECONDS": 30.0,
}
LLM_WRITE_TIMEOUT = 10.0
LLM_POOL_TIMEOUT = 5.0
RUN_RECURSION_LIMIT = 12  # a chain needs 6 graph steps (agent, guard, tools x2)
MAX_BUDGET_SECONDS = 600.0


@dataclass(frozen=True)
class RunBudget:
    llm_timeout: httpx.Timeout
    deadline_seconds: float
    recursion_limit: int


def single_worker_confirmed(environ: Mapping[str, str] | None = None) -> bool:
    """Process-local receipts, gates and history need one worker; the supported launcher asserts it."""
    return (os.environ if environ is None else environ).get(SINGLE_WORKER_ENV) == "1"


def run_budget(environ: Mapping[str, str] | None = None) -> RunBudget:
    """Validated budget; an invalid override raises instead of silently using a default."""
    settings = os.environ if environ is None else environ
    values = {}
    for name, default in BUDGET_DEFAULTS.items():
        raw = settings.get(name)
        value = default if raw is None else float(raw)
        if not math.isfinite(value) or not 0 < value <= MAX_BUDGET_SECONDS:
            raise ValueError(f"Invalid run budget: {name}")
        values[name] = value
    timeout = httpx.Timeout(connect=values["AI_EMS_RUN_LLM_CONNECT_TIMEOUT"],
                            read=values["AI_EMS_RUN_LLM_READ_TIMEOUT"],
                            write=LLM_WRITE_TIMEOUT, pool=LLM_POOL_TIMEOUT)
    return RunBudget(timeout, values["AI_EMS_RUN_DEADLINE_SECONDS"], RUN_RECURSION_LIMIT)


def run_chat_model(budget: RunBudget):
    """The configured provider model with explicit transport timeouts (legacy chat is unchanged)."""
    return _chat_model(None, None, timeout=budget.llm_timeout)


def agent_failure(error: BaseException) -> tuple[str, str]:
    """Fixed {code, message} for an Agent runtime failure; provider text is never echoed."""
    if isinstance(error, (RunDeadlineExceeded, GraphRecursionError)):
        return "AGENT_BUDGET_EXCEEDED", "분석 실행 예산(시간·단계)을 넘어 실행을 중단했습니다. 결과를 공개하지 않았습니다."
    if isinstance(error, httpx.TimeoutException):
        return "AGENT_TIMEOUT", "AI 모델 응답 시간이 초과되어 실행을 완료하지 못했습니다. 결과를 공개하지 않았습니다."
    if isinstance(error, (httpx.HTTPError, ResponseError, ConnectionError)):
        return "AGENT_UNAVAILABLE", "AI 모델 연결 또는 응답 오류로 실행을 완료하지 못했습니다. 결과를 공개하지 않았습니다."
    return "AGENT_FAILED", "Agent 실행 중 오류로 실행을 완료하지 못했습니다. 결과를 공개하지 않았습니다."
