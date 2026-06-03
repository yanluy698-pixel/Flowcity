"""Standardized local tools for FlowCity.

These wrappers keep the Mock API shaped like future external tools: every tool
reports items, rejected records, warnings, latency, and source.
"""

from __future__ import annotations

import time
from typing import Any

import mock_api


def _tool_result(
    name: str,
    *,
    started_at: float,
    items: list[dict[str, Any]],
    rejected: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    source: str = "local_mock",
) -> dict[str, Any]:
    return {
        "tool": name,
        "items": items,
        "rejected": rejected or [],
        "warnings": warnings or [],
        "latencyMs": round((time.perf_counter() - started_at) * 1000, 2),
        "source": source,
    }


def search_supply_with_tools(structured_demand: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.perf_counter()
    supply = mock_api.search_supply(structured_demand)
    filtered = supply.get("filteredOut", [])
    activity_rejected = [item for item in filtered if item.get("kind") == "activity"]
    restaurant_rejected = [item for item in filtered if item.get("kind") == "restaurant"]
    tool_results = [
        _tool_result(
            "search_activities",
            started_at=started,
            items=supply.get("activityCandidates", []),
            rejected=activity_rejected,
            source="mock_activities+mock_availability",
        ),
        _tool_result(
            "search_restaurants",
            started_at=started,
            items=supply.get("restaurantCandidates", []),
            rejected=restaurant_rejected,
            source="mock_restaurants+mock_availability",
        ),
        _tool_result(
            "get_routes",
            started_at=started,
            items=supply.get("routeCandidates", []),
            rejected=[],
            source="mock_routes",
        ),
    ]
    supply["toolResults"] = tool_results
    return supply, tool_results
