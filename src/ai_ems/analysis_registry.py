"""PH4-02B3-1 process-local analysis receipts, request de-duplication and v1 session gates.

Single worker, process memory only: a restart forgets every receipt, request and
gate, and nothing here claims exactly-once execution across restarts. Each lock
guards short bookkeeping; no engine, model or LLM call runs while one is held and
none of them waits for another lock. Only server-computed canonical results are
registered; client values, ``verified`` flags or hashes are never accepted.
"""
from __future__ import annotations

import dataclasses
import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ai_ems.analysis_context import (
    ASSESSMENTS, OPERATION_PLANS, SENSITIVITY_TOP_N, AnalysisApiError, AnalysisContextV1,
    _is_line_id, canonical_json, is_session_id, is_uuid4,
)

REGISTRY_CAPACITY = 256
REGISTRY_TTL_SECONDS = 600
REQUEST_WINDOW_SECONDS = 600
REQUEST_CAPACITY = 1024
PRODUCERS = frozenset({"manual", "agent_tool"})
_REVISION = re.compile(r"[0-9a-f]{64}")


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class AnalysisReceipt:
    """Server-issued identity of one registered result; the ID itself is not authority."""

    result_id: str
    grid_id: str
    kind: str
    outage_line_id: str
    monitored_line_id: str | None
    monitored_selection: str | None
    top_n: int | None
    model_revision: str
    producer: str
    producer_request_id: str
    tool_call_id: str | None
    assessment: str
    reason_code: str | None
    created_at: str
    expires_at: str

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class RegisteredResult:
    receipt: AnalysisReceipt
    session_id: str
    payload_json: str
    evidence_json: str
    expires_at_monotonic: float

    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)


def _receipt_problem(kind: str, monitored: Any, selection: Any, top_n: Any,
                     assessment: Any, reason: Any) -> bool:
    if kind == "security":
        return (monitored, selection, top_n) != (None, None, None)
    if kind != "sensitivity" or type(top_n) is not int or top_n != SENSITIVITY_TOP_N:
        return True
    if selection not in ("auto", "explicit"):
        return True
    # A sensitivity target exists exactly when it was evaluated (or explicitly given).
    if monitored is None:
        return not (selection == "auto" and assessment == "not_evaluated")
    return not _is_line_id(monitored)


