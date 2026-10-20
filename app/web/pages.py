"""HTML pages: the ranked lead list, a lead's detail page, and the style card."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.web import data
from app.web.deps import get_session, templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


def _is_partial(request: Request) -> bool:
    """HTMX filter requests get just the results; a history restore needs the whole page."""
    h = request.headers
    return h.get("HX-Request") == "true" and h.get("HX-History-Restore-Request") != "true"


def _center() -> list[float]:
    s = get_settings()
    return [s.search_center_lat, s.search_center_lng]


@router.get("/", response_class=HTMLResponse)
def lead_list(request: Request, session: SessionDep) -> HTMLResponse:
    filters = data.Filters.from_params(request.query_params)
    ctx = {
        "leads": data.list_leads(session, filters),
        "filters": filters,
        "center": _center(),
    }
    if _is_partial(request):
        return templates.TemplateResponse(request, "partials/lead_results.html", ctx)
    ctx |= {
        "counts": data.summary_counts(session),
        "plays": data.PLAYS,
        "sorts": data.SORTS,
        "tag_options": data.tag_options(session),
        "statuses": data.LEAD_STATUSES,
    }
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/leads/{gaf_id}", response_class=HTMLResponse)
def lead_page(request: Request, gaf_id: str, session: SessionDep) -> HTMLResponse:
    lead = data.lead_detail(session, gaf_id)
    if lead is None:
        return templates.TemplateResponse(
            request, "not_found.html", {"gaf_id": gaf_id}, status_code=404
        )
    ctx = {"lead": lead, "center": _center(), "statuses": data.LEAD_STATUSES,
           "reasons": data.FEEDBACK_REASONS}  # fmt: skip
    return templates.TemplateResponse(request, "detail.html", ctx)


@router.get("/style", response_class=HTMLResponse)
def style_card(request: Request) -> HTMLResponse:
    """A one-page check of the tokens and components; needs no database."""
    return templates.TemplateResponse(request, "style.html", {"sample": SAMPLE_ROW})


SAMPLE_ROW = {
    "gaf_id": "1116285",
    "name": "Allied Brothers Home Corporation",
    "initials": "AB",
    "city": "Wayne",
    "state": "NJ",
    "distance_mi": 18.73,
    "rating": 4.9,
    "review_count": 283,
    "presidents_club_stars": 2,
    "commercial": True,
    "rank": 1,
    "ranked_by": "score",
    "score": 86.5,
    "play": "Volume leader",
    "insight_status": "ok",
    "summary": "High-volume Master Elite shop in Wayne with two President's Club stars and steady reviews.",
}
