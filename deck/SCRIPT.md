# Script: Roofing lead intelligence (about 9 min talk + 6 min demo + Q&A)

Open `deck/index.html` (→ / space to advance, ← back). Have `http://localhost:8000` open in a second tab, on Allied Brothers (`/leads/1116285`) and the list.

---

## 1 · Title (20 s)
"I built a lead-intelligence tool for a roofing distributor's sales reps, from GAF's public contractor directory. One idea runs through all of it: every lead is ranked, and every reason behind the rank is something a rep can check."

## 2 · The job (40 s)
"Before a call, a rep needs four answers: who to call first, why them, who to ask for, and what to say. And they need to trust those answers. InstaLILY's own targeting-list work shows that reps ignore a list that says who without saying why. So success for me meant three things: every lead scored and explained, every claim traceable to a source, and about a cent per lead, computed ahead of time so reps only read."

## 3 · The data (60 s)
"I explored the source before designing anything, and it changed the design.
- The listing comes from GAF's own search API: one call returns all 85, with coordinates. A hidden 'recommended' flag narrows the same 25-mile circle from 426 to 85. I kept the brief's 85 and made 426 one setting.
- All 85 are Master Elite, so certification tier can't rank anyone.
- Each profile shows only its 3 newest reviews, and 11 have none.
- Profiles return 403 to plain HTTP. Headless Chrome loads them, and the data inside is embedded JSON, so I parse JSON, not visible text.
What does separate them is President's Club, commercial certifications, and what's written in the free text: trades, staff names and review themes. That's why the LLM matters here."

## 4 · Architecture (60 s)
"Code owns the pipeline. The LLM works inside two fenced steps, the green ones.
- Fetch and parse are plain code. Raw files are kept, so I can re-parse without hitting GAF again, and dedupe is an upsert on GAF's ID.
- Step one: a small model extracts signals, and every signal must carry a quote. Code drops any quote that isn't word for word in the source.
- The score is plain code: weighted points with a reason per factor, plus a play tag like 'Volume leader'.
- Step two: a stronger model writes the insight. It may cite only facts we handed it and name only people we verified. A validator checks it, retries once, and otherwise flags it for review.
One more thing: the extractor can propose new signal categories. When ten or more contractors share one, code promotes it. The count decides, not the model.
In one line: the LLM proposes, code disposes."

## 5 · Options (40 s)
"I compared three shapes. A writes on top of a code score: simplest, but the rank can't see the text. C sends an agent to research the top leads on the web: real owner names, but it leaves the data we were given and is hard to test. I chose B. The LLM reads the text, code ranks, and the LLM writes last. That's the same order as InstaProspect: signals in, rules score, the opening written last. I rejected letting a model score leads directly, because it's unstable and nobody can say why #3 beat #4."

## 6 · Trust (40 s)
"Three checks, all in code.
- Extraction: 1% of 1,018 quotes were dropped because they weren't verbatim.
- Insights: 85 of 85 passed validation first try. That means citations only to supplied facts, people only if verified, and no invented phone numbers.
- Chat: every citation is checked against what the tools actually returned in that turn.
In the UI, you can hover any tag or citation and see the sentence it came from."

## 7 · Cost (30 s)
"A profile page is about 50,000 tokens of HTML. The model reads about 1,500, because I parse first and send only the About text and reviews. Extraction costs half a tenth of a cent; writing costs a cent. All 85 cost 85 cents, measured from the traces, and a re-run with unchanged inputs costs nothing."

