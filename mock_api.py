"""
FlowCity Stage 3 - Mock API

Goal:
Structured demand JSON -> local mock supply data -> filtered and ranked candidates.

This module does not call any LLM or real Meituan API. It reads Flowcity/data/*.json
and simulates local-life tool calls for activities, restaurants, routes,
availability, and deals.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
EXAMPLES_PATH = ROOT / "examples.json"
RUNTIME_STATUS_PATH = DATA_DIR / "mock_runtime_status.json"

QUEUE_LIMIT_NORMAL = 40
QUEUE_LIMIT_STRICT = 30
DEFAULT_CITY = "西安"
LOW_COST_ACTIVITY_LIMIT = 40
LOW_COST_RESTAURANT_LIMIT = 40

LOW_COST_KEYWORDS = ["不想花钱", "不花钱", "少花钱", "低成本", "省钱", "便宜", "预算少", "预算越低"]
FREE_PREFERENCE_KEYWORDS = ["优先免费", "最好免费", "尽量免费", "免费公共空间", "免费活动"]
FREE_REQUIRED_KEYWORDS = [
    "预算0",
    "零预算",
    "一分钱都不能花",
    "必须免费",
    "只能免费",
    "只要免费",
    "完全免费",
]

DIRECTED_ACTIVITY_ALIASES = {
    "滑雪": ["滑雪", "雪场", "滑雪场", "冰雪"],
    "酒吧": ["酒吧", "bar"],
    "展览": ["展览", "看展", "展馆", "展厅"],
    "电影": ["电影", "看电影", "电影票", "影院", "影城"],
}

AREA_LABELS = {
    "area_xa_xiaozhai": "小寨",
    "area_xa_qujiang": "曲江",
    "area_xa_zhonglou": "钟楼",
    "area_xa_gaoxin": "高新",
    "area_xa_daminggong": "大明宫",
    "area_xa_xingzheng": "行政中心",
    "origin_xianyang_downtown": "咸阳秦都",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_mock_data(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    """Load all stage-3 mock data files."""
    return {
        "areas": load_json(data_dir / "mock_areas.json")["areas"],
        "activities": load_json(data_dir / "mock_activities.json")["activities"],
        "restaurants": load_json(data_dir / "mock_restaurants.json")["restaurants"],
        "routes": load_json(data_dir / "mock_routes.json")["routes"],
        "activityAvailability": load_json(data_dir / "mock_availability.json")[
            "activityAvailability"
        ],
        "restaurantAvailability": load_json(data_dir / "mock_availability.json")[
            "restaurantAvailability"
        ],
        "deals": load_json(data_dir / "mock_deals.json")["deals"],
    }


def load_runtime_status(path: Path = RUNTIME_STATUS_PATH) -> dict[str, Any]:
    """Load stage-6 runtime status pool used only at confirmation time."""
    if not path.exists():
        return {
            "activityRuntimeStatus": [],
            "restaurantRuntimeStatus": [],
            "routeRuntimeStatus": [],
            "dealRuntimeStatus": [],
        }
    return load_json(path)


def _runtime_records_by_key(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {record[key]: record for record in records if record.get(key)}


def find_runtime_activity_status(
    poi_id: str,
    date_text: str | None = None,
    runtime_status: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    runtime_status = runtime_status or load_runtime_status()
    for status in runtime_status.get("activityRuntimeStatus", []):
        if status.get("poiId") != poi_id:
            continue
        if _date_matches(status.get("dateText"), date_text):
            return status
    return None


def find_runtime_restaurant_status(
    poi_id: str,
    date_text: str | None = None,
    runtime_status: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    runtime_status = runtime_status or load_runtime_status()
    for status in runtime_status.get("restaurantRuntimeStatus", []):
        if status.get("poiId") != poi_id:
            continue
        if _date_matches(status.get("dateText"), date_text):
            return status
    return None


def find_runtime_route_status(
    route_ref: str,
    runtime_status: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    runtime_status = runtime_status or load_runtime_status()
    return _runtime_records_by_key(runtime_status.get("routeRuntimeStatus", []), "routeRef").get(route_ref)


def find_runtime_deal_status(
    deal_id: str,
    runtime_status: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    runtime_status = runtime_status or load_runtime_status()
    return _runtime_records_by_key(runtime_status.get("dealRuntimeStatus", []), "dealId").get(deal_id)


def _area_by_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {area["areaId"]: area for area in data["areas"]}


def _parse_minutes(value: str | None) -> int | None:
    if not value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _parse_time_range(value: str) -> tuple[int, int]:
    start, end = value.split("-", 1)
    start_minutes = _parse_minutes(start)
    end_minutes = _parse_minutes(end)
    if start_minutes is None or end_minutes is None:
        raise ValueError(f"Invalid time range: {value}")
    if end_minutes <= start_minutes:
        end_minutes += 24 * 60
    return start_minutes, end_minutes


def _ranges_overlap(
    first_start: int | None,
    first_end: int | None,
    second_start: int,
    second_end: int,
) -> bool:
    if first_start is None or first_end is None:
        return True
    if first_end <= first_start:
        first_end += 24 * 60
    return max(first_start, second_start) < min(first_end, second_end)


def _date_text(demand: dict[str, Any]) -> str | None:
    return demand.get("timeWindow", {}).get("dateText")


def _is_weekend(date_text: str | None) -> bool:
    return bool(date_text and ("周六" in date_text or "周日" in date_text or "周末" in date_text))


def _open_range_for_demand(poi: dict[str, Any], demand: dict[str, Any]) -> tuple[int, int]:
    hours_key = "weekend" if _is_weekend(_date_text(demand)) else "weekday"
    return _parse_time_range(poi["openHours"][hours_key])


def _demand_time_range(demand: dict[str, Any]) -> tuple[int | None, int | None]:
    time_window = demand.get("timeWindow", {})
    return _parse_minutes(time_window.get("startTime")), _parse_minutes(time_window.get("endTime"))


def _is_open_during_demand(poi: dict[str, Any], demand: dict[str, Any]) -> bool:
    demand_start, demand_end = _demand_time_range(demand)
    open_start, open_end = _open_range_for_demand(poi, demand)
    return _ranges_overlap(demand_start, demand_end, open_start, open_end)


def _date_matches(status_date: str | None, demand_date: str | None) -> bool:
    if not status_date or not demand_date:
        return True
    status_aliases = _date_aliases(status_date)
    demand_aliases = _date_aliases(demand_date)
    return bool(status_aliases & demand_aliases) or status_date in demand_date or demand_date in status_date


def _date_aliases(date_text: str) -> set[str]:
    aliases = {date_text}
    if any(value in date_text for value in ("周日", "周天", "星期日", "星期天", "礼拜日", "礼拜天")):
        aliases.update({"周日", "周天", "星期日", "星期天", "礼拜日", "礼拜天"})
    if any(value in date_text for value in ("周六", "星期六", "礼拜六")):
        aliases.update({"周六", "星期六", "礼拜六"})
    if "今晚" in date_text:
        aliases.add("今晚")
    if "周末" in date_text:
        aliases.update({"周六", "周日", "周天", "周末"})
    return aliases


def _time_slot_matches(slot: dict[str, Any], demand: dict[str, Any]) -> bool:
    demand_start, demand_end = _demand_time_range(demand)
    slot_start = _parse_minutes(slot.get("start"))
    slot_end = _parse_minutes(slot.get("end"))
    if slot_start is None or slot_end is None:
        return False
    return _ranges_overlap(demand_start, demand_end, slot_start, slot_end)


def _all_tags(demand: dict[str, Any]) -> list[str]:
    preferences = demand.get("preferences", {})
    scene_tags = demand.get("scene", {}).get("tags", [])
    values: list[str] = []
    for key in ("activityTypes", "foodTags", "experienceTags", "avoidTags"):
        values.extend(preferences.get(key, []))
    values.extend(scene_tags)
    return [str(value) for value in values if value]


def _has_any_text(demand: dict[str, Any], keywords: list[str]) -> bool:
    """Search only structured fields, not rawInput, to keep Stage 3 deterministic."""
    haystack = " ".join(
        [
            " ".join(_all_tags(demand)),
            " ".join(demand.get("constraints", {}).get("soft", [])),
            " ".join(demand.get("constraints", {}).get("hard", [])),
        ]
    )
    return any(keyword in haystack for keyword in keywords)


def _raw_and_structured_text(demand: dict[str, Any]) -> str:
    return " ".join(
        [
            str(demand.get("rawInput") or ""),
            " ".join(_all_tags(demand)),
            " ".join(str(item) for item in demand.get("constraints", {}).get("soft", [])),
            " ".join(str(item) for item in demand.get("constraints", {}).get("hard", [])),
        ]
    )


def _avoid_terms(demand: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for tag in demand.get("preferences", {}).get("avoidTags", []):
        text = str(tag)
        if text.startswith("避开:"):
            terms.append(text.split(":", 1)[1])
    text = _raw_and_structured_text(demand)
    for keyword in ("大明宫", "小寨", "钟楼", "曲江", "高新", "行政中心"):
        if any(prefix + keyword in text for prefix in ("不想去", "不要", "别去", "避开")):
            terms.append(keyword)
    deduped: list[str] = []
    for term in terms:
        clean = term.strip()
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped


def _matches_avoid_term(poi: dict[str, Any], area: dict[str, Any], term: str) -> bool:
    searchable = " ".join(
        [
            poi.get("name", ""),
            poi.get("category", ""),
            poi.get("cuisine", ""),
            area.get("name", ""),
            area.get("district", ""),
            " ".join(poi.get("tags", [])),
            " ".join(area.get("landmarks", [])),
        ]
    )
    return bool(term and term in searchable)


def _seasonally_unsuitable(poi: dict[str, Any], demand: dict[str, Any]) -> bool:
    text = _raw_and_structured_text(demand)
    if "春" in text:
        return False
    searchable = " ".join([poi.get("name", ""), " ".join(poi.get("tags", [])), str(poi.get("mockBasis", ""))])
    return any(keyword in searchable for keyword in ("风筝", "春季限定", "春天限定"))


def _children_ages(demand: dict[str, Any]) -> list[int]:
    ages: list[int] = []
    for child in demand.get("people", {}).get("children", []):
        age = child.get("age")
        if isinstance(age, int):
            ages.append(age)
    return ages


def _people_total(demand: dict[str, Any]) -> int:
    people = demand.get("people", {})
    total = people.get("total")
    if isinstance(total, int) and total > 0:
        return total
    adults = people.get("adults") if isinstance(people.get("adults"), int) else 0
    return max(1, adults + len(people.get("children", [])) + len(people.get("seniors", [])))


def _budget_max(demand: dict[str, Any]) -> float | None:
    budget = demand.get("budget", {})
    max_total = budget.get("maxTotal")
    if isinstance(max_total, (int, float)) and max_total > 0:
        return float(max_total)
    per_person = budget.get("perPerson")
    if isinstance(per_person, (int, float)) and per_person > 0:
        return float(per_person) * _people_total(demand)
    return None


def _budget_mode(demand: dict[str, Any]) -> str:
    budget = demand.get("budget", {})
    max_total = budget.get("maxTotal")
    per_person = budget.get("perPerson")
    if max_total == 0 or per_person == 0:
        return "free_required"
    if _has_any_text(demand, FREE_REQUIRED_KEYWORDS):
        return "free_required"
    if _has_any_text(demand, FREE_PREFERENCE_KEYWORDS):
        return "free_preferred"
    if _has_any_text(demand, LOW_COST_KEYWORDS):
        return "low_cost_preferred"
    if isinstance(max_total, (int, float)) or isinstance(per_person, (int, float)):
        return "strict_amount"
    return "unknown"


def _directed_activity_types(demand: dict[str, Any]) -> list[str]:
    preferences = demand.get("preferences", {})
    hard_constraints = demand.get("constraints", {}).get("hard", [])
    structured_values = [str(value) for value in preferences.get("activityTypes", [])]
    structured_values.extend(str(value) for value in hard_constraints)
    haystack = " ".join(structured_values)

    directed: list[str] = []
    for canonical, aliases in DIRECTED_ACTIVITY_ALIASES.items():
        if any(alias in haystack for alias in aliases):
            directed.append(canonical)
    return directed


def _activity_matches_directed(activity: dict[str, Any], directed_types: list[str]) -> bool:
    if not directed_types:
        return True
    searchable = " ".join(
        [
            activity.get("name", ""),
            activity.get("category", ""),
            " ".join(activity.get("tags", [])),
        ]
    ).lower()
    for directed_type in directed_types:
        aliases = DIRECTED_ACTIVITY_ALIASES.get(directed_type, [directed_type])
        if any(alias.lower() in searchable for alias in aliases):
            return True
    return False


def _is_xianyang_to_xian(demand: dict[str, Any]) -> bool:
    location = demand.get("location", {})
    cross_city = location.get("crossCityIntent", {})
    if not cross_city.get("enabled"):
        return False
    from_city = str(cross_city.get("fromCity") or "")
    to_city = str(cross_city.get("toCity") or "")
    return "咸阳" in from_city and "西安" in to_city


def _queue_limit(demand: dict[str, Any]) -> int:
    if _has_any_text(demand, ["少排队", "排队久", "带孩子", "亲子"]):
        return QUEUE_LIMIT_STRICT
    return QUEUE_LIMIT_NORMAL


def _preferred_area_ids(demand: dict[str, Any], data: dict[str, Any]) -> set[str]:
    location = demand.get("location", {})
    preferred_texts = [
        value
        for value in (location.get("preferredArea"), location.get("startPoint"))
        if value
    ]
    preferred_texts.extend(
        origin.get("point")
        for origin in location.get("originPoints", [])
        if isinstance(origin, dict) and origin.get("point")
    )
    if not preferred_texts:
        return set()
    matched: set[str] = set()
    for area in data["areas"]:
        searchable = " ".join([area["name"], area["district"], *area.get("landmarks", [])])
        for preferred_text in preferred_texts:
            if preferred_text in searchable or any(part in searchable for part in preferred_text.split()):
                matched.add(area["areaId"])
                break
    return matched


def _target_preferred_area_ids(demand: dict[str, Any], data: dict[str, Any]) -> set[str]:
    preferred_area = demand.get("location", {}).get("preferredArea")
    if not preferred_area:
        return set()
    preferred_text = str(preferred_area)
    matched: set[str] = set()
    for area in data["areas"]:
        tokens = [area["name"], area["district"], *area.get("landmarks", [])]
        searchable = " ".join(tokens)
        token_hit = any(token and (token in preferred_text or preferred_text in token) for token in tokens)
        if preferred_text in searchable or token_hit or any(part and part in searchable for part in preferred_text.split()):
            matched.add(area["areaId"])
    return matched


def _availability_for_activity(
    activity_id: str, demand: dict[str, Any], data: dict[str, Any]
) -> dict[str, Any] | None:
    demand_date = _date_text(demand)
    for status in data["activityAvailability"]:
        if status.get("poiId") != activity_id:
            continue
        if not _date_matches(status.get("dateText"), demand_date):
            continue
        matching_slots = [
            slot for slot in status.get("timeSlots", []) if _time_slot_matches(slot, demand)
        ]
        if matching_slots:
            return {
                "dateText": status.get("dateText"),
                "timeSlots": matching_slots,
                "bestTicketLeft": max(slot.get("ticketLeft", 0) for slot in matching_slots),
                "minQueueMinutes": min(slot.get("queueMinutes", 0) for slot in matching_slots),
                "worstCrowdLevel": _worst_crowd_level(
                    [slot.get("crowdLevel", "unknown") for slot in matching_slots]
                ),
            }
    return None


def _availability_for_restaurant(
    restaurant_id: str, demand: dict[str, Any], data: dict[str, Any]
) -> dict[str, Any] | None:
    demand_date = _date_text(demand)
    for status in data["restaurantAvailability"]:
        if status.get("poiId") != restaurant_id:
            continue
        if _date_matches(status.get("dateText"), demand_date):
            return status
    return None


def _worst_crowd_level(levels: list[str]) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return max(levels or ["unknown"], key=lambda level: order.get(level, -1))


def _matched_tags(poi_tags: list[str], demand_tags: list[str]) -> list[str]:
    matches: list[str] = []
    for demand_tag in demand_tags:
        for poi_tag in poi_tags:
            if demand_tag and (demand_tag in poi_tag or poi_tag in demand_tag):
                if poi_tag not in matches:
                    matches.append(poi_tag)
    return matches


def check_deals(poi_id: str, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = data or load_mock_data()
    return [deal for deal in data["deals"] if deal.get("poiId") == poi_id and deal.get("stockLeft", 0) > 0]


def check_activity_availability(
    activity_id: str,
    date_text: str | None,
    time_window: dict[str, Any],
    data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    data = data or load_mock_data()
    demand = {"timeWindow": {"dateText": date_text, **time_window}}
    return _availability_for_activity(activity_id, demand, data)


def check_restaurant_availability(
    restaurant_id: str,
    date_text: str | None,
    time_window: dict[str, Any],
    data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    data = data or load_mock_data()
    demand = {"timeWindow": {"dateText": date_text, **time_window}}
    return _availability_for_restaurant(restaurant_id, demand, data)


def search_activities(
    structured_demand: dict[str, Any], data: dict[str, Any] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return activity candidates, filtered-out records, and tool logs."""
    data = data or load_mock_data()
    areas = _area_by_id(data)
    demand_tags = _all_tags(structured_demand)
    children_ages = _children_ages(structured_demand)
    people_total = _people_total(structured_demand)
    max_budget = _budget_max(structured_demand)
    budget_mode = _budget_mode(structured_demand)
    queue_limit = _queue_limit(structured_demand)
    preferred_areas = _preferred_area_ids(structured_demand, data)
    target_preferred_areas = _target_preferred_area_ids(structured_demand, data)
    wants_near = _has_any_text(structured_demand, ["别太远", "附近", "近", "少走路"])
    directed_types = _directed_activity_types(structured_demand)
    avoid_terms = _avoid_terms(structured_demand)
    wants_scenic = any(keyword in _raw_and_structured_text(structured_demand) for keyword in ("想玩", "我要玩", "景点", "逛一下", "城市景点"))
    requires_indoor = any(keyword in _raw_and_structured_text(structured_demand) for keyword in ("室内", "下雨", "雨天"))

    candidates: list[dict[str, Any]] = []
    filtered_out: list[dict[str, Any]] = []
    logs = [
        {
            "tool": "search_activities",
            "action": "load_activity_pois",
            "outputCount": len(data["activities"]),
        }
    ]

    for activity in data["activities"]:
        reasons: list[str] = []
        area = areas[activity["areaId"]]

        avoided = next((term for term in avoid_terms if _matches_avoid_term(activity, area, term)), None)
        if avoided:
            filtered_out.append(_filtered(activity, "activity", f"用户明确说避开 {avoided}"))
            continue

        if _seasonally_unsuitable(activity, structured_demand):
            filtered_out.append(_filtered(activity, "activity", "该活动偏季节性，当前按全年可用体验过滤"))
            continue

        if requires_indoor and activity.get("indoorOutdoor") == "outdoor":
            filtered_out.append(_filtered(activity, "activity", "用户需要室内/雨天友好活动，过滤户外候选"))
            continue

        if not _activity_matches_directed(activity, directed_types):
            filtered_out.append(
                _filtered(
                    activity,
                    "activity",
                    f"不匹配定向活动硬约束：{'、'.join(directed_types)}",
                )
            )
            continue

        if not _is_open_during_demand(activity, structured_demand):
            filtered_out.append(
                _filtered(activity, "activity", "目标时间与活动营业时间不重叠")
            )
            continue

        age_failed = False
        for age in children_ages:
            if not activity["ageMin"] <= age <= activity["ageMax"]:
                filtered_out.append(
                    _filtered(activity, "activity", f"{age} 岁儿童不在适龄范围 {activity['ageMin']}-{activity['ageMax']} 岁")
                )
                age_failed = True
                break
        if age_failed:
            continue
        if children_ages:
            reasons.append(f"适合 {min(children_ages)} 岁儿童")

        availability = _availability_for_activity(activity["id"], structured_demand, data)
        if not availability:
            filtered_out.append(_filtered(activity, "activity", "没有匹配目标日期和时间的余票状态"))
            continue
        if availability["bestTicketLeft"] <= 0:
            filtered_out.append(_filtered(activity, "activity", "目标时间段余票为 0"))
            continue
        if availability["bestTicketLeft"] < people_total:
            filtered_out.append(
                _filtered(
                    activity,
                    "activity",
                    f"目标时间段余票 {availability['bestTicketLeft']} 张，少于同行人数 {people_total} 人",
                )
            )
            continue
        if availability["minQueueMinutes"] > queue_limit:
            filtered_out.append(
                _filtered(activity, "activity", f"排队 {availability['minQueueMinutes']} 分钟，超过阈值 {queue_limit} 分钟")
            )
            continue

        estimated_cost = activity["pricePerPerson"] * people_total
        if budget_mode == "free_required" and estimated_cost > 0:
            filtered_out.append(
                _filtered(activity, "activity", f"活动预估费用 {estimated_cost:.0f} 元，不满足必须免费/预算 0")
            )
            continue
        if max_budget is not None and estimated_cost > max_budget:
            filtered_out.append(
                _filtered(activity, "activity", f"活动预估费用 {estimated_cost:.0f} 元超过预算 {max_budget:.0f} 元")
            )
            continue

        matched_tags = _matched_tags(activity.get("tags", []), demand_tags)
        score = 0
        score += len(matched_tags) * 2
        if children_ages and "儿童友好" in activity.get("tags", []):
            score += 2
        if availability["minQueueMinutes"] <= 15:
            score += 1
            reasons.append("排队较短")
        if target_preferred_areas and activity["areaId"] in target_preferred_areas:
            score += 8
            reasons.append("匹配目标商圈")
        elif target_preferred_areas and activity["areaId"] not in target_preferred_areas:
            score -= 6
        elif preferred_areas and activity["areaId"] in preferred_areas:
            score += 1
            reasons.append("匹配偏好商圈")
        elif wants_near and activity["areaId"] in {"area_xa_xiaozhai", "area_xa_xingzheng", "area_xa_daminggong"}:
            score += 1
            reasons.append("适合作为近距离/低折腾候选")
        if activity.get("baseRating", 0) >= 4.5:
            score += 1
            reasons.append("基础评分较高")
        if max_budget is not None and estimated_cost <= max_budget * 0.6:
            score += 1
            reasons.append("预算友好")
        if budget_mode in {"free_required", "free_preferred", "low_cost_preferred"}:
            if activity["pricePerPerson"] == 0:
                score += 8 if budget_mode in {"free_required", "free_preferred"} else 5
                reasons.append("免费活动")
            elif activity["pricePerPerson"] <= LOW_COST_ACTIVITY_LIMIT:
                score += 3
                reasons.append("低消费活动")
            elif "低预算" in activity.get("tags", []):
                score += 2
                reasons.append("低预算标签")
        if wants_scenic and any(tag in activity.get("tags", []) for tag in ("地标", "citywalk", "文旅", "夜游", "大雁塔", "大唐不夜城", "城墙", "钟楼")):
            score += 7
            reasons.append("明确可逛景点/城市地标")
        reasons.extend([f"命中标签：{tag}" for tag in matched_tags])

        candidates.append(
            {
                "poiId": activity["id"],
                "name": activity["name"],
                "kind": "activity",
                "areaId": activity["areaId"],
                "areaName": area["name"],
                "category": activity["category"],
                "score": score,
                "matchedReasons": reasons,
                "estimatedCost": estimated_cost,
                "availability": availability,
                "deals": check_deals(activity["id"], data),
                "source": "mock_activities.json",
            }
        )

    candidates.sort(key=lambda item: (-item["score"], item["estimatedCost"], item["name"]))
    logs.append(
        {
            "tool": "search_activities",
            "action": "hard_filter_and_rank",
            "outputCount": len(candidates),
            "filteredCount": len(filtered_out),
        }
    )
    return candidates, filtered_out, logs


