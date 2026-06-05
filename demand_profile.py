"""Canonical demand profile for FlowCity.

The LLM may suggest scenes, but recommendation scores only consume facts,
hard constraints, stable dimensions, destination anchors, and open hypotheses.
"""

from __future__ import annotations

import hashlib
import os
import re
from copy import deepcopy
from typing import Any


SCHEMA_VERSION = "demand-profile-v2"

DIMENSION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "physicalIntensity": {"label": "体力强度", "direction": "target"},
    "activityIntensity": {"label": "活动活跃程度", "direction": "target"},
    "interactionLevel": {"label": "互动程度", "direction": "target"},
    "conversationFriendly": {"label": "聊天友好程度", "direction": "target"},
    "noiseLevel": {"label": "噪音水平", "direction": "target"},
    "formality": {"label": "正式程度", "direction": "target"},
    "privacy": {"label": "私密程度", "direction": "target"},
    "novelty": {"label": "新奇程度", "direction": "target"},
    "restAvailability": {"label": "休息便利程度", "direction": "higher"},
    "safety": {"label": "安全程度", "direction": "higher"},
    "familyAccessibility": {"label": "家庭便利程度", "direction": "higher"},
    "weatherResilience": {"label": "天气适应能力", "direction": "higher"},
    "routeConvenience": {"label": "路线便利程度", "direction": "higher"},
    "pricePreference": {"label": "消费倾向", "direction": "target"},
}

SOURCE_WEIGHTS = {
    "explicit": 1.0,
    "llm_inference": 0.65,
    "hypothesis": 0.25,
}

AREA_ALIASES = {
    "小寨": "area_xa_xiaozhai",
    "赛格": "area_xa_xiaozhai",
    "陕西历史博物馆": "area_xa_xiaozhai",
    "曲江": "area_xa_qujiang",
    "大雁塔": "area_xa_qujiang",
    "大唐不夜城": "area_xa_qujiang",
    "大唐芙蓉园": "area_xa_qujiang",
    "曲江大悦城": "area_xa_qujiang",
    "钟楼": "area_xa_zhonglou",
    "鼓楼": "area_xa_zhonglou",
    "回民街": "area_xa_zhonglou",
    "城墙": "area_xa_zhonglou",
    "高新": "area_xa_gaoxin",
    "科技路": "area_xa_gaoxin",
    "行政中心": "area_xa_xingzheng",
    "熙地港": "area_xa_xingzheng",
    "大明宫": "area_xa_daminggong",
    "龙首原": "area_xa_daminggong",
}

PREFERRED_ANCHOR_MARKERS = ("最好", "有时间", "顺路", "可以去", "想顺便")
REQUIRED_ANCHOR_MARKERS = ("必须", "一定", "就去", "专门去", "主要去", "想去", "要去", "去玩", "去逛")

EXPLICIT_DIMENSION_RULES = (
    ("physicalIntensity", 0.2, ("少走路", "别太累", "不累", "走不动", "低体力")),
    ("activityIntensity", 0.8, ("放电", "释放精力", "跑跳", "运动", "痛快玩")),
    ("interactionLevel", 0.8, ("互动", "一起玩", "边玩边聊", "不会冷场", "破冰")),
    ("conversationFriendly", 0.85, ("聊天", "慢聊", "坐下来聊", "不会冷场", "边玩边聊")),
    ("noiseLevel", 0.2, ("安静", "别太吵", "不要太吵")),
    ("formality", 0.25, ("不要太正式", "别太正式", "随意一点", "轻松一点")),
    ("privacy", 0.75, ("私密", "不被打扰", "独处")),
    ("novelty", 0.8, ("新奇", "新鲜", "没体验过", "小众")),
    ("restAvailability", 0.9, ("有座", "能坐", "休息", "少走路")),
    ("safety", 0.95, ("安全", "带孩子", "带娃", "老人", "长辈")),
    ("familyAccessibility", 0.95, ("带孩子", "带娃", "亲子", "老人", "长辈", "老婆")),
    ("weatherResilience", 0.9, ("室内", "下雨", "雨天", "别晒")),
    ("routeConvenience", 0.95, ("附近", "别太远", "近一点", "少折腾", "少转场")),
    ("pricePreference", 0.2, ("省钱", "便宜", "低成本", "少花钱")),
)

