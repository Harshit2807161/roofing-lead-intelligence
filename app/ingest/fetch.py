"""Fetch step: GAF listing via its search API, profiles via a real browser. Writes raw files only.

Output layout for one run:
    data/raw/<run_id>/listing.json
    data/raw/<run_id>/profiles/<gaf_id>.html
    data/raw/<run_id>/manifest.json   # one entry per file: url, status, fetched_at, sha256, path
"""

import hashlib
import json
import logging
import random
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from playwright.sync_api import Page, sync_playwright

from app.config import Settings

log = logging.getLogger(__name__)

# Heavy assets we never need for the raw HTML; skipping them keeps the fetch light.
BLOCKED_RESOURCES = {"image", "media", "font"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def listing_query(settings: Settings, first_result: int, page_size: int) -> dict:
    """The same query gaf.com's listing page sends, minus analytics."""
    query = {
        "locale": "en-US",
        "q": "",
        "aq": f"@distanceinmiles <= {settings.search_radius_mi} AND @gaf_f_country_code = USA",
        "cq": 'NOT @gaf_content_type=="NO CONTENT TYPE FILTER"',
        "pipeline": settings.coveo_pipeline,
        "searchHub": settings.coveo_pipeline,
        "sortCriteria": "relevancy",
        "enableQuerySyntax": False,
        "firstResult": first_result,
        "numberOfResults": page_size,
        "queryFunctions": [
            {
                "fieldName": "@distanceinmiles",
                "function": (
                    "dist(@gaf_latitude, @gaf_longitude, "
                    f"{settings.search_center_lat}, {settings.search_center_lng})*0.000621371"
                ),
            }
        ],
    }
    if settings.search_recommended_only:
        # This context is what narrows the circle to the 85 the page shows (426 without it).
        query["context"] = {"sortingStrategy": "gafrecommended-initial"}
    return query


def fetch_listing(settings: Settings, page_size: int = 100) -> dict:
    """All listing results in as few calls as possible; checks we got every result GAF counted."""
    if not settings.coveo_api_key:
        raise RuntimeError("COVEO_API_KEY is not set (see .env.example)")
    headers = {
        "authorization": f"Bearer {settings.coveo_api_key}",
        "content-type": "application/json",
    }
    results: list[dict] = []
    total = None
    with httpx.Client(timeout=settings.fetch_timeout_s) as client:
        while total is None or len(results) < total:
            body = listing_query(settings, first_result=len(results), page_size=page_size)
            resp = client.post(settings.coveo_search_url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            total = data["totalCount"]
            if not data["results"]:
                break
            results.extend(data["results"])
    if len(results) != total:
        raise RuntimeError(f"listing incomplete: got {len(results)} of {total}")
    return {"totalCount": total, "query": listing_query(settings, 0, page_size), "results": results}


def fetch_profile_html(page: Page, url: str, settings: Settings) -> tuple[int, str]:
    """The server's raw HTML for one profile (not the JS-modified DOM)."""
    resp = page.goto(url, wait_until="domcontentloaded", timeout=settings.fetch_timeout_s * 1000)
    if resp is None:
        return 0, ""
    return resp.status, resp.text()


def run_fetch(settings: Settings, run_id: str, limit: int | None = None) -> Path:
    """Fetch listing + every profile into data/raw/<run_id>/. Failures are recorded, not raised."""
    run_dir = settings.raw_dir / run_id
    (run_dir / "profiles").mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []

    listing = fetch_listing(settings)
    listing_bytes = json.dumps(listing, ensure_ascii=False).encode()
    (run_dir / "listing.json").write_bytes(listing_bytes)
    manifest.append(
        {
            "kind": "listing",
            "gaf_id": None,
            "url": settings.coveo_search_url,
            "http_status": 200,
            "fetched_at": now_iso(),
            "sha256": sha256(listing_bytes),
            "path": "listing.json",
        }
    )
    log.info("listing: %d contractors", listing["totalCount"])

    targets = [(r["raw"]["gaf_contractor_id"], r["uri"]) for r in listing["results"]][:limit]
    with sync_playwright() as pw:
        # Headless Chrome announces itself as "HeadlessChrome" and gets a 403 from GAF's bot
        # protection; the same browser with its normal user agent loads the public page fine.
        browser = pw.chromium.launch(
            channel=settings.browser_channel,
            headless=settings.fetch_headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.new_context(user_agent=settings.browser_user_agent).new_page()
        page.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type in BLOCKED_RESOURCES
                else route.continue_()
            ),
        )
        for i, (gaf_id, url) in enumerate(targets, 1):
            status, html = 0, ""
            for attempt in (1, 2):  # one retry, then record the failure and move on
                try:
                    status, html = fetch_profile_html(page, url, settings)
                except Exception as exc:  # timeouts, navigation errors
                    log.warning("profile %s attempt %d failed: %s", gaf_id, attempt, exc)
                    status, html = 0, ""
                if status == 200 and html:
                    break
                time.sleep(settings.fetch_delay_s * 3)
            entry = {
                "kind": "profile",
                "gaf_id": gaf_id,
                "url": url,
                "http_status": status,
                "fetched_at": now_iso(),
                "sha256": None,
                "path": None,
            }
            if status == 200 and html:
                data = html.encode()
                rel = f"profiles/{gaf_id}.html"
                (run_dir / rel).write_bytes(data)
                entry.update(sha256=sha256(data), path=rel)
            manifest.append(entry)
            log.info("profile %d/%d %s -> %s", i, len(targets), gaf_id, status)
            time.sleep(settings.fetch_delay_s + random.uniform(0, 0.5))
        browser.close()

    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return run_dir
