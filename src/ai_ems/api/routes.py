from fastapi import APIRouter, HTTPException

from ai_ems.api.schemas import (
    ContingencyResponseRequest,
    SecurityAnalysisRequest,
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

    @router.post("/security-analysis")
    def security_analysis(
        request: SecurityAnalysisRequest,
    ):
        monitored_line_ids = (
            [request.monitored_line_id]
            if request.monitored_line_id is not None
            else None
        )

        try:
            return run_line_contingency(
                network,
                outage_line_id=request.outage_line_id,
                monitored_line_ids=monitored_line_ids,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    @router.post("/contingency-response")
    def contingency_response(
        request: ContingencyResponseRequest,
    ):
        try:
            return analyze_contingency_response(
                case_path=case_path,
                outage_line_id=request.outage_line_id,
                monitored_line_id=request.monitored_line_id,
                delta_mw=request.delta_mw,
                top_n=request.top_n,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc
    return router