class ResultRegistry:
    """Bounded receipts: oldest entry evicted first, TTL by a monotonic clock."""

    def __init__(self, capacity: int = REGISTRY_CAPACITY, ttl_seconds: float = REGISTRY_TTL_SECONDS,
                 monotonic: Callable[[], float] = time.monotonic,
                 utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.capacity, self.ttl_seconds = capacity, ttl_seconds
        self._monotonic, self._utcnow = monotonic, utcnow
        self._lock = threading.Lock()
        self._entries: dict[str, RegisteredResult] = {}

    def register(self, *, grid_id: str, session_id: str, kind: str, outage_line_id: str,
                 monitored_line_id: str | None, monitored_selection: str | None, top_n: int | None,
                 model_revision: str, producer: str, producer_request_id: str,
                 tool_call_id: str | None, assessment: str, reason_code: str | None,
                 payload: dict[str, Any], evidence: dict[str, Any]) -> AnalysisReceipt:
        if (not isinstance(grid_id, str) or not is_session_id(session_id) or not _is_line_id(outage_line_id)
                or not isinstance(model_revision, str) or not _REVISION.fullmatch(model_revision)
                or producer not in PRODUCERS or not is_uuid4(producer_request_id)
                or (producer == "manual") != (tool_call_id is None)
                or assessment not in ASSESSMENTS or (assessment == "completed") != (reason_code is None)
                or (reason_code is not None and not (isinstance(reason_code, str) and reason_code))
                or _receipt_problem(kind, monitored_line_id, monitored_selection, top_n,
                                    assessment, reason_code)
                or not isinstance(payload, dict)):
            raise ValueError("Invalid analysis receipt")
        payload_json, evidence_json = canonical_json(payload), canonical_json(evidence)  # outside the lock
        created = self._utcnow()
        receipt = AnalysisReceipt(
            str(uuid.uuid4()), grid_id, kind, outage_line_id, monitored_line_id, monitored_selection,
            top_n, model_revision, producer, producer_request_id, tool_call_id, assessment,
            reason_code, _rfc3339(created), _rfc3339(created + timedelta(seconds=self.ttl_seconds)))
        entry = RegisteredResult(receipt, session_id, payload_json, evidence_json,
                                 self._monotonic() + self.ttl_seconds)
        with self._lock:
            self._purge_locked()
            while len(self._entries) >= self.capacity:
                self._entries.pop(next(iter(self._entries)))  # insertion order = oldest first
            self._entries[receipt.result_id] = entry
        return receipt

    def _purge_locked(self) -> None:
        now = self._monotonic()
        for result_id in [key for key, entry in self._entries.items() if entry.expires_at_monotonic <= now]:
            del self._entries[result_id]

    def lookup(self, result_id: str | None) -> RegisteredResult | None:
        with self._lock:
            self._purge_locked()
            return self._entries.get(result_id) if isinstance(result_id, str) else None

    def __len__(self) -> int:
        with self._lock:
            self._purge_locked()
            return len(self._entries)

    def resolve(self, *, grid_id: str, session_id: str,
                context: AnalysisContextV1) -> list[RegisteredResult]:
        """Every reference must be a live result of this owner, kind and exact condition."""
        found = []
        for kind in OPERATION_PLANS[context.operation]:
            entry = self.lookup(context.reference(kind))
            if entry is None or not _matches(entry, grid_id, session_id, kind, context):
                raise AnalysisApiError(409, "ANALYSIS_REFERENCE_UNAVAILABLE",
                                       "참조한 분석 결과를 사용할 수 없습니다(없음·만료·재시작·소유자/조건 불일치). "
                                       "자동으로 다시 계산하지 않았습니다.")
            found.append(entry)
        return found


def _matches(entry: RegisteredResult, grid_id: str, session_id: str, kind: str,
             context: AnalysisContextV1) -> bool:
    receipt = entry.receipt
    if (receipt.grid_id, entry.session_id, receipt.kind, receipt.outage_line_id) != (
            grid_id, session_id, kind, context.outage_line_id):
        return False
    if kind == "security":
        return receipt.monitored_line_id is None and receipt.top_n is None
    if receipt.top_n != SENSITIVITY_TOP_N:
        return False
    if context.monitored_line_id is None:  # Automatic request: explain the server-selected line.
        return receipt.monitored_selection == "auto"
    return (receipt.monitored_selection, receipt.monitored_line_id) == ("explicit", context.monitored_line_id)


@dataclass(frozen=True)
class _Completed:
    fingerprint: str
    envelope_json: str
    expires_at_monotonic: float


class RequestTracker:
    """(grid, session, request_id) de-duplication within a bounded process-local window.

    Only successful envelopes are kept; an error releases the ID so the client may
    retry it. Cached envelopes are returned byte-for-byte in their original key order.
    """

    def __init__(self, window_seconds: float = REQUEST_WINDOW_SECONDS, capacity: int = REQUEST_CAPACITY,
                 monotonic: Callable[[], float] = time.monotonic):
        self.window_seconds, self.capacity, self._monotonic = window_seconds, capacity, monotonic
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str, str], str] = {}
        self._completed: dict[tuple[str, str, str], _Completed] = {}

    def _purge_locked(self) -> None:
        now = self._monotonic()
        for key in [key for key, done in self._completed.items() if done.expires_at_monotonic <= now]:
            del self._completed[key]

    def begin(self, key: tuple[str, str, str], fingerprint: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            done = self._completed.get(key)
            if done is not None:
                if done.fingerprint != fingerprint:
                    raise AnalysisApiError(409, "REQUEST_ID_REUSE", "같은 request_id를 다른 요청에 다시 사용할 수 없습니다.")
                return json.loads(done.envelope_json)
            pending = self._pending.get(key)
            if pending is not None:
                if pending == fingerprint:
                    raise AnalysisApiError(409, "REQUEST_IN_PROGRESS", "같은 요청을 처리하고 있습니다.")
                raise AnalysisApiError(409, "REQUEST_ID_REUSE", "같은 request_id를 다른 요청에 다시 사용할 수 없습니다.")
            self._pending[key] = fingerprint
            return None

    def complete(self, key: tuple[str, str, str], fingerprint: str, envelope: dict[str, Any]) -> None:
        text = json.dumps(envelope, ensure_ascii=False, allow_nan=False)  # keeps key order
        with self._lock:
            if self._pending.get(key) == fingerprint:
                del self._pending[key]
            self._purge_locked()
            while len(self._completed) >= self.capacity:
                self._completed.pop(next(iter(self._completed)))
            self._completed[key] = _Completed(fingerprint, text, self._monotonic() + self.window_seconds)

    def abandon(self, key: tuple[str, str, str], fingerprint: str) -> None:
        with self._lock:
            if self._pending.get(key) == fingerprint:
                del self._pending[key]


class SessionGates:
    """Non-blocking per (grid, session) gate for v1 requests; KPG and MN never share one."""

    def __init__(self):
        self._lock = threading.Lock()
        self._busy: set[tuple[str, str]] = set()

    @contextmanager
    def hold(self, grid_id: str, session_id: str):
        key = (grid_id, session_id)
        with self._lock:
            if key in self._busy:
                raise AnalysisApiError(409, "SESSION_BUSY", "같은 대화 세션의 다른 요청을 처리하고 있습니다.")
            self._busy.add(key)
        try:
            yield
        finally:  # Released only by the executing request's own completion.
            with self._lock:
                self._busy.discard(key)
