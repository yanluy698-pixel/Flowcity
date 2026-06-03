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
from copy import deepcopy
from pathlib import Path
from typing import Any

import mock_api
import planner
import validator


EXECUTION_DRAFT_STATUSES = {"ready", "warning", "blocked"}
EXECUTION_STATUSES = {"confirmed", "partial", "blocked", "not_requested", "replan_ready"}
RUNTIME_QUEUE_BLOCK_MINUTES = 40
RUNTIME_ROUTE_DELAY_MINUTES = 15


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


def _parse_minutes(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _timeline_minutes(item: dict[str, Any]) -> int | None:
    start = _parse_minutes(item.get("start"))
    end = _parse_minutes(item.get("end"))
    if start is None or end is None:
        return None
    if end <= start:
        end += 24 * 60
    return end - start


def _route_ref(route: dict[str, Any] | None) -> str | None:
    if not route:
        return None
    from_area = route.get("fromAreaId")
    to_area = route.get("toAreaId")
    if not from_area or not to_area:
        return None
    return f"{from_area}->{to_area}"


def _route_by_ref(mock_supply: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        ref: route
        for route in mock_supply.get("routeCandidates", [])
        if (ref := _route_ref(route))
    }


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
    routes = _route_by_ref(mock_supply)
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
            availability = candidate.get("availability") or {}
            actions.append(
                {
                    **base,
                    "actionType": "mock_ticket_lock",
                    "description": "用户确认后模拟锁定活动票。",
                    "runtimeLookup": {
                        "kind": "activity",
                        "poiId": poi_id,
                        "dateText": availability.get("dateText"),
                    },
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
                    "runtimeLookup": {
                        "kind": "restaurant",
                        "poiId": poi_id,
                        "dateText": availability.get("dateText"),
                    },
                    **_deal_preview_fields(candidate),
                }
            )
        elif item_type == "route":
            route_ref = item.get("routeRef")
            route = routes.get(route_ref)
            actions.append(
                {
                    **base,
                    "actionType": "route_reminder",
                    "routeRef": route_ref,
                    "plannedRouteMinutes": route.get("minutes") if route else _timeline_minutes(item),
                    "runtimeLookup": {
                        "kind": "route",
                        "routeRef": route_ref,
                    },
                    "description": "路线只生成出发提醒，不生成订单。",
                }
            )
    return actions


def _runtime_issue(
    code: str,
    action: dict[str, Any],
    message: str,
    *,
    blocking: bool,
    expected: Any = None,
    actual: Any = None,
    runtime_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "dimension": "runtime_supply",
        "severity": "error" if blocking else "warning",
        "blocking": blocking,
        "message": message,
        "timelineIndex": action.get("timelineIndex"),
        "poiId": action.get("poiId"),
        "routeRef": action.get("routeRef"),
        "expected": expected,
        "actual": actual,
        "runtimeEventType": (runtime_status or {}).get("eventType"),
        "runtimeMessage": (runtime_status or {}).get("runtimeMessage"),
    }


def _activity_runtime_check(
    action: dict[str, Any],
    runtime_status: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    lookup = action.get("runtimeLookup") or {}
    status = mock_api.find_runtime_activity_status(
        lookup.get("poiId") or action.get("poiId"),
        lookup.get("dateText"),
        runtime_status,
    )
    if not status:
        return None, []

    slots = status.get("timeSlots", [])
    ticket_left = max((slot.get("ticketLeft", 0) for slot in slots), default=None)
    queue_minutes = min((slot.get("queueMinutes", 0) for slot in slots), default=None)
    issues: list[dict[str, Any]] = []
    if isinstance(ticket_left, int) and ticket_left <= 0:
        issues.append(
            _runtime_issue(
                "RUNTIME_TICKET_SOLD_OUT",
                action,
                "确认前活动余票变为 0，不能继续生成 Mock 票码。",
                blocking=True,
                expected="ticketLeft > 0",
                actual=ticket_left,
                runtime_status=status,
            )
        )
    if isinstance(queue_minutes, int) and queue_minutes >= RUNTIME_QUEUE_BLOCK_MINUTES:
        issues.append(
            _runtime_issue(
                "RUNTIME_QUEUE_TOO_LONG",
                action,
                f"确认前活动排队变为 {queue_minutes} 分钟，需要换更短等待的候选。",
                blocking=True,
                expected=f"< {RUNTIME_QUEUE_BLOCK_MINUTES}",
                actual=queue_minutes,
                runtime_status=status,
            )
        )
    return status, issues


def _restaurant_runtime_check(
    action: dict[str, Any],
    runtime_status: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    lookup = action.get("runtimeLookup") or {}
    status = mock_api.find_runtime_restaurant_status(
        lookup.get("poiId") or action.get("poiId"),
        lookup.get("dateText"),
        runtime_status,
    )
    if not status:
        return None, []

    issues: list[dict[str, Any]] = []
    if status.get("tableAvailable") is False:
        issues.append(
            _runtime_issue(
                "RUNTIME_TABLE_NOT_AVAILABLE",
                action,
                "确认前餐厅变为无座或无可预约时段，不能继续生成 Mock 预约号。",
                blocking=True,
                expected="tableAvailable = true",
                actual=False,
                runtime_status=status,
            )
        )
    queue_minutes = status.get("queueMinutes")
    if isinstance(queue_minutes, int) and queue_minutes >= RUNTIME_QUEUE_BLOCK_MINUTES:
        issues.append(
            _runtime_issue(
                "RUNTIME_QUEUE_TOO_LONG",
                action,
                f"确认前餐厅排队变为 {queue_minutes} 分钟，需要换更短等待的候选。",
                blocking=True,
                expected=f"< {RUNTIME_QUEUE_BLOCK_MINUTES}",
                actual=queue_minutes,
                runtime_status=status,
            )
        )
    if action.get("actionType") == "mock_restaurant_reservation" and not status.get("availableSlots"):
        issues.append(
            _runtime_issue(
                "RUNTIME_RESERVATION_SLOT_GONE",
                action,
                "确认前餐厅可预约时段消失，不能继续生成 Mock 预约号。",
                blocking=True,
                expected="availableSlots 非空",
                actual=[],
                runtime_status=status,
            )
        )
    return status, issues


def _route_runtime_check(
    action: dict[str, Any],
    runtime_status: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    lookup = action.get("runtimeLookup") or {}
    route_ref = lookup.get("routeRef") or action.get("routeRef")
    if not route_ref:
        return None, []
    status = mock_api.find_runtime_route_status(route_ref, runtime_status)
    if not status:
        return None, []

    runtime_minutes = status.get("minutes")
    planned_minutes = action.get("plannedRouteMinutes") or status.get("originalMinutes")
    if not isinstance(runtime_minutes, int) or not isinstance(planned_minutes, int):
        return status, []
    delay = runtime_minutes - planned_minutes
    if delay < RUNTIME_ROUTE_DELAY_MINUTES:
        return status, []
    return status, [
        _runtime_issue(
            "RUNTIME_ROUTE_DELAYED",
            action,
            f"确认前路线耗时增加 {delay} 分钟，需要更新出发提醒或重新压缩时间轴。",
            blocking=True,
            expected=f"delay < {RUNTIME_ROUTE_DELAY_MINUTES}",
            actual=delay,
            runtime_status=status,
        )
    ]


def _deal_runtime_warnings(
    action: dict[str, Any],
    runtime_status: dict[str, Any],
) -> list[dict[str, Any]]:
    deal = action.get("dealPreview")
    if not isinstance(deal, dict) or not deal.get("dealId"):
        return []
    status = mock_api.find_runtime_deal_status(deal["dealId"], runtime_status)
    if not status or status.get("stockLeft", 1) > 0:
        return []
    return [
        _runtime_issue(
            "RUNTIME_DEAL_SOLD_OUT",
            action,
            "确认前团购库存售罄；不影响 Mock 预约/取号，但预算提示需要更新。",
            blocking=False,
            expected="stockLeft > 0",
            actual=status.get("stockLeft"),
            runtime_status=status,
        )
    ]


def _runtime_check_action(
    action: dict[str, Any],
    runtime_status: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    action_type = action.get("actionType")
    if action_type == "mock_ticket_lock":
        status, issues = _activity_runtime_check(action, runtime_status)
    elif action_type in {"mock_restaurant_reservation", "mock_queue_number"}:
        status, issues = _restaurant_runtime_check(action, runtime_status)
    elif action_type == "route_reminder":
        status, issues = _route_runtime_check(action, runtime_status)
    else:
        status, issues = None, []
    issues.extend(_deal_runtime_warnings(action, runtime_status))
    return status, issues


def _runtime_activity_available(item: dict[str, Any], runtime_status: dict[str, Any]) -> bool:
    status = mock_api.find_runtime_activity_status(item.get("poiId", ""), None, runtime_status)
    if not status:
        return True
    slots = status.get("timeSlots", [])
    ticket_left = max((slot.get("ticketLeft", 0) for slot in slots), default=1)
    queue_minutes = min((slot.get("queueMinutes", 0) for slot in slots), default=0)
    return ticket_left > 0 and queue_minutes < RUNTIME_QUEUE_BLOCK_MINUTES


def _runtime_restaurant_available(item: dict[str, Any], runtime_status: dict[str, Any]) -> bool:
    status = mock_api.find_runtime_restaurant_status(item.get("poiId", ""), None, runtime_status)
    if not status:
        return True
    return status.get("tableAvailable") is not False and status.get("queueMinutes", 0) < RUNTIME_QUEUE_BLOCK_MINUTES


def _runtime_alternative_candidates(
    execution_draft: dict[str, Any],
    runtime_status: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    alternatives = execution_draft.get("alternativeCandidates", {})
    return {
        "activities": [
            item
            for item in alternatives.get("activities", [])
            if _runtime_activity_available(item, runtime_status)
        ],
        "restaurants": [
            item
            for item in alternatives.get("restaurants", [])
            if _runtime_restaurant_available(item, runtime_status)
        ],
    }


def _min_slot_queue(slots: list[dict[str, Any]]) -> int:
    return min((int(slot.get("queueMinutes", 0)) for slot in slots), default=0)


def _max_slot_tickets(slots: list[dict[str, Any]]) -> int:
    return max((int(slot.get("ticketLeft", 0)) for slot in slots), default=1)


def _runtime_activity_blocks(status: dict[str, Any] | None) -> bool:
    if not status:
        return False
    slots = status.get("timeSlots", [])
    return _max_slot_tickets(slots) <= 0 or _min_slot_queue(slots) >= RUNTIME_QUEUE_BLOCK_MINUTES


def _runtime_restaurant_blocks(status: dict[str, Any] | None) -> bool:
    if not status:
        return False
    if status.get("tableAvailable") is False:
        return True
    if int(status.get("queueMinutes", 0)) >= RUNTIME_QUEUE_BLOCK_MINUTES:
        return True
    return status.get("eventType") == "reservation_slot_gone"


def _apply_runtime_to_supply(
    mock_supply: dict[str, Any],
    runtime_status: dict[str, Any],
) -> dict[str, Any]:
    runtime_supply = deepcopy(mock_supply)

    activities: list[dict[str, Any]] = []
    for item in runtime_supply.get("activityCandidates", []):
        status = mock_api.find_runtime_activity_status(item.get("poiId", ""), None, runtime_status)
        if _runtime_activity_blocks(status):
            continue
        if status:
            slots = status.get("timeSlots", [])
            item["availability"] = {
                "dateText": status.get("dateText"),
                "timeSlots": slots,
                "bestTicketLeft": _max_slot_tickets(slots),
                "minQueueMinutes": _min_slot_queue(slots),
                "runtimeState": status.get("runtimeState"),
                "runtimeEventType": status.get("eventType"),
            }
        activities.append(item)

    restaurants: list[dict[str, Any]] = []
    for item in runtime_supply.get("restaurantCandidates", []):
        status = mock_api.find_runtime_restaurant_status(item.get("poiId", ""), None, runtime_status)
        if _runtime_restaurant_blocks(status):
            continue
        if status:
            item["availability"] = {
                "dateText": status.get("dateText"),
                "queueMinutes": status.get("queueMinutes"),
                "tableAvailable": status.get("tableAvailable"),
                "availableSlots": status.get("availableSlots", []),
                "runtimeState": status.get("runtimeState"),
                "runtimeEventType": status.get("eventType"),
            }
        restaurants.append(item)

    route_status_by_ref = {
        status.get("routeRef"): status
        for status in runtime_status.get("routeRuntimeStatus", [])
        if status.get("routeRef")
    }
    routes: list[dict[str, Any]] = []
    for route in runtime_supply.get("routeCandidates", []):
        ref = _route_ref(route)
        status = route_status_by_ref.get(ref)
        if status and isinstance(status.get("minutes"), int):
            route["originalMinutes"] = route.get("minutes")
            route["minutes"] = status["minutes"]
            route["runtimeState"] = status.get("runtimeState")
            route["runtimeEventType"] = status.get("eventType")
            route["mockDescription"] = status.get("runtimeMessage") or route.get("mockDescription")
        routes.append(route)

    deal_status_by_id = {
        status.get("dealId"): status
        for status in runtime_status.get("dealRuntimeStatus", [])
        if status.get("dealId")
    }
    for item in [*activities, *restaurants]:
        updated_deals = []
        for deal in item.get("deals", []):
            status = deal_status_by_id.get(deal.get("dealId"))
            if status:
                deal = dict(deal)
                deal["stockLeft"] = status.get("stockLeft", deal.get("stockLeft"))
                deal["runtimeState"] = status.get("runtimeState")
                deal["runtimeEventType"] = status.get("eventType")
            if deal.get("stockLeft", 0) > 0:
                updated_deals.append(deal)
        item["deals"] = updated_deals

    runtime_supply["activityCandidates"] = activities
    runtime_supply["restaurantCandidates"] = restaurants
    runtime_supply["routeCandidates"] = routes
    runtime_supply["supplyStatus"] = dict(
        runtime_supply.get("supplyStatus", {}),
        status="ok" if activities or restaurants else "partial",
        runtimeApplied=True,
    )
    return runtime_supply


def _selected_item_names(plan: dict[str, Any]) -> dict[str, list[str]]:
    selected: dict[str, list[str]] = {"activity": [], "restaurant": [], "route": []}
    for item in plan.get("selectedItems", []):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind in selected and item.get("name"):
            selected[kind].append(item["name"])
    return selected


def _replacement_summary(
    old_plan: dict[str, Any],
    new_plan: dict[str, Any],
    runtime_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    old_selected = _selected_item_names(old_plan)
    new_selected = _selected_item_names(new_plan)
    return {
        "changedBecause": [
            issue.get("message")
            for issue in runtime_issues
            if issue.get("blocking") and issue.get("message")
        ],
        "before": old_selected,
        "after": new_selected,
    }


def _runtime_replan(
    *,
    structured_demand: dict[str, Any] | None,
    timeline_plan: dict[str, Any] | None,
    mock_supply: dict[str, Any] | None,
    runtime_status: dict[str, Any],
    runtime_issues: list[dict[str, Any]],
    planner_llm: bool,
) -> dict[str, Any] | None:
    if not structured_demand or not timeline_plan or not mock_supply:
        return None

    runtime_supply = _apply_runtime_to_supply(mock_supply, runtime_status)
    replanned_timeline_plan = planner.plan_timeline(
        structured_demand,
        runtime_supply,
        use_llm=planner_llm,
        fallback_on_error=True,
        limit=16,
    )
    replanned_stage5 = validator.validate_and_replan(
        structured_demand,
        runtime_supply,
        replanned_timeline_plan,
    )
    replanned_execution_draft = build_execution_draft(
        replanned_timeline_plan,
        replanned_stage5["validationResult"],
        replanned_stage5["replanResult"],
        runtime_supply,
    )
    final_new_plan, final_validation, used_stage5_replan = _final_plan_and_validation(
        replanned_timeline_plan,
        replanned_stage5["validationResult"],
        replanned_stage5["replanResult"],
    )
    return {
        "status": "ready" if replanned_execution_draft.get("draftStatus") != "blocked" else "blocked",
        "replannedTimelinePlan": replanned_timeline_plan,
        "replannedValidationResult": replanned_stage5["validationResult"],
        "replannedStage5Result": replanned_stage5,
        "replannedFinalPlan": final_new_plan,
        "replannedFinalValidationResult": final_validation,
        "usedStage5Replan": used_stage5_replan,
        "replannedExecutionDraft": replanned_execution_draft,
        "runtimeSupply": runtime_supply,
        "replacementSummary": _replacement_summary(timeline_plan, final_new_plan, runtime_issues),
    }


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


def confirm_execution(
    execution_draft: dict[str, Any],
    *,
    structured_demand: dict[str, Any] | None = None,
    timeline_plan: dict[str, Any] | None = None,
    mock_supply: dict[str, Any] | None = None,
    planner_llm: bool = False,
    replan_on_runtime_failure: bool = False,
) -> dict[str, Any]:
    if execution_draft.get("draftStatus") == "blocked":
        return {
            "executionStatus": "blocked",
            "actions": [],
            "confirmationCodes": [],
            "executionSummary": "阶段五校验失败，不能执行。",
            "userNotice": execution_draft.get("blockedReason"),
        }

    runtime_status = mock_api.load_runtime_status()
    confirmed_actions: list[dict[str, Any]] = []
    blocked_actions: list[dict[str, Any]] = []
    confirmation_codes: list[dict[str, Any]] = []
    runtime_issues: list[dict[str, Any]] = []
    runtime_events_applied: list[dict[str, Any]] = []
    for action in execution_draft.get("pendingActions", []):
        action_type = action.get("actionType")
        poi_id = action.get("poiId")
        action_runtime_status, action_issues = _runtime_check_action(action, runtime_status)
        runtime_issues.extend(action_issues)
        if action_runtime_status and action_runtime_status.get("runtimeState") == "changed":
            runtime_events_applied.append(
                {
                    "timelineIndex": action.get("timelineIndex"),
                    "poiId": poi_id,
                    "routeRef": action.get("routeRef"),
                    "eventType": action_runtime_status.get("eventType"),
                    "message": action_runtime_status.get("runtimeMessage"),
                }
            )
        if any(issue.get("blocking") for issue in action_issues):
            blocked_actions.append(
                {
                    **action,
                    "status": "runtime_blocked",
                    "runtimeStatus": action_runtime_status,
                    "runtimeIssues": action_issues,
                }
            )
            continue

        confirmed = dict(action)
        if action_runtime_status:
            confirmed["runtimeStatus"] = action_runtime_status
        warnings = [issue for issue in action_issues if not issue.get("blocking")]
        if warnings:
            confirmed["runtimeWarnings"] = warnings
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

    blocking_runtime_issues = [issue for issue in runtime_issues if issue.get("blocking")]
    if blocking_runtime_issues:
        available_alternatives = _runtime_alternative_candidates(execution_draft, runtime_status)
        can_replan_with_context = bool(structured_demand and timeline_plan and mock_supply)
        runtime_replan_result = (
            _runtime_replan(
                structured_demand=structured_demand,
                timeline_plan=timeline_plan,
                mock_supply=mock_supply,
                runtime_status=runtime_status,
                runtime_issues=runtime_issues,
                planner_llm=False,
            )
            if can_replan_with_context
            else None
        )
        execution_status = (
            "replan_ready"
            if runtime_replan_result and runtime_replan_result.get("status") == "ready"
            else "partial"
            if confirmed_actions
            else "blocked"
        )
        result = {
            "executionStatus": execution_status,
            "actions": [] if runtime_replan_result else confirmed_actions,
            "blockedActions": blocked_actions,
            "confirmationCodes": [] if runtime_replan_result else confirmation_codes,
            "runtimeEventsApplied": runtime_events_applied,
            "runtimeValidationResult": {
                "status": "failed",
                "issues": runtime_issues,
                "checkedDimensions": ["runtime_supply"],
                "replanNeeded": True,
            },
            "canRuntimeReplan": can_replan_with_context,
            "executionAdjustment": {
                "status": "runtime_replanned" if runtime_replan_result else "runtime_replan_needed",
                "message": (
                    "已基于异常池生成新版方案，请确认新版下单。"
                    if runtime_replan_result
                    else "确认前 Mock 状态发生变化，已阻断受影响动作，并基于异常池筛出仍可用的替代候选。"
                ),
                "availableAlternativeCandidates": available_alternatives,
                "suggestedActions": [
                    "用 availableAlternativeCandidates 中仍可用的同类候选替换受影响 POI",
                    "若路线耗时变长，优先压缩活动时长或改用同商圈候选",
                    "若团购售罄，只更新预算提示，不自动购买团购",
                ],
            },
            "executionSummary": (
                "确认前状态变化，已生成新版 Mock 方案，等待用户再次确认。"
                if runtime_replan_result
                else "确认前状态变化，部分 Mock 执行动作未生成确认码。"
            ),
            "userNotice": "这是下单阶段读取异常池后的二次校验结果；不代表真实交易。",
            "warningsCarriedForward": execution_draft.get("warningsCarriedForward", []),
        }
        if runtime_replan_result:
            result["runtimeReplanResult"] = runtime_replan_result
        return result

    runtime_status_text = "pass" if not runtime_issues else "warning"
    return {
        "executionStatus": "confirmed",
        "actions": confirmed_actions,
        "confirmationCodes": confirmation_codes,
        "runtimeEventsApplied": runtime_events_applied,
        "runtimeValidationResult": {
            "status": runtime_status_text,
            "issues": runtime_issues,
            "checkedDimensions": ["runtime_supply"],
            "replanNeeded": False,
        },
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
