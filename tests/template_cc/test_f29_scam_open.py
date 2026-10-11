"""F29 (opt-in): a scam question in the first reply is answered with the brief's line before the first identity question."""
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import call_vars, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
BASE = "f1,f2,f3,f4,f7,f8,f9,f11,f12,f13,f18"
NOW = "2031-03-05T10:00"


def build(features, mutate=None):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    if mutate:
        mutate(pol)
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features=features)
    return wf, pol, api


def names(wf):
    return [e["name"] for e in wf["nodes"]["opening"]["extraction_schema"]]


def test_off_by_default_and_on_with_the_short_id():
    wf, _, _ = build(BASE)
    assert "scam_asked" not in names(wf)
    wf, pol, api = build(BASE + ",f29")
    assert "scam_asked" in names(wf)
    assert compiler.lint(wf, pol, api)["errors"] == []


def test_the_scam_line_is_said_before_the_first_identity_question():
    wf, pol, _ = build(BASE + ",f29")
    v = run_code(wf, "setup", dict(call_vars(pol, NOW), scam_asked="yes", who1="customer"))
    scam = pol["lines"]["scam"]["text"]
    assert v["id_say"].startswith(scam), v["id_say"]
    v = run_code(wf, "setup", dict(call_vars(pol, NOW), scam_asked="no", who1="customer"))
    assert scam not in v["id_say"]


def test_without_f1_or_the_ai_line_nothing_changes():
    ref, _, _ = build("f2,f3")
    wf, _, _ = build("f2,f3,f29")  # needs f1_id_complete: the identity questions are spoken by the system
    assert json.dumps(wf, sort_keys=True) == json.dumps(ref, sort_keys=True)
