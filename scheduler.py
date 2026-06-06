"""
FlowCity constraint scheduler.

LLM may understand intent, but this module owns executable timelines. It uses
Top-K candidates, route matrix entries, budget/time constraints, and small
combination search so the first plan is already feasible before Validator runs.
"""

from __future__ import annotations

from typing import Any

import demand_profile


AREA_LABELS = {
    "area_xa_xiaozhai": "小寨商圈",
    "area_xa_qujiang": "曲江商圈",
    "area_xa_zhonglou": "钟楼商圈",
    "area_xa_gaoxin": "高新商圈",
    "area_xa_daminggong": "大明宫-龙首原商圈",
    "area_xa_xingzheng": "行政中心商圈",
    "origin_xianyang_downtown": "咸阳市区",
    "origin_xianyang_qindu": "咸阳秦都",
    "origin_xa_changan_university": "长安大学",
    "origin_xa_jiaotong_university": "西安交大",
    "origin_xa_northwest_university": "西北大学",
    "origin_xa_shaanxi_normal_university": "陕师大",
    "origin_xa_weishui_campus": "长安大学渭水校区",
}

NEARBY_AREA_IDS = {
    "area_xa_xingzheng": {"area_xa_daminggong"},
    "area_xa_daminggong": {"area_xa_xingzheng"},
    "area_xa_xiaozhai": {"area_xa_qujiang"},
    "area_xa_qujiang": {"area_xa_xiaozhai"},
}

DEFAULT_ACTIVITY_MINUTES = 90
DEFAULT_RESTAURANT_MINUTES = 75
AFTER_MEAL_WALK_MINUTES = 35
BUFFER_MINUTES = 15
DINNER_EARLIEST_MINUTES = 17 * 60 + 30
EARLY_DINNER_EARLIEST_MINUTES = 16 * 60 + 30
FILLER_MIN_GAP_MINUTES = 35
FILLER_TARGET_MINUTES = 45


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


def _parse_time_range(value: str | None) -> tuple[int, int] | None:
    if not value or "-" not in value:
        return None
    start, end = value.split("-", 1)
    start_minutes = _parse_minutes(start)
    end_minutes = _parse_minutes(end)
    if start_minutes is None or end_minutes is None:
        return None
    if end_minutes <= start_minutes:
        end_minutes += 24 * 60
    return start_minutes, end_minutes


def _is_weekend(date_text: str | None) -> bool:
    return bool(date_text and any(value in date_text for value in ("周六", "周日", "周天", "周末")))


def _poi_open_for_interval(poi: dict[str, Any] | None, demand: dict[str, Any], start: int | None, minutes: int) -> bool:
    if not poi or start is None:
        return True
    open_hours = poi.get("openHours")
    if not isinstance(open_hours, dict):
        return True
    key = "weekend" if _is_weekend(demand.get("timeWindow", {}).get("dateText")) else "weekday"
    open_range = _parse_time_range(str(open_hours.get(key) or ""))
    if not open_range:
        return True
    open_start, open_end = open_range
    end = start + minutes
    if end <= start:
        end += 24 * 60
    return open_start <= start and end <= open_end


def _default_start_minutes(demand: dict[str, Any]) -> int:
    text = _demand_text(demand)
    if any(keyword in text for keyword in ("今晚", "傍晚", "晚上", "夜景", "夜游", "晚饭", "晚餐")):
        return 18 * 60 if "今晚" in text else 17 * 60
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


def _positive_activity_text(demand: dict[str, Any]) -> str:
    preferences = demand.get("preferences", {}) if isinstance(demand.get("preferences"), dict) else {}
    constraints = demand.get("constraints", {}) if isinstance(demand.get("constraints"), dict) else {}
    return " ".join(
        [
            str(demand.get("rawInput") or ""),
            " ".join(str(item) for item in preferences.get("activityTypes", [])),
            " ".join(str(item) for item in preferences.get("experienceTags", [])),
            " ".join(str(item) for item in constraints.get("soft", [])),
        ]
    )


def _has_meal_constraint(demand: dict[str, Any]) -> bool:
    return any(keyword in _demand_text(demand) for keyword in ("晚饭", "晚餐", "正餐", "吃饭", "餐饮"))


def _requires_dinner_anchor(demand: dict[str, Any]) -> bool:
    text = _demand_text(demand)
    explicit_dinner = any(keyword in text for keyword in ("晚饭", "晚餐", "晚上吃", "傍晚吃", "夜间吃饭"))
    if explicit_dinner:
        return True
    if any(keyword in text for keyword in ("早吃", "早点吃", "先吃", "午饭", "午餐", "中午", "下午茶", "奶茶", "茶歇")):
        return False
    start = _parse_minutes(demand.get("timeWindow", {}).get("startTime"))
    end = _parse_minutes(demand.get("timeWindow", {}).get("endTime"))
    has_meal = any(keyword in text for keyword in ("吃饭", "正餐", "餐饮", "聚餐", "吃点"))
    return bool(has_meal and ((start is not None and start >= 15 * 60) or (end is not None and end >= 18 * 60 + 30)))


