# Roofing lead intelligence

Pre-generated, explainable sales leads for a roofing distributor's reps, built from GAF's public directory of certified contractors (the brief's search: ZIP 10013, 25 mi, the 85 contractors GAF shows).

```text
GAF search API ──► listing.json ─┐
GAF profiles (headless Chrome) ──┴─► parse (pure) ─► dedupe + upsert ─► Postgres
                                                                         │
           extract signals (small model, verbatim quotes checked in code) ◄┘
           → discover open-ended categories (promote at ≥10 contractors)
           → score in code (weighted points, reason per factor, play tag)
           → write insight (stronger model) → validate → 1 retry → ok | needs_review
                                                                         │
                                         FastAPI + Jinja + HTMX rep UI ◄─┘
```

## Run it
```bash
cp .env.example .env               # add COVEO_API_KEY and OPENAI_API_KEY
docker compose up -d db            # Postgres 17 + pgvector on localhost:5433
uv run python -m app.ingest.run    # fetch → parse → dedupe → load (≈5 min, polite)
uv run python -m app.intel.run     # extract → discover → score → write (≈5–8 min, ≈$1: ~$0.011 per contractor)
uv run uvicorn app.api:app --port 8000   # http://localhost:8000
```
- Re-parse saved pages without hitting GAF: `uv run python -m app.ingest.run --from-raw <folder under data/raw>`.
- Re-running either pipeline is safe. Ingest upserts on the GAF ID; intel skips any contractor whose inputs haven't changed.

## Checks
```bash
uv run pytest -q          # 31 tests: parser on real saved pages, idempotent load, score, validators, API
uv run ruff check app tests
```

## Where things are
| Path | What |
|---|---|
| `app/ingest/` | `fetch` (API + Chrome), `parse` (pure functions), `load` (dedupe, upsert), `run` (CLI) |
| `app/intel/` | `extract`, `discover`, `score`, `insight`, `llm` (one wrapper, JSONL traces), `run` |
| `app/web/`, `app/templates/`, `app/static/` | Rep UI; InstaLILY tokens in `app/static/tokens.css`, style card at `/style` |
| `app/models.py` | runs, raw_snapshots, contractors, reviews, signals, signal_categories, scores, insights |
| `REQUIREMENTS.md`, `DATA-EXPLORATION.md`, `ARCHITECTURE-OPTIONS.md` | Scope, what the source gives us, options considered and why |

## Key decisions
- **Scope:** the 85 contractors the brief's link shows (all Master Elite). Dropping GAF's "recommended" flag gives 426 in the same circle; that's P1 and one setting (`SEARCH_RECOMMENDED_ONLY=false`).
- **Option B, the LLM proposes and code disposes:** extraction quotes must be verbatim; the rank is code; insights may cite only facts we supplied and may name only verified people.
- **Models:** OpenAI `gpt-6-luna` for extraction and grouping (~1.5k tokens in, $0.0005 a call), `gpt-6.1-sol` for writing (~1.7k in, $0.01 a call). The LLM never sees raw HTML: parsing first cuts a ~50k-token page to ~1.5k tokens of text. Perplexity is kept for P1 web research on owners, where open-web search with citations is the point.
