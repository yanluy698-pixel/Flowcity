"""
FlowCity constraint scheduler.

LLM may understand intent, but this module owns executable timelines. It uses
Top-K candidates, route matrix entries, budget/time constraints, and small
combination search so the first plan is already feasible before Validator runs.
"""

from __future__ import annotations

from typing import Any


AREA_LABELS = {
    "area_xa_xiaozhai": "小寨商圈",
    "area_xa_qujiang": "曲江商圈",
    "area_xa_zhonglou": "钟楼商圈",
    "area_xa_gaoxin": "高新商圈",
    "area_xa_daminggong": "大明宫-龙首原商圈",
    "area_xa_xingzheng": "行政中心商圈",
    "origin_xianyang_downtown": "咸阳市区",
    "origin_xianyang_qindu": "咸阳秦都",
}

DEFAULT_ACTIVITY_MINUTES = 90
DEFAULT_RESTAURANT_MINUTES = 75
AFTER_MEAL_WALK_MINUTES = 35
BUFFER_MINUTES = 15


def _people_total(demand: dict[str, Any]) -> int:
    people = demand.get("people", {})
    total = people.get("total")
    if isinstance(total, int) and total > 0:
        return total
    adults = people.get("adults") if isinstance(people.get("adults"), int) else 0
    return max(1, adults + len(people.get("children", [])) + len(people.get("seniors", [])))


def _budget_limit(demand: dict[str, Any]) -> float | None:
    budget = demand.get("budget", {})
    max_total = budget.get("maxTotal")
    if isinstance(max_total, (int, float)) and max_total >= 0:
        return float(max_total)
    per_person = budget.get("perPerson")
    if isinstance(per_person, (int, float)) and per_person >= 0:
        return float(per_person) * _people_total(demand)
    return None


def _budget_is_strict(demand: dict[str, Any]) -> bool:
    budget = demand.get("budget", {})
    return budget.get("flexibility") == "strict" or _budget_limit(demand) == 0