def _meal_timing(demand: dict[str, Any]) -> str | None:
    plan_control = demand.get("planControl", {}) if isinstance(demand.get("planControl"), dict) else {}
    timing = plan_control.get("mealTiming")
    if isinstance(timing, str):
        return timing
    patch = plan_control.get("constraintsPatch", {}) if isinstance(plan_control.get("constraintsPatch"), dict) else {}
    timing = patch.get("mealTiming")
    return timing if isinstance(timing, str) else None


def _dinner_earliest_minutes(demand: dict[str, Any]) -> int:
    return EARLY_DINNER_EARLIEST_MINUTES if _meal_timing(demand) == "earlier" else DINNER_EARLIEST_MINUTES


def _meal_first(demand: dict[str, Any]) -> bool:
    text = _demand_text(demand)
    if _meal_timing(demand) == "earlier":
        return True
    return any(keyword in text for keyword in ("先吃", "先晚饭", "先吃晚饭", "先吃饭", "吃完饭再", "饭后再"))


def _wants_after_meal_walk(demand: dict[str, Any]) -> bool:
    return any(keyword in _demand_text(demand) for keyword in ("饭后", "吃完饭", "吃完晚饭", "转一会", "散步"))


def _wants_loose_mall_stroll(demand: dict[str, Any]) -> bool:
    text = _demand_text(demand)
    location = demand.get("location", {}) if isinstance(demand.get("location"), dict) else {}
    preferred_area = str(location.get("preferredArea") or "")
    return any(keyword in text for keyword in ("转一圈", "逛商场", "商场轻逛", "简单逛", "吃点东西", "吃点", "减脂")) or any(
        keyword in preferred_area for keyword in ("熙地港", "赛格", "大都荟", "大融城", "商场")
    )


def _requires_activity(demand: dict[str, Any]) -> bool:
    text = _positive_activity_text(demand)
    plan_control = demand.get("planControl", {}) if isinstance(demand.get("planControl"), dict) else {}
    patch = plan_control.get("constraintsPatch", {}) if isinstance(plan_control.get("constraintsPatch"), dict) else {}
    if plan_control.get("skipActivity") or patch.get("skipActivity"):
        return False
    requested_components = set(
        demand.get("demandProfile", {}).get("requestedComponents", [])
    )
    if requested_components == {"activity"}:
        return True
    if _wants_loose_mall_stroll(demand) and not any(keyword in text for keyword in ("必须玩", "一定要玩", "专门玩", "买票", "游乐场")):
        return bool(plan_control.get("requireActivity"))
    return bool(plan_control.get("requireActivity")) or any(
        keyword in text for keyword in ("想玩", "我要玩", "景点", "逛一下", "明确可玩活动", "不要自由活动", "看电影", "电影票")
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


def _has_multi_origin(demand: dict[str, Any]) -> bool:
    origins = demand.get("location", {}).get("originPoints") or []
    return isinstance(origins, list) and len(origins) > 1


def _requires_inbound_route(demand: dict[str, Any]) -> bool:
    cross_city = demand.get("location", {}).get("crossCityIntent")
    return isinstance(cross_city, dict) and bool(cross_city.get("enabled"))


def _multi_origin_route_for_area(supply: dict[str, Any], area_id: str | None) -> dict[str, Any] | None:
    if not area_id:
        return None
    fairness = supply.get("routeFairnessByArea") or {}
    aggregate = fairness.get(area_id)
    if not aggregate or not aggregate.get("isComplete"):
        return None
    return aggregate


def _multi_origin_route_ref(route: dict[str, Any] | None) -> str | None:
    if not route:
        return None
    return f"multi_origin->{route.get('areaId')}"


def _multi_origin_route_description(route: dict[str, Any]) -> str:
    origin_routes = route.get("originRoutes") or []
    parts = [
        f"{item.get('displayName') or item.get('point')}约{int(item.get('minutes') or 0)}分钟"
        for item in origin_routes
    ]
    area_name = route.get("areaName") or AREA_LABELS.get(str(route.get("areaId")), str(route.get("areaId")))
    fairness_text = (
        f"最大{int(route.get('maxMinutes') or 0)}分钟，平均{route.get('avgMinutes')}分钟，方差{route.get('variance')}"
    )
    return f"多人公平集合到{area_name}：{'；'.join(parts)}。通勤{fairness_text}。"


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


def _activity_slot_start_minutes(activity: dict[str, Any] | None) -> list[int]:
    if not activity:
        return []
    slots = (activity.get("availability") or {}).get("timeSlots") or []
    parsed = [_parse_minutes(slot.get("start")) for slot in slots if isinstance(slot, dict)]
    return sorted(value for value in parsed if value is not None)


def _align_activity_start(
    timeline: list[dict[str, Any]],
    activity: dict[str, Any],
    cursor: int | None,
) -> tuple[int | None, dict[str, Any] | None]:
    if cursor is None or _activity_has_slot_at(activity, cursor):
        return cursor, None
    slots = _activity_slot_start_minutes(activity)
    if not slots:
        return cursor, None
    aligned = next((slot for slot in slots if slot >= cursor), None)
    if aligned is None:
        return None, {
            "reason": "活动无晚于预计开始时间的可用余票时段",
            "activity": activity.get("name"),
            "arrival": _format_minutes(cursor, "待定"),
            "timeSlots": (activity.get("availability") or {}).get("timeSlots", []),
        }
    wait_minutes = aligned - cursor
    _add_step(
        timeline,
        cursor=cursor,
        minutes=wait_minutes,
        item_type="buffer",
        title="活动开场前缓冲",
        description=f"活动余票时段需对齐到 {_format_minutes(aligned, '待定')}，这段时间用于集合、等位或附近轻逛。",
    )
    return aligned, None


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
    anchor_areas = demand_profile.protected_area_ids(demand)
    text = str(demand.get("location", {}).get("preferredArea") or "")
    if not text:
        return anchor_areas
    aliases = {
        "area_xa_xiaozhai": ("小寨", "赛格"),
        "area_xa_qujiang": ("曲江", "大雁塔", "大唐不夜城", "大悦城", "芙蓉园"),
        "area_xa_zhonglou": ("钟楼", "鼓楼", "回民街", "城墙", "南门"),
        "area_xa_gaoxin": ("高新", "大都荟"),
        "area_xa_daminggong": ("大明宫", "龙首原"),
        "area_xa_xingzheng": ("行政中心", "熙地港"),
    }
    return anchor_areas | {area_id for area_id, words in aliases.items() if any(word in text for word in words)}


def _nearby_target_area_ids(demand: dict[str, Any]) -> set[str]:
    nearby: set[str] = set()
    for area_id in _target_area_ids(demand):
        nearby.update(NEARBY_AREA_IDS.get(area_id, set()))
    return nearby


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
    area_id: str | None = None,
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
            "areaId": area_id,
        }
    )
    return end


