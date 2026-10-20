# Data exploration: GAF residential directory, ZIP 10013, 25 mi

Checked live on 20 Oct 2026: the full listing (all 85) and two profile pages (Allied Brothers, 1116285; Blackstone Group, 1151902). Listing numbers are exact. Profile numbers come from those two pages only; real fill rates will come from the first full scrape.

## What we found
1. **Two different ways in.**
   - **Listing:** the page calls a search API (Coveo) that returns clean JSON. One call with `numberOfResults=100` returns all 85 contractors, so there's no paging.
   - **Profiles:** server-rendered HTML behind bot protection. Plain HTTP gets **403**; a headless browser loads them fine.
   - Each profile embeds JSON (a schema.org block plus `data-contractor` and `data-reviews` attributes), so parsing is mostly reading JSON, not scraping text.
2. **The page shows 85, but the same 25-mile circle holds 426.** The page sends a "recommended" flag (`sortingStrategy: gafrecommended-initial`). Without it, the same API returns 426 residential contractors, mostly GAF Certified and Certified Plus. **The 85 are all Master Elite and all enrolled in GAF Leads.**
3. **Certification tier doesn't separate the 85.** It's the same for all of them. What does vary:
   - President's Club: 12 contractors
   - Commercial certifications: 34
   - Specialties (solar, metal, FORTIFIED): 7
   - Review count: 0 to 1,334, median 104
   - Rating: median 4.9; 11 have no rating (0.0 means no reviews)
   - Distance: 1.5 to 24.9 mi
   - Years in business and employee count, where the profile lists them
4. **Only the 3 newest reviews are on a profile.** That's enough to tell whether a contractor is active, but not to see a trend. The rest live on Google (out of scope).
5. **Reviews name the people a rep can ask for:** "Tyler, Lisa, Edgar", "Bryan", "Jonathan Guglielmino was our main contact". Sales and project staff are the decision-maker evidence GAF's pages give us. Owners rarely appear.
6. **Two phone numbers.** The listing phone is the business line. The page's click-to-call number sometimes differs (Allied: 566-0060 vs 566-3383), probably GAF call tracking. We keep both and show the business line.
7. **No duplicates in this set.** Contractor ID, phone, name and Google place ID are all unique. We still dedupe on ID, so re-runs and paging can't create doubles.

## Per-contractor fields we can store

**From the listing (all 85):**
| Field | Source key | Filled | Notes |
|---|---|---|---|
| GAF contractor ID | `gaf_contractor_id` | 85 | Unique; the dedupe key |
| Name / DBA | `title` / `gaf_contractor_dba` | 85 / 32 | |
| Profile URL | `uri` | 85 | |
| Address, city, state, ZIP | `gaf_address`, `gaf_f_city`, `gaf_f_state_code`, `gaf_postal_code` | 85 | 58 NJ, 27 NY, 65 cities |
| Latitude / longitude | `gaf_latitude`, `gaf_longitude` | 85 | Enough for a map view |
| Distance (mi) | `distanceinmiles` | 85 | From GAF's geocode of 10013 (40.72179, -74.00945) |
| Business phone | `gaf_phone` | 85 | All distinct |
| Google rating / review count | `gaf_rating` / `gaf_number_of_reviews` | 85 | 11 with 0 reviews |
| Residential certifications and awards | `gaf_f_contractor_certifications_and_awards_residential` | 85 | All Master Elite; 12 also President's Club |
| President's Club stars | `gaf_residential_presidents_club_stars_count` | 12 | 10 × 1 star, 2 × 2 stars |
| Commercial certifications | `gaf_f_contractor_certifications_and_awards_global_search_commercial` | 34 | Certified Commercial 17, GoldElite 14, CoatingsPro 7, CoatingsPro+ 2 |
| Commercial technologies | `gaf_f_contractor_technologies_commercial` | 34 | Single-ply 30, asphaltic 18, restoration 9 |
| Residential specialties | `gaf_f_contractor_specialties_residential` | 7 | Solar 4, FORTIFIED 4, metal 2 |
| Google place ID | `gaf_google_place_id` | 77 | Link to the contractor's Google reviews |
| Logo | `gaf_featured_image_src` | 62 real, 23 placeholder | |
| GAF's own order and score | result position, `gaf_contractor_residential_ext_score` | 85 | Score is 1000 / 1100 / 1200 and tracks President's Club, so it adds nothing new |
| Constant across all 85 | `gaf_enrolled_in_gaf_leads`, contractor type, Master Elite | 85 | Not worth a column |

**From the profile page (2 sampled):**
| Field | Where on the page | Allied Brothers | Blackstone Group |
|---|---|---|---|
| About text | schema.org `description` | 600 chars: founded 1996; roofing, gutters, siding, windows, doors, commercial | Empty |
| Street address | schema.org `address` | Yes | Yes |
| Website | "Visit Website" link | alliedbrothers.net | None |
| Click-to-call phone | `tel:` link | Differs from listing | Same as listing |
| State license number | Details list | 13VH08476800 | 13VH07739600 (NJ home-improvement format) |
| Years in business | Details list | Not shown | "In business since 1996" |
| Number of employees | Details list | Not shown | "More than 5" |
| Reviews | `data-reviews` JSON | 3: text, stars, author, ISO date, source | None |

