"""Planning policy normalization shared by extraction and scheduling.

This module does not interpret user wording with keyword rules. The LLM or
extractor owns semantic understanding and writes `planningPolicy`; this module
only normalizes that object and fills conservative defaults from structured
fields when legacy data is missing the field.
"""

from __future__ import annotations

from typing import Any, TypedDict


POLICY_ENUMS = {
    "timeScope": {"onsite_after_meetup", "door_to_door", "unknown"},
    "startAnchorType": {"explicit_meetup", "origin_departure", "home_departure", "already_in_area", "unknown"},
    "endAnchorType": {"leave_last_poi", "return_to_origin", "unknown"},
}

SCHEMA_POLICY_KEYS = {
    "timeScope",
    "startAnchorType",
    "endAnchorType",
    "includeOutboundRoute",
    "includeReturnRoute",
    "targetExperienceBlocks",
    "maxIdleMinutes",
    "allowCrossAreaTransfer",
    "maxTransferMinutes",
    "evidence",
}

DEFAULT_MAX_IDLE_MINUTES = 45
DEFAULT_TARGET_BLOCKS_SHORT = 1
DEFAULT_TARGET_BLOCKS_LOCAL = 2
DEFAULT_TARGET_BLOCKS_LONG = 3
LOCAL_TRIP_MINUTES = 240
LONG_TRIP_MINUTES = 330
SOFT_TRIP_MAX_MINUTES = 360
DINNER_WINDOW_START_MINUTES = 17 * 60 + 30
DINNER_WINDOW_END_MINUTES = 19 * 60 + 30
DEFAULT_MEAL_MINUTES = 60
DEFAULT_MAX_TRANSFER_MINUTES = 30
CROSS_CITY_MAX_TRANSFER_MINUTES = 35
TARGET_BUDGET_UTILIZATION = 0.92


class PlanningPolicy(TypedDict):
    timeScope: str
    startAnchorType: str
    endAnchorType: str
    includeOutboundRoute: bool
    includeReturnRoute: bool
    targetExperienceBlocks: int
    maxIdleMinutes: int
    allowCrossAreaTransfer: bool
    maxTransferMinutes: int
    evidence: list[str]
    forbidLongBuffer: bool
    mustImprovePreviousIdle: bool


