"""F12: a short state summary in the task message of the nodes that talk to the customer."""
import json
from pathlib import Path

import pytest

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel
from builder.template_cc.compiler import runtime as R

from .helpers import call_vars, fake_products, run_code, run_post

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
NOW = "2031-03-05T10:00"


@pytest.fixture(scope="module")
def built():
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features="f1,f2,f3,f4,f9,f11,f12")
    return wf, pol, api


def test_summary_in_the_five_task_messages_and_lint(built):
    wf, pol, api = built
    for nid in ("identity", "negotiate", "negotiate_resume", "promise_confirm", "hardship"):
        assert wf["nodes"][nid]["task_message"].startswith("State:\n{{slot_summary}}"), nid
    assert "slot_summary" not in wf["nodes"]["opening"]["task_message"]
    assert compiler.lint(wf, pol, api)["errors"] == []


def test_setup_initialises_it_and_the_states_read_well(built):
    wf, pol, _ = built
    v = run_code(wf, "setup", call_vars(pol, NOW))
    s0 = v["slot_summary"]
    assert s0.count("\n") <= 3 and "identity not verified yet" in s0 and "Still needed: the answer to" in s0
    v["id_state"] = "passed"
    v["products_raw"] = fake_products(pol, 1)
    v = run_code(wf, "facts", v)
    s1 = v["slot_summary"]
    assert "credit card" in s1 and "412.5" in s1 and "an agreed amount and date" in s1
    o = run_code(wf, "promise_check", dict(v, offer_intent="proposes", offer_amount_text="three hundred", offer_date_text="Friday the seventh of March"))
    assert "three hundred" in o["slot_summary"]
    out, _ = run_post(wf, "promise_write", "create_promise", dict(o, rb_pid="p1"), {"ok": True})
    assert "300" in out["slot_summary"] and "nothing, close the call" in out["slot_summary"]


def test_helper_on_three_bare_states():
    cfg = {"identity": {"questions": ["a", "b"]}}
    assert R.slot_summary({"id_state": "pending"}, cfg).splitlines()[-1] == "Still needed: the answer to a"
    assert R.slot_summary({"id_state": "passed"}, cfg).splitlines()[-1] == "Still needed: nothing, close the call"
    assert "Agreed so far: p1 300" in R.slot_summary({"id_state": "passed", "agreed": json.dumps({"p1": "300"})}, cfg)


def test_off_means_no_prefix():
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    wf, _ = compiler.compile(pol, ApiModel.load(REPO / "tools" / "openapi.json"), features="f1,f2,f3,f4,f9,f11")
    assert "slot_summary" not in json.dumps(wf)
