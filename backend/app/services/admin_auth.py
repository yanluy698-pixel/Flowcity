from __future__ import annotations

import os

from fastapi import Header, HTTPException


def require_admin_token(x_flowcity_admin_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("FLOWCITY_ADMIN_TOKEN") or "flowcity-admin-demo"
    if x_flowcity_admin_token != expected:
        raise HTTPException(status_code=403, detail="Invalid admin token")
