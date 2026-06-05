"""Fixed coarse-to-fine area retrieval for every FlowCity request."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import demand_profile
from poi_profiles import build_poi_profile


MIN_EXPLORE_AREAS = 2
MAX_EXPLORE_AREAS = 5
SUPPLY_TARGET_PER_KIND = 24
DIMENSION_COVERAGE_TARGET = 0.85
NEAR_TARGET_MINUTES = 20
NEAR_HARD_MAX_MINUTES = 35
MINIMUM_USABLE_VISIT_MINUTES = 75


def _is_origin(area_id: str) -> bool:
    return area_id.startswith("origin_")


def _people_total(demand: dict[str, Any]) -> int:
    people = demand.get("people", {})
    if isinstance(people.get("total"), int) and people["total"] > 0:
        return int(people["total"])
    return max(1, int(people.get("adults") or 0) + len(people.get("children", [])) + len(people.get("seniors", [])))


def _budget_limit(demand: dict[str, Any]) -> float | None:
    budget = demand.get("budget", {})
    if isinstance(budget.get("maxTotal"), (int, float)):
        return float(budget["maxTotal"])
    if isinstance(budget.get("perPerson"), (int, float)):
        return float(budget["perPerson"]) * _people_total(demand)
    return None


def build_area_summaries(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Aggregate cheap area-level summaries from POIs.

    Production can replace this with an offline index without changing callers.
    """
    grouped_activities: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped_restaurants: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in data.get("activities", []):
        grouped_activities[str(item["areaId"])].append(item)
    for item in data.get("restaurants", []):
        grouped_restaurants[str(item["areaId"])].append(item)

    summaries: dict[str, dict[str, Any]] = {}
    for area in data.get("areas", []):
        area_id = str(area["areaId"])
        activities = grouped_activities.get(area_id, [])
        restaurants = grouped_restaurants.get(area_id, [])
        pois = [*activities, *restaurants]
        profiles = [build_poi_profile(item) for item in pois]
        dimension_means: dict[str, float] = {}
        if profiles:
            for key in profiles[0]:
                dimension_means[key] = round(sum(profile[key] for profile in profiles) / len(profiles), 3)
        prices = [
            float(item.get("pricePerPerson") or item.get("avgPricePerPerson") or 0)
            for item in pois
        ]
        searchable_tokens = sorted(
            {
                str(value)
                for item in pois
                for value in [
                    item.get("category"),
                    item.get("cuisine"),
                    *item.get("tags", []),
                ]
                if value
            }
        )
        summaries[area_id] = {
            "areaId": area_id,
            "name": area.get("name"),
            "district": area.get("district"),
            "landmarks": area.get("landmarks", []),
            "activityCount": len(activities),
            "restaurantCount": len(restaurants),
            "supplyCount": len(pois),
            "dimensionMeans": dimension_means,
            "averageRating": round(
                sum(float(item.get("baseRating") or 0) for item in pois) / len(pois), 3
            )
            if pois
            else 0.0,
            "averagePricePerPerson": round(sum(prices) / len(prices), 2) if prices else 0.0,
            "indoorRatio": round(
                sum(1 for item in activities if item.get("indoorOutdoor") == "indoor") / len(activities), 3
            )
            if activities
            else 0.0,
            "childCoverage": round(
                sum(
                    1
                    for item in pois
                    if item.get("childFriendly") or "儿童友好" in item.get("tags", []) or "亲子" in item.get("tags", [])
                )
                / len(pois),
                3,
            )
            if pois
            else 0.0,
            "reservableRatio": round(
                sum(1 for item in restaurants if item.get("reservable")) / len(restaurants), 3
            )
            if restaurants
            else 0.0,
            "searchableTokens": searchable_tokens,
        }
    return summaries


def _origin_area_ids(demand: dict[str, Any], data: dict[str, Any]) -> set[str]:
    location = demand.get("location", {})
    texts = [
        str(location.get("startPoint") or ""),
        *[
            str(item.get("point") or "")
            for item in location.get("originPoints", [])
            if isinstance(item, dict)
        ],
    ]
    result: set[str] = set()
    for area in data.get("areas", []):
        searchable = " ".join([str(area.get("name") or ""), str(area.get("district") or ""), *area.get("landmarks", [])])
        if any(text and (text in searchable or searchable in text) for text in texts):
            result.add(str(area["areaId"]))
    return result


