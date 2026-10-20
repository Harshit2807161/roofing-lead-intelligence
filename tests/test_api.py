"""Rep UI and JSON API against the real `leads` database (skipped when Postgres isn't running).

Read-only: these tests never write, so they're safe while the pipeline fills scores and insights.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.api import app
from app.db import get_engine
from app.web.data import Filters, initials

KNOWN_ID = "1116285"
KNOWN_NAME = "Allied Brothers Home Corporation"


@pytest.fixture(scope="module")
def client() -> TestClient:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("select 1"))
    except OperationalError:
        pytest.skip("Postgres not running (docker compose up -d db)")
    return TestClient(app)


@pytest.mark.db
def test_list_page_renders_known_contractor(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert KNOWN_NAME in r.text
    assert 'id="lead-results"' in r.text


@pytest.mark.db
def test_htmx_filter_returns_partial_only(client: TestClient) -> None:
    r = client.get("/", params={"state": "NY"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<html" not in r.text
    assert KNOWN_NAME not in r.text  # Wayne, NJ is filtered out


@pytest.mark.db
def test_detail_page(client: TestClient) -> None:
    r = client.get(f"/leads/{KNOWN_ID}")
    assert r.status_code == 200
    assert KNOWN_NAME in r.text
    assert "Reviews" in r.text


@pytest.mark.db
def test_unknown_lead_is_404(client: TestClient) -> None:
    assert client.get("/leads/does-not-exist").status_code == 404
    assert client.get("/api/leads/does-not-exist").status_code == 404


@pytest.mark.db
def test_api_leads_returns_all_85(client: TestClient) -> None:
    body = client.get("/api/leads").json()
    assert body["count"] == 85
    assert len(body["items"]) == 85
    first = body["items"][0]
    assert {"gaf_id", "name", "score", "play", "summary", "rank"} <= first.keys()


@pytest.mark.db
def test_api_leads_filters(client: TestClient) -> None:
    body = client.get("/api/leads", params={"state": "NJ", "max_distance": "10"}).json()
    assert body["items"], "expected some NJ leads within 10 miles"
    assert all(x["state"] == "NJ" and x["distance_mi"] <= 10 for x in body["items"])


@pytest.mark.db
def test_api_lead_has_reviews(client: TestClient) -> None:
    body = client.get(f"/api/leads/{KNOWN_ID}").json()
    assert body["name"] == KNOWN_NAME
    assert body["reviews"]
    assert body["reviews"][0]["cite"] == "review_1"
    assert "review_1" in body["citations"]


@pytest.mark.db
def test_api_runs_and_pipeline_stub(client: TestClient) -> None:
    runs = client.get("/api/runs").json()["items"]
    assert runs and {"id", "status", "parsed"} <= runs[0].keys()
    r = client.post("/api/pipeline/runs")
    assert r.status_code == 501
    assert "app.ingest.run" in r.json()["detail"]


def test_style_card_needs_no_database() -> None:
    r = TestClient(app).get("/style")
    assert r.status_code == 200
    assert "DM Mono" in r.text
    assert "/static/tokens.css" in r.text


def test_filters_parse_blank_fields_as_no_filter() -> None:
    f = Filters.from_params({"state": "", "max_distance": "", "min_rating": "abc", "sort": "nope"})
    assert f == Filters()
    assert Filters.from_params({"state": "nj", "commercial": "on"}).state == "NJ"


def test_initials_skip_filler_words() -> None:
    assert initials("The Roof Doctor LLC") == "RD"
    assert initials(KNOWN_NAME) == "AB"
