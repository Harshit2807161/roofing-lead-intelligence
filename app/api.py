"""The web app: rep UI pages plus the JSON API (F6, F7). Run with `uv run uvicorn app.api:app`."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.web import data
from app.web.actions import router as actions_router
from app.web.chat_pages import router as chat_router
from app.web.deps import STATIC_DIR, get_session
from app.web.explain_pages import router as explain_router
from app.web.pages import router as pages_router

SessionDep = Annotated[Session, Depends(get_session)]
api = APIRouter(prefix="/api", tags=["api"])


@api.get("/leads")
def api_leads(request: Request, session: SessionDep) -> dict[str, Any]:
    """Ranked leads with score, play and insight summary. Same filters as the list page."""
    filters = data.Filters.from_params(request.query_params)
    leads = data.list_leads(session, filters)
    return {"count": len(leads), "filters": filters.as_dict(), "items": leads}


@api.get("/leads/{gaf_id}")
def api_lead(gaf_id: str, session: SessionDep) -> dict[str, Any]:
    """One lead with reviews, score factors, insight and extracted signals."""
    lead = data.lead_detail(session, gaf_id)
    if lead is None:
        raise HTTPException(404, f"No contractor with GAF id {gaf_id}")
    return lead


@api.get("/runs")
def api_runs(session: SessionDep, limit: int = 20) -> dict[str, Any]:
    """Latest pipeline runs with status and per-step counts."""
    return {"items": data.list_runs(session, limit=min(max(limit, 1), 100))}


@api.post("/pipeline/runs", status_code=501)
def api_trigger_run() -> JSONResponse:
    """Not wired yet: runs are started from the CLI so a web request can't launch a long scrape."""
    return JSONResponse(
        {"detail": "Not implemented here; trigger via `uv run python -m app.ingest.run`."},
        status_code=501,
    )


def create_app() -> FastAPI:
    app = FastAPI(title="Roofing Leads", version="0.1.0")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(api)
    app.include_router(pages_router)
    app.include_router(actions_router)
    app.include_router(chat_router)
    app.include_router(explain_router)
    return app


app = create_app()