def _parse_minutes(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _default_start_minutes(demand: dict[str, Any]) -> int:
    text = _demand_text(demand)
    if any(keyword in text for keyword in ("傍晚", "晚上", "夜景", "夜游", "晚饭", "晚餐")):
        return 17 * 60
    if any(keyword in text for keyword in ("上午", "早上")):
        return 10 * 60
    return 14 * 60


def _format_minutes(value: int | None, fallback: str) -> str:
    if value is None:
        return fallback
    value %= 24 * 60
    return f"{value // 60:02d}:{value % 60:02d}"


def _demand_text(demand: dict[str, Any]) -> str:
    preferences = demand.get("preferences", {})
    constraints = demand.get("constraints", {})
    return " ".join(
        [
            str(demand.get("rawInput") or ""),
            " ".join(str(item) for item in preferences.get("activityTypes", [])),
            " ".join(str(item) for item in preferences.get("foodTags", [])),
            " ".join(str(item) for item in preferences.get("experienceTags", [])),
            " ".join(str(item) for item in preferences.get("avoidTags", [])),
            " ".join(str(item) for item in constraints.get("hard", [])),
            " ".join(str(item) for item in constraints.get("soft", [])),
        ]
    )


def _has_meal_constraint(demand: dict[str, Any]) -> bool:
    return any(keyword in _demand_text(demand) for keyword in ("晚饭", "晚餐", "正餐", "吃饭", "餐饮"))


def _meal_first(demand: dict[str, Any]) -> bool:
    text = _demand_text(demand)
    return any(keyword in text for keyword in ("先吃", "先晚饭", "先吃晚饭", "先吃饭", "吃完饭再", "饭后再"))


def _wants_after_meal_walk(demand: dict[str, Any]) -> bool:
    return any(keyword in _demand_text(demand) for keyword in ("饭后", "吃完饭", "吃完晚饭", "转一会", "散步"))


def _requires_activity(demand: dict[str, Any]) -> bool:
    text = _demand_text(demand)
    plan_control = demand.get("planControl", {})
    return bool(plan_control.get("requireActivity")) or any(
        keyword in text for keyword in ("想玩", "我要玩", "景点", "逛一下", "明确可玩活动", "不要自由活动")
    )


def _route_ref(route: dict[str, Any] | None) -> str | None:
    if not route:
        return None
    start = route.get("fromAreaId")
    end = route.get("toAreaId")
    if not start or not end:
        return None
    return f"{start}->{end}"


def _route_name(route: dict[str, Any] | None) -> str:
    if not route:
        return "路线/通勤"
    if route.get("routeSummary"):
        return str(route["routeSummary"])
    start = AREA_LABELS.get(str(route.get("fromAreaId")), str(route.get("fromAreaId")))
    end = AREA_LABELS.get(str(route.get("toAreaId")), str(route.get("toAreaId")))
    transport = {"public_transport": "公共交通", "taxi": "打车", "walk": "步行"}.get(
        str(route.get("transport")),
        str(route.get("transport") or "交通"),
    )
    return f"{start}到{end}，{transport}约{route.get('minutes')}分钟"


def _activity_has_slot_at(activity: dict[str, Any] | None, cursor: int | None) -> bool:
    if not activity or cursor is None:
        return True
    slots = (activity.get("availability") or {}).get("timeSlots") or []
    if not slots:
        return True
    for slot in slots:
        slot_start = _parse_minutes(slot.get("start"))
        slot_end = _parse_minutes(slot.get("end"))
        if slot_start is None or slot_end is None:
            continue
        if slot_end <= slot_start:
            slot_end += 24 * 60
        if slot_start <= cursor < slot_end:
            return True
    return False


def _route_for_area(supply: dict[str, Any], area_id: str | None) -> dict[str, Any] | None:
    if not area_id:
        return None
    routes = supply.get("routeCandidates", [])
    inbound = [
        route for route in routes
        if route.get("toAreaId") == area_id
        and (route.get("isCrossCityInbound") or route.get("routeType") == "origin_to_area")
    ]
    if inbound:
        return sorted(inbound, key=lambda item: (item.get("minutes", 999), item.get("estimatedCostTotal", 999)))[0]
    same_area = [
        route for route in routes if route.get("fromAreaId") == area_id and route.get("toAreaId") == area_id
    ]
    if same_area:
        return sorted(same_area, key=lambda item: item.get("minutes", 999))[0]
    direct = [route for route in routes if route.get("toAreaId") == area_id]
    if direct:
        return sorted(direct, key=lambda item: (item.get("minutes", 999), item.get("estimatedCostTotal", 999)))[0]
    return None


def _reverse_origin_route(supply: dict[str, Any], area_id: str | None) -> dict[str, Any] | None:
    if not area_id:
        return None
    origin_routes = [
        route for route in supply.get("routeCandidates", [])
        if route.get("toAreaId") == area_id
        and (
            str(route.get("fromAreaId", "")).startswith("origin_")
            or route.get("routeType") in {"origin_to_area", "cross_city_inbound"}
            or route.get("isCrossCityInbound")
        )
    ]
    if not origin_routes:
        return None
    inbound = sorted(origin_routes, key=lambda item: (item.get("minutes", 999), item.get("estimatedCostTotal", 999)))[0]
    return {
        **inbound,
        "fromAreaId": inbound.get("toAreaId"),
        "toAreaId": inbound.get("fromAreaId"),
        "routeType": "return_to_origin",
        "isCrossCityInbound": False,
        "routeSummary": f"返程预留：{AREA_LABELS.get(str(inbound.get('toAreaId')), str(inbound.get('toAreaId')))}回到{AREA_LABELS.get(str(inbound.get('fromAreaId')), str(inbound.get('fromAreaId')))}，按来程反向约{inbound.get('minutes')}分钟",
    }


def _should_include_return_route(demand: dict[str, Any]) -> bool:
    location = demand.get("location", {})
    if len(location.get("originPoints") or []) > 1:
        return False
    if _people_total(demand) > 2 and not any(keyword in _demand_text(demand) for keyword in ("亲子", "孩子", "父母", "老人")):
        return False
    text = _demand_text(demand)
    if any(keyword in text for keyword in ("回家", "回学校", "回去", "返程", "回到", "到家", "赶回")):
        return True
    return bool(demand.get("timeWindow", {}).get("endTime")) and any(keyword in text for keyword in ("玩到", "逛到", "待到"))


def _target_area_ids(demand: dict[str, Any]) -> set[str]:
    text = str(demand.get("location", {}).get("preferredArea") or "")
    if not text:
        return set()
    aliases = {
        "area_xa_xiaozhai": ("小寨", "赛格"),
        "area_xa_qujiang": ("曲江", "大雁塔", "大唐不夜城", "大悦城", "芙蓉园"),
        "area_xa_zhonglou": ("钟楼", "鼓楼", "回民街", "城墙", "南门"),
        "area_xa_gaoxin": ("高新", "大都荟"),
        "area_xa_daminggong": ("大明宫", "龙首原"),
        "area_xa_xingzheng": ("行政中心", "熙地港"),
    }
    return {area_id for area_id, words in aliases.items() if any(word in text for word in words)}


def _route_between(supply: dict[str, Any], start: str | None, end: str | None) -> dict[str, Any] | None:
    if not start or not end or start == end:
        return None
    routes = [
        route for route in supply.get("routeCandidates", [])
        if route.get("fromAreaId") == start and route.get("toAreaId") == end
    ]
    if not routes:
        return None
    return sorted(routes, key=lambda item: (item.get("minutes", 999), item.get("estimatedCostTotal", 999)))[0]


def _activity_minutes(activity: dict[str, Any] | None) -> int:
    if not activity:
        return 0
    return int(activity.get("suggestedDurationMinutes") or DEFAULT_ACTIVITY_MINUTES)


def _restaurant_minutes(_: dict[str, Any] | None) -> int:
    return DEFAULT_RESTAURANT_MINUTES


def _add_step(
    timeline: list[dict[str, Any]],
    *,
    cursor: int | None,
    minutes: int,
    item_type: str,
    title: str,
    description: str,
    cost: float = 0,
    poi_id: str | None = None,
    route_ref: str | None = None,
) -> int | None:
    end = None if cursor is None else cursor + minutes
    timeline.append(
        {
            "start": _format_minutes(cursor, "待定"),
            "end": _format_minutes(end, "待定"),
            "type": item_type,
            "title": title,
            "description": description,
            "poiId": poi_id,
            "routeRef": route_ref,
            "estimatedCost": cost,
        }
    )
    return end


def _build_candidate_plan(
    demand: dict[str, Any],
    supply: dict[str, Any],
    activity: dict[str, Any] | None,
    restaurant: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    start = _parse_minutes(demand.get("timeWindow", {}).get("startTime"))
    end_limit = _parse_minutes(demand.get("timeWindow", {}).get("endTime"))
    if start is None:
        start = _default_start_minutes(demand)
    if end_limit is None:
        duration_hours = demand.get("timeWindow", {}).get("durationHours")
        duration_minutes = int(duration_hours * 60) if isinstance(duration_hours, (int, float)) and duration_hours else 4 * 60
        end_limit = start + duration_minutes
    cursor = start
    timeline: list[dict[str, Any]] = []
    route_cost = 0.0
    meal_first = _meal_first(demand)
    transfer_route = None
    return_route = None

    selected_area = (restaurant or activity or {}).get("areaId") if meal_first else (activity or restaurant or {}).get("areaId")
    first_route = _route_for_area(supply, selected_area)
    if first_route and not (
        first_route.get("isCrossCityInbound") or first_route.get("routeType") == "origin_to_area"
    ):
        first_route = None
    if first_route:
        cost = float(first_route.get("estimatedCostTotal", 0))
        route_cost += cost
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=int(first_route.get("minutes", 0)),
            item_type="route",
            title="出发去目标商圈",
            description=_route_name(first_route),
            cost=cost,
            route_ref=_route_ref(first_route),
        )

    if meal_first and restaurant:
        availability = restaurant.get("availability", {})
        description = "；".join(restaurant.get("matchedReasons", [])[:4]) or "作为本次吃饭和坐下休息安排。"
        if availability:
            description += (
                f" 座位状态：{'有座' if availability.get('tableAvailable') else '未知'}，"
                f"排队约 {availability.get('queueMinutes')} 分钟。"
            )
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=_restaurant_minutes(restaurant),
            item_type="restaurant",
            title=restaurant.get("name", "吃饭地点"),
            description=description,
            cost=float(restaurant.get("estimatedCost", 0)),
            poi_id=restaurant.get("poiId"),
        )

        if restaurant and activity and restaurant.get("areaId") != activity.get("areaId"):
            transfer_route = _route_between(supply, restaurant.get("areaId"), activity.get("areaId"))
            if not transfer_route:
                return None, {"reason": "餐厅到活动缺少可用路线", "activity": activity.get("name"), "restaurant": restaurant.get("name")}
            cost = float(transfer_route.get("estimatedCostTotal", 0))
            route_cost += cost
            cursor = _add_step(
                timeline,
                cursor=cursor,
                minutes=int(transfer_route.get("minutes", 0)),
                item_type="route",
                title="吃完饭去附近活动",
                description=_route_name(transfer_route),
                cost=cost,
                route_ref=_route_ref(transfer_route),
            )

        if restaurant and activity:
            cursor = _add_step(
                timeline,
                cursor=cursor,
                minutes=BUFFER_MINUTES,
                item_type="buffer",
                title="缓冲时间",
                description="预留找路、等人和调整节奏的时间。",
            )

    if activity:
        if not _activity_has_slot_at(activity, cursor):
            return None, {
                "reason": "活动开始时间未命中可用余票时段",
                "activity": activity.get("name"),
                "start": _format_minutes(cursor, "待定"),
            }
        availability = activity.get("availability", {})
        description = "；".join(activity.get("matchedReasons", [])[:4]) or "作为本次明确活动安排。"
        if availability:
            description += (
                f" 余票 {availability.get('bestTicketLeft')}，"
                f"排队约 {availability.get('minQueueMinutes')} 分钟。"
            )
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=_activity_minutes(activity),
            item_type="activity",
            title=activity.get("name", "活动"),
            description=description,
            cost=float(activity.get("estimatedCost", 0)),
            poi_id=activity.get("poiId"),
        )

    if not meal_first:
        transfer_route = _route_between(
            supply,
            (activity or {}).get("areaId"),
            (restaurant or {}).get("areaId"),
        )
        if activity and restaurant and activity.get("areaId") != restaurant.get("areaId"):
            if not transfer_route:
                return None, {"reason": "活动到餐厅缺少可用路线", "activity": activity.get("name"), "restaurant": restaurant.get("name")}
            cost = float(transfer_route.get("estimatedCostTotal", 0))
            route_cost += cost
            cursor = _add_step(
                timeline,
                cursor=cursor,
                minutes=int(transfer_route.get("minutes", 0)),
                item_type="route",
                title="活动到吃饭地点转场",
                description=_route_name(transfer_route),
                cost=cost,
                route_ref=_route_ref(transfer_route),
            )

        if activity and restaurant:
            cursor = _add_step(
                timeline,
                cursor=cursor,
                minutes=BUFFER_MINUTES,
                item_type="buffer",
                title="缓冲时间",
                description="预留找路、等人和调整节奏的时间。",
            )

    if restaurant and not meal_first:
        availability = restaurant.get("availability", {})
        description = "；".join(restaurant.get("matchedReasons", [])[:4]) or "作为本次吃饭和坐下休息安排。"
        if availability:
            description += (
                f" 座位状态：{'有座' if availability.get('tableAvailable') else '未知'}，"
                f"排队约 {availability.get('queueMinutes')} 分钟。"
            )
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=_restaurant_minutes(restaurant),
            item_type="restaurant",
            title=restaurant.get("name", "吃饭地点"),
            description=description,
            cost=float(restaurant.get("estimatedCost", 0)),
            poi_id=restaurant.get("poiId"),
        )

    if restaurant and _wants_after_meal_walk(demand):
        area_name = restaurant.get("areaName") or AREA_LABELS.get(str(restaurant.get("areaId")), "附近")
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=AFTER_MEAL_WALK_MINUTES,
            item_type="activity",
            title=f"{area_name}附近饭后散步",
            description="吃完饭后就近走一小段，留出聊天和各自返程前的缓冲，不额外增加门票预算。",
        )

    last_area = (activity or restaurant or {}).get("areaId")
    if _should_include_return_route(demand):
        return_route = _reverse_origin_route(supply, last_area)
        if not return_route:
            return None, {"reason": "用户要求返程，但缺少返程路线估算", "areaId": last_area}
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=int(return_route.get("minutes", 0)),
            item_type="route",
            title="返程预留",
            description=return_route.get("routeSummary") or _route_name(return_route),
            cost=0,
            route_ref=None,
        )

    activity_cost = float((activity or {}).get("estimatedCost", 0))
    restaurant_cost = float((restaurant or {}).get("estimatedCost", 0))
    total_cost = activity_cost + restaurant_cost + route_cost
    people_total = _people_total(demand)
    budget_limit = _budget_limit(demand)
    if end_limit is not None and cursor is not None and cursor > end_limit:
        return None, {"reason": "超过时间窗口", "end": _format_minutes(cursor, "待定")}
    if _budget_is_strict(demand) and budget_limit is not None and total_cost > budget_limit:
        return None, {"reason": "超过严格预算", "totalCost": total_cost, "budgetLimit": budget_limit}
    if _requires_activity(demand) and not activity:
        return None, {"reason": "用户明确想玩，但组合没有活动"}

    selected_items: list[dict[str, Any]] = []
    for route in (first_route, transfer_route, return_route):
        if route:
            selected_items.append({"kind": "route", "poiId": None, "name": _route_name(route), "reason": "用于估算转场时间。"})
    if activity:
        selected_items.append({"kind": "activity", "poiId": activity.get("poiId"), "name": activity.get("name"), "reason": "匹配活动偏好并通过时间预算约束。"})
    if restaurant:
        selected_items.append({"kind": "restaurant", "poiId": restaurant.get("poiId"), "name": restaurant.get("name"), "reason": "匹配餐饮偏好并通过时间预算约束。"})

    summary_parts = []
    if first_route and first_route.get("isCrossCityInbound"):
        summary_parts.append("先完成跨城入城")
    if meal_first and restaurant:
        summary_parts.append(f"先去 {restaurant.get('name')} 吃饭")
    if activity:
        summary_parts.append(f"安排 {activity.get('name')}")
    if restaurant and not meal_first:
        summary_parts.append(f"{'再去' if summary_parts else '安排'} {restaurant.get('name')}")
    summary = "，".join(summary_parts) if summary_parts else "当前候选不足，只能形成部分规划"
    summary += f"，预估总价 {total_cost:.0f} 元，人均 {total_cost / people_total:.0f} 元。"
    tradeoffs = _tradeoff_notes(demand)
    plan = {
        "status": "ok",
        "summary": summary,
        "timeline": timeline,
        "selectedItems": selected_items,
        "budgetEstimate": {
            "activityCost": activity_cost,
            "restaurantCost": restaurant_cost,
            "routeCost": route_cost,
            "totalCost": total_cost,
            "perPersonCost": round(total_cost / people_total, 2),
            "currency": "CNY",
            "notes": ["费用来自候选供给，不代表真实交易价格。"],
        },
        "recommendationReasons": [
            reason for reason in [
                f"活动选择 {activity.get('name')}：{'；'.join(activity.get('matchedReasons', [])[:3])}" if activity else None,
                f"吃饭选择 {restaurant.get('name')}：{'；'.join(restaurant.get('matchedReasons', [])[:3])}" if restaurant else None,
                "时间轴由约束调度器按路线、预算、排队和时间窗口计算生成。",
            ] if reason
        ],
        "riskTips": ["确认前还会再次检查余票、座位、排队和路线变化。"],
        "tradeoffs": tradeoffs,
        "rawPlannerNotes": "Generated by constraint scheduler.",
    }
    score = _score_plan(demand, activity, restaurant, first_route, transfer_route, total_cost, cursor)
    return plan, {"score": score, "activity": activity.get("name") if activity else None, "restaurant": restaurant.get("name") if restaurant else None}


