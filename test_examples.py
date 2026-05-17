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
from pathlib import Path
from typing import Any

import extractor
import mock_api
import planner


ROOT = Path(__file__).resolve().parent
EXAMPLES_PATH = ROOT / "examples.json"


def load_examples() -> list[dict[str, Any]]:
    data = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
    return data["examples"]


def validate_expected_examples(examples: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    schema = extractor.load_json(extractor.SCHEMA_PATH)

    for example in examples:
        example_id = example["id"]
        demand = example["expectedStructuredDemand"]
        validation_errors = extractor.basic_validate(demand, schema)
        errors.extend(f"{example_id}: {error}" for error in validation_errors)

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
    if normalized["budget"]["flexibility"] != "flexible":
        errors.append("low-cost normalization: flexibility should become flexible")
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

    low_cost_demand = example_by_id["contradictory_low_cost_not_tired"]["expectedStructuredDemand"]
    low_cost_supply = mock_api.search_supply(low_cost_demand)
    low_cost_plan = planner.plan_timeline(low_cost_demand, low_cost_supply, use_llm=False)
    tradeoff_text = " ".join(low_cost_plan.get("tradeoffs", []))
    if ("低成本" not in tradeoff_text and "不想花钱" not in tradeoff_text) or (
        "不想太累" not in tradeoff_text and "走不了太多路" not in tradeoff_text
    ):
        errors.append("contradictory_low_cost_not_tired: planner should explain low-cost vs less-tired tradeoff")

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
            result = extractor.normalize_structured_demand(result)
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
    errors.extend(check_stage4_planner(examples))

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
