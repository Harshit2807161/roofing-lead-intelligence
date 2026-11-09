"""Read-only tools for the rep prep chat (F8). Typed args, one execution path, errors as data.

Every tool reads our own database; none can write, browse or send. Each result also reports the
(gaf_id, key) pairs it showed the model, so the answer's citations can be checked against what
the model actually saw.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.intel.insight import facts_for
from app.models import Contractor
from app.web import data

try:  # the SDK's own strict-schema converter (all fields required, nullable where optional)
    from openai.lib._pydantic import to_strict_json_schema
except ImportError:  # pragma: no cover
    to_strict_json_schema = None


@dataclass
class ToolResult:
    ok: bool
    payload: Any
    seen: set[tuple[str, str]] = field(default_factory=set)  # citable (gaf_id, key) pairs

    def to_model(self) -> str:
        body = {"ok": True, "result": self.payload} if self.ok else {"ok": False, **self.payload}
        return json.dumps(body, default=str)


@dataclass
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[[Session, Any], ToolResult]

    def openai_schema(self) -> dict:
        schema = (to_strict_json_schema(self.args_model) if to_strict_json_schema
                  else self.args_model.model_json_schema())  # fmt: skip
        return {"type": "function", "name": self.name, "description": self.description,
                "parameters": schema, "strict": to_strict_json_schema is not None}  # fmt: skip


def error(message: str, hint: str) -> ToolResult:
    return ToolResult(False, {"error": message, "hint": hint})


# --- leads_search ----------------------------------------------------------------------------


class SearchArgs(BaseModel):
    state: Literal["NJ", "NY"] | None = None
    max_distance_mi: float | None = Field(default=None, ge=0, le=25)
    min_rating: float | None = Field(default=None, ge=0, le=5)
    min_score: float | None = Field(default=None, ge=0, le=100)
    play: str | None = None
    tag: str | None = None
    presidents_club_only: bool | None = None
    commercial_only: bool | None = None
    status: Literal["new", "contacted", "not_a_fit"] | None = None
    name_contains: str | None = None
    sort: Literal["rank", "distance", "rating", "reviews"] | None = None
    limit: int | None = Field(default=None, ge=1, le=15)


def leads_search(session: Session, a: SearchArgs) -> ToolResult:
    if a.play and a.play not in data.PLAYS:
        return error(f"Unknown play {a.play!r}.", f"Use one of {data.PLAYS}.")
    options = {o["key"] for o in data.tag_options(session)}
    if a.tag and a.tag not in options:
        return error(f"Unknown tag {a.tag!r}.", f"Use one of {sorted(options)}.")
    f = data.Filters(state=a.state, max_distance=a.max_distance_mi, min_rating=a.min_rating,
                     min_score=a.min_score, play=a.play, tag=a.tag, status=a.status,
                     presidents_club=bool(a.presidents_club_only), commercial=bool(a.commercial_only),
                     q=a.name_contains, sort=a.sort or "rank")  # fmt: skip
    rows = data.list_leads(session, f)
    keep = ("gaf_id", "rank", "name", "city", "state", "distance_mi", "rating", "review_count",
            "presidents_club_stars", "commercial", "score", "play", "status")  # fmt: skip
    items = [
        {**{k: r[k] for k in keep}, "tags": [t["label"] for t in r["tags"]]}
        for r in rows[: a.limit or 8]
    ]
    seen = {(r["gaf_id"], "listing") for r in items}
    return ToolResult(
        True, {"total_matches": len(rows), "showing": len(items), "leads": items}, seen
    )


# --- lead_get --------------------------------------------------------------------------------


class GetArgs(BaseModel):
    gaf_id: str = Field(
        description="GAF contractor id, digits only, from leads_search or text_search"
    )


def lead_get(session: Session, a: GetArgs) -> ToolResult:
    d = data.lead_detail(session, a.gaf_id)
    if d is None:
        return error(
            f"No contractor with GAF id {a.gaf_id!r}.",
            "Find the id with leads_search(name_contains=...).",
        )
    c = session.get(Contractor, a.gaf_id)
    facts = facts_for(c)
    ins = (d["insight"] or {}).get("data", {})
    payload = {
        "gaf_id": a.gaf_id,
        "facts": facts,  # cite these keys, e.g. [1116285:review_2]
        "score": {
            "total": d["score"],
            "rank": d["rank"],
            "play": d["play"],
            "factors": [
                {"label": f["label"], "points": f["points"], "max": f["max"], "reason": f["reason"]}
                for f in (d["score_detail"] or {}).get("factors", [])
            ],
        },  # fmt: skip
        "insight": {
            k: ins.get(k) for k in ("summary", "next_action", "talking_points", "decision_makers")
        },
        "tags": [
            {"label": t["label"], "quote": t["quote"], "source": t["source"]} for t in d["tags"]
        ],
        "workflow": {
            "status": d["workflow"]["status"],
            "notes": [h["note"] for h in d["workflow"]["history"] if h["note"]][:5],
        },  # fmt: skip
    }
    seen = {(a.gaf_id, k) for k in facts} | {(a.gaf_id, "score"), (a.gaf_id, "listing")}
    return ToolResult(True, payload, seen)


# --- text_search -----------------------------------------------------------------------------


class TextArgs(BaseModel):
    query: str = Field(
        min_length=2, max_length=120, description="plain words, e.g. 'storm damage insurance'"
    )
    limit: int | None = Field(default=None, ge=1, le=12)


SEARCH_SQL = text("""
with docs as (
    select c.gaf_id, c.name, 'about' as source, c.about as body, null::timestamptz as published_at
    from contractors c where c.about is not null
    union all
    select r.gaf_id, c.name,
           'review_' || row_number() over (partition by r.gaf_id order by r.published_at desc, r.id desc),
           r.text, r.published_at
    from reviews r join contractors c using (gaf_id)
)
select gaf_id, name, source, published_at,
       ts_headline('english', body, q, 'MaxWords=30, MinWords=12, StartSel=«, StopSel=»') as snippet,
       ts_rank(to_tsvector('english', body), q) as rank