INFERRED_DIMENSION_RULES = (
    ("familyAccessibility", 0.9, ("孩子", "带娃", "老人", "父母", "长辈")),
    ("safety", 0.9, ("孩子", "带娃", "老人", "父母", "长辈")),
    ("restAvailability", 0.75, ("老人", "父母", "长辈")),
    ("physicalIntensity", 0.3, ("老人", "父母", "长辈", "减脂")),
    ("interactionLevel", 0.65, ("喜欢的女生", "好感", "暧昧", "刚认识", "第一次见")),
    ("conversationFriendly", 0.75, ("喜欢的女生", "好感", "暧昧", "刚认识", "朋友", "同学")),
    ("formality", 0.45, ("喜欢的女生", "好感", "暧昧")),
)

OPEN_HYPOTHESIS_RULES = (
    (
        "shared_task_icebreaking",
        "通过轻度共同任务减少聊天冷场",
        ("不会冷场", "不尴尬", "边玩边聊", "有事情做", "自然聊天"),
    ),
    (
        "gentle_family_pacing",
        "照顾不同体力成员并保留随时休息的节奏",
        ("带孩子和老人", "一家人", "老婆和孩子", "老少"),
    ),
    (
        "low_friction_trip",
        "减少决策、排队和转场带来的出行内耗",
        ("别折腾", "省心", "轻松一点", "懒得排队"),
    ),
)

DIMENSION_EVIDENCE_TERMS: dict[str, tuple[str, ...]] = {
    "physicalIntensity": ("少走路", "别太累", "不累", "走不动", "低体力", "老人", "父母", "长辈", "带孩子", "带娃"),
    "activityIntensity": ("放电", "释放精力", "跑跳", "运动", "痛快玩", "放松", "别太刺激"),
    "interactionLevel": ("互动", "一起玩", "边玩边聊", "不会冷场", "破冰", "暧昧", "好感", "刚认识"),
    "conversationFriendly": ("聊天", "慢聊", "坐下来聊", "不会冷场", "边玩边聊", "朋友", "同学", "暧昧", "好感", "刚认识"),
    "noiseLevel": ("安静", "别太吵", "不要太吵", "吵", "降噪"),
    "formality": ("不要太正式", "别太正式", "随意一点", "轻松一点", "暧昧", "好感", "第一次见"),
    "privacy": ("私密", "不被打扰", "独处"),
    "novelty": ("新奇", "新鲜", "没体验过", "小众"),
    "restAvailability": ("有座", "能坐", "休息", "少走路", "老人", "父母", "长辈"),
    "safety": ("安全", "带孩子", "带娃", "老人", "父母", "长辈"),
    "familyAccessibility": ("带孩子", "带娃", "亲子", "老人", "父母", "长辈", "老婆", "家庭"),
    "weatherResilience": ("室内", "下雨", "雨天", "别晒"),
    "routeConvenience": ("附近", "别太远", "近一点", "少折腾", "少转场", "少走路"),
    "pricePreference": ("省钱", "便宜", "低成本", "少花钱", "不想花钱", "免费"),
}

ACTIVITY_REQUEST_TERMS = (
    "出去玩",
    "想玩",
    "去玩",
    "活动",
    "景点",
    "逛",
    "看电影",
    "桌游",
    "手作",
    "放电",
    "运动",
    "体验",
)
RESTAURANT_REQUEST_TERMS = (
    "吃饭",
    "吃个饭",
    "吃一顿",
    "餐厅",
    "晚饭",
    "晚餐",
    "午饭",
    "火锅",
    "烤肉",
    "大排档",
    "减脂",
    "清淡",
)


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = repr(value)
        if key not in seen:
            result.append(value)
            seen.add(key)
    return result


def _hypothesis_id(text: str) -> str:
    return "hyp_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _dimension(
    key: str,
    target: float,
    *,
    source: str,
    confidence: float,
    evidence: list[str],
    importance: float = 0.7,
    scope: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": DIMENSION_DEFINITIONS[key]["label"],
        "target": round(max(0.0, min(1.0, float(target))), 3),
        "importance": round(max(0.0, min(1.0, float(importance))), 3),
        "source": source,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 3),
        "evidence": _unique(evidence)[:5],
        "scope": scope or ["activity", "restaurant"],
    }


