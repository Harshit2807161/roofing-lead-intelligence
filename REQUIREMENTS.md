# Roofing lead intelligence: requirements (draft v2, P0-scoped)

## The problem in one paragraph
A roofing **distributor** (think SRS, ABC Supply, Beacon) sells shingles and materials to **roofing contractors**. Its sales reps need to know which contractors to go after, why, who to talk to and what to say. GAF, the largest roofing manufacturer, publishes a public directory of GAF-certified contractors. We **pre-generate** lead insights from that directory, store them, and show reps a ranked, explainable lead list they review during account planning.

## Scope
- **P0 (build now):** the brief's search only, GAF residential contractors within 25 miles of ZIP **10013**: scrape → store → score → insights → Rep UI, end to end on that one dataset (85 contractors).
- **P1 (after P0 works):** a rep prep chat over the stored data; widening the same circle from the 85 "recommended" contractors to all 426 (one setting); plus the smaller items listed under P1 below.
- **Out of scope:** any other ZIP or radius, multi-ZIP territories, distance from a rep's branch. ZIP and radius sit in one config value so they aren't hard-coded, but nothing in the UI, API or evals uses other values.
- **Parked:** non-functional requirements (scale, reliability, cost, observability, security). We come back to them after P0; the list is at the bottom so nothing is lost.

## What the data source actually gives us (checked live, ZIP 10013, 25 mi)
- **85 residential contractors** across 9 pages of 10, with `countryCode=us` in the URL: https://www.gaf.com/en-us/roofing-contractors/residential?distance=25&postalCode=10013&countryCode=us
  - Without `countryCode`, the same search returns 81, and every distance is slightly different (Wayne 17.9 vs 18.7 mi), because GAF places the search centre differently.
  - So: always send `countryCode=us`, store the exact query URL with each record, and use GAF's reported distance as given.
- **Listing card:** name, Google rating, review count, city and state, distance, distinctions, phone, profile URL.
- **Distinctions:** President's Club (award), Master Elite, Certified Plus, Certified. **All 85 on the page are Master Elite**, so tier doesn't separate them; President's Club (12), commercial certifications (34) and specialties (7) do. Full field list: `DATA-EXPLORATION.md`.
- **Profile page:** street address, website, phone, "About" text (founding year, services such as siding, gutters, windows or commercial), GAF contractor ID, **state license number**, Google reviews with dates and text (often naming staff), certifications.
- **Access:**
  - Plain HTTP returns **403**, because the site has bot protection (Akamai).
  - The listing is loaded from a **Coveo search API** (JSON) that the page calls.
  - Profile pages are server-rendered HTML.
  - So we need either a headless browser or the page's own search API. Fetch slowly and only public pages, or we get blocked mid-demo.

## P0 functional requirements
| ID | Requirement |
|---|---|
| F1 | **Ingest:** collect every residential contractor for the brief's search (ZIP 10013, 25 mi, `countryCode=us`; expect 85 across 9 pages), and keep the raw listing JSON and profile HTML with `fetched_at` |
| F2 | **Profile parse:** parse each profile page into a typed record |
| F3 | **Normalize, dedupe and store:** dedupe on the GAF contractor ID; store contractors, reviews, raw snapshots, scores and insights in one database; keep the source URL for each field |
| F4 | **Lead score:** a deterministic, explainable score in code, with a per-factor breakdown the UI can show. Inputs: President's Club stars, commercial certifications, specialties, rating (adjusted for review count), review volume, newest review date, years in business and employees where shown, and GAF's distance, plus the LLM-extracted signals from step B. The weights live in code |
| F5 | **AI insights per lead** (structured output, grounded): a summary, why it's a fit, signals, likely decision-maker(s) with source, talking points, recommended next action. Every claim cites a field or review. Output is schema-validated, and a decision-maker appears only with a cited source (no invented people or contacts) |
| F6 | **Rep UI:** a ranked lead list with filters (tier, distance, rating, score), and a lead detail page with the score breakdown, insights, signals and source links. **Styled like an InstaLILY product** (next section) |
| F7 | **API:** `GET /leads`, `GET /leads/{id}`, `POST /pipeline/runs` (runs the configured search, no ZIP parameter) with run status |

