"""Dedupe and load step: typed records into Postgres. Safe to run again on the same data."""

import hashlib
import re
from collections import defaultdict
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.ingest.parse import digits
from app.ingest.schemas import ContractorRecord
from app.models import Contractor, RawSnapshot, Review

# Columns a re-run may overwrite. first_seen_run is set once and never touched again.
CONTRACTOR_FIELDS = [
    c.name
    for c in Contractor.__table__.columns
    if c.name not in ("gaf_id", "first_seen_run", "updated_at")
]


def dedupe(records: list[ContractorRecord]) -> tuple[list[ContractorRecord], int]:
    """One record per GAF ID, keeping the first (best-ranked) one. Returns (records, dropped)."""
    seen: dict[str, ContractorRecord] = {}
    for rec in records:
        seen.setdefault(rec.gaf_id, rec)
    return list(seen.values()), len(records) - len(seen)


def possible_duplicates(records: list[ContractorRecord]) -> list[dict]:
    """Different IDs that look like the same business. Flagged for a human, never merged."""
    keys = {
        "phone": lambda r: digits(r.phone) or None,
        "google_place_id": lambda r: r.google_place_id,
        "name_zip": lambda r: f"{re.sub(r'[^a-z0-9]', '', r.name.lower())}|{r.postal_code}",
    }
    flags = []
    for key_name, key_fn in keys.items():
        groups: dict[str, list[str]] = defaultdict(list)
        for rec in records:
            if (k := key_fn(rec)) is not None:
                groups[k].append(rec.gaf_id)
        flags += [
            {"key": key_name, "value": k, "gaf_ids": ids}
            for k, ids in groups.items()
            if len(ids) > 1
        ]
    return flags


def save_snapshots(session: Session, run_id: str, raw_subdir: str, manifest: list[dict]) -> dict:
    """Snapshot rows for the run's raw files. Returns {(kind, gaf_id): snapshot_id}."""
    ids = {}
    for entry in manifest:
        snap = RawSnapshot(
            run_id=run_id,
            kind=entry["kind"],
            gaf_id=entry["gaf_id"],
            url=entry["url"],
            fetched_at=datetime.fromisoformat(entry["fetched_at"]),
            http_status=entry["http_status"],
            sha256=entry["sha256"],
            path=f"{raw_subdir}/{entry['path']}" if entry["path"] else None,
        )
        session.add(snap)
        session.flush()
        ids[(entry["kind"], entry["gaf_id"])] = snap.id
    return ids


def upsert_contractors(
    session: Session, run_id: str, records: list[ContractorRecord], snapshot_ids: dict
) -> None:
    for rec in records:
        values = rec.model_dump(exclude={"reviews"})
        values.update(
            listing_snapshot_id=snapshot_ids.get(("listing", None)),
            profile_snapshot_id=snapshot_ids.get(("profile", rec.gaf_id)),
            first_seen_run=run_id,
            last_seen_run=run_id,
        )
        stmt = insert(Contractor).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Contractor.gaf_id],
            set_={f: stmt.excluded[f] for f in CONTRACTOR_FIELDS} | {"updated_at": func.now()},
        )
        session.execute(stmt)


def insert_reviews(session: Session, run_id: str, records: list[ContractorRecord]) -> int:
    """New reviews only; one already stored (same contractor, time and text) is skipped."""
    added = 0
    for rec in records:
        for review in rec.reviews:
            stmt = (
                insert(Review)
                .values(
                    gaf_id=rec.gaf_id,
                    published_at=review.published_at,
                    rating=review.rating,
                    text=review.text,
                    text_sha=hashlib.sha256(review.text.encode()).hexdigest(),
                    author_initials=review.author_initials,
                    source=review.source,
                    first_seen_run=run_id,
                )
                .on_conflict_do_nothing(constraint="uq_review")
                .returning(Review.id)  # a skipped row returns nothing; rowcount isn't reliable here
            )
            added += len(session.execute(stmt).all())
    return added
