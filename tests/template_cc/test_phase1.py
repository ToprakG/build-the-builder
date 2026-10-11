"""Phase 1: io, trace, llm adapter, policy schema, extract, policy_check."""

import copy
import json

import pytest

from builder.template_cc import extract, io, llm, policy_check
from builder.template_cc.trace import NullTrace, Trace
from tests.template_cc.conftest import FIX, REPO, load, tb_policies


# --- io --------------------------------------------------------------------------------------------------------------

def test_split_inputs_finds_the_api_by_its_openapi_key(tmp_path):
    a = tmp_path / "mail.eml"
    a.write_text("from: bank\nrules")
    b = tmp_path / "codes.json"
    b.write_text('{"codes": [1]}')
    api = tmp_path / "x.txt"
    api.write_text('{"openapi": "3.0.0", "paths": {}}')
    briefs, tool = io.split_inputs([a, api, b])
    assert tool == api and briefs == [a, b]
    with pytest.raises(io.InputError):
        io.split_inputs([a, b])


def test_slot_and_brief_order(tmp_path):
    d = tmp_path / "f9-sample"
    d.mkdir()
    p = d / "brief.md"
    p.write_text("one")
    assert io.slot_name([p]) == "tcc-f9-sample"
    q = tmp_path / "memo.txt"
    q.write_text("two")
    briefs = io.read_briefs([p, q])
    assert [b.name for b in briefs] == ["brief-1.md", "brief-2.txt"]
    text = io.brief_text(briefs)
    assert text.index("one") < text.index("two") and "override" in text


def test_models_from_env_and_file(tmp_path):
    f = tmp_path / "m.json"
    f.write_text(json.dumps({"judge_threshold": 0.7, "extract": "openai:x"}))
    m = io.Models.load(str(f), env={"TCC_ANALYZE_MODEL": "anthropic:y", "DECISION_MODEL": "openai:z"})
    assert (m.extract, m.analyze, m.judge, m.judge_threshold) == ("openai:x", "anthropic:y", "openai:z", 0.7)


def test_parse_args_defaults():
    a = io.parse_args(["b.md", "t.json"])
    assert (a.save, a.max_minutes, a.audit, a.self_test, a.max_iters, a.close_form) == ("freya", 25.0, "on", "none", 3, "lifecycle")


# --- trace -----------------------------------------------------------------------------------------------------------

def test_trace_records_model_calls_and_totals(tmp_path):
    t = Trace(tmp_path / "tr", {"slot": "x"})
    t.model_call("anthropic:m", "extract pass 1", 100, 20, 0.01, 1.5)
    t.check("lint", True, "0 errors")
    t.freya("save_workflow", {"workflow_id": "w"})
    t.close("ok")
    txt = (tmp_path / "tr" / "trace.txt").read_text()
    assert "model call anthropic:m: extract pass 1" in txt and "check lint: ok" in txt and "freya save_workflow" in txt
    assert "status=ok" in txt and "tokens_in=100" in txt
    events = [json.loads(l) for l in (tmp_path / "tr" / "events.jsonl").read_text().splitlines()]
    assert events[0]["type"] == "start" and events[-1]["type"] == "end"


# --- llm adapter -----------------------------------------------------------------------------------------------------

def test_split_model():
    assert llm.split_model("openai:gpt-x") == ("openai", "gpt-x")
    assert llm.split_model("claude-opus-5-5") == ("anthropic", "claude-opus-5-5")
    assert llm.split_model("gpt-x") == ("openai", "gpt-x")


