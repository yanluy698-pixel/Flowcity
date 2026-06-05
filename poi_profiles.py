"""Stable numeric POI profiles used by retrieval and scoring."""

from __future__ import annotations

from typing import Any


DEFAULT_PROFILE = {
    "physicalIntensity": 0.35,
    "activityIntensity": 0.4,
    "interactionLevel": 0.45,
    "conversationFriendly": 0.55,
    "noiseLevel": 0.45,
    "formality": 0.45,
    "privacy": 0.4,
    "novelty": 0.5,
    "restAvailability": 0.55,
    "safety": 0.75,
    "familyAccessibility": 0.5,
    "weatherResilience": 0.55,
    "routeConvenience": 0.55,
    "pricePreference": 0.5,
}

CATEGORY_PROFILES = {
    "parent_child_playground": {
        "physicalIntensity": 0.6,
        "activityIntensity": 0.85,
        "interactionLevel": 0.8,
        "conversationFriendly": 0.35,
        "noiseLevel": 0.75,
        "familyAccessibility": 0.95,
        "safety": 0.85,
        "weatherResilience": 0.85,
    },
    "museum": {
        "physicalIntensity": 0.35,
        "activityIntensity": 0.25,
        "interactionLevel": 0.35,
        "conversationFriendly": 0.65,
        "noiseLevel": 0.2,
        "formality": 0.6,
        "novelty": 0.75,
        "safety": 0.9,
    },
    "citywalk": {
        "physicalIntensity": 0.7,
        "activityIntensity": 0.55,
        "interactionLevel": 0.55,
        "conversationFriendly": 0.7,
        "noiseLevel": 0.55,
        "novelty": 0.7,
        "weatherResilience": 0.2,
    },
    "handcraft": {
        "physicalIntensity": 0.25,
        "activityIntensity": 0.5,
        "interactionLevel": 0.8,
        "conversationFriendly": 0.8,
        "noiseLevel": 0.3,
        "novelty": 0.85,
        "restAvailability": 0.85,
    },
    "movie": {
        "physicalIntensity": 0.05,
        "activityIntensity": 0.2,
        "interactionLevel": 0.1,
        "conversationFriendly": 0.05,
        "noiseLevel": 0.35,
        "privacy": 0.55,
        "restAvailability": 0.9,
        "weatherResilience": 0.95,
    },
}

CUISINE_PROFILES = {
    "light_food": {"noiseLevel": 0.3, "formality": 0.4, "conversationFriendly": 0.7, "pricePreference": 0.4},
    "cafe_meal": {"noiseLevel": 0.3, "formality": 0.5, "conversationFriendly": 0.85, "privacy": 0.55},
    "bistro": {"noiseLevel": 0.45, "formality": 0.7, "conversationFriendly": 0.8, "privacy": 0.65},
    "hotpot": {"noiseLevel": 0.8, "formality": 0.25, "interactionLevel": 0.8, "conversationFriendly": 0.55},
    "barbecue": {"noiseLevel": 0.75, "formality": 0.2, "interactionLevel": 0.8, "conversationFriendly": 0.55},
    "fast_food": {"noiseLevel": 0.6, "formality": 0.15, "conversationFriendly": 0.25, "pricePreference": 0.25},
}

TAG_ADJUSTMENTS = {
    "室内": {"weatherResilience": 0.95},
    "户外": {"weatherResilience": 0.2},
    "儿童友好": {"familyAccessibility": 0.95, "safety": 0.9},
    "亲子": {"familyAccessibility": 0.95},
    "少走路": {"physicalIntensity": 0.2, "restAvailability": 0.8},
    "安静": {"noiseLevel": 0.2, "conversationFriendly": 0.85},
    "聊天": {"conversationFriendly": 0.9, "interactionLevel": 0.7},
    "桌游": {"interactionLevel": 0.9, "conversationFriendly": 0.75, "novelty": 0.75},
    "密室": {"interactionLevel": 0.9, "activityIntensity": 0.75, "novelty": 0.85},
    "运动": {"physicalIntensity": 0.85, "activityIntensity": 0.9},
    "拍照": {"novelty": 0.7},
    "可预约": {"routeConvenience": 0.7},
    "商场": {"restAvailability": 0.85, "weatherResilience": 0.9, "routeConvenience": 0.75},
}


def _apply(profile: dict[str, float], values: dict[str, float]) -> None:
    for key, value in values.items():
        profile[key] = float(value)


def build_poi_profile(poi: dict[str, Any]) -> dict[str, float]:
    explicit = poi.get("baseProfile")
    if isinstance(explicit, dict):
        profile = dict(DEFAULT_PROFILE)
        _apply(profile, {str(key): float(value) for key, value in explicit.items() if isinstance(value, (int, float))})
        return profile

    profile = dict(DEFAULT_PROFILE)
    category = str(poi.get("category") or "")
    cuisine = str(poi.get("cuisine") or "")
    _apply(profile, CATEGORY_PROFILES.get(category, {}))
    _apply(profile, CUISINE_PROFILES.get(cuisine, {}))
    searchable = " ".join(
        [
            str(poi.get("name") or ""),
            category,
            cuisine,
            *[str(tag) for tag in poi.get("tags", [])],
        ]
    )
    for tag, adjustments in TAG_ADJUSTMENTS.items():
        if tag in searchable:
            _apply(profile, adjustments)
    if poi.get("indoorOutdoor") == "indoor":
        profile["weatherResilience"] = 0.95
    if poi.get("childFriendly"):
        profile["familyAccessibility"] = 0.95
    if poi.get("reservable"):
        profile["routeConvenience"] = max(profile["routeConvenience"], 0.7)
    return {key: round(max(0.0, min(1.0, value)), 3) for key, value in profile.items()}


def neutral_poi_document(poi: dict[str, Any], area_name: str = "") -> str:
    profile = build_poi_profile(poi)
    high_dimensions = sorted(profile.items(), key=lambda item: item[1], reverse=True)[:5]
    return "；".join(
        [
            str(poi.get("name") or ""),
            f"区域:{area_name}",
            f"类别:{poi.get('category') or poi.get('cuisine') or ''}",
            "事实标签:" + "、".join(str(tag) for tag in poi.get("tags", [])),
            "基础属性:" + "、".join(f"{key}={value:.2f}" for key, value in high_dimensions),
        ]
    )
