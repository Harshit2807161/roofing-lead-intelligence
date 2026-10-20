"""Signal discovery: turn the extractor's open-ended labels into categories, and promote the ones
enough contractors share.

The extractor names new ideas freely ("storm_repair", "storm_damage_repair"), so raw labels split
one idea across spellings. One small-model call groups the labels; code then counts distinct
contractors per group and decides promotion. The count, not the model, decides what's promoted.
"""

import logging
import re
from collections import defaultdict

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.intel.extract import label
from app.intel.llm import structured
from app.models import ContractorSignals, SignalCategory

log = logging.getLogger(__name__)
CANONICAL = re.compile(r"^[a-z][a-z0-9_]{2,47}$")


class LabelGroup(BaseModel):
    canonical: str  # snake_case name for the group
    description: str  # one line a sales rep would understand
    raw_labels: list[str]


class Grouping(BaseModel):
    groups: list[LabelGroup]


INSTRUCTIONS = """You group signal labels that a model extracted from roofing contractors' profiles
and reviews. Put labels that mean the same thing for a building-materials sales rep in one group
(e.g. "storm_repair" and "storm_damage_restoration"). Do not merge different ideas.
Every input label must appear in exactly one group. Prefer an existing category name when one fits.
`canonical` is snake_case, 3 to 48 characters. `description` is one plain sentence."""


def group_labels(
    labels: dict[str, str], existing: dict[str, str], model: str
) -> dict[str, tuple[str, str]]:
    """{raw_label: (canonical, description)}. Falls back to each label as its own group."""
    fallback = {raw: (raw, "") for raw in labels}
    if not labels:
        return fallback
    lines = [f"- {raw}: e.g. {example}" for raw, example in sorted(labels.items())]
    known = [f"- {name}: {desc}" for name, desc in sorted(existing.items())] or ["(none yet)"]
    user_input = (
        "EXISTING CATEGORIES:\n" + "\n".join(known) + "\n\nLABELS TO GROUP:\n" + "\n".join(lines)
    )
    try:
        out = structured(
            model=model, instructions=INSTRUCTIONS, user_input=user_input, schema=Grouping,
            step="discover", gaf_id="*",
        )  # fmt: skip
    except Exception as exc:
        log.warning("label grouping failed, using raw labels: %s", exc)
        return fallback
    mapping: dict[str, tuple[str, str]] = {}
    for g in out.groups:
        name = label(g.canonical)
        if not CANONICAL.match(name):
            continue
        for raw in g.raw_labels:
            if raw in labels and raw not in mapping:  # ignore labels the model made up or repeated
                mapping[raw] = (name, g.description.strip())
    return fallback | mapping  # any label the model left out stays its own group


def discover(session: Session, settings: Settings) -> list[SignalCategory]:
    """Recount every category across all stored signals, write the canonical name back onto each
    signal, and mark categories promoted or candidate. Returns the promoted ones."""
    rows = list(session.scalars(select(ContractorSignals)))
    examples: dict[str, str] = {}
    for r in rows:
        for o in r.data.get("other_signals", []):
            examples.setdefault(label(o["category"]), f'{o["detail"]} ("{o["quote"][:80]}")')
    existing = {c.name: c.description for c in session.scalars(select(SignalCategory))}
    mapping = group_labels(examples, existing, settings.small_model)

    members: dict[str, set[str]] = defaultdict(set)
    raw_of: dict[str, set[str]] = defaultdict(set)
    quotes: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        updated = []
        for o in r.data.get("other_signals", []):
            canonical, _ = mapping.get(label(o["category"]), (label(o["category"]), ""))
            members[canonical].add(r.gaf_id)
            raw_of[canonical].add(label(o["category"]))
            if len(quotes[canonical]) < 3:
                quotes[canonical].append(o["quote"])
            updated.append({**o, "canonical": canonical})
        if updated:
            r.data = {**r.data, "other_signals": updated}  # reassign so the JSON change is saved

    descriptions = {canon: desc for canon, desc in mapping.values() if desc}
    seen = set()
    for name, ids in members.items():
        status = (
            "promoted" if len(ids) >= settings.signal_promotion_min_contractors else "candidate"
        )
        session.merge(SignalCategory(
            name=name, description=descriptions.get(name) or existing.get(name, ""), status=status,
            contractor_count=len(ids), gaf_ids=sorted(ids), raw_labels=sorted(raw_of[name]),
            examples=quotes[name],
        ))  # fmt: skip
        seen.add(name)
    for stale in set(existing) - seen:  # a category nobody carries any more
        session.delete(session.get(SignalCategory, stale))
    session.commit()
    promoted = list(
        session.scalars(select(SignalCategory).where(SignalCategory.status == "promoted"))
    )
    log.info("signal categories: %d total, %d promoted", len(members), len(promoted))
    return promoted
