from __future__ import annotations

import json
import sys
import time
import uuid
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.schemas.flow import ExecuteRequest, FlowRunRequest


FLOWCITY_ROOT = Path(__file__).resolve().parents[3]
if str(FLOWCITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLOWCITY_ROOT))

import executor  # noqa: E402
import extractor  # noqa: E402
import flow_tools  # noqa: E402
import demand_profile  # noqa: E402
import learning_events  # noqa: E402
import area_retrieval  # noqa: E402
import mock_api  # noqa: E402
import planner  # noqa: E402
import refinement  # noqa: E402
import router  # noqa: E402
import validator  # noqa: E402


SESSION_STORE: dict[str, dict[str, Any]] = {}
STREAM_STAGE_PAUSE_SECONDS = 0.25


def _record_learning_event(
    event_type: str,
    *,
    session_id: str | None,
    hypothesis_id: str | None = None,
    cluster_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    try:
        learning_events.get_store().record(
            event_type,
            session_id=session_id,
            hypothesis_id=hypothesis_id,
            cluster_key=cluster_key,
            payload=payload,
        )
    except Exception:
        # Learning telemetry must never block the planning product path.
        return


def _record_profile_learning_events(session_id: str, structured_demand: dict[str, Any]) -> None:
    for item in structured_demand.get("demandProfile", {}).get("openHypotheses", []):
        hypothesis_id = item.get("hypothesisId")
        cluster_key = item.get("key")
        payload = {
            "text": item.get("text"),
            "confidence": item.get("confidence"),
            "evidence": item.get("evidence", []),
        }
        _record_learning_event(
            "hypothesis_created",
            session_id=session_id,
            hypothesis_id=hypothesis_id,
            cluster_key=cluster_key,
            payload=payload,
        )
        _record_learning_event(
            "hypothesis_shown",
            session_id=session_id,
            hypothesis_id=hypothesis_id,
            cluster_key=cluster_key,
            payload={"text": item.get("text")},
        )


def _record_supply_learning_events(session_id: str, mock_supply: dict[str, Any]) -> None:
    for item in mock_supply.get("vectorRecallResult", {}).get("matches", []):
        _record_learning_event(
            "poi_recalled_by_hypothesis",
            session_id=session_id,
            hypothesis_id=item.get("hypothesisId"),
            payload={
                "poiId": item.get("poiId"),
                "areaId": item.get("areaId"),
                "similarity": item.get("similarity"),
                "provider": item.get("provider"),
            },
        )


def _active_hypotheses(structured_demand: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(structured_demand, dict):
        return []
    return [
        deepcopy(item)
        for item in structured_demand.get("demandProfile", {}).get("openHypotheses", [])
        if isinstance(item, dict)
        and item.get("hypothesisId")
        and item.get("status") != "user_rejected"
    ]


def _record_outcome_for_active_hypotheses(
    event_type: str,
    *,
    session_id: str | None,
    structured_demand: dict[str, Any] | None,
    payload: dict[str, Any] | None = None,
) -> None:
    for item in _active_hypotheses(structured_demand):
        _record_learning_event(
            event_type,
            session_id=session_id,
            hypothesis_id=item.get("hypothesisId"),
            cluster_key=item.get("key"),
            payload={
                "hypothesisText": item.get("text"),
                **(payload or {}),
            },
        )


def _ndjson(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, default=str) + "\n"


def _event(
    event_type: str,
    *,
    stage: str | None = None,
    label: str | None = None,
    payload: dict[str, Any] | None = None,
    message: str | None = None,
) -> str:
    event: dict[str, Any] = {"type": event_type}
    if stage is not None:
        event["stage"] = stage
    if label is not None:
        event["label"] = label
    if payload is not None:
        event["payload"] = payload
    if message is not None:
        event["message"] = message
    return _ndjson(event)


def _pause_for_streaming() -> None:
    time.sleep(STREAM_STAGE_PAUSE_SECONDS)


def _limit_supply(result: dict[str, Any], limit: int) -> dict[str, Any]:
    limited = dict(result)
    limited["activityCandidates"] = result.get("activityCandidates", [])[:limit]
    limited["restaurantCandidates"] = result.get("restaurantCandidates", [])[:limit]
    limited["routeCandidates"] = result.get("routeCandidates", [])[:limit]
    return limited


def _compact_supply_counts(mock_supply: dict[str, Any]) -> dict[str, Any]:
    return {
        "activityCount": len(mock_supply.get("activityCandidates", [])),
        "restaurantCount": len(mock_supply.get("restaurantCandidates", [])),
        "routeCount": len(mock_supply.get("routeCandidates", [])),
        "supplyStatus": mock_supply.get("supplyStatus", {}),
        "toolLogs": mock_supply.get("toolLogs", []),
    }


def _build_plan_explanation(
    *,
    router_result: dict[str, Any],
    timeline_plan: dict[str, Any] | None,
    validation_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if router_result.get("mode") != "explain" or not timeline_plan:
        return None
    timeline = timeline_plan.get("timeline") or []
    scheduler_result = timeline_plan.get("schedulerResult") or {}
    rejected = scheduler_result.get("rejectedCombinations") or []
    issues = (validation_result or {}).get("issues") or []
    facts: list[str] = []
    for item in timeline:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        if item.get("type") == "filler":
            facts.append(f"系统插入了实体过渡点「{item.get('title')}」，时间为 {item.get('start')}-{item.get('end')}。")
        if item.get("type") == "restaurant":
            facts.append(f"餐厅「{item.get('title')}」被安排在 {item.get('start')}，这是最终时间轴中的到店时间。")
        if "晚餐前" in title:
            facts.append(f"{title} 为 {item.get('start')}-{item.get('end')}，用于避免过早吃晚饭。")
        if item.get("type") == "multi_origin_route":
            facts.append(str(item.get("description") or "已按多人出发点计算公平集合路线。"))
    for issue in issues:
        code = issue.get("code")
        if code in {"RESTAURANT_SLOT_MISMATCH", "BUSINESS_HOURS_MISMATCH", "ACTIVITY_SLOT_MISMATCH"}:
            facts.append(f"校验日志记录：{issue.get('message')} 期望 {issue.get('expected')}，实际 {issue.get('actual')}。")
    for item in rejected[:3]:
        reason = item.get("reason")
        if reason:
            facts.append(f"调度器曾拒绝组合：{reason}。")
    if not facts:
        facts.append("当前解释来自调度器结果、校验结果和拒绝组合日志；没有额外编造城市习惯或商家规则。")
    message = "为您查阅了刚才的调度日志：\n" + "\n".join(f"- {fact}" for fact in facts[:6])
    return {
        "mode": "explain",
        "message": message,
        "source": "schedulerResult + validationResult + rejectedCombinations",
        "facts": facts[:10],
    }


def _timeline_digest(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(plan, dict):
        return []
    digest = []
    for item in plan.get("timeline", [])[:6]:
        if not isinstance(item, dict):
            continue
        digest.append(
            {
                "time": f"{item.get('start', '')}-{item.get('end', '')}",
                "type": item.get("type"),
                "title": item.get("title"),
                "description": item.get("description"),
            }
        )
    return digest


def _selected_poi_id(plan: dict[str, Any] | None, kind: str) -> str | None:
    if not isinstance(plan, dict):
        return None
    for item in plan.get("selectedItems", []):
        if item.get("kind") == kind and item.get("poiId"):
            return str(item["poiId"])
    for step in plan.get("timeline", []):
        if step.get("type") == kind and step.get("poiId"):
            return str(step["poiId"])
    return None


def _clicked_modify_context(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for line in str(text or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key in {"targetKind", "targetTitle", "targetTimeRange", "targetPoiId", "allowedPatchKeys"}:
            fields[key] = value
    if fields.get("allowedPatchKeys"):
        fields["allowedPatchKeys"] = [
            item.strip() for item in str(fields["allowedPatchKeys"]).split(",") if item.strip()
        ]
    return fields


def _build_refinement_dialogue_prompt(
    *,
    user_input: str,
    current_demand: dict[str, Any],
    current_plan: dict[str, Any] | None,
    router_result: dict[str, Any],
) -> str:
    payload = {
        "userFollowup": user_input,
        "currentTimeWindow": current_demand.get("timeWindow", {}),
        "currentBudget": current_demand.get("budget", {}),
        "currentTimeline": _timeline_digest(current_plan),
        "routerPatch": router_result.get("constraintsPatch", {}),
    }
    return (
        "你是 FlowCity 的出行规划助手。用户正在对上一版方案做二次修改。\n"
        "请判断这个修改是否会和当前时间窗、餐饮营业/预约、路线或预算产生冲突。\n"
        "你的追问必须有引导性：每个选项都必须能直接转成后端调度补丁，例如 mealTiming、timeWindow、locks、preferredArea、budgetFlex、transportPreference。\n"
        "不要问泛泛的问题，比如“还有什么要求”；不要把选择权丢给用户但不给可执行方向。\n"
        "只输出 JSON，不要 Markdown。字段：\n"
        "{\n"
        '  "message": "给用户的一段自然中文，80字以内，像人一样说明冲突和建议",\n'
        '  "quickReplies": ["2到4个短选项，每个都必须是可执行选择"],\n'
        '  "patchOptions": [{"label": "与quickReplies一致", "patch": {"mealTiming": "earlier|keep", "restaurantLock": "keep|release", "budgetFlex": "strict|flexible", "timeWindowEnd": "HH:MM 或 null"}}],\n'
        '  "riskLevel": "low|medium|high"\n'
        "}\n"
        "要求：不要编造具体门店实时库存；可以基于已给时间轴提出建议。"
        "如果用户要求早一点吃饭，可提示4点多正餐选择可能少，并给出类似：按16:30左右简餐/茶点、保留原餐厅只提前到最早可约、放宽到17:30正餐。\n"
        "当前上下文：\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )


def _fallback_refinement_message(request_input: str, router_result: dict[str, Any]) -> dict[str, Any]:
    patch = router_result.get("constraintsPatch", {})
    if patch.get("mealTiming") == "earlier" or any(word in request_input for word in ("早一点", "早点", "提前")):
        return {
            "mode": "clarification",
            "message": "可以往前调。不过4点多正餐选择通常会少一些，我建议先找能坐下的茶点/简餐垫一下，或者把正餐提前到较早可约时段。你想按这个方向重排吗？",
            "quickReplies": ["16:30左右简餐垫一下", "保留原餐厅提前到最早", "放宽到17:30正餐"],
            "patchOptions": [
                {"label": "16:30左右简餐垫一下", "patch": {"mealTiming": "earlier", "restaurantLock": "release", "timeWindowEnd": None}},
                {"label": "保留原餐厅提前到最早", "patch": {"mealTiming": "earlier", "restaurantLock": "keep", "timeWindowEnd": None}},
                {"label": "放宽到17:30正餐", "patch": {"mealTiming": "keep", "restaurantLock": "release", "timeWindowEnd": None}},
            ],
            "source": "local-fallback",
            "riskLevel": "medium",
        }
    return {
        "mode": "clarification",
        "message": "这个修改可能会影响原来的时间、路线或预算。我可以先按这个方向重排一版，也可以只调整你指定的那一项。",
        "quickReplies": ["按这个方向重排", "尽量保留原地点", "放宽时间窗口"],
        "patchOptions": [
            {"label": "按这个方向重排", "patch": {"restaurantLock": "release", "budgetFlex": "strict"}},
            {"label": "尽量保留原地点", "patch": {"restaurantLock": "keep", "budgetFlex": "strict"}},
            {"label": "放宽时间窗口", "patch": {"restaurantLock": "release", "budgetFlex": "strict", "timeWindowEnd": None}},
        ],
        "source": "local-fallback",
        "riskLevel": "medium",
    }


def _call_refinement_dialogue_llm(
    *,
    request_input: str,
    current_demand: dict[str, Any],
    current_plan: dict[str, Any] | None,
    router_result: dict[str, Any],
) -> dict[str, Any]:
    prompt = _build_refinement_dialogue_prompt(
        user_input=request_input,
        current_demand=current_demand,
        current_plan=current_plan,
        router_result=router_result,
    )
    try:
        response_text = extractor.call_llm(
            prompt,
            max_tokens=800,
            timeout_seconds=22,
            retries=0,
        )
        data = extractor.parse_json_object(response_text)
        message = str(data.get("message") or "").strip()
        quick_replies = data.get("quickReplies")
        if not message:
            raise ValueError("LLM dialogue response missing message")
        if not isinstance(quick_replies, list):
            quick_replies = []
        patch_options = data.get("patchOptions")
        if not isinstance(patch_options, list):
            patch_options = []
        return {
            "mode": "clarification",
            "message": message,
            "quickReplies": [str(item) for item in quick_replies[:4] if item],
            "patchOptions": patch_options[:4],
            "source": "llm-refinement-dialogue",
            "riskLevel": data.get("riskLevel") or "medium",
        }
    except Exception:
        return _fallback_refinement_message(request_input, router_result)


def _should_start_refinement_dialogue(request: FlowRunRequest, router_result: dict[str, Any], session: dict[str, Any] | None) -> bool:
    if not session or router_result.get("mode") != "refine":
        return False
    patch = router_result.get("constraintsPatch", {})
    if patch.get("usePendingRefinement"):
        return False
    text = str(request.input or "")
    if "【整体大改上下文】" in text:
        return False
    if "【节点修改上下文】" in text and not any(word in text for word in ("早一点", "晚一点", "早点", "提前", "赶", "到家", "来得及")):
        return False
    current_plan = session.get("currentPlan") if isinstance(session, dict) else None
    if isinstance(current_plan, dict) and current_plan.get("status") == "failed":
        if any(word in text for word in ("只吃饭", "只安排吃饭", "不安排活动", "不要活动", "不玩了", "不安排项目")):
            return True
    if patch.get("mealTiming") in {"earlier", "later"}:
        return True
    return any(word in text for word in ("早一点", "晚一点", "早点", "提前", "赶", "到家", "来得及"))


def _start_refinement_dialogue(
    session_id: str,
    request: FlowRunRequest,
    router_result: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    pending_demand, pending_intent = refinement.apply_refinement(
        session["currentDemand"],
        request.input,
        session.get("currentPlan"),
    )
    pending_demand.setdefault("planControl", {})["routerResult"] = router_result
    pending_demand["planControl"]["actionFlags"] = router_result.get("actionFlags", {})
    pending_demand["planControl"]["locks"] = router_result.get("locks", {"timeFlexMinutes": 30})
    pending_demand["planControl"]["constraintsPatch"] = router_result.get("constraintsPatch", {})
    clicked_context = _clicked_modify_context(request.input)
    if clicked_context:
        pending_demand["planControl"]["clickedModify"] = clicked_context
    if "【整体大改上下文】" in request.input:
        pending_demand["planControl"]["majorChange"] = True
    pending_intent["routerResult"] = router_result
    pending_intent["locks"] = router_result.get("locks", {})
    pending_intent["actionFlags"] = router_result.get("actionFlags", {})
    pending_intent["awaitingUserConfirmation"] = True
    SESSION_STORE.setdefault(session_id, session)["pendingRefinement"] = {
        "demand": deepcopy(pending_demand),
        "intent": deepcopy(pending_intent),
        "createdAt": time.time(),
        "rawFeedback": request.input,
    }
    return _call_refinement_dialogue_llm(
        request_input=request.input,
        current_demand=session["currentDemand"],
        current_plan=session.get("currentPlan"),
        router_result=router_result,
    )


def _new_plan_id() -> str:
    return f"plan_{uuid.uuid4().hex[:10]}"


def _session_id(request: FlowRunRequest) -> str:
    return request.sessionId or f"session_{uuid.uuid4().hex[:10]}"


def _save_session(
    session_id: str,
    *,
    plan_id: str,
    input_text: str,
    structured_demand: dict[str, Any],
    mock_supply: dict[str, Any],
    timeline_plan: dict[str, Any],
    refinement_intent: dict[str, Any],
) -> None:
    previous = SESSION_STORE.get(session_id, {})
    history = list(previous.get("userFeedbackHistory", []))
    if refinement_intent.get("mode") == "refine":
        history.append(refinement_intent)
    SESSION_STORE[session_id] = {
        "sessionId": session_id,
        "currentPlanId": plan_id,
        "originalDemand": previous.get("originalDemand") or deepcopy(structured_demand),
        "currentDemand": deepcopy(structured_demand),
        "currentSupply": deepcopy(mock_supply),
        "currentPlan": deepcopy(timeline_plan),
        "activeHypotheses": _active_hypotheses(structured_demand),
        "lastInput": input_text,
        "userFeedbackHistory": history,
        "updatedAt": time.time(),
    }


def _extract_or_refine_demand(
    request: FlowRunRequest,
    session_id: str,
    router_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    session = SESSION_STORE.get(session_id)
    if router_result.get("mode") in {"explain", "confirm"} and session:
        demand = deepcopy(session["currentDemand"])
        demand.setdefault("planControl", {})["routerResult"] = router_result
        return demand, {
            "mode": router_result["mode"],
            "operations": ["explain_plan" if router_result["mode"] == "explain" else "confirm_execute"],
            "avoidTerms": [],
            "requireActivity": False,
            "lockedItems": [],
            "changedItems": [],
            "rawFeedback": request.input,
            "routerResult": router_result,
        }

    should_refine = router_result.get("mode") == "refine" or (
        request.interactionMode == "refine"
        or (
            request.interactionMode == "auto"
            and refinement.is_likely_refinement(request.input, bool(session))
        )
    )
    if should_refine and session and router_result.get("constraintsPatch", {}).get("usePendingRefinement"):
        pending = session.pop("pendingRefinement", None)
        if pending:
            demand = deepcopy(pending["demand"])
            intent = deepcopy(pending["intent"])
            choice_text = str(request.input or "")
            plan_control = demand.setdefault("planControl", {})
            locks = plan_control.setdefault("locks", {})
            if "保留原餐厅" in choice_text or "原餐厅" in choice_text:
                restaurant_id = _selected_poi_id(session.get("currentPlan"), "restaurant")
                if restaurant_id:
                    locks["restaurantPoiId"] = restaurant_id
            if "17:30" in choice_text or "正餐" in choice_text and "放宽" in choice_text:
                plan_control["mealTiming"] = "keep"
                plan_control.setdefault("constraintsPatch", {})["mealTiming"] = "keep"
            demand.setdefault("planControl", {})["routerResult"] = router_result
            intent["acceptedPendingRefinement"] = True
            intent["routerResult"] = router_result
            return demand, intent

    clicked_modify = "【节点修改上下文】" in request.input or "【整体大改上下文】" in request.input
    if should_refine and session and clicked_modify:
        clicked_context = _clicked_modify_context(request.input)
        context_payload = {
            "previousDemand": session.get("currentDemand"),
            "previousTimeline": _timeline_digest(session.get("currentPlan")),
            "clickedModifyContext": clicked_context,
            "userModification": request.input,
        }
        prompt = (
            "你是 FlowCity 的结构化需求修改器。用户在前端点击了时间轴节点或整体大改按钮，"
            "随后补充了自然语言要求。\n"
            "请基于 previousDemand 做修改，输出完整结构化需求 JSON，字段必须尽量沿用原 schema。\n"
            "要求：保留原始硬约束，例如人数、预算、时间窗、出发地、儿童/老人/饮食边界；"
            "只根据 userModification 改相关偏好、约束、planControl。\n"
            "必须读取 clickedModifyContext.targetKind 和 clickedModifyContext.allowedPatchKeys。\n"
            "如果 targetKind=restaurant，只能重点修改餐饮相关偏好、时间、预算、排队和餐厅锁定；不要无故替换活动。\n"
            "如果 targetKind=activity，只能重点修改活动偏好、体力、室内外、余票和路线；不要无故替换餐厅。\n"
            "如果 targetKind=route，只能重点修改路线、交通方式、商圈布局和转场；不要无故换成语义高但更远的 POI。\n"
            "如果 targetKind=whole_plan，允许重排活动、餐厅和路线，但仍保留所有硬约束。\n"
            "如果用户表达会导致时间/预算/路线冲突，把风险写进 potentialConflicts，并在 constraints.soft 给出可执行取舍方向。\n"
            "保留 previousDemand.demandProfile 中仍成立的事实、目的地锚点和已拒绝猜测；"
            "仅为用户这次明确修改的底层维度更新 target/source/confidence/evidence。"
            "不要把具体推荐 POI 的特点反写成用户需求，也不要因为上一版选中了某地点就推断用户喜欢它。\n"
            "不要输出解释，不要输出 POI 方案，只输出 JSON。\n"
            f"{json.dumps(context_payload, ensure_ascii=False, default=str)}"
        )
        try:
            response_text = extractor.call_llm(
                prompt,
                max_tokens=2200,
                timeout_seconds=24,
                retries=0,
            )
            demand = extractor.parse_json_object(response_text)
            demand = extractor.normalize_structured_demand(demand, request.input)
        except Exception:
            demand, _ = refinement.apply_refinement(
                session["currentDemand"],
                request.input,
                session.get("currentPlan"),
            )
        demand.setdefault("planControl", {})["routerResult"] = router_result
        demand["planControl"]["actionFlags"] = router_result.get("actionFlags", {})
        demand["planControl"]["locks"] = router_result.get("locks", {"timeFlexMinutes": 30})
        demand["planControl"]["constraintsPatch"] = router_result.get("constraintsPatch", {})
        demand["planControl"]["clickedModify"] = clicked_context
        if "【整体大改上下文】" in request.input:
            demand["planControl"]["locks"] = {"timeFlexMinutes": 30}
            demand["planControl"]["majorChange"] = True
        return demand, {
            "mode": "refine",
            "operations": ["llm_node_refine" if "【节点修改上下文】" in request.input else "llm_major_replan"],
            "avoidTerms": [],
            "requireActivity": False,
            "lockedItems": [],
            "changedItems": ["selected_node" if "【节点修改上下文】" in request.input else "all"],
            "rawFeedback": request.input,
            "routerResult": router_result,
            "source": "llm_clicked_modify",
        }

    if should_refine and session:
        demand, intent = refinement.apply_refinement(
            session["currentDemand"],
            request.input,
            session.get("currentPlan"),
        )
        demand.setdefault("planControl", {})["routerResult"] = router_result
        demand["planControl"]["actionFlags"] = router_result.get("actionFlags", {})
        demand["planControl"]["locks"] = router_result.get("locks", {"timeFlexMinutes": 30})
        demand["planControl"]["constraintsPatch"] = router_result.get("constraintsPatch", {})
        intent["routerResult"] = router_result
        intent["locks"] = router_result.get("locks", {})
        intent["actionFlags"] = router_result.get("actionFlags", {})
        return demand, intent

    prompt = extractor.build_prompt(request.input)
    response_text = extractor.call_llm(prompt)
    structured_demand = extractor.parse_json_object(response_text)
    structured_demand = extractor.normalize_structured_demand(structured_demand, request.input)
    return structured_demand, {
        "mode": "new_plan",
        "operations": ["new_plan"],
        "avoidTerms": [],
        "requireActivity": False,
        "lockedItems": [],
        "changedItems": ["all"],
        "rawFeedback": None,
        "routerResult": router_result,
    }


def _schema_safe_demand(structured_demand: dict[str, Any]) -> dict[str, Any]:
    """Drop internal control fields before validating against schema.json."""
    schema = extractor.load_json(extractor.SCHEMA_PATH)
    allowed = set(schema.get("properties", {}).keys())
    return {key: value for key, value in structured_demand.items() if key in allowed}


def _client_visible_demand(structured_demand: dict[str, Any]) -> dict[str, Any]:
    """Expose schema-safe demand plus interaction context needed by the UI."""
    demand = _schema_safe_demand(structured_demand)
    plan_control = structured_demand.get("planControl")
    if isinstance(plan_control, dict):
        visible_control = {
            key: deepcopy(plan_control[key])
            for key in ("clickedModify", "majorChange", "mealTiming", "routerResult", "actionFlags", "constraintsPatch")
            if key in plan_control
        }
        if visible_control:
            demand["planControl"] = visible_control
    return demand


def stream_flow_events(request: FlowRunRequest) -> Iterator[str]:
    session_id = _session_id(request)
    plan_id = _new_plan_id()
    full_supply: dict[str, Any] | None = None
    mock_supply: dict[str, Any] | None = None
    structured_demand: dict[str, Any] | None = None
    timeline_plan: dict[str, Any] | None = None
    stage5: dict[str, Any] | None = None
    refinement_intent: dict[str, Any] = {}

    try:
        session = SESSION_STORE.get(session_id)
        router_result = router.route_interaction(
            request.input,
            has_session=bool(session),
            session=session,
        )

        if _should_start_refinement_dialogue(request, router_result, session):
            assistant_message = _start_refinement_dialogue(session_id, request, router_result, session)
            pending = (SESSION_STORE.get(session_id) or {}).get("pendingRefinement") or {}
            pending_demand = pending.get("demand") if isinstance(pending, dict) else None
            yield _event(
                "final",
                payload={
                    "input": request.input,
                    "sessionId": session_id,
                    "planId": plan_id,
                    "routerResult": router_result,
                    "assistantMessage": assistant_message,
                    "awaitingUserChoice": True,
                    "structuredDemand": _client_visible_demand(pending_demand) if isinstance(pending_demand, dict) else {},
                },
            )
            return

        yield _event("stage_start", stage="extract", label="理解需求")
        structured_demand, refinement_intent = _extract_or_refine_demand(request, session_id, router_result)
        if request.hypothesisFeedback:
            demand_profile.apply_hypothesis_feedback(structured_demand, request.hypothesisFeedback)
        demand_profile.ensure_demand_profile(structured_demand)
        if refinement_intent.get("mode") == "refine":
            _record_outcome_for_active_hypotheses(
                "node_modified",
                session_id=session_id,
                structured_demand=structured_demand,
                payload={
                    "changedItems": refinement_intent.get("changedItems", []),
                    "operations": refinement_intent.get("operations", []),
                },
            )
        if request.hypothesisFeedback:
            feedback = request.hypothesisFeedback
            _record_learning_event(
                str(feedback.get("action") or "hypothesis_feedback"),
                session_id=session_id,
                hypothesis_id=feedback.get("hypothesisId"),
                cluster_key=feedback.get("clusterKey"),
                payload=feedback,
            )
        _record_profile_learning_events(session_id, structured_demand)
        schema_demand = _schema_safe_demand(structured_demand)
        client_demand = _client_visible_demand(structured_demand)
        validation_errors = extractor.basic_validate(
            schema_demand, extractor.load_json(extractor.SCHEMA_PATH)
        )
        if validation_errors:
            raise ValueError("Stage 2 validation failed: " + "; ".join(validation_errors))
        yield _event(
            "stage_done",
            stage="extract",
            payload={
                "structuredDemand": client_demand,
                "refinementIntent": refinement_intent,
                "sessionId": session_id,
                "planId": plan_id,
            },
        )
        _pause_for_streaming()

        yield _event("stage_start", stage="area", label="比较可行区域与点名目的地")
        area_recall_preview = area_retrieval.recall_areas(structured_demand, mock_api.load_mock_data())
        yield _event(
            "stage_done",
            stage="area",
            payload={"areaRecallResult": area_recall_preview},
        )
        _pause_for_streaming()
        anchor_conflicts = area_recall_preview.get("anchorConflicts", [])
        if anchor_conflicts:
            conflict = anchor_conflicts[0]
            yield _event(
                "final",
                payload={
                    "input": request.input,
                    "sessionId": session_id,
                    "planId": plan_id,
                    "routerResult": router_result,
                    "structuredDemand": client_demand,
                    "areaRecallResult": area_recall_preview,
                    "awaitingUserChoice": True,
                    "assistantMessage": {
                        "mode": "clarification",
                        "message": (
                            f"我会保留你点名的「{conflict.get('areaName')}」，但当前条件下暂时排不成："
                            f"{conflict.get('reason')}。你希望怎么取舍？"
                        ),
                        "quickReplies": conflict.get("suggestedActions", []),
                        "source": "destination-anchor-feasibility",
                        "riskLevel": "high",
                    },
                },
            )
            return

        yield _event("stage_start", stage="supply", label="在入围区域中寻找地点")
        full_supply, tool_results = flow_tools.search_supply_with_tools(structured_demand)
        _record_supply_learning_events(session_id, full_supply)
        mock_supply = _limit_supply(full_supply, request.limit)
        yield _event(
            "stage_done",
            stage="supply",
            payload={
                "mockSupply": mock_supply,
                "toolResults": tool_results,
                **_compact_supply_counts(mock_supply),
            },
        )
        _pause_for_streaming()
        preference_conflicts = full_supply.get("explicitPreferenceCoverage", {}).get("blockingConflicts", [])
        if preference_conflicts:
            conflict = preference_conflicts[0]
            _save_session(
                session_id,
                plan_id=plan_id,
                input_text=request.input,
                structured_demand=structured_demand,
                mock_supply=full_supply,
                timeline_plan={},
                refinement_intent=refinement_intent,
            )
            yield _event(
                "final",
                payload={
                    "input": request.input,
                    "sessionId": session_id,
                    "planId": plan_id,
                    "routerResult": router_result,
                    "structuredDemand": client_demand,
                    "mockSupply": full_supply,
                    "awaitingUserChoice": True,
                    "assistantMessage": {
                        "mode": "clarification",
                        "message": (
                            f"我没有直接拿别的类型糊弄你：{conflict.get('reason')}。"
                            "你更想保留地点，还是保留这次明确提出的偏好？"
                        ),
                        "quickReplies": conflict.get("suggestedActions", []),
                        "source": "explicit-preference-coverage",
                        "riskLevel": "high" if conflict.get("strength") == "hard" else "medium",
                    },
                    "preferenceConflict": conflict,
                },
            )
            return

        yield _event("stage_start", stage="plan", label="组合时间轴")
        timeline_plan = planner.plan_timeline(
            structured_demand,
            full_supply,
            use_llm=request.plannerLlm,
            fallback_on_error=not request.strictPlannerLlm,
            limit=max(request.limit, 1),
        )
        for item in timeline_plan.get("selectedItems", []):
            for source in item.get("recallSources", []):
                if source == "open_hypothesis_vector":
                    _record_learning_event(
                        "poi_selected_in_plan",
                        session_id=session_id,
                        payload={"poiId": item.get("poiId"), "kind": item.get("kind")},
                    )
        yield _event(
            "stage_done",
            stage="plan",
            payload={
                "timelinePlan": timeline_plan,
                "schedulerResult": timeline_plan.get("schedulerResult"),
            },
        )
        _pause_for_streaming()

        yield _event("stage_start", stage="validate", label="校验预算、余票和路线风险")
        stage5 = validator.validate_and_replan(structured_demand, full_supply, timeline_plan)
        yield _event("stage_done", stage="validate", payload=stage5)
        _pause_for_streaming()

        yield _event("stage_start", stage="execute_draft", label="生成执行草案")
        stage6 = executor.prepare_execution(
            timeline_plan,
            stage5["validationResult"],
            stage5["replanResult"],
            full_supply,
            confirm_execute=request.confirmExecute,
        )
        plan_explanation = _build_plan_explanation(
            router_result=router_result,
            timeline_plan=timeline_plan,
            validation_result=stage5["validationResult"],
        )
        _save_session(
            session_id,
            plan_id=plan_id,
            input_text=request.input,
            structured_demand=structured_demand,
            mock_supply=full_supply,
            timeline_plan=timeline_plan,
            refinement_intent=refinement_intent,
        )
        yield _event(
            "final",
            payload={
                "input": request.input,
                "sessionId": session_id,
                "planId": plan_id,
                "routerResult": router_result,
                "refinementIntent": refinement_intent,
                "structuredDemand": client_demand,
                "mockSupply": full_supply,
                "toolResults": full_supply.get("toolResults", []),
                "timelinePlan": timeline_plan,
                "schedulerResult": timeline_plan.get("schedulerResult"),
                "rejectedCombinations": (timeline_plan.get("schedulerResult") or {}).get("rejectedCombinations", []),
                "lockedItems": refinement_intent.get("lockedItems", []),
                "changedItems": refinement_intent.get("changedItems", []),
                "validationResult": stage5["validationResult"],
                "replanResult": stage5["replanResult"],
                "planExplanation": plan_explanation,
                "executionDraft": stage6["executionDraft"],
                "executionResult": stage6["executionResult"],
            },
        )
    except Exception as exc:
        failed_stage = "extract"
        if structured_demand is not None and full_supply is None:
            failed_stage = "supply"
        elif full_supply is not None and timeline_plan is None:
            failed_stage = "plan"
        elif timeline_plan is not None and stage5 is None:
            failed_stage = "validate"
        elif stage5 is not None:
            failed_stage = "execute_draft"
        yield _event("error", stage=failed_stage, message=str(exc))


def confirm_execution_from_draft(request: ExecuteRequest) -> dict[str, Any]:
    result = executor.confirm_execution(
        request.executionDraft,
        structured_demand=request.structuredDemand,
        timeline_plan=request.timelinePlan,
        mock_supply=request.mockSupply,
        planner_llm=request.plannerLlm,
        replan_on_runtime_failure=request.replanOnRuntimeFailure,
    )
    if result.get("executionStatus") == "confirmed":
        _record_learning_event(
            "plan_confirmed",
            session_id=request.sessionId,
            payload={"planId": request.planId},
        )
        session = SESSION_STORE.get(str(request.sessionId or ""))
        structured_demand = (
            session.get("currentDemand")
            if isinstance(session, dict)
            else request.structuredDemand
        )
        _record_outcome_for_active_hypotheses(
            "plan_confirmed",
            session_id=request.sessionId,
            structured_demand=structured_demand,
            payload={"planId": request.planId},
        )
    return result
