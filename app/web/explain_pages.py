"""F14 head-to-head compare and F15 ranking tuner: two read-only pages over the stored scores.

Both read contractors and their score-v1 factors in one query, so they show the same points as the
detail page. Nothing here writes: the tuner re-weights in the browser only.
"""

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contractor, LeadScore
from app.web.data import initials
from app.web.deps import get_session, templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]

# How each factor reads mid-sentence in the verdict ("mostly on President's Club (+20) and ...").
PHRASES = {
    "presidents_club": "President's Club",
    "review_volume": "job volume",
    "rating": "reputation",
    "activity": "recent activity",
    "commercial": "commercial capability",
    "specialties": "specialty systems",
    "multi_trade": "other trades",
    "established": "years in business",
    "named_contact": "a named contact",
    "proximity": "proximity",
    "complaints": "fewer review complaints",
}
_SUFFIXES = ("home corporation", "corporation", "corp", "inc", "llc", "co", "company", "ltd")
BAR_MAX_PCT = 82  # widest bar, as a share of its half; leaves room for the "+20" label


# --- pure helpers (unit-tested) -----------------------------------------------------------------


def short_name(name: str) -> str:
    """'Allied Brothers Home Corporation' -> 'Allied Brothers', 'Penyak Roofing Co Inc' -> 'Penyak Roofing'."""
    words = name.replace(",", " ").split()
    changed = True
    while changed and len(words) > 1:
        changed = False
        for suffix in _SUFFIXES:
            n = len(suffix.split())
            tail = " ".join(w.lower().rstrip(".") for w in words[-n:])
            if len(words) > n and tail == suffix:
                words, changed = words[:-n], True
                break
    return " ".join(words)


def fmt_points(value: float) -> str:
    """One decimal, trailing zero dropped: 20.0 -> '20', 4.2 -> '4.2'."""
    return f"{round(value, 1):.1f}".removesuffix(".0")


@dataclass(frozen=True)
class FactorGap:
    """One factor for both leads. `a`/`b` are points; a factor a lead lacks counts as 0."""

    key: str
    label: str
    a: float
    b: float
    max: float
    reason_a: str
    reason_b: str

    @property
    def diff(self) -> float:
        return round(self.a - self.b, 1)


def factor_gaps(factors_a: list[dict], factors_b: list[dict]) -> list[FactorGap]:
    """Pair up two stored factor lists by key, in scoring order."""
    ma = {f["key"]: f for f in factors_a}
    mb = {f["key"]: f for f in factors_b}
    out = []
    for key in dict.fromkeys([*ma, *mb]):
        fa, fb = ma.get(key, {}), mb.get(key, {})
        src = fa or fb
        missing = "No complaints in reviews" if key == "complaints" else "Not scored"
        out.append(
            FactorGap(
                key=key,
                label=src.get("label", key),
                a=float(fa.get("points") or 0),
                b=float(fb.get("points") or 0),
                max=float(src.get("max") or 0),
                reason_a=fa.get("reason") or missing,
                reason_b=fb.get("reason") or missing,
            )
        )
    return out


def _phrase(g: FactorGap, edge: float) -> str:
    return f"{PHRASES.get(g.key, g.label.lower())} (+{fmt_points(edge)})"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def verdict(name_a: str, total_a: float, name_b: str, total_b: float, gaps: list[FactorGap]) -> str:
    """One sentence, in code: who leads by how much, on which factors, and where the other is ahead.

    e.g. "Allied Brothers leads Complete Roof Systems by 8.1 points, mostly on President's Club
    (+20) and job volume (+4.2); Complete Roof Systems is ahead on commercial capability (+8)."
    """
    gap = round(total_a - total_b, 1)
    if gap < 0:  # say it from the leader's side
        name_a, name_b, total_a, total_b = name_b, name_a, total_b, total_a
        gaps = [FactorGap(g.key, g.label, g.b, g.a, g.max, g.reason_b, g.reason_a) for g in gaps]
        gap = -gap
    ahead = sorted((g for g in gaps if g.diff > 0), key=lambda g: -g.diff)
    behind = sorted((g for g in gaps if g.diff < 0), key=lambda g: g.diff)
    lead_on = [_phrase(g, g.diff) for g in ahead[:2]]
    trail_on = [_phrase(g, -g.diff) for g in behind[:2]]

    if gap == 0:
        s = f"{name_a} and {name_b} are tied at {fmt_points(total_a)} points"
        if not ahead and not behind:
            return s + " on every factor."
        bits = [f"{name_a} is ahead on {_join(lead_on)}"] if lead_on else []
        if trail_on:
            bits.append(f"{name_b} {'on' if lead_on else 'is ahead on'} {_join(trail_on)}")
        return f"{s}; {', '.join(bits)}."

    unit = "point" if gap == 1 else "points"
    s = f"{name_a} leads {name_b} by {fmt_points(gap)} {unit}"
    if lead_on:
        s += f", {'mostly ' if len(ahead) > 2 else ''}on {_join(lead_on)}"
    if trail_on:
        more = len(behind) - len(trail_on)
        s += f"; {name_b} is ahead on {_join(trail_on)}"
        if more:
            s += f", plus {more} smaller factor{'s' if more > 1 else ''}"
    return s + "."


