"""Session-aware refinement intent parsing for FlowCity."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
import re


FOLLOW_UP_HINTS = (
    "换",
    "不要",
    "不想",
    "别去",
    "避开",
    "太贵",
    "便宜",
    "少走路",
    "远",
    "我要玩",
    "想玩",
    "景点",
    "逛一下",
    "为什么",
    "解释",
)


def is_likely_refinement(text: str, has_session: bool) -> bool:
    if not has_session:
        return False
    return any(hint in text for hint in FOLLOW_UP_HINTS) or len(text) <= 28


def parse_refinement_intent(text: str) -> dict[str, Any]:
    operations: list[str] = []
    locked_items: list[str] = []
    changed_items: list[str] = []
    avoid_terms: list[str] = []
    preferred_area: str | None = None
    time_hints: list[str] = []
    require_activity = False

    if any(value in text for value in ("我要玩", "想玩", "景点", "逛一下", "没有什么景点", "自由活动")):
        operations.append("replace_activity")
        changed_items.append("activity")
        require_activity = True
    if any(value in text for value in ("餐厅", "吃饭", "太贵", "便宜", "低脂", "清淡", "排队")):
        operations.append("replace_restaurant")
        changed_items.append("restaurant")
    if any(value in text for value in ("太贵", "便宜", "预算", "人均")):
        operations.append("tighten_budget")
    if any(value in text for value in ("少走路", "太远", "远一点", "近一点", "地铁", "路线")):
        operations.append("adjust_time_or_route")
    if any(value in text for value in ("几点", "点", "左右", "那会", "先去", "再去")):
        operations.append("adjust_time_or_route")
    if any(value in text for value in ("为什么", "解释", "合理吗")):
        operations.append("explain_plan")

    for area in ("大明宫", "小寨", "钟楼", "曲江", "高新", "行政中心"):
        if any(prefix + area in text for prefix in ("不想去", "不要", "别去", "避开")):
            avoid_terms.append(area)
            operations.append("avoid_area_or_poi")
            changed_items.append("area")
        elif area in text:
            preferred_area = area
            operations.append("adjust_time_or_route")
            changed_items.append("area")
    for match in re.finditer(r"([一二三四五六七八九十\d]{1,2})点(?:半|左右|那会|前后)?", text):
        time_hints.append(match.group(0))
    if "换" in text and "replace_activity" not in operations and "replace_restaurant" not in operations:
        operations.append("replace_activity")
        changed_items.append("activity")

    if "activity" not in changed_items:
        locked_items.append("activity")
    if "restaurant" not in changed_items:
        locked_items.append("restaurant")

    deduped_operations = []
    for operation in operations or ["refine_plan"]:
        if operation not in deduped_operations:
            deduped_operations.append(operation)

    return {
        "mode": "refine",
        "operations": deduped_operations,
        "avoidTerms": avoid_terms,
        "preferredArea": preferred_area,
        "timeHints": time_hints,
        "requireActivity": require_activity,
        "lockedItems": locked_items,
        "changedItems": sorted(set(changed_items)),
        "rawFeedback": text,
    }


def apply_refinement(
    previous_demand: dict[str, Any],
    feedback: str,
    previous_plan: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    intent = parse_refinement_intent(feedback)
    demand = deepcopy(previous_demand)
    demand["rawInput"] = "。".join(
        value for value in [str(previous_demand.get("rawInput") or ""), f"用户继续要求：{feedback}"] if value
    )
    preferences = demand.setdefault("preferences", {})
    avoid_tags = preferences.setdefault("avoidTags", [])
    if isinstance(avoid_tags, list):
        for term in intent["avoidTerms"]:
            tag = f"避开:{term}"
            if tag not in avoid_tags:
                avoid_tags.append(tag)
    experience_tags = preferences.setdefault("experienceTags", [])
    if isinstance(experience_tags, list) and intent["requireActivity"] and "明确可玩活动" not in experience_tags:
        experience_tags.append("明确可玩活动")

    if intent.get("preferredArea"):
        location = demand.setdefault("location", {})
        location["preferredArea"] = intent["preferredArea"]

    constraints = demand.setdefault("constraints", {})
    hard = constraints.setdefault("hard", [])
    if isinstance(hard, list):
        for term in intent["avoidTerms"]:
            item = f"避开用户明确不想去的地点或商圈：{term}"
            if item not in hard:
                hard.append(item)
        if intent["requireActivity"] and "必须安排明确可玩的景点或活动，不能只给吃饭和自由闲逛" not in hard:
            hard.append("必须安排明确可玩的景点或活动，不能只给吃饭和自由闲逛")
        if intent.get("preferredArea"):
            item = f"二次修改要求优先围绕{intent['preferredArea']}安排。"
            if item not in hard:
                hard.append(item)
        for hint in intent.get("timeHints", []):
            item = f"二次修改提到时间点：{hint}，规划时尽量按该时间附近安排相关节点。"
            if item not in hard:
                hard.append(item)

    return demand, intent