- The **Details list** is a variable set of label/value pairs, so we parse it as pairs, not fixed positions.
- **NY profiles** may show no license number, since New York has no statewide roofing licence. To check on the first full scrape.
- **Review authors:** the JSON carries the reviewer's full name, but the page shows only initials. We store initials only.

## Ingest → parse → dedupe → database (proposed, to approve)

```text
fetch (headless browser) → raw files + snapshot rows → parse (pure functions on saved files)
  → validate (Pydantic) → dedupe + upsert (database) → run summary
```

1. **Fetch the listing (decided: search API over plain HTTP).**
   - One POST to the search API, with the same query the page sends: the 25-mile filter, GAF's 10013 centre, the "recommended" flag and `numberOfResults=100`.
   - Save `listing.json`, and check that the result count equals `totalCount` (85).
   - The public search key and org ID live in `.env`, not in code. If GAF rotates the key, the fresh one sits in the page's `data-coveo` attribute.
2. **Fetch the profiles (decided: the Playwright Python library, run by the pipeline).**
   - Headless Chrome, one page at a time, with a 1–2 s pause; save the server's raw HTML per contractor ID.
   - The Playwright MCP stays a tool for exploring pages and debugging selectors, not part of the pipeline.
   - One retry per page, then record it as failed and carry on.
   - About 85 × 3 s ≈ 5 min.
3. **Parse** the saved files with pure functions, so we can re-parse as often as we like without hitting GAF again. These double as unit-test fixtures.
   - The listing JSON gives the listing fields.
   - Each profile gives the schema.org block, `data-contractor`, `data-reviews`, the Details pairs, the website link and the `tel:` link.
   - Everything merges into one typed `ContractorRecord`.
4. **Derived in code, no LLM:**
   - `in_business_since` (a year)
   - `has_website`, `has_about`, `has_logo`
   - `latest_review_at`
   - `commercial_certified`
   - `presidents_club_stars`
   - the `specialties` list
5. **When sources disagree,** the listing wins for name, business phone, rating, review count and distance; the profile adds the rest. The page phone is stored as `display_phone` only when it differs.
6. **Dedupe and load.**
   - Upsert contractors on the GAF ID. Upsert reviews on (contractor, published time, text hash).
   - A check flags, but doesn't merge, two IDs that share a phone, a Google place ID, or a normalised name plus ZIP. That's zero today.
   - Running the pipeline twice must still leave 85 rows.
7. **Run summary:** listed / fetched / parsed / failed counts, plus the exact query (URL, centre, radius, recommended flag).

**Alternatives considered for the listing:**
- **Replay the search inside a browser.** It reads the key from the page at run time, but adds a browser to a step that doesn't need one. Kept as the fallback if plain HTTP gets blocked.
- **Click through the 9 HTML listing pages.** Slower and more fragile. Rejected.

**Schema (P0):**
| Table | Columns |
|---|---|
| `runs` | id, started_at, finished_at, query (JSON), status, listed, fetched, parsed, failed |
| `raw_snapshots` | id, run_id, kind (listing / profile), contractor_id, url, fetched_at, http_status, sha256, path |
| `contractors` | gaf_id (PK), name, dba, profile_url, street, city, state, postal_code, lat, lng, distance_mi, phone, display_phone, website, license_number, in_business_since, employees_band, about, rating, review_count, presidents_club_stars, residential_certs, commercial_certs, commercial_technologies, specialties (JSON lists), google_place_id, logo_url, gaf_rank, listing_snapshot_id, profile_snapshot_id, first_seen_run, last_seen_run, updated_at |
| `reviews` | id, gaf_id (FK), published_at, rating, text, author_initials, source; unique on (gaf_id, published_at, text hash) |
| *next phase* | `scores`, `insights` |

- **Raw files** live on disk under `data/raw/<run_id>/`, which git ignores; the database keeps the path and hash.
- **Field provenance:** each contractor row points at the two snapshots it came from.

**Database options:**
- **Postgres with pgvector, in Docker (my pick).** Docker is running on this machine. One database for the P0 tables and, later, the P1 chat embeddings; JSON columns for the lists; the closest to production. About 10 minutes to set up.
- **SQLite.** No setup, and plenty for 85 rows. But a weaker production story, and embeddings would need an extension.
- **Either way:** SQLAlchemy 2 models, with tables created from the models for P0. Proper migrations come with the parked NFRs.

**Tests:**
- Parser tests on the saved listing and the two saved profiles (one rich, one sparse), with no network.
- A load test: run twice, still 85 contractors and no duplicate reviews.

## Decided
- **P0 uses the 85** the brief's link shows, all Master Elite. **P1 widens to all 426** in the same circle by dropping the "recommended" flag (one setting), which makes tier a real signal.
