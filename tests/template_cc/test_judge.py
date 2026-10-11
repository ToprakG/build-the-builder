"""Phase 3: patches, judge (deterministic + mocked decision model), vendor adapter, judge_agreement."""

import copy
import json
import sys
import textwrap

import pytest

from builder.template_cc import compiler, judge_agreement, patches, zones
from builder.template_cc import judge as J
from builder.template_cc.io import Models
from builder.template_cc.trace import NullTrace
from tests.template_cc.conftest import FIX, load


@pytest.fixture
def setup(fx_api, fx_policy, fx_brief, tmp_path):
    wf, book = compiler.compile(fx_policy, fx_api)
    ctx = J.JudgeContext(brief_text=fx_brief, policy=fx_policy, workflow=wf, book=book, api=fx_api,
                         bugs={"b1": {"bug_id": "b1", "behaviour": "agent asked twice"}},
                         evidence_text={"b1": "case X rep 1 turn 4: AGENT: Could you settle this today?"},
                         facts_text="past due 412.50, minimum 137.50", case_tokens={"Imogen", "c_8812", "731.20"},
                         base_size=zones.model_text_size(book), log_path=tmp_path / "patch_log.jsonl")
    return ctx


def good_patch(**kw):
    p = {"id": "p1", "bug_ids": ["b1"], "zone": "node:negotiate/rules", "op": "append", "before": "",
         "after": "- Read every promise back together before any create_promise call.",
         "brief_quote": "read every promise back together", "rationale": "the agent recorded before the read-back"}
    p.update(kw)
    return p


class ScriptedBackend(J.DecisionBackend):
    name = "scripted"

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def ask(self, text, questions):
        self.calls.append((text, [q["name"] for q in questions]))
        return {q["name"]: J.Answer(self.answers.get(q["name"]), None, "ev") for q in questions}


PASS = {"necessary": "yes", "supported": "yes", "contradicts_brief": "no", "case_specific": "no", "redundant": "no", "right_place": "yes"}


# --- patches ---------------------------------------------------------------------------------------------------------

def test_apply_append_and_replace(setup):
    ctx = setup
    wf, book = patches.apply_one(ctx.workflow, ctx.book, good_patch())
    assert "Read every promise back together before" in wf["nodes"]["negotiate"]["task_message"]
    p2 = good_patch(id="p2", op="replace", before="before any create_promise call", after="before the first create_promise call")
    wf, book2 = patches.apply_one(wf, book, p2)
    assert "before the first create_promise call" in book2.zones["node:negotiate/rules"]["text"]
    assert "node:negotiate/rules" in patches.diff(ctx.book, book2)
    assert patches.growth(ctx.book, book2) > 0
    wf3, b3, applied, failed = patches.apply_all(ctx.workflow, ctx.book, [good_patch(), good_patch(id="bad", zone="system/core")])
    assert [p["id"] for p in applied] == ["p1"] and failed[0][0]["id"] == "bad"


# --- deterministic checks --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("patch, failing", [
    (good_patch(op="replace", before="text that is not there"), "before_once"),
    (good_patch(zone="system/core"), "zone_unlocked"),
    (good_patch(zone="tts:opening"), "zone_unlocked"),
    (good_patch(after="- " + "x" * 1300), "zone_budget"),
    (good_patch(brief_quote="a rule the bank never wrote"), "quote_in_brief"),
    (good_patch(after="- Say {{secret_total}} to the customer."), "no_unknown_vars"),
    (good_patch(after="- Offer 250 as the minimum amount."), "numbers_known"),
    (good_patch(after="- If Imogen asks again, repeat the date."), "no_case_tokens"),
    (good_patch(zone="node:opening/rules", after="- Never mention overdue items yet."), "no_forbidden_pre_verification"),
    (good_patch(zone="nope/zone"), "zone_unlocked"),
])
def test_each_deterministic_check_rejects_its_seeded_patch(setup, patch, failing):
    backend = ScriptedBackend(PASS)
    v = J.PatchJudge(backend).judge(patch, setup)
    assert not v.accepted
    assert failing in [c["name"] for c in v.checks if not c["passed"]], v.checks
    assert backend.calls == []  # no decision call on an invalid patch


def test_growth_budget(setup):
    ctx = setup
    ctx.base_size = 1000  # pretend the iteration started much smaller
    v = J.PatchJudge(ScriptedBackend(PASS)).judge(good_patch(), ctx)
    assert "growth_budget" in [c["name"] for c in v.checks if not c["passed"]]


def test_lint_break_is_rejected(setup, monkeypatch):
    monkeypatch.setattr(J.compiler, "lint", lambda wf, p, a: {"errors": [{"path": "$", "msg": "broken"}], "warnings": []})
    v = J.PatchJudge(ScriptedBackend(PASS)).judge(good_patch(), setup)
    assert not v.accepted and "lint_clean" in v.reasons[0]