def _restaurant_available_slot_minutes(restaurant: dict[str, Any] | None) -> list[int]:
    if not restaurant:
        return []
    availability = restaurant.get("availability") or {}
    slots = availability.get("availableSlots") or []
    parsed = [_parse_minutes(str(slot)) for slot in slots if slot]
    return sorted(value for value in parsed if value is not None)


def _align_restaurant_start(
    timeline: list[dict[str, Any]],
    restaurant: dict[str, Any],
    cursor: int | None,
) -> tuple[int | None, dict[str, Any] | None]:
    if cursor is None:
        return cursor, None
    slots = _restaurant_available_slot_minutes(restaurant)
    if not slots:
        return cursor, None
    aligned = next((slot for slot in slots if slot >= cursor), None)
    if aligned is None:
        return None, {
            "reason": "餐厅无晚于预计到店时间的合法预约时段",
            "restaurant": restaurant.get("name"),
            "arrival": _format_minutes(cursor, "待定"),
            "availableSlots": (restaurant.get("availability") or {}).get("availableSlots", []),
        }
    if aligned == cursor:
        return cursor, None
    wait_minutes = aligned - cursor
    if timeline and timeline[-1].get("type") == "buffer":
        is_dinner_buffer = "晚餐前" in str(timeline[-1].get("title") or "")
        timeline[-1]["end"] = _format_minutes(aligned, "待定")
        if not is_dinner_buffer:
            timeline[-1]["title"] = "预约时段等位/节奏缓冲"
            timeline[-1]["description"] = (
                f"餐厅只开放整点/半点预约，已把到店时间吸附到 {timeline[-1]['end']}，中间用于等人、取号或从已选具体 POI 转场。"
            )
        else:
            timeline[-1]["description"] = (
                f"{timeline[-1].get('description', '')} 餐厅预约时段已顺延吸附到 {timeline[-1]['end']}。"
            )
    else:
        _add_step(
            timeline,
            cursor=cursor,
            minutes=wait_minutes,
            item_type="buffer",
            title="预约时段等位缓冲",
            description=f"餐厅预约时段需对齐到 {_format_minutes(aligned, '待定')}，这段时间用于等人或附近轻逛。",
        )
    return aligned, None


def _reason_values(candidate: dict[str, Any], key: str) -> list[str]:
    details = candidate.get("reasonDetails", {}) if isinstance(candidate.get("reasonDetails"), dict) else {}
    values = details.get(key, [])
    return [str(item) for item in values if item] if isinstance(values, list) else []


def _strip_reason_prefix(value: str) -> str:
    return value.split("：", 1)[1] if "：" in value else value