## 8 · Demo (about 6 min, live)
Run of show:
1. **List** (`/`). "Ranked by score. The first line is GAF's facts; the second is what the AI found in the text." Hover a sentence-case tag on Allied ("Additional project work") to show its verified quote. Set **Signal → Storm & insurance** to get 11 leads, then reset.
2. **Lead** (Allied, #1). Walk the score breakdown: President's Club 20/20, job volume 14.7/18. Then the insight: next action "Ask for Jonathan Guglielmino". Hover his citation chip to show the review quote. "Named because a customer named him, not because the model guessed."
3. **Chat**: click "Prep me for a first call…". The question appears at once. Point at the citation chips in the answer, then ask a follow-up: "What do customers complain about?" Optionally try "What's their annual revenue?", which answers "That isn't in our data".
4. **Workflow and feedback**: mark Contacted, add a note, then click Useful. "Every vote stores the prompt version that wrote the insight, so a downvote becomes an eval case."
5. **Compare** (link under the score breakdown): #1 vs #2. "Allied leads by 8.1, almost all President's Club; Complete Roof Systems is ahead on commercial."
6. **Tuner**: drag President's Club to 0×. Allied drops from #1 to #5. "A sales manager can tilt the ranking for this quarter's push; the official score doesn't change until engineering adopts the weights."

If something breaks live: the API is at `/api/leads/1116285`, and the screenshots are in `.playwright-mcp/`.

## 9 · Evidence (40 s)
"I measured instead of asserting. The scorecard covers all 85: 66 name a verified contact, and a re-run makes zero new rows and zero LLM calls. There are 87 tests. The ones that matter run the pipeline end to end on saved GAF pages, check every filter against SQL, and check that every citation resolves for every lead. The honest gap: the validator checks where facts come from, not that every sentence is right. The next eval round is a check that every number in an insight matches a stored field."

## 10 · Future: production (60 s)
"To go from one laptop to thousands of reps, three changes.
- **Identity:** a login with JWT sessions, meaning a short-lived access token and a refresh token in an httpOnly cookie. Each rep sees their own territory, and managers get the tuner. The workflow and feedback rows already store who acted, so this slots in.
- **Read path:** the app is already stateless, since even the chat carries its history in the request. So it scales out behind a load balancer. Redis caches the list, lead pages and tag counts, and is invalidated when a run lands. Chat gets per-rep rate limits, and Postgres gets read replicas.
- **Write path:** one job per contractor on a queue, run by Celery workers asynchronously. That means retries with backoff, idempotent jobs because of the input hash we already store, nightly runs from a scheduler, and the deferred POST /pipeline/runs simply enqueues.
Cost still grows with contractors, not reps, because reps only read precomputed rows."

## 11 · Future considerations (60 s)
"Three directions.
- **Data:** widen to all 426, which is one setting and makes tier a real signal. Research owners for the top leads with Perplexity, which is built for cited web search. Match against the distributor's customer book, and flag what changed between runs.
- **Learning:** downvotes become eval cases. Tuner weights, then win/loss data, set the score instead of my judgment. Embeddings come in when paraphrase or volume needs them; Postgres full-text is enough for 220 reviews.
- **Product:** CRM write-back behind an approval step, tracing and cost dashboards, and daily 'what changed' alerts rather than another dashboard."

## 12 · Close (10 s)
"Ranked, explained and checkable. P0 is on main; the P1 features are on their branch. Happy to take questions."

---

## Likely questions, short answers
- **Why weights you picked?** "There are no outcome labels yet. The weights are explicit, in one file, and testable. The tuner and feedback are the path to learning them."
- **Are the named people decision-makers?** "Not necessarily. They're people customers name, often project managers. The insight says so and suggests asking who orders materials. Owner research is option C."
- **Why not embeddings / RAG?** "Each contractor's record fits in one prompt. For the chat, full-text search over 220 reviews is exact and enough; embeddings come with scale or paraphrase."
- **Why OpenAI and not Perplexity?** "Perplexity searches the web even with no tools, and is weakest on private data. OpenAI's strict structured output fits extraction and writing. Perplexity fits owner research."
- **Is scraping OK?** "Public pages only, one at a time with a pause, using GAF's own search API the page calls. The key lives in config, not in code."
- **Scale to thousands of reps?** "Reps only read precomputed rows, cached in Redis, from stateless app servers. Writes are per-contractor Celery jobs. The chat is the one per-rep cost, and it has budgets and rate limits."
- **What breaks, and what catches it?** "GAF rotates its key: the run fails loudly on the count check. A misquote: the verbatim check drops it. An invented person: the validator rejects it. Instructions in a review: treated as data, and the tools are read-only."