def test_a_good_patch_is_accepted_and_logged(setup):
    backend = ScriptedBackend(PASS)
    v = J.PatchJudge(backend).judge(good_patch(), setup)
    assert v.accepted, v.reasons
    text, names = backend.calls[0]
    assert names == list(J.PROMPT_QUESTIONS) and "Read every promise back together" in text and "turn 4" in text
    rec = json.loads(setup.log_path.read_text().splitlines()[-1])
    assert rec["accepted"] and rec["patch"]["id"] == "p1" and {c["name"] for c in rec["checks"]} >= set(J.PROMPT_QUESTIONS)


@pytest.mark.parametrize("q", list(J.PROMPT_QUESTIONS))
def test_every_critical_question_can_reject(setup, q):
    answers = dict(PASS)
    answers[q] = "no" if PASS[q] == "yes" else "yes"
    v = J.PatchJudge(ScriptedBackend(answers)).judge(good_patch(), setup)
    assert not v.accepted and v.reasons[0].startswith(q)


def test_non_critical_question_does_not_reject(setup):
    answers = dict(PASS, redundant="yes")
    setup.critical = {"redundant": False}
    v = J.PatchJudge(ScriptedBackend(answers)).judge(good_patch(), setup)
    assert v.accepted


def test_no_answer_rejects_and_no_backend_rejects(setup):
    v = J.PatchJudge(ScriptedBackend({})).judge(good_patch(), setup)
    assert not v.accepted
    v = J.PatchJudge(None).judge(good_patch(), setup)
    assert not v.accepted and "decision_model" in v.reasons[0]


def test_local_backend_majority_vote():
    seq = iter([{"answer": "yes", "evidence": "a"}, {"answer": "no", "evidence": "b"}, {"answer": "yes", "evidence": "c"}])
    calls = []

    def fake(system, user, model, json_schema=None, **kw):
        calls.append(kw["purpose"])
        assert kw["temperature"] == 0
        return next(seq)

    b = J.LocalDecisionBackend(fake, "anthropic:x", votes=3)
    out = b.ask("ctx", [{"name": "necessary", "question": "q?", "patch_id": "p1"}])
    assert out["necessary"].answer == "yes" and len(calls) == 3 and "vote 3" in calls[-1]


# --- policy patches --------------------------------------------------------------------------------------------------

def test_policy_patch_checks(setup):
    ctx = setup
    ok = {"id": "a1", "path": "/callback/end", "old_value": "16:30", "new_value": "16:30", "brief_quote": "09:30 to 16:30", "rationale": "x"}
    answers = {"value_supported": "yes", "override_respected": "yes", "contradicts_brief": "no"}
    v = J.PatchJudge(ScriptedBackend(answers)).judge_policy(ok, ctx)
    assert v.accepted, v.reasons
    for bad, failing in ((dict(ok, path="/callback/nope"), "pointer_exists"),
                         (dict(ok, new_value=5), "schema_valid"),
                         (dict(ok, path="/queue_map/human", new_value="desk_unknown"), "policy_check"),
                         (dict(ok, brief_quote="not in the brief at all"), "quote_in_brief")):
        v = J.PatchJudge(ScriptedBackend(answers)).judge_policy(bad, ctx)
        assert not v.accepted and failing in [c["name"] for c in v.checks if not c["passed"]], (failing, v.checks)
    v = J.PatchJudge(ScriptedBackend(dict(answers, override_respected="no"))).judge_policy(ok, ctx)
    assert not v.accepted


# --- vendor adapter (stubbed package, never the real repo) -----------------------------------------------------------

def make_vendor(tmp_path, has_key=True):
    tools = tmp_path / "vendor" / "tools"
    tools.mkdir(parents=True)
    (tools / "envfile.py").write_text("def load_env(path=None):\n    pass\n")
    (tools / "decide.py").write_text(textwrap.dedent(f"""
        from envfile import load_env
        MODEL = "stub-model"
        CALLS = []
        def has_decision_key():
            return {has_key}
        def decide(text, questions, model=MODEL, timeout=90):
            CALLS.append((text, questions, model))
            ans = []
            for q in questions:
                p = 0.9 if q["name"] in ("necessary", "supported", "right_place") else 0.1
                if q["name"] == "redundant":
                    ans.append(dict(name=q["name"], type="refusal", probability=None))
                    continue
                ans.append(dict(name=q["name"], type="predicate", probability=p))
            return dict(answers=ans, model=model, provider="stub")
    """))
    return tmp_path / "vendor"