def _candidate_reason_summary(candidate: dict[str, Any], *, fallback: str) -> str:
    parts: list[str] = []
    feasibility = _reason_values(candidate, "feasibility")
    explicit = _reason_values(candidate, "explicitPreference")
    profile = _reason_values(candidate, "profileAssist")
    if feasibility:
        parts.append("可执行依据：" + "、".join(feasibility[:3]))
    if explicit:
        parts.append("用户明确偏好：" + "、".join(_strip_reason_prefix(item) for item in explicit[:2]))
    if profile:
        parts.append("画像辅助参考：" + "、".join(_strip_reason_prefix(item) for item in profile[:2]))
    return "；".join(parts) or fallback


def _restaurant_description(restaurant: dict[str, Any]) -> str:
    availability = restaurant.get("availability", {})
    description = _candidate_reason_summary(restaurant, fallback="作为本次吃饭和坐下休息安排。")
    if availability:
        description += (
            f" 座位状态：{'有座' if availability.get('tableAvailable') else '未知'}，"
            f"排队约 {availability.get('queueMinutes')} 分钟。"
        )
        slots = availability.get("availableSlots") or []
        if slots:
            description += f" 可预约时段：{'、'.join(str(slot) for slot in slots[:6])}。"
    return description


def _reason_badges(
    demand: dict[str, Any],
    activity: dict[str, Any] | None,
    restaurant: dict[str, Any] | None,
    first_route: dict[str, Any] | None,
    transfer_route: dict[str, Any] | None,
    total_cost: float,
) -> list[str]:
    badges: list[str] = []
    if any(_reason_values(item, "explicitPreference") for item in (activity, restaurant) if item):
        badges.append("命中明确偏好")
    if any(_reason_values(item, "profileAssist") for item in (activity, restaurant) if item):
        badges.append("画像辅助参考")
    budget_limit = _budget_limit(demand)
    if budget_limit is not None and total_cost <= budget_limit:
        badges.append("预算合适")
    if activity and restaurant and activity.get("areaId") == restaurant.get("areaId"):
        badges.append("同商圈少折腾")
    elif not transfer_route:
        badges.append("路线简单")
    if restaurant and (restaurant.get("availability") or {}).get("tableAvailable"):
        badges.append("有座可约")
    if first_route and first_route.get("type") == "multi_origin_fairness":
        badges.append("集合更公平")
    if activity and (activity.get("availability") or {}).get("minQueueMinutes", 99) <= 15:
        badges.append("排队较短")
    result: list[str] = []
    for badge in badges:
        if badge not in result:
            result.append(badge)
    return result[:4]


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
    if end_limit is not None and end_limit <= start:
        end_limit = None
    if end_limit is None:
        duration_hours = demand.get("timeWindow", {}).get("durationHours")
        if isinstance(duration_hours, (int, float)) and duration_hours:
            duration_minutes = int(duration_hours * 60)
        elif _requires_activity(demand) and _has_meal_constraint(demand):
            duration_minutes = 5 * 60
        else:
            duration_minutes = 4 * 60
        end_limit = start + duration_minutes
    cursor = start
    timeline: list[dict[str, Any]] = []
    route_cost = 0.0
    meal_first = _meal_first(demand)
    transfer_route = None
    return_route = None

    selected_area = (restaurant or activity or {}).get("areaId") if meal_first else (activity or restaurant or {}).get("areaId")
    first_route = _multi_origin_route_for_area(supply, selected_area) if _has_multi_origin(demand) else _route_for_area(supply, selected_area)
    if _has_multi_origin(demand) and selected_area and not first_route:
        return None, {"reason": "多人公平集合缺少完整出发点路线矩阵", "areaId": selected_area}
    if _requires_inbound_route(demand) and selected_area and not first_route:
        return None, {"reason": "跨城出行缺少到目标商圈的入城路线", "areaId": selected_area}
    if first_route and first_route.get("type") != "multi_origin_fairness" and not (
        first_route.get("isCrossCityInbound") or first_route.get("routeType") == "origin_to_area"
    ):
        first_route = None
    if first_route:
        is_multi_origin_route = first_route.get("type") == "multi_origin_fairness"
        cost = float(first_route.get("estimatedCostTotal", 0))
        minutes = int(first_route.get("maxMinutes") if is_multi_origin_route else first_route.get("minutes", 0))
        route_cost += cost
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=minutes,
            item_type="multi_origin_route" if is_multi_origin_route else "route",
            title="多人公平集合到目标商圈" if is_multi_origin_route else "出发去目标商圈",
            description=_multi_origin_route_description(first_route) if is_multi_origin_route else _route_name(first_route),
            cost=cost,
            route_ref=_multi_origin_route_ref(first_route) if is_multi_origin_route else _route_ref(first_route),
            area_id=selected_area,
        )

    if meal_first and restaurant:
        cursor, align_error = _align_restaurant_start(timeline, restaurant, cursor)
        if align_error:
            return None, align_error
        if not _poi_open_for_interval(restaurant, demand, cursor, _restaurant_minutes(restaurant)):
            return None, {
                "reason": "餐厅具体到店时间不在营业时间内",
                "restaurant": restaurant.get("name"),
                "start": _format_minutes(cursor, "待定"),
            }
        description = _restaurant_description(restaurant)
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=_restaurant_minutes(restaurant),
            item_type="restaurant",
            title=restaurant.get("name", "吃饭地点"),
            description=description,
            cost=float(restaurant.get("estimatedCost", 0)),
            poi_id=restaurant.get("poiId"),
            area_id=restaurant.get("areaId"),
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
        cursor, align_error = _align_activity_start(timeline, activity, cursor)
        if align_error:
            return None, align_error
        if not _poi_open_for_interval(activity, demand, cursor, _activity_minutes(activity)):
            return None, {
                "reason": "活动具体开始时间不在营业时间内",
                "activity": activity.get("name"),
                "start": _format_minutes(cursor, "待定"),
            }
        if not _activity_has_slot_at(activity, cursor):
            return None, {
                "reason": "活动开始时间未命中可用余票时段",
                "activity": activity.get("name"),
                "start": _format_minutes(cursor, "待定"),
            }
        availability = activity.get("availability", {})
        description = _candidate_reason_summary(activity, fallback="作为本次明确活动安排。")
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
            area_id=activity.get("areaId"),
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
        dinner_earliest = _dinner_earliest_minutes(demand)
        if _requires_dinner_anchor(demand) and cursor is not None and cursor < dinner_earliest:
            gap_minutes = dinner_earliest - cursor
            cursor = _add_step(
                timeline,
                cursor=cursor,
                minutes=gap_minutes,
                item_type="buffer",
                title="晚餐前等位/休息空档",
                description=(
                    "已按你的要求把吃饭时间往前靠；这段时间用于就近坐下休息或轻逛等待。"
                    if dinner_earliest < DINNER_EARLIEST_MINUTES
                    else "晚餐默认不早于 17:30；这段时间优先就近安排可坐下休息或轻逛等待。"
                ),
            )
        cursor, align_error = _align_restaurant_start(timeline, restaurant, cursor)
        if align_error:
            return None, align_error
        if not _poi_open_for_interval(restaurant, demand, cursor, _restaurant_minutes(restaurant)):
            return None, {
                "reason": "餐厅具体到店时间不在营业时间内",
                "restaurant": restaurant.get("name"),
                "start": _format_minutes(cursor, "待定"),
            }
        description = _restaurant_description(restaurant)
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=_restaurant_minutes(restaurant),
            item_type="restaurant",
            title=restaurant.get("name", "吃饭地点"),
            description=description,
            cost=float(restaurant.get("estimatedCost", 0)),
            poi_id=restaurant.get("poiId"),
            area_id=restaurant.get("areaId"),
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
    selected_area_ids = {item.get("areaId") for item in (activity, restaurant) if item and item.get("areaId")}
    required_anchor_areas = demand_profile.required_area_ids(demand)
    if required_anchor_areas and not required_anchor_areas.issubset(selected_area_ids):
        return None, {
            "reason": "组合没有覆盖用户明确点名的目的地区域",
            "requiredAnchorAreas": sorted(required_anchor_areas),
            "selectedAreas": sorted(selected_area_ids),
        }

    selected_items: list[dict[str, Any]] = []
    for route in (first_route, transfer_route, return_route):
        if route:
            selected_items.append(
                {
                    "kind": "route",
                    "poiId": None,
                    "name": _multi_origin_route_description(route) if route.get("type") == "multi_origin_fairness" else _route_name(route),
                    "reason": "用于估算多人集合通勤公平性。" if route.get("type") == "multi_origin_fairness" else "用于估算转场时间。",
                }
            )
    if activity:
        selected_items.append(
            {
                "kind": "activity",
                "poiId": activity.get("poiId"),
                "name": activity.get("name"),
                "reason": _candidate_reason_summary(activity, fallback="通过时间、预算和供给约束。"),
                "recallSources": activity.get("recallSources", []),
            }
        )
    if restaurant:
        selected_items.append(
            {
                "kind": "restaurant",
                "poiId": restaurant.get("poiId"),
                "name": restaurant.get("name"),
                "reason": _candidate_reason_summary(restaurant, fallback="通过时间、预算和供给约束。"),
                "recallSources": restaurant.get("recallSources", []),
            }
        )

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
    target_areas = _target_area_ids(demand)
    nearby_areas = _nearby_target_area_ids(demand)
    selected_areas = {item.get("areaId") for item in (activity, restaurant) if item}
    nearby_area_reason = None
    if target_areas and selected_areas and not selected_areas.issubset(target_areas) and selected_areas.issubset(target_areas | nearby_areas):
        nearby_labels = "、".join(AREA_LABELS.get(area_id, area_id) for area_id in sorted(selected_areas - target_areas))
        nearby_area_reason = f"目标仍围绕用户指定商圈；因时间/供给匹配，允许 10 分钟内近邻 {nearby_labels} 作为补充。"
    plan = {
        "status": "ok",
        "summary": summary,
        "timeline": timeline,
        "selectedItems": selected_items,
        "reasonBadges": _reason_badges(demand, activity, restaurant, first_route, transfer_route, total_cost),
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
                f"活动选择 {activity.get('name')}：{_candidate_reason_summary(activity, fallback='通过时间、预算和供给约束。')}" if activity else None,
                f"吃饭选择 {restaurant.get('name')}：{_candidate_reason_summary(restaurant, fallback='通过时间、预算和供给约束。')}" if restaurant else None,
                (
                    f"公平集合选择 {first_route.get('areaName')}：覆盖 {first_route.get('originCount')} 个出发点，"
                    f"最大通勤 {int(first_route.get('maxMinutes') or 0)} 分钟，方差 {first_route.get('variance')}，避免只照顾某一个人。"
                    if first_route and first_route.get("type") == "multi_origin_fairness"
                    else None
                ),
                nearby_area_reason,
                "时间轴由约束调度器按路线、预算、排队和时间窗口计算生成。",
            ] if reason
        ],
        "riskTips": ["确认前还会再次检查余票、座位、排队和路线变化。"],
        "tradeoffs": tradeoffs,
        "rawPlannerNotes": "Generated by constraint scheduler.",
    }
    experience_score = _score_plan(demand, activity, restaurant, first_route, transfer_route, total_cost, cursor)
    expected_platform_revenue = sum(
        float((item or {}).get("businessMetrics", {}).get("expectedPlatformRevenue") or 0)
        for item in (activity, restaurant)
    )
    budget_limit = _budget_limit(demand)
    budget_utilization = total_cost / budget_limit if budget_limit and budget_limit > 0 else None
    low_cost_intent = any(
        keyword in _demand_text(demand)
        for keyword in ("不想花钱", "少花钱", "低成本", "省钱", "便宜", "预算越低")
    )
    if low_cost_intent:
        business_score = -total_cost / 20.0
    else:
        utilization_score = (
            max(0.0, 1.0 - abs(0.92 - min(budget_utilization, 1.2))) * 10.0
            if budget_utilization is not None
            else 0.0
        )
        business_score = expected_platform_revenue * 0.7 + utilization_score * 0.3
    plan["commercialEstimate"] = {
        "expectedPlatformRevenue": round(expected_platform_revenue, 3),
        "budgetUtilization": round(budget_utilization, 4) if budget_utilization is not None else None,
        "qualityGateApplied": True,
    }
    return plan, {
        "score": experience_score,
        "experienceScore": experience_score,
        "businessScore": round(business_score, 3),
        "expectedPlatformRevenue": round(expected_platform_revenue, 3),
        "budgetUtilization": round(budget_utilization, 4) if budget_utilization is not None else None,
        "activity": activity.get("name") if activity else None,
        "restaurant": restaurant.get("name") if restaurant else None,
    }