def _tradeoff_notes(demand: dict[str, Any]) -> list[str]:
    text = _demand_text(demand)
    notes: list[str] = []
    conflict_text = " ".join(str(item) for item in demand.get("potentialConflicts", []))
    if any(keyword in text + conflict_text for keyword in ("低成本", "不想花钱", "少花钱", "预算少", "便宜")) and any(
        keyword in text + conflict_text for keyword in ("不想太累", "走不了太多路", "少走路", "别太远")
    ):
        notes.append("用户同时表达低成本/不想花钱和不想太累/走不了太多路，调度器优先选择低预算、少转场的组合。")
    if _has_meal_constraint(demand):
        notes.append("有吃饭硬约束时，先给餐饮和座位留预算，再用剩余预算安排活动。")
    notes.append("优先保证硬约束，再在可行组合里选择更少转场、更低预算和更匹配偏好的方案。")
    return notes


def _score_plan(
    demand: dict[str, Any],
    activity: dict[str, Any] | None,
    restaurant: dict[str, Any] | None,
    first_route: dict[str, Any] | None,
    transfer_route: dict[str, Any] | None,
    total_cost: float,
    cursor: int | None,
) -> float:
    score = 0.0
    if activity:
        score += float(activity.get("score", 0)) * 3
    if restaurant:
        score += float(restaurant.get("score", 0)) * 3
    if activity and restaurant and activity.get("areaId") == restaurant.get("areaId"):
        score += 10
    if _requires_activity(demand) and activity:
        score += 12
    if _has_meal_constraint(demand) and restaurant:
        score += 10
    target_areas = _target_area_ids(demand)
    selected_areas = {item.get("areaId") for item in (activity, restaurant) if item}
    if target_areas:
        if selected_areas and selected_areas.issubset(target_areas):
            score += 30
        else:
            score -= 25
    if _meal_first(demand) and restaurant:
        score += 12
    if _wants_after_meal_walk(demand) and activity and activity.get("areaId") == (restaurant or {}).get("areaId"):
        score += 8
    if first_route:
        score -= float(first_route.get("minutes", 0)) / 8
    if transfer_route:
        score -= float(transfer_route.get("minutes", 0)) / 5
    budget_limit = _budget_limit(demand)
    if budget_limit is not None:
        score += max(0, budget_limit - total_cost) / 20
    end_limit = _parse_minutes(demand.get("timeWindow", {}).get("endTime"))
    if cursor is not None and end_limit is not None:
        score += max(0, end_limit - cursor) / 60
    return score


