"""Load tests against the Postgres test database: re-running must not create duplicates."""

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.db import get_engine, session_factory
from app.ingest.load import dedupe, possible_duplicates
from app.ingest.parse import merge, parse_listing
from app.ingest.run import ingest
from app.models import Base, Contractor, Review, Run

FIX = Path(__file__).parent / "fixtures"
TEST_DB = "postgresql+psycopg://leads:leads@localhost:5433/leads_test"


@pytest.fixture
def session():
    engine = get_engine(TEST_DB)
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    except OperationalError:
        pytest.skip("Postgres not running (docker compose up -d db)")
    with session_factory(engine)() as s:
        yield s


@pytest.fixture
def raw_dir(tmp_path) -> Path:
    """A saved run with the fixture listing and its two profiles; the third has no profile."""
    run = tmp_path / "fixture-run"
    (run / "profiles").mkdir(parents=True)
    shutil.copy(FIX / "listing.json", run / "listing.json")
    manifest = [
        {
            "kind": "listing",
            "gaf_id": None,
            "url": "x",
            "http_status": 200,
            "fetched_at": "2026-10-20T15:00:00+00:00",
            "sha256": "x",
            "path": "listing.json",
        }
    ]
    for gaf_id in ("1116285", "1151902"):
        shutil.copy(FIX / f"profile_{gaf_id}.html", run / "profiles" / f"{gaf_id}.html")
        manifest.append(
            {
                "kind": "profile",
                "gaf_id": gaf_id,
                "url": "x",
                "http_status": 200,
                "fetched_at": "2026-10-20T15:00:00+00:00",
                "sha256": "x",
                "path": f"profiles/{gaf_id}.html",
            }
        )
    (run / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


@pytest.mark.db
def test_rerun_is_idempotent(session, raw_dir):
    settings = Settings(raw_dir=raw_dir)
    first = ingest(settings, session, from_raw="fixture-run")
    second = ingest(settings, session, from_raw="fixture-run")

    listed = len(json.loads((raw_dir / "fixture-run" / "listing.json").read_text())["results"])
    assert session.scalar(select(func.count()).select_from(Contractor)) == listed
    assert session.scalar(select(func.count()).select_from(Review)) == 3  # Allied's three, once
    assert second.summary["new_reviews"] == 0

    allied = session.get(Contractor, "1116285")
    assert allied.first_seen_run == first.id and allied.last_seen_run == second.id
    assert allied.profile_snapshot_id is not None
    assert {r.status for r in session.scalars(select(Run))} <= {"succeeded", "partial"}


def test_dedupe_keeps_first_and_flags_lookalikes():
    rows = parse_listing(json.loads((FIX / "listing.json").read_text()))
    a = merge(rows[0], None)
    twin = a.model_copy(update={"gaf_id": "999", "gaf_rank": 99})
    records, dropped = dedupe([a, a.model_copy(update={"gaf_rank": 50}), twin])
    assert dropped == 1 and [r.gaf_rank for r in records] == [a.gaf_rank, 99]
    flags = possible_duplicates(records)
    assert {f["key"] for f in flags} == {"phone", "google_place_id", "name_zip"}