def parse_minutes(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _list_values(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def demand_text(demand: dict[str, Any], raw_input: str | None = None) -> str:
    """Return already-structured text fields for legacy scheduler helpers."""
    preferences = demand.get("preferences", {}) if isinstance(demand.get("preferences"), dict) else {}
    constraints = demand.get("constraints", {}) if isinstance(demand.get("constraints"), dict) else {}
    location = demand.get("location", {}) if isinstance(demand.get("location"), dict) else {}
    return " ".join(
        [
            str(raw_input if raw_input is not None else demand.get("rawInput") or ""),
            str(location.get("startPoint") or ""),
            str(location.get("preferredArea") or ""),
            " ".join(str(item) for item in _list_values(preferences.get("activityTypes"))),
            " ".join(str(item) for item in _list_values(preferences.get("foodTags"))),
            " ".join(str(item) for item in _list_values(preferences.get("experienceTags"))),
            " ".join(str(item) for item in _list_values(preferences.get("avoidTags"))),
            " ".join(str(item) for item in _list_values(constraints.get("hard"))),
            " ".join(str(item) for item in _list_values(constraints.get("soft"))),
        ]
    )


def default_start_minutes(_: dict[str, Any]) -> int:
    return 14 * 60


def _requested_components(demand: dict[str, Any]) -> set[str]:
    if _explicit_no_meal(demand):
        profile = demand.get("demandProfile") if isinstance(demand.get("demandProfile"), dict) else {}
        components = profile.get("requestedComponents") if isinstance(profile.get("requestedComponents"), list) else []
        if not components and isinstance(demand.get("requestedComponents"), list):
            components = demand["requestedComponents"]
        return {str(item) for item in components if str(item) != "restaurant"} or {"activity"}
    profile = demand.get("demandProfile") if isinstance(demand.get("demandProfile"), dict) else {}
    components = profile.get("requestedComponents") if isinstance(profile.get("requestedComponents"), list) else []
    if not components and isinstance(demand.get("requestedComponents"), list):
        components = demand["requestedComponents"]
    if components:
        return {str(item) for item in components}
    must_include = demand.get("expectedOutput", {}).get("mustInclude", [])
    must_text = " ".join(str(item) for item in _list_values(must_include))
    inferred: set[str] = set()
    if "活动" in must_text:
        inferred.add("activity")
    if "餐饮" in must_text or "吃饭" in must_text:
        inferred.add("restaurant")
    return inferred


def _explicit_no_meal(demand: dict[str, Any]) -> bool:
    text = demand_text(demand)
    return any(
        keyword in text
        for keyword in (
            "不吃饭",
            "不用吃饭",
            "不用吃",
            "不要吃饭",
            "不安排吃",
            "不安排吃饭",
            "不安排正餐",
            "不吃正餐",
            "不安排餐厅",
            "只玩不吃",
            "只逛不吃",
        )
    )


def _has_meal_component(demand: dict[str, Any]) -> bool:
    return "restaurant" in _requested_components(demand)


def _target_duration_range(time_window: dict[str, Any]) -> tuple[int, int]:
    raw = time_window.get("targetDurationRangeMinutes")
    if (
        isinstance(raw, list)
        and len(raw) >= 2
        and isinstance(raw[0], (int, float))
        and isinstance(raw[1], (int, float))
        and raw[0] > 0
        and raw[1] >= raw[0]
    ):
        return int(raw[0]), int(raw[1])
    return LOCAL_TRIP_MINUTES, SOFT_TRIP_MAX_MINUTES


def _is_lunch_or_tea_intent(demand: dict[str, Any]) -> bool:
    text = demand_text(demand)
    return any(keyword in text for keyword in ("午饭", "午餐", "中午吃", "先吃午饭", "下午茶", "奶茶", "咖啡"))


def _soft_window_end(demand: dict[str, Any], start: int) -> int:
    time_window = demand.get("timeWindow", {}) if isinstance(demand.get("timeWindow"), dict) else {}
    min_duration, max_duration = _target_duration_range(time_window)
    end = start + min_duration
    crosses_dinner = start < DINNER_WINDOW_START_MINUTES and end >= 18 * 60
    should_keep_dinner_room = (
        not _explicit_no_meal(demand)
        and not _is_lunch_or_tea_intent(demand)
        and (_has_meal_component(demand) or crosses_dinner)
    )
    if should_keep_dinner_room:
        if start < DINNER_WINDOW_START_MINUTES:
            end = max(end, DINNER_WINDOW_START_MINUTES + DEFAULT_MEAL_MINUTES)
        elif start <= DINNER_WINDOW_END_MINUTES:
            end = max(end, start + min_duration)
        else:
            end = max(end, start + min_duration)
    return min(end, start + max_duration)


def time_window_bounds(demand: dict[str, Any]) -> tuple[int, int]:
    time_window = demand.get("timeWindow", {}) if isinstance(demand.get("timeWindow"), dict) else {}
    start = parse_minutes(time_window.get("startTime"))
    if start is None:
        start = default_start_minutes(demand)
    end = parse_minutes(time_window.get("endTime"))
    if time_window.get("hasExplicitEnd") is False:
        end = None
    if end is not None and end <= start:
        end = None
    if end is None:
        duration_hours = time_window.get("durationHours")
        if isinstance(duration_hours, (int, float)) and duration_hours:
            end = start + int(duration_hours * 60)
        else:
            end = _soft_window_end(demand, start)
    return start, end


def time_window_duration_minutes(demand: dict[str, Any]) -> int:
    start, end = time_window_bounds(demand)
    return max(0, end - start)


def has_low_cost_intent(demand: dict[str, Any]) -> bool:
    profile = demand.get("demandProfile") if isinstance(demand.get("demandProfile"), dict) else {}
    for item in _list_values(profile.get("dimensions")):
        if not isinstance(item, dict) or item.get("key") != "pricePreference":
            continue
        if item.get("source") == "explicit" and isinstance(item.get("target"), (int, float)) and float(item["target"]) <= 0.35:
            return True
    budget = demand.get("budget", {}) if isinstance(demand.get("budget"), dict) else {}
    return budget.get("flexibility") == "low_cost"


def is_simple_trip_intent(demand: dict[str, Any]) -> bool:
    raw_policy = demand.get("planningPolicy") if isinstance(demand.get("planningPolicy"), dict) else {}
    plan_control = demand.get("planControl", {}) if isinstance(demand.get("planControl"), dict) else {}
    patch = plan_control.get("constraintsPatch", {}) if isinstance(plan_control.get("constraintsPatch"), dict) else {}
    if isinstance(patch.get("targetExperienceBlocks"), int) and patch["targetExperienceBlocks"] <= 1:
        return True
    if isinstance(raw_policy.get("targetExperienceBlocks"), int) and raw_policy["targetExperienceBlocks"] <= 1:
        return True
    raw_text = demand_text(demand)
    return any(
        keyword in raw_text
        for keyword in (
            "简单",
            "随便",
            "不折腾",
            "别折腾",
            "不想太累",
            "走不了太多路",
            "轻松一点",
            "少安排",
            "别安排太满",
        )
    )


def _has_explicit_multi_stop_intent(demand: dict[str, Any]) -> bool:
    """Lightweight trips can still need multiple small stops."""
    text = demand_text(demand)
    requested = _requested_components(demand)
    if any(keyword in text for keyword in ("citywalk", "逛吃", "小吃", "坐下来聊", "边逛边吃", "走走吃吃")):
        return True
    preferences = demand.get("preferences", {}) if isinstance(demand.get("preferences"), dict) else {}
    activity_types = [str(item) for item in _list_values(preferences.get("activityTypes")) if item]
    food_tags = [str(item) for item in _list_values(preferences.get("foodTags")) if item]
    return bool(activity_types) and bool(food_tags) and any(
        keyword in text for keyword in ("聊", "坐", "歇", "小吃", "逛")
    )


def allow_single_node_itinerary(demand: dict[str, Any]) -> bool:
    if has_low_cost_intent(demand):
        return True
    requested = _requested_components(demand)
    if {"activity", "restaurant"}.issubset(requested) and time_window_duration_minutes(demand) >= 180:
        return is_simple_trip_intent(demand)
    if requested == {"restaurant"}:
        raw_policy = demand.get("planningPolicy") if isinstance(demand.get("planningPolicy"), dict) else {}
        raw_blocks = raw_policy.get("targetExperienceBlocks")
        return not (isinstance(raw_blocks, int) and raw_blocks >= DEFAULT_TARGET_BLOCKS_LOCAL)
    if time_window_duration_minutes(demand) < LOCAL_TRIP_MINUTES:
        return True
    return is_simple_trip_intent(demand)


def _enum_value(raw_policy: dict[str, Any], key: str, default: str) -> str:
    value = raw_policy.get(key)
    return value if isinstance(value, str) and value in POLICY_ENUMS[key] else default


def _has_target_area(demand: dict[str, Any]) -> bool:
    profile = demand.get("demandProfile") if isinstance(demand.get("demandProfile"), dict) else {}
    anchors = [
        item for item in _list_values(profile.get("destinationAnchors"))
        if isinstance(item, dict) and item.get("resolvedAreaId") and item.get("commitment") in {"required", "preferred"}
    ]
    return bool(anchors)


def _has_explicit_meetup_context(demand: dict[str, Any], raw_text: str) -> bool:
    if not any(term in raw_text for term in ("集合", "见面", "碰头", "约在", "会合")):
        return False
    location = demand.get("location", {}) if isinstance(demand.get("location"), dict) else {}
    profile = demand.get("demandProfile") if isinstance(demand.get("demandProfile"), dict) else {}
    anchor_text = " ".join(
        str(item.get("evidence") or item.get("name") or "")
        for item in _list_values(profile.get("destinationAnchors"))
        if isinstance(item, dict)
    )
    return bool(location.get("startPoint") or location.get("preferredArea") or anchor_text)


def _has_explicit_meetup_start(raw_text: str, demand: dict[str, Any]) -> bool:
    if not any(term in raw_text for term in ("集合", "见面", "碰头", "会合", "约在")):
        return False
    time_window = demand.get("timeWindow", {}) if isinstance(demand.get("timeWindow"), dict) else {}
    return bool(time_window.get("startTime")) or any(
        marker in raw_text for marker in ("点集合", "点见", "点碰头", "点会合", "点约")
    )


def resolve_planning_policy(demand: dict[str, Any], raw_input: str | None = None) -> PlanningPolicy:
    raw_text = str(raw_input if raw_input is not None else demand.get("rawInput") or "")
    raw_policy = demand.get("planningPolicy") if isinstance(demand.get("planningPolicy"), dict) else {}
    plan_control = demand.get("planControl", {}) if isinstance(demand.get("planControl"), dict) else {}
    patch = plan_control.get("constraintsPatch", {}) if isinstance(plan_control.get("constraintsPatch"), dict) else {}
    location = demand.get("location", {}) if isinstance(demand.get("location"), dict) else {}
    time_window = demand.get("timeWindow", {}) if isinstance(demand.get("timeWindow"), dict) else {}
    cross_city = location.get("crossCityIntent") if isinstance(location.get("crossCityIntent"), dict) else {}
    origin_points = location.get("originPoints") if isinstance(location.get("originPoints"), list) else []
    has_origin = bool(location.get("startPoint")) or bool(origin_points) or bool(cross_city.get("enabled"))
    duration = time_window_duration_minutes(demand)
    requested = _requested_components(demand)
    simple_trip = is_simple_trip_intent(demand)
    explicit_meetup_context = _has_explicit_meetup_context(demand, raw_text)
    explicit_meetup_start = _has_explicit_meetup_start(raw_text, demand)

    default_time_scope = "door_to_door" if has_origin else "unknown"
    default_start_anchor = "origin_departure" if has_origin else "unknown"
    time_scope = _enum_value(raw_policy, "timeScope", default_time_scope)
    start_anchor_type = _enum_value(raw_policy, "startAnchorType", default_start_anchor)
    end_anchor_type = _enum_value(raw_policy, "endAnchorType", "leave_last_poi")
    if (explicit_meetup_context or explicit_meetup_start) and not cross_city.get("enabled"):
        time_scope = "onsite_after_meetup"
        start_anchor_type = "explicit_meetup"
    if origin_points and (not explicit_meetup_start or (not _has_target_area(demand) and not location.get("startPoint"))):
        time_scope = "door_to_door"
        start_anchor_type = "origin_departure"
    if any(keyword in raw_text for keyword in ("回家", "回去", "到家", "回学校", "回校")):
        end_anchor_type = "return_to_origin"

    include_outbound = raw_policy.get("includeOutboundRoute")
    if not isinstance(include_outbound, bool):
        include_outbound = time_scope == "door_to_door" or bool(cross_city.get("enabled")) or bool(origin_points)
    if origin_points and (not explicit_meetup_start or (not _has_target_area(demand) and not location.get("startPoint"))):
        include_outbound = True
    if start_anchor_type in {"explicit_meetup", "already_in_area"} and not cross_city.get("enabled"):
        include_outbound = False

    include_return = raw_policy.get("includeReturnRoute")
    if not isinstance(include_return, bool):
        include_return = end_anchor_type == "return_to_origin"
    if end_anchor_type == "return_to_origin":
        include_return = True
    if include_return and not has_origin:
        include_return = False
        if end_anchor_type == "return_to_origin":
            end_anchor_type = "leave_last_poi"

    raw_blocks = raw_policy.get("targetExperienceBlocks")
    patch_blocks = patch.get("targetExperienceBlocks")
    if isinstance(patch_blocks, int) and patch_blocks >= 0:
        target_blocks = patch_blocks
    elif isinstance(raw_blocks, int) and raw_blocks >= 0:
        target_blocks = raw_blocks
    elif requested == {"restaurant"}:
        target_blocks = 0
    elif duration >= LONG_TRIP_MINUTES and not simple_trip:
        target_blocks = DEFAULT_TARGET_BLOCKS_LONG
    elif duration >= LOCAL_TRIP_MINUTES:
        target_blocks = DEFAULT_TARGET_BLOCKS_LOCAL
    else:
        target_blocks = DEFAULT_TARGET_BLOCKS_SHORT
    if "activity" in requested and target_blocks < 1:
        target_blocks = 1
    if has_low_cost_intent(demand) and simple_trip and not isinstance(patch_blocks, int):
        target_blocks = min(target_blocks, 1)
    if {"activity", "restaurant"}.issubset(requested) and duration >= 180 and (not simple_trip or _has_explicit_multi_stop_intent(demand)):
        target_blocks = max(DEFAULT_TARGET_BLOCKS_LOCAL, target_blocks)
    if duration >= LONG_TRIP_MINUTES and not simple_trip and target_blocks >= DEFAULT_TARGET_BLOCKS_LOCAL:
        target_blocks = max(DEFAULT_TARGET_BLOCKS_LONG, target_blocks)
    if _has_meal_component(demand) and duration >= LOCAL_TRIP_MINUTES and requested != {"restaurant"} and not simple_trip:
        target_blocks = max(DEFAULT_TARGET_BLOCKS_LOCAL, target_blocks)
    start_minutes = parse_minutes(time_window.get("startTime"))
    if (
        _has_meal_component(demand)
        and start_minutes is not None
        and DINNER_WINDOW_START_MINUTES <= start_minutes <= DINNER_WINDOW_END_MINUTES
        and not _is_lunch_or_tea_intent(demand)
        and not _has_explicit_multi_stop_intent(demand)
    ):
        target_blocks = min(target_blocks, DEFAULT_TARGET_BLOCKS_SHORT)

    raw_max_idle = raw_policy.get("maxIdleMinutes")
    max_idle = int(raw_max_idle) if isinstance(raw_max_idle, (int, float)) and raw_max_idle > 0 else DEFAULT_MAX_IDLE_MINUTES
    if isinstance(patch.get("maxIdleMinutes"), (int, float)):
        max_idle = min(max_idle, int(patch["maxIdleMinutes"]))
    if plan_control.get("forbidLongBuffer") or patch.get("forbidLongBuffer"):
        max_idle = min(max_idle, DEFAULT_MAX_IDLE_MINUTES)

    raw_allow_cross = raw_policy.get("allowCrossAreaTransfer")
    if isinstance(raw_allow_cross, bool):
        allow_cross_area = raw_allow_cross
    else:
        allow_cross_area = duration >= LOCAL_TRIP_MINUTES and not simple_trip
    if _has_target_area(demand) and raw_allow_cross is not True:
        allow_cross_area = False
    if patch.get("allowCrossAreaTransfer") is True:
        allow_cross_area = True

    raw_max_transfer = raw_policy.get("maxTransferMinutes")
    if isinstance(raw_max_transfer, (int, float)) and raw_max_transfer > 0:
        max_transfer = int(raw_max_transfer)
    elif cross_city.get("enabled"):
        max_transfer = CROSS_CITY_MAX_TRANSFER_MINUTES
    else:
        max_transfer = DEFAULT_MAX_TRANSFER_MINUTES

    evidence = raw_policy.get("evidence") if isinstance(raw_policy.get("evidence"), list) else []
    forbid_long_buffer = bool(plan_control.get("forbidLongBuffer") or patch.get("forbidLongBuffer"))
    return {
        "timeScope": time_scope,
        "startAnchorType": start_anchor_type,
        "endAnchorType": end_anchor_type,
        "includeOutboundRoute": bool(include_outbound),
        "includeReturnRoute": bool(include_return),
        "targetExperienceBlocks": max(0, min(4, int(target_blocks))),
        "maxIdleMinutes": max(10, min(90, int(max_idle))),
        "allowCrossAreaTransfer": bool(allow_cross_area),
        "maxTransferMinutes": max(0, min(90, int(max_transfer))),
        "evidence": [str(item) for item in evidence[:6] if item],
        "forbidLongBuffer": forbid_long_buffer,
        "mustImprovePreviousIdle": bool(plan_control.get("mustImprovePreviousIdle") or patch.get("mustImprovePreviousIdle")),
    }


def schema_planning_policy(demand: dict[str, Any], raw_input: str | None = None) -> dict[str, Any]:
    policy = resolve_planning_policy(demand, raw_input)
    return {key: policy[key] for key in SCHEMA_POLICY_KEYS}
