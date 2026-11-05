"""PH4-02B2 per-request analysis job, execution ledger and evidence checks.

Server-only bookkeeping that binds an Agent's actual Tool executions to one
validated job. Nothing here calls an engine or an LLM. The ledger's own lock
guards short record updates only; it is never held while a model or engine
runs. ChatRequest options, receipts and API envelopes are PH4-02B3/C work.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from langchain_core.messages import AIMessage, ToolMessage

from ai_ems.grid_catalog import KPG_GRID_ID
from ai_ems.network_access import ModelIdentityUnavailable, NetworkBusy

ARTIFACT_SCHEMA = "ai-ems-line-analysis-artifact-v1"
SENSITIVITY_TOP_N = 5
OPERATION_PLANS = {
    "security": ("security",),
    "sensitivity": ("sensitivity",),
    "security_then_sensitivity": ("security", "sensitivity"),
}
# B2 binds KPG only: it alone has an identified model revision and full-artifact
# Tools. Mongolia keeps its existing evidence path until its own identity exists.
ANALYSIS_TOOLS = {
    KPG_GRID_ID: {"line_contingency": "security", "generator_sensitivity": "sensitivity"},
}
ASSESSMENTS = frozenset({"completed", "not_evaluated"})
_OUTCOME_KEYS = frozenset({"assessment", "reason_code", "outage_line_id",
                           "monitored_line_id", "monitored_selection", "top_n"})


class ToolCallRejected(ValueError):
    """A model tool call was refused before any execution."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class UnauthorizedExecution(RuntimeError):
    """A Tool body was entered without a matching, unused guard authorization."""


class ArtifactInvalid(ValueError):
    """A result cannot be sealed as strict canonical JSON evidence."""


