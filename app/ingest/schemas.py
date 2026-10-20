"""Typed records the parser produces and the loader writes. Validation happens here, once."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ReviewRecord(BaseModel):
    published_at: datetime
    rating: int | None = Field(default=None, ge=1, le=5)
    text: str
    # GAF's JSON carries the reviewer's full name but the page shows initials; we keep initials only.
    author_initials: str | None = None
    source: str | None = None


class ContractorRecord(BaseModel):
    gaf_id: str
    name: str
    dba: str | None = None
    profile_url: str
    gaf_rank: int  # position in GAF's own "recommended" order, 1-based

    street: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    distance_mi: float = Field(ge=0)

    phone: str | None = None  # business line from the listing
    display_phone: str | None = None  # click-to-call number on the profile, only if different
    website: str | None = None
    license_number: str | None = None
    in_business_since: int | None = None
    employees_band: str | None = None
    about: str | None = None

    rating: float | None = Field(default=None, ge=0, le=5)  # None when there are no reviews
    review_count: int = Field(ge=0)
    presidents_club_stars: int = Field(default=0, ge=0)
    residential_certs: list[str] = []
    commercial_certs: list[str] = []
    commercial_technologies: list[str] = []
    specialties: list[str] = []
    google_place_id: str | None = None
    logo_url: str | None = None

    reviews: list[ReviewRecord] = []
    profile_fetched: bool = False

    @field_validator("gaf_id")
    @classmethod
    def gaf_id_is_numeric(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError(f"unexpected GAF contractor id {v!r}")
        return v
