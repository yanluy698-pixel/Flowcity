from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.schemas.flow import ExecuteRequest, FlowRunRequest
from app.services.pipeline import confirm_execution_from_draft, stream_flow_events


router = APIRouter()


@router.post("/run-stream")
def run_stream(request: FlowRunRequest) -> StreamingResponse:
    return StreamingResponse(
        stream_flow_events(request),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/execute")
def execute(request: ExecuteRequest) -> dict:
    return confirm_execution_from_draft(request)
