"""Stable POI identity helpers used by supply and scheduling.

POI names are display text, not identity. A paid ticket, an open square, and a
second-level business district can point to the same physical place, so the
scheduler should dedupe by explicit place facts when available.
"""

from __future__ import annotations

import re
from typing import Any


def normalized_name(value: Any) -> str:
    return re.sub(r"[\s（）()·\-—_]+", "", str(value or "")).lower()


def place_group_id(item: dict[str, Any] | None) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("placeGroupId", "canonicalPlaceId"):
        value = item.get(key)
        if value:
            return str(value)
    if item.get("poiLevel") == "sub_area" and item.get("subAreaId"):
        return f"subarea:{item['subAreaId']}"
    return ""


def comparable_place_key(item: dict[str, Any] | None) -> str:
    explicit = place_group_id(item)
    if explicit:
        return explicit
    if not isinstance(item, dict):
        return ""
    return normalized_name(item.get("name") or item.get("title") or "")


def same_place(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    left_key = comparable_place_key(left)
    right_key = comparable_place_key(right)
    return bool(left_key and right_key and left_key == right_key)


def is_open_access(item: dict[str, Any] | None) -> bool:
    if not isinstance(item, dict):
        return False
    availability = item.get("availability") if isinstance(item.get("availability"), dict) else {}
    return bool(
        item.get("accessType") == "open_access"
        or item.get("poiLevel") == "sub_area"
        or availability.get("status") == "open_access"
        or availability.get("supplyType") == "open_subarea"
    )
