"""Scorecard for one state of the pipeline: coverage, grounding, validation, latency and cost.

    uv run python -m evals.scorecard            # prints the table and saves evals/results/<time>.json

Numbers come from the database and traces/llm.jsonl, so they describe what actually ran.
"""

import json
import math
import re
import statistics as st
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.config import ROOT
from app.db import get_engine, session_factory
from app.models import Contractor, ContractorSignals, LeadInsight, LeadScore, SignalCategory

# $ per 1M tokens (input, output), from ARCHITECTURE-OPTIONS.md section 4.
PRICES = {"gpt-6-luna": (0.10, 0.50), "gpt-6.1-sol": (2.00, 10.00)}
RESULTS = ROOT / "evals" / "results"


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "0/0"


def error_category(message: str) -> str:
    """Group validator messages into failure categories (axial coding, done by code)."""
    for key, name in [("unknown cite", "unknown citation key"), ("not in PEOPLE", "invented person"),
                      ("not verbatim", "misquoted person"), ("contact detail", "invented contact detail"),
                      ("is empty", "empty section"), ("without cites", "uncited point")]:  # fmt: skip
        if key in message:
            return name
    return "other"


def trace_stats(path: Path) -> dict:
    by_step: dict[str, list[dict]] = defaultdict(list)
    if path.exists():
        for line in path.read_text().splitlines():
            span = json.loads(line)
            by_step[span["step"]].append(span)
    out = {}
    for step, spans in by_step.items():
        ok = [s for s in spans if s.get("ok")]
        lat = sorted(s["latency_s"] for s in ok) or [0]
        cost = sum(s["input_tokens"] * PRICES.get(s["model"], (0, 0))[0]
                   + s["output_tokens"] * PRICES.get(s["model"], (0, 0))[1] for s in ok) / 1e6  # fmt: skip
        out[step] = {"calls": len(spans), "errors": len(spans) - len(ok),
                     "p50_s": round(st.median(lat), 1), "p95_s": round(lat[max(0, math.ceil(0.95 * len(lat)) - 1)], 1),
                     "cost_usd": round(cost, 4)}  # fmt: skip
    return out


def main() -> None:
    with session_factory(get_engine())() as db:
        n = db.scalar(select(func.count()).select_from(Contractor))
        signals = list(db.scalars(select(ContractorSignals)))
        insights = list(db.scalars(select(LeadInsight)))
        scored = db.scalar(select(func.count()).select_from(LeadScore))
        cats = list(db.scalars(select(SignalCategory)))

    kept = Counter()
    for s in signals:
        for field in (
            "services",
            "people",
            "gaf_warranty_mentions",
            "praise",
            "complaints",
            "other_signals",
        ):
            kept[field] += len(s.data.get(field, []))
    dropped = Counter(d["field"] for s in signals for d in s.dropped)
    total_kept, total_dropped = sum(kept.values()), sum(dropped.values())

    status = Counter(i.status for i in insights)
    first_try = sum(1 for i in insights if i.status == "ok" and i.attempts == 1)
    after_retry = sum(1 for i in insights if i.status == "ok" and i.attempts > 1)
    failures = Counter(error_category(e) for i in insights for e in i.validation_errors)
    people_named = sum(1 for i in insights if i.data.get("decision_makers"))
    traces = trace_stats(ROOT / "traces" / "llm.jsonl")
    total_cost = sum(t["cost_usd"] for t in traces.values())

    card = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "coverage": {
            "contractors": n,
            "signals": len(signals),
            "scored": scored,
            "insights": len(insights),
        },
        "extraction": {
            "items_kept": total_kept,
            "items_dropped": total_dropped,
            "drop_rate": round(total_dropped / max(1, total_kept + total_dropped), 3),
            "dropped_by_field": dict(dropped),
        },  # fmt: skip
        "insights": {
            "ok_first_try": first_try,
            "ok_after_retry": after_retry,
            "needs_review": status.get("needs_review", 0),
            "with_named_contact": people_named,
            "failure_categories": dict(failures),
        },  # fmt: skip
        "signal_categories": {
            "total": len(cats),
            "promoted": sorted(
                (c.name, c.contractor_count) for c in cats if c.status == "promoted"
            ),
        },  # fmt: skip
        "llm": traces,
        "cost_usd_total": round(total_cost, 3),
        "cost_usd_per_contractor": round(total_cost / max(1, n), 4),
    }

    print(
        f"Coverage     signals {len(signals)}/{n} · scored {scored}/{n} · insights {len(insights)}/{n}"
    )
    print(f"Grounding    extraction quotes dropped {pct(total_dropped, total_kept + total_dropped)}"
          f" · by field {dict(dropped) or '{}'}")  # fmt: skip
    print(f"Validation   ok first try {pct(first_try, len(insights))} · ok after retry {after_retry}"
          f" · needs review {status.get('needs_review', 0)} · failure categories {dict(failures) or '{}'}")  # fmt: skip
    print(f"Contacts     insights naming a verified person {pct(people_named, len(insights))}")
    print(
        f"Categories   {len(cats)} discovered · promoted (≥10 contractors): "
        + (", ".join(f"{a} ({b})" for a, b in card["signal_categories"]["promoted"]) or "none")
    )
    for step, t in sorted(traces.items()):
        print(
            f"LLM {step:<9}{t['calls']:>4} calls · errors {t['errors']} · p50 {t['p50_s']}s · p95 {t['p95_s']}s · ${t['cost_usd']}"
        )
    print(
        f"Cost         ${total_cost:.2f} total · ${card['cost_usd_per_contractor']:.4f} per contractor"
    )

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{re.sub(r'[^0-9T]', '', card['at'])}.json"
    out.write_text(json.dumps(card, indent=1))
    print(f"saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
