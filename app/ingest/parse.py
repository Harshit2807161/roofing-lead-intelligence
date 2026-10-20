"""Parse step: pure functions from saved raw files to typed records. No network, no database.

Listing JSON gives the structured fields; each profile page adds what only it has (About text,
website, license, years in business, reviews). The profile embeds most of this as JSON (a
schema.org block and data-* attributes), so we read JSON rather than scrape visible text.
"""

import json
import re
from datetime import datetime

from bs4 import BeautifulSoup

from app.ingest.schemas import ContractorRecord, ReviewRecord

PLACEHOLDER_LOGO = "no_profile_image"


def digits(phone: str | None) -> str:
    """Phone digits without the US country code, for comparing numbers written differently."""
    d = re.sub(r"\D", "", phone or "")
    return d[1:] if len(d) == 11 and d.startswith("1") else d


def as_list(value) -> list[str]:
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def blank_to_none(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def initials(name: str | None) -> str | None:
    """'Elena Go' -> 'E.G', the way GAF's page shows reviewers."""
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return None
    first, last = parts[0], parts[-1]
    return f"{first[0]}.{last[0]}".upper() if len(parts) > 1 else first[0].upper()


def parse_listing(listing: dict) -> list[dict]:
    """One dict of listing fields per result, in GAF's order. Merged with the profile later."""
    rows = []
    for rank, result in enumerate(listing["results"], 1):
        raw = result["raw"]
        reviews = int(raw.get("gaf_number_of_reviews") or 0)
        name = raw.get("title") or raw.get("gaf_navigation_title")
        dba = blank_to_none(raw.get("gaf_contractor_dba"))
        logo = raw.get("gaf_featured_image_src")
        rows.append(
            {
                "gaf_id": str(raw["gaf_contractor_id"]),
                "name": name,
                "dba": dba if dba and dba != name else None,
                "profile_url": result.get("uri") or raw.get("uri"),
                "gaf_rank": rank,
                "city": raw.get("gaf_f_city"),
                "state": raw.get("gaf_f_state_code"),
                "postal_code": raw.get("gaf_postal_code"),
                "lat": raw["gaf_latitude"],
                "lng": raw["gaf_longitude"],
                "distance_mi": round(float(raw["distanceinmiles"]), 2),
                "phone": blank_to_none(raw.get("gaf_phone")),
                # GAF stores "no reviews" as a 0.0 rating; that's missing, not terrible.
                "rating": float(raw["gaf_rating"]) if reviews and raw.get("gaf_rating") else None,
                "review_count": reviews,
                "presidents_club_stars": int(
                    raw.get("gaf_residential_presidents_club_stars_count") or 0
                ),
                "residential_certs": as_list(
                    raw.get("gaf_f_contractor_certifications_and_awards_residential")
                ),
                "commercial_certs": as_list(
                    raw.get("gaf_f_contractor_certifications_and_awards_global_search_commercial")
                ),
                "commercial_technologies": as_list(
                    raw.get("gaf_f_contractor_technologies_commercial")
                ),
                "specialties": as_list(raw.get("gaf_f_contractor_specialties_residential")),
                "google_place_id": blank_to_none(raw.get("gaf_google_place_id")),
                "logo_url": None if not logo or PLACEHOLDER_LOGO in logo else logo,
                "_address_full": raw.get("gaf_address"),
            }
        )
    return rows


def _json_attr(soup: BeautifulSoup, attr: str):
    el = soup.find(attrs={attr: True})
    if el is None:
        return None
    try:
        return json.loads(el[attr])
    except (ValueError, TypeError):
        return None


def _local_business(soup: BeautifulSoup) -> dict:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "LocalBusiness":
            return data
    return {}


def parse_profile(html: str) -> dict:
    """Fields only the profile page has. Missing sections come back as None or empty."""
    soup = BeautifulSoup(html, "html.parser")
    business = _local_business(soup)
    address = business.get("address") or {}

    details = {}
    for info in soup.select(".contractor-details__info"):
        label, value = info.find("h3"), info.find("p")
        if label and value:
            details[label.get_text(strip=True)] = value.get_text(" ", strip=True)
    since = re.search(r"(18|19|20)\d{2}", details.get("Years in Business", ""))

    website = next(
        (a.get("href") for a in soup.find_all("a", href=True) if "Visit Website" in a.get_text()),
        None,
    )
    tel = next((a["href"] for a in soup.select('a[href^="tel:"]')), None)

    reviews = []
    for r in _json_attr(soup, "data-reviews") or []:
        text, published = blank_to_none(r.get("Text")), r.get("PublishTime")
        if not text or not published:
            continue
        reviews.append(
            ReviewRecord(
                published_at=datetime.fromisoformat(published.replace("Z", "+00:00")),
                rating=r.get("Rating") or None,
                text=text,
                author_initials=initials(r.get("AuthorName")),
                source=r.get("ReviewSource"),
            )
        )

    return {
        "street": blank_to_none(address.get("streetAddress")),
        "about": blank_to_none(business.get("description")),
        "business_phone": blank_to_none(business.get("telephone")),
        "tel_link": tel.removeprefix("tel:") if tel else None,
        "website": blank_to_none(website),
        "license_number": blank_to_none(details.get("State License Number")),
        "in_business_since": int(since.group(0)) if since else None,
        "employees_band": blank_to_none(details.get("Number of Employees")),
        "details_contractor_id": blank_to_none(details.get("Contractor ID")),
        "reviews": reviews,
    }


def street_from_address(listing_row: dict) -> str | None:
    """'20 Parish Dr Wayne, NJ 07470' -> '20 Parish Dr' (the listing's one-line address)."""
    full = listing_row.get("_address_full") or ""
    street = full.split(f" {listing_row.get('city')},")[0] if full else ""
    return street.strip() or None


def merge(listing_row: dict, profile: dict | None) -> ContractorRecord:
    """Listing wins for name, phone, rating, review count and distance; the profile adds the rest."""
    row = {k: v for k, v in listing_row.items() if not k.startswith("_")}
    if profile is None:
        # No profile page (fetch failed): keep the listing fields.
        return ContractorRecord(
            **row, street=street_from_address(listing_row), profile_fetched=False
        )

    if profile.get("details_contractor_id") not in (None, row["gaf_id"]):
        raise ValueError(f"profile is for {profile['details_contractor_id']}, not {row['gaf_id']}")
    tel = profile.get("tel_link")
    return ContractorRecord(
        **row,
        # Some profiles leave the street out of their schema.org block; the listing still has it.
        street=profile["street"] or street_from_address(listing_row),
        about=profile["about"],
        website=profile["website"],
        license_number=profile["license_number"],
        in_business_since=profile["in_business_since"],
        employees_band=profile["employees_band"],
        display_phone=tel if tel and digits(tel) != digits(row["phone"]) else None,
        reviews=profile["reviews"],
        profile_fetched=True,
    )
