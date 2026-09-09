from fastapi import APIRouter, HTTPException

from ai_ems.api.adapters import (
    to_contingency_response,
    to_security_response,
)
from ai_ems.api.schemas import (
    ContingencyResponseRequest,
    ContingencyResponseResponse,
    SecurityAnalysisRequest,
    SecurityAnalysisResponse,
)

from ai_ems.tools.security_tools import run_line_contingency
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

    return router