def test_call_llm_anthropic_json_and_trace(monkeypatch):
    seen = {}

    def fake(url, headers, payload, timeout):
        seen.update(url=url, payload=payload)
        return {"content": [{"type": "text", "text": "```json\n{\"a\": 1}\n```"}], "usage": {"input_tokens": 7, "output_tokens": 3}}

    monkeypatch.setattr(llm, "TRANSPORT", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    tr = NullTrace()
    out = llm.call_llm("sys", "user", "anthropic:claude-x", json_schema={"type": "object", "required": ["a"]}, trace=tr, purpose="unit")
    assert out == {"a": 1} and seen["url"] == llm.ANTHROPIC_URL and "JSON Schema" in seen["payload"]["system"]
    assert tr.model_calls == 1 and tr.tokens_in == 7


def test_call_llm_retries_bad_json_then_gives_up(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "TRANSPORT", lambda *a: calls.append(1) or {"choices": [{"message": {"content": "no json"}}]})
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    with pytest.raises(ValueError):
        llm.call_llm("s", "u", "openai:m", json_schema={"type": "object"}, attempts=2)
    assert len(calls) == 2


def test_call_llm_respects_deadline(monkeypatch):
    monkeypatch.setattr(llm, "TRANSPORT", lambda *a: pytest.fail("must not call"))
    with pytest.raises(llm.OutOfTime):
        llm.call_llm("s", "u", "openai:m", deadline=0)


def test_parse_json_tolerates_prose():
    assert llm.parse_json("Here: {\"x\": [1]} done") == {"x": [1]}


# --- schema and policy_check -----------------------------------------------------------------------------------------

def test_schema_accepts_every_testbed_config_and_the_fixture(fx_policy):
    assert policy_check.validate_schema(fx_policy) == []
    for pid, policy, _ in tb_policies():
        assert policy_check.validate_schema(policy) == [], pid


def test_schema_has_only_keys_and_types():
    text = (REPO / "builder" / "template_cc" / "policy.schema.json").read_text()
    for cfg in REPO.joinpath("testbed").rglob("config.json"):
        bank = load(cfg)["bank"]
        assert bank not in text


def test_policy_check_clean_on_fixture(fx_policy, fx_api, fx_brief):
    assert policy_check.check(fx_policy, fx_api, fx_brief) == []


@pytest.mark.parametrize("mutate, path_part", [
    (lambda p: p["queue_map"].__setitem__("human", "desk_unknown"), "/queue_map/human"),
    (lambda p: p["outcome_map"].__setitem__("promise", ["kept_maybe", None]), "/outcome_map/promise"),
    (lambda p: p["bands"][1].__setitem__("min_days", 15), "/bands/1"),
    (lambda p: p["_source"].__setitem__("/bank", "a sentence nobody wrote"), "/_source//bank"),
    (lambda p: p["holidays"].append("2031-02-30"), "/holidays/2"),
    (lambda p: p["callback"].__setitem__("end", "08:00"), "/callback"),
    (lambda p: p["identity"]["questions"].append("shoe_size"), "/identity/questions/2"),
    (lambda p: p["tools"]["create_promise"].__setitem__("amount", "amount_major"), "/tools/create_promise/amount"),
    (lambda p: p["note_map"].__setitem__("paid", "nx_gone"), "/note_map/paid"),
])
def test_policy_check_catches_each_seeded_error(fx_policy, fx_api, fx_brief, mutate, path_part):
    p = copy.deepcopy(fx_policy)
    mutate(p)
    paths = [v["path"] for v in policy_check.check(p, fx_api, fx_brief)]
    assert any(x.startswith(path_part) for x in paths), paths


def test_quote_normalisation():
    brief = "**Callbacks**: only the *next* business day,\n 09:30 to 16:30. Say “Is this {title}?”"
    assert policy_check.quote_in("only the next business day, 09:30 to 16:30", brief)
    assert policy_check.quote_in('Say "Is this {honorific}?"', brief)
    assert not policy_check.quote_in("the previous business day", brief)


def test_repair_applies_conservative_defaults(fx_policy, fx_api, fx_brief):
    p = copy.deepcopy(fx_policy)
    p["bands"][1]["min_days"] = 15
    p["queue_map"]["human"] = "nope"
    p["holidays"].append("bad")
    fixed, log = policy_check.repair(p, policy_check.check(p, fx_api, fx_brief), fx_api)
    assert fixed["queue_map"]["human"] is None and fixed["bands"] == [policy_check.DEFAULT_BAND]
    assert "bad" not in fixed["holidays"] and log


def test_complete_adds_extension_defaults(fx_api):
    p = policy_check.complete({"address": "{honorific} {family_name}", "sentences": {}, "identity": {"questions": ["birth_date"]}}, fx_api)
    assert {"honorific", "family_name", "customer_id", "now"} <= set(p["call_variables"])
    assert set(policy_check.LINE_KEYS) <= set(p["lines"])
    assert p["identity"]["kinds"]["birth_date"] == "date" and p["weekday_words"]["next"] == "following_week"


# --- extract (mocked model) ------------------------------------------------------------------------------------------

def test_extract_two_passes_and_one_fix_round(fx_api, fx_brief):
    good = load(FIX / "invented.policy.json")
    bad = copy.deepcopy(good)
    bad["queue_map"]["human"] = "desk_unknown"  # enum not in the API
    bad["bands"][1]["min_days"] = 10  # overlapping bands
    bad["_source"]["/bank"] = "invented quote not in brief"  # absent quote
    bad["holidays"].append("2031-13-01")  # bad date
    calls = []

    def fake_llm(system, user, model, json_schema=None, **kw):
        calls.append(kw["purpose"])
        if kw["purpose"].startswith("extract fix"):
            for needle in ("/queue_map/human", "/bands/1", "/_source//bank", "/holidays/2"):
                assert needle in user
            return copy.deepcopy(good)
        return copy.deepcopy(bad)

    tr = NullTrace()
    res = extract.extract(fx_brief, fx_api, fake_llm, "anthropic:x", tr)
    assert [c.split(":")[0] for c in calls] == ["extract reading A", "extract reading B", "extract pass 2", "extract fix round"]
    assert policy_check.check(res["policy"], fx_api, fx_brief) == []
    assert len(res["violations"]) >= 4 and res["repairs"] == []


def test_extract_falls_back_to_defaults_when_fix_fails(fx_api, fx_brief):
    bad = load(FIX / "invented.policy.json")
    bad["queue_map"]["human"] = "desk_unknown"
    res = extract.extract(fx_brief, fx_api, lambda *a, **k: copy.deepcopy(bad), "m", NullTrace())
    assert res["policy"]["queue_map"]["human"] is None and res["repairs"]


def test_call_llm_openrouter_provider(monkeypatch):
    seen = {}

    def fake(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, payload=payload)
        return {"choices": [{"message": {"content": '{"a": 2}'}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}

    monkeypatch.setattr(llm, "TRANSPORT", fake)
    monkeypatch.setenv("OPENROUTER_API_KEY", "rk")
    out = llm.call_llm("s", "u", "openrouter:anthropic/claude-opus-5.5", json_schema={"type": "object", "required": ["a"]})
    assert out == {"a": 2} and seen["url"] == llm.OPENROUTER_URL and seen["headers"]["Authorization"] == "Bearer rk"
    assert seen["payload"]["model"] == "anthropic/claude-opus-5.5" and seen["payload"]["temperature"] == 0.2


def test_openai_reasoning_models_get_max_completion_tokens(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "TRANSPORT", lambda u, h, p, t: seen.update(p=p) or {"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    llm.call_llm("s", "u", "openai:gpt-5.5")
    assert "temperature" not in seen["p"] and "max_completion_tokens" in seen["p"] and "max_tokens" not in seen["p"]
