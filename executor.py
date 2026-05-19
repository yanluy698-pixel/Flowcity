"""
FlowCity Stage 6 - Mock execution draft and confirmed execution.

Stage 6 does not call real APIs. By default it only prepares an execution
draft. Mock ticket, reservation, queue, and route reminder codes are generated
only when the caller explicitly confirms execution. Deal data is preview-only:
it can inform the user that a deal exists, but confirmation does not purchase a
deal or generate a deal code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EXECUTION_DRAFT_STATUSES = {"ready", "warning", "blocked"}
EXECUTION_STATUSES = {"confirmed", "partial", "blocked", "not_requested"}


def _stable_code(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part) for part in parts if part is not None)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefix}-{digest}"


def _final_plan_and_validation(
    timeline_plan: dict[str, Any],
    validation_result: dict[str, Any],
    replan_result: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    if replan_result and replan_result.get("success"):
        return (
            replan_result.get("replannedTimelinePlan", timeline_plan),
            replan_result.get("replannedValidationResult", validation_result),
            True,
        )
    return timeline_plan, validation_result, False


def _candidate_by_id(mock_supply: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["poiId"]: item
        for item in [
            *mock_supply.get("activityCandidates", []),
            *mock_supply.get("restaurantCandidates", []),
        ]
        if item.get("poiId")
    }


def _selected_poi_ids(plan: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for item in plan.get("timeline", []):
        if isinstance(item, dict) and item.get("poiId"):
            ids.add(item["poiId"])
    for item in plan.get("selectedItems", []):
        if isinstance(item, dict) and item.get("poiId"):
            ids.add(item["poiId"])
    return ids


def _alternative_candidates(
    mock_supply: dict[str, Any],
    final_plan: dict[str, Any],
    limit: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    selected = _selected_poi_ids(final_plan)

    def compact(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "poiId": item.get("poiId"),
            "name": item.get("name"),
            "kind": item.get("kind"),
            "areaName": item.get("areaName"),
            "estimatedCost": item.get("estimatedCost"),
            "matchedReasons": item.get("matchedReasons", [])[:3],
        }

    return {
        "activities": [
            compact(item)
            for item in mock_supply.get("activityCandidates", [])
            if item.get("poiId") not in selected
        ][:limit],
        "restaurants": [
            compact(item)
            for item in mock_supply.get("restaurantCandidates", [])
            if item.get("poiId") not in selected
        ][:limit],
    }


def _best_deal(candidate: dict[str, Any]) -> dict[str, Any] | None:
    deals = [
        deal
        for deal in candidate.get("deals", [])
        if isinstance(deal, dict) and deal.get("stockLeft", 0) > 0
    ]
    if not deals:
        return None
    return min(deals, key=lambda deal: float(deal.get("price", 0)))


def _deal_preview_fields(candidate: dict[str, Any]) -> dict[str, Any]:
    deal = _best_deal(candidate)
    if not deal:
        return {}
    return {
        "dealPreview": deal,
        "dealPreviewStatus": "preview_only",
        "dealPreviewDescription": "仅展示可选团购参考；当前 Mock 确认只预约/锁票，不自动购买团购券。",
    }


def _pending_actions(final_plan: dict[str, Any], mock_supply: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = _candidate_by_id(mock_supply)
    actions: list[dict[str, Any]] = []
    for index, item in enumerate(final_plan.get("timeline", [])):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        poi_id = item.get("poiId")
        candidate = candidates.get(poi_id, {})
        base = {
            "timelineIndex": index,
            "title": item.get("title"),
            "poiId": poi_id,
            "estimatedCost": item.get("estimatedCost", 0),
        }
        if item_type == "activity" and poi_id:
            actions.append(
                {
                    **base,
                    "actionType": "mock_ticket_lock",
                    "description": "用户确认后模拟锁定活动票。",
                    **_deal_preview_fields(candidate),
                }
            )
        elif item_type == "restaurant" and poi_id:
            availability = candidate.get("availability") or {}
            slots = availability.get("availableSlots", [])
            if slots:
                action_type = "mock_restaurant_reservation"
                description = f"用户确认后模拟预约餐厅，优先时段 {slots[0]}。"
            else:
                action_type = "mock_queue_number"
                description = "用户确认后模拟线上取号。"
            actions.append(
                {
                    **base,
                    "actionType": action_type,
                    "description": description,
                    **_deal_preview_fields(candidate),
                }
            )
        elif item_type == "route":
            actions.append(
                {
                    **base,
                    "actionType": "route_reminder",
                    "routeRef": item.get("routeRef"),
                    "description": "路线只生成出发提醒，不生成订单。",
                }
            )
    return actions


def _warning_messages(validation_result: dict[str, Any]) -> list[str]:
    return [
        issue.get("message", "")
        for issue in validation_result.get("issues", [])
        if issue.get("severity") == "warning" and issue.get("message")
    ]


def build_execution_draft(
    timeline_plan: dict[str, Any],
    validation_result: dict[str, Any],
    replan_result: dict[str, Any] | None,
    mock_supply: dict[str, Any],
) -> dict[str, Any]:
    final_plan, final_validation, used_replan = _final_plan_and_validation(
        timeline_plan, validation_result, replan_result
    )
    validation_status = final_validation.get("status")
    blocked = validation_status == "failed"
    draft_status = "blocked" if blocked else "warning" if validation_status == "warning" else "ready"

    pending_actions = [] if blocked else _pending_actions(final_plan, mock_supply)
    warnings = _warning_messages(final_validation)
    return {
        "draftStatus": draft_status,
        "sourcePlan": "replannedTimelinePlan" if used_replan else "timelinePlan",
        "requiresUserConfirmation": True,
        "pendingActions": pending_actions,
        "warningsCarriedForward": warnings,
        "alternativeCandidates": _alternative_candidates(mock_supply, final_plan),
        "estimatedTotalCost": (final_plan.get("budgetEstimate") or {}).get("totalCost", 0),
        "executionBoundary": "Mock only: no real booking, reservation, queueing, payment, or Meituan API call.",
        "blockedReason": "Stage 5 validation failed; execution is not allowed." if blocked else None,
    }


def confirm_execution(execution_draft: dict[str, Any]) -> dict[str, Any]:
    if execution_draft.get("draftStatus") == "blocked":
        return {
            "executionStatus": "blocked",
            "actions": [],
            "confirmationCodes": [],
            "executionSummary": "阶段五校验失败，不能执行。",
            "userNotice": execution_draft.get("blockedReason"),
        }

    confirmed_actions: list[dict[str, Any]] = []
    confirmation_codes: list[dict[str, Any]] = []
    for action in execution_draft.get("pendingActions", []):
        action_type = action.get("actionType")
        poi_id = action.get("poiId")
        confirmed = dict(action)
        if action_type == "mock_ticket_lock":
            code = _stable_code("TICKET", poi_id, action.get("timelineIndex"))
            confirmed["mockTicketCode"] = code
            confirmation_codes.append({"type": "ticket", "code": code, "poiId": poi_id})
        elif action_type == "mock_restaurant_reservation":
            code = _stable_code("RESERVE", poi_id, action.get("timelineIndex"))
            confirmed["mockReservationCode"] = code
            confirmation_codes.append({"type": "reservation", "code": code, "poiId": poi_id})
        elif action_type == "mock_queue_number":
            code = _stable_code("QUEUE", poi_id, action.get("timelineIndex"))
            confirmed["mockQueueNumber"] = code
            confirmation_codes.append({"type": "queue", "code": code, "poiId": poi_id})
        elif action_type == "route_reminder":
            code = _stable_code("ROUTE", action.get("routeRef"), action.get("timelineIndex"))
            confirmed["routeReminderId"] = code
            confirmation_codes.append({"type": "route_reminder", "code": code, "routeRef": action.get("routeRef")})

        confirmed["status"] = "mock_confirmed"
        confirmed_actions.append(confirmed)

    return {
        "executionStatus": "confirmed",
        "actions": confirmed_actions,
        "confirmationCodes": confirmation_codes,
        "executionSummary": "用户已确认，已生成 Mock 执行结果。",
        "userNotice": "所有票码、预约号、取号号和路线提醒码均为本地 Mock，不代表真实交易；团购仅做可选预览，未自动购买或发券。",
        "warningsCarriedForward": execution_draft.get("warningsCarriedForward", []),
    }


def prepare_execution(
    timeline_plan: dict[str, Any],
    validation_result: dict[str, Any],
    replan_result: dict[str, Any] | None,
    mock_supply: dict[str, Any],
    *,
    confirm_execute: bool = False,
) -> dict[str, Any]:
    execution_draft = build_execution_draft(
        timeline_plan, validation_result, replan_result, mock_supply
    )
    result: dict[str, Any] = {
        "executionDraft": execution_draft,
        "executionResult": {
            "executionStatus": "not_requested",
            "reason": "Waiting for explicit user confirmation.",
        },
    }
    if confirm_execute:
        result["executionResult"] = confirm_execution(execution_draft)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FlowCity Stage 6 Mock Executor.")
    parser.add_argument("--pipeline-result", type=Path, required=True)
    parser.add_argument("--confirm-execute", action="store_true")
    args = parser.parse_args()

    pipeline = json.loads(args.pipeline_result.read_text(encoding="utf-8"))
    result = prepare_execution(
        pipeline["timelinePlan"],
        pipeline["validationResult"],
        pipeline.get("replanResult"),
        pipeline["mockSupply"],
        confirm_execute=args.confirm_execute,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
