# After dedupe: score, insight and UI options

The pipeline up to the database (`fetch → parse → dedupe → store`) is proposed in `DATA-EXPLORATION.md`. This file covers what comes after: the intelligence step (decided), the score (open) and the UI (open).

## What the data changed
- **All 85 are Master Elite**, so certification tier can't rank them. President's Club (12), commercial certifications (34) and specialties (7) are the distinctions that vary.
- **Only 3 reviews per contractor**, and 11 have none. Reviews show recent activity and name staff, but give no trend.
- **About text is sometimes empty** (Blackstone). Extraction has to cope with no text, and the score has to work on listing fields alone.
- **Decision-makers:** GAF's pages name sales and project staff in reviews, and rarely the owner. Owner names would need the web (option C, P1).
- **Latitude and longitude** for every contractor make a map view cheap.

## 1. Intelligence step: **B, chosen**
**Extract → score → write.**
1. A small model reads the About text and reviews and returns typed signals, each with a verbatim quote: services, commercial work, years in business, named staff and their role, review themes and complaints.
2. Code drops any signal whose quote isn't in the source.
3. The score uses the GAF fields plus the surviving signals, with the weights in code.
4. A stronger model writes the insight from the typed record and the score breakdown.
5. **Fallback:** if extraction isn't ready in time, the writer runs on GAF fields alone (option A).

Considered and not chosen:
- **A (score, then write):** simplest, but the rank can't see services or activity in the text.
- **C (research the top N on the web):** the only way to real owner names, but it leaves GAF data and is harder to test. Candidate for P1.
- **The LLM scores the lead:** unstable across runs and impossible to explain.

**The insight loop:** generate → validate in code → one retry with the errors → else store as `needs_review`.
- The validator checks the schema, that every citation points at a stored field or review, and that every name appears verbatim in the source.
- The same check runs on extraction.

## 2. Lead score (open)
Inputs, all in code:
- **From GAF fields:** President's Club stars; review count (log-scaled); rating, adjusted for how many reviews back it; date of the newest review; commercial certifications; specialties; years in business and employee band (where shown); distance from 10013.
- **From B's extraction:** extra services (siding, gutters, windows: more products to sell), commercial or storm work mentioned, named staff (someone to ask for), complaint themes (risk).

| | S1 · Weighted points | S2 · Percentile within the territory | S3 · Segments, then sort |
|---|---|---|---|
| How | Each factor earns capped points; total 0–100 | Each factor becomes its percentile among the 85, then weighted | Rules put each lead in a play ("volume leader", "commercial cross-sell", "growing shop"), sorted within it |
| Rep sees | "+18 President's Club 2★, +12 commercial GoldElite…" | "Top 10% for reviews" | "Commercial cross-sell, #2 of 9" |
| Strength | Easiest to explain and unit-test; weights in one config file | One outlier (1,334 reviews) can't swamp the rest; calibrates itself | Matches how reps plan; each play suggests an opening |
| Weakness | Weights are a judgment call; raw counts need log scaling | Ranks shift when other contractors change; percentiles are harder to read | Coarse; the rules need care at the edges |
| Build time | ~20 min | ~30 min | ~40 min |

**Rating adjustment (all options):** pull a rating toward the territory average when few reviews back it, so 4.9 from 283 reviews beats 5.0 from 2.

**My pick: S1, plus a play label from S3's rules as a tag.** It gives the clearest "why" on screen, and the play label gives the rep an opening line.
- Rejected: a learned model (we have no outcome labels). Also rejected: copying GAF's own order, which ranks for homeowners, not for a distributor.

## 3. Rep UI stack (open)
| | U1 · FastAPI + Jinja + HTMX | U2 · React (Vite) + FastAPI API | U3 · Streamlit |
|---|---|---|---|
| Strength | One process; server-rendered; InstaLILY CSS tokens drop straight in; filters and detail panes via HTMX | Richest interaction (map, live filters, chat panel) | Fastest data app |
| Weakness | Less app-like; complex client state is awkward | A second build and toolchain; ~1 h more | Can't match InstaLILY's look, which fails the style requirement |
| Fits F6 + P1 chat | Yes; the chat streams over SSE | Yes | Weak |

**My pick: U1.** It's the shortest path to a styled, polished list and detail page. A Leaflet map from the latitude and longitude is a cheap distinctive feature. Switch to U2 only if the chat or map needs more client state than HTMX handles cleanly.

## 4. Model provider per step
Researched from the providers' docs on 20 Oct 2026. Model IDs live in `.env`, and I'll confirm each with one test call once the keys are in.

| Step | Provider · model | Why | Rough cost for 85 |
|---|---|---|---|
| Extract signals (B, step 1) | OpenAI `gpt-6-luna`, Responses API, strict JSON schema, low reasoning | Cheapest current model ($0.10 / $0.50 per 1M tokens); strict structured output | ~$0.03 |
| Write insights (B, step 4) | OpenAI `gpt-6.1-sol`, low reasoning | OpenAI's "balance" model at $2 / $10, close to flagship quality | ~$1–2 |
| Embeddings (P1) | OpenAI `text-embedding-3-small`, 1536 dims | $0.02 per 1M; fits pgvector's index limit; plain floats | <$0.01 |
| Rep chat (P1) | OpenAI `gpt-6.1-sol`, Responses API, strict tools, streaming | Its tool calling works only in the Responses API | ~$0.015 a turn |
| Web research for owners (option C, P1) | Perplexity Agent API, `low` preset + `people_search`, JSON schema | Built for open-web research; returns typed search results; people search suits owner lookup | ~$0.20–0.30 for 15 |

- **Perplexity's Sonar API was retired on 27 Sep 2026.** Its replacement is the Agent API, which uses presets (`fast` … `xhigh`) and is OpenAI-SDK compatible.
- **Perplexity is a poor fit for our own data.** It searches the web even with no tools, and it's most likely to make things up when the facts aren't online. So it stays out of steps 1–4.
- **Gotchas:**
  - OpenAI strict mode needs every field `required` and `additionalProperties: false`; optional fields are typed as `[type, "null"]`.
  - Reasoning tokens bill as output, so set the effort explicitly.
  - Perplexity's first call with a new schema takes 10–30 s.
  - A Perplexity citation points at a search result but doesn't prove the sentence. Take URLs from `search_results`, never from the model's text.
  - A new Perplexity account is limited to 1 request per second.
- **No Batch API** inside a 4-hour build (it can take up to 24 h). Synchronous calls cost cents here.

## Side decisions
- **Embeddings:** not needed in the P0 pipeline, because a contractor's whole record fits in one prompt. They come in with the P1 chat, in the same Postgres via pgvector.
- **Chat shape (P1):** one tool-gated agent (pattern B) with read-only database tools.
- **Orchestration (P0):** plain Python with one task per contractor. There's no approval or pause in the pipeline, so LangGraph adds nothing until option C.
