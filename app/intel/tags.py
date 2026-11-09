"""Semantic tags for the lead list, built from verified extraction signals.

A tag exists only because a quote survived the verbatim check, and it carries that quote, so the
list describes a lead in words and every word is backed by a source. Promoted open-ended
categories become tags automatically; nothing here needs to change when a new one is promoted.
"""

from typing import Any

TRADES = {
    "siding": "Siding", "gutters": "Gutters", "windows": "Windows", "doors": "Doors",
    "skylights": "Skylights", "solar": "Solar", "insulation": "Insulation", "chimney": "Chimney",
    "masonry": "Masonry", "decks": "Decks", "painting": "Painting",
    "general_contracting": "General contracting",
}  # fmt: skip
FIXED = {"storm": "Storm & insurance", "warranty": "GAF warranty", "contact": "Named contact",
         "complaints": "Complaints"}  # fmt: skip


def source_label(source: str) -> str:
    """'review_2' -> 'Review 2', 'about' -> 'About'."""
    return source.replace("_", " ").capitalize()


def category_label(name: str) -> str:
    """'family_owned_business' -> 'Family owned business', for any promoted category."""
    return name.replace("_", " ").capitalize()


def _tag(
    key: str, label: str, quoted: dict, kind: str, keys: list[str] | None = None
) -> dict[str, Any]:
    return {"key": key, "keys": keys or [key], "label": label, "kind": kind,
            "quote": quoted.get("quote", ""), "source": source_label(quoted.get("source", ""))}  # fmt: skip


def semantic_tags(data: dict | None, promoted: set[str] | frozenset[str]) -> list[dict[str, Any]]:
    """Tags in display order: trades, promoted categories, then storm, warranty, contact, complaints."""
    if not data:
        return []
    tags: list[dict[str, Any]] = []

    trades, seen = [], set()
    for s in data.get("services", []):
        if s.get("service") in TRADES and s["service"] not in seen:
            seen.add(s["service"])
            trades.append(s)
    if trades:
        names = [TRADES[t["service"]] for t in trades]
        label = " · ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
        tags.append(
            _tag("trades", label, trades[0], "trade", [f"trade:{t['service']}" for t in trades])
        )

    shown = set()
    for o in data.get("other_signals", []):
        name = o.get("canonical") or o.get("category")
        if name in promoted and name not in shown:
            shown.add(name)
            tags.append(_tag(f"signal:{name}", category_label(name), o, "signal"))

    if storm := data.get("storm_or_insurance_work"):
        tags.append(_tag("storm", FIXED["storm"], storm, "signal"))
    if warranty := data.get("gaf_warranty_mentions"):
        tags.append(_tag("warranty", FIXED["warranty"], warranty[0], "signal"))
    if people := data.get("people"):
        tags.append(_tag("contact", FIXED["contact"], people[0], "contact"))
    if complaints := data.get("complaints"):
        tags.append(_tag("complaints", FIXED["complaints"], complaints[0], "warn"))
    return tags


def tag_label(key: str) -> str:
    """The filter-menu label for a tag key."""
    if key.startswith("trade:"):
        return TRADES.get(key[6:], key[6:])
    if key.startswith("signal:"):
        return category_label(key[7:])
    return FIXED.get(key, key)