def _is_line_id(value: Any) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 128 and value == value.strip()
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def canonical_json(value: Any) -> str:
    """Strict JSON (no NaN/Infinity, no implicit object conversion)."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ArtifactInvalid("ARTIFACT_INVALID") from exc


def artifact_digest(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    try:
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    except ArtifactInvalid:
        return None


def failure_code(error: BaseException) -> str:
    if isinstance(error, NetworkBusy):
        return "ANALYSIS_BUSY"
    if isinstance(error, ModelIdentityUnavailable):
        return "MODEL_IDENTITY_UNAVAILABLE"
    if isinstance(error, ArtifactInvalid):
        return "ARTIFACT_INVALID"
    if isinstance(error, ValueError):
        return "TOOL_INPUT_REJECTED"
    return "TOOL_FAILED"


@dataclass(frozen=True)
class AnalysisJob:
    """Immutable, server-validated condition of one requested analysis run."""

    grid_id: str
    operation: str
    outage_line_id: str
    monitored_line_id: str | None = None  # None: domain automatic selection
    job_id: str = field(init=False, default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self):
        if not isinstance(self.grid_id, str) or self.grid_id not in ANALYSIS_TOOLS:
            raise ValueError("UNSUPPORTED_GRID")
        if not isinstance(self.operation, str) or self.operation not in OPERATION_PLANS:
            raise ValueError("UNSUPPORTED_OPERATION")
        if not _is_line_id(self.outage_line_id):
            raise ValueError("INVALID_OUTAGE_LINE_ID")
        if self.monitored_line_id is not None:
            if "sensitivity" not in self.plan:
                raise ValueError("MONITORED_LINE_NOT_ALLOWED")
            if (not _is_line_id(self.monitored_line_id)
                    or self.monitored_line_id == self.outage_line_id):
                raise ValueError("INVALID_MONITORED_LINE_ID")

    @property
    def plan(self) -> tuple[str, ...]:
        return OPERATION_PLANS[self.operation]

    @property
    def tools(self) -> dict[str, str]:
        """Tool name -> kind for exactly the kinds this job may execute."""
        return {name: kind for name, kind in ANALYSIS_TOOLS[self.grid_id].items()
                if kind in self.plan}


def normalize_arguments(job: AnalysisJob, kind: str, args: Any) -> dict[str, Any]:
    """The only accepted arguments; a model can neither choose nor change them."""
    if not isinstance(args, Mapping):
        raise ToolCallRejected("INVALID_ARGUMENTS")
    allowed = {"outage_line_id", "monitored_line_id"} | ({"top_n"} if kind == "sensitivity" else set())
    if not set(args) <= allowed:
        raise ToolCallRejected("UNEXPECTED_ARGUMENT")
    if args.get("outage_line_id") != job.outage_line_id:
        raise ToolCallRejected("OUTAGE_LINE_MISMATCH")
    monitored = args.get("monitored_line_id")
    if kind == "security":
        if monitored is not None:
            raise ToolCallRejected("MONITORED_LINE_NOT_ALLOWED")
        return {"outage_line_id": job.outage_line_id, "monitored_line_id": None}
    if monitored != job.monitored_line_id:
        raise ToolCallRejected("AUTO_SELECTION_REPLACED" if job.monitored_line_id is None
                               else "MONITORED_LINE_MISMATCH")
    top_n = args.get("top_n", SENSITIVITY_TOP_N)
    if type(top_n) is not int or top_n != SENSITIVITY_TOP_N:
        raise ToolCallRejected("TOP_N_NOT_ALLOWED")
    return {"outage_line_id": job.outage_line_id,
            "monitored_line_id": job.monitored_line_id, "top_n": SENSITIVITY_TOP_N}


def _frozen(arguments: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted(arguments.items()))


def _outcome_problem(job: AnalysisJob, kind: str, outcome: Any) -> str | None:
    """The executed condition must equal the frozen job condition."""
    if not isinstance(outcome, dict) or set(outcome) != _OUTCOME_KEYS:
        return "ARTIFACT_INVALID"
    assessment, reason = outcome["assessment"], outcome["reason_code"]
    if (assessment not in ASSESSMENTS or (assessment == "completed") != (reason is None)
            or (reason is not None and not (isinstance(reason, str) and reason))):
        return "ARTIFACT_INVALID"
    if outcome["outage_line_id"] != job.outage_line_id:
        return "OUTAGE_LINE_MISMATCH"
    monitored, selection, top_n = (outcome["monitored_line_id"], outcome["monitored_selection"],
                                   outcome["top_n"])
    if kind == "security":
        return None if (monitored, selection, top_n) == (None, None, None) else "MONITORED_LINE_MISMATCH"
    if type(top_n) is not int or top_n != SENSITIVITY_TOP_N:
        return "TOP_N_NOT_ALLOWED"
    if job.monitored_line_id is not None:
        return None if (selection, monitored) == ("explicit", job.monitored_line_id) else "MONITORED_LINE_MISMATCH"
    # Automatic selection: a target exists exactly when the result was evaluated.
    if (selection != "auto" or (monitored is None) != (assessment == "not_evaluated")
            or (monitored is not None and (not _is_line_id(monitored) or monitored == job.outage_line_id))):
        return "MONITORED_LINE_MISMATCH"
    return None


@dataclass(frozen=True)
class ExecutionRecord:
    """Immutable view of one guard decision and its actual execution."""

    tool_call_id: str
    tool_name: str
    kind: str
    arguments: tuple[tuple[str, Any], ...]
    status: str  # authorized | started | succeeded | failed
    execution_id: str | None = None
    reason_code: str | None = None
    assessment: str | None = None
    revision_before: str | None = None
    revision_after: str | None = None
    artifact_sha256: str | None = None
    artifact_json: str | None = None

    @property
    def revision_stable(self) -> bool:
        return self.revision_before is not None and self.revision_before == self.revision_after

    def artifact(self) -> dict[str, Any] | None:
        return json.loads(self.artifact_json) if self.artifact_json is not None else None


@dataclass(frozen=True)
class LedgerSnapshot:
    job: AnalysisJob
    records: tuple[ExecutionRecord, ...]
    rejection: str | None
    rejected_call_ids: frozenset[str]


class ExecutionLedger:
    """Per-job, process-local record of guard decisions and actual executions."""

    def __init__(self, job: AnalysisJob):
        self.job = job
        self._lock = threading.Lock()
        self._records: dict[str, ExecutionRecord] = {}
        self._rejection: str | None = None
        self._rejected_ids: set[str] = set()
        self._claimed = False

    def claim(self) -> None:
        """One ledger backs exactly one graph run; evidence is never shared."""
        with self._lock:
            if self._claimed:
                raise RuntimeError("LEDGER_ALREADY_CLAIMED")
            self._claimed = True

    def _next_kind(self) -> str | None:
        records = list(self._records.values())
        if self._rejection is not None or len(records) >= len(self.job.plan):
            return None
        if any(record.status != "succeeded" for record in records):
            return None
        # A chain continues only after an attributable, completed N-1 result.
        if records and not (records[-1].revision_stable and records[-1].assessment == "completed"):
            return None
        return self.job.plan[len(records)]

    def next_kind(self) -> str | None:
        with self._lock:
            return self._next_kind()

    @property
    def rejection(self) -> str | None:
        with self._lock:
            return self._rejection

    def authorize(self, tool_calls: list[dict[str, Any]]) -> None:
        """Guard one AI turn BEFORE ToolNode: one allowed call, frozen args, plan order."""
        with self._lock:
            try:
                if self._rejection is not None:
                    raise ToolCallRejected(self._rejection)
                if len(tool_calls) != 1:
                    raise ToolCallRejected("MULTIPLE_TOOL_CALLS" if tool_calls else "NO_TOOL_CALL")
                call = tool_calls[0]
                name = call.get("name")
                kind = self.job.tools.get(name) if isinstance(name, str) else None
                if kind is None:
                    raise ToolCallRejected("TOOL_NOT_ALLOWED")
                call_id = call.get("id")
                if not isinstance(call_id, str) or not call_id or call_id in self._records:
                    raise ToolCallRejected("INVALID_TOOL_CALL_ID")
                if kind != self._next_kind():
                    raise ToolCallRejected("UNEXPECTED_TOOL_SEQUENCE")
                arguments = normalize_arguments(self.job, kind, call.get("args"))
            except ToolCallRejected as exc:
                self._rejection = self._rejection or exc.code
                self._rejected_ids.update(c.get("id") for c in tool_calls
                                          if isinstance(c.get("id"), str))
                raise
            self._records[call_id] = ExecutionRecord(call_id, name, kind, _frozen(arguments),
                                                     "authorized")

    def begin(self, tool_call_id: str, tool_name: str, arguments: Mapping[str, Any]) -> str:
        """Enter an authorized Tool body exactly once; anything else computes nothing."""
        with self._lock:
            record = self._records.get(tool_call_id)
            try:
                allowed = (self._rejection is None and record is not None
                           and record.status == "authorized" and record.tool_name == tool_name
                           and _frozen(normalize_arguments(self.job, record.kind, arguments))
                           == record.arguments)
            except ToolCallRejected:
                allowed = False
            if not allowed:
                raise UnauthorizedExecution("UNAUTHORIZED_EXECUTION")
            execution_id = str(uuid.uuid4())
            self._records[tool_call_id] = dataclasses.replace(record, status="started",
                                                              execution_id=execution_id)
            return execution_id

    def complete(self, tool_call_id: str, *, revision_before, revision_after,
                 outcome: dict[str, Any], result: Any) -> dict[str, Any]:
        """Seal one real execution's full result and return a detached artifact copy.

        ``revision_before``/``revision_after`` are the model revisions captured
        inside the same engine guard as the calculation (``None``: unavailable).
        """
        with self._lock:
            record = self._records.get(tool_call_id)
        if record is None or record.status != "started":
            raise UnauthorizedExecution("UNAUTHORIZED_EXECUTION")
        if _outcome_problem(self.job, record.kind, outcome) is not None:
            raise ArtifactInvalid("ARTIFACT_INVALID")
        revisions = {"before": _revision(revision_before), "after": _revision(revision_after)}
        artifact = {
            "schema": ARTIFACT_SCHEMA,
            "job_id": self.job.job_id,
            "execution_id": record.execution_id,
            "grid_id": self.job.grid_id,
            "operation": self.job.operation,
            "kind": record.kind,
            "tool_name": record.tool_name,
            "tool_call_id": tool_call_id,
            "arguments": dict(record.arguments),
            "model_revision": revisions,
            "outcome": outcome,
            "result": result,
        }
        text = canonical_json(artifact)  # Serialized outside the engine and ledger locks.
        sealed = dataclasses.replace(
            record, status="succeeded", assessment=outcome["assessment"],
            reason_code=outcome["reason_code"],
            revision_before=(revisions["before"] or {}).get("revision"),
            revision_after=(revisions["after"] or {}).get("revision"),
            artifact_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), artifact_json=text)
        with self._lock:
            if self._records.get(tool_call_id) is not record:
                raise UnauthorizedExecution("UNAUTHORIZED_EXECUTION")
            self._records[tool_call_id] = sealed
        return json.loads(text)

    def fail(self, tool_call_id: str, error: BaseException) -> None:
        with self._lock:
            record = self._records.get(tool_call_id)
            if record is not None and record.status == "started":
                self._records[tool_call_id] = dataclasses.replace(
                    record, status="failed", reason_code=failure_code(error))

    def snapshot(self) -> LedgerSnapshot:
        with self._lock:
            return LedgerSnapshot(self.job, tuple(self._records.values()), self._rejection,
                                  frozenset(self._rejected_ids))


def _revision(revision) -> dict[str, Any] | None:
    if revision is None:
        return None
    if not dataclasses.is_dataclass(revision) or not isinstance(getattr(revision, "revision", None), str):
        raise ArtifactInvalid("ARTIFACT_INVALID")
    return dataclasses.asdict(revision)


@dataclass(frozen=True)
class VerifiedResult:
    """One ledger-backed execution that matched its AI call, ToolMessage and job."""

    kind: str
    grid_id: str
    job_id: str
    execution_id: str
    tool_call_id: str
    tool_name: str
    outage_line_id: str
    monitored_line_id: str | None
    monitored_selection: str | None
    top_n: int | None
    model_revision: str
    assessment: str
    reason_code: str | None
    artifact_sha256: str
    artifact_json: str

    def artifact(self) -> dict[str, Any]:
        return json.loads(self.artifact_json)


@dataclass(frozen=True)
class Verification:
    status: str  # completed | not_evaluated | not_run | failed
    reason_code: str | None
    results: tuple[VerifiedResult, ...] = ()
    issues: tuple[str, ...] = ()


def verify_evidence(ledger: ExecutionLedger, messages: list[Any], *,
                    current_revision: str | None) -> Verification:
    """Bind this run's AI calls, ToolMessages, ledger executions and artifacts.

    ``messages`` are only those generated by this job's graph run. Message text,
    ``verified`` flags, history and client values are never evidence. Failed or
    partial runs publish no result. ``current_revision`` is the model revision
    captured by the caller now; ``None`` means it could not be identified.
    """
    snap = ledger.snapshot()
    job = snap.job
    issues: list[str] = []
    calls: dict[str, dict[str, Any]] = {}
    answered: dict[str, ToolMessage] = {}
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                call_id = call.get("id")
                if not isinstance(call_id, str) or call_id in calls:
                    issues.append("INVALID_TOOL_CALL_ID")
                else:
                    calls[call_id] = call
        elif isinstance(message, ToolMessage):
            if message.tool_call_id not in calls:
                issues.append("UNPAIRED_TOOL_MESSAGE")
            elif message.tool_call_id in answered:
                issues.append("DUPLICATE_TOOL_MESSAGE")
            else:
                answered[message.tool_call_id] = message
        else:
            issues.append("UNEXPECTED_MESSAGE")
    if snap.rejection is not None:
        issues.append(snap.rejection)
    if set(calls) - set(answered):
        issues.append("TOOL_NOT_EXECUTED")
    records = {record.tool_call_id: record for record in snap.records}
    if set(records) - set(answered):
        issues.append("LEDGER_WITHOUT_TOOL_MESSAGE")
    executed: list[ExecutionRecord] = []
    for call_id, message in answered.items():
        if call_id in snap.rejected_call_ids:
            continue  # Reported through the ledger rejection code.
        record = records.get(call_id)
        if record is None:
            issues.append("UNLEDGERED_TOOL_MESSAGE")
            continue
        problem = _message_problem(job, calls[call_id], message, record)
        if problem is None:
            executed.append(record)
        else:
            issues.append(problem)
    if not issues and not executed:
        return Verification("not_run", "TOOL_NOT_EXECUTED")
    if not issues:
        issues.extend(_condition_problems(job, [r.tool_call_id for r in snap.records],
                                          executed, current_revision))
    if issues:
        unique = tuple(dict.fromkeys(issues))
        return Verification("failed", unique[0], (), unique)
    results = tuple(_verified(job, record) for record in executed)
    pending = [result for result in results if result.assessment != "completed"]
    return Verification("not_evaluated" if pending else "completed",
                        pending[0].reason_code if pending else None, results)


def _message_problem(job: AnalysisJob, call: dict[str, Any], message: ToolMessage,
                     record: ExecutionRecord) -> str | None:
    if call.get("name") != record.tool_name or message.name != record.tool_name:
        return "TOOL_NAME_MISMATCH"
    try:
        arguments = _frozen(normalize_arguments(job, record.kind, call.get("args")))
    except ToolCallRejected:
        arguments = None
    if arguments != record.arguments:
        return "TOOL_ARGUMENT_MISMATCH"
    if record.status != "succeeded":
        return record.reason_code or "TOOL_NOT_COMPLETED"
    if message.status != "success":
        return "TOOL_MESSAGE_ERROR"
    if artifact_digest(message.artifact) != record.artifact_sha256:
        return "ARTIFACT_MISMATCH"
    return None


def _condition_problems(job: AnalysisJob, ledger_order: list[str],
                        executed: list[ExecutionRecord], current_revision: str | None) -> list[str]:
    problems: list[str] = []
    kinds = tuple(record.kind for record in executed)
    if ([record.tool_call_id for record in executed] != ledger_order
            or kinds != job.plan[:len(kinds)]
            or (len(executed) > 1 and executed[0].assessment != "completed")):
        problems.append("UNEXPECTED_TOOL_SEQUENCE")
    for record in executed:
        artifact = record.artifact()
        identity = (artifact.get("schema"), artifact.get("job_id"), artifact.get("grid_id"),
                    artifact.get("operation"), artifact.get("execution_id"),
                    artifact.get("tool_call_id"), artifact.get("kind"), artifact.get("tool_name"))
        if identity != (ARTIFACT_SCHEMA, job.job_id, job.grid_id, job.operation,
                        record.execution_id, record.tool_call_id, record.kind, record.tool_name):
            problems.append("ARTIFACT_IDENTITY_MISMATCH")
        problem = _outcome_problem(job, record.kind, artifact.get("outcome"))
        if problem is not None:
            problems.append(problem)
        if record.revision_before is None or record.revision_after is None:
            problems.append("MODEL_IDENTITY_UNAVAILABLE")
        elif record.revision_before != record.revision_after:
            problems.append("MODEL_REVISION_CHANGED")  # e.g. the first AC wrote the state.
    if current_revision is None:
        problems.append("MODEL_IDENTITY_UNAVAILABLE")
    elif {record.revision_after for record in executed} != {current_revision}:
        problems.append("MODEL_REVISION_CHANGED")
    if not problems and kinds != job.plan:
        # Only an actual, unevaluated N-1 may end a chain early; never a skipped step.
        early_end = (job.operation == "security_then_sensitivity" and kinds == ("security",)
                     and executed[0].assessment == "not_evaluated")
        if not early_end:
            problems.append("CHAIN_INCOMPLETE")
    return problems


def _verified(job: AnalysisJob, record: ExecutionRecord) -> VerifiedResult:
    outcome = record.artifact()["outcome"]
    return VerifiedResult(
        kind=record.kind, grid_id=job.grid_id, job_id=job.job_id,
        execution_id=record.execution_id, tool_call_id=record.tool_call_id,
        tool_name=record.tool_name, outage_line_id=outcome["outage_line_id"],
        monitored_line_id=outcome["monitored_line_id"],
        monitored_selection=outcome["monitored_selection"], top_n=outcome["top_n"],
        model_revision=record.revision_after, assessment=record.assessment,
        reason_code=record.reason_code, artifact_sha256=record.artifact_sha256,
        artifact_json=record.artifact_json)


# --- PH4-02B3-1: v1 opt-in request validation (pure; no engine, registry or LLM) ---

UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
MAX_MESSAGE_CHARS = 8000
MAX_CONTEXT_REVISION = 2 ** 53 - 1
_CONTEXT_KEYS = frozenset({"version", "view_id", "revision", "mode", "operation",
                           "outage_line_id", "monitored_line_id", "references"})
_REFERENCE_KINDS = ("security", "sensitivity")


class AnalysisApiError(ValueError):
    """A v1 request refused with a fixed {code, message}; never a raw input echo."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


