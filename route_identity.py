"""
Stable route identity helpers.

Routes are not uniquely identified by fromAreaId -> toAreaId once the same
pair can have public-transport, taxi, or estimated variants. Keep a stable
routeId as the canonical runtime key while carrying the legacy routeRef for
older mock runtime records.
"""

from __future__ import annotations

import re
from typing import Any


def _slug(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"


def legacy_route_ref(route: dict[str, Any] | None) -> str | None:
    if not route:
        return None
    from_area = route.get("fromAreaId")
    to_area = route.get("toAreaId")
    if not from_area or not to_area:
        return None
    return f"{from_area}->{to_area}"


def inferred_route_type(route: dict[str, Any]) -> str:
    if route.get("routeType"):
        return str(route["routeType"])
    if route.get("fromAreaId") == route.get("toAreaId"):
        return "same_area"
    return "area_to_area"


def generated_route_id(route: dict[str, Any]) -> str | None:
    from_area = route.get("fromAreaId")
    to_area = route.get("toAreaId")
    if not from_area or not to_area:
        return None
    transport = route.get("transport") or "unknown_transport"
    route_type = inferred_route_type(route)
    return "route__{}__{}__{}__{}".format(
        _slug(from_area),
        _slug(to_area),
        _slug(transport),
        _slug(route_type),
    )


def route_ref(route: dict[str, Any] | None) -> str | None:
    if not route:
        return None
    return str(route.get("routeId") or route.get("routeRef") or generated_route_id(route) or "") or None


def route_record_keys(record: dict[str, Any] | None) -> list[str]:
    if not record:
        return []
    keys: list[str] = []
    for key in ("routeId", "routeRef", "legacyRouteRef"):
        value = record.get(key)
        if value:
            keys.append(str(value))
    legacy = legacy_route_ref(record)
    if legacy:
        keys.append(legacy)
    generated = generated_route_id(record)
    if generated:
        keys.append(generated)
    deduped: list[str] = []
    for key in keys:
        if key not in deduped:
            deduped.append(key)
    return deduped


def with_route_identity(route: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(route)
    if "routeType" not in enriched:
        enriched["routeType"] = inferred_route_type(enriched)
    generated = generated_route_id(enriched)
    if generated and not enriched.get("routeId"):
        enriched["routeId"] = generated
    legacy = legacy_route_ref(enriched)
    if legacy and not enriched.get("legacyRouteRef"):
        enriched["legacyRouteRef"] = legacy
    if enriched.get("routeId"):
        enriched["routeRef"] = enriched["routeId"]
    elif generated:
        enriched["routeRef"] = generated
    return enriched
