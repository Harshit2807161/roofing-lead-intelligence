"""The rep's writes (P1): lead workflow (F9) and insight feedback (F12), as HTMX forms and JSON.

Everything else in the UI is read-only. These endpoints validate in code, append a row, and
return the refreshed card, so the page never shows state the database doesn't have.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models import Contractor, InsightFeedback, LeadActivity, LeadInsight
from app.web import data
from app.web.deps import get_session, templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
MAX_NOTE = 2000


def _require_lead(session: Session, gaf_id: str) -> None:
    if session.get(Contractor, gaf_id) is None:
        raise HTTPException(404, f"No contractor with GAF id {gaf_id}")


def add_activity(
    session: Session, gaf_id: str, status: str | None, note: str | None
) -> LeadActivity:
    """Validate and append one workflow row. Raises 422 on a bad status or an empty request."""
    _require_lead(session, gaf_id)
    status = (status or "").strip() or None
    note = (note or "").strip()[:MAX_NOTE] or None
    if status is not None and status not in data.LEAD_STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(data.LEAD_STATUSES)}")
    if status is None and note is None:
        raise HTTPException(422, "send a status, a note, or both")
    row = LeadActivity(gaf_id=gaf_id, status=status, note=note)
    session.add(row)
    session.commit()
    return row


def add_feedback(session: Session, gaf_id: str, vote: int, reason: str | None) -> InsightFeedback:
    """Append a vote on the lead's current insight, recording which prompt and inputs made it."""
    _require_lead(session, gaf_id)
    insight = session.get(LeadInsight, gaf_id)
    if insight is None:
        raise HTTPException(409, "this lead has no insight to rate yet")
    if vote not in (1, -1):
        raise HTTPException(422, "vote must be 1 or -1")
    reason = (reason or "").strip() or None
    if reason is not None and reason not in data.FEEDBACK_REASONS:
        raise HTTPException(422, f"reason must be one of {data.FEEDBACK_REASONS}")
    row = InsightFeedback(gaf_id=gaf_id, vote=vote, reason=reason if vote < 0 else None,
                          model=insight.model, prompt_version=insight.prompt_version,
                          input_hash=insight.input_hash)  # fmt: skip
    session.add(row)
    session.commit()
    return row


# --- HTMX forms on the detail page ------------------------------------------------------------


@router.post("/leads/{gaf_id}/activity", response_class=HTMLResponse)
def activity_form(
    request: Request,
    gaf_id: str,
    session: SessionDep,
    status: Annotated[str | None, Form()] = None,
    note: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    add_activity(session, gaf_id, status, note)
    ctx = {"lead": {"gaf_id": gaf_id, "workflow": data.workflow(session, gaf_id)},
           "statuses": data.LEAD_STATUSES}  # fmt: skip
    return templates.TemplateResponse(request, "partials/workflow_card.html", ctx)


@router.post("/leads/{gaf_id}/feedback", response_class=HTMLResponse)
def feedback_form(
    request: Request,
    gaf_id: str,
    session: SessionDep,
    vote: Annotated[int, Form()],
    reason: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    add_feedback(session, gaf_id, vote, reason)
    ctx = {"lead": {"gaf_id": gaf_id, "feedback": data.feedback_summary(session, gaf_id)},
           "reasons": data.FEEDBACK_REASONS, "just_voted": vote}  # fmt: skip
    return templates.TemplateResponse(request, "partials/feedback.html", ctx)


# --- JSON API ---------------------------------------------------------------------------------


class ActivityIn(BaseModel):
    status: str | None = None
    note: str | None = Field(default=None, max_length=MAX_NOTE)


class FeedbackIn(BaseModel):
    vote: int
    reason: str | None = None


@router.get("/api/leads/{gaf_id}/activity")
def api_activity(gaf_id: str, session: SessionDep) -> dict[str, Any]:
    _require_lead(session, gaf_id)
    return data.workflow(session, gaf_id)


@router.post("/api/leads/{gaf_id}/activity", status_code=201)
def api_add_activity(gaf_id: str, body: ActivityIn, session: SessionDep) -> dict[str, Any]:
    add_activity(session, gaf_id, body.status, body.note)
    return data.workflow(session, gaf_id)


@router.post("/api/leads/{gaf_id}/feedback", status_code=201)
def api_add_feedback(gaf_id: str, body: FeedbackIn, session: SessionDep) -> dict[str, Any]:
    add_feedback(session, gaf_id, body.vote, body.reason)
    return data.feedback_summary(session, gaf_id)
