"""Settings from the environment (.env). No secrets or GAF internals are hard-coded in code."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://leads:leads@localhost:5433/leads"
    raw_dir: Path = ROOT / "data" / "raw"

    # The brief's search. P0 scope is this one circle; it lives here so it isn't hard-coded.
    search_postal_code: str = "10013"
    search_radius_mi: int = 25
    # GAF geocodes 10013 to this point and measures distance from it (seen in the page's own query).
    search_center_lat: float = 40.7217861
    search_center_lng: float = -74.0094471
    # True = the 85 contractors the page shows. False = all 426 in the circle (P1, F13).
    search_recommended_only: bool = True

    # GAF's public search API (Coveo). The key is the public search key embedded in gaf.com.
    coveo_org_id: str = "gafmaterialscorporationproduction3yalqk12"
    coveo_api_key: str = ""
    coveo_pipeline: str = "prod-gaf-recommended-residential-contractors"

    # Profile fetching: one page at a time, with a pause, so we stay polite and unblocked.
    fetch_delay_s: float = 1.5
    fetch_timeout_s: float = 30.0
    fetch_headless: bool = True
    browser_channel: str = "chrome"  # use the installed Chrome instead of downloading Chromium
    browser_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
    )

    @property
    def listing_page_url(self) -> str:
        return (
            "https://www.gaf.com/en-us/roofing-contractors/residential"
            f"?distance={self.search_radius_mi}&postalCode={self.search_postal_code}&countryCode=us"
        )

    @property
    def coveo_search_url(self) -> str:
        return (
            f"https://{self.coveo_org_id}.org.coveo.com/rest/search/v2"
            f"?organizationId={self.coveo_org_id}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
