from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.schemas.flow import ExecuteRequest, FlowRunRequest


FLOWCITY_ROOT = Path(__file__).resolve().parents[3]
if str(FLOWCITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLOWCITY_ROOT))

import executor  # noqa: E402
import extractor  # noqa: E402
import mock_api  # noqa: E402
import planner  # noqa: E402
import validator  # noqa: E402


def _ndjson(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, default=str) + "\n"


def _event(
    event_type: str,
    *,
    stage: str | None = None,
    label: str | None = None,
    payload: dict[str, Any] | None = None,
    message: str | None = None,
) -> str:
    event: dict[str, Any] = {"type": event_type}
    if stage is not None:
        event["stage"] = stage
    if label is not None:
        event["label"] = label
    if payload is not None:
        event["payload"] = payload
    if message is not None:
        event["message"] = message
    return _ndjson(event)


def _limit_supply(result: dict[str, Any], limit: int) -> dict[str, Any]:
    limited = dict(result)
    limited["activityCandidates"] = result.get("activityCandidates", [])[:limit]
    limited["restaurantCandidates"] = result.get("restaurantCandidates", [])[:limit]
    limited["routeCandidates"] = result.get("routeCandidates", [])[:limit]
    return limited


def _compact_supply_counts(mock_supply: dict[str, Any]) -> dict[str, Any]:
    return {
        "activityCount": len(mock_supply.get("activityCandidates", [])),
        "restaurantCount": len(mock_supply.get("restaurantCandidates", [])),
        "routeCount": len(mock_supply.get("routeCandidates", [])),
        "supplyStatus": mock_supply.get("supplyStatus", {}),
        "toolLogs": mock_supply.get("toolLogs", []),
    }


def stream_flow_events(request: FlowRunRequest) -> Iterator[str]:
    full_supply: dict[str, Any] | None = None
    mock_supply: dict[str, Any] | None = None
    structured_demand: dict[str, Any] | None = None
    timeline_plan: dict[str, Any] | None = None
    stage5: dict[str, Any] | None = None

    try:
        yield _event("stage_start", stage="extract", label="理解需求")
        prompt = extractor.build_prompt(request.input)
        response_text = extractor.call_llm(prompt)
        structured_demand = extractor.parse_json_object(response_text)
        structured_demand = extractor.normalize_structured_demand(structured_demand)
        validation_errors = extractor.basic_validate(
            structured_demand, extractor.load_json(extractor.SCHEMA_PATH)
        )
        if validation_errors:
            raise ValueError("Stage 2 validation failed: " + "; ".join(validation_errors))
        yield _event(
            "stage_done",
            stage="extract",
            payload={"structuredDemand": structured_demand},
        )

        yield _event("stage_start", stage="supply", label="查活动、餐厅和路线")
        full_supply = mock_api.search_supply(structured_demand)
        mock_supply = _limit_supply(full_supply, request.limit)
        yield _event(
            "stage_done",
            stage="supply",
            payload={"mockSupply": mock_supply, **_compact_supply_counts(mock_supply)},
        )

        yield _event("stage_start", stage="plan", label="组合时间轴")
        timeline_plan = planner.plan_timeline(
            structured_demand,
            full_supply,
            use_llm=request.plannerLlm,
            fallback_on_error=not request.strictPlannerLlm,
            limit=max(request.limit, 1),
        )
        yield _event(
            "stage_done",
            stage="plan",
            payload={"timelinePlan": timeline_plan},
        )

        yield _event("stage_start", stage="validate", label="校验预算、余票和路线风险")
        stage5 = validator.validate_and_replan(structured_demand, full_supply, timeline_plan)
        yield _event("stage_done", stage="validate", payload=stage5)

        yield _event("stage_start", stage="execute_draft", label="生成执行草案")
        stage6 = executor.prepare_execution(
            timeline_plan,
            stage5["validationResult"],
            stage5["replanResult"],
            full_supply,
            confirm_execute=request.confirmExecute,
        )
        yield _event(
            "final",
            payload={
                "input": request.input,
                "structuredDemand": structured_demand,
                "mockSupply": mock_supply,
                "timelinePlan": timeline_plan,
                "validationResult": stage5["validationResult"],
                "replanResult": stage5["replanResult"],
                "executionDraft": stage6["executionDraft"],
                "executionResult": stage6["executionResult"],
            },
        )
    except Exception as exc:
        failed_stage = "extract"
        if structured_demand is not None and full_supply is None:
            failed_stage = "supply"
        elif full_supply is not None and timeline_plan is None:
            failed_stage = "plan"
        elif timeline_plan is not None and stage5 is None:
            failed_stage = "validate"
        elif stage5 is not None:
            failed_stage = "execute_draft"
        yield _event("error", stage=failed_stage, message=str(exc))


def confirm_execution_from_draft(request: ExecuteRequest) -> dict[str, Any]:
    return executor.confirm_execution(request.executionDraft)
