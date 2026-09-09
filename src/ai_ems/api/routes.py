from fastapi import APIRouter, HTTPException

from ai_ems.api.adapters import (
    to_contingency_response,
    to_security_response,
    to_sensitivity_response,
    to_redispatch_validation_response,
)
from ai_ems.api.schemas import (
    ContingencyResponseRequest,
    ContingencyResponseResponse,
    RedispatchValidationRequest,
    RedispatchValidationResponse,
    SecurityAnalysisRequest,
    SecurityAnalysisResponse,
    SensitivityAnalysisRequest,
    SensitivityAnalysisResponse,
)

from ai_ems.tools.control_tools import (
    validate_balanced_redispatch,
)
from ai_ems.tools.security_tools import (
    run_line_contingency,
    select_most_severe_violated_line,
)
from ai_ems.tools.sensitivity_tools import (
    rank_generator_sensitivities,
)

from ai_ems.tools.workflow_tools import analyze_contingency_response


def create_physics_router(
    network,
    case_path: str,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1",
        tags=["physics"],
    )

    @router.post(
        "/security-analysis",
        response_model=SecurityAnalysisResponse,
    )
    def security_analysis(
        request: SecurityAnalysisRequest,
    ):
        monitored_line_ids = (
            [request.monitored_line_id]
            if request.monitored_line_id is not None
            else None
        )

        try:
            result = run_line_contingency(
                network,
                outage_line_id=request.outage_line_id,
                monitored_line_ids=monitored_line_ids,
            )

            return to_security_response(result)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    @router.post(
        "/sensitivity-analysis",
        response_model=SensitivityAnalysisResponse,
    )
    def sensitivity_analysis(
        request: SensitivityAnalysisRequest,
    ):
        try:
            monitored_line_id = request.monitored_line_id

            target_selection = "user_specified"

            if monitored_line_id is None:
                security_result = run_line_contingency(
                    network,
                    outage_line_id=(request.outage_line_id),
                )

                selected = select_most_severe_violated_line(
                    network,
                    security_result,
                )

                if selected is None:
                    raise ValueError(
                        "No overloaded transmission "
                        "line found. Please specify "
                        "monitored_line_id."
                    )

                monitored_line_id = selected["equipment_id"]

                target_selection = "most_severe_violation"

            result = rank_generator_sensitivities(
                network,
                outage_line_id=(request.outage_line_id),
                monitored_line_id=(monitored_line_id),
                top_n=request.top_n,
            )

            return to_sensitivity_response(
                result,
                target_selection=target_selection,
            )

        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    @router.post(
        "/contingency-response",
        response_model=ContingencyResponseResponse,
    )
    def contingency_response(
        request: ContingencyResponseRequest,
    ):
        try:
            result = analyze_contingency_response(
                case_path=case_path,
                outage_line_id=request.outage_line_id,
                monitored_line_id=request.monitored_line_id,
                delta_mw=request.delta_mw,
                top_n=request.top_n,
            )

            return to_contingency_response(result)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    @router.post(
        "/redispatch-validation",
        response_model=RedispatchValidationResponse,
    )
    def redispatch_validation(
        request: RedispatchValidationRequest,
    ):
        try:
            result = validate_balanced_redispatch(
                case_path=case_path,
                outage_line_id=(request.outage_line_id),
                monitored_line_id=(request.monitored_line_id),
                up_generator_id=(request.up_generator_id),
                down_generator_id=(request.down_generator_id),
                delta_mw=request.delta_mw,
            )

            return to_redispatch_validation_response(result)

        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

        except RuntimeError as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

    return router
