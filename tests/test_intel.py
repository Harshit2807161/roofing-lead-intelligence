"""Intelligence step without the network: the verbatim check, the score, the insight validator and
label grouping. These are the code half of "the LLM proposes, code disposes"."""

from datetime import UTC, datetime, timedelta

import pytest

from app.intel import discover
from app.intel.extract import EMPTY, OpenSignal, Person, Quoted, ServiceSignal, Signals, verify
from app.intel.insight import DecisionMaker, Insight, Point, validate
from app.intel.score import adjusted_rating, score_all
from app.models import Contractor, Review

TODAY = datetime(2026, 10, 20, tzinfo=UTC)
ABOUT = "Since its founding in 1996 we have completed roofing, gutter and siding projects."
REVIEW = "Jonathan Guglielmino was our main contact throughout the project. Great crew."


def contractor(gaf_id: str = "1", **kw) -> Contractor:
    fields = dict(
        gaf_id=gaf_id, name=f"Roofer {gaf_id}", profile_url="u", gaf_rank=int(gaf_id), street="1 Main St",
        city="Wayne", state="NJ", postal_code="07470", lat=40.9, lng=-74.2, distance_mi=12.0,
        phone="(973) 566-0060", rating=4.9, review_count=283, presidents_club_stars=0,
        residential_certs=["GAF Master Elite® - Certification"], commercial_certs=[],
        commercial_technologies=[], specialties=[], about=ABOUT, in_business_since=None,
        employees_band=None, website=None, license_number=None,
    )  # fmt: skip
    fields.update(kw)
    c = Contractor(**fields)
    c.reviews = [Review(published_at=TODAY - timedelta(days=30), rating=5, text=REVIEW)]
    return c


SOURCES = {"about": ABOUT, "review_1": REVIEW}


def signals(**kw) -> Signals:
    return EMPTY.model_copy(update=kw)


def test_verify_keeps_verbatim_quotes_and_drops_invented_ones():
    raw = signals(
        services=[
            ServiceSignal(quote="roofing, gutter and siding projects", source="about", service="siding"),
            ServiceSignal(quote="we install solar panels", source="about", service="solar"),  # not in text
        ],
        people=[
            Person(quote="Jonathan Guglielmino was our main contact", source="review_1",
                   name="Jonathan Guglielmino", role="main contact"),
            Person(quote="Great crew.", source="review_1", name="Mike", role=None),  # name not in quote
        ],
        founded_year=1997, founded_quote=Quoted(quote="Since its founding in 1996", source="about"),
        other_signals=[OpenSignal(quote="Great crew.", source="review_1", category="Crew Quality!", detail="x")],
    )  # fmt: skip
    kept, dropped = verify(raw, SOURCES)
    assert [s.service for s in kept.services] == ["siding"]
    assert [p.name for p in kept.people] == ["Jonathan Guglielmino"]
    assert kept.founded_year is None  # quote says 1996, model said 1997
    assert kept.other_signals[0].category == "crew_quality"
    assert {d["field"] for d in dropped} == {"services", "people", "founded"}


def test_adjusted_rating_trusts_volume():
    assert adjusted_rating(5.0, 2, 4.7) < adjusted_rating(4.9, 283, 4.7)
    assert adjusted_rating(None, 0, 4.7) is None


def test_score_is_capped_explained_and_ranks_presidents_club_first():
    plain, award = contractor("1"), contractor("2", presidents_club_stars=2)
    rows = score_all([plain, award], {}, today=TODAY)
    assert [r["gaf_id"] for r in rows] == ["2", "1"] and rows[0]["rank"] == 1
    for r in rows:
        assert 0 <= r["total"] <= 100
        assert sum(f["max"] for f in r["factors"]) == 100  # caps add to 100
        assert all(f["reason"] for f in r["factors"])
    assert rows[0]["play"] == "Volume leader"


def test_extracted_signals_add_points_only_through_their_factors():
    c = contractor("1")
    with_signals = signals(
        services=[ServiceSignal(quote="gutter and siding", source="about", service=s) for s in ("siding", "gutters")],
        people=[Person(quote="Jonathan Guglielmino was our main contact", source="review_1",
                       name="Jonathan Guglielmino", role=None)],
    )  # fmt: skip
    base = score_all([c], {}, today=TODAY)[0]
    richer = score_all([c], {"1": with_signals}, today=TODAY)[0]
    gained = {f["key"]: f["points"] for f in richer["factors"]}
    assert (
        richer["total"] - base["total"] == gained["multi_trade"] + gained["named_contact"] == 4 + 4
    )


def good_insight(**kw) -> Insight:
    fields = dict(
        summary="Busy Wayne roofer.", why_fit=[Point(text="283 reviews", cites=["rating"])],
        signals=[Point(text="Founded 1996", cites=["about"])],
        decision_makers=[DecisionMaker(name="Jonathan Guglielmino", role=None,
                                       quote="Jonathan Guglielmino was our main contact", cite="review_1")],
        talking_points=[Point(text="Ask about siding volume", cites=[])],
        next_action="Call (973) 566-0060 and ask for Jonathan.", confidence="high",
    )  # fmt: skip
    fields.update(kw)
    return Insight(**fields)


PEOPLE = signals(people=[Person(quote="Jonathan Guglielmino was our main contact", source="review_1",
                                name="Jonathan Guglielmino", role=None)])  # fmt: skip


def test_validator_accepts_a_grounded_insight():
    assert validate(good_insight(), contractor(), PEOPLE) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"why_fit": [Point(text="x", cites=["revenue"])]}, "unknown cite keys"),
        ({"decision_makers": [DecisionMaker(name="Mike Smith", role="owner", quote="q", cite="about")]}, "not in PEOPLE"),
        ({"next_action": "Email mike@roofer.com or call 201-555-0199."}, "contact detail not in our data"),
        ({"signals": []}, "signals is empty"),
    ],
)  # fmt: skip
def test_validator_catches_ungrounded_output(change, expected):
    errors = validate(good_insight(**change), contractor(), PEOPLE)
    assert any(expected in e for e in errors), errors


def test_label_grouping_falls_back_and_ignores_invented_labels(monkeypatch):
    def boom(**_):
        raise RuntimeError("offline")

    monkeypatch.setattr(discover, "structured", boom)
    assert discover.group_labels({"financing": "x"}, {}, "m") == {"financing": ("financing", "")}

    grouped = discover.Grouping(groups=[discover.LabelGroup(
        canonical="Storm Repair", description="Storm work", raw_labels=["storm_repair", "storm_damage", "made_up"])])  # fmt: skip
    monkeypatch.setattr(discover, "structured", lambda **_: grouped)
    out = discover.group_labels(
        {"storm_repair": "x", "storm_damage": "y", "financing": "z"}, {}, "m"
    )
    assert out["storm_repair"] == out["storm_damage"] == ("storm_repair", "Storm work")
    assert out["financing"] == ("financing", "") and "made_up" not in out