def _facts(demand: dict[str, Any]) -> dict[str, Any]:
    return {
        "people": deepcopy(demand.get("people", {})),
        "timeWindow": deepcopy(demand.get("timeWindow", {})),
        "budget": deepcopy(demand.get("budget", {})),
        "location": deepcopy(demand.get("location", {})),
        "preferences": deepcopy(demand.get("preferences", {})),
    }


def _hard_constraints(demand: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in demand.get("constraints", {}).get("hard", []):
        result.append({"type": "declared", "value": str(item), "source": "explicit"})
    budget = demand.get("budget", {})
    if budget.get("maxTotal") is not None:
        result.append({"type": "budgetMaxTotal", "value": budget["maxTotal"], "source": "explicit"})
    if budget.get("perPerson") is not None:
        result.append({"type": "budgetPerPerson", "value": budget["perPerson"], "source": "explicit"})
    time_window = demand.get("timeWindow", {})
    if time_window.get("startTime") or time_window.get("endTime"):
        result.append(
            {
                "type": "timeWindow",
                "value": {
                    "startTime": time_window.get("startTime"),
                    "endTime": time_window.get("endTime"),
                },
                "source": "explicit",
            }
        )
    return _unique(result)


def _anchor_commitment(raw_input: str, name: str, preferred_area: str) -> str:
    fragments = [match.group(0) for match in re.finditer(rf".{{0,8}}{re.escape(name)}.{{0,8}}", raw_input)]
    text = " ".join(fragments) or raw_input
    if any(marker in text for marker in PREFERRED_ANCHOR_MARKERS):
        return "preferred"
    if name in raw_input and any(marker in text for marker in REQUIRED_ANCHOR_MARKERS):
        return "required"
    if preferred_area and name in preferred_area:
        return "required"
    return "optional"


def _has_destination_context(raw_input: str, name: str) -> bool:
    patterns = (
        rf"(想去|要去|必须去|一定去|专门去|主要去|顺路去|可以去|去){re.escape(name)}",
        rf"{re.escape(name)}(玩|逛|吃|见面|约会|打卡|看展|看电影)",
    )
    return any(re.search(pattern, raw_input) for pattern in patterns)


def resolve_destination_anchors(demand: dict[str, Any]) -> list[dict[str, Any]]:
    raw_input = str(demand.get("rawInput") or "")
    preferred_area = str(demand.get("location", {}).get("preferredArea") or "")
    start_point = str(demand.get("location", {}).get("startPoint") or "")
    existing = demand.get("demandProfile", {}).get("destinationAnchors", [])
    anchors: list[dict[str, Any]] = [deepcopy(item) for item in existing if isinstance(item, dict)]
    for name, area_id in AREA_ALIASES.items():
        if name not in raw_input and name not in preferred_area:
            continue
        if name in start_point and not _has_destination_context(raw_input, name):
            continue
        commitment = _anchor_commitment(raw_input, name, preferred_area)
        if commitment == "optional" and name not in preferred_area:
            continue
        anchors.append(
            {
                "anchorId": "anchor_" + hashlib.sha1(f"{name}:{area_id}".encode("utf-8")).hexdigest()[:10],
                "name": name,
                "entityType": "landmark_or_area",
                "resolvedPoiId": None,
                "resolvedAreaId": area_id,
                "commitment": commitment,
                "evidence": name,
                "status": "resolved",
            }
        )
    by_area: dict[str, dict[str, Any]] = {}
    rank = {"optional": 0, "preferred": 1, "required": 2}
    for anchor in anchors:
        area_id = str(anchor.get("resolvedAreaId") or "")
        if not area_id:
            continue
        current = by_area.get(area_id)
        anchor_name = str(anchor.get("name") or "")
        current_name = str((current or {}).get("name") or "")
        anchor_rank = rank.get(str(anchor.get("commitment")), 0) * 100 + (50 if anchor_name in raw_input else 0) + len(anchor_name)
        current_rank = rank.get(str((current or {}).get("commitment")), 0) * 100 + (50 if current_name in raw_input else 0) + len(current_name)
        if current is None or anchor_rank > current_rank:
            by_area[area_id] = anchor
    return list(by_area.values())


def _dimensions(demand: dict[str, Any]) -> list[dict[str, Any]]:
    raw = str(demand.get("rawInput") or "")
    dimensions: dict[str, dict[str, Any]] = {}
    existing = demand.get("demandProfile", {}).get("dimensions", [])
    social_primary = str(demand.get("socialIntent", {}).get("primary") or "unknown")
    for item in existing:
        if not isinstance(item, dict) or item.get("key") not in DIMENSION_DEFINITIONS:
            continue
        key = str(item["key"])
        source = str(item.get("source") or "hypothesis")
        support_terms = DIMENSION_EVIDENCE_TERMS.get(key, ())
        if not any(term in raw for term in support_terms):
            continue
        if social_primary in {"unknown", "casual_meetup"} and source != "explicit":
            continue
        dimensions[key] = deepcopy(item)

    for key, target, keywords in EXPLICIT_DIMENSION_RULES:
        evidence = [keyword for keyword in keywords if keyword in raw]
        if evidence:
            dimensions[key] = _dimension(
                key,
                target,
                source="explicit",
                confidence=0.95,
                evidence=evidence,
                importance=0.9,
            )
    for key, target, keywords in INFERRED_DIMENSION_RULES:
        if key in dimensions:
            continue
        evidence = [keyword for keyword in keywords if keyword in raw]
        if evidence:
            dimensions[key] = _dimension(
                key,
                target,
                source="llm_inference",
                confidence=0.72,
                evidence=evidence,
                importance=0.65,
            )

    return list(dimensions.values())


def _requested_components(demand: dict[str, Any]) -> list[str]:
    raw = str(demand.get("rawInput") or "")
    preferences = demand.get("preferences", {})
    activity_requested = bool(preferences.get("activityTypes")) or any(term in raw for term in ACTIVITY_REQUEST_TERMS)
    restaurant_requested = bool(preferences.get("foodTags")) or any(term in raw for term in RESTAURANT_REQUEST_TERMS)
    if not activity_requested and not restaurant_requested:
        return ["activity", "restaurant"]
    result = []
    if activity_requested:
        result.append("activity")
    if restaurant_requested:
        result.append("restaurant")
    return result


def _scene_hypotheses(demand: dict[str, Any]) -> list[dict[str, Any]]:
    social = demand.get("socialIntent", {}) if isinstance(demand.get("socialIntent"), dict) else {}
    primary = str(social.get("primary") or "unknown")
    if primary in {"unknown", "casual_meetup"}:
        return []
    return [
        {
            "key": primary,
            "confidence": float(social.get("confidence") or 0.65),
            "evidence": [str(item) for item in social.get("evidence", []) if item][:5],
            "usage": ["explanation", "recall_query"],
        }
    ]


def _open_hypotheses(demand: dict[str, Any]) -> list[dict[str, Any]]:
    raw = str(demand.get("rawInput") or "")
    current_profile = demand.get("demandProfile", {}) if isinstance(demand.get("demandProfile"), dict) else {}
    rejected_ids = set(current_profile.get("rejectedHypothesisIds", []))
    existing = current_profile.get("openHypotheses", [])
    result = [deepcopy(item) for item in existing if isinstance(item, dict) and item.get("status") != "user_rejected"]
    for key, text, keywords in OPEN_HYPOTHESIS_RULES:
        evidence = [keyword for keyword in keywords if keyword in raw]
        if len(evidence) < 1:
            continue
        item = (
            {
                "hypothesisId": _hypothesis_id(key + ":" + text),
                "key": key,
                "text": text,
                "confidence": 0.72,
                "evidence": evidence[:5],
                "status": "runtime_open",
            }
        )
        if item["hypothesisId"] not in rejected_ids:
            result.append(item)
    if os.getenv("FLOWCITY_APPROVED_LEARNING_ENABLED", "true").lower() == "true" and raw.strip():
        try:
            from ontology_evolution import approved_hypothesis_matches

            normalized_queries = _unique(
                [
                    *[str(item.get("text") or "") for item in result if item.get("text")],
                    raw,
                ]
            )
            for query in normalized_queries:
                for match in approved_hypothesis_matches(query):
                    proposal_id = str(match.get("proposalId") or match.get("clusterKey") or match.get("text"))
                    item = {
                        "hypothesisId": _hypothesis_id("approved:" + proposal_id),
                        "key": str(match.get("clusterKey") or "approved_learning_pattern"),
                        "text": str(match.get("text") or ""),
                        "confidence": round(float(match.get("similarity") or 0.0), 3),
                        "evidence": [query[:80]],
                        "status": "approved_learned_hypothesis",
                        "proposalId": match.get("proposalId"),
                    }
                    if item["text"] and item["hypothesisId"] not in rejected_ids:
                        result.append(item)
        except Exception:
            # Approved learning is a recall enhancement; it must not block planning.
            pass
    return _unique(result)


def ensure_demand_profile(demand: dict[str, Any]) -> dict[str, Any]:
    previous = demand.get("demandProfile", {}) if isinstance(demand.get("demandProfile"), dict) else {}
    profile = {
        "schemaVersion": SCHEMA_VERSION,
        "facts": _facts(demand),
        "hardConstraints": _hard_constraints(demand),
        "dimensions": _dimensions(demand),
        "destinationAnchors": resolve_destination_anchors(demand),
        "sceneHypotheses": _scene_hypotheses(demand),
        "openHypotheses": _open_hypotheses(demand),
        "requestedComponents": _requested_components(demand),
        "rejectedHypothesisIds": list(previous.get("rejectedHypothesisIds", [])),
        "conflicts": deepcopy(demand.get("potentialConflicts", [])),
    }
    demand["demandProfile"] = profile
    return profile


def apply_hypothesis_feedback(demand: dict[str, Any], feedback: dict[str, Any]) -> None:
    hypothesis_id = str(feedback.get("hypothesisId") or "")
    action = str(feedback.get("action") or "")
    if not hypothesis_id:
        return
    profile = demand.setdefault("demandProfile", {})
    rejected = profile.setdefault("rejectedHypothesisIds", [])
    if action in {"hypothesis_deleted", "hypothesis_rejected"} and hypothesis_id not in rejected:
        rejected.append(hypothesis_id)
    for item in profile.get("openHypotheses", []):
        if item.get("hypothesisId") == hypothesis_id:
            item["status"] = "user_rejected" if action in {"hypothesis_deleted", "hypothesis_rejected"} else action


def dimension_map(demand: dict[str, Any]) -> dict[str, dict[str, Any]]:
    profile = demand.get("demandProfile") if isinstance(demand.get("demandProfile"), dict) else ensure_demand_profile(demand)
    return {
        str(item["key"]): item
        for item in profile.get("dimensions", [])
        if isinstance(item, dict) and item.get("key") in DIMENSION_DEFINITIONS
    }


def protected_area_ids(demand: dict[str, Any]) -> set[str]:
    profile = demand.get("demandProfile") if isinstance(demand.get("demandProfile"), dict) else ensure_demand_profile(demand)
    return {
        str(item["resolvedAreaId"])
        for item in profile.get("destinationAnchors", [])
        if item.get("resolvedAreaId") and item.get("commitment") in {"required", "preferred"}
    }


def required_area_ids(demand: dict[str, Any]) -> set[str]:
    profile = demand.get("demandProfile") if isinstance(demand.get("demandProfile"), dict) else ensure_demand_profile(demand)
    return {
        str(item["resolvedAreaId"])
        for item in profile.get("destinationAnchors", [])
        if item.get("resolvedAreaId") and item.get("commitment") == "required"
    }


def open_hypothesis_texts(demand: dict[str, Any]) -> list[str]:
    profile = demand.get("demandProfile") if isinstance(demand.get("demandProfile"), dict) else ensure_demand_profile(demand)
    return [
        str(item["text"])
        for item in profile.get("openHypotheses", [])
        if item.get("text") and item.get("status") != "user_rejected"
    ]