from docs, websearch_to_tsquery('english', :query) q
where to_tsvector('english', body) @@ q and source in ('about', 'review_1', 'review_2', 'review_3')
order by rank desc limit :limit
""")


def text_search(session: Session, a: TextArgs) -> ToolResult:
    """Postgres full-text search over About text and reviews: enough for 85 contractors and
    220 reviews. Embeddings (pgvector) come in when paraphrase matters or the corpus grows."""
    rows = session.execute(SEARCH_SQL, {"query": a.query, "limit": a.limit or 8}).mappings().all()
    hits = [dict(r) | {"rank": round(float(r["rank"]), 3)} for r in rows]
    if not hits:
        return ToolResult(
            True, {"hits": [], "note": "No About text or review matches. Try other words."}
        )
    return ToolResult(True, {"hits": hits}, {(h["gaf_id"], h["source"]) for h in hits})


REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec(
            "leads_search",
            "Find leads in the territory by filters, ranked. Use for 'which / how many / top' "
            "questions. Returns gaf_id, rank, score, play, status and tag labels. Don't use it for "
            "what reviews say (use text_search) or for one lead's details (use lead_get). "
            "tag keys look like 'storm', 'warranty', 'contact', 'complaints', 'trade:siding', "
            '\'signal:family_owned_business\'. Example: {"state": "NJ", "commercial_only": true}.',
            SearchArgs,
            leads_search,
        ),
        ToolSpec(
            "lead_get",
            "Everything stored about one lead: GAF facts (each under a citable key), the score "
            "breakdown, the written insight with its named contacts, verified tags with quotes, and "
            "the rep's status and notes. Needs a gaf_id; get it from leads_search(name_contains=...) "
            'first if you only have a name. Example: {"gaf_id": "1116285"}.',
            GetArgs,
            lead_get,
        ),
        ToolSpec(
            "text_search",
            "Full-text search across every contractor's About text and newest 3 reviews. Use for "
            "'who mentions X' or 'what do customers say about Y'. Returns gaf_id, source key "
            '(about, review_1..3) and a snippet. Example: {"query": "storm damage insurance"}.',
            TextArgs,
            text_search,
        ),
    ]
}


@dataclass
class CallRecord:
    name: str
    args: dict
    ok: bool
    ms: int


class Executor:
    """The only path from the model to data. Validates args, runs, and turns any failure into an
    error the model can read and fix; it never raises into the loop."""

    def __init__(self, session: Session):
        self.session = session
        self.log: list[CallRecord] = []
        self.seen: set[tuple[str, str]] = set()
        self.outputs: list[str] = []

    def run(self, name: str, raw_args: str) -> str:
        started = time.perf_counter()
        spec = REGISTRY.get(name)
        if spec is None:
            result = error(f"Unknown tool {name!r}.", f"Use one of {sorted(REGISTRY)}.")
        else:
            try:
                args = spec.args_model.model_validate_json(raw_args or "{}")
                result = spec.fn(self.session, args)
            except ValidationError as exc:
                problems = "; ".join(
                    f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:4]
                )
                result = error(
                    f"Invalid arguments: {problems}.", "Fix the arguments and call again once."
                )
            except Exception as exc:  # a tool bug becomes an observation, not a crash
                self.session.rollback()
                result = error(
                    f"{type(exc).__name__}: {exc}", "Try a different tool or simpler arguments."
                )
        self.seen |= result.seen
        out = result.to_model()
        self.outputs.append(out)
        self.log.append(CallRecord(name, json.loads(raw_args or "{}") if raw_args else {}, result.ok,
                                   int((time.perf_counter() - started) * 1000)))  # fmt: skip
        return out
