"""Shared helpers for user-facing date and time semantics."""

from __future__ import annotations


WEEKEND_TERMS = ("周六", "周日", "周天", "周末", "星期六", "星期日", "星期天", "礼拜六", "礼拜日", "礼拜天")


def is_weekend_text(date_text: str | None) -> bool:
    """Return whether a Chinese date expression clearly points to weekend."""
    return bool(date_text and any(term in date_text for term in WEEKEND_TERMS))
