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
    "board_game": {
        "physicalIntensity": 0.2,
        "activityIntensity": 0.55,
        "interactionLevel": 0.9,
        "conversationFriendly": 0.78,
        "noiseLevel": 0.55,
        "novelty": 0.72,
        "restAvailability": 0.85,
        "weatherResilience": 0.95,
    },
    "light_sport": {
        "physicalIntensity": 0.65,
        "activityIntensity": 0.82,
        "interactionLevel": 0.72,
        "conversationFriendly": 0.55,
        "noiseLevel": 0.62,
        "formality": 0.18,
        "novelty": 0.65,
        "weatherResilience": 0.9,
    },
    "mall_walk": {
        "physicalIntensity": 0.35,
        "activityIntensity": 0.48,
        "interactionLevel": 0.52,
        "conversationFriendly": 0.68,
        "noiseLevel": 0.52,
        "formality": 0.22,
        "novelty": 0.56,
        "restAvailability": 0.85,
        "weatherResilience": 0.95,
        "routeConvenience": 0.82,
        "pricePreference": 0.18,
    },
    "arcade": {
        "physicalIntensity": 0.42,
        "activityIntensity": 0.78,
        "interactionLevel": 0.82,
        "conversationFriendly": 0.58,
        "noiseLevel": 0.72,
        "formality": 0.15,
        "novelty": 0.72,
        "restAvailability": 0.7,
        "weatherResilience": 0.95,
        "pricePreference": 0.35,
    },
    "exhibition": {
        "physicalIntensity": 0.32,
        "activityIntensity": 0.45,
        "interactionLevel": 0.45,
        "conversationFriendly": 0.72,
        "noiseLevel": 0.32,
        "formality": 0.36,
        "novelty": 0.72,
        "restAvailability": 0.68,
        "weatherResilience": 0.92,
        "pricePreference": 0.3,
    },
    "culture_walk": {
        "physicalIntensity": 0.5,
        "activityIntensity": 0.48,
        "interactionLevel": 0.45,
        "conversationFriendly": 0.65,
        "noiseLevel": 0.38,
        "formality": 0.38,
        "novelty": 0.68,
        "restAvailability": 0.48,
        "weatherResilience": 0.35,
        "routeConvenience": 0.76,
        "pricePreference": 0.16,
    },
    "food_walk": {
        "physicalIntensity": 0.55,
        "activityIntensity": 0.62,
        "interactionLevel": 0.58,
        "conversationFriendly": 0.62,
        "noiseLevel": 0.68,
        "formality": 0.18,
        "novelty": 0.62,
        "restAvailability": 0.45,
        "weatherResilience": 0.25,
        "routeConvenience": 0.78,
        "pricePreference": 0.15,
    },
    "landmark_walk": {
        "physicalIntensity": 0.5,
        "activityIntensity": 0.5,
        "interactionLevel": 0.45,
        "conversationFriendly": 0.62,
        "noiseLevel": 0.5,
        "formality": 0.36,
        "novelty": 0.68,
        "restAvailability": 0.52,
        "weatherResilience": 0.3,
        "routeConvenience": 0.78,
        "pricePreference": 0.15,
    },
}

CATEGORY_ALIASES = {
    "cinema_ticket": "movie",
    "student_board_game": "board_game",
    "board_game_light_sport": "board_game",
    "light_sport": "light_sport",
    "arcade_light_play": "arcade",
    "student_exhibition": "exhibition",
    "free_mall_walk": "mall_walk",
    "sub_area_mall_walk": "mall_walk",
    "sub_area_art_mall_walk": "mall_walk",
    "compact_citywalk": "citywalk",
    "sub_area_culture_walk": "culture_walk",
    "sub_area_food_walk": "food_walk",
    "sub_area_shopping_walk": "food_walk",
    "sub_area_landmark_walk": "landmark_walk",
    "sub_area_night_walk": "landmark_walk",
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
    raw_category = str(poi.get("category") or "")
    category = CATEGORY_ALIASES.get(raw_category, raw_category)
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