def _insert_filler_if_needed(
    demand: dict[str, Any],
    plan: dict[str, Any],
    fillers: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    timeline = plan.get("timeline", [])
    if not timeline or not fillers:
        return plan, None

    plan_control = demand.get("planControl", {}) if isinstance(demand.get("planControl"), dict) else {}
    buffer_index = next(
        (
            index
            for index, step in enumerate(timeline)
            if step.get("type") == "buffer"
            and (_parse_minutes(step.get("end")) or 0) - (_parse_minutes(step.get("start")) or 0) >= FILLER_MIN_GAP_MINUTES
            and (
                plan_control.get("forceFillerInsert")
                or (
                    any(prev.get("type") == "activity" for prev in timeline[:index])
                    and ("晚餐前" in str(step.get("title") or "") or "等位" in str(step.get("title") or ""))
                )
                or (
                    any(prev.get("type") == "activity" for prev in timeline[:index])
                    and
                    index + 1 < len(timeline)
                    and timeline[index + 1].get("type") == "restaurant"
                )
            )
        ),
        None,
    )
    if buffer_index is None:
        return plan, None

    before = timeline[buffer_index - 1] if buffer_index > 0 else {}
    after = timeline[buffer_index + 1] if buffer_index + 1 < len(timeline) else {}
    preferred_area = before.get("areaId") or after.get("areaId")
    if not preferred_area:
        for item in plan.get("selectedItems", []):
            if item.get("kind") in {"activity", "restaurant"}:
                preferred_area = next(
                    (
                        step.get("areaId")
                        for step in timeline
                        if step.get("poiId") == item.get("poiId") and step.get("areaId")
                    ),
                    None,
                )
                if preferred_area:
                    break
    if not preferred_area:
        preferred_area = after.get("areaId") or before.get("areaId")

    budget = plan.get("budgetEstimate") or {}
    budget_limit = _budget_limit(demand)
    current_total = float(budget.get("totalCost") or 0)
    strict = _budget_is_strict(demand)
    same_area_fillers = [item for item in fillers if item.get("areaId") == preferred_area]
    if not same_area_fillers:
        return plan, None
    affordable = [
        item
        for item in same_area_fillers
        if not strict or budget_limit is None or current_total + float(item.get("estimatedCost") or 0) <= budget_limit
    ]
    if not affordable:
        return plan, None

    filler = sorted(
        affordable,
        key=lambda item: (
            0 if any(word in str(item.get("name") or "") for word in ("茶", "奶茶", "小吃", "回民街")) else 1,
            float(item.get("estimatedCost") or 0),
            -float(item.get("score") or 0),
            item.get("name", ""),
        ),
    )[0]
    start = _parse_minutes(timeline[buffer_index].get("start"))
    end = _parse_minutes(timeline[buffer_index].get("end"))
    if start is None or end is None or end <= start:
        return plan, None
    gap_minutes = end - start
    if gap_minutes >= 90:
        filler_minutes = min(90, max(75, gap_minutes - 15))
    else:
        filler_minutes = min(FILLER_TARGET_MINUTES, max(30, gap_minutes))
    filler_cost = float(filler.get("estimatedCost") or 0)
    filler_step = {
        "start": _format_minutes(start, "待定"),
        "end": _format_minutes(start + filler_minutes, "待定"),
        "type": "filler",
        "title": f"建议在 {filler.get('name', '已选具体店铺')} 等位休息",
        "description": "实体补位节点：可坐下喝杯咖啡/茶饮、商场休息或附近轻逛，避免活动后过早吃晚饭。",
        "poiId": filler.get("poiId"),
        "routeRef": None,
        "estimatedCost": filler_cost,
        "areaId": filler.get("areaId"),
    }
    remaining_minutes = end - (start + filler_minutes)
    replacement = [filler_step]
    if remaining_minutes >= 10:
        replacement.append(
            {
                **timeline[buffer_index],
                "start": _format_minutes(start + filler_minutes, "待定"),
                "end": _format_minutes(end, "待定"),
                "description": "预留从该具体 POI 到晚餐地点的等位和转场余量。",
            }
        )
    plan["timeline"] = [*timeline[:buffer_index], *replacement, *timeline[buffer_index + 1 :]]
    budget["activityCost"] = round(float(budget.get("activityCost") or 0) + filler_cost, 2)
    budget["totalCost"] = round(current_total + filler_cost, 2)
    budget["perPersonCost"] = round(budget["totalCost"] / _people_total(demand), 2)
    plan["budgetEstimate"] = budget
    plan.setdefault("selectedItems", []).append(
        {
            "kind": "filler",
            "poiId": filler.get("poiId"),
            "name": filler.get("name"),
            "reason": "晚餐锚点前的同商圈实体休息/等位 filler，不参与主组合穷举。",
        }
    )
    plan.setdefault("recommendationReasons", []).append(
        f"晚餐不早于 17:30，空档用 {filler.get('name')} 作为同商圈具体 POI 补位。"
    )
    return plan, filler


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
    per_person = demand.get("budget", {}).get("perPerson")
    if (
        isinstance(per_person, (int, float))
        and 120 <= per_person <= 150
        and demand.get("budget", {}).get("flexibility") == "strict"
        and any(
            item.get("key") in {"conversationFriendly", "privacy", "formality"}
            for item in demand.get("demandProfile", {}).get("dimensions", [])
        )
    ):
        notes.append("当前预算卡在轻约会/慢聊氛围餐厅的临界位；如人均上浮 30-50 元，可优先升级到更稳妥的日料、Bistro 或慢聊餐厅。")
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
    nearby_areas = _nearby_target_area_ids(demand)
    selected_areas = {item.get("areaId") for item in (activity, restaurant) if item}
    if target_areas:
        if selected_areas and selected_areas.issubset(target_areas):
            score += 30
        elif selected_areas and selected_areas.issubset(target_areas | nearby_areas):
            score += 10
        else:
            score -= 25
    if _meal_first(demand) and restaurant:
        score += 12
    if _wants_after_meal_walk(demand) and activity and activity.get("areaId") == (restaurant or {}).get("areaId"):
        score += 8
    if first_route:
        if first_route.get("type") == "multi_origin_fairness":
            score += 18
            score -= float(first_route.get("maxMinutes") or 0) / 6
            score -= float(first_route.get("variance") or 0) / 20
        else:
            score -= float(first_route.get("minutes", 0)) / 8
    if transfer_route:
        score -= float(transfer_route.get("minutes", 0)) / 5
    budget_limit = _budget_limit(demand)
    if budget_limit is not None:
        utilization = total_cost / max(budget_limit, 1)
        score += max(0.0, 1.0 - abs(0.92 - utilization)) * 8.0
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

    plan_control = structured_demand.get("planControl", {}) if isinstance(structured_demand.get("planControl"), dict) else {}
    locks = plan_control.get("locks", {}) if isinstance(plan_control.get("locks"), dict) else {}
    excluded_poi_ids = {
        str(item)
        for item in plan_control.get("excludedPoiIds", [])
        if item
    } if isinstance(plan_control.get("excludedPoiIds"), list) else set()
    all_activities = mock_supply.get("activityCandidates", [])
    fillers = [item for item in all_activities if item.get("isFiller")]
    activities = [
        item for item in all_activities if not item.get("isFiller") and item.get("poiId") not in excluded_poi_ids
    ][:top_k]
    restaurants = [
        item for item in mock_supply.get("restaurantCandidates", []) if item.get("poiId") not in excluded_poi_ids
    ][:top_k]
    if locks.get("activityPoiId"):
        locked = [item for item in all_activities if item.get("poiId") == locks.get("activityPoiId") and not item.get("isFiller")]
        if locked:
            activities = locked
    if locks.get("restaurantPoiId"):
        locked = [item for item in mock_supply.get("restaurantCandidates", []) if item.get("poiId") == locks.get("restaurantPoiId")]
        if locked:
            restaurants = locked
    require_activity = _requires_activity(structured_demand)
    meal_hard = _has_meal_constraint(structured_demand)
    requested_components = set(
        structured_demand.get("demandProfile", {}).get("requestedComponents", [])
    )
    activity_options: list[dict[str, Any] | None] = activities if activities else [None]
    restaurant_options: list[dict[str, Any] | None] = restaurants if meal_hard else [None, *restaurants]
    if not restaurant_options:
        restaurant_options = [None]
    if (meal_hard or _wants_loose_mall_stroll(structured_demand)) and not require_activity:
        activity_options = [None, *activities]
    if requested_components == {"restaurant"}:
        activity_options = [None]
    if requested_components == {"activity"}:
        restaurant_options = [None]
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

    best_experience_score = max(float(item[2].get("experienceScore") or item[0]) for item in feasible)
    quality_threshold = max(
        best_experience_score - 8.0,
        min(75.0, best_experience_score),
    )
    quality_qualified = [
        item
        for item in feasible
        if float(item[2].get("experienceScore") or item[0]) >= quality_threshold
    ]
    quality_qualified.sort(
        key=lambda item: (
            float(item[2].get("businessScore") or 0),
            float(item[2].get("experienceScore") or item[0]),
        ),
        reverse=True,
    )
    best_score, best_plan, best_meta = quality_qualified[0]
    best_plan, filler = _insert_filler_if_needed(structured_demand, best_plan, fillers)
    if filler:
        best_meta["filler"] = filler.get("name")
    return {
        "status": "ok",
        "timelinePlan": best_plan,
        "rejectedCombinations": rejected[:20],
        "selectedCombination": {**best_meta, "score": round(best_score, 3)},
        "strategy": "top_k_constraint_search_with_greedy_filler",
        "evaluatedCombinationCount": len(feasible) + len(rejected),
        "feasibleCombinationCount": len(feasible),
        "qualityQualifiedCombinationCount": len(quality_qualified),
        "qualityThreshold": round(quality_threshold, 3),
        "fillerInsertion": {"inserted": bool(filler), "poiId": filler.get("poiId") if filler else None},
        "locks": locks,
    }


def _failure_suggestions(rejected: list[dict[str, Any]] | None) -> list[str]:
    reasons = " ".join(str(item.get("reason") or "") for item in (rejected or []))
    suggestions: list[str] = []
    if "超过时间窗口" in reasons or "营业时间" in reasons or "预约时段" in reasons:
        suggestions.append("这个时间窗口比较紧，建议二选一：把吃饭改成更早可约的简餐/茶点，或把结束时间放宽 30-60 分钟。")
    if "超过严格预算" in reasons:
        suggestions.append("预算卡得比较死，可以优先保留免费/低价活动，把餐饮换成同商圈低客单，或把人均预算上浮一点点。")
    if "路线" in reasons:
        suggestions.append("路线约束比较硬，建议优先锁定同商圈活动和餐饮，减少跨商圈转场。")
    if not suggestions:
        suggestions.append("我建议先放宽一个条件：时间、预算、商圈或是否必须同时安排活动和吃饭。")
    return suggestions[:3]


def _failed_plan(mock_supply: dict[str, Any], rejected: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    reasons = mock_supply.get("supplyStatus", {}).get("reasons") or []
    if rejected:
        reasons.append(f"已尝试 {len(rejected)} 个候选组合，但都不满足时间/预算/路线约束")
    return {
        "status": "failed",
        "summary": "这版条件有点打架，我先不硬凑一个不靠谱的方案。",
        "timeline": [],
        "selectedItems": [],
        "reasonBadges": [],
        "budgetEstimate": {
            "activityCost": 0,
            "restaurantCost": 0,
            "routeCost": 0,
            "totalCost": 0,
            "perPersonCost": 0,
            "currency": "CNY",
            "notes": ["未形成可执行方案。"],
        },
        "recommendationReasons": _failure_suggestions(rejected),
        "riskTips": reasons or ["需要放宽预算、时间、商圈或活动要求。"],
        "tradeoffs": _failure_suggestions(rejected),
        "rawPlannerNotes": "Constraint scheduler found no feasible combination.",
    }