def _best_route_minutes_by_area(demand: dict[str, Any], data: dict[str, Any]) -> dict[str, float]:
    origins = _origin_area_ids(demand, data)
    if not origins:
        return {}
    minutes: dict[str, float] = {}
    for route in data.get("routes", []):
        if str(route.get("fromAreaId")) not in origins:
            continue
        area_id = str(route.get("toAreaId") or "")
        value = route.get("minutes")
        if area_id and isinstance(value, (int, float)):
            minutes[area_id] = min(minutes.get(area_id, float("inf")), float(value))
    return minutes


def _dimension_coverage(summary: dict[str, Any], demand: dict[str, Any]) -> float:
    dimensions = demand_profile.dimension_map(demand)
    if not dimensions:
        return 1.0
    means = summary.get("dimensionMeans", {})
    weighted = 0.0
    total = 0.0
    for key, item in dimensions.items():
        importance = float(item.get("importance") or 0.5)
        confidence = float(item.get("confidence") or 0.5)
        weight = importance * confidence
        value = float(means.get(key, 0.5))
        target = float(item.get("target") or 0.5)
        weighted += max(0.0, 1.0 - abs(value - target)) * weight
        total += weight
    return weighted / total if total else 1.0


def _near_requested(demand: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(demand.get("rawInput") or ""),
            str(demand.get("location", {}).get("distancePreference") or ""),
        ]
    )
    return any(word in text for word in ("附近", "近一点", "别太远", "少走路", "不要太远"))


