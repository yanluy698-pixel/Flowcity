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
    "空窗",
    "缓冲",
    "等位",
    "时间段",
    "这段",
    "加点",
    "加些",
    "加一个",
    "奶茶",
    "茶饮",
    "休息",
    "优化",
    "晚饭",
    "晚餐",
    "吃饭",
    "早一点",
    "早点",
    "提前",
    "先吃",
    "为什么",
    "解释",
)


def is_likely_refinement(text: str, has_session: bool) -> bool:
    if not has_session:
        return False
    return any(hint in text for hint in FOLLOW_UP_HINTS) or len(text) <= 28


def _complains_long_buffer(text: str) -> bool:
    return any(
        value in text
        for value in (
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
        )
    )


def parse_refinement_intent(text: str) -> dict[str, Any]:
    operations: list[str] = []
    locked_items: list[str] = []
    changed_items: list[str] = []
    avoid_terms: list[str] = []
    preferred_area: str | None = None
    time_hints: list[str] = []
    meal_timing: str | None = None
    require_activity = False
    skip_activity = False
    fill_buffer = False
    forbid_long_buffer = False

    if any(value in text for value in ("只吃饭", "只安排吃饭", "不安排活动", "不要活动", "不玩了", "不安排项目")):
        operations.append("replace_restaurant")
        changed_items.append("restaurant")
        skip_activity = True
    if any(value in text for value in ("空窗", "缓冲", "等位", "时间段", "这段", "加点", "加些", "加一个", "奶茶", "茶饮", "休息")):
        operations.append("fill_buffer")
        changed_items.append("filler")
        fill_buffer = True
    if _complains_long_buffer(text):
        operations.append("forbid_long_buffer")
        changed_items.append("time_window")
        changed_items.append("activity")
        fill_buffer = True
        forbid_long_buffer = True
    if not skip_activity and any(value in text for value in ("我要玩", "想玩", "景点", "逛一下", "没有什么景点", "自由活动")):
        operations.append("replace_activity")
        changed_items.append("activity")
        require_activity = True
    if any(value in text for value in ("餐厅", "饭店", "烤肉", "火锅", "太贵", "便宜", "低脂", "清淡", "排队")):
        operations.append("replace_restaurant")
        changed_items.append("restaurant")
    if any(value in text for value in ("太贵", "便宜", "预算", "人均")):
        operations.append("tighten_budget")
    if any(value in text for value in ("少走路", "太远", "远一点", "近一点", "地铁", "路线")):
        operations.append("adjust_time_or_route")
    if any(value in text for value in ("几点", "点", "左右", "那会", "先去", "再去", "早一点", "早点", "提前", "先吃")):
        operations.append("adjust_time_or_route")
    if any(value in text for value in ("晚饭早一点", "晚餐早一点", "吃饭早一点", "早点吃", "早些吃", "提前吃", "先吃")):
        meal_timing = "earlier"
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
        "mealTiming": meal_timing,
        "fillBuffer": fill_buffer,
        "forbidLongBuffer": forbid_long_buffer,
        "requireActivity": require_activity,
        "skipActivity": skip_activity,
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

    plan_control = demand.setdefault("planControl", {})
    if intent.get("mealTiming"):
        plan_control["mealTiming"] = intent["mealTiming"]
    if intent.get("skipActivity"):
        plan_control["skipActivity"] = True
        plan_control["requireActivity"] = False
    if intent.get("fillBuffer"):
        plan_control["forceFillerInsert"] = True
        plan_control.setdefault("constraintsPatch", {})["fillBuffer"] = True
    if intent.get("forbidLongBuffer"):
        plan_control["forbidLongBuffer"] = True
        plan_control["mustImprovePreviousIdle"] = True
        patch = plan_control.setdefault("constraintsPatch", {})
        patch["fillBuffer"] = True
        patch["forbidLongBuffer"] = True
        patch["mustImprovePreviousIdle"] = True
        patch["maxIdleMinutes"] = 45
        patch["targetExperienceBlocksMin"] = 2
        policy = demand.setdefault("planningPolicy", {})
        if isinstance(policy, dict):
            policy["maxIdleMinutes"] = min(int(policy.get("maxIdleMinutes") or 45), 45)
            policy["targetExperienceBlocks"] = max(int(policy.get("targetExperienceBlocks") or 0), 2)

    constraints = demand.setdefault("constraints", {})
    hard = constraints.setdefault("hard", [])
    if isinstance(hard, list):
        for term in intent["avoidTerms"]:
            item = f"避开用户明确不想去的地点或商圈：{term}"
            if item not in hard:
                hard.append(item)
        if intent["requireActivity"] and "必须安排明确可玩的景点或活动，不能只给吃饭和自由闲逛" not in hard:
            hard.append("必须安排明确可玩的景点或活动，不能只给吃饭和自由闲逛")
        if intent.get("skipActivity"):
            constraints["hard"] = [
                item
                for item in hard
                if "必须安排明确可玩的景点或活动" not in str(item)
                and "活动必须适合儿童" not in str(item)
            ]
            hard = constraints["hard"]
            item = "二次修改要求只安排餐饮，不再安排活动节点。"
            if item not in hard:
                hard.append(item)
        if intent.get("preferredArea"):
            item = f"二次修改要求优先围绕{intent['preferredArea']}安排。"
            if item not in hard:
                hard.append(item)
        for hint in intent.get("timeHints", []):
            item = f"二次修改提到时间点：{hint}，规划时尽量按该时间附近安排相关节点。"
            if item not in hard:
                hard.append(item)
        if intent.get("mealTiming") == "earlier":
            item = "二次修改要求晚饭/吃饭早一点，调度时优先把餐饮节点提前到可预约的较早时段。"
            if item not in hard:
                hard.append(item)
        if intent.get("fillBuffer"):
            item = "二次修改要求优化空窗/等位时间，优先加入同商圈低成本休息、茶饮或短逛节点。"
            if item not in hard:
                hard.append(item)
        if intent.get("forbidLongBuffer"):
            item = "二次修改明确认为长时间空窗不合理；最大连续无意义等待不得超过45分钟，必要时换活动、加短逛或跨商圈补充体验。"
            if item not in hard:
                hard.append(item)

    return demand, intent