def schedule_timeline(
    structured_demand: dict[str, Any],
    mock_supply: dict[str, Any],
    *,
    top_k: int = 8,
) -> dict[str, Any]:
    if mock_supply.get("supplyStatus", {}).get("status") == "failed":
        return {
            "status": "failed",
            "timelinePlan": _failed_plan(mock_supply),
            "rejectedCombinations": [],
            "selectedCombination": None,
            "strategy": "supply_failed",
        }

    activities = mock_supply.get("activityCandidates", [])[:top_k]
    restaurants = mock_supply.get("restaurantCandidates", [])[:top_k]
    require_activity = _requires_activity(structured_demand)
    meal_hard = _has_meal_constraint(structured_demand)
    activity_options: list[dict[str, Any] | None] = activities if activities else [None]
    restaurant_options: list[dict[str, Any] | None] = restaurants if restaurants else [None]
    if meal_hard and not require_activity:
        activity_options = [None, *activities]
    if require_activity:
        activity_options = activities

    feasible: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    for activity in activity_options:
        for restaurant in restaurant_options:
            if activity is None and restaurant is None:
                continue
            plan, meta = _build_candidate_plan(structured_demand, mock_supply, activity, restaurant)
            if plan is None:
                rejected.append(meta)
                continue
            feasible.append((float(meta["score"]), plan, meta))

    if not feasible and meal_hard and restaurants:
        for restaurant in restaurants:
            plan, meta = _build_candidate_plan(structured_demand, mock_supply, None, restaurant)
            if plan is None:
                rejected.append(meta)
                continue
            feasible.append((float(meta["score"]) - 5, plan, meta))

    if not feasible:
        return {
            "status": "failed",
            "timelinePlan": _failed_plan(mock_supply, rejected),
            "rejectedCombinations": rejected[:20],
            "selectedCombination": None,
            "strategy": "top_k_constraint_search",
        }

    feasible.sort(key=lambda item: item[0], reverse=True)
    best_score, best_plan, best_meta = feasible[0]
    return {
        "status": "ok",
        "timelinePlan": best_plan,
        "rejectedCombinations": rejected[:20],
        "selectedCombination": {**best_meta, "score": round(best_score, 3)},
        "strategy": "top_k_constraint_search",
        "evaluatedCombinationCount": len(feasible) + len(rejected),
        "feasibleCombinationCount": len(feasible),
    }


def _failed_plan(mock_supply: dict[str, Any], rejected: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    reasons = mock_supply.get("supplyStatus", {}).get("reasons") or []
    if rejected:
        reasons.append(f"已尝试 {len(rejected)} 个候选组合，但都不满足时间/预算/路线约束")
    return {
        "status": "failed",
        "summary": "当前硬约束下没有可执行组合。",
        "timeline": [],
        "selectedItems": [],
        "budgetEstimate": {
            "activityCost": 0,
            "restaurantCost": 0,
            "routeCost": 0,
            "totalCost": 0,
            "perPersonCost": 0,
            "currency": "CNY",
            "notes": ["未形成可执行方案。"],
        },
        "recommendationReasons": [],
        "riskTips": reasons or ["需要放宽预算、时间、商圈或活动要求。"],
        "tradeoffs": ["可尝试减少活动数量、选择免费活动、放宽结束时间或提高预算。"],
        "rawPlannerNotes": "Constraint scheduler found no feasible combination.",
    }
