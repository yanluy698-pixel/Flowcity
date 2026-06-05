"""Focused regression tests for the FlowCity v5 architecture."""

from __future__ import annotations

import tempfile
from pathlib import Path

import area_retrieval
import demand_profile
import extractor
import learning_events
import mock_api
import ontology_evolution
import executor
import planner
from poi_profiles import build_poi_profile


def demand_from_text(text: str) -> dict:
    return extractor.normalize_structured_demand(extractor._repair_sparse_result({}, text), text)


def record_hypothesis_session(
    store: learning_events.LearningEventStore,
    *,
    session: str,
    hypothesis_id: str,
    cluster_key: str,
    text: str,
    outcome: str,
) -> None:
    for event_type in ("hypothesis_created", "hypothesis_shown"):
        store.record(
            event_type,
            session_id=session,
            hypothesis_id=hypothesis_id,
            cluster_key=cluster_key,
            payload={"text": text},
        )
    store.record(
        outcome,
        session_id=session,
        hypothesis_id=hypothesis_id,
        cluster_key=cluster_key,
        payload={},
    )


def run() -> list[str]:
    errors: list[str] = []

    anchored = demand_from_text("周六下午想去西安大雁塔玩，从咸阳秦都站附近出发，预算400。")
    anchored["location"]["startPoint"] = "咸阳秦都站附近"
    anchored["location"]["preferredArea"] = "大雁塔"
    anchored["timeWindow"].update({"startTime": "14:00", "endTime": "16:00", "durationHours": 2})
    demand_profile.ensure_demand_profile(anchored)
    anchors = anchored["demandProfile"]["destinationAnchors"]
    if not anchors or anchors[0].get("name") != "大雁塔":
        errors.append("destination anchor: explicit 大雁塔 should be preserved as the specific anchor")
    data = mock_api.load_mock_data()
    area_result = area_retrieval.recall_areas(anchored, data)
    if "area_xa_qujiang" not in area_result["selectedAreaIds"]:
        errors.append("destination anchor: protected destination area must enter selected areas")
    if not area_result["anchorConflicts"]:
        errors.append("destination anchor: impossible two-hour cross-city trip should produce guided conflict")

    origin_only = demand_from_text("周六下午从曲江池附近出发，带孩子吃个饭，别太远。")
    if demand_profile.protected_area_ids(origin_only):
        errors.append("destination anchor: an origin mention must not be silently promoted to a destination anchor")

    open_demand = demand_from_text("和刚认识的人找个有事情做、边玩边聊、不会冷场的地方。")
    supply = mock_api.search_supply(open_demand)
    if not open_demand["demandProfile"]["openHypotheses"]:
        errors.append("open hypothesis: fuzzy icebreaking need should create an open hypothesis")
    if supply["vectorRecallResult"]["provider"] not in {"hashed-fallback", "fastembed:BAAI/bge-small-zh-v1.5"}:
        errors.append("vector retrieval: provider should be reported")
    selected_area_ids = set(supply["areaRecallResult"]["selectedAreaIds"])
    if any(item["areaId"] not in selected_area_ids for item in supply["activityCandidates"] + supply["restaurantCandidates"]):
        errors.append("progressive recall: POI candidates must only come from selected areas")

    unknown = demand_from_text("周六下午两人去高新吃个饭。")
    unknown_supply = mock_api.search_supply(unknown)
    if unknown["demandProfile"]["dimensions"]:
        errors.append("unknown fallback: plain request must not receive invented default semantic dimensions")
    if any(float(item.get("demandMatchScore") or 0) != 0 for item in unknown_supply["restaurantCandidates"]):
        errors.append("unknown fallback: semantic matrix score should remain zero without evidence")
    unknown_scores = {float(item.get("score") or 0) for item in unknown_supply["restaurantCandidates"]}
    if len(unknown_scores) <= 1:
        errors.append("unknown fallback: Top-K still needs quality/constraint scores instead of JSON order")
    unknown_plan = planner.plan_timeline(unknown, unknown_supply, use_llm=False, limit=8)
    if any(item.get("kind") == "activity" for item in unknown_plan.get("selectedItems", [])):
        errors.append("requested components: a clear meal-only request must not invent an activity")

    hotpot = demand_from_text("周六下午两人去高新，改成吃火锅，人均120。")
    hotpot_supply = mock_api.search_supply(hotpot)
    if hotpot_supply["explicitPreferenceCoverage"]["blockingConflicts"]:
        errors.append("explicit preference: available high-tech hotpot should satisfy the requested change")
    elif "火锅" not in str(hotpot_supply["restaurantCandidates"][0].get("name")):
        errors.append("explicit preference: available requested cuisine should lead the restaurant ranking")

    unavailable_required = demand_from_text("周六下午两人去高新，只想吃大排档，别的不要。")
    unavailable_supply = mock_api.search_supply(unavailable_required)
    if not unavailable_supply["explicitPreferenceCoverage"]["blockingConflicts"]:
        errors.append("explicit preference: unmet required-area cuisine must trigger guided conflict instead of silent replacement")

    quiet = build_poi_profile({"name": "安静咖啡", "cuisine": "cafe_meal", "tags": ["安静", "聊天"]})
    lively = build_poi_profile({"name": "热闹烤肉", "cuisine": "barbecue", "tags": ["多人", "互动"]})
    if quiet["conversationFriendly"] <= lively["conversationFriendly"]:
        errors.append("base profile: conversation-friendly cafe should score above lively barbecue")
    if lively["noiseLevel"] <= quiet["noiseLevel"]:
        errors.append("base profile: barbecue should carry higher factual noise than quiet cafe")

    runtime_supply, runtime_strategy = executor._refresh_supply_for_runtime_replan(
        open_demand,
        supply,
        mock_api.load_runtime_status(),
    )
    if runtime_strategy != "fresh_fixed_pipeline_recall" or "areaRecallResult" not in runtime_supply:
        errors.append("runtime replan: must re-enter the same fixed recall pipeline before fallback")

    with tempfile.TemporaryDirectory() as temp_dir:
        store = learning_events.LearningEventStore(Path(temp_dir) / "learning.sqlite3")
        for index in range(20):
            record_hypothesis_session(
                store,
                session=f"positive-{index}",
                hypothesis_id="hyp_shared_task",
                cluster_key="shared_task_icebreaking",
                text="通过轻度共同任务减少聊天冷场" if index % 2 == 0 else "通过轻度共同任务来减少聊天冷场",
                outcome="plan_confirmed",
            )
            record_hypothesis_session(
                store,
                session=f"negative-{index}",
                hypothesis_id="hyp_forced_photo",
                cluster_key="forced_photo",
                text="通过强制拍照任务减少冷场",
                outcome="hypothesis_deleted",
            )
            record_hypothesis_session(
                store,
                session=f"mixed-{index}",
                hypothesis_id="hyp_lively_music",
                cluster_key="lively_music",
                text="用热闹音乐帮助聊天",
                outcome="plan_confirmed" if index % 2 == 0 else "hypothesis_deleted",
            )
        evolution = ontology_evolution.analyze(store)
        reports = {item["clusterKey"]: item for item in evolution["clusters"]}
        if not evolution["proposals"]:
            errors.append("ontology evolution: stable positive hypothesis cluster should generate a review proposal")
        else:
            proposal = next(
                (item for item in evolution["proposals"] if item["clusterKey"] == "shared_task_icebreaking"),
                evolution["proposals"][0],
            )
            proposal_id = proposal["proposalId"]
            if not store.review_proposal(proposal_id, "approved"):
                errors.append("ontology evolution: generated proposals must support human review")
            elif store.proposals("approved")[0]["proposal_id"] != proposal_id:
                errors.append("ontology evolution: approved proposal status must be auditable")
            elif not ontology_evolution.approved_hypothesis_matches(
                "通过轻度共同任务减少聊天时的冷场",
                store=store,
            ):
                errors.append("ontology evolution: approved pattern should generalize to an unseen paraphrase")
        if reports.get("forced_photo", {}).get("status") != "negative_pattern_blocked":
            errors.append("ontology evolution: high-delete negative pattern must be blocked")
        if reports.get("lively_music", {}).get("status") != "mixed_signal_observing":
            errors.append("ontology evolution: split feedback must remain observing")
        store.record(
            "privacy_check",
            session_id="private",
            payload={"nested": {"rawInput": "不应保存", "safe": "可保存"}},
        )
        if "rawInput" in store.events("privacy_check")[0]["payload"]["nested"]:
            errors.append("learning privacy: nested raw input must be removed")

    return errors


if __name__ == "__main__":
    failures = run()
    if failures:
        print("FAILED")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("OK: FlowCity v5 architecture checks passed")