def pick_pair(order: list[str], a: str | None, b: str | None) -> tuple[str, str]:
    """Defaults: #1 vs #2. With only `a`, the lead ranked just below it (just above, if a is last).
    With only `b`, #1 (or #2 when b is #1)."""
    if a and b:
        return a, b
    if a:
        i = order.index(a)
        return a, order[i + 1] if i + 1 < len(order) else order[i - 1]
    if b:
        return (order[0] if order[0] != b else order[1]), b
    return order[0], order[1]


# --- data ---------------------------------------------------------------------------------------


def ranked_leads(session: Session) -> list[dict[str, Any]]:
    """Every contractor in official order (score rank; GAF order for any not scored yet)."""
    stmt = (
        select(Contractor, LeadScore)
        .outerjoin(LeadScore, LeadScore.gaf_id == Contractor.gaf_id)
        .order_by(LeadScore.rank.asc().nulls_last(), Contractor.gaf_rank.asc())
    )
    return [
        {
            "gaf_id": c.gaf_id,
            "name": c.name,
            "short": short_name(c.name),
            "initials": initials(c.name),
            "city": c.city,
            "state": c.state,
            "distance_mi": c.distance_mi,
            "presidents_club_stars": c.presidents_club_stars,
            "rank": s.rank if s else c.gaf_rank,
            "score": s.total if s else None,
            "play": s.play if s else None,
            "version": s.version if s else None,
            "factors": list(s.factors or []) if s else [],
        }
        for c, s in session.execute(stmt).all()
    ]


def compare_view(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Rows for the diverging chart: factors that differ, biggest gap first; ties on one line."""
    gaps = factor_gaps(a["factors"], b["factors"])
    differ = sorted((g for g in gaps if g.diff), key=lambda g: -abs(g.diff))  # stable: score order
    gap = round((a["score"] or 0) - (b["score"] or 0), 1)
    scale = max([abs(g.diff) for g in differ] + [abs(gap), 0.1])
    pct = lambda v: round(abs(v) / scale * BAR_MAX_PCT, 1)  # noqa: E731
    if a["gaf_id"] == b["gaf_id"]:
        sentence = "Pick two different leads to compare."
    elif a["score"] is None or b["score"] is None:
        sentence = "One of these leads hasn't been scored yet, so there is no gap to explain."
    else:
        sentence = verdict(a["short"], a["score"], b["short"], b["score"], gaps)
    return {
        "rows": [{"g": g, "abs": abs(g.diff), "pct": pct(g.diff)} for g in differ],
        "tied": [g for g in gaps if not g.diff],
        "gap": gap,
        "gap_abs": abs(gap),
        "gap_pct": pct(gap),
        "verdict": sentence,
    }


def tuner_payload(leads: list[dict[str, Any]]) -> dict[str, Any]:
    """What tuner.js needs: the tunable factors (positive caps, score order) and every lead's points."""
    scored = [x for x in leads if x["score"] is not None]
    first = scored[0]["factors"] if scored else []
    return {
        "version": scored[0]["version"] if scored else None,
        "factors": [
            {"key": f["key"], "label": f["label"], "max": f["max"]}
            for f in first
            if (f.get("max") or 0) > 0
        ],
        "leads": [
            {
                "id": x["gaf_id"],
                "rank": x["rank"],
                "name": x["name"],
                "city": x["city"],
                "state": x["state"],
                "play": x["play"],
                "total": x["score"],
                "f": [
                    {
                        "k": f["key"],
                        "l": f["label"],
                        "p": f["points"],
                        "m": f["max"],
                        "r": f["reason"],
                    }
                    for f in x["factors"]
                ],
            }
            for x in scored
        ],
    }


# --- routes -------------------------------------------------------------------------------------


def _not_found(request: Request, gaf_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "not_found.html", {"gaf_id": gaf_id}, status_code=404
    )


@router.get("/compare", response_class=HTMLResponse)
def compare_page(
    request: Request, session: SessionDep, a: str | None = None, b: str | None = None
) -> HTMLResponse:
    leads = ranked_leads(session)
    by_id = {x["gaf_id"]: x for x in leads}
    a, b = (a or "").strip() or None, (b or "").strip() or None
    for gaf_id in (a, b):
        if gaf_id and gaf_id not in by_id:
            return _not_found(request, gaf_id)
    if len(leads) < 2:
        return _not_found(request, a or b or "(none loaded)")
    a, b = pick_pair([x["gaf_id"] for x in leads], a, b)
    lead_a, lead_b = by_id[a], by_id[b]
    ctx = {"leads": leads, "A": lead_a, "B": lead_b, "cmp": compare_view(lead_a, lead_b)}
    return templates.TemplateResponse(request, "compare.html", ctx)


@router.get("/tuner", response_class=HTMLResponse)
def tuner_page(request: Request, session: SessionDep) -> HTMLResponse:
    payload = tuner_payload(ranked_leads(session))
    return templates.TemplateResponse(request, "tuner.html", {"payload": payload})
