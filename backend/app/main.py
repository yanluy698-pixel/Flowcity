from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.flow import router as flow_router
from app.services.admin_auth import admin_routes_enabled


def _cors_origins() -> list[str]:
    raw = os.getenv("FLOWCITY_CORS_ORIGINS")
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app() -> FastAPI:
    app = FastAPI(
        title="FlowCity Demo API",
        description="Streaming API for the FlowCity local-life planning demo.",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(flow_router, prefix="/api/flow", tags=["flow"])

    if admin_routes_enabled():
        from app.routers.admin import router as admin_router
        from app.routers.learning import router as learning_router

        app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
        app.include_router(learning_router, prefix="/api/learning", tags=["learning-admin"])

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
