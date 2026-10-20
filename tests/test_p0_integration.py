"""P0 integration tests.

1. End to end on the test database: saved GAF pages → ingest → extract → discover → score →
   write → the rep UI and API, with the LLM stubbed (no network, deterministic).
2. Against the real `leads` database: every list filter and sort returns exactly what SQL says,
   and every citation chip on every lead resolves to a stored value.
"""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api import app
from app.config import Settings
from app.db import get_engine, session_factory
from app.ingest.run import ingest
from app.intel import discover as discover_mod
from app.intel import extract as extract_mod
from app.intel import insight as insight_mod
from app.intel.discover import discover
from app.intel.extract import EMPTY, Person, ServiceSignal, load
from app.intel.insight import DecisionMaker, Insight, Point
from app.intel.run import load_contractors, run_extract, run_insights, run_scores
from app.models import Base, Contractor, ContractorSignals, LeadInsight, LeadScore
from app.web.data import Filters, lead_detail, list_leads
from app.web.deps import get_session

FIX = Path(__file__).parent / "fixtures"
TEST_DB = "postgresql+psycopg://leads:leads@localhost:5433/leads_test"
ALLIED = "1116285"
JONATHAN_QUOTE = "Jonathan Guglielmino was our main contact throughout the project"


# --- 1. end to end on the test database ----------------------------------------------------------


def fake_structured(**kw):
    """Stands in for the OpenAI call. Returns grounded output for Allied and empty output
    otherwise, so the real verbatim checks and validators run on it."""
    schema, gaf_id = kw["schema"], kw["gaf_id"]
    if schema is extract_mod.Signals:
        if gaf_id != ALLIED:
            return EMPTY
        return EMPTY.model_copy(update={
            "services": [ServiceSignal(quote="roofing, gutter, siding, window, and door projects",
                                       source="about", service="siding")],
            "people": [Person(quote=JONATHAN_QUOTE, source="review_3",
                              name="Jonathan Guglielmino", role="main contact")],
        })  # fmt: skip
    if schema is discover_mod.Grouping:
        return discover_mod.Grouping(groups=[])
    if schema is Insight:
        people = [DecisionMaker(name="Jonathan Guglielmino", role="main contact",
                                quote=JONATHAN_QUOTE, cite="review_3")] if gaf_id == ALLIED else []  # fmt: skip
        return Insight(
            summary="A GAF Master Elite contractor near 10013.",
            why_fit=[Point(text="Master Elite certified", cites=["certifications"])],
            signals=[Point(text="Within the territory", cites=["distance"])],
            decision_makers=people,
            talking_points=[Point(text="Ask about upcoming re-roofs", cites=[])],
            next_action="Call and ask for whoever orders materials.",
            confidence="medium",
        )
    raise AssertionError(f"unexpected schema {schema}")


@pytest.fixture
def test_session() -> Iterator[Session]:
    engine = get_engine(TEST_DB)
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    except OperationalError:
        pytest.skip("Postgres not running (docker compose up -d db)")
    with session_factory(engine)() as s:
        yield s


