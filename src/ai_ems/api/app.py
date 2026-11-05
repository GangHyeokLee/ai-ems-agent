from fastapi import FastAPI

from ai_ems.network import load_identified_network
from ai_ems.network_access import NetworkBusy
from fastapi.responses import JSONResponse
from ai_ems.api.routes import create_physics_router
from ai_ems.config import CASE_FILE


def create_app(
    case_path: str = str(CASE_FILE),
) -> FastAPI:
    network = load_identified_network(case_path)

    app = FastAPI(
        title="AI_EMS_Physics API",
        version="0.1.0",
    )

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "service": "ai-ems-physics",
        }

    @app.exception_handler(NetworkBusy)
    async def network_busy(request, exc):
        return JSONResponse(status_code=409, content={"detail": "ANALYSIS_BUSY"})

    app.include_router(
        create_physics_router(
            network=network,
            case_path=case_path,
        )
    )

    return app


app = create_app()
