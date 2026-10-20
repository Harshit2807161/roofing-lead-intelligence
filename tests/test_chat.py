"""Prep chat (F8) loop, offline: a scripted fake model drives the real tools on the real data.
Checks the code half: budgets, every call_id answered, errors as data, citation repair."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.chat.agent import Budgets, bad_citations, cited, run_chat
from app.chat.tools import Executor
from app.db import get_engine, session_factory

ALLIED = "1116285"


def call(name: str, args: dict, call_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call", name=name, arguments=json.dumps(args), call_id=call_id
    )


def reply(text_: str) -> SimpleNamespace:
    return SimpleNamespace(type="message", text=text_)


class ScriptedModel:
    """Returns the scripted outputs in order and records what the loop sent."""

    def __init__(self, script: list[list]):
        self.script, self.sent = list(script), []

    def create(self, **kw):
        self.sent.append(kw)
        out = self.script.pop(0) if self.script else [reply("final")]
        text_ = " ".join(o.text for o in out if o.type == "message")
        return SimpleNamespace(id=f"resp_{len(self.sent)}", output=out, output_text=text_)


@pytest.fixture(scope="module")
def session():
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("select count(*) from scores"))
    except OperationalError:
        pytest.skip("Postgres not running")
    with session_factory(engine)() as s:
        yield s


def test_citation_parsing():
    answer = "Ask for Jonathan [1116285:review_3], 4.9★ [1116285:rating] [1116285:rating]"
    assert cited(answer) == [("1116285", "review_3"), ("1116285", "rating")]
    assert bad_citations(answer, {("1116285", "rating")}) == ["[1116285:review_3]"]


@pytest.mark.db
def test_tool_errors_come_back_as_data_with_hints(session):
    ex = Executor(session)
    for name, args, hint in [("lead_get", {"gaf_id": "nope"}, "leads_search"),
                             ("leads_search", {"tag": "bogus"}, "storm"),
                             ("leads_search", {"limit": 99}, "Fix the arguments"),
                             ("send_email", {}, "lead_get")]:  # fmt: skip
        out = json.loads(ex.run(name, json.dumps(args)))
        assert out["ok"] is False and hint in out["hint"], out


@pytest.mark.db
def test_grounded_answer_passes_without_repair(session):
    model = ScriptedModel([
        [call("lead_get", {"gaf_id": ALLIED}, "c1")],
        [reply(f"Ask for Jonathan [{ALLIED}:review_3]; 4.9★ [{ALLIED}:rating].")],
    ])  # fmt: skip
    r = run_chat(session, "Who do I ask for?", gaf_id=ALLIED, model=model)
    assert r.stop_reason == "answer" and r.unverified == [] and not r.retried
    assert model.sent[1]["previous_response_id"] == "resp_1"
    assert model.sent[1]["input"][0]["call_id"] == "c1"  # the tool output went back
    assert f"gaf_id={ALLIED}" in model.sent[0]["instructions"]  # pinned lead is in context


@pytest.mark.db
def test_citation_the_tools_never_returned_triggers_one_repair(session):
    model = ScriptedModel([
        [call("lead_get", {"gaf_id": ALLIED}, "c1")],
        [reply(f"Revenue is $9M [{ALLIED}:revenue].")],
        [reply(f"That isn't in our data [{ALLIED}:about].")],
    ])  # fmt: skip
    r = run_chat(session, "Revenue?", model=model)
    assert r.retried and r.unverified == [] and "isn't in our data" in r.answer
    assert "revenue" in model.sent[-1]["input"][-1]["content"]


@pytest.mark.db
def test_tool_budget_forces_a_final_answer_and_answers_every_call(session):
    model = ScriptedModel([
        [call("text_search", {"query": "storm"}, "a"), call("text_search", {"query": "hail"}, "b"),
         call("text_search", {"query": "insurance"}, "c")],
        [reply("Partial answer.")],
    ])  # fmt: skip
    r = run_chat(session, "storm?", model=model, budgets=Budgets(max_tool_calls=2))
    assert r.stop_reason == "max_tool_calls" and len(r.calls) == 2
    outputs = [i for i in model.sent[1]["input"] if i.get("type") == "function_call_output"]
    assert [o["call_id"] for o in outputs] == [
        "a",
        "b",
        "c",
    ]  # the refused one still gets an output
    assert "budget" in json.loads(outputs[2]["output"])["error"].lower()
    assert model.sent[1]["tool_choice"] == "none"


@pytest.mark.db
def test_step_budget_stops_a_model_that_keeps_calling_tools(session):
    loop = [[call("leads_search", {"limit": 1}, f"c{i}")] for i in range(10)]
    model = ScriptedModel([*loop[:3], [reply("Done.")]])
    r = run_chat(session, "loop", model=model, budgets=Budgets(max_steps=3, max_tool_calls=20))
    assert r.stop_reason == "max_steps" and r.steps == 4  # 3 tool steps + the forced final


def test_history_is_capped():
    model = ScriptedModel([[reply("ok")]])
    history = [{"role": "user", "content": f"q{i}"} for i in range(20)]

    class NoDB:
        pass

    run_chat(NoDB(), "now", history=history, model=model)
    sent = model.sent[0]["input"]
    assert len(sent) == 9 and sent[-1]["content"] == "now" and sent[0]["content"] == "q12"