def is_uuid4(value: Any) -> bool:
    return isinstance(value, str) and UUID4.fullmatch(value) is not None


def is_session_id(value: Any) -> bool:
    return isinstance(value, str) and SESSION_ID.fullmatch(value) is not None


def _invalid(field: str) -> AnalysisApiError:
    return AnalysisApiError(422, "ANALYSIS_CONTEXT_INVALID", f"분석 문맥 요청 형식이 올바르지 않습니다: {field}")


@dataclass(frozen=True)
class AnalysisContextV1:
    """The validated v1 analysis context; echoed exactly, never auto-filled."""

    view_id: str
    revision: int
    mode: str
    operation: str
    outage_line_id: str
    monitored_line_id: str | None
    references: tuple[tuple[str, str | None], ...]

    def reference(self, kind: str) -> str | None:
        return dict(self.references)[kind]

    def echo(self) -> dict[str, Any]:
        return {"version": 1, "view_id": self.view_id, "revision": self.revision,
                "mode": self.mode, "operation": self.operation,
                "outage_line_id": self.outage_line_id, "monitored_line_id": self.monitored_line_id,
                "references": dict(self.references)}


@dataclass(frozen=True)
class AnalysisRequestV1:
    request_id: str
    session_id: str
    message: str
    context: AnalysisContextV1

    def fingerprint(self, grid_id: str) -> str:
        """Normalized input identity for the per-request de-duplication window."""
        return hashlib.sha256(canonical_json({
            "grid_id": grid_id, "session_id": self.session_id, "message": self.message,
            "analysis_context": self.context.echo()}).encode("utf-8")).hexdigest()


