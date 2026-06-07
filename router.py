"""Lightweight interaction router for FlowCity.

This stays deliberately small: the LLM/Pipeline can still do full planning,
while this module decides whether a user utterance is a new plan, a local
refinement, an explanation request, or a confirmation. Ambiguous router output
falls back to full replanning so the demo never gets stuck in a half-refined
state.
"""

from __future__ import annotations

import re
from typing import Any


ACTION_FLAG_KEYS = (
    "needNewActivity",
    "needNewRestaurant",
    "needRouteRefresh",
    "needReschedule",
    "modifyBudget",
    "modifyDistance",
    "needExplanation",
    "confirmExecution",
)

AREA_ALIASES = {
    "大雁塔": "曲江",
    "大唐不夜城": "曲江",
    "曲江大悦城": "曲江",
    "回民街": "钟楼",
    "鼓楼": "钟楼",
    "赛格": "小寨",
    "MOMOPARK": "小寨",
    "大兴善寺": "小寨",
    "熙地港": "行政中心",
    "龙首原": "大明宫",
}


def _empty_flags() -> dict[str, bool]:
    return {key: False for key in ACTION_FLAG_KEYS}


def _selected_poi_id(plan: dict[str, Any] | None, kind: str) -> str | None:
    if not isinstance(plan, dict):
        return None
    for item in plan.get("selectedItems", []):
        if item.get("kind") == kind and item.get("poiId"):
            return str(item["poiId"])
    for step in plan.get("timeline", []):
        if step.get("type") == kind and step.get("poiId"):
            return str(step["poiId"])
    return None


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _complains_long_buffer(text: str) -> bool:
    return _has_any(
        text,
        (
            "空窗",
            "空了",
            "等太久",
            "等待太久",
            "休息太久",
            "休息两个",
            "等两个",
            "两个小时",
            "两个半小时",
            "太不合理",
            "中间太空",
        ),
    )


def _locks_from_text(text: str, current_plan: dict[str, Any] | None) -> dict[str, Any]:
    locks: dict[str, Any] = {"timeFlexMinutes": 30}
    if _has_any(text, ("活动别换", "电影别换", "玩的别换", "项目别换", "保留活动", "活动不变")):
        poi_id = _selected_poi_id(current_plan, "activity")
        if poi_id:
            locks["activityPoiId"] = poi_id
    if _has_any(text, ("餐厅别换", "吃饭别换", "饭店别换", "保留餐厅", "餐厅不变")):
        poi_id = _selected_poi_id(current_plan, "restaurant")
        if poi_id:
            locks["restaurantPoiId"] = poi_id
    return locks


def _budget_constraints_from_text(text: str) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    normalized = str(text or "").replace("块", "元")
    per_person_match = (
        re.search(r"人均\s*(?:大概|差不多|约|控制在|到|就)?\s*(\d{1,4})", normalized)
        or re.search(r"(?:每人|一个人)\s*(?:大概|差不多|约|控制在|到|就)?\s*(\d{1,4})", normalized)
    )
    if per_person_match:
        patch["budgetPerPerson"] = int(per_person_match.group(1))
        patch["budgetPreference"] = "lower"
        patch["budgetFlex"] = "strict"
    total_match = re.search(r"(?:总预算|预算总共|一共|总共)\s*(?:大概|差不多|约|控制在|到|就)?\s*(\d{1,5})", normalized)
    if total_match:
        patch["budgetMaxTotal"] = int(total_match.group(1))
        patch["budgetPreference"] = "lower"
        patch["budgetFlex"] = "strict"
    if _has_any(normalized, ("便宜", "太贵", "预算低", "压预算", "省钱", "少花钱")):
        patch["budgetPreference"] = "lower"
        patch.setdefault("budgetFlex", "low_cost")
    return patch


