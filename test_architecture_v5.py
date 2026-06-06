"""Focused regression tests for the FlowCity v5 architecture."""

from __future__ import annotations

import tempfile
import os
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import area_retrieval
import demand_profile
import extractor
import learning_events
import mock_api
import ontology_evolution
import executor
import planner
from app.services import pipeline
import app.main
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

    drifted_scene = extractor.normalize_structured_demand(
        {
            "rawInput": "周六下午2点到4点，从咸阳出发，必须去大雁塔逛一圈，还想吃饭，预算200。",
            "scene": {"primaryType": "tourist_sightseeing", "confidence": 0.9, "tags": ["游客", "地标"]},
            "socialIntent": {"primary": "tourist_sightseeing", "preferredVibes": [], "avoidVibes": [], "evidence": ["大雁塔"]},
            "timeWindow": {"dateText": "周六", "startTime": "14:00", "endTime": "16:00", "durationHours": 2, "isFlexible": False},
            "people": {"total": None, "adults": None, "children": [], "seniors": [], "relationship": None, "specialNeeds": []},
            "budget": {"maxTotal": 200, "perPerson": None, "flexibility": "strict"},
            "location": {"startPoint": "咸阳", "preferredArea": "大雁塔", "distancePreference": None, "transportPreference": "public_transport"},
            "preferences": {"activityTypes": [], "foodTags": [], "experienceTags": [], "avoidTags": []},
            "constraints": {"hard": [], "soft": []},
            "potentialConflicts": [],
            "expectedOutput": {"planFormat": "timeline_plan", "mustInclude": ["时间轴", "预算估算"]},
            "assumptions": [],
            "clarificationQuestions": [],
        },
        "周六下午2点到4点，从咸阳出发，必须去大雁塔逛一圈，还想吃饭，预算200。",
    )
    if drifted_scene["scene"]["primaryType"] not in {"family", "couple", "friends", "solo", "elderly", "open"}:
        errors.append("schema drift: scene.primaryType must normalize social-intent enum values")
    schema_errors = extractor.basic_validate(drifted_scene, extractor.load_json(extractor.SCHEMA_PATH))
    if schema_errors:
        errors.append(f"schema drift: normalized tourist scene should validate, got {schema_errors[:2]}")

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

    current_plan = {
        "selectedItems": [
            {"kind": "activity", "poiId": "act_keep", "name": "原活动"},
            {"kind": "restaurant", "poiId": "res_old", "name": "原餐厅"},
        ]
    }
    local_router_result = {
        "mode": "new_plan",
        "actionFlags": {key: False for key in pipeline.router.ACTION_FLAG_KEYS},
        "locks": {"timeFlexMinutes": 30},
        "constraintsPatch": {},
        "fallbackMode": "none",
        "clarificationQuestion": None,
        "rawInput": "这个吃的地方不太行",
    }
    routed_restaurant = pipeline._router_result_from_llm(
        llm_data={
            "mode": "refine",
            "targetKind": "restaurant",
            "operation": "replace",
            "confidence": 0.91,
            "evidence": ["吃的地方不太行"],
            "preserve": ["activity"],
            "constraintsPatch": {"foodPreference": "更适合聊天"},
        },
        local_router_result=local_router_result,
        current_plan=current_plan,
        raw_input="这个吃的地方不太行",
    )
    if not routed_restaurant["actionFlags"]["needNewRestaurant"]:
        errors.append("LLM router: freeform meal complaint should release restaurant search")
    if routed_restaurant["actionFlags"]["needNewActivity"]:
        errors.append("LLM router: restaurant-only follow-up must not release the activity")
    if routed_restaurant["locks"].get("activityPoiId") != "act_keep":
        errors.append("LLM router: restaurant-only follow-up should lock the existing activity")
    if routed_restaurant["locks"].get("restaurantPoiId"):
        errors.append("LLM router: restaurant replacement should not keep the old restaurant lock")

    routed_whole = pipeline._router_result_from_llm(
        llm_data={
            "mode": "refine",
            "targetKind": "whole_plan",
            "operation": "major_replan",
            "confidence": 0.88,
            "evidence": ["整体换个思路"],
            "preserve": ["budget", "timeWindow"],
            "constraintsPatch": {},
        },
        local_router_result=local_router_result,
        current_plan=current_plan,
        raw_input="整体换个思路重新来",
    )
    if not (
        routed_whole["actionFlags"]["needNewActivity"]
        and routed_whole["actionFlags"]["needNewRestaurant"]
        and routed_whole["actionFlags"]["needRouteRefresh"]
    ):
        errors.append("LLM router: whole-plan change should release activity, restaurant and route")
    if routed_whole["locks"].get("activityPoiId") or routed_whole["locks"].get("restaurantPoiId"):
        errors.append("LLM router: whole-plan change should not keep old POI locks")

    overlap_session = {
        "currentPlan": {
            "selectedItems": [
                {"kind": "activity", "poiId": "act_old", "name": "大雁塔北广场"},
                {"kind": "restaurant", "poiId": "res_hotpot", "name": "高新云炉鲜切火锅"},
            ]
        }
    }
    overlap_demand = {"planControl": {}}
    pipeline._exclude_previous_plan_for_major_change(
        overlap_demand,
        overlap_session,
        followup_text="整体大改，改成火锅局，别排队太久。",
    )
    if "res_hotpot" in overlap_demand["planControl"].get("excludedPoiIds", []):
        errors.append("major change: already-matching explicit preference should not be excluded")
    if "act_old" not in overlap_demand["planControl"].get("excludedPoiIds", []):
        errors.append("major change: unrelated old activity should still be excluded")
    negated_demand = {"planControl": {}}
    pipeline._exclude_previous_plan_for_major_change(
        negated_demand,
        overlap_session,
        followup_text="整体大改，不要火锅，换个别的吃。",
    )
    if "res_hotpot" not in negated_demand["planControl"].get("excludedPoiIds", []):
        errors.append("major change: negated current preference should still be excluded")

    previous_admin_token = os.environ.pop("FLOWCITY_ADMIN_TOKEN", None)
    try:
        admin_disabled_routes = [route.path for route in app.main.create_app().routes]
        if any(path.startswith("/api/learning") for path in admin_disabled_routes):
            errors.append("learning admin: routes must stay disabled unless FLOWCITY_ADMIN_TOKEN is configured")
        if any(path.startswith("/api/admin") for path in admin_disabled_routes):
            errors.append("admin console: data routes must stay disabled unless FLOWCITY_ADMIN_TOKEN is configured")
        os.environ["FLOWCITY_ADMIN_TOKEN"] = "architecture-test-token"
        admin_enabled_routes = [route.path for route in app.main.create_app().routes]
        if "/api/admin/datasets" not in admin_enabled_routes:
            errors.append("admin console: dataset route should mount when FLOWCITY_ADMIN_TOKEN is configured")
        if "/api/learning/analysis" not in admin_enabled_routes:
            errors.append("learning admin: analysis route should mount when FLOWCITY_ADMIN_TOKEN is configured")
    finally:
        if previous_admin_token is not None:
            os.environ["FLOWCITY_ADMIN_TOKEN"] = previous_admin_token
        else:
            os.environ.pop("FLOWCITY_ADMIN_TOKEN", None)

    session_id = "test_session_security"
    plan_id = "plan_security"
    pipeline.SESSION_STORE.pop(session_id, None)
    pipeline._save_session(
        session_id,
        plan_id=plan_id,
        input_text="测试",
        structured_demand={"rawInput": "测试", "demandProfile": {"openHypotheses": []}},
        mock_supply={},
        timeline_plan={"status": "ok", "selectedItems": []},
        execution_draft={"draftStatus": "ready", "pendingActions": []},
        refinement_intent={},
    )
    wrong_plan = pipeline.confirm_execution_from_draft(
        pipeline.ExecuteRequest(sessionId=session_id, planId="wrong_plan")
    )
    if wrong_plan.get("executionStatus") != "blocked":
        errors.append("session execution: wrong planId must be blocked")
    right_plan = pipeline.confirm_execution_from_draft(
        pipeline.ExecuteRequest(sessionId=session_id, planId=plan_id)
    )
    if right_plan.get("executionStatus") != "confirmed":
        errors.append("session execution: current session plan should confirm from backend draft")
    pipeline.SESSION_STORE["expired_session"] = {"updatedAt": time.time() - pipeline.SESSION_TTL_SECONDS - 1}
    pipeline._cleanup_sessions()
    if "expired_session" in pipeline.SESSION_STORE:
        errors.append("session store: expired sessions must be cleaned")
    pipeline.SESSION_STORE.pop(session_id, None)

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
            elif ontology_evolution.approved_hypothesis_matches(
                "和不熟的人出门时，强制安排拍照打卡任务避免冷场",
                store=store,
            ):
                errors.append("ontology evolution: approved pattern must not recall blocked negative concepts")
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
