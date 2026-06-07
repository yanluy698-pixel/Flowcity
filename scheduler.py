"""
FlowCity constraint scheduler.

LLM may understand intent, but this module owns executable timelines. It uses
Top-K candidates, route matrix entries, budget/time constraints, and small
combination search so the first plan is already feasible before Validator runs.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

import demand_profile
import poi_identity
import planning_policy
import route_identity
import temporal_utils
import timeline_quality


AREA_LABELS = {
    "area_xa_xiaozhai": "小寨商圈",
    "area_xa_qujiang": "曲江商圈",
    "area_xa_zhonglou": "钟楼商圈",
    "area_xa_gaoxin": "高新商圈",
    "area_xa_daminggong": "大明宫-龙首原商圈",
    "area_xa_xingzheng": "行政中心商圈",
    "origin_xa_qujiangchi": "曲江池附近",
    "origin_xa_zhonglou_metro": "钟楼地铁站",
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
MIN_ACTIVITY_MINUTES = 30
DEFAULT_RESTAURANT_MINUTES = 75
MIN_RESTAURANT_MINUTES = 30
RESTAURANT_DURATION_STEP_MINUTES = 15
AFTER_MEAL_WALK_MINUTES = 35
BUFFER_MINUTES = 15
DINNER_EARLIEST_MINUTES = 17 * 60 + 30
DINNER_LATEST_MINUTES = 19 * 60 + 30
EARLY_DINNER_EARLIEST_MINUTES = 16 * 60 + 30
FILLER_MIN_GAP_MINUTES = 35
FILLER_TARGET_MINUTES = 45
DEFAULT_MAX_IDLE_MINUTES = planning_policy.DEFAULT_MAX_IDLE_MINUTES
LONG_IDLE_MINUTES = 90
TARGET_BUDGET_UTILIZATION = planning_policy.TARGET_BUDGET_UTILIZATION
SCHEDULER_MIN_TOP_K = 10
SCHEDULER_PRIMARY_BEAM = 10
SCHEDULER_SUPPLEMENTAL_BEAM = 6
SCHEDULER_SECONDARY_BEAM = 2
SCHEDULER_RESTAURANT_BEAM = 10
SCHEDULER_MAX_MULTI_ATTEMPTS = 360

MEAL_REQUEST_TERMS = (
    "吃饭",
    "吃个饭",
    "吃一顿",
    "再吃",
    "先玩再吃",
    "玩完再吃",
    "吃的",
    "好吃",
    "吃点",
    "吃和玩",
    "安排吃",
    "餐厅",
    "餐饮",
    "聚餐",
    "晚饭",
    "晚餐",
    "正餐",
    "午饭",
    "午餐",
    "火锅",
    "烤肉",
    "小吃",
    "简餐",
    "下午茶",
    "奶茶",
    "咖啡",
    "坐下来聊",
    "坐下聊天",
    "找个地方坐",
)

NO_MEAL_TERMS = (
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


def _low_cost_ceiling(demand: dict[str, Any]) -> float | None:
    plan_control = demand.get("planControl", {})
    if not isinstance(plan_control, dict):
        return None
    value = plan_control.get("lowCostCeilingTotal")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _violates_low_cost_ceiling(demand: dict[str, Any], total_cost: float) -> dict[str, Any] | None:
    ceiling = _low_cost_ceiling(demand)
    if ceiling is None or not _is_low_cost_intent(demand):
        return None
    if total_cost >= ceiling:
        return {
            "reason": "低价修改不能比上一版更贵",
            "totalCost": round(total_cost, 2),
            "lowCostCeilingTotal": round(ceiling, 2),
        }
    return None


def _budget_fit_score(demand: dict[str, Any], total_cost: float, *, weight: float = 20.0) -> float:
    budget_limit = _budget_limit(demand)
    if budget_limit is None or budget_limit <= 0 or _is_low_cost_intent(demand):
        return 0.0
    utilization = total_cost / max(budget_limit, 1)
    fit = max(0.0, 1.0 - abs(TARGET_BUDGET_UTILIZATION - min(utilization, 1.2))) * weight
    if budget_limit >= 180 and utilization < 0.6:
        fit -= (0.6 - utilization) * weight * 2.4
    if budget_limit >= 180 and utilization < 0.35:
        fit -= (0.35 - utilization) * weight * 3.6
    return fit


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
    return temporal_utils.is_weekend_text(date_text)


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
    return planning_policy.default_start_minutes(demand)


def _format_minutes(value: int | None, fallback: str) -> str:
    if value is None:
        return fallback
    value %= 24 * 60
    return f"{value // 60:02d}:{value % 60:02d}"


def _demand_text(demand: dict[str, Any]) -> str:
    return planning_policy.demand_text(demand)


def _raw_user_text(demand: dict[str, Any]) -> str:
    return str(demand.get("rawInput") or "")


def _raw_has_meal_request(demand: dict[str, Any]) -> bool:
    if _explicit_no_meal(demand):
        return False
    raw = _raw_user_text(demand)
    plan_control = demand.get("planControl", {}) if isinstance(demand.get("planControl"), dict) else {}
    if isinstance(plan_control.get("mealTiming"), str):
        return True
    patch = plan_control.get("constraintsPatch", {}) if isinstance(plan_control.get("constraintsPatch"), dict) else {}
    if isinstance(patch.get("mealTiming"), str):
        return True
    return any(keyword in raw for keyword in MEAL_REQUEST_TERMS)


def _activity_only_component_intent(demand: dict[str, Any]) -> bool:
    text = _positive_activity_text(demand)
    walk_only_terms = (
        "只想走走",
        "随便走走",
        "简单走走",
        "就走走",
        "只逛逛",
        "简单逛逛",
        "就逛逛",
        "散散步",
        "散步",
    )
    if not any(term in text for term in walk_only_terms) or _raw_has_meal_request(demand):
        return False
    profile = demand.get("demandProfile") if isinstance(demand.get("demandProfile"), dict) else {}
    components = profile.get("requestedComponents")
    if isinstance(components, list) and components:
        normalized = {str(item) for item in components}
        return "restaurant" not in normalized
    return True


def _structured_has_meal_requirement(demand: dict[str, Any]) -> bool:
    if _explicit_no_meal(demand):
        return False
    text_parts: list[str] = []
    constraints = demand.get("constraints", {}) if isinstance(demand.get("constraints"), dict) else {}
    for key in ("hard", "soft"):
        values = constraints.get(key, [])
        if isinstance(values, list):
            text_parts.extend(str(item) for item in values)
    people = demand.get("people", {}) if isinstance(demand.get("people"), dict) else {}
    special_needs = people.get("specialNeeds", [])
    if isinstance(special_needs, list):
        text_parts.extend(str(item) for item in special_needs)
    text = " ".join(text_parts)
    action_terms = (
        "安排吃饭",
        "一起吃",
        "吃饭",
        "吃一顿",
        "吃个饭",
        "晚饭",
        "晚餐",
        "正餐",
        "聚餐",
        "小吃",
        "找个地方坐",
        "坐下来聊",
        "坐下聊天",
        "可订座",
        "支持订座",
    )
    return any(keyword in text for keyword in action_terms)


def _should_default_dinner_component(demand: dict[str, Any]) -> bool:
    if _explicit_no_meal(demand):
        return False
    raw_text = _raw_user_text(demand)
    if _activity_only_component_intent(demand):
        return False
    if any(keyword in raw_text for keyword in ("回家", "回到家", "到家", "回校", "回学校")):
        end = _parse_minutes(demand.get("timeWindow", {}).get("endTime"))
        if end is not None and end <= 18 * 60:
            return False
    if any(keyword in raw_text for keyword in ("午饭", "午餐", "中午吃", "下午茶", "奶茶", "咖啡")):
        return False
    start, end = _time_window_bounds(demand)
    return bool(
        start is not None
        and end is not None
        and start < DINNER_EARLIEST_MINUTES
        and end >= 18 * 60
        and _time_window_duration_minutes(demand) >= planning_policy.LOCAL_TRIP_MINUTES
    )


def _time_window_bounds(demand: dict[str, Any]) -> tuple[int, int]:
    return planning_policy.time_window_bounds(demand)


def _time_window_duration_minutes(demand: dict[str, Any]) -> int:
    return planning_policy.time_window_duration_minutes(demand)


def _is_low_cost_intent(demand: dict[str, Any]) -> bool:
    return planning_policy.has_low_cost_intent(demand)


def _is_simple_trip_intent(demand: dict[str, Any]) -> bool:
    return planning_policy.is_simple_trip_intent(demand)


def _allow_single_node_itinerary(demand: dict[str, Any]) -> bool:
    return planning_policy.allow_single_node_itinerary(demand)


def _planning_policy(demand: dict[str, Any]) -> dict[str, Any]:
    return planning_policy.resolve_planning_policy(demand)


def _include_outbound_route_in_timeline(demand: dict[str, Any]) -> bool:
    return bool(_planning_policy(demand).get("includeOutboundRoute"))


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
    if _explicit_no_meal(demand):
        return False
    return _raw_has_meal_request(demand) or _structured_has_meal_requirement(demand) or _should_default_dinner_component(demand)


def _explicit_no_meal(demand: dict[str, Any]) -> bool:
    text = _demand_text(demand)
    return any(keyword in text for keyword in NO_MEAL_TERMS)


def _needs_sit_down_component(demand: dict[str, Any]) -> bool:
    text = " ".join([_demand_text(demand), _positive_activity_text(demand)])
    if any(keyword in text for keyword in ("坐下来", "坐下", "坐着", "坐一会", "慢聊", "聊天", "茶", "咖啡", "奶茶", "休息", "歇")):
        return True
    dimensions = demand.get("demandProfile", {}).get("dimensions", {})
    if not isinstance(dimensions, dict):
        return False
    for key in ("conversationFriendly", "restAvailability"):
        value = dimensions.get(key)
        if isinstance(value, dict) and float(value.get("score") or 0) >= 0.72:
            return True
    return False


def _requires_dinner_anchor(demand: dict[str, Any]) -> bool:
    raw_text = _raw_user_text(demand)
    explicit_dinner = any(keyword in raw_text for keyword in ("晚饭", "晚餐", "傍晚吃", "夜间吃饭")) or (
        "晚上" in raw_text and any(keyword in raw_text for keyword in ("吃饭", "聚餐", "餐厅", "吃一顿"))
    )
    if explicit_dinner:
        return True
    if any(keyword in raw_text for keyword in ("早吃", "早点吃", "先吃", "午饭", "午餐", "中午吃", "下午茶", "奶茶", "茶歇")):
        return False
    start, end = _time_window_bounds(demand)
    has_meal = _has_meal_constraint(demand) or any(keyword in raw_text for keyword in ("聚餐", "吃点"))
    return bool(
        has_meal
        and (
            (start is not None and start >= 15 * 60)
            or (end is not None and end >= 17 * 60 + 30)
        )
    )


def _should_anchor_restaurant_as_dinner(demand: dict[str, Any], restaurant: dict[str, Any] | None) -> bool:
    if not restaurant or _meal_timing(demand) == "earlier":
        return False
    if _requires_dinner_anchor(demand):
        return True
    raw_text = _raw_user_text(demand)
    if (_is_low_cost_intent(demand) or _is_simple_trip_intent(demand)) and not any(
        keyword in raw_text for keyword in ("晚饭", "晚餐", "晚上吃", "正常饭点")
    ):
        return False
    if any(keyword in raw_text for keyword in ("早餐", "早饭", "午饭", "午餐", "中午吃", "下午茶", "奶茶", "咖啡")):
        return False
    start, end = _time_window_bounds(demand)
    return bool(
        end is not None
        and end >= DINNER_EARLIEST_MINUTES
        and (start is None or start < DINNER_EARLIEST_MINUTES)
        and _time_window_duration_minutes(demand) >= 240
    )


def _meal_timing(demand: dict[str, Any]) -> str | None:
    plan_control = demand.get("planControl", {}) if isinstance(demand.get("planControl"), dict) else {}
    timing = plan_control.get("mealTiming")
    if isinstance(timing, str):
        return timing
    patch = plan_control.get("constraintsPatch", {}) if isinstance(plan_control.get("constraintsPatch"), dict) else {}
    timing = patch.get("mealTiming")
    return timing if isinstance(timing, str) else None


def _dinner_earliest_minutes(demand: dict[str, Any]) -> int:
    if _meal_timing(demand) == "earlier":
        return EARLY_DINNER_EARLIEST_MINUTES
    return DINNER_EARLIEST_MINUTES


def _needs_meal_timing_choice(demand: dict[str, Any]) -> bool:
    end = _parse_minutes(demand.get("timeWindow", {}).get("endTime"))
    return bool(
        end is not None
        and end <= 18 * 60
        and _requires_dinner_anchor(demand)
        and _meal_timing(demand) is None
    )


def _with_meal_timing(demand: dict[str, Any], timing: str) -> dict[str, Any]:
    variant = deepcopy(demand)
    plan_control = variant.setdefault("planControl", {})
    if not isinstance(plan_control, dict):
        plan_control = {}
        variant["planControl"] = plan_control
    patch = plan_control.setdefault("constraintsPatch", {})
    if not isinstance(patch, dict):
        patch = {}
        plan_control["constraintsPatch"] = patch
    patch["mealTiming"] = timing
    if timing == "earlier":
        patch["targetExperienceBlocks"] = 1
        policy = variant.setdefault("planningPolicy", {})
        if not isinstance(policy, dict):
            policy = {}
            variant["planningPolicy"] = policy
        policy["targetExperienceBlocks"] = 1
    return variant


def _compact_decision_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(plan, dict) or plan.get("status") == "failed":
        return None
    return {
        "summary": plan.get("summary"),
        "budgetEstimate": plan.get("budgetEstimate", {}),
        "timeline": plan.get("timeline", []),
        "riskTips": plan.get("riskTips", []),
        "tradeoffs": plan.get("tradeoffs", []),
    }


def _result_has_preview_plan(result: dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    plan = result.get("timelinePlan", {})
    return bool(_compact_decision_plan(plan if isinstance(plan, dict) else None))


def _meal_timing_option(
    *,
    option_id: str,
    label: str,
    result: dict[str, Any],
    tradeoff: str,
    user_prompt: str,
) -> dict[str, Any]:
    plan = result.get("timelinePlan", {}) if isinstance(result, dict) else {}
    ok = result.get("status") == "ok" and isinstance(plan, dict) and plan.get("status") != "failed"
    constraints_patch = {
        "mealTiming": "earlier" if option_id == "early_simple_meal" else "normal",
        "targetExperienceBlocksMin": 1 if option_id == "early_simple_meal" else 2,
    }
    return {
        "id": option_id,
        "label": label,
        "status": "ok" if ok else "tight",
        "summary": plan.get("summary") if ok else "这个选项在当前结束时间里放不太下，可能需要少去一个点，或把结束时间往后放一点。",
        "tradeoff": tradeoff,
        "previewPlan": _compact_decision_plan(plan),
        "userPrompt": user_prompt,
        "constraintsPatch": constraints_patch,
    }


def _attach_meal_timing_decision(
    result: dict[str, Any],
    demand: dict[str, Any],
    supply: dict[str, Any],
    *,
    top_k: int,
) -> dict[str, Any]:
    if not _needs_meal_timing_choice(demand):
        return result

    normal_result = result
    if not _result_has_preview_plan(normal_result):
        normal_result = schedule_timeline(
            _with_meal_timing(demand, "normal"),
            supply,
            top_k=top_k,
            _with_decision_options=False,
        )
    early_result = schedule_timeline(
        _with_meal_timing(demand, "earlier"),
        supply,
        top_k=top_k,
        _with_decision_options=False,
    )
    chosen = normal_result if _result_has_preview_plan(normal_result) else result
    chosen_option_id = "normal_dinner"
    if not _result_has_preview_plan(chosen) and early_result.get("status") == "ok":
        chosen = early_result
        chosen_option_id = "early_simple_meal"

    options = [
        _meal_timing_option(
            option_id="normal_dinner",
            label="按正常饭点吃",
            result=normal_result,
            tradeoff="尊重平常吃晚饭的节奏。只是如果结束时间卡得紧，这顿就适合吃得利落一点；想坐着多聊会儿，可以少安排一站或把结束时间放宽半小时。",
            user_prompt="我选按正常饭点吃。如果坐下来吃饭时间不够，就帮我少安排一站，或者提示我需要放宽结束时间。",
        ),
        _meal_timing_option(
            option_id="early_simple_meal",
            label="提前吃一顿轻松点的",
            result=early_result,
            tradeoff="把吃饭提前一些，适合必须早结束的时候。它更像先好好垫一顿，不会把 16:30 偷偷当成正常晚饭。",
            user_prompt="我选提前吃一顿轻松点的。把吃饭往前放，其他体验尽量保留。",
        ),
    ]
    decision = {
        "type": "meal_timing_conflict",
        "title": "吃饭这块想让你拍板",
        "message": "你给的结束时间比正常晚饭略早，我没有直接替你们改成早吃，而是把两种更现实的走法都放在这里。",
        "chosenOptionId": chosen_option_id,
        "options": options,
    }
    plan = chosen.get("timelinePlan", {}) if isinstance(chosen.get("timelinePlan"), dict) else {}
    plan["decisionRequired"] = True
    plan["mealTimingDecision"] = decision
    plan["decisionOptions"] = options
    plan.setdefault("reasonBadges", []).append("饭点需要你确认")
    tip = (
        "当前主方案是提前吃一顿轻松点的；如果你们想按平常晚饭节奏走，可以切到另一个走法，只是留给吃饭的时间会更短。"
        if chosen_option_id == "early_simple_meal"
        else "当前主方案按平常晚饭节奏走；我也放了一个提前吃的备选，避免系统偷偷替你们改饭点。"
    )
    plan.setdefault("riskTips", []).insert(0, tip)
    chosen["timelinePlan"] = plan
    chosen["mealTimingDecision"] = decision
    chosen["decisionOptions"] = options
    if isinstance(chosen.get("selectedCombination"), dict):
        chosen["selectedCombination"]["mealTimingChoice"] = chosen_option_id
    chosen["strategy"] = f"{chosen.get('strategy', 'scheduler')}_with_meal_timing_decision"
    return chosen


def _meal_first(demand: dict[str, Any]) -> bool:
    text = _raw_user_text(demand)
    if _meal_timing(demand) == "earlier":
        return True
    if any(keyword in text for keyword in ("先吃", "先晚饭", "先吃晚饭", "先吃饭", "吃完饭再", "饭后再")):
        return True
    start = _parse_minutes(demand.get("timeWindow", {}).get("startTime"))
    if start is None:
        start = _default_start_minutes(demand)
    return bool(
        _requires_dinner_anchor(demand)
        and DINNER_EARLIEST_MINUTES <= start <= DINNER_LATEST_MINUTES
    )


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
    if int(_planning_policy(demand).get("targetExperienceBlocks") or 0) >= 2:
        return True
    if requested_components == {"activity"}:
        return True
    if _wants_loose_mall_stroll(demand) and not any(keyword in text for keyword in ("必须玩", "一定要玩", "专门玩", "买票", "游乐场")):
        return bool(plan_control.get("requireActivity"))
    return bool(plan_control.get("requireActivity")) or any(
        keyword in text for keyword in ("想玩", "我要玩", "景点", "逛一下", "明确可玩活动", "不要自由活动", "看电影", "电影票")
    )


def _route_ref(route: dict[str, Any] | None) -> str | None:
    return route_identity.route_ref(route)


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
    policy = _planning_policy(demand)
    if not policy.get("includeOutboundRoute"):
        return False
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


def _activity_has_slot_at(activity: dict[str, Any] | None, cursor: int | None, minutes: int = 1) -> bool:
    if not activity or cursor is None:
        return True
    slots = (activity.get("availability") or {}).get("timeSlots") or []
    if not slots:
        return True
    end = cursor + max(1, int(minutes or 1))
    for slot in slots:
        slot_start = _parse_minutes(slot.get("start"))
        slot_end = _parse_minutes(slot.get("end"))
        if slot_start is None or slot_end is None:
            continue
        if slot_end <= slot_start:
            slot_end += 24 * 60
        if slot_start <= cursor and end <= slot_end:
            return True
    return False


def _activity_slot_start_minutes(activity: dict[str, Any] | None) -> list[int]:
    if not activity:
        return []
    slots = (activity.get("availability") or {}).get("timeSlots") or []
    parsed = [_parse_minutes(slot.get("start")) for slot in slots if isinstance(slot, dict)]
    return sorted(value for value in parsed if value is not None)


def _poi_open_range_for_demand(poi: dict[str, Any] | None, demand: dict[str, Any]) -> tuple[int, int] | None:
    if not poi:
        return None
    open_hours = poi.get("openHours")
    if not isinstance(open_hours, dict):
        return None
    key = "weekend" if _is_weekend(demand.get("timeWindow", {}).get("dateText")) else "weekday"
    return _parse_time_range(str(open_hours.get(key) or ""))


def _align_activity_start(
    timeline: list[dict[str, Any]],
    activity: dict[str, Any],
    cursor: int | None,
    demand: dict[str, Any],
) -> tuple[int | None, dict[str, Any] | None]:
    if cursor is None or _activity_has_slot_at(activity, cursor):
        aligned_cursor = cursor
    else:
        slots = _activity_slot_start_minutes(activity)
        if not slots:
            aligned_cursor = cursor
        else:
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
                title="活动开场前等一会",
                description=f"这个点要到 {_format_minutes(aligned, '待定')} 才方便开始，中间可以集合、买水，或者在附近先熟悉一下环境。",
            )
            aligned_cursor = aligned

    open_range = _poi_open_range_for_demand(activity, demand)
    if open_range and aligned_cursor is not None:
        open_start, _ = open_range
        if aligned_cursor < open_start:
            _add_step(
                timeline,
                cursor=aligned_cursor,
                minutes=open_start - aligned_cursor,
                item_type="buffer",
                title="等开门前附近走走",
                description=f"{activity.get('name')} {_format_minutes(open_start, '待定')} 后开始更稳，这段可以先在附近集合、拍照或买点喝的。",
            )
            aligned_cursor = open_start
    return aligned_cursor, None


def _route_for_area(supply: dict[str, Any], area_id: str | None) -> dict[str, Any] | None:
    if not area_id:
        return None
    routes = supply.get("routeCandidates", [])
    inbound = [
        route for route in routes
        if route.get("toAreaId") == area_id
        and (route.get("isCrossCityInbound") or route.get("routeType") in {"origin_to_area", "estimated_origin_to_area"})
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
            or route.get("routeType") in {"origin_to_area", "estimated_origin_to_area", "cross_city_inbound"}
            or route.get("isCrossCityInbound")
        )
    ]
    if not origin_routes:
        return None
    inbound = sorted(origin_routes, key=lambda item: (item.get("minutes", 999), item.get("estimatedCostTotal", 999)))[0]
    reversed_route = {
        **inbound,
        "fromAreaId": inbound.get("toAreaId"),
        "toAreaId": inbound.get("fromAreaId"),
        "routeType": "return_to_origin",
        "isCrossCityInbound": False,
        "routeSummary": f"返程预留：从{AREA_LABELS.get(str(inbound.get('toAreaId')), '目标商圈')}回到{AREA_LABELS.get(str(inbound.get('fromAreaId')), '出发地附近')}，按来程反向约{inbound.get('minutes')}分钟",
    }
    for key in ("routeId", "routeRef", "legacyRouteRef"):
        reversed_route.pop(key, None)
    return route_identity.with_route_identity(reversed_route)


def _should_include_return_route(demand: dict[str, Any]) -> bool:
    policy = _planning_policy(demand)
    if policy.get("includeReturnRoute"):
        return True
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


def _constraints_patch(demand: dict[str, Any]) -> dict[str, Any]:
    plan_control = demand.get("planControl", {}) if isinstance(demand.get("planControl"), dict) else {}
    patch = plan_control.get("constraintsPatch", {}) if isinstance(plan_control.get("constraintsPatch"), dict) else {}
    return patch


def _same_area_required(demand: dict[str, Any]) -> bool:
    patch = _constraints_patch(demand)
    location = demand.get("location", {}) if isinstance(demand.get("location"), dict) else {}
    return patch.get("distancePreference") == "same_area" or location.get("distancePreference") == "same_area"


def _route_sensitive_intent(demand: dict[str, Any]) -> bool:
    if _same_area_required(demand):
        return True
    location = demand.get("location", {}) if isinstance(demand.get("location"), dict) else {}
    distance_text = str(location.get("distancePreference") or "")
    if any(term in distance_text for term in ("附近", "别太远", "近一点", "少走路", "少折腾", "少转场")):
        return True
    profile = demand.get("demandProfile") if isinstance(demand.get("demandProfile"), dict) else {}
    for item in profile.get("dimensions", []) if isinstance(profile.get("dimensions"), list) else []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        source = item.get("source")
        target = item.get("target")
        importance = item.get("importance")
        if (
            key == "routeConvenience"
            and source in {"explicit", "llm_inference"}
            and isinstance(target, (int, float))
            and float(target) >= 0.8
            and isinstance(importance, (int, float))
            and float(importance) >= 0.65
        ):
            return True
    return False


def _route_transport_allowed(demand: dict[str, Any] | None, route: dict[str, Any]) -> bool:
    if not demand:
        return True
    patch = _constraints_patch(demand)
    avoid = {str(item) for item in patch.get("avoidTransport", [])} if isinstance(patch.get("avoidTransport"), list) else set()
    preference = str(patch.get("transportPreference") or demand.get("location", {}).get("transportPreference") or "")
    transport = str(route.get("transport") or "")
    if transport in avoid:
        return False
    if preference in {"public_transport_or_walk", "no_taxi", "walk_or_public"} and transport == "taxi":
        return False
    return True


def _route_between(
    supply: dict[str, Any],
    start: str | None,
    end: str | None,
    demand: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not start or not end or start == end:
        return None
    if demand and _same_area_required(demand):
        return None
    routes = [
        route for route in supply.get("routeCandidates", [])
        if route.get("fromAreaId") == start and route.get("toAreaId") == end
        and _route_transport_allowed(demand, route)
    ]
    if not routes:
        return None
    return sorted(routes, key=lambda item: (item.get("minutes", 999), item.get("estimatedCostTotal", 999)))[0]


def _activity_minutes(activity: dict[str, Any] | None) -> int:
    if not activity:
        return 0
    return _activity_duration_bounds(activity)[1]


def _activity_duration_bounds(activity: dict[str, Any] | None) -> tuple[int, int, int]:
    if not activity:
        return 0, 0, 0
    preferred = int(activity.get("suggestedDurationMinutes") or DEFAULT_ACTIVITY_MINUTES)
    category = str(activity.get("category") or "").lower()
    text = " ".join(
        [
            category,
            str(activity.get("name") or ""),
            " ".join(str(tag) for tag in activity.get("tags", [])),
            " ".join(str(tag) for tag in activity.get("behaviorTags", [])),
        ]
    )
    fixed_terms = ("cinema_ticket", "movie", "performance", "script_game", "剧本杀", "电影", "演出", "场次")
    light_terms = (
        "sub_area",
        "citywalk",
        "mall",
        "book",
        "tea",
        "cafe",
        "photo",
        "snack",
        "street",
        "square",
        "park_walk",
        "二级商圈",
        "步行街",
        "商场",
        "书店",
        "茶",
        "咖啡",
        "拍照",
        "广场",
        "轻逛",
        "小吃",
    )
    visit_terms = ("museum", "exhibition", "landmark", "culture", "temple", "aquarium", "park", "展览", "博物馆", "地标", "文化", "公园", "水族")
    guided_terms = ("workshop", "handcraft", "playground", "sport", "体验课", "手作", "乐园", "运动")
    if any(term in text for term in fixed_terms):
        return preferred, preferred, preferred
    if activity.get("isFiller") or _is_sub_area_node(activity) or any(term in text for term in light_terms):
        minimum = max(MIN_ACTIVITY_MINUTES, min(preferred, preferred - 30))
        return minimum, preferred, max(preferred, preferred + 30)
    if any(term in text for term in visit_terms):
        minimum = max(45, preferred - 30)
        return minimum, preferred, max(preferred + 30, min(120, preferred + 60))
    if any(term in text for term in guided_terms):
        minimum = max(45, preferred - 15)
        return minimum, preferred, max(preferred, preferred + 15)
    minimum = max(45, preferred - 15)
    return minimum, preferred, max(preferred, preferred + 15)


def _activity_duration_options(activity: dict[str, Any] | None, demand: dict[str, Any]) -> list[int]:
    min_minutes, preferred_minutes, max_minutes = _activity_duration_bounds(activity)
    if min_minutes == max_minutes:
        return [preferred_minutes]
    values = list(range(min_minutes, max_minutes + 1, RESTAURANT_DURATION_STEP_MINUTES))
    if preferred_minutes not in values:
        values.append(preferred_minutes)
    values = sorted(set(values))
    tight_window = _time_window_duration_minutes(demand) <= 4 * 60 or _needs_meal_timing_choice(demand)
    if tight_window or _meal_timing(demand) == "earlier":
        return sorted(values)
    return sorted(values, key=lambda value: (abs(value - preferred_minutes), -value))


def _fit_activity_minutes(
    activity: dict[str, Any] | None,
    demand: dict[str, Any],
    cursor: int | None,
    end_limit: int | None,
    *,
    reserve_after_minutes: int = 0,
) -> tuple[int, dict[str, Any] | None]:
    options = _activity_duration_options(activity, demand)
    if not options:
        return 0, None
    preferred = _activity_minutes(activity)
    if cursor is None or end_limit is None:
        chosen = preferred if preferred in options else options[-1]
    else:
        latest_end = end_limit - max(0, reserve_after_minutes)
        fitting = [
            minutes
            for minutes in options
            if cursor + minutes <= latest_end
            and _poi_open_for_interval(activity, demand, cursor, minutes)
            and _activity_has_slot_at(activity, cursor, minutes)
        ]
        if not fitting:
            return preferred, {
                "reason": "活动时长无法放入时间窗口",
                "activity": (activity or {}).get("name"),
                "arrival": _format_minutes(cursor, "待定"),
                "endLimit": _format_minutes(end_limit, "待定"),
                "durationOptions": options,
                "reserveAfterMinutes": reserve_after_minutes,
            }
        preferred_fitting = [minutes for minutes in fitting if minutes >= preferred]
        chosen = min(preferred_fitting, key=lambda minutes: abs(minutes - preferred)) if preferred_fitting else max(fitting)
    return chosen, {
        "durationMinutes": chosen,
        "preferredDurationMinutes": preferred,
        "compressed": chosen < preferred,
        "durationOptions": options,
        "fixedDuration": len(options) == 1,
    }


def _activity_duration_note(meta: dict[str, Any] | None) -> str:
    if not meta:
        return ""
    duration = int(meta.get("durationMinutes") or 0)
    preferred = int(meta.get("preferredDurationMinutes") or duration)
    if meta.get("fixedDuration"):
        return f" 这个是固定场次，基本就是 {duration} 分钟。"
    if duration < preferred:
        return f" 这里先留 {duration} 分钟，把最值得看的部分逛到；如果想慢慢看，可以少安排后面的一站。"
    if duration > preferred:
        return f" 这里给你们留 {duration} 分钟，逛起来会更从容一点。"
    return f" 这里给你们留 {duration} 分钟。"


def _is_sub_area_node(item: dict[str, Any] | None) -> bool:
    if not isinstance(item, dict):
        return False
    text = " ".join(
        [
            str(item.get("poiLevel") or ""),
            str(item.get("category") or ""),
            " ".join(str(tag) for tag in item.get("tags", [])),
            " ".join(str(tag) for tag in item.get("matchedSemanticTags", [])),
        ]
    )
    return item.get("poiLevel") == "sub_area" or "sub_area" in text or "二级商圈" in text


def _candidate_estimated_cost(item: dict[str, Any] | None) -> float:
    if not item:
        return 0.0
    value = item.get("estimatedCost")
    if isinstance(value, (int, float)):
        return float(value)
    value = item.get("pricePerPerson")
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _candidate_duration_minutes(item: dict[str, Any] | None) -> int:
    if not item:
        return 0
    for key in ("suggestedDurationMinutes", "durationMinutes", "estimatedDurationMinutes"):
        value = item.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return DEFAULT_ACTIVITY_MINUTES


def _append_unique_candidate(target: list[dict[str, Any]], item: dict[str, Any] | None) -> None:
    if not item:
        return
    key = poi_identity.comparable_place_key(item) or str(item.get("poiId") or item.get("id") or item.get("name") or "")
    if not key:
        return
    for existing in target:
        existing_key = poi_identity.comparable_place_key(existing) or str(existing.get("poiId") or existing.get("id") or existing.get("name") or "")
        if existing_key == key:
            return
    target.append(item)


def _sit_down_filler_as_restaurant_candidates(
    fillers: list[dict[str, Any]],
    demand: dict[str, Any],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    if not _is_low_cost_intent(demand):
        return []
    budget_limit = _budget_limit(demand)
    consumable_terms = (
        "cafe",
        "coffee",
        "tea",
        "tea_drink",
        "tea_meal",
        "dessert",
        "snack",
        "light_food",
        "奶茶",
        "茶",
        "咖啡",
        "甜品",
        "小吃",
        "轻食",
        "饮品",
    )
    candidates: list[dict[str, Any]] = []
    for item in fillers:
        text = " ".join(
            [
                str(item.get("name") or ""),
                str(item.get("category") or ""),
                " ".join(str(tag) for tag in item.get("tags", [])),
                " ".join(str(tag) for tag in item.get("behaviorTags", [])),
            ]
        ).lower()
        if not any(term.lower() in text for term in consumable_terms):
            continue
        category = str(item.get("category") or "").lower()
        if any(blocked in category for blocked in ("bookstore", "mall_walk", "citywalk", "street", "square")):
            continue
        if "walk" in category and "snack" not in category:
            continue
        cost = _candidate_estimated_cost(item)
        if budget_limit is not None and cost > budget_limit:
            continue
        availability = item.get("availability") if isinstance(item.get("availability"), dict) else {}
        queue = availability.get("minQueueMinutes", availability.get("queueMinutes", 8))
        converted = deepcopy(item)
        converted["kind"] = "restaurant"
        converted["cuisine"] = converted.get("cuisine") or "light_sit_down"
        converted["score"] = float(converted.get("score") or 0) + 2.5
        converted["isFillerRestaurant"] = True
        converted["availability"] = {
            "poiId": converted.get("poiId"),
            "dateText": demand.get("timeWindow", {}).get("dateText"),
            "tableAvailable": True,
            "queueMinutes": int(queue or 8),
            "availableSlots": [],
            "sourceType": "activity_filler_as_sit_down_node",
            "confidence": 0.78,
        }
        reasons = list(converted.get("matchedReasons") or [])
        reasons.append("低预算下用作坐下聊天/休息点")
        converted["matchedReasons"] = reasons
        candidates.append(converted)
    candidates.sort(key=lambda candidate: (_candidate_estimated_cost(candidate), -float(candidate.get("score") or 0)))
    return candidates[:top_k]


def _scheduler_candidate_pool(
    candidates: list[dict[str, Any]],
    demand: dict[str, Any],
    *,
    kind: str,
    top_k: int,
) -> list[dict[str, Any]]:
    """Keep the scheduler search pool diverse without adding semantic patches."""
    if not candidates:
        return []
    strict_budget = _budget_is_strict(demand) or _is_low_cost_intent(demand)
    target_size = min(len(candidates), max(top_k, top_k + 4))
    selected: list[dict[str, Any]] = []

    for item in candidates[: max(4, top_k // 2)]:
        _append_unique_candidate(selected, item)

    if strict_budget:
        cheapest = sorted(
            candidates,
            key=lambda candidate: (_candidate_estimated_cost(candidate), -float(candidate.get("score") or 0)),
        )
        for item in cheapest[: max(4, top_k // 2)]:
            _append_unique_candidate(selected, item)

    if kind == "activity":
        for item in [candidate for candidate in candidates if _is_sub_area_node(candidate)][:4]:
            _append_unique_candidate(selected, item)
        short_nodes = sorted(
            [candidate for candidate in candidates if _candidate_duration_minutes(candidate) <= 60],
            key=lambda candidate: (_candidate_duration_minutes(candidate), _candidate_estimated_cost(candidate)),
        )
        for item in short_nodes[:4]:
            _append_unique_candidate(selected, item)

    for item in candidates:
        if len(selected) >= target_size:
            break
        _append_unique_candidate(selected, item)
    return selected[:target_size]


def _poi_name_key(item_or_name: Any) -> str:
    if isinstance(item_or_name, dict):
        name = str(item_or_name.get("name") or item_or_name.get("title") or "")
    else:
        name = str(item_or_name or "")
    return poi_identity.normalized_name(name)


def _poi_place_key(item: dict[str, Any] | None) -> str:
    return poi_identity.comparable_place_key(item)


def _is_open_access_node(item: dict[str, Any] | None) -> bool:
    return poi_identity.is_open_access(item)


def _restaurant_duration_bounds(restaurant: dict[str, Any] | None, demand: dict[str, Any] | None = None) -> tuple[int, int, int]:
    if not restaurant:
        return MIN_RESTAURANT_MINUTES, DEFAULT_RESTAURANT_MINUTES, DEFAULT_RESTAURANT_MINUTES
    cuisine = str(restaurant.get("cuisine") or "").lower()
    tags = " ".join(str(tag) for tag in [*restaurant.get("tags", []), *restaurant.get("behaviorTags", [])])
    text = f"{cuisine} {tags} {restaurant.get('name') or ''}"
    quick_terms = (
        "simple",
        "student",
        "fast",
        "snack",
        "noodle",
        "dumpling",
        "cafe",
        "tea",
        "book",
        "bookstore",
        "light",
        "书店",
        "简餐",
        "快餐",
        "轻食",
        "小吃",
        "面",
        "饺",
        "盖饭",
        "茶餐",
        "少排队",
    )
    long_terms = ("hotpot", "barbecue", "family", "bistro", "火锅", "烤肉", "正餐", "聚餐", "家庭", "可预约")
    if any(term in text for term in quick_terms):
        return 30, 45, 60
    if any(term in text for term in long_terms):
        return 45, 75, 90
    return 45, 60, 75


def _restaurant_duration_options(restaurant: dict[str, Any] | None, demand: dict[str, Any]) -> list[int]:
    min_minutes, preferred_minutes, max_minutes = _restaurant_duration_bounds(restaurant, demand)
    values = list(range(min_minutes, max_minutes + 1, RESTAURANT_DURATION_STEP_MINUTES))
    if preferred_minutes not in values:
        values.append(preferred_minutes)
    values = sorted(set(values))
    if _meal_timing(demand) == "earlier" or _needs_meal_timing_choice(demand):
        ordered = sorted(values)
    else:
        ordered = sorted(values, key=lambda value: (abs(value - preferred_minutes), -value))
    return ordered


def _restaurant_minutes(restaurant: dict[str, Any] | None, demand: dict[str, Any] | None = None) -> int:
    if demand is None:
        return _restaurant_duration_bounds(restaurant)[1]
    return _restaurant_duration_bounds(restaurant, demand)[1]


def _fit_restaurant_minutes(
    restaurant: dict[str, Any] | None,
    demand: dict[str, Any],
    cursor: int | None,
    end_limit: int | None,
    *,
    reserve_after_minutes: int = 0,
) -> tuple[int, dict[str, Any] | None]:
    options = _restaurant_duration_options(restaurant, demand)
    preferred = _restaurant_minutes(restaurant, demand)
    if cursor is None or end_limit is None:
        chosen = preferred if preferred in options else options[-1]
    else:
        latest_end = end_limit - max(0, reserve_after_minutes)
        fitting = [minutes for minutes in options if cursor + minutes <= latest_end]
        if not fitting:
            return preferred, {
                "reason": "餐饮时长无法放入时间窗口",
                "restaurant": (restaurant or {}).get("name"),
                "arrival": _format_minutes(cursor, "待定"),
                "endLimit": _format_minutes(end_limit, "待定"),
                "durationOptions": options,
                "reserveAfterMinutes": reserve_after_minutes,
            }
        chosen = max(fitting)
        preferred_fitting = [minutes for minutes in fitting if minutes >= preferred]
        if preferred_fitting:
            chosen = min(preferred_fitting, key=lambda minutes: abs(minutes - preferred))
    meta = {
        "durationMinutes": chosen,
        "preferredDurationMinutes": preferred,
        "compressed": chosen < preferred,
        "durationOptions": options,
    }
    return chosen, meta


def _restaurant_duration_note(meta: dict[str, Any] | None) -> str:
    if not meta:
        return ""
    duration = int(meta.get("durationMinutes") or 0)
    preferred = int(meta.get("preferredDurationMinutes") or duration)
    if duration < preferred:
        return f" 这顿先留 {duration} 分钟，更适合吃得清爽利落一点；如果想坐着慢慢聊，建议少安排一站或把结束时间往后放一点。"
    return f" 这顿留 {duration} 分钟，基本够正常吃完。"


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
    place_group_id: str | None = None,
    access_type: str | None = None,
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
            "placeGroupId": place_group_id,
            "accessType": access_type,
        }
    )
    return end


def _step_minutes(step: dict[str, Any]) -> int:
    return timeline_quality.step_minutes(step)


def _is_idle_step(step: dict[str, Any]) -> bool:
    return timeline_quality.is_idle_step(step)


def _is_experience_step(step: dict[str, Any]) -> bool:
    return timeline_quality.is_experience_step(step)


def _timeline_quality_metrics(
    demand: dict[str, Any],
    timeline: list[dict[str, Any]],
    cursor: int | None,
) -> dict[str, Any]:
    start, end_limit = _time_window_bounds(demand)
    return timeline_quality.metrics(timeline=timeline, window_start=start, window_end=end_limit, cursor=cursor)


def _timeline_quality_rejection(demand: dict[str, Any], metrics: dict[str, Any], has_restaurant: bool) -> dict[str, Any] | None:
    policy = _planning_policy(demand)
    return timeline_quality.rejection(
        metrics_value=metrics,
        max_idle_minutes=int(policy.get("maxIdleMinutes") or DEFAULT_MAX_IDLE_MINUTES),
        target_experience_blocks=int(policy.get("targetExperienceBlocks") or 0),
        has_restaurant=has_restaurant,
    )


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
            timeline[-1]["title"] = "到店前留点余量"
            timeline[-1]["description"] = (
                f"这家店更适合 {timeline[-1]['end']} 左右到，中间留给取号、等人和从上一个点慢慢走过去。"
            )
        else:
            timeline[-1]["description"] = (
                f"{timeline[-1].get('description', '')} 到店时间顺到 {timeline[-1]['end']} 更稳。"
            )
    else:
        _add_step(
            timeline,
            cursor=cursor,
            minutes=wait_minutes,
            item_type="buffer",
            title="到店前附近小逛",
            description=f"这家店 {_format_minutes(aligned, '待定')} 左右到更稳，中间可以等人、取号，或者在附近小逛一下。",
        )
    return aligned, None


def _reason_values(candidate: dict[str, Any], key: str) -> list[str]:
    details = candidate.get("reasonDetails", {}) if isinstance(candidate.get("reasonDetails"), dict) else {}
    values = details.get(key, [])
    return [str(item) for item in values if item] if isinstance(values, list) else []


def _strip_reason_prefix(value: str) -> str:
    return value.split("：", 1)[1] if "：" in value else value


def _candidate_reason_summary(candidate: dict[str, Any], *, fallback: str) -> str:
    values: list[str] = []
    feasibility = _reason_values(candidate, "feasibility")
    explicit = _reason_values(candidate, "explicitPreference")
    profile = _reason_values(candidate, "profileAssist")
    for item in [*explicit, *profile, *feasibility]:
        value = _strip_reason_prefix(item).strip()
        if not value or value in {"基础评分较高", "预算友好"}:
            continue
        if value not in values:
            values.append(value)
    if values:
        return "、".join(values[:3]) + "，放进这段行程比较顺。"
    return fallback


def _activity_availability_description(item: dict[str, Any], availability: dict[str, Any] | None) -> str:
    if _is_open_access_node(item):
        return " 开放街区，不用预约；人多的时候留一点集合、拍照和找路时间就好。"
    if not availability:
        return ""
    return (
        f" 余票 {availability.get('bestTicketLeft')}，"
        f"排队约 {availability.get('minQueueMinutes')} 分钟。"
    )


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
        badges.append("适合同伴状态")
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
    if activity and _is_open_access_node(activity):
        badges.append("开放可逛")
    elif activity and (activity.get("availability") or {}).get("minQueueMinutes", 99) <= 15:
        badges.append("排队较短")
    result: list[str] = []
    for badge in badges:
        if badge not in result:
            result.append(badge)
    return result[:4]


def _commercial_meta(demand: dict[str, Any], selected_nodes: list[dict[str, Any] | None], total_cost: float) -> dict[str, Any]:
    expected_platform_revenue = sum(
        float((item or {}).get("businessMetrics", {}).get("expectedPlatformRevenue") or 0)
        for item in selected_nodes
    )
    budget_limit = _budget_limit(demand)
    budget_utilization = total_cost / budget_limit if budget_limit and budget_limit > 0 else None
    if _is_low_cost_intent(demand):
        business_score = -total_cost / 20.0
    else:
        utilization_score = (
            max(0.0, 1.0 - abs(TARGET_BUDGET_UTILIZATION - min(budget_utilization, 1.2))) * 10.0
            if budget_utilization is not None
            else 0.0
        )
        business_score = expected_platform_revenue * 0.45 + utilization_score * 0.55
    return {
        "expectedPlatformRevenue": round(expected_platform_revenue, 3),
        "budgetUtilization": round(budget_utilization, 4) if budget_utilization is not None else None,
        "budgetFitScore": round(_budget_fit_score(demand, total_cost, weight=10.0), 3),
        "businessScore": round(business_score, 3),
    }


def _plan_total_cost(plan: dict[str, Any] | None) -> float:
    if not isinstance(plan, dict):
        return float("inf")
    budget = plan.get("budgetEstimate") if isinstance(plan.get("budgetEstimate"), dict) else {}
    value = budget.get("totalCost")
    if isinstance(value, (int, float)):
        return float(value)
    return float("inf")


def _plan_route_cost(plan: dict[str, Any] | None) -> float:
    if not isinstance(plan, dict):
        return float("inf")
    budget = plan.get("budgetEstimate") if isinstance(plan.get("budgetEstimate"), dict) else {}
    value = budget.get("routeCost")
    if isinstance(value, (int, float)):
        return float(value)
    return float("inf")


def _build_candidate_plan(
    demand: dict[str, Any],
    supply: dict[str, Any],
    activity: dict[str, Any] | None,
    restaurant: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    start, end_limit = _time_window_bounds(demand)
    cursor = start
    timeline: list[dict[str, Any]] = []
    route_cost = 0.0
    meal_first = _meal_first(demand)
    transfer_route = None
    return_route = None

    policy = _planning_policy(demand)
    selected_area = (restaurant or activity or {}).get("areaId") if meal_first else (activity or restaurant or {}).get("areaId")
    first_route = _multi_origin_route_for_area(supply, selected_area) if _has_multi_origin(demand) else _route_for_area(supply, selected_area)
    if _has_multi_origin(demand) and selected_area and not first_route:
        return None, {"reason": "多人公平集合缺少完整出发点路线矩阵", "areaId": selected_area}
    if _requires_inbound_route(demand) and selected_area and not first_route:
        return None, {"reason": "跨城出行缺少到目标商圈的入城路线", "areaId": selected_area}
    if first_route and first_route.get("type") != "multi_origin_fairness" and not (
        first_route.get("isCrossCityInbound") or first_route.get("routeType") in {"origin_to_area", "estimated_origin_to_area"}
    ):
        first_route = None
    if first_route and policy.get("includeOutboundRoute"):
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
        restaurant_minutes, restaurant_duration_meta = _fit_restaurant_minutes(restaurant, demand, cursor, end_limit)
        if restaurant_duration_meta and restaurant_duration_meta.get("reason"):
            return None, restaurant_duration_meta
        if not _poi_open_for_interval(restaurant, demand, cursor, restaurant_minutes):
            return None, {
                "reason": "餐厅具体到店时间不在营业时间内",
                "restaurant": restaurant.get("name"),
                "start": _format_minutes(cursor, "待定"),
            }
        description = _restaurant_description(restaurant) + _restaurant_duration_note(restaurant_duration_meta)
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=restaurant_minutes,
            item_type="restaurant",
            title=restaurant.get("name", "吃饭地点"),
            description=description,
            cost=float(restaurant.get("estimatedCost", 0)),
            poi_id=restaurant.get("poiId"),
            area_id=restaurant.get("areaId"),
            place_group_id=restaurant.get("placeGroupId"),
            access_type=restaurant.get("accessType"),
        )

        if restaurant and activity and restaurant.get("areaId") != activity.get("areaId"):
            transfer_route = _route_between(supply, restaurant.get("areaId"), activity.get("areaId"), demand)
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
                title="路上留点余量",
                description="预留找路、等人和现场小变化的时间。",
            )

    if activity:
        cursor, align_error = _align_activity_start(timeline, activity, cursor, demand)
        if align_error:
            return None, align_error
        activity_reserve_after_minutes = 0
        if restaurant and not meal_first:
            if activity.get("areaId") != restaurant.get("areaId"):
                route_after_activity = _route_between(supply, activity.get("areaId"), restaurant.get("areaId"), demand)
                if route_after_activity:
                    activity_reserve_after_minutes += int(route_after_activity.get("minutes", 0))
            activity_reserve_after_minutes += BUFFER_MINUTES + _restaurant_duration_bounds(restaurant, demand)[0]
            if _should_include_return_route(demand):
                return_after_restaurant = _reverse_origin_route(supply, restaurant.get("areaId"))
                if return_after_restaurant:
                    activity_reserve_after_minutes += int(return_after_restaurant.get("minutes", 0))
        elif _should_include_return_route(demand):
            return_after_activity = _reverse_origin_route(supply, activity.get("areaId"))
            if return_after_activity:
                activity_reserve_after_minutes += int(return_after_activity.get("minutes", 0))
        activity_minutes, activity_duration_meta = _fit_activity_minutes(
            activity,
            demand,
            cursor,
            end_limit,
            reserve_after_minutes=activity_reserve_after_minutes,
        )
        if activity_duration_meta and activity_duration_meta.get("reason"):
            return None, activity_duration_meta
        if not _poi_open_for_interval(activity, demand, cursor, activity_minutes):
            return None, {
                "reason": "活动具体开始时间不在营业时间内",
                "activity": activity.get("name"),
                "start": _format_minutes(cursor, "待定"),
            }
        if not _activity_has_slot_at(activity, cursor, activity_minutes):
            return None, {
                "reason": "活动开始时间未命中可用余票时段",
                "activity": activity.get("name"),
                "start": _format_minutes(cursor, "待定"),
            }
        availability = activity.get("availability", {})
        description = _candidate_reason_summary(activity, fallback="作为本次明确活动安排。")
        description += _activity_availability_description(activity, availability)
        description += _activity_duration_note(activity_duration_meta)
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=activity_minutes,
            item_type="activity",
            title=activity.get("name", "活动"),
            description=description,
            cost=float(activity.get("estimatedCost", 0)),
            poi_id=activity.get("poiId"),
            area_id=activity.get("areaId"),
            place_group_id=activity.get("placeGroupId"),
            access_type=activity.get("accessType"),
        )

    if not meal_first:
        transfer_route = _route_between(
            supply,
            (activity or {}).get("areaId"),
            (restaurant or {}).get("areaId"),
            demand,
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
                title="路上留点余量",
                description="预留找路、等人和现场小变化的时间。",
            )

    if restaurant and not meal_first:
        dinner_earliest = _dinner_earliest_minutes(demand)
        if _should_anchor_restaurant_as_dinner(demand, restaurant) and cursor is not None and cursor < dinner_earliest:
            gap_minutes = dinner_earliest - cursor
            cursor = _add_step(
                timeline,
                cursor=cursor,
                minutes=gap_minutes,
                item_type="buffer",
                title="餐前在附近缓一缓",
                description=(
                    "已按你的要求把吃饭时间往前靠；这段可以就近坐一下，或者在附近随手逛一圈。"
                    if dinner_earliest < DINNER_EARLIEST_MINUTES
                    else "这段留给餐前在附近小逛、取号和集合，别把吃饭压得太赶。"
                ),
            )
        cursor, align_error = _align_restaurant_start(timeline, restaurant, cursor)
        if align_error:
            return None, align_error
        return_reserve_minutes = 0
        if _should_include_return_route(demand):
            return_route = _reverse_origin_route(supply, restaurant.get("areaId"))
            if not return_route:
                return None, {"reason": "用户要求返程，但缺少返程路线估算", "areaId": restaurant.get("areaId")}
            return_reserve_minutes = int(return_route.get("minutes", 0))
        restaurant_minutes, restaurant_duration_meta = _fit_restaurant_minutes(
            restaurant,
            demand,
            cursor,
            end_limit,
            reserve_after_minutes=return_reserve_minutes,
        )
        if restaurant_duration_meta and restaurant_duration_meta.get("reason"):
            return None, restaurant_duration_meta
        if not _poi_open_for_interval(restaurant, demand, cursor, restaurant_minutes):
            return None, {
                "reason": "餐厅具体到店时间不在营业时间内",
                "restaurant": restaurant.get("name"),
                "start": _format_minutes(cursor, "待定"),
            }
        description = _restaurant_description(restaurant) + _restaurant_duration_note(restaurant_duration_meta)
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=restaurant_minutes,
            item_type="restaurant",
            title=restaurant.get("name", "吃饭地点"),
            description=description,
            cost=float(restaurant.get("estimatedCost", 0)),
            poi_id=restaurant.get("poiId"),
            area_id=restaurant.get("areaId"),
            place_group_id=restaurant.get("placeGroupId"),
            access_type=restaurant.get("accessType"),
        )

    last_area = (activity or restaurant or {}).get("areaId")
    if _should_include_return_route(demand):
        return_route = return_route or _reverse_origin_route(supply, last_area)
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
    low_cost_rejection = _violates_low_cost_ceiling(demand, total_cost)
    if low_cost_rejection:
        return None, low_cost_rejection
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
    quality_metrics = _timeline_quality_metrics(demand, timeline, cursor)
    quality_rejection = _timeline_quality_rejection(demand, quality_metrics, bool(restaurant))
    if quality_rejection:
        return None, quality_rejection

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
                "这版按路线、预算、排队和时间窗口一起排出来。",
            ] if reason
        ],
        "riskTips": ["确认前还会再次检查余票、座位、排队和路线变化。"],
        "tradeoffs": tradeoffs,
        "rawPlannerNotes": "Generated by constraint scheduler.",
        "qualityMetrics": quality_metrics,
    }
    experience_score = _score_plan(demand, activity, restaurant, first_route, transfer_route, total_cost, cursor)
    commercial = _commercial_meta(demand, [activity, restaurant], total_cost)
    plan["commercialEstimate"] = {
        "expectedPlatformRevenue": commercial["expectedPlatformRevenue"],
        "budgetUtilization": commercial["budgetUtilization"],
        "qualityGateApplied": True,
    }
    return plan, {
        "score": experience_score,
        "experienceScore": experience_score,
        "businessScore": commercial["businessScore"],
        "expectedPlatformRevenue": commercial["expectedPlatformRevenue"],
        "budgetUtilization": commercial["budgetUtilization"],
        "budgetFitScore": commercial["budgetFitScore"],
        "qualityMetrics": quality_metrics,
        "activity": activity.get("name") if activity else None,
        "restaurant": restaurant.get("name") if restaurant else None,
    }


def _supplemental_candidates(
    activities: list[dict[str, Any]],
    fillers: list[dict[str, Any]],
    primary: dict[str, Any] | None,
    restaurant: dict[str, Any] | None,
    demand: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    excluded = {str((primary or {}).get("poiId") or ""), str((restaurant or {}).get("poiId") or "")}
    excluded_names = {
        _poi_name_key(item)
        for item in (primary, restaurant)
        if item and _poi_name_key(item)
    }
    excluded_places = {
        _poi_place_key(item)
        for item in (primary, restaurant)
        if item and _poi_place_key(item)
    }
    policy = _planning_policy(demand)
    target_area = (restaurant or primary or {}).get("areaId")
    target_areas = _target_area_ids(demand)

    def is_micro_activity(item: dict[str, Any]) -> bool:
        if item.get("isFiller"):
            return True
        duration = _activity_minutes(item)
        text = " ".join(
            [
                str(item.get("name") or ""),
                str(item.get("category") or ""),
                " ".join(str(tag) for tag in item.get("vibeTags", [])),
                " ".join(str(tag) for tag in item.get("behaviorTags", [])),
                " ".join(str(tag) for tag in item.get("matchedReasons", [])),
            ]
        )
        return duration <= 75 or any(keyword in text for keyword in ("轻", "短", "citywalk", "书店", "茶", "小吃", "广场", "免费", "商场"))

    pool: list[dict[str, Any]] = []
    for item in [*fillers, *activities]:
        poi_id = str(item.get("poiId") or "")
        if not poi_id or poi_id in excluded:
            continue
        if _poi_name_key(item) in excluded_names:
            continue
        if _poi_place_key(item) and _poi_place_key(item) in excluded_places:
            continue
        if item.get("isFiller") or is_micro_activity(item):
            pool.append(item)

    def score(item: dict[str, Any]) -> tuple[float, float, float, str]:
        area = item.get("areaId")
        route_bonus = 0.0
        if area == target_area:
            route_bonus += 8
        elif area in target_areas:
            route_bonus += 4
        elif policy.get("allowCrossAreaTransfer"):
            route_bonus += 1
        filler_bonus = 4 if item.get("isFiller") else 0
        duration_fit = -abs(_activity_minutes(item) - 55) / 10
        return (
            float(item.get("score") or 0) + route_bonus + filler_bonus + duration_fit,
            -float(item.get("estimatedCost") or 0),
            -float(_activity_minutes(item)),
            str(item.get("name") or ""),
        )

    return sorted(pool, key=score, reverse=True)[:limit]


def _can_transfer_between(
    demand: dict[str, Any],
    route: dict[str, Any] | None,
    *,
    gap_context_minutes: int = 0,
) -> bool:
    if not route:
        return False
    policy = _planning_policy(demand)
    minutes = int(route.get("minutes") or 0)
    if not policy.get("allowCrossAreaTransfer"):
        return False
    if minutes <= int(policy.get("maxTransferMinutes") or 30):
        return True
    if policy.get("allowCrossAreaTransfer") and gap_context_minutes >= LONG_IDLE_MINUTES and minutes <= 35:
        return True
    return False


def _add_activity_like_step(
    timeline: list[dict[str, Any]],
    demand: dict[str, Any],
    item: dict[str, Any],
    cursor: int | None,
    *,
    item_type: str,
    title_prefix: str | None = None,
    end_limit: int | None = None,
    reserve_after_minutes: int = 0,
) -> tuple[int | None, dict[str, Any] | None]:
    cursor, align_error = _align_activity_start(timeline, item, cursor, demand)
    if align_error:
        return cursor, align_error
    minutes, duration_meta = _fit_activity_minutes(
        item,
        demand,
        cursor,
        end_limit,
        reserve_after_minutes=reserve_after_minutes,
    )
    if duration_meta and duration_meta.get("reason"):
        return cursor, duration_meta
    if not _poi_open_for_interval(item, demand, cursor, minutes):
        return cursor, {
            "reason": "补充体验具体开始时间不在营业时间内",
            "activity": item.get("name"),
            "start": _format_minutes(cursor, "待定"),
        }
    if not _activity_has_slot_at(item, cursor, minutes):
        return cursor, {
            "reason": "补充体验开始时间未命中可用余票时段",
            "activity": item.get("name"),
            "start": _format_minutes(cursor, "待定"),
        }
    availability = item.get("availability", {})
    description = _candidate_reason_summary(item, fallback="作为本次时间窗内顺路可逛的一站。")
    if item.get("isFiller"):
        _, description = _filler_experience_label(item)
    else:
        description += _activity_availability_description(item, availability)
    description += _activity_duration_note(duration_meta)
    title = item.get("name", "附近可逛的一站")
    if title_prefix:
        title = f"{title_prefix}{title}"
    return (
        _add_step(
            timeline,
            cursor=cursor,
            minutes=minutes,
            item_type=item_type,
            title=title,
            description=description,
            cost=float(item.get("estimatedCost", 0)),
            poi_id=item.get("poiId"),
            area_id=item.get("areaId"),
            place_group_id=item.get("placeGroupId"),
            access_type=item.get("accessType"),
        ),
        None,
    )


def _minimum_multi_node_tail_minutes(
    demand: dict[str, Any],
    supply: dict[str, Any],
    current_area: str | None,
    remaining_nodes: list[dict[str, Any]],
    restaurant: dict[str, Any] | None,
) -> int:
    minutes = 0
    previous_area = current_area
    for node in remaining_nodes:
        node_area = node.get("areaId")
        if previous_area and node_area and previous_area != node_area:
            route = _route_between(supply, previous_area, node_area)
            if route:
                minutes += int(route.get("minutes", 0))
        elif previous_area and node_area:
            minutes += 10
        minutes += _activity_duration_bounds(node)[0]
        previous_area = node_area
    if restaurant:
        restaurant_area = restaurant.get("areaId")
        if previous_area and restaurant_area and previous_area != restaurant_area:
            route = _route_between(supply, previous_area, restaurant_area)
            if route:
                minutes += int(route.get("minutes", 0))
        elif previous_area and restaurant_area:
            minutes += 10
        minutes += _restaurant_duration_bounds(restaurant, demand)[0]
        if _should_include_return_route(demand):
            return_route = _reverse_origin_route(supply, restaurant_area)
            if return_route:
                minutes += int(return_route.get("minutes", 0))
    return minutes


def _build_multi_node_candidate_plan(
    demand: dict[str, Any],
    supply: dict[str, Any],
    primary: dict[str, Any] | None,
    supplemental: dict[str, Any] | list[dict[str, Any]] | None,
    restaurant: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    supplemental_nodes = supplemental if isinstance(supplemental, list) else [supplemental] if supplemental else []
    if not primary or not supplemental_nodes:
        return None, {"reason": "多节点规划需要主活动和补充体验"}
    node_ids = [str(primary.get("poiId") or ""), *[str(item.get("poiId") or "") for item in supplemental_nodes]]
    if len(node_ids) != len(set(node_ids)):
        return None, {"reason": "多节点体验不能复用同一个 POI", "poiIds": node_ids}
    place_keys = [key for key in [_poi_place_key(primary), *[_poi_place_key(item) for item in supplemental_nodes]] if key]
    if len(place_keys) != len(set(place_keys)):
        return None, {"reason": "多节点体验不能重复安排同一实际地点", "placeKeys": place_keys}
    if _meal_first(demand):
        return None, {"reason": "当前请求要求先吃饭，多节点晚饭前补位不适用"}

    policy = _planning_policy(demand)
    start, end_limit = _time_window_bounds(demand)
    cursor = start
    timeline: list[dict[str, Any]] = []
    route_cost = 0.0
    route_refs: list[dict[str, Any]] = []

    selected_area = primary.get("areaId")
    first_route = None
    first_route = _multi_origin_route_for_area(supply, selected_area) if _has_multi_origin(demand) else _route_for_area(supply, selected_area)
    if _has_multi_origin(demand) and selected_area and not first_route:
        return None, {"reason": "多人公平集合缺少完整出发点路线矩阵", "areaId": selected_area}
    if _requires_inbound_route(demand) and selected_area and not first_route:
        return None, {"reason": "跨城出行缺少到目标商圈的入城路线", "areaId": selected_area}
    if first_route and first_route.get("type") != "multi_origin_fairness" and not (
        first_route.get("isCrossCityInbound") or first_route.get("routeType") in {"origin_to_area", "estimated_origin_to_area"}
    ):
        first_route = None
    if first_route and policy.get("includeOutboundRoute"):
        is_multi_origin_route = first_route.get("type") == "multi_origin_fairness"
        cost = float(first_route.get("estimatedCostTotal", 0))
        minutes = int(first_route.get("maxMinutes") if is_multi_origin_route else first_route.get("minutes", 0))
        route_cost += cost
        route_refs.append(first_route)
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

    cursor, error = _add_activity_like_step(
        timeline,
        demand,
        primary,
        cursor,
        item_type="activity",
        end_limit=end_limit,
        reserve_after_minutes=_minimum_multi_node_tail_minutes(
            demand,
            supply,
            primary.get("areaId"),
            supplemental_nodes,
            restaurant,
        ),
    )
    if error:
        return None, error

    previous = primary
    for index, node in enumerate(supplemental_nodes, start=1):
        if previous.get("areaId") != node.get("areaId"):
            route = _route_between(supply, previous.get("areaId"), node.get("areaId"), demand)
            if not _can_transfer_between(demand, route):
                return None, {
                    "reason": "体验节点之间缺少可接受路线",
                    "from": previous.get("name"),
                    "to": node.get("name"),
                }
            cost = float(route.get("estimatedCostTotal", 0))
            route_cost += cost
            route_refs.append(route)
            cursor = _add_step(
                timeline,
                cursor=cursor,
                minutes=int(route.get("minutes", 0)),
                item_type="route",
                title=f"去第 {index + 1} 个体验点",
                description=_route_name(route),
                cost=cost,
                route_ref=_route_ref(route),
            )
        else:
            cursor = _add_step(
                timeline,
                cursor=cursor,
                minutes=10,
                item_type="buffer",
                title="附近慢慢转场",
                description="给相邻地点之间留一点找路、等人和现场小变化的时间。",
            )

        supplemental_type = "filler" if node.get("isFiller") else "micro_activity"
        remaining_nodes = supplemental_nodes[index:]
        cursor, error = _add_activity_like_step(
            timeline,
            demand,
            node,
            cursor,
            item_type=supplemental_type,
            title_prefix=None,
            end_limit=end_limit,
            reserve_after_minutes=_minimum_multi_node_tail_minutes(
                demand,
                supply,
                node.get("areaId"),
                remaining_nodes,
                restaurant,
            ),
        )
        if error:
            return None, error
        previous = node

    return_route = None
    return_reserve_minutes = 0
    last_area = previous.get("areaId")
    transfer_route = None
    if restaurant:
        if last_area != restaurant.get("areaId"):
            transfer_route = _route_between(supply, last_area, restaurant.get("areaId"), demand)
            if not _can_transfer_between(demand, transfer_route):
                return None, {
                    "reason": "补充体验到餐厅缺少可接受路线",
                    "supplemental": previous.get("name"),
                    "restaurant": restaurant.get("name"),
                }
            cost = float(transfer_route.get("estimatedCostTotal", 0))
            route_cost += cost
            route_refs.append(transfer_route)
            cursor = _add_step(
                timeline,
                cursor=cursor,
                minutes=int(transfer_route.get("minutes", 0)),
                item_type="route",
                title="去晚饭地点",
                description=_route_name(transfer_route),
                cost=cost,
                route_ref=_route_ref(transfer_route),
            )
        else:
            cursor = _add_step(
                timeline,
                cursor=cursor,
                minutes=10,
                item_type="buffer",
                title="去吃饭的路上",
                description="从上一站慢慢走到餐厅，顺便留一点等人和现场变化的时间。",
            )

        dinner_earliest = _dinner_earliest_minutes(demand)
        if _should_anchor_restaurant_as_dinner(demand, restaurant) and cursor is not None and cursor < dinner_earliest:
            gap_minutes = dinner_earliest - cursor
            cursor = _add_step(
                timeline,
                cursor=cursor,
                minutes=gap_minutes,
                item_type="buffer",
                title="餐前附近小逛",
                description="吃饭前不用干等，可以在附近慢慢走一会儿，时间到了再进店。",
            )

        cursor, align_error = _align_restaurant_start(timeline, restaurant, cursor)
        if align_error:
            return None, align_error
        if _should_include_return_route(demand):
            return_route = _reverse_origin_route(supply, restaurant.get("areaId"))
            if not return_route:
                return None, {"reason": "用户要求返程，但缺少返程路线估算", "areaId": restaurant.get("areaId")}
            return_reserve_minutes = int(return_route.get("minutes", 0))
        restaurant_minutes, restaurant_duration_meta = _fit_restaurant_minutes(
            restaurant,
            demand,
            cursor,
            end_limit,
            reserve_after_minutes=return_reserve_minutes,
        )
        if restaurant_duration_meta and restaurant_duration_meta.get("reason"):
            return None, restaurant_duration_meta
        if not _poi_open_for_interval(restaurant, demand, cursor, restaurant_minutes):
            return None, {
                "reason": "餐厅具体到店时间不在营业时间内",
                "restaurant": restaurant.get("name"),
                "start": _format_minutes(cursor, "待定"),
            }
        cursor = _add_step(
            timeline,
            cursor=cursor,
            minutes=restaurant_minutes,
            item_type="restaurant",
            title=restaurant.get("name", "吃饭地点"),
            description=_restaurant_description(restaurant) + _restaurant_duration_note(restaurant_duration_meta),
            cost=float(restaurant.get("estimatedCost", 0)),
            poi_id=restaurant.get("poiId"),
            area_id=restaurant.get("areaId"),
            place_group_id=restaurant.get("placeGroupId"),
            access_type=restaurant.get("accessType"),
        )
    elif _should_include_return_route(demand):
        return_route = _reverse_origin_route(supply, last_area)
        if not return_route:
            return None, {"reason": "用户要求返程，但缺少返程路线估算", "areaId": last_area}

    if _should_include_return_route(demand):
        route_refs.append(return_route)
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

    if end_limit is not None and cursor is not None and cursor > end_limit:
        return None, {"reason": "超过时间窗口", "end": _format_minutes(cursor, "待定")}

    activity_nodes = [primary, *supplemental_nodes]
    activity_cost = sum(float(item.get("estimatedCost", 0)) for item in activity_nodes)
    restaurant_cost = float((restaurant or {}).get("estimatedCost", 0))
    total_cost = activity_cost + restaurant_cost + route_cost
    budget_limit = _budget_limit(demand)
    low_cost_rejection = _violates_low_cost_ceiling(demand, total_cost)
    if low_cost_rejection:
        return None, low_cost_rejection
    if _budget_is_strict(demand) and budget_limit is not None and total_cost > budget_limit:
        return None, {"reason": "超过严格预算", "totalCost": total_cost, "budgetLimit": budget_limit}

    selected_area_ids = {item.get("areaId") for item in (*activity_nodes, restaurant) if item and item.get("areaId")}
    required_anchor_areas = demand_profile.required_area_ids(demand)
    if required_anchor_areas and not required_anchor_areas.issubset(selected_area_ids):
        return None, {
            "reason": "组合没有覆盖用户明确点名的目的地区域",
            "requiredAnchorAreas": sorted(required_anchor_areas),
            "selectedAreas": sorted(selected_area_ids),
        }
    target_area_ids = _target_area_ids(demand)
    if _route_sensitive_intent(demand) and target_area_ids and not selected_area_ids.issubset(target_area_ids):
        return None, {
            "reason": "路线偏好要求优先围绕目标商圈",
            "targetAreas": sorted(target_area_ids),
            "selectedAreas": sorted(selected_area_ids),
        }

    quality_metrics = _timeline_quality_metrics(demand, timeline, cursor)
    quality_rejection = _timeline_quality_rejection(demand, quality_metrics, bool(restaurant))
    if quality_rejection:
        return None, quality_rejection

    people_total = _people_total(demand)
    selected_items: list[dict[str, Any]] = []
    for route in route_refs:
        selected_items.append(
            {
                "kind": "route",
                "poiId": None,
                "name": _multi_origin_route_description(route) if route.get("type") == "multi_origin_fairness" else _route_name(route),
                "reason": "用于估算多人集合通勤公平性。" if route.get("type") == "multi_origin_fairness" else "用于估算转场时间。",
            }
        )
    for item in activity_nodes:
        selected_items.append(
            {
                "kind": "activity" if not item.get("isFiller") else "filler",
                "poiId": item.get("poiId"),
                "name": item.get("name"),
                "reason": _candidate_reason_summary(item, fallback="通过时间、预算和供给约束。"),
                "recallSources": item.get("recallSources", []),
            }
        )
    if restaurant:
        selected_items.append(
            {
                "kind": "restaurant",
                "poiId": restaurant.get("poiId"),
                "name": restaurant.get("name"),
                "reason": _candidate_reason_summary(restaurant, fallback="时间、预算和现场供给都能接上。"),
                "recallSources": restaurant.get("recallSources", []),
            }
        )

    route_count = len([step for step in timeline if step.get("type") in {"route", "multi_origin_route"}])
    area_labels = "、".join(AREA_LABELS.get(area_id, area_id) for area_id in sorted(selected_area_ids))
    supplemental_names = "、".join(str(item.get("name")) for item in supplemental_nodes)
    if restaurant:
        summary = (
            f"先去 {primary.get('name')}，中间顺路逛 {supplemental_names}，"
            f"再去 {restaurant.get('name')}，覆盖 {area_labels}，"
            f"预估总价 {total_cost:.0f} 元，人均 {total_cost / people_total:.0f} 元。"
        )
    else:
        summary = (
            f"先去 {primary.get('name')}，再顺路逛 {supplemental_names}，覆盖 {area_labels}，"
            f"预估总价 {total_cost:.0f} 元，人均 {total_cost / people_total:.0f} 元。"
        )
    tradeoffs = _tradeoff_notes(demand)
    if route_count > 1 and restaurant:
        tradeoffs.append("这版把餐前时间换成了一个顺路可逛的点；如果你更想少折腾，可以让它改成同商圈慢一点的版本。")
    elif route_count > 1:
        tradeoffs.append("这版用顺路的第二个点把时间窗补完整，同时尽量不拉长转场。")
    else:
        tradeoffs.append("这版把茶饮、书店或短逛自然放进行程里，避免提前到商圈空等。")

    plan = {
        "status": "ok",
        "summary": summary,
        "timeline": timeline,
        "selectedItems": selected_items,
        "reasonBadges": _reason_badges(demand, primary, restaurant, first_route, transfer_route, total_cost),
        "budgetEstimate": {
            "activityCost": round(activity_cost, 2),
            "restaurantCost": round(restaurant_cost, 2),
            "routeCost": round(route_cost, 2),
            "totalCost": round(total_cost, 2),
            "perPersonCost": round(total_cost / people_total, 2),
            "currency": "CNY",
            "notes": ["费用来自候选供给，不代表真实交易价格。"],
        },
        "recommendationReasons": [
            reason for reason in [
                f"{primary.get('name')}：{_candidate_reason_summary(primary, fallback='时间、预算和现场供给都能接上。')}",
                f"{supplemental_names}：把中间时间变成顺路可逛的内容。",
                f"{restaurant.get('name')}：{_candidate_reason_summary(restaurant, fallback='时间、预算和现场供给都能接上。')}" if restaurant else None,
                "这版把路线、预算、排队和节奏一起算过。",
            ] if reason
        ],
        "riskTips": ["确认前还会再次检查余票、座位、排队和路线变化。"],
        "tradeoffs": tradeoffs,
        "rawPlannerNotes": "Generated by multi-node constraint scheduler.",
        "qualityMetrics": quality_metrics,
        "planningPolicyApplied": policy,
    }

    route_penalty = sum(
        float(route.get("maxMinutes") or route.get("minutes") or 0)
        for route in route_refs
    )
    score = (
        sum(float(item.get("score", 0)) * 2.4 for item in activity_nodes)
        + float((restaurant or {}).get("score", 0)) * 3
        + quality_metrics["activeTimeUtilization"] * 22
        + min(18, quality_metrics["experienceBlockCount"] * 7)
        - quality_metrics["idleMinutes"] / 4
        - route_penalty / 7
    )
    if quality_metrics["experienceBlockCount"] >= int(policy.get("targetExperienceBlocks") or 0):
        score += 10
    if len(selected_area_ids) > 1 and policy.get("allowCrossAreaTransfer"):
        score += 4
    if _has_meal_constraint(demand):
        score += 10
    if _requires_activity(demand):
        score += 12
    if budget_limit is not None:
        utilization = total_cost / max(budget_limit, 1)
        if _is_low_cost_intent(demand):
            score -= utilization * 16.0
        else:
            score += _budget_fit_score(demand, total_cost, weight=4.0)
    if _route_sensitive_intent(demand):
        taxi_count = sum(1 for route in route_refs if route.get("transport") == "taxi")
        cross_area_count = sum(1 for route in route_refs if route.get("fromAreaId") != route.get("toAreaId"))
        score -= taxi_count * 6
        score -= max(0, len(selected_area_ids) - 1) * 12
        score -= cross_area_count * 4
        score -= route_cost / 10
    if _is_low_cost_intent(demand):
        taxi_count = sum(1 for route in route_refs if route.get("transport") == "taxi")
        cross_area_count = sum(1 for route in route_refs if route.get("fromAreaId") != route.get("toAreaId"))
        score -= taxi_count * 18
        score -= max(0, len(selected_area_ids) - 1) * 10
        score -= cross_area_count * 5
        score -= route_cost / 12

    commercial = _commercial_meta(demand, [item for item in [primary, *supplemental_nodes, restaurant] if item], total_cost)
    plan["commercialEstimate"] = {
        "expectedPlatformRevenue": commercial["expectedPlatformRevenue"],
        "budgetUtilization": commercial["budgetUtilization"],
        "qualityGateApplied": True,
    }
    return plan, {
        "score": round(score, 3),
        "experienceScore": round(score, 3),
        "businessScore": commercial["businessScore"],
        "expectedPlatformRevenue": commercial["expectedPlatformRevenue"],
        "budgetUtilization": commercial["budgetUtilization"],
        "budgetFitScore": commercial["budgetFitScore"],
        "qualityMetrics": quality_metrics,
        "activity": primary.get("name"),
        "supplemental": "、".join(str(item.get("name")) for item in supplemental_nodes),
        "restaurant": restaurant.get("name") if restaurant else None,
        "mode": "multi_node",
    }


def _multi_node_candidate_plans(
    demand: dict[str, Any],
    supply: dict[str, Any],
    activities: list[dict[str, Any]],
    fillers: list[dict[str, Any]],
    restaurants: list[dict[str, Any]],
    *,
    top_k: int,
) -> tuple[list[tuple[float, dict[str, Any], dict[str, Any]]], list[dict[str, Any]]]:
    policy = _planning_policy(demand)
    if int(policy.get("targetExperienceBlocks") or 0) < 2:
        return [], []
    needs_restaurant = _has_meal_constraint(demand) or _needs_sit_down_component(demand)
    if _meal_first(demand) or not activities or (needs_restaurant and not restaurants):
        return [], []
    primary_choices = activities[: min(len(activities), SCHEDULER_PRIMARY_BEAM)]
    restaurant_choices: list[dict[str, Any] | None] = (
        restaurants[: min(len(restaurants), SCHEDULER_RESTAURANT_BEAM)] if needs_restaurant else [None]
    )
    allow_extra_experience = not needs_restaurant and _time_window_duration_minutes(demand) >= planning_policy.LOCAL_TRIP_MINUTES
    feasible: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    attempts = 0
    max_attempts_per_primary = max(48, SCHEDULER_MAX_MULTI_ATTEMPTS // max(len(primary_choices), 1))
    for primary in primary_choices:
        primary_attempts = 0
        stop_primary = False
        supplemental_options = _supplemental_candidates(
            activities[: top_k + 4],
            fillers[: top_k + 6],
            primary,
            None,
            demand,
            limit=SCHEDULER_SUPPLEMENTAL_BEAM,
        )
        for supplemental in supplemental_options:
            second_options: list[dict[str, Any] | None] = [None]
            if int(policy.get("targetExperienceBlocks") or 0) >= 3 or allow_extra_experience:
                second_limit = min(len(supplemental_options), SCHEDULER_SECONDARY_BEAM)
                second_options.extend(
                    item
                    for item in supplemental_options[:second_limit]
                    if item.get("poiId") not in {primary.get("poiId"), supplemental.get("poiId")}
                    and _poi_name_key(item) not in {_poi_name_key(primary), _poi_name_key(supplemental)}
                    and (
                        not _poi_place_key(item)
                        or _poi_place_key(item) not in {_poi_place_key(primary), _poi_place_key(supplemental)}
                    )
                )
            for second in second_options:
                supplemental_nodes = [supplemental, *([second] if second else [])]
                for restaurant in restaurant_choices:
                    if primary_attempts >= max_attempts_per_primary:
                        stop_primary = True
                        break
                    attempts += 1
                    primary_attempts += 1
                    if attempts > SCHEDULER_MAX_MULTI_ATTEMPTS:
                        return feasible, rejected
                    if restaurant and any(node.get("poiId") == restaurant.get("poiId") for node in supplemental_nodes):
                        continue
                    plan, meta = _build_multi_node_candidate_plan(demand, supply, primary, supplemental_nodes, restaurant)
                    if plan is None:
                        rejected.append(meta)
                        continue
                    feasible.append((float(meta["score"]), plan, meta))
                if stop_primary:
                    break
            if stop_primary:
                break
    return feasible, rejected


def _filler_experience_label(filler: dict[str, Any]) -> tuple[str, str]:
    name = str(filler.get("name") or "附近可逛的一站")
    text = " ".join(
        [
            name,
            str(filler.get("category") or ""),
            " ".join(str(tag) for tag in filler.get("tags", [])),
            " ".join(str(tag) for tag in filler.get("behaviorTags", [])),
        ]
    )
    if any(keyword in text for keyword in ("书店", "看书", "选书")):
        return name, "这里适合翻翻书、坐下聊一会儿，把餐前时间过得轻松一点。"
    if any(keyword in text for keyword in ("茶", "咖啡", "饮品", "奶茶")):
        return name, "可以点杯茶饮或咖啡坐一下，顺便等人、缓缓脚。"
    if any(keyword in text for keyword in ("街", "步行", "小吃", "商街", "citywalk")):
        return name, "沿街区逛一圈、拍两张照，餐前不会变成干等。"
    if any(keyword in text for keyword in ("商场", "潮玩", "集合店")):
        return name, "在商场里轻松逛一下，体力和预算都比较好控。"
    return name, "附近轻逛或拍照打卡一下，让这段时间更自然。"


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
    existing_poi_ids = {
        str(step.get("poiId"))
        for step in timeline
        if step.get("poiId")
    }
    existing_names = {
        _poi_name_key(step)
        for step in timeline
        if _poi_name_key(step)
    }
    existing_places = {
        _poi_place_key(step)
        for step in timeline
        if _poi_place_key(step)
    }
    same_area_fillers = [
        item
        for item in fillers
        if item.get("areaId") == preferred_area
        and str(item.get("poiId") or "") not in existing_poi_ids
        and _poi_name_key(item) not in existing_names
        and (not _poi_place_key(item) or _poi_place_key(item) not in existing_places)
    ]
    if not same_area_fillers:
        plan.setdefault("riskTips", []).append("餐前这段没有找到不重复、又顺路的附近去处，先保留成取号和找路时间。")
        return plan, None
    affordable = [
        item
        for item in same_area_fillers
        if not strict or budget_limit is None or current_total + float(item.get("estimatedCost") or 0) <= budget_limit
    ]
    if not affordable:
        plan.setdefault("riskTips", []).append("附近其实有茶饮、书店或短逛点，但当前预算卡得比较紧；可以换低价餐厅，或者把预算稍微放一点。")
        return plan, None

    filler = sorted(
        affordable,
        key=lambda item: (
            0 if any(word in str(item.get("name") or "") for word in ("书店", "茶", "咖啡", "奶茶", "小吃", "步行街", "商场")) else 1,
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
    title, description = _filler_experience_label(filler)
    filler_step = {
        "start": _format_minutes(start, "待定"),
        "end": _format_minutes(start + filler_minutes, "待定"),
        "type": "filler",
        "title": title,
        "description": description,
        "poiId": filler.get("poiId"),
        "routeRef": None,
        "estimatedCost": filler_cost,
        "areaId": filler.get("areaId"),
        "placeGroupId": filler.get("placeGroupId"),
        "accessType": filler.get("accessType"),
    }
    remaining_minutes = end - (start + filler_minutes)
    replacement = [filler_step]
    if remaining_minutes >= 10:
        replacement.append(
            {
                **timeline[buffer_index],
                "start": _format_minutes(start + filler_minutes, "待定"),
                "end": _format_minutes(end, "待定"),
                "description": "预留从该具体 POI 到晚餐地点的取号、找路和转场余量。",
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
            "reason": "餐前顺路安排的附近去处，避免原地干等。",
        }
    )
    plan.setdefault("recommendationReasons", []).append(
        f"餐前这段用 {filler.get('name')} 顺路衔接，不让大家原地等。"
    )
    return plan, filler


def _tradeoff_notes(demand: dict[str, Any]) -> list[str]:
    text = _demand_text(demand)
    notes: list[str] = []
    conflict_text = " ".join(str(item) for item in demand.get("potentialConflicts", []))
    if any(keyword in text + conflict_text for keyword in ("低成本", "不想花钱", "少花钱", "预算少", "便宜")) and any(
        keyword in text + conflict_text for keyword in ("不想太累", "走不了太多路", "少走路", "别太远")
    ):
        notes.append("你同时提到低成本和不想太累，我会优先选低预算、少走路、少转场的组合。")
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
        if _is_low_cost_intent(demand):
            score -= utilization * 14.0
        else:
            score += _budget_fit_score(demand, total_cost, weight=4.0)
    if _route_sensitive_intent(demand):
        routes = [route for route in (first_route, transfer_route) if isinstance(route, dict)]
        taxi_count = sum(1 for route in routes if route.get("transport") == "taxi")
        cross_area_count = sum(1 for route in routes if route.get("fromAreaId") != route.get("toAreaId"))
        route_cost = sum(float(route.get("estimatedCostTotal") or 0) for route in routes)
        score -= taxi_count * 6
        score -= cross_area_count * 5
        score -= max(0, len(selected_areas) - 1) * 12
        score -= route_cost / 10
    if _is_low_cost_intent(demand):
        routes = [route for route in (first_route, transfer_route) if isinstance(route, dict)]
        taxi_count = sum(1 for route in routes if route.get("transport") == "taxi")
        cross_area_count = sum(1 for route in routes if route.get("fromAreaId") != route.get("toAreaId"))
        route_cost = sum(float(route.get("estimatedCostTotal") or 0) for route in routes)
        score -= taxi_count * 18
        score -= cross_area_count * 8
        score -= route_cost / 12
    if cursor is not None:
        start, end_limit = _time_window_bounds(demand)
        window = max(1, end_limit - start)
        used = max(0, min(cursor, end_limit) - start)
        score += min(1.0, used / window) * 8.0
    return score


def schedule_timeline(
    structured_demand: dict[str, Any],
    mock_supply: dict[str, Any],
    *,
    top_k: int = SCHEDULER_MIN_TOP_K,
    _with_decision_options: bool = True,
) -> dict[str, Any]:
    top_k = max(SCHEDULER_MIN_TOP_K, min(14, top_k))
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
    fillers = [
        item for item in all_activities if item.get("isFiller") and item.get("poiId") not in excluded_poi_ids
    ][: top_k + 8]
    ranked_activities = [
        item for item in all_activities if not item.get("isFiller") and item.get("poiId") not in excluded_poi_ids
    ]
    ranked_restaurants = [
        item for item in mock_supply.get("restaurantCandidates", []) if item.get("poiId") not in excluded_poi_ids
    ]
    if _is_low_cost_intent(structured_demand):
        for item in _sit_down_filler_as_restaurant_candidates(fillers, structured_demand, top_k=top_k):
            _append_unique_candidate(ranked_restaurants, item)
    activities = _scheduler_candidate_pool(ranked_activities, structured_demand, kind="activity", top_k=top_k)
    restaurants = _scheduler_candidate_pool(ranked_restaurants, structured_demand, kind="restaurant", top_k=top_k)
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
    sit_down_hard = _needs_sit_down_component(structured_demand)
    requested_components = set(
        structured_demand.get("demandProfile", {}).get("requestedComponents", [])
    )
    activity_options: list[dict[str, Any] | None] = activities if activities else [None]
    restaurant_options: list[dict[str, Any] | None] = restaurants if (meal_hard or sit_down_hard) else [None, *restaurants]
    if _explicit_no_meal(structured_demand):
        restaurant_options = [None]
    if not restaurant_options:
        restaurant_options = [None]
    if (meal_hard or _wants_loose_mall_stroll(structured_demand)) and not require_activity:
        activity_options = [None, *activities]
    if requested_components == {"restaurant"}:
        activity_options = [None]
    if requested_components == {"activity"} and not meal_hard and not sit_down_hard:
        restaurant_options = [None]
    if require_activity:
        activity_options = activities

    feasible: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    multi_feasible, multi_rejected = _multi_node_candidate_plans(
        structured_demand,
        mock_supply,
        activities,
        fillers,
        restaurants,
        top_k=top_k,
    )
    feasible.extend(multi_feasible)
    rejected.extend(multi_rejected)
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
        result = {
            "status": "failed",
            "timelinePlan": _failed_plan(mock_supply, rejected),
            "rejectedCombinations": rejected[:20],
            "selectedCombination": None,
            "strategy": "top_k_constraint_search",
        }
        if _with_decision_options:
            result = _attach_meal_timing_decision(result, structured_demand, mock_supply, top_k=top_k)
        return result

    policy = _planning_policy(structured_demand)
    selection_pool = feasible
    if multi_feasible and int(policy.get("targetExperienceBlocks") or 0) >= 2 and not _allow_single_node_itinerary(structured_demand):
        selection_pool = multi_feasible

    low_cost_mode = _is_low_cost_intent(structured_demand)
    best_experience_score = max(float(item[2].get("experienceScore") or item[0]) for item in selection_pool)
    route_sensitive_mode = _route_sensitive_intent(structured_demand)
    if low_cost_mode:
        quality_threshold = max(0.0, best_experience_score - 55.0)
    elif route_sensitive_mode:
        quality_threshold = max(
            best_experience_score - 5.0,
            min(75.0, best_experience_score),
        )
    else:
        quality_threshold = max(
            best_experience_score - 8.0,
            min(75.0, best_experience_score),
        )
    quality_qualified = selection_pool if low_cost_mode else [
        item
        for item in selection_pool
        if float(item[2].get("experienceScore") or item[0]) >= quality_threshold
    ]
    if low_cost_mode:
        quality_qualified.sort(
            key=lambda item: (
                _plan_route_cost(item[1]),
                _plan_total_cost(item[1]),
                -float(item[2].get("experienceScore") or item[0]),
            )
        )
    else:
        quality_qualified.sort(
            key=lambda item: (
                float(item[2].get("experienceScore") or item[0]),
                -_plan_route_cost(item[1]) if route_sensitive_mode else 0.0,
                float(item[2].get("budgetFitScore") or 0),
                float(item[2].get("businessScore") or 0),
            ),
            reverse=True,
        )
    best_score, best_plan, best_meta = quality_qualified[0]
    best_plan, filler = _insert_filler_if_needed(structured_demand, best_plan, fillers)
    if filler:
        best_meta["filler"] = filler.get("name")
        best_plan["qualityMetrics"] = _timeline_quality_metrics(
            structured_demand,
            best_plan.get("timeline", []),
            _parse_minutes((best_plan.get("timeline") or [{}])[-1].get("end")) if best_plan.get("timeline") else None,
        )
    result = {
        "status": "ok",
        "timelinePlan": best_plan,
        "rejectedCombinations": rejected[:20],
        "selectedCombination": {**best_meta, "score": round(best_score, 3)},
        "strategy": "multi_node_time_window_search_with_legacy_fallback",
        "evaluatedCombinationCount": len(feasible) + len(rejected),
        "feasibleCombinationCount": len(feasible),
        "multiNodeFeasibleCombinationCount": len(multi_feasible),
        "selectionPool": "multi_node_only" if selection_pool is multi_feasible else "all_feasible",
        "qualityQualifiedCombinationCount": len(quality_qualified),
        "qualityThreshold": round(quality_threshold, 3),
        "fillerInsertion": {"inserted": bool(filler), "poiId": filler.get("poiId") if filler else None},
        "locks": locks,
    }
    if _with_decision_options:
        result = _attach_meal_timing_decision(result, structured_demand, mock_supply, top_k=top_k)
    return result


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