def test_vendor_adapter_formats_questions_and_maps_answers(tmp_path, setup):
    path = make_vendor(tmp_path)
    before = set(sys.modules)
    mod = J.load_vendor(path)
    assert mod is not None and "envfile" not in sys.modules and "decide" not in sys.modules
    assert not ({"decide", "envfile"} & (set(sys.modules) - before))
    tr = NullTrace()
    judge = J.make_judge(Models(), tr, call_llm=None, vendor_path=path)
    assert isinstance(judge, J.ToprakDecisionJudge) and any("vendor decision model" in l for l in tr.lines)
    v = judge.judge(good_patch(), setup)
    text, qs, model = judge.backend.mod.CALLS[-1]
    assert [q["type"] for q in qs] == ["predicate"] * 6 and [q["name"] for q in qs] == list(J.PROMPT_QUESTIONS)
    assert all(q["instructions"] for q in qs) and "patch" not in qs[0] and "# The edit" in text
    by = {c["name"]: c for c in v.checks}
    assert by["necessary"]["answer"] == "yes" and by["contradicts_brief"]["answer"] == "no" and by["redundant"]["answer"] is None
    assert not v.accepted and v.reasons[0].startswith("redundant")  # a refusal is not a pass


def test_missing_vendor_falls_back_to_local_judge_with_a_trace_line(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    tr = NullTrace()
    judge = J.make_judge(Models(), tr, call_llm=lambda *a, **k: {"answer": "yes", "evidence": ""}, vendor_path=tmp_path / "none")
    assert isinstance(judge, J.LocalDecisionJudge)
    assert any("NOT available" in l for l in tr.lines)


def test_vendor_without_key_and_no_local_key_gives_no_judge(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(J, "REPO", tmp_path)
    import builder.template_cc.llm as L
    monkeypatch.setattr(L, "load_env", lambda path=None: None)
    tr = NullTrace()
    judge = J.make_judge(Models(judge="anthropic:claude-sonnet-5-5"), tr, call_llm=None, vendor_path=make_vendor(tmp_path, has_key=False))
    assert judge is None and any("logged, not applied" in l for l in tr.lines)


# --- judge agreement -------------------------------------------------------------------------------------------------

def test_judge_agreement_on_a_toy_labels_file(tmp_path):
    log = [{"patch": {"id": "p1"}, "accepted": True, "checks": [{"name": "necessary", "kind": "decision", "answer": "yes"},
                                                                {"name": "case_specific", "kind": "decision", "answer": "no"}]},
           {"patch": {"id": "p2"}, "accepted": False, "checks": [{"name": "necessary", "kind": "decision", "answer": "no"}]},
           {"patch": {"id": "p3"}, "accepted": True, "checks": [{"name": "necessary", "kind": "decision", "answer": "yes"}]}]
    labels = [{"patch_id": "p1", "accept": True, "questions": {"necessary": "yes", "case_specific": "yes"}},
              {"patch_id": "p2", "accept": True, "questions": {"necessary": "no"}},
              {"patch_id": "p3", "accept": True, "questions": {"necessary": "no"}},
              {"patch_id": "p9", "accept": False}]
    out = judge_agreement.agreement(log, labels)
    assert out["accept"] == {"n": 3, "agree": 2, "agreement": 2 / 3}
    assert out["questions"]["necessary"] == {"yes_yes": 1, "yes_no": 0, "no_yes": 1, "no_no": 1, "n": 3, "agreement": 2 / 3}
    assert out["questions"]["case_specific"]["yes_no"] == 1 and out["missing"] == ["p9"]
    a, b = tmp_path / "log.jsonl", tmp_path / "labels.jsonl"
    a.write_text("\n".join(json.dumps(x) for x in log))
    b.write_text("\n".join(json.dumps(x) for x in labels))
    assert judge_agreement.main([str(a), str(b)]) == 0


def test_claude_cli_judge_needs_no_api_key(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("builder.template_cc.llm.load_env", lambda *a, **k: None)
    judge = J.make_judge(Models(), NullTrace(), call_llm=lambda *a, **k: {"answer": "yes", "evidence": ""}, vendor_path=tmp_path / "none")
    assert isinstance(judge, J.LocalDecisionJudge)


def test_default_judge_is_local_even_with_openai_and_openrouter_keys(monkeypatch):
    """The final path must not call OpenAI / OpenRouter: the vendor decision model is opt-in (TCC_VENDOR_JUDGE=1)."""
    monkeypatch.setenv("OPENAI_API_KEY", "k-not-real")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k-not-real")
    monkeypatch.delenv("TCC_VENDOR_JUDGE", raising=False)
    judge = J.make_judge(Models(), NullTrace(), call_llm=lambda *a, **k: {"answer": "yes", "evidence": ""})
    assert isinstance(judge, J.LocalDecisionJudge)
