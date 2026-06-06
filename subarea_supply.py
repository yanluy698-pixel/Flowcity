"""Validated second-level area supply for open-access itinerary nodes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import demand_profile


REQUIRED_FIELDS = {
    "id",
    "name",
    "areaId",
    "subAreaId",
    "category",
    "tags",
    "openHours",
    "suggestedDurationMinutes",
    "pricePerPerson",
    "indoorOutdoor",
    "baseRating",
    "baseProfile",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_subarea(item: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_FIELDS - set(item.keys()))
    if missing:
        raise ValueError(f"mock_subareas missing fields for {item.get('id')}: {', '.join(missing)}")
    profile = item.get("baseProfile")
    if not isinstance(profile, dict) or not profile:
        raise ValueError(f"mock_subareas {item.get('id')} must define baseProfile")
    known_dimensions = set(demand_profile.DIMENSION_DEFINITIONS)
    unknown_dimensions = sorted(str(key) for key in profile if key not in known_dimensions)
    if unknown_dimensions:
        raise ValueError(f"mock_subareas {item.get('id')} has unknown baseProfile keys: {', '.join(unknown_dimensions)}")
    non_numeric = sorted(str(key) for key, value in profile.items() if not isinstance(value, (int, float)))
    if non_numeric:
        raise ValueError(f"mock_subareas {item.get('id')} has non-numeric baseProfile keys: {', '.join(non_numeric)}")
    hours = item.get("openHours")
    if not isinstance(hours, dict) or "weekday" not in hours or "weekend" not in hours:
        raise ValueError(f"mock_subareas {item.get('id')} must define weekday/weekend openHours")


def _to_activity(subarea: dict[str, Any]) -> dict[str, Any]:
    area_id = str(subarea.get("areaId") or "")
    return {
        **subarea,
        "id": str(subarea.get("id") or subarea.get("subAreaId") or ""),
        "areaId": area_id,
        "parentAreaId": area_id,
        "poiLevel": "sub_area",
        "isFiller": False,
        "ageMin": int(subarea.get("ageMin") or 0),
        "ageMax": int(subarea.get("ageMax") or 99),
        "source": "mock_subareas.json",
    }


def load_subarea_activities(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    subareas = _load_json(path).get("subareas", [])
    if not isinstance(subareas, list):
        raise ValueError("mock_subareas.json must contain a subareas array")
    result = []
    for item in subareas:
        if not isinstance(item, dict):
            raise ValueError("mock_subareas entries must be objects")
        _validate_subarea(item)
        result.append(_to_activity(item))
    return result


def open_access_availability() -> dict[str, Any]:
    return {
        "dateText": "开放街区",
        "timeSlots": [],
        "bestTicketLeft": 999,
        "minQueueMinutes": 0,
        "worstCrowdLevel": "unknown",
        "supplyType": "open_subarea",
        "status": "open_access",
    }
