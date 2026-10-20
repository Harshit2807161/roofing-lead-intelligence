"""Database tables. P0: runs, raw snapshots, contractors, reviews. Scores and insights come next."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

# JSONB on Postgres, plain JSON elsewhere.
JsonList = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Run(Base):
    """One pipeline run: what we asked GAF for, and how many records made it through each step."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")
    query: Mapped[dict] = mapped_column(JsonList, default=dict)
    listed: Mapped[int] = mapped_column(Integer, default=0)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    parsed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JsonList, default=dict)  # dedupe flags, parse errors


class RawSnapshot(Base):
    """A raw file we fetched. The bytes stay on disk; the row keeps where, when and its hash."""

    __tablename__ = "raw_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    kind: Mapped[str] = mapped_column(String(20))  # listing | profile
    gaf_id: Mapped[str | None] = mapped_column(String(20))
    url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    path: Mapped[str | None] = mapped_column(Text)


class Contractor(Base):
    """One GAF contractor, keyed on GAF's contractor ID. Upserted on every run."""

    __tablename__ = "contractors"

    gaf_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    dba: Mapped[str | None] = mapped_column(Text)
    profile_url: Mapped[str] = mapped_column(Text)
    gaf_rank: Mapped[int] = mapped_column(Integer)

    street: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(2))
    postal_code: Mapped[str | None] = mapped_column(String(10))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    distance_mi: Mapped[float] = mapped_column(Float)

    phone: Mapped[str | None] = mapped_column(String(40))
    display_phone: Mapped[str | None] = mapped_column(String(40))
    website: Mapped[str | None] = mapped_column(Text)
    license_number: Mapped[str | None] = mapped_column(String(60))
    in_business_since: Mapped[int | None] = mapped_column(Integer)
    employees_band: Mapped[str | None] = mapped_column(Text)
    about: Mapped[str | None] = mapped_column(Text)

    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int] = mapped_column(Integer)
    presidents_club_stars: Mapped[int] = mapped_column(Integer, default=0)
    residential_certs: Mapped[list] = mapped_column(JsonList, default=list)
    commercial_certs: Mapped[list] = mapped_column(JsonList, default=list)
    commercial_technologies: Mapped[list] = mapped_column(JsonList, default=list)
    specialties: Mapped[list] = mapped_column(JsonList, default=list)
    google_place_id: Mapped[str | None] = mapped_column(String(80))
    logo_url: Mapped[str | None] = mapped_column(Text)
    profile_fetched: Mapped[bool] = mapped_column(default=False)

    # Provenance: the raw files this row was built from.
    listing_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))
    profile_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))
    first_seen_run: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    last_seen_run: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    reviews: Mapped[list["Review"]] = relationship(
        back_populates="contractor", order_by="Review.published_at.desc()"
    )


class Review(Base):
    """A Google review shown on the GAF profile (GAF shows the newest three)."""

    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("gaf_id", "published_at", "text_sha", name="uq_review"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gaf_id: Mapped[str] = mapped_column(ForeignKey("contractors.gaf_id"))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rating: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_sha: Mapped[str] = mapped_column(String(64))
    author_initials: Mapped[str | None] = mapped_column(String(10))
    source: Mapped[str | None] = mapped_column(String(40))
    first_seen_run: Mapped[str] = mapped_column(ForeignKey("runs.id"))

    contractor: Mapped[Contractor] = relationship(back_populates="reviews")


# --- Intelligence step (B): extract → score → write. One current row per contractor. ---


class ContractorSignals(Base):
    """Typed signals a small model pulled from About text and reviews, each with a verbatim quote."""

    __tablename__ = "signals"

    gaf_id: Mapped[str] = mapped_column(ForeignKey("contractors.gaf_id"), primary_key=True)
    model: Mapped[str] = mapped_column(String(60))
    prompt_version: Mapped[str] = mapped_column(String(20))
    input_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JsonList, default=dict)  # the Signals schema
    dropped: Mapped[list] = mapped_column(
        JsonList, default=list
    )  # signals whose quote wasn't found
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LeadScore(Base):
    """Deterministic score in code. `factors` is the per-factor breakdown the UI shows."""

    __tablename__ = "scores"

    gaf_id: Mapped[str] = mapped_column(ForeignKey("contractors.gaf_id"), primary_key=True)
    total: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)
    play: Mapped[str] = mapped_column(String(40))  # e.g. "Volume leader", "Commercial cross-sell"
    # [{"key", "label", "points", "max", "reason", "cites": [...]}]
    factors: Mapped[list] = mapped_column(JsonList, default=list)
    version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LeadInsight(Base):
    """The written insight. status is 'ok' or 'needs_review' (failed validation twice)."""

    __tablename__ = "insights"

    gaf_id: Mapped[str] = mapped_column(ForeignKey("contractors.gaf_id"), primary_key=True)
    model: Mapped[str] = mapped_column(String(60))
    prompt_version: Mapped[str] = mapped_column(String(20))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    data: Mapped[dict] = mapped_column(JsonList, default=dict)  # the Insight schema
    validation_errors: Mapped[list] = mapped_column(JsonList, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
