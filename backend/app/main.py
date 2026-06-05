from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.flow import router as flow_router
from app.routers.learning import router as learning_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="FlowCity Demo API",
        description="Streaming API for the FlowCity local-life planning demo.",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(flow_router, prefix="/api/flow", tags=["flow"])
    app.include_router(learning_router, prefix="/api/learning", tags=["learning-admin"])

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
