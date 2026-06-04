"""
FlowCity Stage 4 - LLM Planner

Goal:
structuredDemand + mockSupply -> timelinePlan.

Rules provide the boundary: compact candidates, hard failure handling, and
light output checks. The LLM plans inside that boundary. A deterministic draft
planner is kept as a fallback and for tests so the pipeline remains runnable
without network access.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import extractor
import mock_api
import scheduler


ROOT = Path(__file__).resolve().parent
PROMPT_PATH = ROOT / "planner_prompt.md"
EXAMPLES_PATH = ROOT / "examples.json"

REQUIRED_FIELDS = [
    "status",
    "summary",
    "timeline",
    "selectedItems",
    "budgetEstimate",
    "recommendationReasons",
    "riskTips",
    "tradeoffs",
    "rawPlannerNotes",
]
VALID_STATUSES = {"ok", "partial", "failed"}

AREA_LABELS = {
    "area_xa_xiaozhai": "小寨",
    "area_xa_qujiang": "曲江",
    "area_xa_zhonglou": "钟楼",
    "area_xa_gaoxin": "高新",
    "area_xa_daminggong": "大明宫",
    "area_xa_xingzheng": "行政中心",
    "origin_xianyang_downtown": "咸阳市区",
}


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _people_total(demand: dict[str, Any]) -> int:
    people = demand.get("people", {})
    total = people.get("total")
    if isinstance(total, int) and total > 0:
        return total
    adults = people.get("adults") if isinstance(people.get("adults"), int) else 0
    return max(1, adults + len(people.get("children", [])) + len(people.get("seniors", [])))


def _parse_minutes(value: str | None) -> int | None:
    if not value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _format_minutes(value: int | None) -> str | None:
    if value is None:
        return None
    value = value % (24 * 60)
    return f"{value // 60:02d}:{value % 60:02d}"


def _time_label(value: int | None, fallback: str) -> str:
    return _format_minutes(value) or fallback


def _budget_limit(demand: dict[str, Any]) -> float | None:
    budget = demand.get("budget", {})
    max_total = budget.get("maxTotal")
    if max_total == 0:
        return 0.0
    if isinstance(max_total, (int, float)) and max_total > 0:
        return float(max_total)
    per_person = budget.get("perPerson")
    if per_person == 0:
        return 0.0
    if isinstance(per_person, (int, float)) and per_person > 0:
        return float(per_person) * _people_total(demand)
    return None


def _budget_is_strict(demand: dict[str, Any]) -> bool:
    budget = demand.get("budget", {})
    return budget.get("flexibility") == "strict" or _budget_limit(demand) == 0


def _demand_text(demand: dict[str, Any]) -> str:
    preferences = demand.get("preferences", {})
    constraints = demand.get("constraints", {})
    return " ".join(
        [
            str(demand.get("rawInput") or ""),
            " ".join(str(item) for item in preferences.get("activityTypes", [])),
            " ".join(str(item) for item in preferences.get("foodTags", [])),
            " ".join(str(item) for item in preferences.get("experienceTags", [])),
            " ".join(str(item) for item in constraints.get("hard", [])),
            " ".join(str(item) for item in constraints.get("soft", [])),
        ]
    )


def _has_meal_hard_constraint(demand: dict[str, Any]) -> bool:
    text = _demand_text(demand)
    return any(keyword in text for keyword in ("晚饭", "晚餐", "正餐", "吃饭", "餐饮"))


def _wants_after_meal_walk(demand: dict[str, Any]) -> bool:
    text = _demand_text(demand)
    return any(keyword in text for keyword in ("饭后", "吃完晚饭", "吃完饭", "再转", "转一会", "散步"))


def _avoid_terms(demand: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    text = _demand_text(demand)
    for tag in demand.get("preferences", {}).get("avoidTags", []):
        value = str(tag)
        if value.startswith("避开:"):
            terms.append(value.split(":", 1)[1])
    for keyword in ("大明宫", "小寨", "钟楼", "曲江", "高新", "行政中心"):
        if any(prefix + keyword in text for prefix in ("不想去", "不要", "别去", "避开")):
            terms.append(keyword)
    deduped: list[str] = []
    for term in terms:
        if term and term not in deduped:
            deduped.append(term)
    return deduped


def _compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    availability = candidate.get("availability") or {}
    return {
        "poiId": candidate.get("poiId"),
        "name": candidate.get("name"),
        "kind": candidate.get("kind"),
        "areaName": candidate.get("areaName"),
        "areaId": candidate.get("areaId"),
        "category": candidate.get("category") or candidate.get("cuisine"),
        "score": candidate.get("score"),
        "matchedReasons": candidate.get("matchedReasons", [])[:6],
        "estimatedCost": candidate.get("estimatedCost", 0),
        "routeSummary": candidate.get("routeSummary"),
        "estimatedRouteCost": candidate.get("estimatedRouteCost", 0),
        "estimatedTotalCostWithRoute": candidate.get("estimatedTotalCostWithRoute"),
        "availability": {
            "dateText": availability.get("dateText"),
            "bestTicketLeft": availability.get("bestTicketLeft"),
            "minQueueMinutes": availability.get("minQueueMinutes"),
            "queueMinutes": availability.get("queueMinutes"),
            "tableAvailable": availability.get("tableAvailable"),
            "availableSlots": availability.get("availableSlots", [])[:4],
        },
        "deals": [
            {
                "dealId": deal.get("dealId"),
                "name": deal.get("name"),
                "price": deal.get("price"),
                "peopleCount": deal.get("peopleCount"),
                "stockLeft": deal.get("stockLeft"),
            }
            for deal in candidate.get("deals", [])[:2]
        ],
    }


def _compact_route(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "fromAreaId": route.get("fromAreaId"),
        "toAreaId": route.get("toAreaId"),
        "transport": route.get("transport"),
        "minutes": route.get("minutes"),
        "distanceKm": route.get("distanceKm"),
        "walkMinutesInsideArea": route.get("walkMinutesInsideArea"),
        "routeType": route.get("routeType"),
        "estimatedCostPerPerson": route.get("estimatedCostPerPerson", 0),
        "estimatedCostTotal": route.get("estimatedCostTotal", 0),
        "isCrossCityInbound": route.get("isCrossCityInbound", False),
    }


def compact_planner_input(
    structured_demand: dict[str, Any],
    mock_supply: dict[str, Any],
    limit: int = 5,
) -> dict[str, Any]:
    route_limit = max(limit, 8)
    return {
        "structuredDemand": structured_demand,
        "mockSupply": {
            "city": mock_supply.get("city"),
            "supplyStatus": mock_supply.get("supplyStatus", {}),
            "activityCandidates": [
                _compact_candidate(item)
                for item in mock_supply.get("activityCandidates", [])[:limit]
            ],
            "restaurantCandidates": [
                _compact_candidate(item)
                for item in mock_supply.get("restaurantCandidates", [])[:limit]
            ],
            "routeCandidates": [
                _compact_route(item)
                for item in mock_supply.get("routeCandidates", [])[:route_limit]
            ],
            "filteredOut": mock_supply.get("filteredOut", [])[:12],
        },
    }


def build_prompt(
    structured_demand: dict[str, Any],
    mock_supply: dict[str, Any],
    limit: int = 5,
) -> str:
    template = load_text(PROMPT_PATH)
    planner_input = compact_planner_input(structured_demand, mock_supply, limit)
    return template.replace(
        "{{PLANNER_INPUT}}",
        json.dumps(planner_input, ensure_ascii=False, indent=2),
    )


def build_repair_prompt(base_prompt: str, plan: dict[str, Any], errors: list[str]) -> str:
    return (
        base_prompt
        + "\n\n# 上一次输出未通过本地校验，请只返回修正后的完整 JSON\n"
        + "必须修复这些错误：\n"
        + json.dumps(errors, ensure_ascii=False, indent=2)
        + "\n\n上一次输出：\n"
        + json.dumps(plan, ensure_ascii=False, indent=2)
        + "\n\n修正原则：\n"
        + "- 如果 selected POIs 跨多个 areaId，timeline 必须包含一条 routeRef 来自 routeCandidates 的跨区路线。\n"
        + "- 如果没有合适 routeRef，请改选同一 areaId 的活动/餐厅组合，或输出 partial 并明确转场风险。\n"
        + "- 不能自造 poiId、routeRef、价格或路线。\n"
        + "- 只输出 JSON，不要解释。\n"
    )


def _candidate_ids(mock_supply: dict[str, Any]) -> set[str]:
    return {
        item["poiId"]
        for item in [
            *mock_supply.get("activityCandidates", []),
            *mock_supply.get("restaurantCandidates", []),
        ]
        if item.get("poiId")
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


def _route_refs(mock_supply: dict[str, Any]) -> set[str]:
    refs = {
        _route_ref(route)
        for route in mock_supply.get("routeCandidates", [])
        if route.get("fromAreaId") and route.get("toAreaId")
    }
    refs.update(
        f"multi_origin->{area_id}"
        for area_id, aggregate in (mock_supply.get("routeFairnessByArea") or {}).items()
        if isinstance(aggregate, dict) and aggregate.get("isComplete")
    )
    return refs


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


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def validate_timeline_plan(plan: dict[str, Any], mock_supply: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in plan:
            errors.append(f"timelinePlan.{field}: missing required field")

    status = plan.get("status")
    if status not in VALID_STATUSES:
        errors.append(f"timelinePlan.status: expected one of {sorted(VALID_STATUSES)}, got {status!r}")

    for field in ("timeline", "selectedItems", "recommendationReasons", "riskTips", "tradeoffs"):
        if field in plan and not isinstance(plan[field], list):
            errors.append(f"timelinePlan.{field}: expected array")

    if "budgetEstimate" in plan and not isinstance(plan["budgetEstimate"], dict):
        errors.append("timelinePlan.budgetEstimate: expected object")

    allowed_poi_ids = _candidate_ids(mock_supply)
    candidates = _candidate_by_id(mock_supply)
    allowed_route_refs = _route_refs(mock_supply)
    routes = _route_by_ref(mock_supply)
    selected_area_ids: list[str] = []

    for index, item in enumerate(plan.get("selectedItems", [])):
        if not isinstance(item, dict):
            errors.append(f"timelinePlan.selectedItems[{index}]: expected object")
            continue
        kind = item.get("kind")
        poi_id = item.get("poiId")
        if kind in {"activity", "restaurant"} and poi_id not in allowed_poi_ids:
            errors.append(
                f"timelinePlan.selectedItems[{index}].poiId: {poi_id!r} is not from mockSupply"
            )
        if kind in {"activity", "restaurant"} and poi_id in candidates:
            selected_area_ids.append(candidates[poi_id].get("areaId"))

    for index, item in enumerate(plan.get("timeline", [])):
        if not isinstance(item, dict):
            errors.append(f"timelinePlan.timeline[{index}]: expected object")
            continue
        poi_id = item.get("poiId")
        if poi_id is not None and poi_id not in allowed_poi_ids:
            errors.append(f"timelinePlan.timeline[{index}].poiId: {poi_id!r} is not from mockSupply")
        if poi_id in candidates:
            selected_area_ids.append(candidates[poi_id].get("areaId"))
        route_ref = item.get("routeRef")
        if route_ref is not None and route_ref not in allowed_route_refs:
            errors.append(
                f"timelinePlan.timeline[{index}].routeRef: {route_ref!r} is not from mockSupply"
            )

    if mock_supply.get("supplyStatus", {}).get("status") == "failed" and status != "failed":
        errors.append("timelinePlan.status: must be failed when mockSupply.supplyStatus.status is failed")

    budget = plan.get("budgetEstimate", {})
    if isinstance(budget, dict):
        for field in ("activityCost", "restaurantCost", "routeCost", "totalCost", "perPersonCost"):
            if _number(budget.get(field)) is None:
                errors.append(f"timelinePlan.budgetEstimate.{field}: expected number")
        activity_cost = _number(budget.get("activityCost"))
        restaurant_cost = _number(budget.get("restaurantCost"))
        route_cost = _number(budget.get("routeCost"))
        total_cost = _number(budget.get("totalCost"))
        if None not in (activity_cost, restaurant_cost, route_cost, total_cost):
            expected_total = activity_cost + restaurant_cost + route_cost
            if abs(expected_total - total_cost) > 1:
                errors.append(
                    f"timelinePlan.budgetEstimate.totalCost: expected activity+restaurant+route={expected_total}, got {total_cost}"
                )

    used_route_refs = [
        item.get("routeRef")
        for item in plan.get("timeline", [])
        if isinstance(item, dict) and item.get("routeRef")
    ]
    route_fairness = mock_supply.get("routeFairnessByArea") or {}
    route_cost_sum = 0.0
    for ref in used_route_refs:
        if ref in routes:
            route_cost_sum += float(routes[ref].get("estimatedCostTotal", 0))
        elif isinstance(ref, str) and ref.startswith("multi_origin->"):
            area_id = ref.split("->", 1)[1]
            route_cost_sum += float((route_fairness.get(area_id) or {}).get("estimatedCostTotal", 0))
    budget_route_cost = _number((plan.get("budgetEstimate") or {}).get("routeCost"))
    if budget_route_cost is not None and used_route_refs and abs(route_cost_sum - budget_route_cost) > 1:
        errors.append(
            f"timelinePlan.budgetEstimate.routeCost: expected selected route cost {route_cost_sum}, got {budget_route_cost}"
        )

    unique_area_ids = {area_id for area_id in selected_area_ids if area_id}
    if len(unique_area_ids) > 1:
        connected_pairs = {
            (routes[ref].get("fromAreaId"), routes[ref].get("toAreaId"))
            for ref in used_route_refs
            if ref in routes
        }
        if not any(start != end for start, end in connected_pairs):
            errors.append("timelinePlan.routeRef: selected POIs span multiple areas but no cross-area route is selected")

    plan_text = json.dumps(
        {
            "summary": plan.get("summary"),
            "timeline": plan.get("timeline"),
            "recommendationReasons": plan.get("recommendationReasons"),
            "riskTips": plan.get("riskTips"),
        },
        ensure_ascii=False,
    )
    if len(unique_area_ids) > 1 and any(value in plan_text for value in ("同商圈", "同一商圈", "步行可达", "步行即可")):
        errors.append("timelinePlan.text: selected POIs span multiple areas but text claims same-area walking convenience")

    return errors


def _planned_item_types(plan: dict[str, Any]) -> set[str]:
    types: set[str] = set()
    for item in [*plan.get("selectedItems", []), *plan.get("timeline", [])]:
        if isinstance(item, dict) and item.get("kind") in {"activity", "restaurant"}:
            types.add(item["kind"])
        if isinstance(item, dict) and item.get("type") in {"activity", "restaurant"}:
            types.add(item["type"])
    return types


def _llm_plan_needs_fallback(
    plan: dict[str, Any],
    structured_demand: dict[str, Any],
    mock_supply: dict[str, Any],
) -> bool:
    if plan.get("status") == "failed":
        return True
    has_activity_supply = bool(mock_supply.get("activityCandidates"))
    has_restaurant_supply = bool(mock_supply.get("restaurantCandidates"))
    planned_types = _planned_item_types(plan)
    if has_activity_supply and has_restaurant_supply and not {"activity", "restaurant"} <= planned_types:
        return True

    plan_text = json.dumps(
        {
            "summary": plan.get("summary"),
            "timeline": plan.get("timeline"),
            "riskTips": plan.get("riskTips"),
            "tradeoffs": plan.get("tradeoffs"),
        },
        ensure_ascii=False,
    )
    bad_phrases = ("餐饮需自理", "自行转场", "路线自理", "无法识别", "无法理解", "乱码", "不完整输入")
    if any(phrase in plan_text for phrase in bad_phrases):
        return True

    if _wants_after_meal_walk(structured_demand) and not any(
        keyword in plan_text for keyword in ("饭后", "散步", "转一会", "走一走", "附近转")
    ):
        return True

    for term in _avoid_terms(structured_demand):
        if term and term in plan_text:
            return True

    budget_limit = _budget_limit(structured_demand)
    total_cost = _number((plan.get("budgetEstimate") or {}).get("totalCost"))
    if _budget_is_strict(structured_demand) and budget_limit is not None and total_cost is not None:
        if total_cost > budget_limit:
            return True

    if structured_demand.get("timeWindow", {}).get("endTime") and any(
        isinstance(item, dict) and str(item.get("start", "")).startswith(("先到达", "第一段", "活动后", "最后一段"))
        for item in plan.get("timeline", [])
    ):
        return True
    return False


def _select_pair(
    activities: list[dict[str, Any]],
    restaurants: list[dict[str, Any]],
    demand: dict[str, Any],
    mock_supply: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not activities and not restaurants:
        return None, None
    if not activities:
        return None, restaurants[0]
    if not restaurants:
        return activities[0], None

    budget_limit = _budget_limit(demand)
    strict_budget = _budget_is_strict(demand)
    meal_hard = _has_meal_hard_constraint(demand)
    after_meal_walk = _wants_after_meal_walk(demand)
    best_pair: tuple[float, dict[str, Any], dict[str, Any]] | None = None
    best_feasible_pair: tuple[float, dict[str, Any], dict[str, Any]] | None = None
    for activity in activities[:8]:
        for restaurant in restaurants[:8]:
            activity_cost = float(activity.get("estimatedCost", 0))
            restaurant_cost = float(restaurant.get("estimatedCost", 0))
            first_route = _route_for_area(mock_supply, activity.get("areaId"))
            if first_route and not first_route.get("isCrossCityInbound"):
                first_route = None
            transfer_route = _route_between(
                mock_supply,
                activity.get("areaId"),
                restaurant.get("areaId"),
            )
            route_cost = float((first_route or {}).get("estimatedCostTotal", 0)) + float(
                (transfer_route or {}).get("estimatedCostTotal", 0)
            )
            total = activity_cost + restaurant_cost + route_cost
            score = float(activity.get("score", 0)) + float(restaurant.get("score", 0))
            if activity.get("areaId") == restaurant.get("areaId"):
                score += 4
            elif transfer_route:
                score += 1
            else:
                continue
            if meal_hard:
                score += 3
                if budget_limit is not None and restaurant_cost <= budget_limit * 0.55:
                    score += 3
                score -= activity_cost / 80
            if after_meal_walk:
                activity_text = " ".join([activity.get("category", ""), " ".join(activity.get("matchedReasons", []))])
                if any(keyword in activity_text for keyword in ("散步", "citywalk", "轻松", "低消费", "免费", "少排队")):
                    score += 2
            if budget_limit is not None:
                if total <= budget_limit:
                    score += 8 if strict_budget else 3
                else:
                    over_budget = total - budget_limit
                    if strict_budget:
                        score -= 100 + over_budget
                    else:
                        score -= min(6, over_budget / 50)
            score -= total / 300
            if best_pair is None or score > best_pair[0]:
                best_pair = (score, activity, restaurant)
            if budget_limit is not None and total <= budget_limit:
                if best_feasible_pair is None or score > best_feasible_pair[0]:
                    best_feasible_pair = (score, activity, restaurant)

    if strict_budget and best_feasible_pair:
        return best_feasible_pair[1], best_feasible_pair[2]
    if strict_budget and meal_hard and budget_limit is not None:
        cheapest_restaurant: tuple[float, dict[str, Any]] | None = None
        for restaurant in restaurants:
            restaurant_cost = float(restaurant.get("estimatedCost", 0))
            first_route = _route_for_area(mock_supply, restaurant.get("areaId"))
            if first_route and not first_route.get("isCrossCityInbound"):
                first_route = None
            route_cost = float((first_route or {}).get("estimatedCostTotal", 0))
            total = restaurant_cost + route_cost
            if total <= budget_limit and (
                cheapest_restaurant is None or total < cheapest_restaurant[0]
            ):
                cheapest_restaurant = (total, restaurant)
        if cheapest_restaurant:
            return None, cheapest_restaurant[1]
    if best_pair:
        return best_pair[1], best_pair[2]
    return activities[0], restaurants[0]


def _route_for_area(mock_supply: dict[str, Any], area_id: str | None) -> dict[str, Any] | None:
    if not area_id:
        return None
    routes = mock_supply.get("routeCandidates", [])
    inbound = [
        route
        for route in routes
        if route.get("toAreaId") == area_id and route.get("isCrossCityInbound")
    ]
    if inbound:
        return sorted(inbound, key=lambda item: (item.get("minutes", 999), item.get("estimatedCostTotal", 999)))[0]
    same_area = [
        route
        for route in routes
        if route.get("fromAreaId") == area_id and route.get("toAreaId") == area_id
    ]
    if same_area:
        return sorted(same_area, key=lambda item: item.get("minutes", 999))[0]
    direct = [route for route in routes if route.get("toAreaId") == area_id]
    if direct:
        return sorted(direct, key=lambda item: item.get("minutes", 999))[0]
    return None


def _route_between(
    mock_supply: dict[str, Any],
    from_area_id: str | None,
    to_area_id: str | None,
) -> dict[str, Any] | None:
    if not from_area_id or not to_area_id or from_area_id == to_area_id:
        return None
    routes = [
        route
        for route in mock_supply.get("routeCandidates", [])
        if route.get("fromAreaId") == from_area_id and route.get("toAreaId") == to_area_id
    ]
    if not routes:
        return None
    return sorted(
        routes,
        key=lambda item: (item.get("minutes", 999), item.get("estimatedCostTotal", 999)),
    )[0]


def _route_name(route: dict[str, Any] | None, selected: dict[str, Any] | None) -> str:
    if not route:
        return "路线/通勤"
    if selected and selected.get("routeSummary"):
        return selected["routeSummary"]
    if route.get("routeSummary"):
        return route["routeSummary"]
    from_name = AREA_LABELS.get(str(route.get("fromAreaId")), str(route.get("fromAreaId")))
    to_name = AREA_LABELS.get(str(route.get("toAreaId")), str(route.get("toAreaId")))
    transport_name = {"walk": "步行", "public_transport": "公共交通", "taxi": "打车"}.get(
        str(route.get("transport")),
        str(route.get("transport") or "交通"),
    )
    return f"{from_name}到{to_name}，{transport_name}约{route.get('minutes')}分钟"


def _failed_plan(structured_demand: dict[str, Any], mock_supply: dict[str, Any]) -> dict[str, Any]:
    supply_status = mock_supply.get("supplyStatus", {})
    reasons = supply_status.get("reasons") or ["阶段三供给查询未返回可用候选"]
    failed_constraints = supply_status.get("failedConstraints", [])
    return {
        "status": "failed",
        "summary": "当前硬约束下没有可用供给，阶段四不强行生成无关方案。",
        "timeline": [
            {
                "start": "当前阶段",
                "end": "当前阶段",
                "type": "note",
                "title": "供给失败",
                "description": "；".join(reasons),
                "poiId": None,
                "routeRef": None,
                "estimatedCost": 0,
            }
        ],
        "selectedItems": [],
        "budgetEstimate": {
            "activityCost": 0,
            "restaurantCost": 0,
            "routeCost": 0,
            "totalCost": 0,
            "perPersonCost": 0,
            "currency": "CNY",
            "notes": ["未形成方案，因此不估算预算。"],
        },
        "recommendationReasons": [
            "阶段三工具层已经明确硬约束失败，Planner 不能推荐不相关替代。"
        ],
        "riskTips": [
            item.get("reason", str(item)) for item in failed_constraints
        ]
        or reasons,
        "tradeoffs": [
            "需要用户放宽定向活动、日期、出发地、预算或人数等条件后，才能进入重新规划。"
        ],
        "rawPlannerNotes": "supplyStatus failed; no candidate composition attempted.",
    }


def draft_plan_without_llm(
    structured_demand: dict[str, Any],
    mock_supply: dict[str, Any],
    limit: int = 8,
) -> dict[str, Any]:
    scheduled = scheduler.schedule_timeline(structured_demand, mock_supply, top_k=max(8, limit))
    plan = scheduled["timelinePlan"]
    plan["schedulerResult"] = {
        "status": scheduled.get("status"),
        "strategy": scheduled.get("strategy"),
        "selectedCombination": scheduled.get("selectedCombination"),
        "rejectedCombinations": scheduled.get("rejectedCombinations", []),
        "evaluatedCombinationCount": scheduled.get("evaluatedCombinationCount", 0),
        "feasibleCombinationCount": scheduled.get("feasibleCombinationCount", 0),
    }
    return plan

    supply_status = mock_supply.get("supplyStatus", {}).get("status")
    if supply_status == "failed":
        return _failed_plan(structured_demand, mock_supply)

    activities = mock_supply.get("activityCandidates", [])
    restaurants = mock_supply.get("restaurantCandidates", [])
    activity, restaurant = _select_pair(activities, restaurants, structured_demand, mock_supply)
    selected_area = (activity or restaurant or {}).get("areaId")
    first_route = _route_for_area(mock_supply, selected_area)
    if first_route and not first_route.get("isCrossCityInbound"):
        first_route = None
    transfer_route = _route_between(
        mock_supply,
        (activity or {}).get("areaId"),
        (restaurant or {}).get("areaId"),
    )
    route_cost = float((first_route or {}).get("estimatedCostTotal", 0)) + float(
        (transfer_route or {}).get("estimatedCostTotal", 0)
    )
    activity_cost = float((activity or {}).get("estimatedCost", 0))
    restaurant_cost = float((restaurant or {}).get("estimatedCost", 0))
    total_cost = activity_cost + restaurant_cost + route_cost
    people_total = _people_total(structured_demand)
    per_person = round(total_cost / people_total, 2)

    time_window = structured_demand.get("timeWindow", {})
    cursor = _parse_minutes(time_window.get("startTime"))
    timeline: list[dict[str, Any]] = []
    wants_after_meal_walk = _wants_after_meal_walk(structured_demand)

    if first_route and first_route.get("minutes", 0) > 0:
        route_start = cursor
        route_end = None if cursor is None else cursor + int(first_route.get("minutes", 0))
        timeline.append(
            {
                "start": _time_label(route_start, "先到达目标商圈"),
                "end": _time_label(route_end, "到达后"),
                "type": "route",
                "title": "路线/通勤",
                "description": _route_name(first_route, activity or restaurant),
                "poiId": None,
                "routeRef": _route_ref(first_route),
                "estimatedCost": float(first_route.get("estimatedCostTotal", 0)),
            }
        )
        cursor = route_end

    if activity:
        start = cursor
        end = None if cursor is None else cursor + 90
        availability = activity.get("availability", {})
        timeline.append(
            {
                "start": _time_label(start, "第一段"),
                "end": _time_label(end, "活动结束"),
                "type": "activity",
                "title": activity["name"],
                "description": "；".join(activity.get("matchedReasons", [])[:4])
                or "作为本次主要活动。",
                "poiId": activity.get("poiId"),
                "routeRef": None,
                "estimatedCost": activity_cost,
            }
        )
        cursor = end
        if availability:
            queue = availability.get("minQueueMinutes")
            tickets = availability.get("bestTicketLeft")
            if queue is not None or tickets is not None:
                timeline[-1]["description"] += f" 余票 {tickets}，排队约 {queue} 分钟。"

    if activity and restaurant and transfer_route:
        start = cursor
        end = None if cursor is None else cursor + int(transfer_route.get("minutes", 0))
        timeline.append(
            {
                "start": _time_label(start, "活动后"),
                "end": _time_label(end, "到达餐饮点"),
                "type": "route",
                "title": "活动到餐饮转场",
                "description": _route_name(transfer_route, None),
                "poiId": None,
                "routeRef": _route_ref(transfer_route),
                "estimatedCost": float(transfer_route.get("estimatedCostTotal", 0)),
            }
        )
        cursor = end

    if activity and restaurant:
        start = cursor
        end = None if cursor is None else cursor + 15
        timeline.append(
            {
                "start": _time_label(start, "活动后"),
                "end": _time_label(end, "餐饮前"),
                "type": "buffer",
                "title": "缓冲与转场",
                "description": "预留一点找路、等人和调整节奏的时间。",
                "poiId": None,
                "routeRef": None,
                "estimatedCost": 0,
            }
        )
        cursor = end

    if restaurant:
        start = cursor
        end = None if cursor is None else cursor + 75
        availability = restaurant.get("availability", {})
        timeline.append(
            {
                "start": _time_label(start, "最后一段"),
                "end": _time_label(end, "结束"),
                "type": "restaurant",
                "title": restaurant["name"],
                "description": "；".join(restaurant.get("matchedReasons", [])[:4])
                or "作为本次吃饭和坐下休息安排。",
                "poiId": restaurant.get("poiId"),
                "routeRef": None,
                "estimatedCost": restaurant_cost,
            }
        )
        cursor = end
        queue = availability.get("queueMinutes")
        table = availability.get("tableAvailable")
        slots = availability.get("availableSlots", [])
        if queue is not None or table is not None or slots:
            timeline[-1]["description"] += (
                f" 座位状态：{'有座' if table else '未知'}，排队约 {queue} 分钟，"
                f"可选时段：{'、'.join(slots[:3]) if slots else '未返回明确预约时段'}。"
            )

    if restaurant and wants_after_meal_walk:
        start = cursor
        end = None if cursor is None else cursor + 35
        area_name = restaurant.get("areaName") or AREA_LABELS.get(str(restaurant.get("areaId")), "附近")
        timeline.append(
            {
                "start": _time_label(start, "饭后"),
                "end": _time_label(end, "结束前"),
                "type": "activity",
                "title": f"{area_name}附近饭后散步",
                "description": "吃完饭后就近走一小段，留出聊天和各自返程前的缓冲，不额外增加门票预算。",
                "poiId": None,
                "routeRef": None,
                "estimatedCost": 0,
            }
        )
        cursor = end

    selected_items: list[dict[str, Any]] = []
    for route in (first_route, transfer_route):
        if not route:
            continue
        selected_for_route = activity or restaurant if route is first_route else None
        selected_items.append(
            {
                "kind": "route",
                "poiId": None,
                "name": _route_name(route, selected_for_route),
                "reason": "用于估算通勤时间和路线成本。",
            }
        )
    if activity:
        selected_items.append(
            {
                "kind": "activity",
                "poiId": activity.get("poiId"),
                "name": activity.get("name"),
                "reason": "；".join(activity.get("matchedReasons", [])[:3]) or "活动候选综合分较高。",
            }
        )
    if restaurant:
        selected_items.append(
            {
                "kind": "restaurant",
                "poiId": restaurant.get("poiId"),
                "name": restaurant.get("name"),
                "reason": "；".join(restaurant.get("matchedReasons", [])[:3]) or "餐饮候选综合分较高。",
            }
        )

    budget_limit = _budget_limit(structured_demand)
    risk_tips: list[str] = []
    if first_route and first_route.get("isCrossCityInbound"):
        risk_tips.append(
            f"跨城入城通勤约 {first_route.get('minutes')} 分钟，路线成本约 {float(first_route.get('estimatedCostTotal', 0)):.0f} 元，会压缩可玩时间和预算。"
        )
    if transfer_route:
        risk_tips.append(
            f"活动到餐饮还需要约 {transfer_route.get('minutes')} 分钟转场，阶段五需要继续校验时间窗口。"
        )
    if budget_limit is not None and total_cost > budget_limit:
        risk_tips.append(
            f"预估总价 {total_cost:.0f} 元超过预算上限 {budget_limit:.0f} 元，阶段五需要重排或替换。"
        )
    elif budget_limit is not None:
        risk_tips.append(
            f"预估总价 {total_cost:.0f} 元，低于预算上限 {budget_limit:.0f} 元。"
        )
    if supply_status == "partial":
        risk_tips.append("活动或餐饮候选存在缺口，方案可能需要放宽条件。")
    if not risk_tips:
        risk_tips.append("已按预算、距离和体验匹配度做初步组合，确认前还会再检查余票、座位和排队。")

    tradeoffs = [
        item.get("description", "")
        for item in structured_demand.get("potentialConflicts", [])
        if item.get("description")
    ]
    if not tradeoffs:
        tradeoffs = ["优先在可用供给内平衡预算、通勤和体验匹配度。"]

    return {
        "status": "partial" if supply_status == "partial" else "ok",
        "summary": _summary(activity, restaurant, first_route, total_cost, people_total),
        "timeline": timeline,
        "selectedItems": selected_items,
        "budgetEstimate": {
            "activityCost": activity_cost,
            "restaurantCost": restaurant_cost,
            "routeCost": route_cost,
            "totalCost": total_cost,
            "perPersonCost": per_person,
            "currency": "CNY",
            "notes": ["费用来自阶段三 Mock 供给，不代表真实交易价格。"],
        },
        "recommendationReasons": _recommendation_reasons(activity, restaurant, first_route, transfer_route),
        "riskTips": risk_tips,
        "tradeoffs": tradeoffs,
        "rawPlannerNotes": "Deterministic product planner fallback.",
    }


def _summary(
    activity: dict[str, Any] | None,
    restaurant: dict[str, Any] | None,
    route: dict[str, Any] | None,
    total_cost: float,
    people_total: int,
) -> str:
    parts: list[str] = []
    if route and route.get("isCrossCityInbound"):
        parts.append("先完成跨城入城")
    if activity:
        parts.append(f"安排 {activity.get('name')}")
    if restaurant:
        parts.append(f"{'再去' if activity or parts else '安排'} {restaurant.get('name')}")
    if not parts:
        return "当前候选不足，只能形成部分规划。"
    return "，".join(parts) + f"，预估总价 {total_cost:.0f} 元，人均 {total_cost / people_total:.0f} 元。"


def _recommendation_reasons(
    activity: dict[str, Any] | None,
    restaurant: dict[str, Any] | None,
    first_route: dict[str, Any] | None,
    transfer_route: dict[str, Any] | None = None,
) -> list[str]:
    reasons: list[str] = []
    if activity:
        reasons.append(f"活动选择 {activity['name']}：{'；'.join(activity.get('matchedReasons', [])[:4])}")
    if restaurant:
        reasons.append(f"餐饮选择 {restaurant['name']}：{'；'.join(restaurant.get('matchedReasons', [])[:4])}")
    if first_route:
        reasons.append(f"路线采用 {_route_name(first_route, activity or restaurant)}。")
    if transfer_route:
        reasons.append(f"活动到餐饮转场采用 {_route_name(transfer_route, None)}。")
    if not reasons:
        reasons.append("没有足够候选可组合成完整方案。")
    return reasons


def call_planner_llm(
    structured_demand: dict[str, Any],
    mock_supply: dict[str, Any],
    limit: int = 5,
    validate: bool = False,
) -> dict[str, Any]:
    base_prompt = build_prompt(structured_demand, mock_supply, limit)
    prompt = base_prompt
    last_error: Exception | None = None
    for _ in range(3):
        response_text = extractor.call_llm(prompt)
        try:
            plan = extractor.parse_json_object(response_text)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if validate:
            errors = validate_timeline_plan(plan, mock_supply)
            if errors:
                last_error = ValueError("Stage 4 planner validation failed: " + "; ".join(errors))
                prompt = build_repair_prompt(base_prompt, plan, errors)
                continue
        return plan
    raise RuntimeError(f"Planner returned invalid output after retry: {last_error}") from last_error


def plan_timeline(
    structured_demand: dict[str, Any],
    mock_supply: dict[str, Any],
    use_llm: bool = True,
    fallback_on_error: bool = True,
    limit: int = 5,
) -> dict[str, Any]:
    print(
        "[FlowCity][Planner] mode="
        f"{'llm' if use_llm else 'deterministic'} fallback_on_error={fallback_on_error}",
        flush=True,
    )
    if use_llm:
        try:
            plan = call_planner_llm(structured_demand, mock_supply, limit, validate=True)
            errors = validate_timeline_plan(plan, mock_supply)
            if errors:
                raise ValueError("Stage 4 planner validation failed: " + "; ".join(errors))
            if _llm_plan_needs_fallback(plan, structured_demand, mock_supply):
                raise ValueError("Stage 4 planner produced an incomplete executable plan")
            return plan
        except Exception:
            if not fallback_on_error:
                raise
            print("[FlowCity][Planner] llm failed; using deterministic fallback", flush=True)

    plan = draft_plan_without_llm(structured_demand, mock_supply, limit=limit)
    errors = validate_timeline_plan(plan, mock_supply)
    if errors:
        raise ValueError("Stage 4 fallback planner validation failed: " + "; ".join(errors))
    return plan


def load_demand_and_supply_from_files(demand_path: Path, supply_path: Path | None) -> tuple[dict[str, Any], dict[str, Any]]:
    demand = mock_api.load_demand_from_file(demand_path)
    supply = load_json(supply_path) if supply_path else mock_api.search_supply(demand)
    return demand, supply


def main() -> int:
    parser = argparse.ArgumentParser(description="FlowCity Stage 4 LLM planner")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demand", type=Path, help="Path to structured demand JSON")
    source.add_argument("--example-id", help="Example id from examples.json")
    parser.add_argument("--supply", type=Path, help="Optional path to existing mock supply JSON")
    parser.add_argument("--limit", type=int, default=5, help="Candidate count sent to Planner LLM")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Use the deterministic bounded draft planner. Useful for tests and offline demos.",
    )
    parser.add_argument(
        "--strict-llm",
        action="store_true",
        help="Do not fall back to the deterministic draft if the LLM call or validation fails.",
    )
    args = parser.parse_args()

    if args.example_id:
        demand = mock_api.load_example_demand(args.example_id)
        supply = load_json(args.supply) if args.supply else mock_api.search_supply(demand)
    else:
        demand, supply = load_demand_and_supply_from_files(args.demand, args.supply)

    plan = plan_timeline(
        demand,
        supply,
        use_llm=not args.no_llm,
        fallback_on_error=not args.strict_llm,
        limit=args.limit,
    )
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
