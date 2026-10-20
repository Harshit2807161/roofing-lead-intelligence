"""Read-side queries for the rep UI and the JSON API.

ORM rows become plain dicts in one place, so the HTML pages and the API always show the same
numbers. Scores and insights are optional: a contractor the pipeline hasn't reached yet still
renders, ranked by GAF's own order.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.intel.insight import facts_for
from app.intel.tags import semantic_tags, tag_label
from app.models import Contractor, ContractorSignals, LeadInsight, LeadScore, Run, SignalCategory

PLAYS = [
    "Volume leader",
    "Commercial cross-sell",
    "Multi-trade cross-sell",
    "Growing shop",
    "Residential core",
    "Verify first",
]
SORTS = {
    "rank": "Lead score",
    "distance": "Distance",
    "rating": "Rating",
    "reviews": "Review count",
}
PENALTY_SCALE = 10.0  # points; the width reference for negative factors
_SKIP_WORDS = {"the", "and", "&", "of", "inc", "inc.", "llc", "llc.", "co", "co.", "corp", "corp."}


@dataclass(frozen=True)
class Filters:
    """List filters, parsed leniently from query params (blank form fields mean "no filter")."""

    state: str | None = None
    max_distance: float | None = None
    min_rating: float | None = None
    min_score: float | None = None
    play: str | None = None
    presidents_club: bool = False
    commercial: bool = False
    q: str | None = None
    tag: str | None = None  # a semantic tag key, e.g. "storm" or "trade:siding"
    sort: str = "rank"

    @classmethod
    def from_params(cls, p: Mapping[str, str]) -> "Filters":
        state = (p.get("state") or "").strip().upper()
        sort = p.get("sort") or "rank"
        return cls(
            state=state if state in {"NJ", "NY"} else None,
            max_distance=_float(p.get("max_distance")),
            min_rating=_float(p.get("min_rating")),
            min_score=_float(p.get("min_score")),
            play=(p.get("play") or "").strip() or None,
            presidents_club=_flag(p.get("presidents_club")),
            commercial=_flag(p.get("commercial")),
            q=(p.get("q") or "").strip() or None,
            tag=(p.get("tag") or "").strip() or None,
            sort=sort if sort in SORTS else "rank",
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _float(raw: str | None) -> float | None:
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def _flag(raw: str | None) -> bool:
    return (raw or "").lower() in {"1", "true", "on", "yes"}


def initials(name: str) -> str:
    """Two letters for the avatar, skipping filler words like "The" and "LLC"."""
    words = [w for w in name.replace(",", " ").split() if w.lower() not in _SKIP_WORDS]
    letters = [w[0] for w in words if w[0].isalnum()][:2]
    return "".join(letters).upper() or name[:2].upper()


# --- list ---------------------------------------------------------------------------------------


def _lead_query(f: Filters) -> Select:
    stmt = (
        select(Contractor, LeadScore, LeadInsight, ContractorSignals)
        .outerjoin(LeadScore, LeadScore.gaf_id == Contractor.gaf_id)
        .outerjoin(LeadInsight, LeadInsight.gaf_id == Contractor.gaf_id)
        .outerjoin(ContractorSignals, ContractorSignals.gaf_id == Contractor.gaf_id)
    )
    if f.state:
        stmt = stmt.where(Contractor.state == f.state)
    if f.max_distance is not None:
        stmt = stmt.where(Contractor.distance_mi <= f.max_distance)
    if f.min_rating is not None:
        stmt = stmt.where(Contractor.rating >= f.min_rating)
    if f.min_score is not None:
        stmt = stmt.where(LeadScore.total >= f.min_score)
    if f.play:
        stmt = stmt.where(LeadScore.play == f.play)
    if f.presidents_club:
        stmt = stmt.where(Contractor.presidents_club_stars > 0)
    if f.commercial:
        stmt = stmt.where(func.jsonb_array_length(Contractor.commercial_certs) > 0)
    if f.q:
        stmt = stmt.where(Contractor.name.ilike(f"%{f.q}%"))
    by_rank = (LeadScore.rank.asc().nulls_last(), Contractor.gaf_rank.asc())
    order = {
        "distance": (Contractor.distance_mi.asc(),),
        "rating": (Contractor.rating.desc().nulls_last(), Contractor.review_count.desc()),
        "reviews": (Contractor.review_count.desc(),),
    }.get(f.sort, by_rank)
    return stmt.order_by(*order)


def lead_row(
    c: Contractor, s: LeadScore | None, i: LeadInsight | None, tags: list[dict] | None = None
) -> dict[str, Any]:
    """The fields one row of the ranked list needs (also the /api/leads item shape)."""
    return {
        "gaf_id": c.gaf_id,
        "name": c.name,
        "initials": initials(c.name),
        "city": c.city,
        "state": c.state,
        "distance_mi": c.distance_mi,
        "lat": c.lat,
        "lng": c.lng,
        "rating": c.rating,
        "review_count": c.review_count,
        "presidents_club_stars": c.presidents_club_stars,
        "commercial": bool(c.commercial_certs),
        "commercial_certs": c.commercial_certs,
        "logo_url": c.logo_url,
        "gaf_rank": c.gaf_rank,
        "rank": s.rank if s else c.gaf_rank,
        "ranked_by": "score" if s else "gaf",
        "score": s.total if s else None,
        "play": s.play if s else None,
        "insight_status": i.status if i else None,
        "summary": (i.data or {}).get("summary") if i else None,
        "tags": tags or [],
    }


def promoted_categories(session: Session) -> frozenset[str]:
    stmt = select(SignalCategory.name).where(SignalCategory.status == "promoted")
    return frozenset(session.scalars(stmt))


def list_leads(session: Session, f: Filters) -> list[dict[str, Any]]:
    promoted = promoted_categories(session)
    rows = [
        lead_row(c, s, i, semantic_tags(sig.data if sig else None, promoted))
        for c, s, i, sig in session.execute(_lead_query(f)).all()
    ]
    if f.tag:  # tags come from JSON signals, so this filter runs here rather than in SQL
        rows = [r for r in rows if any(f.tag in t["keys"] for t in r["tags"])]
    return rows


def tag_options(session: Session) -> list[dict[str, Any]]:
    """Every tag key present, with a label and how many leads carry it, for the filter menu."""
    counts: dict[str, int] = {}
    for row in list_leads(session, Filters()):
        for key in {k for t in row["tags"] for k in t["keys"]}:
            counts[key] = counts.get(key, 0) + 1
    order = lambda k: (k.startswith("trade:"), -counts[k], k)  # noqa: E731 - signals first, then trades
    return [
        {"key": k, "label": tag_label(k), "count": counts[k]} for k in sorted(counts, key=order)
    ]


def summary_counts(session: Session) -> dict[str, Any]:
    """The numbers in the strip above the list: how far the pipeline has got."""
    insights = dict(
        session.execute(select(LeadInsight.status, func.count()).group_by(LeadInsight.status)).all()
    )
    return {
        "leads": session.scalar(select(func.count()).select_from(Contractor)) or 0,
        "scored": session.scalar(select(func.count()).select_from(LeadScore)) or 0,
        "insights_ok": insights.get("ok", 0),
        "insights_review": insights.get("needs_review", 0),
        "latest_run": next(iter(list_runs(session, limit=1)), None),
    }


# --- detail -------------------------------------------------------------------------------------


def _factor_view(f: dict[str, Any]) -> dict[str, Any]:
    """Bar width as a share of the factor's max. Penalties have max 0, so they're drawn red
    against a fixed 10-point scale: a -3 reads as small, not as a full bar."""
    points, top = float(f.get("points") or 0), float(f.get("max") or 0)
    negative = points < 0
    scale = top if top > 0 and not negative else PENALTY_SCALE
    pct = min(100.0, abs(points) / scale * 100)
    return {**f, "pct": round(pct, 1), "negative": negative, "cites": f.get("cites") or []}


# Display labels for the fact keys the insight writer cites (keys come from insight.facts_for).
CITE_LABELS = {
    "name": "Name", "location": "Location", "distance": "Distance", "certifications": "GAF certifications",
    "rating": "Rating", "presidents_club": "President's Club", "commercial_certs": "Commercial certs",
    "specialties": "Specialties", "in_business_since": "In business since", "employees": "Employees",
    "website": "Website", "license": "License", "about": "About (GAF profile)",
}  # fmt: skip


def citations(c: Contractor, s: LeadScore | None, reviews: list[dict]) -> dict[str, dict]:
    """What each cite key points at, so a chip shows the source value on hover. Built from the same
    facts the insight writer saw (insight.facts_for), so a chip can never point at nothing."""
    cites = {k: {"label": CITE_LABELS.get(k, k), "value": v} for k, v in facts_for(c).items()}
    if s:
        cites["score"] = {"label": "Lead score", "value": f"{s.total:g}/100, rank #{s.rank}"}
    for r in reviews:
        cites[r["cite"]] = {
            "label": f"Review {r['n']} · {r['published_at']:%b %-d, %Y} · {r['rating']}★",
            "value": r["text"],
        }
    return cites


def _review_view(n: int, r) -> dict[str, Any]:
    return {
        "n": n,
        "cite": f"review_{n}",
        "published_at": r.published_at,
        "rating": r.rating,
        "author_initials": r.author_initials,
        "source": r.source,
        "text": r.text,
    }


def lead_detail(session: Session, gaf_id: str) -> dict[str, Any] | None:
    """Everything the detail page and /api/leads/{id} show for one contractor."""
    c = session.get(Contractor, gaf_id, options=[selectinload(Contractor.reviews)])
    if c is None:
        return None
    s = session.get(LeadScore, gaf_id)
    i = session.get(LeadInsight, gaf_id)
    sig = session.get(ContractorSignals, gaf_id)
    # Numbered newest first, the same order the intel step uses for review_1..review_3.
    reviews = [_review_view(n, r) for n, r in enumerate(c.reviews, 1)]
    address = ", ".join(
        x for x in [c.street, c.city, f"{c.state or ''} {c.postal_code or ''}"] if x
    )
    return {
        **lead_row(c, s, i, semantic_tags(sig.data if sig else None, promoted_categories(session))),
        "dba": c.dba,
        "address": address.strip(", "),
        "phone": c.display_phone or c.phone,
        "website": c.website,
        "profile_url": c.profile_url,
        "license_number": c.license_number,
        "in_business_since": c.in_business_since,
        "employees_band": None if c.employees_band in (None, "None") else c.employees_band,
        "about": c.about,
        "residential_certs": c.residential_certs,
        "commercial_technologies": c.commercial_technologies,
        "specialties": c.specialties,
        "updated_at": c.updated_at,
        "reviews": reviews,
        "score_detail": {
            "total": s.total,
            "rank": s.rank,
            "play": s.play,
            "version": s.version,
            "factors": [_factor_view(f) for f in s.factors or []],
        }
        if s
        else None,
        "insight": {
            "status": i.status,
            "model": i.model,
            "data": i.data or {},
            "validation_errors": i.validation_errors or [],
            "created_at": i.created_at,
        }
        if i
        else None,
        "signals": sig.data if sig else None,
        "citations": citations(c, s, reviews),
    }


# --- runs ---------------------------------------------------------------------------------------


def list_runs(session: Session, limit: int = 20) -> list[dict[str, Any]]:
    runs = session.scalars(select(Run).order_by(Run.started_at.desc()).limit(limit)).all()
    return [
        {
            "id": r.id,
            "status": r.status,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "listed": r.listed,
            "fetched": r.fetched,
            "parsed": r.parsed,
            "failed": r.failed,
            "query": r.query,
            "summary": r.summary,
        }
        for r in runs
    ]
