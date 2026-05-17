"""
FlowCity local pipeline runner.

Natural language input -> Stage 2 extractor -> Stage 3 mock supply search
-> Stage 4 timeline planner.

This script is the glue layer. Stage 2 still lives in extractor.py, and Stage 3
still lives in mock_api.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import extractor
import mock_api
import planner


def _limit_supply(result: dict[str, Any], limit: int) -> dict[str, Any]:
    if limit < 0:
        return result
    limited = dict(result)
    limited["activityCandidates"] = result["activityCandidates"][:limit]
    limited["restaurantCandidates"] = result["restaurantCandidates"][:limit]
    limited["routeCandidates"] = result["routeCandidates"][:limit]
    return limited


def _save_json(path: Path | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_from_natural_language(
    user_input: str,
    limit: int,
    planner_llm: bool,
    strict_planner_llm: bool,
) -> dict[str, Any]:
    prompt = extractor.build_prompt(user_input)
    response_text = extractor.call_llm(prompt)
    structured_demand = extractor.parse_json_object(response_text)
    structured_demand = extractor.normalize_structured_demand(structured_demand)
    validation_errors = extractor.basic_validate(
        structured_demand, extractor.load_json(extractor.SCHEMA_PATH)
    )
    if validation_errors:
        raise ValueError("Stage 2 validation failed: " + "; ".join(validation_errors))

    full_supply = mock_api.search_supply(structured_demand)
    mock_supply = _limit_supply(full_supply, limit)
    timeline_plan = planner.plan_timeline(
        structured_demand,
        full_supply,
        use_llm=planner_llm,
        fallback_on_error=not strict_planner_llm,
        limit=max(limit, 1),
    )
    return {
        "input": user_input,
        "structuredDemand": structured_demand,
        "mockSupply": mock_supply,
        "timelinePlan": timeline_plan,
    }


def run_from_structured_demand(
    structured_demand: dict[str, Any],
    limit: int,
    planner_llm: bool,
    strict_planner_llm: bool,
) -> dict[str, Any]:
    full_supply = mock_api.search_supply(structured_demand)
    mock_supply = _limit_supply(full_supply, limit)
    timeline_plan = planner.plan_timeline(
        structured_demand,
        full_supply,
        use_llm=planner_llm,
        fallback_on_error=not strict_planner_llm,
        limit=max(limit, 1),
    )
    return {
        "input": structured_demand.get("rawInput"),
        "structuredDemand": structured_demand,
        "mockSupply": mock_supply,
        "timelinePlan": timeline_plan,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run FlowCity Stage 2 + Stage 3 + Stage 4 in one command."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Natural-language user demand. Calls the LLM.")
    source.add_argument(
        "--structured-demand",
        type=Path,
        help="Path to an existing structured demand JSON. Skips the LLM.",
    )
    source.add_argument(
        "--example-id",
        help="Use expectedStructuredDemand from examples.json. Skips the LLM.",
    )
    parser.add_argument("--limit", type=int, default=3, help="Limit candidates per type.")
    parser.add_argument(
        "--planner-llm",
        action="store_true",
        help="Call the Stage 4 LLM Planner. Without this flag, run the bounded offline draft planner.",
    )
    parser.add_argument(
        "--strict-planner-llm",
        action="store_true",
        help="Fail instead of falling back if the Stage 4 LLM call or validation fails.",
    )
    parser.add_argument(
        "--save-structured",
        type=Path,
        help="Optional path to save the Stage 2 structured demand JSON.",
    )
    parser.add_argument(
        "--save-supply",
        type=Path,
        help="Optional path to save the Stage 3 mock supply JSON.",
    )
    parser.add_argument(
        "--save-plan",
        type=Path,
        help="Optional path to save the Stage 4 timeline plan JSON.",
    )
    args = parser.parse_args()

    try:
        if args.input:
            result = run_from_natural_language(
                args.input,
                args.limit,
                args.planner_llm,
                args.strict_planner_llm,
            )
        elif args.structured_demand:
            demand = mock_api.load_demand_from_file(args.structured_demand)
            result = run_from_structured_demand(
                demand,
                args.limit,
                args.planner_llm,
                args.strict_planner_llm,
            )
        else:
            demand = mock_api.load_example_demand(args.example_id)
            result = run_from_structured_demand(
                demand,
                args.limit,
                args.planner_llm,
                args.strict_planner_llm,
            )
    except Exception as exc:
        print(f"FlowCity pipeline failed: {exc}", file=sys.stderr)
        return 1

    _save_json(args.save_structured, result["structuredDemand"])
    _save_json(args.save_supply, result["mockSupply"])
    _save_json(args.save_plan, result["timelinePlan"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