def _constraints_patch(text: str) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if _has_any(text, ("只吃饭", "只安排吃饭", "不安排活动", "不要活动", "不玩了", "不安排项目")):
        patch["skipActivity"] = True
    patch.update(_budget_constraints_from_text(text))
    if _has_any(text, ("清淡", "少油", "低脂", "不油腻")):
        patch["foodPreference"] = "清淡不油腻"
    if _has_any(text, ("烧烤", "烤肉", "大排档", "bbq", "BBQ")):
        patch["foodPreference"] = "烧烤"
        patch["restaurantCuisine"] = "bbq"
    if _has_any(text, ("近一点", "别太远", "不想走那么远", "少走路", "附近")):
        patch["distancePreference"] = "nearer"
    if _has_any(text, ("不想打车", "不要打车", "别打车", "不打车")):
        patch["transportPreference"] = "public_transport_or_walk"
        patch.setdefault("avoidTransport", []).append("taxi")
    if _has_any(text, ("别的地方", "换商圈", "去别处", "去别的商圈", "离开这里")) and _has_any(text, ("不想", "不要", "别")):
        patch["distancePreference"] = "same_area"
    if _has_any(text, ("就在", "同商圈", "附近吃", "附近找", "附近安排")):
        patch["distancePreference"] = "same_area"
    if _has_any(text, ("允许扩大", "扩大到附近", "附近区域", "放宽区域", "扩大区域", "周边区域")):
        patch["allowNearbyAreas"] = True
        if patch.get("distancePreference") == "same_area":
            patch["distancePreference"] = "nearby_area"
    if _has_any(text, ("晚饭早一点", "晚餐早一点", "吃饭早一点", "早点吃", "早些吃", "提前吃", "先吃")):
        patch["mealTiming"] = "earlier"
    if _has_any(text, ("空窗", "缓冲", "等位", "时间段", "这段", "加点", "加些", "加一个", "奶茶", "茶饮", "休息")):
        patch["fillBuffer"] = True
    if _complains_long_buffer(text):
        patch["forbidLongBuffer"] = True
        patch["mustImprovePreviousIdle"] = True
        patch["maxIdleMinutes"] = 45
        patch["targetExperienceBlocksMin"] = 2
    for alias, area in AREA_ALIASES.items():
        if alias in text:
            patch["preferredArea"] = area
            if "附近" in text or "就在" in text:
                patch["distancePreference"] = "same_area"
    for area in ("大明宫", "小寨", "钟楼", "曲江", "高新", "行政中心"):
        if _has_any(text, (f"不想去{area}", f"不要{area}", f"别去{area}", f"避开{area}")):
            patch.setdefault("avoidAreas", []).append(area)
        elif area in text:
            patch["preferredArea"] = area
    if patch:
        patch["rawText"] = text
    return patch


def _validate_route(result: dict[str, Any], has_session: bool) -> dict[str, Any]:
    flags = result["actionFlags"]
    active_flags = [key for key, value in flags.items() if value]
    if result["mode"] == "refine" and not has_session:
        result["mode"] = "new_plan"
        result["fallbackMode"] = "full_replan"
        return result
    if result["mode"] == "refine" and not active_flags:
        result["mode"] = "new_plan"
        result["fallbackMode"] = "full_replan"
        return result
    if result["mode"] == "refine":
        locks = result.get("locks", {})
        if flags.get("needNewActivity") and locks.get("activityPoiId"):
            locks.pop("activityPoiId", None)
        if flags.get("needNewRestaurant") and locks.get("restaurantPoiId"):
            locks.pop("restaurantPoiId", None)
        result["locks"] = locks
    return result