def parse_analysis_request(request_id: Any, analysis_context: Any, session_id: Any,
                           message: Any) -> AnalysisRequestV1:
    """Validate a v1 chat opt-in before any LLM, engine or registry access (PH4-02A §3)."""
    if request_id is None or analysis_context is None:
        raise AnalysisApiError(422, "ANALYSIS_OPT_IN_INCOMPLETE",
                               "request_id와 analysis_context는 함께 보내야 합니다.")
    if not is_uuid4(request_id):
        raise _invalid("request_id")
    if not is_session_id(session_id):
        raise _invalid("session_id")
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE_CHARS:
        raise _invalid("message")
    context = analysis_context
    if not isinstance(context, dict) or set(context) != _CONTEXT_KEYS:
        raise _invalid("analysis_context")
    if type(context["version"]) is not int or context["version"] != 1:
        raise _invalid("analysis_context.version")
    if not is_uuid4(context["view_id"]):
        raise _invalid("analysis_context.view_id")
    revision = context["revision"]
    if type(revision) is not int or not 0 <= revision <= MAX_CONTEXT_REVISION:
        raise _invalid("analysis_context.revision")
    mode, operation = context["mode"], context["operation"]
    if not isinstance(mode, str) or mode not in ("reference", "run"):
        raise _invalid("analysis_context.mode")
    if not isinstance(operation, str) or operation not in OPERATION_PLANS:
        raise _invalid("analysis_context.operation")
    outage, monitored = context["outage_line_id"], context["monitored_line_id"]
    if not _is_line_id(outage):
        raise _invalid("analysis_context.outage_line_id")
    # null monitored is domain auto-selection; Security receipts carry no monitored line.
    if monitored is not None and (not _is_line_id(monitored) or monitored == outage
                                  or "sensitivity" not in OPERATION_PLANS[operation]):
        raise _invalid("analysis_context.monitored_line_id")
    references = context["references"]
    if not isinstance(references, dict) or set(references) != set(_REFERENCE_KINDS):
        raise _invalid("analysis_context.references")
    if any(value is not None and not is_uuid4(value) for value in references.values()):
        raise _invalid("analysis_context.references")
    required = set(OPERATION_PLANS[operation]) if mode == "reference" else set()
    if {kind for kind, value in references.items() if value is not None} != required:
        raise _invalid("analysis_context.references")
    return AnalysisRequestV1(request_id, session_id, message, AnalysisContextV1(
        context["view_id"], revision, mode, operation, outage, monitored,
        tuple((kind, references[kind]) for kind in _REFERENCE_KINDS)))


@dataclass(frozen=True)
class ReceiptOptIn:
    session_id: str
    request_id: str


def parse_receipt_opt_in(version: str | None, session_id: str | None,
                         request_id: str | None) -> ReceiptOptIn | None:
    """Manual receipt opt-in needs all three X-EMS headers; none keeps the legacy call."""
    values = (version, session_id, request_id)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise AnalysisApiError(422, "ANALYSIS_OPT_IN_INCOMPLETE",
                               "분석 결과 등록에는 X-EMS 헤더 세 개가 모두 필요합니다.")
    if version != "1":
        raise _invalid("X-EMS-Analysis-Context-Version")
    if not is_session_id(session_id):
        raise _invalid("X-EMS-Session-ID")
    if not is_uuid4(request_id):
        raise _invalid("X-EMS-Request-ID")
    return ReceiptOptIn(session_id, request_id)