def search_restaurants(
    structured_demand: dict[str, Any], data: dict[str, Any] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return restaurant candidates, filtered-out records, and tool logs."""
    data = data or load_mock_data()
    areas = _area_by_id(data)
    demand_tags = _all_tags(structured_demand)
    people_total = _people_total(structured_demand)
    max_budget = _budget_max(structured_demand)
    budget_mode = _budget_mode(structured_demand)
    queue_limit = _queue_limit(structured_demand)
    preferred_areas = _preferred_area_ids(structured_demand, data)
    target_preferred_areas = _target_preferred_area_ids(structured_demand, data)
    wants_low_fat = _has_any_text(structured_demand, ["低脂", "清淡", "减肥", "少油"])
    wants_reservation = _has_any_text(structured_demand, ["订座", "预约", "可订"])
    has_children = bool(_children_ages(structured_demand))
    avoid_terms = _avoid_terms(structured_demand)

    candidates: list[dict[str, Any]] = []
    filtered_out: list[dict[str, Any]] = []
    logs = [
        {
            "tool": "search_restaurants",
            "action": "load_restaurant_pois",
            "outputCount": len(data["restaurants"]),
        }
    ]

    for restaurant in data["restaurants"]:
        reasons: list[str] = []
        area = areas[restaurant["areaId"]]

        avoided = next((term for term in avoid_terms if _matches_avoid_term(restaurant, area, term)), None)
        if avoided:
            filtered_out.append(_filtered(restaurant, "restaurant", f"用户明确说避开 {avoided}"))
            continue

        if not _is_open_during_demand(restaurant, structured_demand):
            filtered_out.append(
                _filtered(restaurant, "restaurant", "目标时间与餐厅营业时间不重叠")
            )
            continue

        if wants_low_fat and not restaurant.get("lowFatOptions"):
            filtered_out.append(_filtered(restaurant, "restaurant", "缺少低脂/清淡选项"))
            continue
        if has_children and not restaurant.get("childFriendly"):
            filtered_out.append(_filtered(restaurant, "restaurant", "亲子场景下儿童友好度不足"))
            continue
        if wants_reservation and not restaurant.get("reservable"):
            filtered_out.append(_filtered(restaurant, "restaurant", "用户希望订座，但餐厅不可预约"))
            continue

        availability = _availability_for_restaurant(restaurant["id"], structured_demand, data)
        if not availability:
            filtered_out.append(_filtered(restaurant, "restaurant", "没有匹配目标日期的餐厅动态状态"))
            continue
        if not availability.get("tableAvailable"):
            filtered_out.append(_filtered(restaurant, "restaurant", "目标日期暂无可用座位"))
            continue
        if availability.get("queueMinutes", 0) > queue_limit:
            filtered_out.append(
                _filtered(restaurant, "restaurant", f"排队 {availability['queueMinutes']} 分钟，超过阈值 {queue_limit} 分钟")
            )
            continue

        estimated_cost = restaurant["avgPricePerPerson"] * people_total
        if budget_mode == "free_required" and estimated_cost > 0:
            filtered_out.append(
                _filtered(restaurant, "restaurant", f"餐饮预估费用 {estimated_cost:.0f} 元，不满足必须免费/预算 0")
            )
            continue
        if max_budget is not None and estimated_cost > max_budget:
            filtered_out.append(
                _filtered(restaurant, "restaurant", f"餐饮预估费用 {estimated_cost:.0f} 元超过预算 {max_budget:.0f} 元")
            )
            continue

        matched_tags = _matched_tags(restaurant.get("tags", []), demand_tags)
        score = 0
        score += len(matched_tags) * 2
        if has_children and restaurant.get("childFriendly"):
            score += 2
            reasons.append("儿童友好")
        if wants_low_fat and restaurant.get("lowFatOptions"):
            score += 2
            reasons.append("提供低脂/清淡选项")
        if restaurant.get("reservable"):
            score += 1
            reasons.append("支持预约")
        if availability.get("queueMinutes", 0) <= 15:
            score += 1
            reasons.append("排队较短")
        if target_preferred_areas and restaurant["areaId"] in target_preferred_areas:
            score += 8
            reasons.append("匹配目标商圈")
        elif target_preferred_areas and restaurant["areaId"] not in target_preferred_areas:
            score -= 6
        elif preferred_areas and restaurant["areaId"] in preferred_areas:
            score += 1
            reasons.append("匹配偏好商圈")
        if restaurant.get("baseRating", 0) >= 4.5:
            score += 1
            reasons.append("基础评分较高")
        if max_budget is not None and estimated_cost <= max_budget * 0.6:
            score += 1
            reasons.append("预算友好")
        if budget_mode in {"free_preferred", "low_cost_preferred"}:
            if restaurant["avgPricePerPerson"] <= LOW_COST_RESTAURANT_LIMIT:
                score += 3
                reasons.append("低消费餐饮候选")
            elif "低预算" in restaurant.get("tags", []):
                score += 2
                reasons.append("低预算标签")
        reasons.extend([f"命中标签：{tag}" for tag in matched_tags])

        candidates.append(
            {
                "poiId": restaurant["id"],
                "name": restaurant["name"],
                "kind": "restaurant",
                "areaId": restaurant["areaId"],
                "areaName": area["name"],
                "cuisine": restaurant["cuisine"],
                "score": score,
                "matchedReasons": reasons,
                "estimatedCost": estimated_cost,
                "availability": availability,
                "deals": check_deals(restaurant["id"], data),
                "source": "mock_restaurants.json",
            }
        )

    candidates.sort(key=lambda item: (-item["score"], item["estimatedCost"], item["name"]))
    logs.append(
        {
            "tool": "search_restaurants",
            "action": "hard_filter_and_rank",
            "outputCount": len(candidates),
            "filteredCount": len(filtered_out),
        }
    )
    return candidates, filtered_out, logs


def _route_with_cost(route: dict[str, Any], people_total: int) -> dict[str, Any]:
    enriched = dict(route)
    if "routeType" not in enriched:
        enriched["routeType"] = (
            "same_area"
            if route.get("fromAreaId") == route.get("toAreaId")
            else "area_to_area"
        )
    if "estimatedCostPerPerson" not in enriched:
        transport = route.get("transport")
        if transport == "walk":
            enriched["estimatedCostPerPerson"] = 0
        elif transport == "public_transport":
            enriched["estimatedCostPerPerson"] = 4
        else:
            enriched["estimatedCostPerPerson"] = 25
    enriched["estimatedCostTotal"] = enriched["estimatedCostPerPerson"] * people_total
    enriched["isCrossCityInbound"] = enriched["routeType"] == "cross_city_inbound"
    return enriched


def _route_endpoint_name(area_id: str, areas: dict[str, dict[str, Any]]) -> str:
    if area_id == "origin_xianyang_downtown":
        return "咸阳市区"
    return areas.get(area_id, {}).get("name") or AREA_LABELS.get(area_id, area_id)


def _route_summary(route: dict[str, Any], data: dict[str, Any]) -> str:
    areas = _area_by_id(data)
    from_name = _route_endpoint_name(route["fromAreaId"], areas)
    to_name = _route_endpoint_name(route["toAreaId"], areas)
    transport_name = {
        "public_transport": "公共交通",
        "taxi": "打车",
        "walk": "步行",
        "drive": "驾车",
    }.get(str(route.get("transport")), str(route.get("transport") or "交通"))
    return f"{from_name}到{to_name}，{transport_name}约{route['minutes']}分钟"


def _attach_route_costs(
    candidates: list[dict[str, Any]],
    route_candidates: list[dict[str, Any]],
    data: dict[str, Any],
    prefer_cross_city: bool,
) -> list[dict[str, Any]]:
    best_route_by_area: dict[str, dict[str, Any]] = {}
    for route in route_candidates:
        if prefer_cross_city and not route.get("isCrossCityInbound"):
            continue
        to_area = route.get("toAreaId")
        if not to_area:
            continue
        current = best_route_by_area.get(to_area)
        if current is None or (route["minutes"], route["estimatedCostPerPerson"]) < (
            current["minutes"],
            current["estimatedCostPerPerson"],
        ):
            best_route_by_area[to_area] = route

    enriched_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        enriched = dict(candidate)
        route = best_route_by_area.get(candidate["areaId"])
        if route:
            route_cost = route["estimatedCostTotal"]
            enriched["routeSummary"] = _route_summary(route, data)
            enriched["estimatedRouteCost"] = route_cost
            enriched["estimatedTotalCostWithRoute"] = candidate["estimatedCost"] + route_cost
            enriched["routeAdjustedScore"] = round(
                candidate["score"]
                - (route["minutes"] / 45)
                - (route["estimatedCostPerPerson"] / 30),
                3,
            )
        else:
            enriched["routeSummary"] = None
            enriched["estimatedRouteCost"] = 0
            enriched["estimatedTotalCostWithRoute"] = candidate["estimatedCost"]
            enriched["routeAdjustedScore"] = candidate["score"]
        enriched_candidates.append(enriched)

    if prefer_cross_city:
        enriched_candidates.sort(
            key=lambda item: (
                -item["routeAdjustedScore"],
                item["estimatedTotalCostWithRoute"],
                item["name"],
            )
        )
    return enriched_candidates


def search_routes(
    structured_demand: dict[str, Any],
    candidate_area_ids: list[str],
    data: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = data or load_mock_data()
    unique_area_ids = sorted(set(candidate_area_ids))
    preferred = _preferred_area_ids(structured_demand, data)
    people_total = _people_total(structured_demand)
    is_xianyang_to_xian = _is_xianyang_to_xian(structured_demand)
    transport_preference = structured_demand.get("location", {}).get("transportPreference")
    route_candidates: list[dict[str, Any]] = []
    seen_route_keys: set[tuple[str, str, str]] = set()

    def add_route(route: dict[str, Any]) -> None:
        key = (route.get("fromAreaId", ""), route.get("toAreaId", ""), route.get("transport", ""))
        if key in seen_route_keys:
            return
        seen_route_keys.add(key)
        route_candidates.append(_route_with_cost(route, people_total))
        route_candidates[-1]["routeSummary"] = _route_summary(route_candidates[-1], data)

    for route in data["routes"]:
        if route.get("routeType") == "cross_city_inbound":
            if not is_xianyang_to_xian:
                continue
            if route["toAreaId"] not in unique_area_ids:
                continue
            if transport_preference and route.get("transport") != transport_preference:
                continue
            add_route(route)
            continue
        if route["fromAreaId"] == route["toAreaId"] and route["fromAreaId"] in unique_area_ids:
            add_route(route)
            continue
        if route["fromAreaId"] in unique_area_ids and route["toAreaId"] in unique_area_ids:
            add_route(route)
            continue
        if preferred and route["fromAreaId"] in preferred and route["toAreaId"] in unique_area_ids:
            add_route(route)

    route_candidates.sort(
        key=lambda item: (
            0 if item.get("isCrossCityInbound") else 1,
            0 if item.get("routeType") == "origin_to_area" else 1,
            item["minutes"],
            item["distanceKm"],
        )
    )
    logs = [
        {
            "tool": "search_routes",
            "action": "route_time_lookup",
            "inputAreaCount": len(unique_area_ids),
            "outputCount": len(route_candidates),
        }
    ]
    return route_candidates[:40], logs


def _build_supply_status(
    structured_demand: dict[str, Any],
    activity_candidates: list[dict[str, Any]],
    restaurant_candidates: list[dict[str, Any]],
    route_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    failed_constraints: list[dict[str, Any]] = []
    reasons: list[str] = []

    directed_types = _directed_activity_types(structured_demand)
    if directed_types and not activity_candidates:
        for directed_type in directed_types:
            failed_constraints.append(
                {
                    "dimension": "activityType",
                    "value": directed_type,
                    "reason": "Mock 供给池没有匹配该定向活动硬约束的可用活动",
                }
            )
        reasons.append(f"定向活动无供给：{'、'.join(directed_types)}")

    if _is_xianyang_to_xian(structured_demand) and not any(
        route.get("isCrossCityInbound") for route in route_candidates
    ):
        failed_constraints.append(
            {
                "dimension": "crossCityRoute",
                "value": "咸阳到西安",
                "reason": "Mock 路线池没有可用的咸阳入城路线",
            }
        )
        reasons.append("缺少咸阳到西安入城路线")

    if failed_constraints:
        status = "failed"
    elif not activity_candidates or not restaurant_candidates:
        status = "partial"
        if not activity_candidates:
            reasons.append("活动候选为空")
        if not restaurant_candidates:
            reasons.append("餐厅候选为空")
    else:
        status = "ok"
        reasons.append("主要供给候选可用")

    return {
        "status": status,
        "failedConstraints": failed_constraints,
        "reasons": reasons,
    }


def search_supply(structured_demand: dict[str, Any]) -> dict[str, Any]:
    """Top-level stage-3 mock supply search entrypoint."""
    data = load_mock_data()
    prefer_cross_city = _is_xianyang_to_xian(structured_demand)
    activity_candidates, filtered_activities, activity_logs = search_activities(
        structured_demand, data
    )
    restaurant_candidates, filtered_restaurants, restaurant_logs = search_restaurants(
        structured_demand, data
    )
    area_ids = [
        item["areaId"] for item in [*activity_candidates[:10], *restaurant_candidates[:10]]
    ]
    route_candidates, route_logs = search_routes(structured_demand, area_ids, data)
    activity_candidates = _attach_route_costs(
        activity_candidates, route_candidates, data, prefer_cross_city
    )
    restaurant_candidates = _attach_route_costs(
        restaurant_candidates, route_candidates, data, prefer_cross_city
    )
    supply_status = _build_supply_status(
        structured_demand, activity_candidates, restaurant_candidates, route_candidates
    )

    return {
        "city": DEFAULT_CITY,
        "activityCandidates": activity_candidates,
        "restaurantCandidates": restaurant_candidates,
        "routeCandidates": route_candidates,
        "supplyStatus": supply_status,
        "filteredOut": [*filtered_activities, *filtered_restaurants],
        "toolLogs": [
            {"tool": "load_mock_data", "action": "read_local_json", "dataDir": str(DATA_DIR)},
            *activity_logs,
            *restaurant_logs,
            *route_logs,
            {
                "tool": "search_supply",
                "action": "combine_results",
                "activityCandidates": len(activity_candidates),
                "restaurantCandidates": len(restaurant_candidates),
                "routeCandidates": len(route_candidates),
                "filteredOut": len(filtered_activities) + len(filtered_restaurants),
            },
        ],
    }


def _filtered(poi: dict[str, Any], kind: str, reason: str) -> dict[str, Any]:
    return {
        "poiId": poi["id"],
        "name": poi["name"],
        "kind": kind,
        "reason": reason,
    }


def load_example_demand(example_id: str) -> dict[str, Any]:
    examples = load_json(EXAMPLES_PATH)["examples"]
    for example in examples:
        if example.get("id") == example_id:
            return example["expectedStructuredDemand"]
    available = ", ".join(example["id"] for example in examples)
    raise ValueError(f"Unknown example id: {example_id}. Available: {available}")


def load_demand_from_file(path: Path) -> dict[str, Any]:
    data = load_json(path)
    if "expectedStructuredDemand" in data:
        return data["expectedStructuredDemand"]
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="FlowCity Stage 3 mock supply API")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demand", type=Path, help="Path to a structured demand JSON file")
    source.add_argument(
        "--example-id",
        help="Example id from examples.json, e.g. family_half_day, couple_date, friends_citywalk",
    )
    parser.add_argument("--limit", type=int, default=5, help="Limit printed candidates per type")
    args = parser.parse_args()

    demand = load_demand_from_file(args.demand) if args.demand else load_example_demand(args.example_id)
    result = search_supply(demand)
    if args.limit >= 0:
        result["activityCandidates"] = result["activityCandidates"][: args.limit]
        result["restaurantCandidates"] = result["restaurantCandidates"][: args.limit]
        result["routeCandidates"] = result["routeCandidates"][: args.limit]

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
