"""B, step 2: the lead score, in code. Weighted points (S1) with a reason per factor, plus a play tag.

Every factor is capped and the caps add up to 100, so a rep can read "+20 President's Club" and
see exactly why one contractor outranks another. Extracted signals add points only through
quotes that survived the verbatim check.
"""

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.intel.extract import EMPTY, Signals
from app.models import Contractor

VERSION = "score-v1"
NON_ROOFING = {"siding", "gutters", "windows", "doors", "skylights", "solar", "insulation",
               "chimney", "masonry", "decks", "painting", "general_contracting"}  # fmt: skip
RATING_PRIOR_WEIGHT = 20  # reviews' worth of "territory average" mixed into every rating


@dataclass
class Factor:
    key: str
    label: str
    points: float
    max: float
    reason: str
    cites: list[str] = field(default_factory=list)


def adjusted_rating(rating: float | None, n: int, territory_mean: float) -> float | None:
    """Pull small-sample ratings toward the territory mean: 5.0 from 2 reviews < 4.9 from 283."""
    if rating is None or n == 0:
        return None
    return (n * rating + RATING_PRIOR_WEIGHT * territory_mean) / (n + RATING_PRIOR_WEIGHT)


def factors(c: Contractor, s: Signals, territory_mean: float, today: datetime) -> list[Factor]:
    out: list[Factor] = []

    stars = c.presidents_club_stars
    out.append(Factor("presidents_club", "President's Club", {0: 0, 1: 12}.get(stars, 20), 20,
                      f"President's Club {stars}★: GAF's top-performer award" if stars
                      else "Not in President's Club", ["presidents_club"] if stars else []))  # fmt: skip

    n = c.review_count
    vol = 18 * min(1.0, math.log10(1 + n) / math.log10(1 + 1000))
    out.append(Factor("review_volume", "Job volume (reviews)", round(vol, 1), 18,
                      f"{n} Google reviews" if n else "No Google reviews yet", ["rating"] if n else []))  # fmt: skip

    adj = adjusted_rating(c.rating, n, territory_mean)
    pts = 0.0 if adj is None else 10 * max(0.0, min(1.0, adj - 4.0))
    out.append(Factor("rating", "Reputation", round(pts, 1), 10,
                      f"{c.rating}★ over {n} reviews (adjusted {adj:.2f})" if adj else "No rating",
                      ["rating"] if adj else []))  # fmt: skip

    newest = c.reviews[0].published_at if c.reviews else None
    days = (today - newest).days if newest else None
    pts = 0 if days is None else 10 if days <= 90 else 7 if days <= 180 else 4 if days <= 365 else 0
    out.append(Factor("activity", "Recent activity", pts, 10,
                      f"Newest review {newest:%b %d, %Y} ({days} days ago)" if newest else "No reviews to date activity",
                      ["review_1"] if newest else []))  # fmt: skip

    certs = " ".join(c.commercial_certs)
    if "GoldElite" in certs:
        pts, why, cites = (
            12,
            "GAF GoldElite commercial contractor: low-slope cross-sell",
            ["commercial_certs"],
        )
    elif c.commercial_certs:
        pts, why, cites = (
            8,
            f"Commercial certified ({', '.join(c.commercial_technologies) or 'commercial'})",
            ["commercial_certs"],
        )
    elif s.commercial_work:
        pts, why, cites = (
            4,
            f'Mentions commercial work: "{s.commercial_work.quote}"',
            [s.commercial_work.source],
        )
    else:
        pts, why, cites = 0, "Residential only", []
    out.append(Factor("commercial", "Commercial capability", pts, 12, why, cites))

    out.append(Factor("specialties", "Specialty systems", min(6, 3 * len(c.specialties)), 6,
                      ", ".join(c.specialties) if c.specialties else "No solar, metal or FORTIFIED listing",
                      ["specialties"] if c.specialties else []))  # fmt: skip

    trades = sorted({x.service for x in s.services if x.service in NON_ROOFING})
    out.append(Factor("multi_trade", "Other trades (cross-sell)", min(8, 2 * len(trades)), 8,
                      ", ".join(trades) if trades else "No other trades mentioned",
                      sorted({x.source for x in s.services if x.service in NON_ROOFING})))  # fmt: skip

    since = c.in_business_since or s.founded_year
    years = today.year - since if since else None
    pts = 0 if years is None else 6 if years >= 20 else 4 if years >= 10 else 2 if years >= 5 else 0
    cite = (
        ["in_business_since"]
        if c.in_business_since
        else ([s.founded_quote.source] if s.founded_quote else [])
    )
    out.append(Factor("established", "Established", pts, 6,
                      f"In business since {since} ({years} years)" if since else "Founding year unknown", cite))  # fmt: skip

    people = s.people
    out.append(Factor("named_contact", "Named contact", 4 if people else 0, 4,
                      ", ".join(p.name for p in people[:3]) if people else "No staff named in reviews",
                      sorted({p.source for p in people})))  # fmt: skip

    d = c.distance_mi
    pts = 6 if d <= 5 else 4 if d <= 10 else 2 if d <= 15 else 0
    out.append(Factor("proximity", "Proximity to 10013", pts, 6, f"{d:.1f} mi", ["distance"]))

    if s.complaints:
        out.append(Factor("complaints", "Complaints in reviews", -min(6, 3 * len(s.complaints)), 0,
                          "; ".join(t.theme for t in s.complaints), sorted({t.source for t in s.complaints})))  # fmt: skip
    return out


def play(c: Contractor, s: Signals, today: datetime) -> str:
    """A one-line plan for the rep, from simple rules in priority order."""
    trades = {x.service for x in s.services if x.service in NON_ROOFING}
    newest = c.reviews[0].published_at if c.reviews else None
    if c.presidents_club_stars or c.review_count >= 300:
        return "Volume leader"
    if c.commercial_certs:
        return "Commercial cross-sell"
    if len(trades) >= 2:
        return "Multi-trade cross-sell"
    if c.review_count == 0 and not c.about:
        return "Verify first"
    if c.review_count < 50 and newest and (today - newest).days <= 180:
        return "Growing shop"
    return "Residential core"


def score_all(
    contractors: list[Contractor], signals: dict[str, Signals], today: datetime | None = None
) -> list[dict]:
    """Scores for everyone, ranked. Ties break on review count, then GAF's own order."""
    today = today or datetime.now(UTC)
    rated = [c.rating for c in contractors if c.rating is not None]
    mean = sum(rated) / len(rated) if rated else 4.5
    rows = []
    for c in contractors:
        s = signals.get(c.gaf_id, EMPTY)
        fs = factors(c, s, mean, today)
        total = max(0.0, min(100.0, sum(f.points for f in fs)))
        rows.append({"gaf_id": c.gaf_id, "total": round(total, 1), "play": play(c, s, today),
                     "factors": [f.__dict__ for f in fs], "version": VERSION,
                     "_tiebreak": (-total, -c.review_count, c.gaf_rank)})  # fmt: skip
    rows.sort(key=lambda r: r["_tiebreak"])
    for rank, r in enumerate(rows, 1):
        del r["_tiebreak"]
        r["rank"] = rank
    return rows
