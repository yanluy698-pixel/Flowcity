"""Batch LLM acceptance checks for the FlowCity main path.

This script calls the real Stage 2 LLM extractor, then uses the deterministic
Scheduler path. It is intentionally outside the product path: the goal is to
exercise capabilities without adding demo-specific behavior to the runtime.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

import run_flow


HOME_CASES = {
    "home_family_qujiang": "周六下午2点到6点，我从曲江池附近出发，带5岁孩子和老婆出去玩，老婆最近减脂，别太远，总预算400，最好6点前能回到家附近。",
    "home_zhonglou_citywalk": "今晚6点半我们4个人在钟楼地铁站集合，2男2女，想citywalk加小吃，但别走太多路，最后找个地方坐下来聊，10点前结束。",
    "home_xianyang_city_trip": "周天1点到7点，我们3个男生从咸阳秦都站附近出发，坐地铁去西安市区玩，人均100以内。",
    "home_multi_origin_students": "我们4个大学生周六中午想出来玩，分别从长安大学、西安交大、西北大学和陕师大出发，最低预算的同学只能接受人均80，希望找个对大家都公平的地方集合，再安排吃和玩。",
    "home_light_date_diet": "周六下午2点到6点，我从长安大学渭水校区出发，想去市区和喜欢的女生玩，她最近减肥，吃的要好吃但别油腻，总预算200以内，别安排得太正式。",
}


CAPABILITY_GROUPS = [
    {
        "capability": "door_to_door_cross_city_multi_node",
        "expect": {"route": True, "multi_block": True, "budget": True},
        "cases": [
            ("home_xianyang_city_trip", HOME_CASES["home_xianyang_city_trip"]),
            ("xianyang_students_budget", "周六1点到7点，我们3个大学生从咸阳秦都站出发，坐地铁去西安市区玩，人均100以内，别一直等晚饭。"),
            ("xianyang_low_cost_evening", "周天13:30到19:00，三个人从咸阳白马河附近出发，想进西安逛逛再吃饭，人均90以内。"),
        ],
    },
    {
        "capability": "explicit_meetup_onsite",
        "expect": {"onsite": True, "multi_block": True},
        "cases": [
            ("home_zhonglou_citywalk", HOME_CASES["home_zhonglou_citywalk"]),
            ("bell_tower_students_meet", "我们4个大学生中午一点半在钟楼集合，晚上一起吃饭，8:30各自回校。"),
            ("xiaozhai_meetup_walk", "周六下午3点我们在小寨地铁站见面，想轻松逛一逛再吃饭，晚上8点前结束。"),
        ],
    },
    {
        "capability": "multi_origin_fair_meetup",
        "expect": {"multi_origin": True, "budget": True},
        "cases": [
            ("home_multi_origin_students", HOME_CASES["home_multi_origin_students"]),
            ("four_campuses_fair", "四个人分别从长安大学、西北大学、西安交大和陕师大出发，周六中午出来玩，预算人均80，希望集合点公平。"),
            ("multi_origin_low_budget", "我们从西电、交大、陕师大和西北大学各自出发，下午1点到晚上7点，最低预算人均80，找个公平地方吃和玩。"),
        ],
    },
    {
        "capability": "family_slow_return",
        "expect": {"route": True, "family": True},
        "cases": [
            ("home_family_qujiang", HOME_CASES["home_family_qujiang"]),
            ("family_home_return", "周末带孩子从家出发玩半天，晚上回家，别走太多路，总预算300。"),
            ("family_diet_return", "周六下午一家三口从曲江池附近出发，孩子5岁，老婆减脂，想玩到6点前回家附近。"),
        ],
    },
    {
        "capability": "budget_control_and_low_cost_exception",
        "expect": {"budget": True},
        "cases": [
            ("low_cost_simple", "预算越低越好，少走路，简单逛逛吃饭，人均80以内。"),
            ("student_low_budget", "三个大学生周六下午想出来玩加吃饭，人均70以内，别安排贵的。"),
            ("xianyang_budget_ceiling", "周天1点到7点，三个人从咸阳秦都站坐地铁去西安市区玩，人均100封顶。"),
        ],
    },
    {
        "capability": "relationship_diet_profile",
        "expect": {"budget": True},
        "cases": [
            ("home_light_date_diet", HOME_CASES["home_light_date_diet"]),
            ("ambiguous_date_light_food", "周六下午想和喜欢的女生去市区玩，她最近减脂，吃的清淡点，总预算220，不要太正式。"),
            ("date_not_formal", "下午2点到6点，我和有好感的女生从大学城出发，想自然一点玩，晚饭别油腻，人均100以内。"),
        ],
    },
    {
        "capability": "simple_meal_exception",
        "expect": {"simple": True, "budget": True},
        "cases": [
            ("just_dinner_gaoxin", "周六下午两人去高新吃个饭，人均120以内，不用安排太满。"),
            ("simple_meet_dinner", "今晚7点两个人在行政中心附近简单吃饭聊聊，别安排活动。"),
            ("short_low_burden", "下午5点到7点半，三个人只想少走路吃顿饭，预算越低越好。"),
        ],
    },
]


def _parse_minutes(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _budget_limit(demand: dict[str, Any]) -> float | None:
    budget = demand.get("budget") if isinstance(demand.get("budget"), dict) else {}
    people = demand.get("people") if isinstance(demand.get("people"), dict) else {}
    total = budget.get("maxTotal")
    if isinstance(total, (int, float)) and total > 0:
        return float(total)
    per_person = budget.get("perPerson")
    people_total = people.get("total") if isinstance(people.get("total"), int) else 1
    if isinstance(per_person, (int, float)) and per_person > 0:
        return float(per_person) * max(1, int(people_total))
    return None


def _timeline_text(timeline: list[dict[str, Any]]) -> str:
    return " | ".join(
        f"{step.get('start')}-{step.get('end')} {step.get('type')} {step.get('title')}"
        for step in timeline
    )


def _structured_snapshot(demand: dict[str, Any]) -> dict[str, Any]:
    profile = demand.get("demandProfile") if isinstance(demand.get("demandProfile"), dict) else {}
    return {
        "timeWindow": demand.get("timeWindow"),
        "people": demand.get("people"),
        "budget": demand.get("budget"),
        "location": demand.get("location"),
        "planningPolicy": demand.get("planningPolicy"),
        "requestedComponents": profile.get("requestedComponents"),
        "destinationAnchors": profile.get("destinationAnchors"),
        "dimensions": profile.get("dimensions"),
    }


def _validate_result(result: dict[str, Any], expect: dict[str, bool]) -> list[str]:
    issues: list[str] = []
    demand = result.get("structuredDemand", {})
    plan = result.get("timelinePlan", {})
    validation = result.get("validationResult", {})
    policy = plan.get("planningPolicyApplied") or demand.get("planningPolicy") or {}
    metrics = plan.get("qualityMetrics") or {}
    timeline = plan.get("timeline") if isinstance(plan.get("timeline"), list) else []
    budget = plan.get("budgetEstimate") if isinstance(plan.get("budgetEstimate"), dict) else {}

    if plan.get("status") != "ok":
        issues.append(f"plan status is {plan.get('status')}")
    if validation.get("status") not in {"pass", "ok", None}:
        issues.append(f"validation status is {validation.get('status')}")
    for key in ("timeScope", "startAnchorType", "targetExperienceBlocks", "maxIdleMinutes"):
        if key not in policy:
            issues.append(f"planningPolicy missing {key}")

    max_idle = int(policy.get("maxIdleMinutes") or 45)
    longest_idle = int(metrics.get("longestIdleMinutes") or 0)
    if not expect.get("simple") and longest_idle > max_idle:
        issues.append(f"longest idle {longest_idle} > max idle {max_idle}")

    if expect.get("multi_block") and int(metrics.get("experienceBlockCount") or 0) < 2:
        issues.append(f"experience blocks {metrics.get('experienceBlockCount')} < 2")
    if expect.get("route") and not any(step.get("type") == "route" for step in timeline):
        issues.append("expected outbound route step")
    if expect.get("onsite") and policy.get("includeOutboundRoute") is True:
        issues.append("onsite meetup unexpectedly includes outbound route")
    if expect.get("multi_origin") and not any(step.get("type") == "multi_origin_route" for step in timeline):
        issues.append("expected multi-origin route step")

    if expect.get("budget"):
        limit = _budget_limit(demand)
        total = budget.get("totalCost")
        if isinstance(limit, (int, float)) and isinstance(total, (int, float)) and float(total) > float(limit) + 0.01:
            issues.append(f"budget total {total} > limit {limit}")

    if expect.get("simple") and int(metrics.get("experienceBlockCount") or 0) > 1:
        issues.append(f"simple case over-planned {metrics.get('experienceBlockCount')} experience blocks")

    return issues


def run_case(case_id: str, capability: str, text: str, expect: dict[str, bool], limit: int) -> dict[str, Any]:
    started = time.perf_counter()
    result = run_flow.run_from_natural_language(
        text,
        limit=limit,
        planner_llm=False,
        strict_planner_llm=False,
        confirm_execute=False,
    )
    elapsed = round(time.perf_counter() - started, 2)
    plan = result.get("timelinePlan", {})
    demand = result.get("structuredDemand", {})
    issues = _validate_result(result, expect)
    return {
        "caseId": case_id,
        "capability": capability,
        "ok": not issues,
        "issues": issues,
        "elapsedSeconds": elapsed,
        "input": text,
        "summary": plan.get("summary"),
        "planningPolicy": plan.get("planningPolicyApplied") or demand.get("planningPolicy"),
        "structuredSnapshot": _structured_snapshot(demand),
        "qualityMetrics": plan.get("qualityMetrics"),
        "budgetEstimate": plan.get("budgetEstimate"),
        "riskTips": plan.get("riskTips"),
        "recommendationReasons": plan.get("recommendationReasons"),
        "validationStatus": result.get("validationResult", {}).get("status"),
        "validationIssues": result.get("validationResult", {}).get("issues"),
        "timeline": _timeline_text(plan.get("timeline") or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FlowCity LLM capability acceptance checks.")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "flowcity_llm_capability_eval_summary.json",
    )
    parser.add_argument("--case-id", help="Run only one case id.")
    parser.add_argument("--capability", help="Run only one capability group.")
    parser.add_argument("--stop-on-fail", action="store_true")
    args = parser.parse_args()

    results: list[dict[str, Any]] = []
    total_cases = sum(len(group["cases"]) for group in CAPABILITY_GROUPS)
    print(f"[FlowCity][Eval] running {total_cases} LLM cases")
    for group in CAPABILITY_GROUPS:
        capability = str(group["capability"])
        if args.capability and args.capability != capability:
            continue
        expect = dict(group["expect"])
        for case_id, text in group["cases"]:
            if args.case_id and args.case_id != case_id:
                continue
            print(f"[FlowCity][Eval] start {capability}/{case_id}", flush=True)
            try:
                record = run_case(str(case_id), capability, str(text), expect, args.limit)
            except Exception as exc:  # noqa: BLE001 - eval script should record all failures.
                record = {
                    "caseId": str(case_id),
                    "capability": capability,
                    "ok": False,
                    "issues": [f"{type(exc).__name__}: {exc}"],
                    "input": str(text),
                }
            results.append(record)
            status = "OK" if record["ok"] else "FAIL"
            print(f"[FlowCity][Eval] {status} {capability}/{case_id}: {record.get('issues') or record.get('summary')}", flush=True)
            if args.stop_on_fail and not record["ok"]:
                break

    capability_summary: dict[str, dict[str, int]] = {}
    for record in results:
        item = capability_summary.setdefault(record["capability"], {"total": 0, "ok": 0})
        item["total"] += 1
        if record["ok"]:
            item["ok"] += 1

    homepage_ids = set(HOME_CASES)
    homepage_summary = {
        case_id: next((record["ok"] for record in results if record["caseId"] == case_id), False)
        for case_id in homepage_ids
    }
    payload = {
        "total": len(results),
        "ok": sum(1 for record in results if record["ok"]),
        "failed": [record for record in results if not record["ok"]],
        "capabilities": capability_summary,
        "homepage": homepage_summary,
        "results": results,
    }
    output_path = args.output
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except PermissionError:
        output_path = Path(tempfile.gettempdir()) / "flowcity_llm_capability_eval_summary.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[FlowCity][Eval] saved {output_path}")
    print(json.dumps({k: payload[k] for k in ("total", "ok", "capabilities", "homepage")}, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] == payload["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