def _parse_minutes(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _time_window_minutes(demand: dict[str, Any]) -> int | None:
    time_window = demand.get("timeWindow", {})
    start = _parse_minutes(time_window.get("startTime"))
    end = _parse_minutes(time_window.get("endTime"))
    if start is not None and end is not None and end > start:
        return end - start
    duration = time_window.get("durationHours")
    if isinstance(duration, (int, float)) and duration > 0:
        return int(float(duration) * 60)
    return None


def _explicit_recall_terms(demand: dict[str, Any]) -> list[str]:
    preferences = demand.get("preferences", {})
    social = demand.get("socialIntent", {}) if isinstance(demand.get("socialIntent"), dict) else {}
    values = [
        *preferences.get("activityTypes", []),
        *preferences.get("foodTags", []),
        *social.get("explicitPreferredVibes", []),
    ]
    return [str(value) for value in values if value]


def recall_areas(demand: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    demand_profile.ensure_demand_profile(demand)
    summaries = build_area_summaries(data)
    protected = demand_profile.protected_area_ids(demand)
    route_minutes = _best_route_minutes_by_area(demand, data)
    budget_limit = _budget_limit(demand)
    near_requested = _near_requested(demand)
    explicit_terms = _explicit_recall_terms(demand)
    time_window_minutes = _time_window_minutes(demand)
    ranked: list[dict[str, Any]] = []

    for area_id, summary in summaries.items():
        if _is_origin(area_id):
            continue
        is_protected = area_id in protected
        minutes = route_minutes.get(area_id)
        status = "feasible"
        filtered_reason = None
        if summary["activityCount"] == 0 or summary["restaurantCount"] == 0:
            status = "strained"
        if near_requested and minutes is not None and minutes > NEAR_HARD_MAX_MINUTES:
            status = "infeasible_requires_confirmation" if is_protected else "filtered"
            filtered_reason = f"用户要求附近，但粗略通勤约 {minutes:.0f} 分钟"
        minimum_required_minutes = (minutes * 2 + MINIMUM_USABLE_VISIT_MINUTES) if minutes is not None else None
        if (
            time_window_minutes is not None
            and minimum_required_minutes is not None
            and minimum_required_minutes > time_window_minutes
        ):
            status = "infeasible_requires_confirmation" if is_protected else "filtered"
            filtered_reason = (
                f"往返粗略通勤加最低游玩时间约 {minimum_required_minutes:.0f} 分钟，"
                f"超过当前时间窗 {time_window_minutes} 分钟"
            )

        route_score = 7.0 if minutes is None else max(-12.0, 10.0 - minutes / 3.0)
        if near_requested and minutes is not None and minutes <= NEAR_TARGET_MINUTES:
            route_score += 6.0
        coverage = _dimension_coverage(summary, demand)
        coverage_score = coverage * 16.0
        richness_score = min(12.0, summary["activityCount"] * 0.7 + summary["restaurantCount"] * 0.5)
        quality_score = max(0.0, (float(summary["averageRating"]) - 4.0) * 5.0)
        price_score = 0.0
        if budget_limit is not None and summary["averagePricePerPerson"] > 0:
            expected_pair_cost = summary["averagePricePerPerson"] * _people_total(demand) * 1.6
            utilization = expected_pair_cost / max(budget_limit, 1.0)
            price_score = max(-6.0, 5.0 - abs(0.9 - utilization) * 10.0)
        token_text = " ".join(summary.get("searchableTokens", []))
        explicit_coverage = [term for term in explicit_terms if term in token_text or any(part in token_text for part in term.split())]
        explicit_score = min(24.0, len(explicit_coverage) * 8.0)
        score = route_score + coverage_score + richness_score + quality_score + price_score + explicit_score
        if is_protected:
            score += 1000.0
        ranked.append(
            {
                **summary,
                "protected": is_protected,
                "routeMinutesHint": minutes,
                "minimumRequiredMinutes": round(minimum_required_minutes, 1) if minimum_required_minutes is not None else None,
                "status": status,
                "filteredReason": filtered_reason,
                "dimensionCoverage": round(coverage, 3),
                "areaScore": round(score, 3),
                "scoreBreakdown": {
                    "route": round(route_score, 3),
                    "dimensionCoverage": round(coverage_score, 3),
                    "supplyRichness": round(richness_score, 3),
                    "quality": round(quality_score, 3),
                    "priceBandFit": round(price_score, 3),
                    "explicitSupplyCoverage": round(explicit_score, 3),
                },
                "matchedExplicitTerms": explicit_coverage,
            }
        )

    ranked.sort(key=lambda item: (-float(item["areaScore"]), str(item["areaId"])))
    selected: list[dict[str, Any]] = []
    explore_count = 0
    activity_supply = 0
    restaurant_supply = 0
    coverage_values: list[float] = []
    for item in ranked:
        if item["status"] == "filtered" and not item["protected"]:
            continue
        if not item["protected"] and explore_count >= MAX_EXPLORE_AREAS:
            continue
        selected.append(item)
        if not item["protected"]:
            explore_count += 1
        activity_supply += int(item["activityCount"])
        restaurant_supply += int(item["restaurantCount"])
        coverage_values.append(float(item["dimensionCoverage"]))
        enough = (
            explore_count >= MIN_EXPLORE_AREAS
            and activity_supply >= SUPPLY_TARGET_PER_KIND
            and restaurant_supply >= SUPPLY_TARGET_PER_KIND
            and max(coverage_values or [0]) >= DIMENSION_COVERAGE_TARGET
        )
        if enough and all(protected_id in {value["areaId"] for value in selected} for protected_id in protected):
            break

    return {
        "strategy": "fixed_progressive_area_recall",
        "evaluatedAreaCount": len(ranked),
        "selectedAreaIds": [item["areaId"] for item in selected],
        "protectedAreaIds": sorted(protected),
        "selectedAreas": selected,
        "rankedAreas": ranked,
        "estimatedActivitySupply": activity_supply,
        "estimatedRestaurantSupply": restaurant_supply,
        "coverageTarget": DIMENSION_COVERAGE_TARGET,
        "anchorConflicts": [
            {
                "areaId": item["areaId"],
                "areaName": item["name"],
                "status": item["status"],
                "reason": item["filteredReason"],
                "suggestedActions": ["延长时间窗", "改为单程结束", "保留目的地并减少其他节点"],
            }
            for item in selected
            if item["protected"] and item["status"] == "infeasible_requires_confirmation"
        ],
    }
