"""
Optional FastAPI wrapper for FlowCity Stage 3 Mock API.

The business logic lives in mock_api.py. This file only exposes HTTP endpoints
when FastAPI is installed.
"""

from __future__ import annotations

from typing import Any

from mock_api import (
    check_deals,
    load_mock_data,
    search_activities,
    search_restaurants,
    search_supply,
)


try:
    from fastapi import FastAPI, HTTPException
except ImportError:  # pragma: no cover - depends on optional local install.
    FastAPI = None
    HTTPException = None


def create_app() -> Any:
    if FastAPI is None:
        raise RuntimeError(
            "FastAPI is not installed. Run the function version first with "
            "`python mock_api.py --example-id family_half_day`, or install "
            "fastapi and uvicorn before starting this server."
        )

    app = FastAPI(
        title="FlowCity Stage 3 Mock API",
        description="Local mock supply tools for activities, restaurants, availability, routes, and deals.",
        version="0.1.0",
    )

    @app.post("/mock/search-supply")
    def search_supply_endpoint(structured_demand: dict[str, Any]) -> dict[str, Any]:
        return search_supply(structured_demand)

    @app.post("/mock/activities/search")
    def search_activities_endpoint(structured_demand: dict[str, Any]) -> dict[str, Any]:
        data = load_mock_data()
        candidates, filtered_out, tool_logs = search_activities(structured_demand, data)
        return {
            "city": "西安",
            "activityCandidates": candidates,
            "filteredOut": filtered_out,
            "toolLogs": tool_logs,
        }

    @app.post("/mock/restaurants/search")
    def search_restaurants_endpoint(structured_demand: dict[str, Any]) -> dict[str, Any]:
        data = load_mock_data()
        candidates, filtered_out, tool_logs = search_restaurants(structured_demand, data)
        return {
            "city": "西安",
            "restaurantCandidates": candidates,
            "filteredOut": filtered_out,
            "toolLogs": tool_logs,
        }

    @app.get("/mock/availability/{poi_id}")
    def availability_endpoint(poi_id: str) -> dict[str, Any]:
        data = load_mock_data()
        activity = [
            item for item in data["activityAvailability"] if item.get("poiId") == poi_id
        ]
        restaurant = [
            item for item in data["restaurantAvailability"] if item.get("poiId") == poi_id
        ]
        if not activity and not restaurant:
            raise HTTPException(status_code=404, detail=f"No availability for {poi_id}")
        return {"poiId": poi_id, "activityAvailability": activity, "restaurantAvailability": restaurant}

    @app.get("/mock/deals/{poi_id}")
    def deals_endpoint(poi_id: str) -> dict[str, Any]:
        return {"poiId": poi_id, "deals": check_deals(poi_id)}

    return app


app = create_app() if FastAPI is not None else None


if __name__ == "__main__":
    if FastAPI is None:
        raise SystemExit(
            "FastAPI is not installed. Use `python mock_api.py --example-id family_half_day` "
            "for the current stage, or install fastapi and uvicorn to run HTTP endpoints."
        )
    raise SystemExit("Run with: uvicorn api:app --reload")