@pytest.fixture
def saved_run(tmp_path) -> Path:
    run = tmp_path / "seed"
    (run / "profiles").mkdir(parents=True)
    shutil.copy(FIX / "listing.json", run / "listing.json")
    entry = {
        "url": "x",
        "http_status": 200,
        "fetched_at": "2026-10-20T15:00:00+00:00",
        "sha256": "x",
    }
    manifest = [{"kind": "listing", "gaf_id": None, "path": "listing.json", **entry}]
    for gaf_id in (ALLIED, "1151902"):
        shutil.copy(FIX / f"profile_{gaf_id}.html", run / "profiles" / f"{gaf_id}.html")
        manifest.append(
            {"kind": "profile", "gaf_id": gaf_id, "path": f"profiles/{gaf_id}.html", **entry}
        )
    (run / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


@pytest.mark.db
def test_p0_end_to_end(test_session, saved_run, monkeypatch):
    for mod in (extract_mod, discover_mod, insight_mod):
        monkeypatch.setattr(mod, "structured", fake_structured)
    settings = Settings(raw_dir=saved_run, openai_api_key="test", llm_concurrency=2)

    run = ingest(settings, test_session, from_raw="seed")
    assert run.status == "succeeded" and run.listed == 3

    everyone = load_contractors(test_session)
    stats = run_extract(test_session, settings, everyone, force=False)
    assert stats["extracted"] == 3 and stats["quotes_dropped"] == 0
    promoted = frozenset(c.name for c in discover(test_session, settings))
    scores = run_scores(test_session, everyone)
    stats = run_insights(test_session, settings, everyone, scores, False, promoted)
    assert stats["insight_ok"] == 3

    # Re-running changes nothing: inputs are unchanged, so every LLM step is skipped.
    again = run_extract(test_session, settings, everyone, force=False)
    assert again["extract_cached"] == 3
    assert (
        run_insights(test_session, settings, everyone, scores, False, promoted)["insight_cached"]
        == 3
    )

    # The rep sees it: ranked list, detail with the verified contact and resolvable citations.
    rows = list_leads(test_session, Filters())
    assert [r["rank"] for r in rows] == [1, 2, 3] and all(r["score"] is not None for r in rows)
    detail = lead_detail(test_session, ALLIED)
    assert detail["insight"]["status"] == "ok"
    assert detail["insight"]["data"]["decision_makers"][0]["name"] == "Jonathan Guglielmino"
    assert {"certifications", "distance", "review_3"} <= detail["citations"].keys()

    app.dependency_overrides[get_session] = lambda: test_session
    try:
        client = TestClient(app)
        page = client.get(f"/leads/{ALLIED}")
        assert page.status_code == 200 and "Jonathan Guglielmino" in page.text
        assert client.get("/api/leads").json()["count"] == 3
    finally:
        app.dependency_overrides.clear()


# --- 2. filters, sorts and citations against the real data ---------------------------------------


@pytest.fixture(scope="module")
def leads_db() -> Iterator[Session]:
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except OperationalError:
        pytest.skip("Postgres not running")
    with session_factory(engine)() as s:
        if not s.scalar(select(func.count()).select_from(LeadScore)):
            pytest.skip("run the pipeline first (app.ingest.run, app.intel.run)")
        yield s


@pytest.fixture(scope="module")
def client(leads_db) -> TestClient:
    return TestClient(app)


FILTER_CASES = [
    ({"state": "NY"}, "c.state = 'NY'"),
    ({"state": "NJ"}, "c.state = 'NJ'"),
    ({"max_distance": "10"}, "c.distance_mi <= 10"),
    ({"max_distance": "15"}, "c.distance_mi <= 15"),
    ({"min_rating": "4.8"}, "c.rating >= 4.8"),
    ({"min_score": "50"}, "s.total >= 50"),
    ({"play": "Volume leader"}, "s.play = 'Volume leader'"),
    ({"play": "Commercial cross-sell"}, "s.play = 'Commercial cross-sell'"),
    ({"presidents_club": "1"}, "c.presidents_club_stars > 0"),
    ({"commercial": "1"}, "jsonb_array_length(c.commercial_certs) > 0"),
    ({"q": "roof"}, "c.name ilike '%roof%'"),
    ({"state": "NJ", "commercial": "1", "min_rating": "4.5"},
     "c.state = 'NJ' and jsonb_array_length(c.commercial_certs) > 0 and c.rating >= 4.5"),
]  # fmt: skip


@pytest.mark.db
@pytest.mark.parametrize(("params", "where"), FILTER_CASES)
def test_each_filter_matches_sql(client, leads_db, params, where):
    sql = f"select c.gaf_id from contractors c left join scores s using (gaf_id) where {where}"
    expected = {row[0] for row in leads_db.execute(text(sql))}
    api = {x["gaf_id"] for x in client.get("/api/leads", params=params).json()["items"]}
    assert api == expected and expected
    # The HTMX partial the filter form swaps in shows the same leads.
    html = client.get("/", params=params, headers={"HX-Request": "true"}).text
    assert "<html" not in html
    assert all(f"/leads/{gaf_id}" in html for gaf_id in expected)


@pytest.mark.db
@pytest.mark.parametrize(
    ("sort", "key", "descending"),
    [
        ("rank", "rank", False),
        ("distance", "distance_mi", False),
        ("reviews", "review_count", True),
    ],
)
def test_sorts_are_ordered(client, sort, key, descending):
    values = [x[key] for x in client.get("/api/leads", params={"sort": sort}).json()["items"]]
    assert values == sorted(values, reverse=descending)


@pytest.mark.db
def test_ranks_are_unique_and_dense(leads_db):
    ranks = sorted(leads_db.scalars(select(LeadScore.rank)))
    assert ranks == list(range(1, len(ranks) + 1))


@pytest.mark.db
def test_every_citation_resolves_for_every_lead(leads_db):
    """Regression: chips citing 'certifications' once showed 'Not in stored data'."""
    unresolved = []
    for gaf_id in leads_db.scalars(select(Contractor.gaf_id)):
        d = lead_detail(leads_db, gaf_id)
        data = (d["insight"] or {}).get("data", {})
        keys = {
            k
            for sec in ("why_fit", "signals", "talking_points")
            for p in data.get(sec, [])
            for k in p["cites"]
        }
        keys |= {dm["cite"] for dm in data.get("decision_makers", [])}
        keys |= {k for f in (d["score_detail"] or {}).get("factors", []) for k in f["cites"]}
        unresolved += [(gaf_id, k) for k in keys if k not in d["citations"]]
    assert unresolved == []


@pytest.mark.db
def test_every_detail_page_renders(client, leads_db):
    ids = list(leads_db.scalars(select(Contractor.gaf_id)))
    ok = sum(client.get(f"/leads/{gaf_id}").status_code == 200 for gaf_id in ids)
    assert ok == len(ids) == 85


@pytest.mark.db
def test_insights_never_name_unverified_people(leads_db):
    """Every decision-maker in a stored insight is a person the extractor found with a quote."""
    for ins in leads_db.scalars(select(LeadInsight)):
        sig = leads_db.get(ContractorSignals, ins.gaf_id)
        people = {p.name for p in load(sig.data).people} if sig else set()
        assert {dm["name"] for dm in ins.data.get("decision_makers", [])} <= people, ins.gaf_id


@pytest.mark.db
def test_every_list_tag_quote_is_verbatim_in_the_source(leads_db):
    """A semantic tag is only as good as its quote: it must be in the contractor's own text."""
    norm = lambda t: " ".join(t.split()).lower()  # noqa: E731
    for row in list_leads(leads_db, Filters()):
        c = leads_db.get(Contractor, row["gaf_id"])
        sources = {"About": c.about or ""} | {
            f"Review {n}": r.text for n, r in enumerate(c.reviews, 1)
        }
        for tag in row["tags"]:
            assert norm(tag["quote"]) in norm(sources.get(tag["source"], "")), (row["gaf_id"], tag)


@pytest.mark.db
@pytest.mark.parametrize("key", ["storm", "warranty", "contact", "complaints", "trade:siding",
                                 "signal:family_owned_business", "signal:competitive_pricing"])  # fmt: skip
def test_tag_filter_matches_signals(client, leads_db, key):
    from app.intel.tags import semantic_tags
    from app.web.data import promoted_categories

    promoted = promoted_categories(leads_db)
    expected = {
        s.gaf_id for s in leads_db.scalars(select(ContractorSignals))
        if any(key in t["keys"] for t in semantic_tags(s.data, promoted))
    }  # fmt: skip
    got = {x["gaf_id"] for x in client.get("/api/leads", params={"tag": key}).json()["items"]}
    assert got == expected and expected, key
    assert f'value="{key}"' in client.get("/").text  # offered in the Signal filter menu
