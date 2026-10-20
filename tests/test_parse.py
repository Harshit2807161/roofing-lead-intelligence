"""Parser tests on real saved GAF pages: one rich profile (Allied) and one sparse one (Blackstone)."""

import json
from pathlib import Path

import pytest

from app.ingest.parse import digits, initials, merge, parse_listing, parse_profile

FIX = Path(__file__).parent / "fixtures"
ALLIED, BLACKSTONE = "1116285", "1151902"


@pytest.fixture(scope="module")
def listing_rows() -> dict[str, dict]:
    rows = parse_listing(json.loads((FIX / "listing.json").read_text()))
    return {r["gaf_id"]: r for r in rows}


@pytest.fixture(scope="module")
def allied() -> dict:
    return parse_profile((FIX / f"profile_{ALLIED}.html").read_text())


@pytest.fixture(scope="module")
def blackstone() -> dict:
    return parse_profile((FIX / f"profile_{BLACKSTONE}.html").read_text())


def test_listing_fields(listing_rows):
    a = listing_rows[ALLIED]
    assert a["name"] == "Allied Brothers Home Corporation"
    assert a["gaf_rank"] == 1
    assert a["rating"] == 4.9 and a["review_count"] == 283
    assert a["presidents_club_stars"] == 2
    assert "GAF Master Elite® - Certification" in a["residential_certs"]
    assert a["state"] == "NJ" and 18 < a["distance_mi"] < 19.5


def test_no_reviews_means_no_rating_and_placeholder_logo_is_dropped(listing_rows):
    b = listing_rows[BLACKSTONE]
    assert b["review_count"] == 0
    assert b["rating"] is None  # GAF's 0.0 means "no reviews", not a terrible score
    assert b["logo_url"] is None


def test_rich_profile(allied):
    assert allied["about"].startswith("Since its founding in 1996")
    assert allied["website"] == "https://alliedbrothers.net/"
    assert allied["license_number"] == "13VH08476800"
    assert allied["street"] == "20 Parish Dr"
    assert len(allied["reviews"]) == 3
    newest = allied["reviews"][0]
    assert newest.rating == 5 and newest.author_initials == "E.G"
    assert newest.published_at.year == 2026 and "Tyler" in newest.text


def test_sparse_profile(blackstone):
    assert blackstone["about"] is None
    assert blackstone["website"] is None
    assert blackstone["reviews"] == []
    assert blackstone["in_business_since"] == 1996
    assert blackstone["employees_band"] == "More than 5"
    assert blackstone["license_number"] == "13VH07739600"


def test_merge_uses_business_phone(listing_rows, allied, blackstone):
    # The call-tracking number seen in a browser is injected by script; the server HTML only has
    # the business line, so display_phone stays empty unless a page really carries another number.
    a = merge(listing_rows[ALLIED], allied)
    assert a.phone == "(973) 566-0060" and a.display_phone is None
    assert a.profile_fetched and len(a.reviews) == 3
    b = merge(listing_rows[BLACKSTONE], blackstone)
    assert b.display_phone is None and b.in_business_since == 1996


def test_merge_without_profile_keeps_listing_fields(listing_rows):
    rec = merge(listing_rows[ALLIED], None)
    assert rec.street == "20 Parish Dr"
    assert rec.profile_fetched is False and rec.reviews == []


def test_merge_rejects_a_profile_for_another_contractor(listing_rows, blackstone):
    with pytest.raises(ValueError, match="not 1116285"):
        merge(listing_rows[ALLIED], blackstone)


def test_helpers():
    assert initials("Elena Go") == "E.G"
    assert initials("Cher") == "C"
    assert initials("") is None
    assert digits("+(973) 624-6300") == digits("tel:+19736246300") == "9736246300"
