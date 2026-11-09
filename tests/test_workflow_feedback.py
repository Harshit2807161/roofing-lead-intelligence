"""P1 lead workflow (F9) and insight feedback (F12), on the test database (never the demo data)."""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api import app
from app.config import Settings
from app.db import get_engine, session_factory
from app.ingest.run import ingest
from app.models import Base, InsightFeedback, LeadInsight
from app.web.data import Filters, list_leads
from app.web.deps import get_session

FIX = Path(__file__).parent / "fixtures"
TEST_DB = "postgresql+psycopg://leads:leads@localhost:5433/leads_test"
ALLIED = "1116285"


@pytest.fixture
def client(tmp_path) -> Iterator[tuple[TestClient, Session]]:
    engine = get_engine(TEST_DB)
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    except OperationalError:
        pytest.skip("Postgres not running")
    run = tmp_path / "seed"
    (run / "profiles").mkdir(parents=True)
    shutil.copy(FIX / "listing.json", run / "listing.json")
    shutil.copy(FIX / f"profile_{ALLIED}.html", run / "profiles" / f"{ALLIED}.html")
    entry = {
        "url": "x",
        "http_status": 200,
        "fetched_at": "2026-10-20T15:00:00+00:00",
        "sha256": "x",
    }
    (run / "manifest.json").write_text(json.dumps([
        {"kind": "listing", "gaf_id": None, "path": "listing.json", **entry},
        {"kind": "profile", "gaf_id": ALLIED, "path": f"profiles/{ALLIED}.html", **entry},
    ]))  # fmt: skip
    with session_factory(engine)() as session:
        ingest(Settings(raw_dir=tmp_path), session, from_raw="seed")
        session.add(LeadInsight(gaf_id=ALLIED, model="gpt-test", prompt_version="insight-v1",
                                input_hash="h1", status="ok", data={"summary": "s"}))  # fmt: skip
        session.commit()
        app.dependency_overrides[get_session] = lambda: session
        try:
            yield TestClient(app), session
        finally:
            app.dependency_overrides.clear()


@pytest.mark.db
def test_status_and_notes_are_appended_and_latest_status_wins(client):
    c, session = client
    assert c.post(f"/api/leads/{ALLIED}/activity", json={"status": "contacted"}).status_code == 201
    c.post(f"/api/leads/{ALLIED}/activity", json={"note": "Spoke to Jonathan, call back Thursday"})
    body = c.post(
        f"/api/leads/{ALLIED}/activity", json={"status": "not_a_fit", "note": "Buys direct"}
    ).json()
    assert body["status"] == "not_a_fit"
    assert [h["status"] for h in body["history"]] == [
        "not_a_fit",
        None,
        "contacted",
    ]  # newest first
    rows = {r["gaf_id"]: r for r in list_leads(session, Filters())}
    assert rows[ALLIED]["status"] == "not_a_fit"
    assert {r["status"] for gid, r in rows.items() if gid != ALLIED} == {"new"}
    assert [r["gaf_id"] for r in list_leads(session, Filters(status="not_a_fit"))] == [ALLIED]


@pytest.mark.db
@pytest.mark.parametrize(
    ("payload", "code"),
    [({"status": "won"}, 422), ({}, 422), ({"note": "   "}, 422)],
)
def test_bad_workflow_requests_are_rejected(client, payload, code):
    c, _ = client
    assert c.post(f"/api/leads/{ALLIED}/activity", json=payload).status_code == code
    assert c.post("/api/leads/nope/activity", json={"status": "contacted"}).status_code == 404


@pytest.mark.db
def test_htmx_workflow_form_returns_the_refreshed_card(client):
    c, _ = client
    r = c.post(f"/leads/{ALLIED}/activity", data={"status": "contacted"})
    assert r.status_code == 200 and 'id="workflow-card"' in r.text and "Contacted" in r.text
    assert "<html" not in r.text
    page = c.get(f"/leads/{ALLIED}").text
    assert 'id="workflow-card"' in page and 'id="feedback"' in page


@pytest.mark.db
def test_feedback_is_tied_to_the_prompt_that_wrote_the_insight(client):
    c, session = client
    c.post(f"/api/leads/{ALLIED}/feedback", json={"vote": 1})
    body = c.post(
        f"/api/leads/{ALLIED}/feedback", json={"vote": -1, "reason": "Too generic"}
    ).json()
    assert body == {"up": 1, "down": 1, "reasons": {"Too generic": 1}}
    down = session.query(InsightFeedback).filter_by(vote=-1).one()
    assert (down.prompt_version, down.input_hash, down.model) == ("insight-v1", "h1", "gpt-test")
    assert c.post(f"/api/leads/{ALLIED}/feedback", json={"vote": 5}).status_code == 422
    assert (
        c.post(f"/api/leads/{ALLIED}/feedback", json={"vote": -1, "reason": "x"}).status_code == 422
    )
    assert (
        c.post("/api/leads/1151902/feedback", json={"vote": 1}).status_code == 409
    )  # no insight yet
    r = c.post(f"/leads/{ALLIED}/feedback", data={"vote": "1"})
    assert r.status_code == 200 and "Thanks, recorded" in r.text and "2 useful" in r.text
