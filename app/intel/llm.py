"""One wrapper for every LLM call: structured output, timeout, transport retries, JSONL trace."""

import json
import time
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel

from app.config import ROOT, get_settings

TRACE_FILE = ROOT / "traces" / "llm.jsonl"


@lru_cache
def client() -> OpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set (see .env.example)")
    # The SDK retries transport errors (timeouts, 429, 5xx); semantic failures are ours to handle.
    return OpenAI(api_key=settings.openai_api_key, max_retries=3, timeout=90)


def trace(span: dict, path: Path = TRACE_FILE) -> None:
    path.parent.mkdir(exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(span, default=str) + "\n")


def structured[T: BaseModel](
    *,
    model: str,
    instructions: str,
    user_input: str,
    schema: type[T],
    effort: str = "low",
    step: str,
    gaf_id: str,
) -> T:
    """Call the Responses API with a strict JSON schema and return the parsed Pydantic object."""
    span = {
        "id": uuid.uuid4().hex,
        "step": step,
        "gaf_id": gaf_id,
        "model": model,
        "at": datetime.now(UTC),
    }
    started = time.perf_counter()
    try:
        resp = client().responses.parse(
            model=model,
            instructions=instructions,
            input=user_input,
            text_format=schema,
            reasoning={"effort": effort},
        )
        usage = resp.usage
        span.update(
            ok=True,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )
        if resp.output_parsed is None:
            raise ValueError("model returned no parsable output")
        return resp.output_parsed
    except Exception as exc:
        span.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        span["latency_s"] = round(time.perf_counter() - started, 2)
        trace(span)
