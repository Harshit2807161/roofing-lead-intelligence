"""Ingest pipeline: fetch → parse → dedupe → load, recorded as one run.

uv run python -m app.ingest.run                      # fetch from GAF, then load
uv run python -m app.ingest.run --from-raw <dir>     # re-parse saved files, no network
uv run python -m app.ingest.run --limit 5            # quick trial on the first 5 contractors
"""

import argparse
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_engine, init_db, session_factory
from app.ingest.fetch import run_fetch
from app.ingest.load import (
    dedupe,
    insert_reviews,
    possible_duplicates,
    save_snapshots,
    upsert_contractors,
)
from app.ingest.parse import merge, parse_listing, parse_profile
from app.ingest.schemas import ContractorRecord
from app.models import Run

log = logging.getLogger("ingest")


def parse_run_dir(run_dir: Path, manifest: list[dict]) -> tuple[list[ContractorRecord], list[dict]]:
    """Typed records for every listed contractor. A profile that fails to parse keeps its listing
    fields, so one bad page never drops a lead; the error is returned for the run summary."""
    listing = json.loads((run_dir / "listing.json").read_text())
    profile_paths = {
        e["gaf_id"]: e["path"] for e in manifest if e["kind"] == "profile" and e["path"]
    }
    records, errors = [], []
    for row in parse_listing(listing):
        profile = None
        if path := profile_paths.get(row["gaf_id"]):
            try:
                profile = parse_profile((run_dir / path).read_text())
            except Exception as exc:
                errors.append({"gaf_id": row["gaf_id"], "step": "parse_profile", "error": str(exc)})
        try:
            records.append(merge(row, profile))
        except Exception as exc:
            errors.append({"gaf_id": row["gaf_id"], "step": "merge", "error": str(exc)})
            records.append(merge(row, None))
    return records, errors


def ingest(
    settings: Settings, session: Session, from_raw: str | None = None, limit: int | None = None
) -> Run:
    run_id = datetime.now(UTC).strftime("r%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
    run = Run(
        id=run_id,
        query={
            "listing_page_url": settings.listing_page_url,
            "radius_mi": settings.search_radius_mi,
            "center": [settings.search_center_lat, settings.search_center_lng],
            "recommended_only": settings.search_recommended_only,
            "from_raw": from_raw,
        },
    )
    session.add(run)
    session.commit()

    try:
        raw_subdir = from_raw or run_id
        run_dir = settings.raw_dir / raw_subdir
        if not from_raw:
            run_fetch(settings, run_id, limit=limit)
        manifest = json.loads((run_dir / "manifest.json").read_text())

        records, errors = parse_run_dir(run_dir, manifest)
        records, dropped = dedupe(records)
        snapshot_ids = save_snapshots(session, run_id, raw_subdir, manifest)
        upsert_contractors(session, run_id, records, snapshot_ids)
        new_reviews = insert_reviews(session, run_id, records)

        fetch_failures = [e["gaf_id"] for e in manifest if e["kind"] == "profile" and not e["path"]]
        run.listed = len(records)
        run.fetched = sum(1 for e in manifest if e["kind"] == "profile" and e["path"])
        run.parsed = sum(1 for r in records if r.profile_fetched)
        run.failed = len(fetch_failures) + len(errors)
        run.summary = {
            "duplicates_dropped": dropped,
            "possible_duplicates": possible_duplicates(records),
            "fetch_failures": fetch_failures,
            "parse_errors": errors,
            "new_reviews": new_reviews,
        }
        run.status = "succeeded" if run.failed == 0 else "partial"
    except Exception as exc:
        session.rollback()
        run.status, run.summary = "failed", {"error": str(exc)}
        raise
    finally:
        run.finished_at = datetime.now(UTC)
        session.merge(run)
        session.commit()
    return run


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--from-raw", help="name of a folder under data/raw to re-parse, no fetching"
    )
    parser.add_argument("--limit", type=int, help="only the first N contractors (fetch mode)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = get_settings()
    engine = get_engine()
    init_db(engine)
    with session_factory(engine)() as session:
        run = ingest(settings, session, from_raw=args.from_raw, limit=args.limit)
    print(
        f"run {run.id}: {run.status} · listed {run.listed} · profiles fetched {run.fetched} · "
        f"parsed {run.parsed} · failed {run.failed} · new reviews {run.summary.get('new_reviews')} · "
        f"possible duplicates {len(run.summary.get('possible_duplicates', []))}"
    )


if __name__ == "__main__":
    main()
