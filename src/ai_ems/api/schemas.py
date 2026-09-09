from pydantic import BaseModel, Field


class SecurityAnalysisRequest(BaseModel):
    outage_line_id: str
    monitored_line_id: str | None = None


class ContingencyResponseRequest(BaseModel):
    outage_line_id: str
    monitored_line_id: str | None = None
    delta_mw: float = Field(default=10.0, gt=0)
    top_n: int = Field(default=3, ge=1)


class ViolationSummary(BaseModel):
    equipment_id: str
    limit_type: str | None = None
    limit_name: str | None = None
    unit: str | None = None
    limit: float | None = None
    value: float | None = None
    violation_amount: float | None = None
    loading_percent: float | None = None


class SecurityAnalysisResponse(BaseModel):
    outage_line_id: str
    base_converged: bool
    pre_status: str | None = None
    post_status: str | None = None

    pre_violated_equipment_count: int
    post_violated_equipment_count: int

    new_violation_count: int
    remaining_violation_count: int
    resolved_violation_count: int

    new_violations: list[ViolationSummary]
    remaining_violations: list[ViolationSummary]
    resolved_violations: list[ViolationSummary]


class RedispatchCandidateResponse(BaseModel):
    rank: int
    ac_validation_rank: int | None = None

    up_generator_id: str
    down_generator_id: str
    delta_mw: float

    predicted_active_power_change_mw: float | None = None
    predicted_abs_p1_reduction_mw: float | None = None

    apparent_power_before_mva: float | None = None
    apparent_power_after_mva: float | None = None
    improvement_mva: float | None = None

    loading_before_percent: float | None = None
    loading_after_percent: float | None = None

    violation_remaining: bool | None = None

    whole_network_converged: bool
    new_violation_detected: bool
    violated_equipment_count_after: int | None = None
    remaining_violation_ids: list[str]


class ContingencyResponseResponse(BaseModel):
    outage_line_id: str
    target_line_id: str | None
    target_selection: str

    delta_mw: float
    candidate_count: int

    pre_violated_equipment_count: int
    post_violated_equipment_count: int
    contingency_new_violation_count: int
    contingency_new_violation_ids: list[str]

    candidates: list[RedispatchCandidateResponse]
    best_candidate: RedispatchCandidateResponse | None