"""Intelligence pipeline (B): extract signals → discover categories → score in code → write
insights → validate → store.

    uv run python -m app.intel.run                # everything; unchanged inputs are skipped
    uv run python -m app.intel.run --limit 5      # extract/write only the top 5 (scores always cover all)
    uv run python -m app.intel.run --force        # ignore the input-hash cache

LLM calls run in a small thread pool; the database is only touched from the main thread.
"""

import argparse
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.db import get_engine, init_db, session_factory
from app.intel import extract as ex
from app.intel import insight as ins
from app.intel.discover import discover
from app.intel.score import score_all
from app.models import Contractor, ContractorSignals, LeadInsight, LeadScore, SignalCategory

log = logging.getLogger("intel")


def load_contractors(session: Session) -> list[Contractor]:
    stmt = (
        select(Contractor).options(selectinload(Contractor.reviews)).order_by(Contractor.gaf_rank)
    )
    return list(session.scalars(stmt))


def run_extract(
    session: Session, settings: Settings, todo: list[Contractor], force: bool
) -> Counter:
    stats: Counter = Counter()
    stored = {s.gaf_id: s for s in session.scalars(select(ContractorSignals))}
    pending = []
    for c in todo:
        text, _ = ex.build_input(c)
        prev = stored.get(c.gaf_id)
        if not force and prev and prev.input_hash == ex.input_hash(settings.small_model, text):
            stats["extract_cached"] += 1
        else:
            pending.append(c)
    known = [cat.name for cat in session.scalars(select(SignalCategory))]
    with ThreadPoolExecutor(settings.llm_concurrency) as pool:
        futures = {pool.submit(ex.extract, c, settings.small_model, known): c for c in pending}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                signals, dropped, h = fut.result()
            except Exception as exc:
                log.warning("extract %s failed: %s", c.gaf_id, exc)
                stats["extract_failed"] += 1
                continue
            session.merge(ContractorSignals(gaf_id=c.gaf_id, model=settings.small_model,
                                            prompt_version=ex.PROMPT_VERSION, input_hash=h,
                                            data=signals.model_dump(mode="json"), dropped=dropped))  # fmt: skip
            stats["extracted"] += 1
            stats["quotes_dropped"] += len(dropped)
    session.commit()
    return stats


def run_scores(session: Session, contractors: list[Contractor]) -> dict[str, dict]:
    signals = {s.gaf_id: ex.load(s.data) for s in session.scalars(select(ContractorSignals))}
    rows = score_all(contractors, signals)
    for r in rows:
        session.merge(LeadScore(**r))
    session.commit()
    return {r["gaf_id"]: r for r in rows}


def run_insights(
    session: Session,
    settings: Settings,
    todo: list[Contractor],
    scores: dict[str, dict],
    force: bool,
    promoted: frozenset[str],
) -> Counter:
    stats: Counter = Counter()
    signals = {s.gaf_id: ex.load(s.data) for s in session.scalars(select(ContractorSignals))}
    stored = {i.gaf_id: i for i in session.scalars(select(LeadInsight))}
    pending = []
    for c in todo:
        sig = signals.get(c.gaf_id, ex.EMPTY)
        h = ins.input_hash(settings.model, ins.build_input(c, scores[c.gaf_id], sig, promoted))
        prev = stored.get(c.gaf_id)
        if not force and prev and prev.input_hash == h and prev.status == "ok":
            stats["insight_cached"] += 1
        else:
            pending.append((c, sig))
    with ThreadPoolExecutor(settings.llm_concurrency) as pool:
        futures = {
            pool.submit(ins.write, c, scores[c.gaf_id], sig, settings.model, promoted): c
            for c, sig in pending
        }
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                insight, errors, attempts, h = fut.result()
            except Exception as exc:
                log.warning("insight %s failed: %s", c.gaf_id, exc)
                stats["insight_failed"] += 1
                continue
            status = "ok" if not errors else "needs_review"
            session.merge(LeadInsight(gaf_id=c.gaf_id, model=settings.model, prompt_version=ins.PROMPT_VERSION,
                                      input_hash=h, status=status, data=insight.model_dump(mode="json"),
                                      validation_errors=errors, attempts=attempts))  # fmt: skip
            stats[f"insight_{status}"] += 1
            stats["insight_retried"] += attempts > 1
            session.commit()  # insights appear in the UI as they finish
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--limit", type=int, help="only the top N by GAF order get LLM calls")
    parser.add_argument("--force", action="store_true", help="ignore the input-hash cache")
    parser.add_argument("--skip-insights", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    settings = get_settings()
    engine = get_engine()
    init_db(engine)
    with session_factory(engine)() as session:
        everyone = load_contractors(session)
        todo = everyone[: args.limit] if args.limit else everyone
        stats = run_extract(session, settings, todo, args.force)
        promoted = frozenset(cat.name for cat in discover(session, settings))
        log.info("promoted signal categories: %s", sorted(promoted) or "none yet")
        scores = run_scores(session, everyone)
        log.info("scored %d contractors", len(scores))
        if not args.skip_insights:
            # Write insights in score order, so the leads a rep sees first are ready first.
            ranked = sorted(todo, key=lambda c: scores[c.gaf_id]["rank"])
            stats += run_insights(session, settings, ranked, scores, args.force, promoted)
    print("intel run:", dict(stats), f"scored={len(scores)}")


if __name__ == "__main__":
    main()
