from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class FlowRunRequest(BaseModel):
    input: str = Field(..., min_length=1)
    limit: int = Field(default=8, ge=1, le=10)
    plannerLlm: bool = False
    strictPlannerLlm: bool = False
    confirmExecute: bool = False
    sessionId: str | None = None
    interactionMode: Literal["auto", "new_plan", "refine"] = "auto"
    previousPlanId: str | None = None
    hypothesisFeedback: dict[str, Any] | None = None
    constraintsPatch: dict[str, Any] | None = None


class ExecuteRequest(BaseModel):
    executionDraft: dict[str, Any] | None = None
    structuredDemand: dict[str, Any] | None = None
    timelinePlan: dict[str, Any] | None = None
    mockSupply: dict[str, Any] | None = None
    plannerLlm: bool = False
    replanOnRuntimeFailure: bool = False
    sessionId: str | None = None
    planId: str | None = None
