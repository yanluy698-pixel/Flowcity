from __future__ import annotations

import os
from typing import Literal

from fastapi import Header, HTTPException

AdminAccess = Literal["read", "write"]


def admin_routes_enabled() -> bool:
    return bool(os.getenv("FLOWCITY_ADMIN_TOKEN") or os.getenv("FLOWCITY_ADMIN_READ_TOKEN"))


def _resolve_admin_access(x_flowcity_admin_token: str | None) -> AdminAccess:
    write_token = os.getenv("FLOWCITY_ADMIN_TOKEN")
    read_token = os.getenv("FLOWCITY_ADMIN_READ_TOKEN")
    if not write_token and not read_token:
        raise HTTPException(status_code=404, detail="Admin API is disabled")
    if write_token and x_flowcity_admin_token == write_token:
        return "write"
    if read_token and x_flowcity_admin_token == read_token:
        return "read"
    raise HTTPException(status_code=403, detail="Invalid admin token")


def require_admin_read_token(x_flowcity_admin_token: str | None = Header(default=None)) -> AdminAccess:
    return _resolve_admin_access(x_flowcity_admin_token)


def require_admin_write_token(x_flowcity_admin_token: str | None = Header(default=None)) -> AdminAccess:
    access = _resolve_admin_access(x_flowcity_admin_token)
    if access != "write":
        raise HTTPException(status_code=403, detail="Read-only admin token cannot modify data")
    return access


def require_admin_token(x_flowcity_admin_token: str | None = Header(default=None)) -> AdminAccess:
    return require_admin_read_token(x_flowcity_admin_token)
