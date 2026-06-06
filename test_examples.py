"""
Batch-check FlowCity Stage 2 examples.

Default mode does not call the LLM:
- validates every expectedStructuredDemand in examples.json against schema.json
- verifies each example can enter Stage 3 mock_api.search_supply

Optional --llm mode calls the configured model for each userInput.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import extractor
import executor
import mock_api
import planner
import run_flow
import validator


ROOT = Path(__file__).resolve().parent
EXAMPLES_PATH = ROOT / "examples.json"


def load_examples() -> list[dict[str, Any]]:
    data = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
    return data["examples"]


def _minutes(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _duration_minutes(item: dict[str, Any]) -> int:
    start = _minutes(item.get("start"))
    end = _minutes(item.get("end"))
    if start is None or end is None:
        return 0
    if end < start:
        end += 24 * 60
    return max(0, end - start)


def _longest_idle_minutes(plan: dict[str, Any]) -> int:
    longest = 0
    for item in plan.get("timeline", []):
        text = " ".join(str(item.get(key) or "") for key in ("type", "title", "description"))
        if item.get("type") == "buffer" or any(keyword in text for keyword in ("空窗", "空档", "等待", "等位", "缓冲")):
            longest = max(longest, _duration_minutes(item))
    return longest


def _experience_block_count(plan: dict[str, Any]) -> int:
    return sum(1 for item in plan.get("timeline", []) if item.get("type") in {"activity", "filler", "micro_activity", "rest"})


def validate_expected_examples(examples: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    schema = extractor.load_json(extractor.SCHEMA_PATH)

    for example in examples:
        example_id = example["id"]
        demand = extractor.normalize_structured_demand(
            json.loads(json.dumps(example["expectedStructuredDemand"], ensure_ascii=False)),
            example.get("userInput"),
        )
        validation_errors = extractor.basic_validate(demand, schema)
        errors.extend(f"{example_id}: {error}" for error in validation_errors)

    return errors


def _base_semantic_test_demand(raw_input: str) -> dict[str, Any]:
    return extractor.normalize_structured_demand(
        {
            "rawInput": raw_input,
            "scene": {"primaryType": "open", "confidence": 0.5, "tags": []},
            "timeWindow": {
                "dateText": "周六",
                "startTime": "14:00",
                "endTime": "18:00",
                "durationHours": 4,
                "isFlexible": True,
            },
            "people": {"total": 2, "adults": 2, "children": [], "seniors": [], "relationship": "friends", "specialNeeds": []},
            "budget": {"maxTotal": None, "perPerson": None, "currency": "CNY", "flexibility": "unknown"},
            "location": {
                "startPoint": None,
                "originPoints": [],
                "preferredArea": None,
                "crossCityIntent": {"enabled": False, "fromCity": None, "toCity": None},
                "maxTravelMinutes": None,
                "transportPreference": None,
                "distancePreference": None,
            },
            "preferences": {"activityTypes": [], "foodTags": [], "experienceTags": [], "avoidTags": []},
            "constraints": {"hard": [], "soft": [], "dynamic": []},
            "potentialConflicts": [],
            "expectedOutput": {"planFormat": "timeline_plan", "mustInclude": ["时间轴", "餐饮安排", "预算估算", "风险提示"]},
            "assumptions": [],
            "clarificationQuestions": [],
        },
        raw_input,
    )


def check_semantic_bridge_behavior() -> list[str]:
    errors: list[str] = []

    neutral_family = _base_semantic_test_demand("周六下午带5岁孩子出去，别太远，预算400。")
    neutral_social = neutral_family.get("socialIntent", {})
    if neutral_social.get("subScenario") != "kid_care":
        errors.append(f"semantic_bridge: neutral child context should use kid_care, got {neutral_social.get('subScenario')}")
    if "释放精力" in neutral_social.get("preferredVibes", []):
        errors.append("semantic_bridge: neutral child context must not infer energy-drain preference")

    hallucinated_profile = extractor.normalize_structured_demand(
        {
            **neutral_family,
            "socialIntent": {
                **neutral_social,
                "preferredVibes": [*neutral_social.get("preferredVibes", []), "自然观察"],
                "explicitPreferredVibes": [],
            },
        },
        "周六下午带5岁孩子出去，别太远，预算400。",
    )
    if "自然观察" in hallucinated_profile.get("socialIntent", {}).get("preferredVibes", []):
        errors.append("semantic_bridge: ungrounded LLM profile tag must not enter the scoring profile")

    explicit_nature = extractor.normalize_structured_demand(
        {
            **neutral_family,
            "socialIntent": {
                **neutral_social,
                "preferredVibes": [],
                "explicitPreferredVibes": ["自然观察"],
            },
        },
        "周六下午带5岁孩子出去，想体验自然观察。",
    )
    if "自然观察" not in explicit_nature.get("socialIntent", {}).get("preferredVibes", []):
        errors.append("semantic_bridge: explicit taxonomy-external preference should remain usable")

    energy_family = _base_semantic_test_demand("周六下午带5岁孩子出去放放电，别太远，预算400。")
    energy_social = energy_family.get("socialIntent", {})
    if energy_social.get("subScenario") != "kid_energy_drain":
        errors.append(f"semantic_bridge: explicit 放电 should use kid_energy_drain, got {energy_social.get('subScenario')}")
    if not energy_social.get("subScenarioEvidence"):
        errors.append("semantic_bridge: evidence-gated sub-scenario should expose evidence")

    unsupported_romantic = extractor.normalize_structured_demand(
        {
            **_base_semantic_test_demand("周六和对象吃个饭。"),
            "socialIntent": {
                "primary": "light_date",
                "subScenario": "romantic_step",
                "preferredVibes": [],
                "avoidVibes": [],
                "evidence": ["对象"],
            },
        },
        "周六和对象吃个饭。",
    )
    if unsupported_romantic.get("socialIntent", {}).get("subScenario") != "general":
        errors.append("semantic_bridge: unsupported specific sub-scenario should fall back to safe default")

    explicit = _base_semantic_test_demand("第一次和有好感的女生约会，她特别爱市井大排档烤肉。")
    social = explicit.get("socialIntent", {})
    preferred = set(social.get("preferredVibes", []))
    avoid = set(social.get("avoidVibes", []))
    if "大排档" not in preferred and "市井大排档" not in preferred:
        errors.append("semantic_bridge: explicit 大排档 preference should enter preferredVibes")
    if preferred & avoid:
        errors.append(f"semantic_bridge: preferred/avoid collision should be removed, got {sorted(preferred & avoid)}")
    supply = mock_api.search_supply(explicit)
    restaurant_text = json.dumps(supply.get("restaurantCandidates", [])[:8], ensure_ascii=False)
    if "烤肉" not in restaurant_text and "烧烤" not in restaurant_text:
        errors.append("semantic_bridge: explicit 烤肉/大排档 preference should keep related restaurants in top candidates")

    unknown = _base_semantic_test_demand("周六下午两人去高新吃个饭。")
    unknown["socialIntent"] = {
        "primary": "unknown",
        "subScenario": "unknown",
        "preferredVibes": [],
        "avoidVibes": [],
        "evidence": ["信息较少"],
    }
    unknown["location"]["preferredArea"] = "高新"
    unknown_supply = mock_api.search_supply(unknown)
    unknown_restaurants = unknown_supply.get("restaurantCandidates", [])[:5]
    if any(float(item.get("semanticScoreDelta") or 0) != 0 for item in unknown_restaurants):
        errors.append("semantic_bridge: unknown intent without explicit tags should have zero semantic score")
    if not all("高新" in str(item.get("areaName") or item.get("name") or "") for item in unknown_restaurants[:3]):
        errors.append("semantic_bridge: unknown high-tech meal should rank by area/base quality, not JSON order")

    hard_hotpot = _base_semantic_test_demand("周六晚上就想吃火锅，别的不要。")
    hard_hotpot["preferences"]["foodTags"] = ["火锅"]
    hard_hotpot["constraints"]["hard"] = ["只想吃火锅"]
    soft_hotpot = _base_semantic_test_demand("周六晚上想吃点火锅也行。")
    soft_hotpot["preferences"]["foodTags"] = ["火锅"]
    soft_supply = mock_api.search_supply(soft_hotpot)
    if len(soft_supply.get("restaurantCandidates", [])) <= 1:
        errors.append("semantic_bridge: soft hotpot preference should not clear other feasible restaurants")

    neutral_supply = mock_api.search_supply(neutral_family)
    for candidate in neutral_supply.get("activityCandidates", [])[:5]:
        details = candidate.get("reasonDetails", {})
        explicit_reasons = " ".join(details.get("explicitPreference", []))
        if "自然观察" in explicit_reasons:
            errors.append("semantic_bridge: recommended activity type must not be presented as explicit user preference")
        if "清淡健康" in explicit_reasons:
            errors.append("semantic_bridge: diet preference must not leak into unrelated activity through broad aliases")

    scoped_activity = {
        "id": "scope-test-activity",
        "name": "清淡健康主题活动",
        "category": "activity",
        "tags": ["清淡健康"],
    }
    scoped_restaurant = {
        "id": "scope-test-restaurant",
        "name": "清淡健康餐厅",
        "category": "restaurant",
        "cuisine": "轻食",
        "avgPricePerPerson": 80,
        "tags": ["清淡健康"],
    }
    diet_demand = _base_semantic_test_demand("老婆最近减脂，想吃清淡健康一点。")
    activity_semantic = mock_api.calculate_semantic_score(scoped_activity, diet_demand)
    restaurant_semantic = mock_api.calculate_semantic_score(scoped_restaurant, diet_demand)
    if "清淡健康" in activity_semantic.get("matchedSemanticTags", []):
        errors.append("semantic_bridge: restaurant-only tag must not score an activity")
    if "清淡健康" not in restaurant_semantic.get("matchedSemanticTags", []):
        errors.append("semantic_bridge: restaurant-only tag should still score a restaurant")

    generic_cases = [
        ("周六和对象吃个饭。", "general"),
        ("周六和朋友聚一下。", "general"),
        ("周六找个地方聊聊天。", "general"),
        ("周六和两个朋友想吃点火锅也行。", "general"),
    ]
    for text, expected_sub_scenario in generic_cases:
        actual = _base_semantic_test_demand(text).get("socialIntent", {}).get("subScenario")
        if actual != expected_sub_scenario:
            errors.append(f"semantic_bridge: generic scene should stay general, got {actual} for {text}")

    senior_from_general = extractor.normalize_structured_demand(
        {
            **_base_semantic_test_demand("周末下雨带父母找个不累的地方，少走路少排队。"),
            "socialIntent": {
                "primary": "family_care",
                "subScenario": "general",
                "preferredVibes": [],
                "avoidVibes": [],
                "evidence": ["父母", "少走路"],
            },
        },
        "周末下雨带父母找个不累的地方，少走路少排队。",
    )
    if senior_from_general.get("socialIntent", {}).get("subScenario") != "senior_care":
        errors.append("semantic_bridge: explicit sub-scenario evidence should promote an overly general LLM result")

    incompatible_pair = extractor.normalize_structured_demand(
        {
            **_base_semantic_test_demand("周六下午两人去高新吃个饭。"),
            "socialIntent": {
                "primary": "casual_meetup",
                "subScenario": "unknown",
                "preferredVibes": [],
                "avoidVibes": [],
                "evidence": [],
            },
        },
        "周六下午两人去高新吃个饭。",
    )
    if incompatible_pair.get("socialIntent", {}).get("subScenario") != "casual":
        errors.append("semantic_bridge: incompatible primary/subScenario pair should use the primary default")

    physical_constraint_leak = extractor.normalize_structured_demand(
        {
            **neutral_family,
            "socialIntent": {
                **neutral_social,
                "explicitPreferredVibes": ["老婆减脂，需要清淡低脂", "清淡健康"],
                "explicitAvoidVibes": ["别太远", "预算400", "不要太吵"],
            },
        },
        "周六带孩子出去，别太远，总预算400，不要太吵。",
    )
    leaked_avoids = set(physical_constraint_leak.get("socialIntent", {}).get("explicitAvoidVibes", []))
    if {"别太远", "预算400"} & leaked_avoids:
        errors.append("semantic_bridge: route/budget constraints must not enter the semantic vibe matrix")
    if "高噪高动" not in leaked_avoids:
        errors.append("semantic_bridge: genuine semantic avoid should remain after operational constraint filtering")
    leaked_preferences = set(
        physical_constraint_leak.get("socialIntent", {}).get("explicitPreferredVibes", [])
    )
    if "老婆减脂，需要清淡低脂" in leaked_preferences:
        errors.append("semantic_bridge: free-form sentences must not enter the stable semantic tag matrix")
    if "清淡健康" not in leaked_preferences:
        errors.append("semantic_bridge: canonical semantic tags should remain after registry filtering")

    return errors


def check_stage3_compatibility(examples: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []

    for example in examples:
        example_id = example["id"]
        demand = example["expectedStructuredDemand"]
        try:
            result = mock_api.search_supply(demand)
        except Exception as exc:
            errors.append(f"{example_id}: mock_api.search_supply failed: {exc}")
            continue

        for field in (
            "activityCandidates",
            "restaurantCandidates",
            "routeCandidates",
            "supplyStatus",
            "filteredOut",
            "toolLogs",
        ):
            if field not in result:
                errors.append(f"{example_id}: missing Stage 3 output field {field}")

    return errors


def check_stage3_behavior(examples: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    example_by_id = {example["id"]: example for example in examples}

    skiing = mock_api.search_supply(
        example_by_id["directed_skiing_activity"]["expectedStructuredDemand"]
    )
    non_skiing_candidates = [
        candidate
        for candidate in skiing["activityCandidates"]
        if "滑雪" not in " ".join(
            [
                candidate.get("name", ""),
                candidate.get("category", ""),
                " ".join(candidate.get("matchedReasons", [])),
            ]
        )
    ]
    if non_skiing_candidates:
        names = ", ".join(candidate["name"] for candidate in non_skiing_candidates)
        errors.append(f"directed_skiing_activity: non-skiing activity candidates returned: {names}")
    if skiing["supplyStatus"]["status"] != "failed":
        errors.append("directed_skiing_activity: supplyStatus.status should be failed")
    if not any(
        item.get("dimension") == "activityType" and item.get("value") == "滑雪"
        for item in skiing["supplyStatus"].get("failedConstraints", [])
    ):
        errors.append("directed_skiing_activity: missing failed activityType=滑雪 constraint")

    xianyang = mock_api.search_supply(
        example_by_id["xianyang_to_xian_city_trip"]["expectedStructuredDemand"]
    )
    inbound_routes = [
        route for route in xianyang["routeCandidates"] if route.get("isCrossCityInbound")
    ]
    if not inbound_routes:
        errors.append("xianyang_to_xian_city_trip: missing cross-city inbound route")
    for route in inbound_routes:
        if "estimatedCostPerPerson" not in route or "estimatedCostTotal" not in route:
            errors.append("xianyang_to_xian_city_trip: inbound route missing cost fields")
            break
    routed_candidates = [
        candidate
        for candidate in [*xianyang["activityCandidates"], *xianyang["restaurantCandidates"]]
        if candidate.get("routeSummary")
        and "estimatedRouteCost" in candidate
        and "estimatedTotalCostWithRoute" in candidate
    ]
    if not routed_candidates:
        errors.append("xianyang_to_xian_city_trip: candidates missing route cost signals")

    low_cost = mock_api.search_supply(
        example_by_id["contradictory_low_cost_not_tired"]["expectedStructuredDemand"]
    )
    if not low_cost["activityCandidates"]:
        errors.append("contradictory_low_cost_not_tired: expected low/free activity candidates")
    else:
        top_activity = low_cost["activityCandidates"][0]
        if top_activity["estimatedCost"] > 40 and not any(
            reason in top_activity.get("matchedReasons", [])
            for reason in ("免费活动", "低消费活动", "低预算标签")
        ):
            errors.append(
                "contradictory_low_cost_not_tired: top activity is not free/low-cost"
            )
    if any("预算 0" in item.get("reason", "") for item in low_cost["filteredOut"]):
        errors.append("contradictory_low_cost_not_tired: still filtering against budget 0")

    free_required_demand = {
        "rawInput": "周末下午预算0元，只能免费，想轻松走走。",
        "scene": {"tags": ["独自放松"]},
        "people": {"total": 1, "adults": 1, "children": [], "seniors": []},
        "budget": {"maxTotal": 0, "perPerson": None, "currency": "CNY", "flexibility": "strict"},
        "preferences": {
            "activityTypes": ["轻松"],
            "foodTags": [],
            "experienceTags": ["只能免费"],
            "avoidTags": ["花钱"],
        },
        "constraints": {"hard": ["只能免费"], "soft": ["轻松"], "dynamic": []},
    }
    free_required = mock_api.search_supply(free_required_demand)
    if any(item["estimatedCost"] > 0 for item in free_required["activityCandidates"]):
        errors.append("free_required: activity candidates should be strictly free")
    if any(item["estimatedCost"] > 0 for item in free_required["restaurantCandidates"]):
        errors.append("free_required: restaurant candidates should be strictly free")

    return errors


def check_stage2_normalization() -> list[str]:
    errors: list[str] = []
    result = {
        "rawInput": "周末下午我不想花钱，就想随便走走，但也不想太累。",
        "budget": {
            "maxTotal": 0,
            "perPerson": None,
            "currency": "CNY",
            "flexibility": "strict",
        },
        "preferences": {
            "activityTypes": ["随便走走"],
            "foodTags": [],
            "experienceTags": ["不想花钱"],
            "avoidTags": [],
        },
        "constraints": {
            "hard": ["尽量不花钱"],
            "soft": ["轻松"],
            "dynamic": [],
        },
    }
    normalized = extractor.normalize_structured_demand(result)
    if normalized["budget"]["maxTotal"] is not None:
        errors.append("low-cost normalization: maxTotal should be null, not 0")
    if normalized["budget"]["flexibility"] != "low_cost":
        errors.append("low-cost normalization: flexibility should become low_cost")
    if "低成本" not in normalized["preferences"]["experienceTags"]:
        errors.append("low-cost normalization: missing low-cost experience tag")
    if any("不花钱" in item or "低成本" in item for item in normalized["constraints"]["hard"]):
        errors.append("low-cost normalization: low-cost language should not remain a hard constraint")

    explicit_zero = {
        "rawInput": "周末下午预算0元，只能免费。",
        "budget": {
            "maxTotal": 0,
            "perPerson": None,
            "currency": "CNY",
            "flexibility": "strict",
        },
    }
    normalized_zero = extractor.normalize_structured_demand(explicit_zero)
    if normalized_zero["budget"]["maxTotal"] != 0:
        errors.append("low-cost normalization: explicit zero budget should stay 0")

    child_accompany = {
        "rawInput": "周六下午带5岁孩子在西安玩半天，预算400元以内。",
        "people": {
            "total": None,
            "adults": None,
            "children": [{"age": 5}],
            "seniors": [],
            "relationship": "family",
            "specialNeeds": [],
        },
        "budget": {
            "maxTotal": 400,
            "perPerson": None,
            "currency": "CNY",
            "flexibility": "strict",
        },
    }
    normalized_child = extractor.normalize_structured_demand(child_accompany)
    if normalized_child["people"]["adults"] != 1 or normalized_child["people"]["total"] != 2:
        errors.append("child accompany normalization: 带孩子 should infer 1 adult + child when LLM leaves people null")

    return errors


def check_stage4_planner(examples: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    example_by_id = {example["id"]: example for example in examples}

    for example in examples:
        example_id = example["id"]
        demand = example["expectedStructuredDemand"]
        try:
            supply = mock_api.search_supply(demand)
            timeline_plan = planner.plan_timeline(demand, supply, use_llm=False)
        except Exception as exc:
            errors.append(f"{example_id}: planner.plan_timeline failed: {exc}")
            continue

        validation_errors = planner.validate_timeline_plan(timeline_plan, supply)
        errors.extend(f"{example_id}: {error}" for error in validation_errors)

    skiing_demand = example_by_id["directed_skiing_activity"]["expectedStructuredDemand"]
    skiing_supply = mock_api.search_supply(skiing_demand)
    skiing_plan = planner.plan_timeline(skiing_demand, skiing_supply, use_llm=False)
    if skiing_plan.get("status") != "failed":
        errors.append("directed_skiing_activity: timelinePlan.status should be failed")
    selected_names = " ".join(
        str(item.get("name", "")) for item in skiing_plan.get("selectedItems", [])
    )
    if any(value in selected_names for value in ("展览", "手作", "书房")):
        errors.append("directed_skiing_activity: planner recommended unrelated substitutes")

    xianyang_demand = example_by_id["xianyang_to_xian_city_trip"]["expectedStructuredDemand"]
    xianyang_supply = mock_api.search_supply(xianyang_demand)
    xianyang_plan = planner.plan_timeline(xianyang_demand, xianyang_supply, use_llm=False)
    route_cost = xianyang_plan.get("budgetEstimate", {}).get("routeCost", 0)
    risk_text = " ".join(xianyang_plan.get("riskTips", []))
    if route_cost <= 0 and "跨城" not in risk_text and "路线成本" not in risk_text:
        errors.append("xianyang_to_xian_city_trip: planner should reflect cross-city route cost")
    if _longest_idle_minutes(xianyang_plan) > 45:
        errors.append("xianyang_to_xian_city_trip: planner must not leave a long dinner-wait idle gap")
    if _experience_block_count(xianyang_plan) < 2:
        errors.append("xianyang_to_xian_city_trip: 4-6h local trip should contain at least two experience blocks")
    restaurant_steps = [item for item in xianyang_plan.get("timeline", []) if item.get("type") == "restaurant"]
    if restaurant_steps and (_minutes(restaurant_steps[0].get("start")) or 0) < 17 * 60 + 30:
        errors.append("xianyang_to_xian_city_trip: default dinner should not start before 17:30")

    qindu_demand = example_by_id["xianyang_qindu_low_budget_trip"]["expectedStructuredDemand"]
    qindu_supply = mock_api.search_supply(qindu_demand)
    qindu_plan = planner.plan_timeline(qindu_demand, qindu_supply, use_llm=False)
    if _longest_idle_minutes(qindu_plan) > 45:
        errors.append("xianyang_qindu_low_budget_trip: planner must fill or reject long idle gaps")
    if _experience_block_count(qindu_plan) < 2:
        errors.append("xianyang_qindu_low_budget_trip: planner should add a second experience block before dinner")
    qindu_restaurants = [item for item in qindu_plan.get("timeline", []) if item.get("type") == "restaurant"]
    if qindu_restaurants and (_minutes(qindu_restaurants[0].get("start")) or 0) < 17 * 60 + 30:
        errors.append("xianyang_qindu_low_budget_trip: default dinner should stay anchored at 17:30 or later")

    low_cost_demand = example_by_id["contradictory_low_cost_not_tired"]["expectedStructuredDemand"]
    low_cost_supply = mock_api.search_supply(low_cost_demand)
    low_cost_plan = planner.plan_timeline(low_cost_demand, low_cost_supply, use_llm=False)
    tradeoff_text = " ".join(low_cost_plan.get("tradeoffs", []))
    if ("低成本" not in tradeoff_text and "不想花钱" not in tradeoff_text) or (
        "不想太累" not in tradeoff_text and "走不了太多路" not in tradeoff_text
    ):
        errors.append("contradictory_low_cost_not_tired: planner should explain low-cost vs less-tired tradeoff")

    family_demand = example_by_id["family_half_day"]["expectedStructuredDemand"]
    family_supply = mock_api.search_supply(family_demand)
    family_plan = planner.plan_timeline(family_demand, family_supply, use_llm=False)
    family_budget_limit = family_demand["budget"]["maxTotal"]
    family_total = family_plan.get("budgetEstimate", {}).get("totalCost", 0)
    if family_total > family_budget_limit:
        errors.append(
            f"family_half_day: strict-budget planner should choose a total-cost-feasible pair, got {family_total} > {family_budget_limit}"
        )

    return errors


def check_stage5_validator(examples: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    example_by_id = {example["id"]: example for example in examples}

    for example in examples:
        example_id = example["id"]
        demand = example["expectedStructuredDemand"]
        try:
            supply = mock_api.search_supply(demand)
            timeline_plan = planner.plan_timeline(demand, supply, use_llm=False)
            result = validator.validate_and_replan(demand, supply, timeline_plan)
        except Exception as exc:
            errors.append(f"{example_id}: Stage 5 validator failed: {exc}")
            continue

        validation_result = result.get("validationResult", {})
        if validation_result.get("status") not in validator.VALIDATION_STATUSES:
            errors.append(f"{example_id}: invalid validationResult.status")
        for field in ("issues", "checkedDimensions", "replanNeeded", "suggestedActions"):
            if field not in validation_result:
                errors.append(f"{example_id}: missing validationResult.{field}")
        if example_id == "directed_skiing_activity" and validation_result.get("replanNeeded"):
            errors.append("directed_skiing_activity: Stage 5 should not replan when Stage 3 hard-failed")

    low_cost_demand = example_by_id["contradictory_low_cost_not_tired"]["expectedStructuredDemand"]
    low_cost_supply = mock_api.search_supply(low_cost_demand)
    low_cost_plan = planner.plan_timeline(low_cost_demand, low_cost_supply, use_llm=False)
    low_cost_validation = validator.validate_plan(low_cost_demand, low_cost_supply, low_cost_plan)
    if any(issue.get("code") == "FREE_REQUIRED_COST_FOUND" for issue in low_cost_validation["issues"]):
        errors.append("contradictory_low_cost_not_tired: Stage 5 treated low-cost preference as zero budget")

    free_preferred_demand = {
        "rawInput": "周末下午最好免费，优先免费，但也可以少花钱兜底，想轻松走走。",
        "scene": {"tags": ["独自放松"]},
        "timeWindow": {
            "dateText": "周六",
            "startTime": "14:00",
            "endTime": "18:00",
            "durationHours": 4,
            "isFlexible": True,
        },
        "people": {"total": 1, "adults": 1, "children": [], "seniors": [], "relationship": "solo", "specialNeeds": []},
        "budget": {"maxTotal": None, "perPerson": None, "currency": "CNY", "flexibility": "flexible"},
        "location": {
            "startPoint": None,
            "originPoints": [],
            "preferredArea": None,
            "crossCityIntent": {"enabled": False, "fromCity": None, "toCity": None},
            "maxTravelMinutes": None,
            "transportPreference": None,
            "distancePreference": "nearby",
        },
        "preferences": {
            "activityTypes": ["轻松"],
            "foodTags": [],
            "experienceTags": ["优先免费", "低成本"],
            "avoidTags": [],
        },
        "constraints": {"hard": [], "soft": ["优先免费"], "dynamic": []},
        "potentialConflicts": [],
        "expectedOutput": {"type": "timeline_plan", "mustInclude": [], "niceToHave": [], "assumptions": []},
    }
    free_preferred_supply = mock_api.search_supply(free_preferred_demand)
    free_preferred_plan = planner.plan_timeline(free_preferred_demand, free_preferred_supply, use_llm=False)
    free_preferred_validation = validator.validate_plan(
        free_preferred_demand, free_preferred_supply, free_preferred_plan
    )
    if free_preferred_plan.get("budgetEstimate", {}).get("totalCost", 0) > 0 and not any(
        issue.get("code") == "FREE_PREFERRED_NOT_FULLY_FREE"
        for issue in free_preferred_validation["issues"]
    ):
        errors.append("free_preferred: Stage 5 should warn when fallback is not fully free")

    free_required_demand = {
        **free_preferred_demand,
        "rawInput": "周末下午预算0元，只能免费，想轻松走走。",
        "budget": {"maxTotal": 0, "perPerson": None, "currency": "CNY", "flexibility": "strict"},
        "preferences": {
            "activityTypes": ["轻松"],
            "foodTags": [],
            "experienceTags": ["只能免费"],
            "avoidTags": ["花钱"],
        },
        "constraints": {"hard": ["只能免费"], "soft": ["轻松"], "dynamic": []},
    }
    free_required_supply = mock_api.search_supply(free_required_demand)
    free_required_plan = {
        "status": "ok",
        "summary": "故意构造一个收费方案，验证阶段五能拦住。",
        "timeline": [],
        "selectedItems": [],
        "budgetEstimate": {
            "activityCost": 10,
            "restaurantCost": 0,
            "routeCost": 0,
            "totalCost": 10,
            "perPersonCost": 10,
            "currency": "CNY",
            "notes": [],
        },
        "recommendationReasons": [],
        "riskTips": [],
        "tradeoffs": [],
    }
    free_required_validation = validator.validate_plan(
        free_required_demand, free_required_supply, free_required_plan
    )
    if not any(issue.get("code") == "FREE_REQUIRED_COST_FOUND" for issue in free_required_validation["issues"]):
        errors.append("free_required: Stage 5 should block paid plan for zero/free-required budget")

    return errors


def check_stage6_executor(examples: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    example_by_id = {example["id"]: example for example in examples}

    family = run_flow.run_from_structured_demand(
        example_by_id["family_half_day"]["expectedStructuredDemand"],
        limit=3,
        planner_llm=False,
        strict_planner_llm=False,
    )
    if "executionDraft" not in family:
        errors.append("family_half_day: missing executionDraft")
    if family.get("executionResult", {}).get("executionStatus") != "not_requested":
        errors.append("family_half_day: should not execute without explicit confirmation")
    draft_actions = family.get("executionDraft", {}).get("pendingActions", [])
    draft_text = json.dumps(draft_actions, ensure_ascii=False)
    if any(value in draft_text for value in ("mockTicketCode", "mockReservationCode", "mockQueueNumber")):
        errors.append("family_half_day: execution draft should not contain confirmation codes")
    if family.get("timelinePlan", {}).get("decisionRequired") and family.get("executionDraft", {}).get("draftStatus") != "blocked":
        errors.append("family_half_day: unresolved meal timing decision should block execution draft")

    family_confirmed = run_flow.run_from_structured_demand(
        example_by_id["family_half_day"]["expectedStructuredDemand"],
        limit=3,
        planner_llm=False,
        strict_planner_llm=False,
        confirm_execute=True,
    )
    if family_confirmed.get("executionResult", {}).get("executionStatus") != "blocked":
        errors.append("family_half_day: unresolved meal timing decision should remain blocked when confirmed")

    confirmable = run_flow.run_from_structured_demand(
        example_by_id["couple_date"]["expectedStructuredDemand"],
        limit=3,
        planner_llm=False,
        strict_planner_llm=False,
        confirm_execute=True,
    )
    if confirmable.get("executionResult", {}).get("executionStatus") != "confirmed":
        errors.append("couple_date: confirm_execute should generate confirmed mock result")
    codes = confirmable.get("executionResult", {}).get("confirmationCodes", [])
    if not codes:
        errors.append("couple_date: confirmed execution should contain mock confirmation codes")
    if any(code.get("type") == "deal" for code in codes):
        errors.append("couple_date: deal previews should not auto-generate deal confirmation codes")

    skiing = run_flow.run_from_structured_demand(
        example_by_id["directed_skiing_activity"]["expectedStructuredDemand"],
        limit=3,
        planner_llm=False,
        strict_planner_llm=False,
        confirm_execute=True,
    )
    if skiing.get("executionDraft", {}).get("draftStatus") != "blocked":
        errors.append("directed_skiing_activity: execution draft should be blocked")
    if skiing.get("executionResult", {}).get("executionStatus") != "blocked":
        errors.append("directed_skiing_activity: confirmed execution should remain blocked")

    low_cost = run_flow.run_from_structured_demand(
        example_by_id["contradictory_low_cost_not_tired"]["expectedStructuredDemand"],
        limit=3,
        planner_llm=False,
        strict_planner_llm=False,
    )
    if low_cost.get("executionDraft", {}).get("draftStatus") not in {"ready", "warning"}:
        errors.append("contradictory_low_cost_not_tired: low-cost plan should produce executable draft")

    free_required_demand = {
        "rawInput": "周末下午预算0元，只能免费，想轻松走走。",
        "scene": {"tags": ["独自放松"]},
        "timeWindow": {
            "dateText": "周六",
            "startTime": "14:00",
            "endTime": "18:00",
            "durationHours": 4,
            "isFlexible": True,
        },
        "people": {"total": 1, "adults": 1, "children": [], "seniors": [], "relationship": "solo", "specialNeeds": []},
        "budget": {"maxTotal": 0, "perPerson": None, "currency": "CNY", "flexibility": "strict"},
        "location": {
            "startPoint": None,
            "originPoints": [],
            "preferredArea": None,
            "crossCityIntent": {"enabled": False, "fromCity": None, "toCity": None},
            "maxTravelMinutes": None,
            "transportPreference": None,
            "distancePreference": "nearby",
        },
        "preferences": {
            "activityTypes": ["轻松"],
            "foodTags": [],
            "experienceTags": ["只能免费"],
            "avoidTags": ["花钱"],
        },
        "constraints": {"hard": ["只能免费"], "soft": ["轻松"], "dynamic": []},
        "potentialConflicts": [],
        "expectedOutput": {"type": "timeline_plan", "mustInclude": [], "niceToHave": [], "assumptions": []},
    }
    free_required = run_flow.run_from_structured_demand(
        free_required_demand,
        limit=3,
        planner_llm=False,
        strict_planner_llm=False,
        confirm_execute=True,
    )
    paid_actions = [
        action
        for action in free_required.get("executionDraft", {}).get("pendingActions", [])
        if float(action.get("estimatedCost") or 0) > 0
    ]
    if paid_actions:
        errors.append("free_required: execution draft should not contain paid actions")

    return errors


def check_five_persona_mock_data(examples: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    example_by_id = {example["id"]: example for example in examples}
    persona_ids = [
        "family_qujiang_half_day",
        "friends_zhonglou_citywalk",
        "xianyang_qindu_low_budget_trip",
        "multi_campus_fair_meetup",
        "pursuing_weishui_low_budget",
    ]

    for example_id in persona_ids:
        demand = example_by_id[example_id]["expectedStructuredDemand"]
        try:
            supply = mock_api.search_supply(demand)
        except Exception as exc:
            errors.append(f"{example_id}: search_supply failed: {exc}")
            continue
        if not supply.get("activityCandidates"):
            errors.append(f"{example_id}: expected activity candidates")
        if not supply.get("restaurantCandidates"):
            errors.append(f"{example_id}: expected restaurant candidates")
        if not supply.get("routeCandidates"):
            errors.append(f"{example_id}: expected route candidates")

    family_supply = mock_api.search_supply(
        example_by_id["family_qujiang_half_day"]["expectedStructuredDemand"]
    )
    if not any(candidate.get("poiId") == "res_xa_019" for candidate in family_supply["restaurantCandidates"]):
        errors.append("family_qujiang_half_day: missing low-fat child-friendly restaurant res_xa_019")

    friends_supply = mock_api.search_supply(
        example_by_id["friends_zhonglou_citywalk"]["expectedStructuredDemand"]
    )
    if not any(candidate.get("poiId") == "res_xa_020" for candidate in friends_supply["restaurantCandidates"]):
        errors.append("friends_zhonglou_citywalk: missing seated chat fallback res_xa_020")

    campus_supply = mock_api.search_supply(
        example_by_id["multi_campus_fair_meetup"]["expectedStructuredDemand"]
    )
    route_text = json.dumps(campus_supply.get("routeCandidates", []), ensure_ascii=False)
    for school in ("origin_xa_changan_university", "origin_xa_jiaotong_university", "origin_xa_northwest_university", "origin_xa_shaanxi_normal_university"):
        if school not in route_text:
            errors.append(f"multi_campus_fair_meetup: missing route signal for {school}")

    return errors


def check_mock_density() -> list[str]:
    errors: list[str] = []
    data = mock_api.load_mock_data()
    core_areas = {
        "area_xa_xiaozhai",
        "area_xa_qujiang",
        "area_xa_zhonglou",
        "area_xa_gaoxin",
        "area_xa_daminggong",
        "area_xa_xingzheng",
    }
    activity_counts = Counter(item.get("areaId") for item in data.get("activities", []))
    restaurant_counts = Counter(item.get("areaId") for item in data.get("restaurants", []))
    subarea_counts = Counter(item.get("areaId") for item in data.get("activities", []) if item.get("poiLevel") == "sub_area")
    for area_id in sorted(core_areas):
        if activity_counts[area_id] < 8:
            errors.append(f"mock_density: {area_id} should have at least 8 activities")
        if restaurant_counts[area_id] < 8:
            errors.append(f"mock_density: {area_id} should have at least 8 restaurants")
        if subarea_counts[area_id] < 2:
            errors.append(f"mock_density: {area_id} should have at least 2 open-access subareas")

    activity_availability = {item.get("poiId") for item in data.get("activityAvailability", [])}
    restaurant_availability = {item.get("poiId") for item in data.get("restaurantAvailability", [])}
    missing_activities = [
        item["id"]
        for item in data.get("activities", [])
        if item.get("poiLevel") != "sub_area" and item["id"] not in activity_availability
    ]
    missing_restaurants = [
        item["id"] for item in data.get("restaurants", []) if item["id"] not in restaurant_availability
    ]
    if missing_activities:
        errors.append(f"mock_density: activities missing availability {missing_activities[:5]}")
    if missing_restaurants:
        errors.append(f"mock_density: restaurants missing availability {missing_restaurants[:5]}")

    restaurant_availability_keys = Counter(
        (item.get("poiId"), item.get("dateText"))
        for item in data.get("restaurantAvailability", [])
        if isinstance(item, dict)
    )
    duplicate_availability = [key for key, count in restaurant_availability_keys.items() if count > 1]
    if duplicate_availability:
        errors.append(f"mock_density: duplicate restaurant availability keys {duplicate_availability[:5]}")

    routes = data.get("routes", [])
    area_ids = {item.get("areaId") for item in data.get("areas", [])}
    route_ids = [route.get("routeId") for route in routes]
    if any(not route_id for route_id in route_ids):
        errors.append("mock_density: every route must have a routeId")
    if len(route_ids) != len(set(route_ids)):
        errors.append("mock_density: routeId must be unique")
    missing_route_refs = [
        (route.get("routeId"), key, route.get(key))
        for route in routes
        for key in ("fromAreaId", "toAreaId")
        if route.get(key) not in area_ids
    ]
    if missing_route_refs:
        errors.append(f"mock_density: routes reference missing areas {missing_route_refs[:5]}")

    governed_items = [*data.get("activities", []), *data.get("restaurants", [])]
    missing_governance = [
        item.get("id")
        for item in governed_items
        if not all(item.get(key) for key in ("sourceType", "confidence", "lastVerifiedAt", "factTags", "constraintTags"))
    ]
    if missing_governance:
        errors.append(f"mock_density: POIs missing governance fields {missing_governance[:5]}")
    return errors


def check_runtime_status_pool() -> list[str]:
    errors: list[str] = []
    runtime_status = mock_api.load_runtime_status()
    mock_data = mock_api.load_mock_data()
    activity_ids = {item.get("id") for item in mock_data.get("activities", []) if item.get("poiLevel") != "sub_area"}
    restaurant_ids = {item.get("id") for item in mock_data.get("restaurants", [])}
    activity_records = runtime_status.get("activityRuntimeStatus", [])
    restaurant_records = runtime_status.get("restaurantRuntimeStatus", [])
    activity_runtime_ids = {record.get("poiId") for record in activity_records}
    restaurant_runtime_ids = {record.get("poiId") for record in restaurant_records}
    if activity_runtime_ids != activity_ids:
        errors.append("runtime_status: activity shadow table should be one-to-one with activity POIs")
    if restaurant_runtime_ids != restaurant_ids:
        errors.append("runtime_status: restaurant shadow table should be one-to-one with restaurant POIs")

    poi_records = [
        *runtime_status.get("activityRuntimeStatus", []),
        *runtime_status.get("restaurantRuntimeStatus", []),
    ]
    changed = [
        record
        for record in poi_records
        if record.get("runtimeState") == "changed" or record.get("eventType") not in (None, "none", "unchanged")
    ]
    if not poi_records:
        errors.append("runtime_status: expected records")
    elif not 0.35 <= len(changed) / len(poi_records) <= 0.45:
        errors.append("runtime_status: POI changed records should be roughly 40%")

    normal_draft = {
        "draftStatus": "ready",
        "pendingActions": [
            {
                "timelineIndex": 0,
                "title": "正常活动",
                "poiId": "act_xa_017",
                "actionType": "mock_ticket_lock",
                "runtimeLookup": {"kind": "activity", "poiId": "act_xa_017", "dateText": "周六"},
            },
            {
                "timelineIndex": 1,
                "title": "正常餐厅",
                "poiId": "res_xa_019",
                "actionType": "mock_restaurant_reservation",
                "runtimeLookup": {"kind": "restaurant", "poiId": "res_xa_019", "dateText": "周六"},
            },
        ],
        "alternativeCandidates": {"activities": [], "restaurants": []},
    }
    normal_result = executor.confirm_execution(normal_draft)
    if normal_result.get("executionStatus") != "confirmed":
        errors.append("runtime_status: unchanged runtime should confirm normally")
    if len(normal_result.get("confirmationCodes", [])) != 2:
        errors.append("runtime_status: unchanged runtime should generate confirmation codes")

    abnormal_draft = {
        "draftStatus": "ready",
        "pendingActions": [
            {
                "timelineIndex": 0,
                "title": "售罄活动",
                "poiId": "act_xa_021",
                "actionType": "mock_ticket_lock",
                "runtimeLookup": {"kind": "activity", "poiId": "act_xa_021", "dateText": "周六"},
            },
            {
                "timelineIndex": 1,
                "title": "无座餐厅",
                "poiId": "res_xa_022",
                "actionType": "mock_restaurant_reservation",
                "runtimeLookup": {"kind": "restaurant", "poiId": "res_xa_022", "dateText": "周六"},
                "dealPreview": {"dealId": "deal_xa_016", "stockLeft": 5},
            },
            {
                "timelineIndex": 2,
                "title": "变慢路线",
                "actionType": "route_reminder",
                "routeRef": "origin_xa_weishui_campus->area_xa_xiaozhai",
                "plannedRouteMinutes": 56,
                "runtimeLookup": {"kind": "route", "routeRef": "origin_xa_weishui_campus->area_xa_xiaozhai"},
            },
        ],
        "alternativeCandidates": {
            "activities": [{"poiId": "act_xa_012", "name": "兴善寺东街书房(小寨店)", "kind": "activity"}],
            "restaurants": [{"poiId": "res_xa_019", "name": "曲江禾悦轻食家常菜", "kind": "restaurant"}],
        },
    }
    abnormal_result = executor.confirm_execution(abnormal_draft)
    if abnormal_result.get("executionStatus") not in {"partial", "blocked"}:
        errors.append("runtime_status: abnormal runtime should block or partially block execution")
    runtime_validation = abnormal_result.get("runtimeValidationResult", {})
    if runtime_validation.get("status") != "failed" or not runtime_validation.get("replanNeeded"):
        errors.append("runtime_status: abnormal runtime should request replan")
    issue_codes = {issue.get("code") for issue in runtime_validation.get("issues", [])}
    for code in ("RUNTIME_TICKET_SOLD_OUT", "RUNTIME_TABLE_NOT_AVAILABLE", "RUNTIME_ROUTE_DELAYED", "RUNTIME_DEAL_SOLD_OUT"):
        if code not in issue_codes:
            errors.append(f"runtime_status: missing issue code {code}")
    adjustment = abnormal_result.get("executionAdjustment", {})
    alternatives = adjustment.get("availableAlternativeCandidates", {})
    if not alternatives.get("activities") or not alternatives.get("restaurants"):
        errors.append("runtime_status: abnormal runtime should return available alternatives")

    demand = mock_api.load_example_demand("pursuing_weishui_low_budget")
    supply = mock_api.search_supply(demand)
    old_plan = {
        "status": "ok",
        "summary": "测试用旧方案，包含确认前会变化的活动和餐厅。",
        "timeline": [
            {
                "start": "14:00",
                "end": "14:56",
                "type": "route",
                "title": "入城路线",
                "routeRef": "origin_xa_weishui_campus->area_xa_xiaozhai",
                "estimatedCost": 12,
            },
            {
                "start": "14:56",
                "end": "15:56",
                "type": "activity",
                "title": "小寨轻松话题书店",
                "poiId": "act_xa_021",
                "estimatedCost": 60,
            },
            {
                "start": "16:11",
                "end": "17:26",
                "type": "restaurant",
                "title": "小寨清爽简餐约会店",
                "poiId": "res_xa_022",
                "estimatedCost": 112,
            },
        ],
        "selectedItems": [
            {"kind": "activity", "poiId": "act_xa_021", "name": "小寨轻松话题书店"},
            {"kind": "restaurant", "poiId": "res_xa_022", "name": "小寨清爽简餐约会店"},
        ],
        "budgetEstimate": {
            "activityCost": 60,
            "restaurantCost": 112,
            "routeCost": 12,
            "totalCost": 184,
            "perPersonCost": 92,
        },
        "recommendationReasons": [],
        "riskTips": [],
        "tradeoffs": [],
        "rawPlannerNotes": "test fixture",
    }
    replan_result = executor.confirm_execution(
        abnormal_draft,
        structured_demand=demand,
        timeline_plan=old_plan,
        mock_supply=supply,
        planner_llm=False,
        replan_on_runtime_failure=True,
    )
    if replan_result.get("executionStatus") != "replan_ready":
        errors.append("runtime_status: runtime replan should produce a replan_ready result")
    runtime_replan = replan_result.get("runtimeReplanResult", {})
    replanned_draft = runtime_replan.get("replannedExecutionDraft", {})
    if not runtime_replan.get("replannedFinalPlan", {}).get("timeline"):
        errors.append("runtime_status: runtime replan should return a new timeline")
    if replanned_draft.get("draftStatus") == "blocked":
        errors.append("runtime_status: replanned execution draft should be confirmable")
    if replanned_draft:
        final_confirm = executor.confirm_execution(replanned_draft)
        if final_confirm.get("executionStatus") != "confirmed":
            errors.append("runtime_status: replanned draft should confirm successfully")

    return errors


def run_llm_examples(examples: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    schema = extractor.load_json(extractor.SCHEMA_PATH)

    for example in examples:
        example_id = example["id"]
        prompt = extractor.build_prompt(example["userInput"])
        try:
            response_text = extractor.call_llm(prompt)
            result = extractor.parse_json_object(response_text)
            result = extractor.normalize_structured_demand(result, example["userInput"])
        except Exception as exc:
            errors.append(f"{example_id}: LLM extraction failed: {exc}")
            continue

        validation_errors = extractor.basic_validate(result, schema)
        errors.extend(f"{example_id}: {error}" for error in validation_errors)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-check FlowCity examples.")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Also call the configured LLM for every userInput.",
    )
    args = parser.parse_args()

    examples = load_examples()
    errors: list[str] = []

    errors.extend(validate_expected_examples(examples))
    errors.extend(check_stage2_normalization())
    errors.extend(check_stage3_compatibility(examples))
    errors.extend(check_stage3_behavior(examples))
    errors.extend(check_semantic_bridge_behavior())
    errors.extend(check_stage4_planner(examples))
    errors.extend(check_stage5_validator(examples))
    errors.extend(check_stage6_executor(examples))
    errors.extend(check_five_persona_mock_data(examples))
    errors.extend(check_mock_density())
    errors.extend(check_runtime_status_pool())

    if args.llm:
        errors.extend(run_llm_examples(examples))

    if errors:
        print("FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    mode = "with LLM" if args.llm else "without LLM"
    print(f"OK: {len(examples)} examples passed ({mode}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
