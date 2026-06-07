from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.admin_auth import AdminAccess, require_admin_read_token, require_admin_write_token


FLOWCITY_ROOT = Path(__file__).resolve().parents[3]
if str(FLOWCITY_ROOT) not in sys.path:
    sys.path.insert(0, str(FLOWCITY_ROOT))

import learning_events  # noqa: E402
import ontology_evolution  # noqa: E402


router = APIRouter()


class ProposalReviewRequest(BaseModel):
    status: Literal["approved", "rejected", "pending_review"]


@router.get("/analysis")
def analyze_learning_events(access: AdminAccess = Depends(require_admin_read_token)) -> dict:
    """Admin-only learning review data; never call this from the consumer chat UI."""
    payload = ontology_evolution.analyze(learning_events.get_store())
    payload["access"] = access
    return payload


@router.get("/proposals")
def list_learning_proposals(status: str | None = None, access: AdminAccess = Depends(require_admin_read_token)) -> dict:
    return {"access": access, "proposals": learning_events.get_store().proposals(status)}


@router.post("/proposals/{proposal_id}/review")
def review_learning_proposal(
    proposal_id: str,
    request: ProposalReviewRequest,
    _: AdminAccess = Depends(require_admin_write_token),
) -> dict:
    updated = learning_events.get_store().review_proposal(proposal_id, request.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return {"proposalId": proposal_id, "status": request.status, "updated": True}