def route_interaction(
    text: str,
    *,
    has_session: bool,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = str(text or "").strip()
    flags = _empty_flags()
    current_plan = (session or {}).get("currentPlan")

    if not has_session:
        return {
            "mode": "new_plan",
            "actionFlags": flags,
            "locks": {"timeFlexMinutes": 30},
            "constraintsPatch": {},
            "fallbackMode": "none",
            "clarificationQuestion": None,
            "rawInput": raw,
        }

    if "【节点修改上下文】" in raw or "【整体大改上下文】" in raw:
        target_kind = "generic"
        if "targetKind=restaurant" in raw:
            target_kind = "restaurant"
            flags["needNewRestaurant"] = True
        elif "targetKind=activity" in raw:
            target_kind = "activity"
            flags["needNewActivity"] = True
        elif "targetKind=filler" in raw:
            target_kind = "filler"
        elif "targetKind=route" in raw:
            target_kind = "route"
            flags["needRouteRefresh"] = True
            flags["modifyDistance"] = True
        elif "targetKind=whole_plan" in raw or "【整体大改上下文】" in raw:
            target_kind = "whole_plan"
            flags["needNewActivity"] = True
            flags["needNewRestaurant"] = True
            flags["needRouteRefresh"] = True
        flags["needReschedule"] = True
        patch = _constraints_patch(raw)
        dinner_route_replan = target_kind == "route" and _has_any(
            raw,
            (
                "去晚饭地点",
                "晚饭地点",
                "去吃饭",
                "去餐厅",
                "饭店",
                "餐厅",
                "烤肉",
                "烧烤",
                "大排档",
                "吃饭",
            ),
        )
        if dinner_route_replan:
            flags["needNewRestaurant"] = True
            flags["needRouteRefresh"] = True
            patch["dinnerRouteReplan"] = True
        locks = _locks_from_text(raw, current_plan)
        if target_kind in {"restaurant", "filler", "route"}:
            activity_id = _selected_poi_id(current_plan, "activity")
            if activity_id:
                locks["activityPoiId"] = activity_id
        if target_kind in {"activity", "filler", "route"} and not flags.get("needNewRestaurant"):
            restaurant_id = _selected_poi_id(current_plan, "restaurant")
            if restaurant_id:
                locks["restaurantPoiId"] = restaurant_id
        if patch.get("forbidLongBuffer"):
            flags["needNewActivity"] = True
            flags["needRouteRefresh"] = True
            locks.pop("activityPoiId", None)
        return {
            "mode": "refine",
            "actionFlags": flags,
            "locks": locks if target_kind != "whole_plan" else {"timeFlexMinutes": 30},
            "constraintsPatch": patch,
            "fallbackMode": "none",
            "clarificationQuestion": None,
            "rawInput": raw,
        }

    if (session or {}).get("pendingRefinement") and not _has_any(raw, ("取消", "算了", "不用")):
        flags["needReschedule"] = True
        return {
            "mode": "refine",
            "actionFlags": flags,
            "locks": _locks_from_text(raw, current_plan),
            "constraintsPatch": {"usePendingRefinement": True},
            "fallbackMode": "none",
            "clarificationQuestion": None,
            "rawInput": raw,
        }

    if _has_any(raw, ("确认", "下单", "就这个", "可以了", "按这个")):
        flags["confirmExecution"] = True
        return {
            "mode": "confirm",
            "actionFlags": flags,
            "locks": _locks_from_text(raw, current_plan),
            "constraintsPatch": {},
            "fallbackMode": "none",
            "clarificationQuestion": None,
            "rawInput": raw,
        }

    if _has_any(raw, ("为什么", "解释", "合理吗", "为啥")):
        flags["needExplanation"] = True
        return {
            "mode": "explain",
            "actionFlags": flags,
            "locks": _locks_from_text(raw, current_plan),
            "constraintsPatch": {},
            "fallbackMode": "none",
            "clarificationQuestion": None,
            "rawInput": raw,
        }

    activity_terms = ("活动", "电影", "影院", "景点", "项目", "玩的", "玩", "票", "太贵")
    restaurant_terms = ("餐厅", "饭店", "烤肉", "火锅", "清淡", "低脂", "不油腻", "排队", "便宜")
    route_terms = ("远", "近", "少走路", "路线", "地铁", "打车", "不想走", "早一点", "早点", "提前", "先吃")
    filler_terms = ("空窗", "缓冲", "等位", "时间段", "这段", "加点", "加些", "加一个", "奶茶", "茶饮", "休息")
    budget_terms = ("太贵", "便宜", "预算", "人均", "省钱", "压预算")
    preserve_activity = _has_any(raw, ("活动别换", "电影别换", "玩的别换", "项目别换", "保留活动", "活动不变"))
    preserve_restaurant = _has_any(raw, ("餐厅别换", "吃饭别换", "饭店别换", "保留餐厅", "餐厅不变"))
    skip_activity = _has_any(raw, ("只吃饭", "只安排吃饭", "不安排活动", "不要活动", "不玩了", "不安排项目"))

    if skip_activity:
        flags["needNewRestaurant"] = not preserve_restaurant
        flags["needRouteRefresh"] = True
        flags["needReschedule"] = True
    elif not preserve_activity and _has_any(raw, activity_terms) and ("餐厅" not in raw or _has_any(raw, ("电影", "活动", "景点", "票", "玩的"))):
        flags["needNewActivity"] = True
        flags["needReschedule"] = True
    if not preserve_restaurant and _has_any(raw, restaurant_terms):
        flags["needNewRestaurant"] = True
        flags["needReschedule"] = True
    if _has_any(raw, route_terms):
        flags["modifyDistance"] = True
        flags["needRouteRefresh"] = True
        flags["needReschedule"] = True
    if _has_any(raw, filler_terms):
        flags["needReschedule"] = True
        if _complains_long_buffer(raw):
            flags["needNewActivity"] = not preserve_activity
            flags["needRouteRefresh"] = True
    elif _complains_long_buffer(raw):
        flags["needNewActivity"] = not preserve_activity
        flags["needRouteRefresh"] = True
        flags["needReschedule"] = True
    if _has_any(raw, ("晚饭早一点", "晚餐早一点", "吃饭早一点", "早点吃", "早些吃", "提前吃", "先吃")):
        flags["needNewRestaurant"] = not preserve_restaurant
        flags["needReschedule"] = True
    if _has_any(raw, budget_terms):
        flags["modifyBudget"] = True
        flags["needReschedule"] = True
        if not preserve_activity and ("电影" in raw or "票" in raw):
            flags["needNewActivity"] = True
        if not preserve_restaurant and ("餐厅" in raw or "吃饭" in raw or "烤肉" in raw or "饭店" in raw):
            flags["needNewRestaurant"] = True
    if "换" in raw and not (flags["needNewActivity"] or flags["needNewRestaurant"]):
        flags["needNewActivity"] = True
        flags["needReschedule"] = True

    mode = "refine" if any(flags.values()) else "new_plan"
    result = {
        "mode": mode,
        "actionFlags": flags,
        "locks": _locks_from_text(raw, current_plan),
        "constraintsPatch": _constraints_patch(raw),
        "fallbackMode": "none",
        "clarificationQuestion": None,
        "rawInput": raw,
    }
    if _has_any(raw, filler_terms):
        result["targetKind"] = "filler"
    if mode == "refine":
        if not flags["needNewActivity"]:
            activity_id = _selected_poi_id(current_plan, "activity")
            if activity_id:
                result["locks"]["activityPoiId"] = activity_id
        if not flags["needNewRestaurant"]:
            restaurant_id = _selected_poi_id(current_plan, "restaurant")
            if restaurant_id:
                result["locks"]["restaurantPoiId"] = restaurant_id
    return _validate_route(result, has_session)
