"""
Lightweight POI governance normalization.

The mock POI files stay readable and concise. This layer derives provenance and
constraint tags from stable facts at load time so coverage checks do not depend
on hand-labeling every POI.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_VERIFIED_AT = "2026-06-06"


def _price_tag(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return "price:free"
    if value <= 30:
        return "price:low"
    if value <= 80:
        return "price:mid"
    return "price:high"


def _unique(values: list[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def governance_fields(poi: dict[str, Any], *, source_type: str) -> dict[str, Any]:
    price = poi.get("pricePerPerson", poi.get("avgPricePerPerson"))
    fact_tags = _unique(
        [
            f"category:{poi.get('category')}" if poi.get("category") else None,
            f"cuisine:{poi.get('cuisine')}" if poi.get("cuisine") else None,
            f"area:{poi.get('areaId')}" if poi.get("areaId") else None,
            f"space:{poi.get('indoorOutdoor')}" if poi.get("indoorOutdoor") else None,
            "open_access_subarea" if poi.get("poiLevel") == "sub_area" else None,
            _price_tag(price),
        ]
    )
    constraint_tags = _unique(
        [
            "has_open_hours" if poi.get("openHours") else None,
            "has_age_range" if poi.get("ageMin") is not None or poi.get("ageMax") is not None else None,
            "reservable" if poi.get("reservable") else None,
            "child_friendly" if poi.get("childFriendly") else None,
            "open_access_no_inventory" if poi.get("poiLevel") == "sub_area" else None,
        ]
    )
    return {
        "sourceType": poi.get("sourceType") or source_type,
        "confidence": poi.get("confidence", 0.76 if source_type == "mock_curated" else 0.68),
        "lastVerifiedAt": poi.get("lastVerifiedAt") or DEFAULT_VERIFIED_AT,
        "factTags": poi.get("factTags") or fact_tags,
        "constraintTags": poi.get("constraintTags") or constraint_tags,
    }


def enrich_poi(poi: dict[str, Any], *, source_type: str) -> dict[str, Any]:
    enriched = deepcopy(poi)
    for key, value in governance_fields(enriched, source_type=source_type).items():
        enriched.setdefault(key, value)
    return enriched


def enrich_many(items: list[dict[str, Any]], *, source_type: str) -> list[dict[str, Any]]:
    return [enrich_poi(item, source_type=source_type) for item in items]


def coverage(items: list[dict[str, Any]]) -> dict[str, int]:
    total = len(items)
    return {
        "total": total,
        "sourceType": sum(1 for item in items if item.get("sourceType")),
        "confidence": sum(1 for item in items if item.get("confidence") is not None),
        "lastVerifiedAt": sum(1 for item in items if item.get("lastVerifiedAt")),
        "factTags": sum(1 for item in items if item.get("factTags")),
        "constraintTags": sum(1 for item in items if item.get("constraintTags")),
    }
