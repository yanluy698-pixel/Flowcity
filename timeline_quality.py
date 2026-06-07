"""Timeline quality gates for Scheduler candidates."""

from __future__ import annotations

from typing import Any, TypedDict


class TimelineMetrics(TypedDict):
    activeTimeUtilization: float
    idleMinutes: int
    longestIdleMinutes: int
    firstIdleMinutes: int
    routeMinutes: int
    experienceBlockCount: int
    unusedTailMinutes: int


def parse_minutes(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def step_minutes(step: dict[str, Any]) -> int:
    start = parse_minutes(step.get("start"))
    end = parse_minutes(step.get("end"))
    if start is None or end is None:
        return 0
    if end < start:
        end += 24 * 60
    return max(0, end - start)


def is_idle_step(step: dict[str, Any]) -> bool:
    step_type = str(step.get("type") or "")
    title = str(step.get("title") or "")
    description = str(step.get("description") or "")
    if step_type in {"buffer", "idle"}:
        return True
    return any(keyword in title + description for keyword in ("空档", "空窗", "等待", "等位", "缓冲", "休息"))


def is_experience_step(step: dict[str, Any]) -> bool:
    return str(step.get("type") or "") in {"activity", "filler", "micro_activity", "rest"}


def metrics(
    *,
    timeline: list[dict[str, Any]],
    window_start: int,
    window_end: int,
    cursor: int | None,
) -> TimelineMetrics:
    window_minutes = max(1, window_end - window_start)
    active_minutes = sum(step_minutes(step) for step in timeline if is_experience_step(step) or step.get("type") == "restaurant")
    idle_minutes = sum(step_minutes(step) for step in timeline if is_idle_step(step))
    route_minutes = sum(step_minutes(step) for step in timeline if step.get("type") in {"route", "multi_origin_route"})
    longest_idle = max([step_minutes(step) for step in timeline if is_idle_step(step)] or [0])
    first_idle = step_minutes(timeline[0]) if timeline and is_idle_step(timeline[0]) else 0
    experience_blocks = sum(1 for step in timeline if is_experience_step(step))
    planned_end = cursor if cursor is not None else window_end
    return {
        "activeTimeUtilization": round(min(1.0, active_minutes / window_minutes), 4),
        "idleMinutes": idle_minutes,
        "longestIdleMinutes": longest_idle,
        "firstIdleMinutes": first_idle,
        "routeMinutes": route_minutes,
        "experienceBlockCount": experience_blocks,
        "unusedTailMinutes": max(0, window_end - planned_end),
    }


def rejection(
    *,
    metrics_value: TimelineMetrics,
    max_idle_minutes: int,
    target_experience_blocks: int,
    has_restaurant: bool,
) -> dict[str, Any] | None:
    if metrics_value.get("firstIdleMinutes", 0) > max_idle_minutes:
        return {
            "reason": "开局等待超过规划策略阈值",
            "firstIdleMinutes": metrics_value["firstIdleMinutes"],
            "maxIdleMinutes": max_idle_minutes,
        }
    if metrics_value["longestIdleMinutes"] > max_idle_minutes:
        return {
            "reason": "连续无意义等待超过规划策略阈值",
            "longestIdleMinutes": metrics_value["longestIdleMinutes"],
            "maxIdleMinutes": max_idle_minutes,
        }
    if target_experience_blocks >= 2 and has_restaurant and metrics_value["experienceBlockCount"] < 2:
        return {
            "reason": "4-6小时本地出行体验块不足",
            "experienceBlockCount": metrics_value["experienceBlockCount"],
            "targetExperienceBlocks": target_experience_blocks,
        }
    if target_experience_blocks >= 2 and metrics_value["unusedTailMinutes"] > max_idle_minutes and metrics_value["activeTimeUtilization"] < 0.7:
        return {
            "reason": "时间窗利用不足，结束得过早",
            "unusedTailMinutes": metrics_value["unusedTailMinutes"],
            "activeTimeUtilization": metrics_value["activeTimeUtilization"],
            "targetExperienceBlocks": target_experience_blocks,
        }
    return None
