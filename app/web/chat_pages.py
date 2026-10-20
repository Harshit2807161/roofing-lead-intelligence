"""Rep prep chat in the UI (F8): a panel on each lead page (pinned to that lead), a /chat page for
the whole territory, and POST /api/chat. The server is stateless: the form carries the last few
turns back and forth, and the agent caps them."""

import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from markupsafe import Markup, escape
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.chat.agent import CITE, MAX_HISTORY, run_chat
from app.models import Contractor
from app.web import data
from app.web.deps import get_session, templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
MAX_MESSAGE = 1000

LEAD_SUGGESTIONS = [
    "Prep me for a first call: who do I ask for and what do I lead with?",
    "What do customers praise or complain about?",
    "Why is this lead ranked where it is?",
]
TERRITORY_SUGGESTIONS = [
    "Which NJ commercial-certified contractors have the most reviews?",
    "Who mentions storm damage or insurance claims?",
    "Which family-owned contractors are within 15 miles?",
]


def parse_history(raw: str | None) -> list[dict[str, str]]:
    """The turns the form sent back, validated and capped; anything malformed is dropped."""
    try:
        turns = json.loads(raw or "[]")
    except ValueError:
        return []
    ok = [t for t in turns if isinstance(t, dict) and t.get("role") in ("user", "assistant")
          and isinstance(t.get("content"), str)]  # fmt: skip
    return [{"role": t["role"], "content": t["content"][:4000]} for t in ok][-MAX_HISTORY:]


def citation_map(session: Session, pairs: list[tuple[str, str]]) -> dict[str, dict[str, str]]:
    """'gaf_id:key' -> label, value and lead name, for the chips in an answer."""
    out: dict[str, dict[str, str]] = {}
    details: dict[str, dict | None] = {}
    for gaf_id, key in pairs:
        if gaf_id not in details:
            details[gaf_id] = data.lead_detail(session, gaf_id)
        d = details[gaf_id]
        if d is None:
            continue
        c = d["citations"].get(key)
        if key == "listing":
            c = {
                "label": "GAF listing",
                "value": f"#{d['rank']} · {d['city']}, {d['state']} · {d['distance_mi']} mi",
            }
        out[f"{gaf_id}:{key}"] = {"name": d["name"], "label": c["label"] if c else key,
                                  "value": c["value"] if c else "Not in stored data"}  # fmt: skip
    return out


def render_answer(answer: str, cites: dict[str, dict[str, str]]) -> Markup:
    """Escape the model's text, then allow a little formatting (bold, bullets) and turn
    [gaf_id:key] into source chips. Nothing the model writes is trusted as HTML."""

    def chip(m: re.Match) -> str:
        key = f"{m.group(1)}:{m.group(2)}"
        c = cites.get(key)
        if c is None:
            return f'<span class="cite cite-bad" title="Not returned by any tool">{key}</span>'
        short = c["name"].split()[0] if c["name"] else m.group(1)
        return (f'<a class="cite" href="/leads/{m.group(1)}" tabindex="0">{escape(short)} · '
                f'{escape(m.group(2).replace("_", " "))}<span class="cite-pop" role="tooltip">'
                f'<span class="cite-pop-label">{escape(c["name"])} · {escape(c["label"])}</span>'
                f'<span class="cite-pop-value">{escape(c["value"][:600])}</span></span></a>')  # fmt: skip

    lines, html, in_list = str(escape(answer)).splitlines(), [], False
    for line in lines:
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        line = CITE.sub(chip, line)
        bullet = re.match(r"^\s*[-•*]\s+(.*)", line)
        if bullet and not in_list:
            html.append("<ul>")
            in_list = True
        if not bullet and in_list:
            html.append("</ul>")
            in_list = False
        if bullet:
            html.append(f"<li>{bullet.group(1)}</li>")
        elif line.strip():
            html.append(f"<p>{line}</p>")
    if in_list:
        html.append("</ul>")
    return Markup("".join(html))


def thread_view(session: Session, history: list[dict[str, str]]) -> list[dict[str, Any]]:
    pairs = [p for t in history if t["role"] == "assistant" for p in CITE.findall(t["content"])]
    cites = citation_map(session, list(dict.fromkeys(pairs)))
    return [{"role": t["role"], "html": render_answer(t["content"], cites) if t["role"] == "assistant"
             else escape(t["content"])} for t in history]  # fmt: skip


def chat_context(
    session: Session, history: list[dict], gaf_id: str | None, last: Any = None
) -> dict:
    lead = session.get(Contractor, gaf_id) if gaf_id else None
    return {
        "thread": thread_view(session, history),
        "history_json": json.dumps(history),
        "gaf_id": gaf_id if lead else None,
        "lead_name": lead.name if lead else None,
        "suggestions": LEAD_SUGGESTIONS if lead else TERRITORY_SUGGESTIONS,
        "last": last,
    }


@router.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(request, "chat.html", chat_context(session, [], None))


@router.post("/chat/messages", response_class=HTMLResponse)
def chat_message(
    request: Request,
    session: SessionDep,
    message: Annotated[str, Form(max_length=MAX_MESSAGE)],
    history: Annotated[str | None, Form()] = None,
    gaf_id: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    turns = parse_history(history)
    gaf_id = (gaf_id or "").strip() or None
    result = run_chat(session, message.strip(), history=turns, gaf_id=gaf_id)
    turns = [
        *turns,
        {"role": "user", "content": message.strip()},
        {"role": "assistant", "content": result.answer},
    ]
    last = {"steps": result.steps, "stop": result.stop_reason, "unverified": result.unverified,
            "tools": [c.name for c in result.calls], "retried": result.retried}  # fmt: skip
    ctx = chat_context(session, turns[-MAX_HISTORY:], gaf_id, last)
    return templates.TemplateResponse(request, "partials/chat_box.html", ctx)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)
    history: list[dict] = []
    gaf_id: str | None = None


@router.post("/api/chat")
def api_chat(body: ChatIn, session: SessionDep) -> dict[str, Any]:
    r = run_chat(
        session, body.message, history=parse_history(json.dumps(body.history)), gaf_id=body.gaf_id
    )
    return {"answer": r.answer, "citations": [f"{g}:{k}" for g, k in r.citations],
            "unverified": r.unverified, "stop_reason": r.stop_reason, "steps": r.steps,
            "retried": r.retried, "tool_calls": [c.__dict__ for c in r.calls]}  # fmt: skip