## Rep UI style (part of F6)
- The UI should look like it came from InstaLILY: their fonts, colours, spacing and card style.
- **Before any UI code:** pull InstaLILY's style from instalily.ai with the `brand` skill (palette and type), turn it into CSS variables with the `design-system` skill, and render a one-page style card to check before building screens. If the live site blocks us, use the saved pages in `onsite-prep/offline/reading/instalily/`.
- First look at the saved homepage and InstaProspect pages: **DM Mono** for body and labels plus a separate display face, near-black text on warm off-white (`#0a0a0a`, `#f2f3ec`).
- **Layout precedent:** InstaProspect's ranked lead list (rank, initials, owner, rating, estimated value, score) is InstaLILY's own UI for this exact job.

## P1 (only after P0 runs end to end)
| ID | Requirement |
|---|---|
| F8 | **Rep prep chat:** a chat panel where a rep prepares for a call and asks questions, answered only from our stored data (details below) |
| F9 | **Lead workflow:** rep marks a lead new / contacted / not a fit, and adds notes |
| F10 | **Refresh and change detection:** re-run the same search, and flag what's new (new contractor, tier change, review surge) |
| F11 | **Extra enrichment:** the contractor's own website (services, team page, owner). This goes beyond GAF's pages, so it's in only if the chosen architecture needs it |
| F12 | **Feedback:** thumbs up or down on an insight, stored to improve prompts and scores |
| F13 | **All 426 in the circle:** drop the "recommended" flag to include GAF Certified and Certified Plus contractors, making tier a real score input |

**F8 rep prep chat, in more detail:**
- **Two entry points:** from a lead's page ("prep me for this call", pinned to that lead), and from the list, with questions across leads ("which Master Elite contractors within 10 miles have under 50 reviews?", "who mentions storm or insurance work?", "compare X and Y").
- **Read-only:** its tools query our database (contractors, reviews, scores, insights). No web, no writes, no outbound messages.
- **Grounded:** every fact names its source (contractor, review date, profile field). When our data doesn't hold the answer, it says so instead of guessing.
- **Review and About text are written by third parties:** they go into prompts as data, never as instructions. One eval case plants an instruction in a review.
- **Bounded:** a maximum number of tool calls per turn, enforced in code; history capped at the last N turns.
- **Retrieval:** structured questions become database filters. Free-text questions ("who does slate?") need search over review and About text, which is where embeddings come in.
- **Evals:** about 8 question-and-answer cases with the expected contractors and cited fields, including one "not in our data" case and one injection case.

## Out of scope
- Any other ZIP or radius, multi-ZIP territories per rep, distance from a rep's branch.
- Commercial directory, other manufacturers' directories, CSV or CRM export.

## Architecture
- **Ingest:** `fetch → parse → dedupe → store` is plain code; the plan is in `DATA-EXPLORATION.md`, awaiting approval.
- **Intelligence step: B, chosen.** Extract signals (small model) → score in code → write the insight (stronger model) → validate.
- **Still open:** score method, UI stack and model provider per step; options in `ARCHITECTURE-OPTIONS.md`.

## Parked: non-functional requirements (after P0)
Kept from draft v1 so nothing is lost; none of these is built in P0.
- **Scale:** per-contractor jobs on a queue with horizontally scalable workers; reps read precomputed rows from DB and cache.
- **Incremental cost:** hash each contractor's inputs and regenerate insights only when they change.
- **Reliability:** idempotent, resumable jobs; retries with backoff; dead-letter list; run reports fetched / parsed / failed counts.
- **Source politeness:** rate limits, caching, robots.txt and terms.
- **Scraper drift:** schema validation of parsed records, null-rate alerts, fixture pages for parser tests.
- **Data management:** Postgres, raw snapshots in object storage, versioned insights (model, prompt version, input hash), lead status per rep, history for diffs.
- **Cost and latency:** small model for extraction, larger for synthesis, batch calls, cost per lead.
- **Security and privacy:** rep auth and territory scoping; care with personal names from reviews.
- **Observability:** pipeline run metrics, per-lead trace, LLM spans (tokens, latency, cost).
- **Evals at scale:** parse completeness against known pages, grounding checks, score sanity, a hand-labelled insight set.
- **Extensibility:** pluggable source adapters (Owens Corning, CertainTeed).
