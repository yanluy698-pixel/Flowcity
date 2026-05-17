"""
FlowCity Stage 5 - Validator and local replanner.

Stage 5 checks whether a Stage 4 timeline plan is feasible against the
structured demand and Stage 3 mock supply. It does not book, reserve, queue,
pay, or create execution records.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import mock_api
import planner


ROOT = Path(__file__).resolve().parent
VALIDATION_STATUSES = {"pass", "warning", "failed"}
CHECKED_DIMENSIONS = [
    "time_window",
    "business_hours",
    "budget",
    "people_fit",
    "dynamic_supply",
    "route_time",
]


def _parse_minutes(value: str | None) -> int | None:
    if not value or not isinstance(value, str) or ":" not in value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _parse_range(value: str) -> tuple[int, int]:
    start, end = value.split("-", 1)
    start_minutes = _parse_minutes(start)
    end_minutes = _parse_minutes(end)
    if start_minutes is None or end_minutes is None:
        raise ValueError(f"Invalid time range: {value}")
    if end_minutes <= start_minutes:
        end_minutes += 24 * 60
    return start_minutes, end_minutes


def _is_weekend(date_text: str | None) -> bool:
    return bool(date_text and ("周六" in date_text or "周日" in date_text or "周末" in date_text))


def _issue(
    code: str,
    dimension: str,
    severity: str,
    message: str,
    *,
    blocking: bool,
    timeline_index: int | None = None,
    poi_id: str | None = None,
    expected: Any = None,
    actual: Any = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "dimension": dimension,
        "severity": severity,
        "message": message,
        "blocking": blocking,
        "timelineIndex": timeline_index,
        "poiId": poi_id,
        "expected": expected,
        "actual": actual,
    }


def _candidate_by_id(mock_supply: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["poiId"]: item
        for item in [
            *mock_supply.get("activityCandidates", []),
            *mock_supply.get("restaurantCandidates", []),
        ]
        if item.get("poiId")
    }


def _poi_details_by_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    details: dict[str, dict[str, Any]] = {}
    for activity in data.get("activities", []):
        details[activity["id"]] = {"kind": "activity", **activity}
    for restaurant in data.get("restaurants", []):
        details[restaurant["id"]] = {"kind": "restaurant", **restaurant}
    return details


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


def _people_total(demand: dict[str, Any]) -> int:
    people = demand.get("people", {})
    total = people.get("total")
    if isinstance(total, int) and total > 0:
        return total
    adults = people.get("adults") if isinstance(people.get("adults"), int) else 0
    return max(1, adults + len(people.get("children", [])) + len(people.get("seniors", [])))


def _children_ages(demand: dict[str, Any]) -> list[int]:
    return [
        child["age"]
        for child in demand.get("people", {}).get("children", [])
        if isinstance(child.get("age"), int)
    ]


def _budget_limit(demand: dict[str, Any]) -> float | None:
    budget = demand.get("budget", {})
    max_total = budget.get("maxTotal")
    if isinstance(max_total, (int, float)):
        return float(max_total)
    per_person = budget.get("perPerson")
    if isinstance(per_person, (int, float)):
        return float(per_person) * _people_total(demand)
    return None


def _has_any_text(demand: dict[str, Any], keywords: list[str]) -> bool:
    text = json.dumps(demand, ensure_ascii=False)
    return any(keyword in text for keyword in keywords)


def _timeline_bounds(item: dict[str, Any]) -> tuple[int | None, int | None]:
    start = _parse_minutes(item.get("start"))
    end = _parse_minutes(item.get("end"))
    if start is not None and end is not None and end <= start:
        end += 24 * 60
    return start, end


def _validate_time_window(
    demand: dict[str, Any], plan: dict[str, Any]
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    timeline = [item for item in plan.get("timeline", []) if isinstance(item, dict)]
    parsed = [_timeline_bounds(item) for item in timeline]
    demand_window = demand.get("timeWindow", {})
    demand_start = _parse_minutes(demand_window.get("startTime"))
    demand_end = _parse_minutes(demand_window.get("endTime"))
    requires_concrete_time = demand_start is not None or demand_end is not None

    previous_end: int | None = None
    for index, (start, end) in enumerate(parsed):
        if start is None or end is None:
            issues.append(
                _issue(
                    "TIMELINE_TIME_MISSING",
                    "time_window",
                    "error" if requires_concrete_time else "warning",
                    "时间轴存在无法解析的开始或结束时间，无法做严格时间履约校验。",
                    blocking=requires_concrete_time,
                    timeline_index=index,
                    expected="HH:mm",
                    actual={"start": timeline[index].get("start"), "end": timeline[index].get("end")},
                )
            )
            continue
        if previous_end is not None and start < previous_end:
            issues.append(
                _issue(
                    "TIMELINE_OVERLAP",
                    "time_window",
                    "error",
                    "时间轴顺序不递增，存在重叠或倒退。",
                    blocking=True,
                    timeline_index=index,
                    expected=f">= {previous_end}",
                    actual=start,
                )
            )
        previous_end = end

    if demand_start is not None and demand_end is not None and demand_end <= demand_start:
        demand_end += 24 * 60

    known_bounds = [(start, end) for start, end in parsed if start is not None and end is not None]
    if demand_start is not None and demand_end is not None and known_bounds:
        actual_start = min(start for start, _ in known_bounds)
        actual_end = max(end for _, end in known_bounds)
        if actual_start < demand_start or actual_end > demand_end:
            flexible = bool(demand_window.get("isFlexible"))
            issues.append(
                _issue(
                    "TIME_WINDOW_OVERFLOW",
                    "time_window",
                    "warning" if flexible else "error",
                    "方案时间轴超出了用户给定时间窗口。",
                    blocking=not flexible,
                    expected={"start": demand_window.get("startTime"), "end": demand_window.get("endTime")},
                    actual={"start": timeline[0].get("start") if timeline else None, "end": timeline[-1].get("end") if timeline else None},
                )
            )

    return issues


def _validate_business_hours(
    demand: dict[str, Any],
    plan: dict[str, Any],
    mock_supply: dict[str, Any],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    details = _poi_details_by_id(data)
    candidates = _candidate_by_id(mock_supply)
    date_text = demand.get("timeWindow", {}).get("dateText")
    hours_key = "weekend" if _is_weekend(date_text) else "weekday"

    for index, item in enumerate(plan.get("timeline", [])):
        if not isinstance(item, dict):
            continue
        poi_id = item.get("poiId")
        if not poi_id or poi_id not in details:
            continue
        start, end = _timeline_bounds(item)
        if start is None or end is None:
            continue
        poi = details[poi_id]
        open_hours = poi.get("openHours", {}).get(hours_key)
        if open_hours:
            open_start, open_end = _parse_range(open_hours)
            if start < open_start or end > open_end:
                issues.append(
                    _issue(
                        "BUSINESS_HOURS_MISMATCH",
                        "business_hours",
                        "error",
                        "计划段落不完全落在 POI 营业时间内。",
                        blocking=True,
                        timeline_index=index,
                        poi_id=poi_id,
                        expected=open_hours,
                        actual={"start": item.get("start"), "end": item.get("end")},
                    )
                )

        candidate = candidates.get(poi_id, {})
        availability = candidate.get("availability") or {}
        if poi.get("kind") == "activity":
            slots = availability.get("timeSlots", [])
            if slots:
                overlaps = []
                for slot in slots:
                    slot_start = _parse_minutes(slot.get("start"))
                    slot_end = _parse_minutes(slot.get("end"))
                    if slot_start is None or slot_end is None:
                        continue
                    if slot_end <= slot_start:
                        slot_end += 24 * 60
                    overlaps.append(max(start, slot_start) < min(end, slot_end))
                if not any(overlaps):
                    issues.append(
                        _issue(
                            "ACTIVITY_SLOT_MISMATCH",
                            "business_hours",
                            "error",
                            "活动计划时间没有命中可用余票时段。",
                            blocking=True,
                            timeline_index=index,
                            poi_id=poi_id,
                            expected=[f"{slot.get('start')}-{slot.get('end')}" for slot in slots],
                            actual={"start": item.get("start"), "end": item.get("end")},
                        )
                    )
        elif poi.get("kind") == "restaurant":
            slots = availability.get("availableSlots", [])
            if slots:
                slot_minutes = [_parse_minutes(slot) for slot in slots]
                if start not in slot_minutes:
                    issues.append(
                        _issue(
                            "RESTAURANT_SLOT_WARNING",
                            "business_hours",
                            "warning",
                            "餐厅计划到店时间不在明确返回的预约时段内。",
                            blocking=False,
                            timeline_index=index,
                            poi_id=poi_id,
                            expected=slots,
                            actual=item.get("start"),
                        )
                    )

    return issues


def _validate_budget(demand: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    budget = plan.get("budgetEstimate") or {}
    activity_cost = float(budget.get("activityCost") or 0)
    restaurant_cost = float(budget.get("restaurantCost") or 0)
    route_cost = float(budget.get("routeCost") or 0)
    total_cost = float(budget.get("totalCost") or 0)
    expected_total = activity_cost + restaurant_cost + route_cost
    if abs(expected_total - total_cost) > 1:
        issues.append(
            _issue(
                "BUDGET_SUM_MISMATCH",
                "budget",
                "error",
                "预算分项和总价不一致。",
                blocking=True,
                expected=expected_total,
                actual=total_cost,
            )
        )

    budget_mode = mock_api._budget_mode(demand)
    if budget_mode == "free_required" and total_cost > 0:
        issues.append(
            _issue(
                "FREE_REQUIRED_COST_FOUND",
                "budget",
                "error",
                "用户表达了预算 0/只能免费，方案中仍存在费用。",
                blocking=True,
                expected=0,
                actual=total_cost,
            )
        )
    elif budget_mode == "free_preferred" and total_cost > 0:
        issues.append(
            _issue(
                "FREE_PREFERRED_NOT_FULLY_FREE",
                "budget",
                "warning",
                "用户优先免费，但当前方案不是完全免费，可作为低消费兜底。",
                blocking=False,
                expected="优先免费",
                actual=total_cost,
            )
        )

    limit = _budget_limit(demand)
    if limit is not None and total_cost > limit:
        strict = demand.get("budget", {}).get("flexibility") == "strict"
        issues.append(
            _issue(
                "BUDGET_EXCEEDED",
                "budget",
                "error" if strict else "warning",
                "方案总价超过用户预算。",
                blocking=strict,
                expected=limit,
                actual=total_cost,
            )
        )
    return issues


def _validate_people_fit(
    demand: dict[str, Any],
    plan: dict[str, Any],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    details = _poi_details_by_id(data)
    children_ages = _children_ages(demand)
    has_children = bool(children_ages)
    wants_low_fat = _has_any_text(demand, ["低脂", "清淡", "减肥", "少油"])

    for index, item in enumerate(plan.get("timeline", [])):
        if not isinstance(item, dict):
            continue
        poi_id = item.get("poiId")
        poi = details.get(poi_id)
        if not poi:
            continue
        if poi.get("kind") == "activity":
            for age in children_ages:
                if not poi.get("ageMin", 0) <= age <= poi.get("ageMax", 99):
                    issues.append(
                        _issue(
                            "PEOPLE_AGE_MISMATCH",
                            "people_fit",
                            "error",
                            "活动不满足儿童适龄范围。",
                            blocking=True,
                            timeline_index=index,
                            poi_id=poi_id,
                            expected=f"{poi.get('ageMin')}-{poi.get('ageMax')} 岁",
                            actual=f"{age} 岁",
                        )
                    )
        elif poi.get("kind") == "restaurant":
            if has_children and not poi.get("childFriendly"):
                issues.append(
                    _issue(
                        "CHILD_FRIENDLY_MISMATCH",
                        "people_fit",
                        "error",
                        "亲子场景选择了儿童友好度不足的餐厅。",
                        blocking=True,
                        timeline_index=index,
                        poi_id=poi_id,
                    )
                )
            if wants_low_fat and not poi.get("lowFatOptions"):
                issues.append(
                    _issue(
                        "LOW_FAT_MISMATCH",
                        "people_fit",
                        "error",
                        "用户有低脂/清淡需求，但餐厅缺少对应选项。",
                        blocking=True,
                        timeline_index=index,
                        poi_id=poi_id,
                    )
                )
    return issues


def _validate_dynamic_supply(
    demand: dict[str, Any],
    plan: dict[str, Any],
    mock_supply: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    candidates = _candidate_by_id(mock_supply)
    people_total = _people_total(demand)
    queue_limit = mock_api._queue_limit(demand)

    for index, item in enumerate(plan.get("timeline", [])):
        if not isinstance(item, dict):
            continue
        poi_id = item.get("poiId")
        candidate = candidates.get(poi_id)
        if not candidate:
            continue
        availability = candidate.get("availability") or {}
        if candidate.get("kind") == "activity":
            tickets = availability.get("bestTicketLeft")
            queue = availability.get("minQueueMinutes")
            if isinstance(tickets, int) and tickets < people_total:
                issues.append(
                    _issue(
                        "TICKET_NOT_ENOUGH",
                        "dynamic_supply",
                        "error",
                        "活动余票少于同行人数。",
                        blocking=True,
                        timeline_index=index,
                        poi_id=poi_id,
                        expected=people_total,
                        actual=tickets,
                    )
                )
            if isinstance(queue, int) and queue > queue_limit:
                issues.append(
                    _issue(
                        "QUEUE_TOO_LONG",
                        "dynamic_supply",
                        "warning",
                        "活动排队时间超过用户可接受阈值。",
                        blocking=False,
                        timeline_index=index,
                        poi_id=poi_id,
                        expected=f"<= {queue_limit}",
                        actual=queue,
                    )
                )
        elif candidate.get("kind") == "restaurant":
            if availability.get("tableAvailable") is False:
                issues.append(
                    _issue(
                        "TABLE_NOT_AVAILABLE",
                        "dynamic_supply",
                        "error",
                        "餐厅暂无可用座位。",
                        blocking=True,
                        timeline_index=index,
                        poi_id=poi_id,
                    )
                )
            queue = availability.get("queueMinutes")
            if isinstance(queue, int) and queue > queue_limit:
                issues.append(
                    _issue(
                        "QUEUE_TOO_LONG",
                        "dynamic_supply",
                        "warning",
                        "餐厅排队时间超过用户可接受阈值。",
                        blocking=False,
                        timeline_index=index,
                        poi_id=poi_id,
                        expected=f"<= {queue_limit}",
                        actual=queue,
                    )
                )
    return issues


def _validate_route_time(
    demand: dict[str, Any],
    plan: dict[str, Any],
    mock_supply: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    routes = _route_by_ref(mock_supply)
    total_route_minutes = 0
    total_walk_minutes = 0
    for index, item in enumerate(plan.get("timeline", [])):
        if not isinstance(item, dict) or not item.get("routeRef"):
            continue
        route = routes.get(item["routeRef"])
        if not route:
            continue
        start, end = _timeline_bounds(item)
        planned_minutes = None if start is None or end is None else end - start
        route_minutes = int(route.get("minutes", 0))
        total_route_minutes += route_minutes
        total_walk_minutes += int(route.get("walkMinutesInsideArea", 0))
        if planned_minutes is not None and planned_minutes + 1 < route_minutes:
            issues.append(
                _issue(
                    "ROUTE_DURATION_TOO_SHORT",
                    "route_time",
                    "error",
                    "路线段预留时间少于 Mock 路线耗时。",
                    blocking=True,
                    timeline_index=index,
                    expected=route_minutes,
                    actual=planned_minutes,
                )
            )
    max_travel = demand.get("location", {}).get("maxTravelMinutes")
    if isinstance(max_travel, (int, float)) and total_route_minutes > max_travel:
        issues.append(
            _issue(
                "ROUTE_TOO_TIGHT",
                "route_time",
                "warning",
                "路线总耗时超过用户距离/通勤偏好。",
                blocking=False,
                expected=max_travel,
                actual=total_route_minutes,
            )
        )
    if _has_any_text(demand, ["少走路", "不想太累", "走不了太多路"]) and total_walk_minutes > 25:
        issues.append(
            _issue(
                "WALK_TOO_MUCH",
                "route_time",
                "warning",
                "用户希望少走路，但路线步行负担偏高。",
                blocking=False,
                expected="<= 25",
                actual=total_walk_minutes,
            )
        )
    return issues


def validate_plan(
    structured_demand: dict[str, Any],
    mock_supply: dict[str, Any],
    timeline_plan: dict[str, Any],
) -> dict[str, Any]:
    data = mock_api.load_mock_data()
    issues: list[dict[str, Any]] = []

    supply_status = mock_supply.get("supplyStatus", {})
    if supply_status.get("status") == "failed":
        issues.append(
            _issue(
                "SUPPLY_STATUS_FAILED",
                "dynamic_supply",
                "error",
                "阶段三供给层已经判定硬约束失败，阶段五不强行重排。",
                blocking=True,
                expected="可用供给",
                actual=supply_status.get("reasons", []),
            )
        )

    issues.extend(_validate_time_window(structured_demand, timeline_plan))
    issues.extend(_validate_business_hours(structured_demand, timeline_plan, mock_supply, data))
    issues.extend(_validate_budget(structured_demand, timeline_plan))
    issues.extend(_validate_people_fit(structured_demand, timeline_plan, data))
    issues.extend(_validate_dynamic_supply(structured_demand, timeline_plan, mock_supply))
    issues.extend(_validate_route_time(structured_demand, timeline_plan, mock_supply))

    blocking = any(issue["blocking"] for issue in issues)
    warnings = any(issue["severity"] == "warning" for issue in issues)
    status = "failed" if blocking else "warning" if warnings else "pass"
    return {
        "status": status,
        "issues": issues,
        "checkedDimensions": CHECKED_DIMENSIONS,
        "replanNeeded": status == "failed" and supply_status.get("status") != "failed",
        "suggestedActions": _suggested_actions(issues),
    }


def _suggested_actions(issues: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    codes = {issue.get("code") for issue in issues}
    if {"TICKET_NOT_ENOUGH", "ACTIVITY_SLOT_MISMATCH", "PEOPLE_AGE_MISMATCH"} & codes:
        actions.append("替换活动候选")
    if {"TABLE_NOT_AVAILABLE", "RESTAURANT_SLOT_WARNING", "CHILD_FRIENDLY_MISMATCH", "LOW_FAT_MISMATCH"} & codes:
        actions.append("替换餐厅候选")
    if {"ROUTE_DURATION_TOO_SHORT", "ROUTE_TOO_TIGHT", "WALK_TOO_MUCH", "TIME_WINDOW_OVERFLOW"} & codes:
        actions.append("优先同商圈组合或替换短路线")
    if {"BUDGET_EXCEEDED", "FREE_REQUIRED_COST_FOUND"} & codes:
        actions.append("替换低成本或免费候选")
    return actions or ["保留当前方案并展示风险提示"]


def _failed_poi_ids(validation_result: dict[str, Any], kind: str | None = None) -> set[str]:
    ids: set[str] = set()
    for issue in validation_result.get("issues", []):
        poi_id = issue.get("poiId")
        if not poi_id:
            continue
        if kind is None or poi_id.startswith("act_" if kind == "activity" else "res_"):
            ids.add(poi_id)
    return ids


def _filter_replan_supply(
    mock_supply: dict[str, Any],
    validation_result: dict[str, Any],
) -> dict[str, Any]:
    filtered = deepcopy(mock_supply)
    failed_activities = _failed_poi_ids(validation_result, "activity")
    failed_restaurants = _failed_poi_ids(validation_result, "restaurant")
    codes = {issue.get("code") for issue in validation_result.get("issues", []) if issue.get("blocking")}

    if codes & {"BUDGET_EXCEEDED", "FREE_REQUIRED_COST_FOUND"}:
        filtered["activityCandidates"] = sorted(
            filtered.get("activityCandidates", []),
            key=lambda item: (float(item.get("estimatedCost", 0)), -float(item.get("score", 0))),
        )
        filtered["restaurantCandidates"] = sorted(
            filtered.get("restaurantCandidates", []),
            key=lambda item: (float(item.get("estimatedCost", 0)), -float(item.get("score", 0))),
        )

    if failed_activities:
        filtered["activityCandidates"] = [
            item for item in filtered.get("activityCandidates", []) if item.get("poiId") not in failed_activities
        ]
    if failed_restaurants:
        filtered["restaurantCandidates"] = [
            item for item in filtered.get("restaurantCandidates", []) if item.get("poiId") not in failed_restaurants
        ]
    filtered["supplyStatus"] = dict(filtered.get("supplyStatus", {}), status="ok")
    return filtered


def replan_if_needed(
    structured_demand: dict[str, Any],
    mock_supply: dict[str, Any],
    timeline_plan: dict[str, Any],
    validation_result: dict[str, Any],
) -> dict[str, Any]:
    if not validation_result.get("replanNeeded"):
        return {
            "attempted": False,
            "reason": "validationResult does not require local replan.",
        }

    replan_supply = _filter_replan_supply(mock_supply, validation_result)
    if not replan_supply.get("activityCandidates") and not replan_supply.get("restaurantCandidates"):
        return {
            "attempted": True,
            "success": False,
            "reason": "No remaining local candidates after removing failed items.",
        }

    replanned = planner.draft_plan_without_llm(structured_demand, replan_supply)
    replanned_validation = validate_plan(structured_demand, replan_supply, replanned)
    return {
        "attempted": True,
        "success": replanned_validation["status"] in {"pass", "warning"},
        "replannedTimelinePlan": replanned,
        "replannedValidationResult": replanned_validation,
        "notes": ["Local Replanner only replaces local candidates and validates once more."],
    }


def validate_and_replan(
    structured_demand: dict[str, Any],
    mock_supply: dict[str, Any],
    timeline_plan: dict[str, Any],
) -> dict[str, Any]:
    validation_result = validate_plan(structured_demand, mock_supply, timeline_plan)
    replan_result = replan_if_needed(
        structured_demand, mock_supply, timeline_plan, validation_result
    )
    return {
        "validationResult": validation_result,
        "replanResult": replan_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FlowCity Stage 5 Validator.")
    parser.add_argument("--structured-demand", type=Path, required=True)
    parser.add_argument("--timeline-plan", type=Path, required=True)
    parser.add_argument("--mock-supply", type=Path)
    args = parser.parse_args()

    structured_demand = json.loads(args.structured_demand.read_text(encoding="utf-8"))
    mock_supply = (
        json.loads(args.mock_supply.read_text(encoding="utf-8"))
        if args.mock_supply
        else mock_api.search_supply(structured_demand)
    )
    timeline_plan = json.loads(args.timeline_plan.read_text(encoding="utf-8"))
    result = validate_and_replan(structured_demand, mock_supply, timeline_plan)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
