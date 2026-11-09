"""Shared FastAPI dependencies: a sync DB session per request, and the Jinja environment."""

from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache

from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker

from app.config import ROOT
from app.db import get_engine, session_factory

TEMPLATES_DIR = ROOT / "app" / "templates"
STATIC_DIR = ROOT / "app" / "static"


@lru_cache
def _sessions() -> sessionmaker[Session]:
    return session_factory(get_engine())


def get_session() -> Iterator[Session]:
    """One short session per request. Pages only read; app/web/actions.py holds the few writes."""
    with _sessions()() as session:
        yield session


def _date(value: datetime | None, fmt: str = "%b %-d, %Y") -> str:
    return value.strftime(fmt) if value else ""


def _num(value: float | None, digits: int = 1) -> str:
    """Trim trailing zeros so 18.0 reads as 18 and 4.90 as 4.9."""
    if value is None:
        return "—"
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["date"] = _date
templates.env.filters["num"] = _num
