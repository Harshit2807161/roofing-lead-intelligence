"""F14 compare and F15 tuner: the pure verdict/pairing helpers, plus both pages against the real
`leads` database (those skip when Postgres isn't running). Read-only."""

import json
import re

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db import get_engine, session_factory
from app.web.deps import STATIC_DIR
from app.web.explain_pages import (
    FactorGap,
    factor_gaps,
    pick_pair,
    ranked_leads,
    router,
    short_name,
    verdict,
)


def _f(key: str, label: str, points: float, top: float, reason: str = "") -> dict:
    return {"key": key, "label": label, "points": points, "max": top, "reason": reason}


# Allied Brothers (#1) vs Complete Roof Systems (#2), trimmed to the factors that matter.
ALLIED = [
    _f("presidents_club", "President's Club", 20, 20),
    _f("review_volume", "Job volume (reviews)", 14.7, 18),
    _f("rating", "Reputation", 8.9, 10),
    _f("commercial", "Commercial capability", 4, 12),
    _f("established", "Established", 6, 6),
]
COMPLETE = [
    _f("presidents_club", "President's Club", 0, 20),
    _f("review_volume", "Job volume (reviews)", 10.5, 18),
    _f("rating", "Reputation", 8.9, 10),
    _f("commercial", "Commercial capability", 12, 12),
    _f("established", "Established", 6, 6),
]


# --- pure ---------------------------------------------------------------------------------------


def test_verdict_names_leader_top_factors_and_runner_up_edge() -> None:
    gaps = factor_gaps(ALLIED, COMPLETE)
    s = verdict("Allied Brothers", 53.6, "Complete Roof Systems", 37.4, gaps)
    assert s == (
        "Allied Brothers leads Complete Roof Systems by 16.2 points, on President's Club (+20) "
        "and job volume (+4.2); Complete Roof Systems is ahead on commercial capability (+8)."
    )


def test_verdict_says_mostly_when_leader_wins_more_than_two_factors() -> None:
    a = [*ALLIED, _f("proximity", "Proximity to 10013", 4, 6)]
    b = [*COMPLETE, _f("proximity", "Proximity to 10013", 2, 6)]
    s = verdict("A", 57.6, "B", 39.4, factor_gaps(a, b))
    assert s.startswith("A leads B by 18.2 points, mostly on President's Club (+20) and job volume")


def test_verdict_speaks_from_the_leader_when_b_is_ahead() -> None:
    s = verdict(
        "Complete Roof Systems", 37.4, "Allied Brothers", 53.6, factor_gaps(COMPLETE, ALLIED)
    )
    assert s.startswith("Allied Brothers leads Complete Roof Systems by 16.2 points")
    assert s.endswith("Complete Roof Systems is ahead on commercial capability (+8).")


def test_verdict_tie_and_penalty_phrasing() -> None:
    same = factor_gaps(ALLIED, ALLIED)
    assert verdict("A", 53.6, "B", 53.6, same) == "A and B are tied at 53.6 points on every factor."
    # B has a complaints penalty A doesn't: A is "ahead" on fewer complaints, and a missing factor is 0.
    b = [
        _f("rating", "Reputation", 8, 10),
        _f("complaints", "Complaints in reviews", -3, 0, "late"),
    ]
    gaps = factor_gaps([_f("rating", "Reputation", 8, 10)], b)
    assert gaps[1] == FactorGap(
        "complaints", "Complaints in reviews", 0, -3, 0, "No complaints in reviews", "late"
    )
    assert (
        verdict("A", 8, "B", 5, gaps) == "A leads B by 3 points, on fewer review complaints (+3)."
    )


def test_short_name_drops_corporate_suffixes() -> None:
    assert short_name("Allied Brothers Home Corporation") == "Allied Brothers"
    assert short_name("Penyak Roofing Co Inc") == "Penyak Roofing"
    assert short_name("Complete Roof Systems") == "Complete Roof Systems"
    assert short_name("Inc") == "Inc"


def test_pick_pair_defaults() -> None:
    order = ["r1", "r2", "r3"]
    assert pick_pair(order, None, None) == ("r1", "r2")
    assert pick_pair(order, "r2", None) == ("r2", "r3")
    assert pick_pair(order, "r3", None) == ("r3", "r2")  # last lead: compare with the one above
    assert pick_pair(order, None, "r1") == ("r2", "r1")
    assert pick_pair(order, "r3", "r1") == ("r3", "r1")


# --- pages (real database) ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def leads() -> list[dict]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("select 1"))
    except OperationalError:
        pytest.skip("Postgres not running (docker compose up -d db)")
    with session_factory(get_engine())() as s:
        rows = ranked_leads(s)
    if len(rows) < 3 or rows[0]["score"] is None:
        pytest.skip("Needs scored leads (run the intel step)")
    return rows


@pytest.fixture(scope="module")
def client(leads: list[dict]) -> TestClient:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)
    return TestClient(app)


def _selected(html: str, name: str) -> str:
    block = re.search(rf'<select name="{name}">(.*?)</select>', html, re.S).group(1)
    return re.search(r'<option value="([^"]+)" selected>', block).group(1)


@pytest.mark.db
def test_compare_defaults_to_first_two(client: TestClient, leads: list[dict]) -> None:
    r = client.get("/compare")
    assert r.status_code == 200
    assert _selected(r.text, "a") == leads[0]["gaf_id"]
    assert _selected(r.text, "b") == leads[1]["gaf_id"]
    assert 'class="vs-summary"' in r.text
    assert f"{leads[0]['short']} leads {leads[1]['short']}" in r.text.replace("&#39;", "'")
    assert r.text.count('class="dv-row"') >= 1  # at least one factor differs


@pytest.mark.db
def test_compare_explicit_pair_and_only_a(client: TestClient, leads: list[dict]) -> None:
    a, b = leads[2]["gaf_id"], leads[0]["gaf_id"]
    r = client.get("/compare", params={"a": a, "b": b})
    assert r.status_code == 200
    assert (_selected(r.text, "a"), _selected(r.text, "b")) == (a, b)
    assert f"/leads/{a}" in r.text and f"/leads/{b}" in r.text

    r = client.get("/compare", params={"a": leads[1]["gaf_id"]})
    assert _selected(r.text, "b") == leads[2]["gaf_id"]  # the lead ranked just below a


@pytest.mark.db
def test_compare_unknown_id_is_404(client: TestClient) -> None:
    r = client.get("/compare", params={"a": "does-not-exist"})
    assert r.status_code == 404
    assert "does-not-exist" in r.text


@pytest.mark.db
def test_tuner_embeds_every_scored_lead(client: TestClient, leads: list[dict]) -> None:
    r = client.get("/tuner")
    assert r.status_code == 200
    assert "/static/tuner.js" in r.text and "Copy weights (JSON)" in r.text
    raw = re.search(r'<script type="application/json" id="tuner-data">(.*?)</script>', r.text, re.S)
    payload = json.loads(raw.group(1))
    scored = [x for x in leads if x["score"] is not None]
    assert len(payload["leads"]) == len(scored)
    assert [f["key"] for f in payload["factors"]][:2] == ["presidents_club", "review_volume"]
    assert len(payload["factors"]) == 10 and all(f["max"] > 0 for f in payload["factors"])
    # At 1x weights the recomputed totals equal the official scores, so nothing moves on load.
    for lead in payload["leads"]:
        assert round(max(0.0, sum(f["p"] for f in lead["f"])), 1) == lead["total"]
    assert payload["leads"][0]["name"] == leads[0]["name"]
