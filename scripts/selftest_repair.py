"""Repair rounds for one build: failed self-test scenarios -> allowed actions -> ONE rebuild per round -> keep it only if nothing breaks.

    .venv310/bin/python scripts/selftest_repair.py --dir <green build dir> --work <dir> --briefs a.md,b.md --api tools.json \
        --cases cases.json [--deadline EPOCH] [--rounds 3]

Actions, in order of preference (never compiler code, never a rule that has a passing scenario):
  R1 policy_field  a failed scenario points at a policy field; the extract verifier is re-run for THAT field with the brief text and a
                   new value is accepted only with an exact brief quote (policy_check must not get worse).
  R2 disable_flag  the failure comes from a code rule whose parameters the brief defines differently: final_build.sh --disable <flag>.
  R3 line_fix      a fixed line of policy.json (/lines/<key>) is replaced by the brief's own sentence (quote-checked).
  R4 prompt_patch  ONLY for UNKNOWN / prompt-only rules (case["unknown"], or a rule type outside selftest_cases.SOURCES, or marked so by
                   rule_coverage.py): the brief sentence verbatim + a short IF/THEN restatement appended to the unlocked zone of its stage.
Diagnosis: deterministic first (ledger diff -> R1 pointers, rule -> flag), then at most ONE claude_cli:sonnet call per failed scenario.
Acceptance per round: all actions of the round are built together; only the failed scenarios + 3 previously passing ones are re-run
(x1, a broken one is re-run once). Keep the round if at least one failure is fixed and no passing scenario breaks, else revert to the
last green build. Rounds: at most --rounds or until --deadline. Writes <work>/patch_report.json; the last stdout line is
`GREEN <dir>` (nothing failed) or `FINAL <dir>` (the last green build; same as --dir when every round was reverted).
"""
from __future__ import annotations

import argparse
import copy
import re
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from builder.template_cc import compiler, extract, llm, patches, policy_check, zones  # noqa: E402
from builder.template_cc.apimodel import ApiModel  # noqa: E402
from builder.template_cc.testgen import persona_tokens  # noqa: E402
from builder.template_cc import features as feat  # noqa: E402
from builder.template_cc import io as tio  # noqa: E402
from selftest_cases import SOURCES  # noqa: E402
from selftest_common import is_outage  # noqa: E402

MODEL = os.environ.get("TCC_SELFTEST_MODEL") or "claude_cli:sonnet"  # every call goes through llm.call_llm: TCC_LLM_PROVIDER=anthropic needs no Claude login
FLAG_DOC = {
    "f1_id_complete": "identity answers are collected completely (spoken, in pieces) before they are checked",
    "f2_question_not_refusal": "a question asked during the identity check is not counted as a refusal",
    "f3_human_first": "a request for a person is first answered with one offer to help, then transferred",
    "f4_discount": "requests for discounts / waivers are declined using only what the brief lists",
    "f5_vague_date_policy": "a vague payment date is answered with the brief's date policy",
    "f6_counters": "counters for repeated asks (right party, id decline, callback re-ask ...)",
    "f7_router": "a side-intent router (hardship, dispute, already paid, restructuring ...) around the main flow",
    "f8_fixed_lines": "the brief's verbatim lines are spoken by the code, not by the model",
    "f9_number_input": "amounts and dates the customer says are collected as typed numbers",
    "f10_extract_vote": "two-reading vote at extraction",
    "f11_promise_typed": "the promise offer, read-back and confirmation are typed steps with the deadline logic",
    "f12_slot_summary": "a summary of the collected slots is given to the model each turn",
    "f13_verifier": "a verifier node checks the closing outcome before close_call",
    "f14_rules_ifthen": "the brief's extra rules are compiled as if-then checks",
    "f15_hardship_terms": "a hardship customer's own terms are taken into account",
    "f16_intents": "intent definitions with examples and near misses from the brief decide side intents",
}
# R2 may only switch off PARAMETRIC rules (their parameters come from the brief). Core flags f1-f15 and f18/f20/f21 are never disabled.
R2_ALLOW = ("f17", "f19_refusal_rule", "f22_forbidden_disclosure", "f24_day_once", "f25_dispute_wins")
FLAG_DOC.update({"f19_refusal_rule": "the brief's refusal rule (what counts as a refusal, how often to ask again)",
                 "f22_forbidden_disclosure": "the brief's list of things never to disclose before identity",
                 "f24_day_once": "a weekday word is taken once as the brief defines it",
                 "f25_dispute_wins": "a dispute wins over other side intents as the brief orders them"})


def r2_ok(flag):
    return any(flag.startswith(a) for a in R2_ALLOW)


LOCKED = ("/_source", "/_overrides", "/tools", "/account_fields", "/product_id_format", "/call_variables", "/unused_tools")
PROPOSE = """You repair a phone agent built from a BRIEF. A self-test scenario failed and the deterministic checks found no cause. You may choose
exactly ONE action:
 1. "policy_field": a value of policy.json contradicts the brief (JSON pointer from ALLOWED_POINTERS, never one in PROTECTED).
 2. "disable_flag": ONLY when the brief defines the parameters of a code rule differently from what the transcript shows (the rule's
    behaviour contradicts a brief sentence you can quote). A mere bug of the agent in following a rule the brief does state is NOT this: choose "none".
    Give "brief_quote" = the exact brief sentence the rule's behaviour contradicts. Name the flag from ACTIVE_FLAGS (only parametric rules are listed; if ACTIVE_FLAGS is empty or none fits, choose "none") (FLAG_HINTS_FOR_THIS_RULE lists likely ones) and say which behaviour contradicts which brief sentence.
 3. "line_fix": a spoken line of policy.json (a key of LINES) differs from a sentence the brief fixes; give "line_key" and "brief_quote" =
    the brief's own sentence, copied exactly.
 4. "prompt_patch": ONLY when UNKNOWN_RULE is true. Pick the unlocked zone of the stage where the behaviour happens from ZONES; "after" must
    contain the brief sentence verbatim (= brief_quote) plus a short IF/THEN restatement, no customer names, amounts or dates; op "append" with
    before "", or "replace" with before copied exactly (once).
 5. "none": no clear cause (the simulated customer went off script, the criterion is not decided by the brief). Prefer "none" over a guess.
Say in "diagnosis" in one sentence which node spoke and what went wrong. Reply with the JSON object only."""
PROPOSE_SCHEMA = {"type": "object", "required": ["diagnosis", "action"], "properties": {
    "diagnosis": {"type": "string"}, "action": {"type": "string", "enum": ["policy_field", "disable_flag", "line_fix", "prompt_patch", "none"]},
    "pointer": {"type": ["string", "null"]}, "flag": {"type": ["string", "null"]}, "line_key": {"type": ["string", "null"]},
    "zone": {"type": ["string", "null"]}, "op": {"type": ["string", "null"]}, "before": {"type": ["string", "null"]},
    "after": {"type": ["string", "null"]}, "brief_quote": {"type": ["string", "null"]}}}
RULE_FLAGS = {"human_then_pay": ["f3_human_first"], "pieces_identity": ["f1_id_complete", "f9_number_input"], "identity_declined": ["f2_question_not_refusal", "f1_id_complete"],
              "identity_failed": ["f1_id_complete"], "hardship": ["f15_hardship_terms", "f7_router"], "vague_date": ["f11_promise_typed", "f5_vague_date_policy"],
              "floor_amount": ["f4_discount", "f11_promise_typed"], "dispute": ["f7_router", "f16_intents"], "already_paid": ["f7_router", "f16_intents"],
              "restructuring": ["f7_router", "f16_intents"], "robot_turn1": ["f8_fixed_lines"], "robot_mid": ["f8_fixed_lines"]}
VERIFY2 = """A self-test of a phone agent suggests that ONE field of its policy.json may not match the brief. Read the brief and answer what
value the BRIEF states for this field. Reply {"value": <the value, same JSON type and enum spelling as the current one>, "quote": <an
EXACT span copied from the brief that states it>, "same_as_current": bool}. If the brief does not state a different value, set
same_as_current true. Never use an example value; later material in the brief overrides earlier material."""
VERIFY2_SCHEMA = {"type": "object", "required": ["quote", "same_as_current"], "properties": {"quote": {"type": "string"}, "same_as_current": {"type": "boolean"}}}


FIELD_MEANING = {
    "/ask_limits/right_party": "the TOTAL number of times the agent asks the right-party question, the first ask included (1 = never asks again; 2 = one more ask after an unclear answer)",
    "/identity/max_wrong": "the number of wrong answers in total after which the identity check is over",
}


def sh(cmd, env=None, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=timeout)


def get_ptr(doc, ptr):
    return extract._get(doc, ptr)


def pointers(policy, limit=400):
    out = []

    def walk(p, v):
        if any(p == k or p.startswith(k + "/") for k in LOCKED):
            return
        if isinstance(v, dict):
            for k, x in v.items():
                walk(f"{p}/{str(k).replace('~', '~0').replace('/', '~1')}", x)
        elif isinstance(v, list) and v and all(isinstance(x, (dict, list)) for x in v):
            for i, x in enumerate(v):
                walk(f"{p}/{i}", x)
        else:
            out.append(p)
    walk("", policy)
    return out[:limit]


def transcript_text(path, last=24):
    try:
        msgs = json.loads(Path(path).read_text())["messages"]
    except Exception:  # noqa: BLE001
        return ""
    lines = []
    for m in msgs[-last:]:
        who = m.get("role") or m.get("speaker") or "?"
        txt = m.get("content") or m.get("text") or m.get("message") or ""
        if m.get("tool_calls") or m.get("toolCalls"):
            txt = (txt + " " + json.dumps(m.get("tool_calls") or m.get("toolCalls"), ensure_ascii=False))[:300]
        lines.append(f"{who}: {str(txt)[:400]}")
    return "\n".join(lines)


def is_unknown(case):
    """R4 is only for scenarios built from a brief rule marked UNKNOWN / prompt-only (they carry that rule's brief quote)."""
    return bool(case.get("unknown")) and bool(case.get("quotes"))


def unlocked_zones(base_dir, limit=60):
    f = Path(base_dir) / "zones.final.json"
    if not f.exists():
        return {}
    book = zones.ZoneBook.from_json(json.loads(f.read_text()))
    return {k: {"purpose": z.get("purpose", ""), "text": z["text"][:700]} for k, z in list(book.zones.items()) if not z["locked"]}


OWNED_BY_FIXUPS = re.compile(r"^/(ask_limits/id_decline|attempt_plan/\d+/cap_at_deadline)$")  # set deterministically by policy_fixups (final_build); never re-decided by a model


def verify_field(policy, ptr, diagnosis, brief):
    """The extraction verifier, for one field: a new value only with an exact brief quote. Returns (new_policy, quote) or (None, why)."""
    if OWNED_BY_FIXUPS.match(ptr):
        return None, "this field is set by policy_fixups from explicit brief phrases"
    cur = get_ptr(policy, ptr)
    if cur is extract.extract_vote.ABSENT:
        return None, "pointer does not exist"
    meaning = FIELD_MEANING.get(ptr)
    user = json.dumps({"pointer": ptr, "current_value": cur, "current_quote": (policy.get("_source") or {}).get(ptr),
                       **({"what_the_value_means_in_the_agent": meaning} if meaning else {}), "what_the_self_test_saw": diagnosis}, ensure_ascii=False)
    ans = llm.call_llm(VERIFY2, f"# BRIEF\n\n{brief}\n\n# FIELD\n\n{user}", MODEL, json_schema=VERIFY2_SCHEMA,
                       purpose=f"selftest verifier {ptr}", attempts=2)
    if ans.get("same_as_current") or "value" not in ans or ans["value"] == cur:
        return None, "the verifier finds the current value right"
    if not policy_check.quote_in(str(ans.get("quote") or ""), brief):
        return None, "the verifier's quote is not in the brief"
    new = copy.deepcopy(policy)
    extract._set(new, ptr, ans["value"])
    new.setdefault("_source", {})[ptr] = ans["quote"]
    return new, ans["quote"]


def check_patch(prop, brief, all_cases):
    """A prompt patch (patches.py schema) or a string saying why it is refused. R4: the brief sentence must be in `after` verbatim."""
    patch = {"id": "st1", "zone": prop.get("zone"), "op": prop.get("op"), "before": prop.get("before") or "", "after": prop.get("after"),
             "brief_quote": prop.get("brief_quote"), "rationale": prop.get("diagnosis", "")}
    errs = patches.shape_errors(patch)
    if errs:
        return "patch: " + "; ".join(errs)
    if not policy_check.quote_in(str(patch["brief_quote"] or ""), brief):
        return "patch: brief_quote is not in the brief"
    if policy_check.norm_text(patch["brief_quote"]) not in policy_check.norm_text(patch["after"]):
        return "patch: the brief sentence is not in the text verbatim"
    low = patch["after"].lower()
    if any(t.lower() in low for t in persona_tokens([{"id": "x", "variables": {}, "identity": {}, "products": [], **c} for c in all_cases]) if len(t) > 3):
        return "patch: names customer data of a scenario"
    return patch


def apply_patches(d, plist, api_path):
    """Apply prompt patches to the build in `d` (workflow.json + zones.final.json). Returns [(patch id, ok/why)]."""
    d = Path(d)
    wf = json.loads((d / "workflow.json").read_text())
    book0 = zones.ZoneBook.from_json(json.loads((d / "zones.final.json").read_text()))
    policy = json.loads((d / "policy.json").read_text())
    book, notes = book0, []
    for i, pt in enumerate(plist):
        try:
            wf2, book2 = patches.apply_one(wf, book, {**pt, "id": f"st{i}"})
        except Exception as e:  # noqa: BLE001
            notes.append(f"{pt['zone']}: {e}"[:160])
            continue
        if not patches.within_growth(book0, book2):
            notes.append(f"{pt['zone']}: over the growth budget")
            continue
        if compiler.lint(wf2, policy, ApiModel.load(api_path))["errors"]:
            notes.append(f"{pt['zone']}: does not lint")
            continue
        wf, book = wf2, book2
        notes.append(f"{pt['zone']}: applied")
    (d / "workflow.json").write_text(compiler.dump(wf))
    (d / "zones.final.json").write_text(json.dumps(book.to_json(), indent=1))
    return notes


def active_flags(build_dir):
    rep = json.loads((Path(build_dir) / "build_report.json").read_text())
    return rep.get("features") or []


def build(cand, base, policy_path, flags, disable, briefs, api):
    env = {"FEATURES": ",".join(flags), "FINAL_SAVE": "none"}  # candidates are tests: nothing is saved to Freya
    if policy_path:
        env["POLICY"] = str(Path(policy_path).resolve())
    cmd = ["bash", str(REPO / "scripts" / "final_build.sh")] + (["--disable", disable] if disable else []) + [*briefs, api, str(cand)]  # disable: "f3,f15"
    r = sh(cmd, env)
    ok = (Path(cand) / "workflow.json").exists() and (Path(cand) / "policy.json").exists()
    return ok, r.stdout[-600:]


def results(d, name="results.json"):
    p = Path(d) / "selftest" / name
    return json.loads(p.read_text()) if p.exists() else None


def run_tests(d, cases, api, only=None, name="results.json", wait=900):
    cmd = ["bash", str(REPO / "scripts" / "selftest_run.sh"), str(d), "--cases", cases, "--api", api, "--results", name]
    if only:
        cmd += ["--only", ",".join(only)]
    return subprocess.Popen(cmd, stdout=open(Path(d) / "selftest" / (name + ".log"), "w"), stderr=subprocess.STDOUT,
                            env={**os.environ, "LANE_WAIT": str(wait)})


def passing(rep):
    return {r["case"] for r in (rep or {}).get("rows", []) if r["pass"]}



def diff_pointers(row, case, policy):
    """Deterministic: which policy fields a ledger diff points at."""
    reasons, exp, led = " ".join(row["failed"]), case["expected"], row.get("ledger") or {}
    out = []
    if "outcome (" in reasons:
        for k, v in (policy.get("outcome_map") or {}).items():
            if v and list(v) in ([exp["outcome"], exp["reason"]], [led.get("outcome"), led.get("reason")]):
                out.append(f"/outcome_map/{k}")
    rule = case.get("rule")
    if "transfers" in reasons:
        out += [f"/queue_map/{k}" for k in (policy.get("queue_map") or {})]
    if "notes" in reasons:
        out += ["/note_map", "/notes_on_restructuring"]
    if "promises" in reasons:
        out += {"floor_amount": ["/product_types"], "multi_product": ["/multi_product", "/max_products", "/close_rule"],
                "vague_date": ["/vague_date_offer", "/bands", "/holidays"]}.get(rule, ["/bands", "/attempt_plan", "/holidays", "/attempt1"])
    if "callbacks" in reasons:
        out += ["/callback", "/holidays"]
    if any(w in reasons for w in ("verify_identity", "identity")):
        out += ["/identity/max_wrong", "/identity/questions"]
    seen = []
    for p in out + [x for x in SOURCES.get(rule, []) if "outcome" not in reasons or x.startswith("/ask")]:
        if p not in seen and get_ptr(policy, p) is not extract.extract_vote.ABSENT:
            seen.append(p)
    return seen


def diagnose(row, case, policy, brief, flags, base, protected):
    """ONE action for a failed scenario. Deterministic first (R1 via the verifier, R2 via rule->flag), then <= 1 model call."""
    act = {"case": row["case"], "rule": case.get("rule"), "failed": row["failed"][:3]}
    ledger_part = any(w in " ".join(row["failed"]) for w in ("outcome (", "promises", "notes", "callbacks", "transfers"))
    if ledger_part:
        for ptr in [p for p in diff_pointers(row, case, policy) if not any(p == q or p.startswith(q + "/") for q in protected)][:2]:
            new, why = verify_field(policy, ptr, "; ".join(row["failed"])[:400], brief)
            if new is not None:
                return {**act, "action": "policy_field", "pointer": ptr, "policy": new, "quote": why, "how": "ledger diff -> R1"}
    ptrs = pointers(policy)
    user = json.dumps({
        "scenario": {"rule_type": case.get("rule"), "customer_script": case.get("script"), "expected_ledger": case.get("expected"),
                     "criteria_with_brief_quotes": case.get("criteria")},
        "result": {"failed": row["failed"], "ledger": row["ledger"], "tools_called": row["tools"]},
        "transcript_tail": transcript_text(Path(base) / "selftest" / "transcripts" / f"{row['case']}.json"),
        "ALLOWED_POINTERS": ptrs, "PROTECTED": sorted(protected), "LINES": {k: v.get("text") for k, v in (policy.get("lines") or {}).items()},
        "ACTIVE_FLAGS": {f: FLAG_DOC.get(f, "") for f in flags if r2_ok(f)}, "FLAG_HINTS_FOR_THIS_RULE": [f for f in RULE_FLAGS.get(case.get("rule"), []) if f in flags and r2_ok(f)], "UNKNOWN_RULE": is_unknown(case),
        "ZONES": unlocked_zones(base) if is_unknown(case) else {}}, ensure_ascii=False)
    try:
        prop = llm.call_llm(PROPOSE, f"# BRIEF\n\n{brief}\n\n# CASE\n\n{user}", MODEL, json_schema=PROPOSE_SCHEMA, purpose="selftest diagnosis", attempts=2)
    except Exception as e:  # noqa: BLE001
        return {**act, "action": "none", "why": f"diagnosis failed {e!r}"[:160]}
    act["diagnosis"], kind = prop.get("diagnosis"), prop.get("action")
    if kind == "policy_field" and prop.get("pointer") and not any(prop["pointer"] == q or prop["pointer"].startswith(q + "/") for q in protected):
        new, why = verify_field(policy, prop["pointer"], prop.get("diagnosis"), brief)
        if new is not None:
            return {**act, "action": "policy_field", "pointer": prop["pointer"], "policy": new, "quote": why, "how": "diagnosis -> R1"}
        return {**act, "action": "none", "why": why}
    if kind == "disable_flag" and prop.get("flag") in flags and r2_ok(prop.get("flag") or "") and policy_check.quote_in(str(prop.get("brief_quote") or ""), brief):
        return {**act, "action": "disable_flag", "flag": prop["flag"], "how": "diagnosis -> R2"}
    if kind == "line_fix" and prop.get("line_key") in (policy.get("lines") or {}) and policy_check.quote_in(str(prop.get("brief_quote") or ""), brief):
        new = copy.deepcopy(policy)
        new["lines"][prop["line_key"]] = {"text": prop["brief_quote"].strip(), "verbatim": True}
        return {**act, "action": "line_fix", "pointer": f"/lines/{prop['line_key']}", "policy": new, "quote": prop["brief_quote"], "how": "diagnosis -> R3"}
    if kind == "prompt_patch":
        if not is_unknown(case):
            return {**act, "action": "none", "why": "prompt patches are only for unknown rules"}
        pt = check_patch(prop, brief, [case])
        if not isinstance(pt, str) and not any(policy_check.norm_text(q) in policy_check.norm_text(pt["brief_quote"]) or policy_check.norm_text(pt["brief_quote"]) in policy_check.norm_text(q) for q in case["quotes"]):
            pt = "patch: the quoted sentence is not the unknown rule of this scenario"
        return {**act, "action": "none", "why": pt} if isinstance(pt, str) else {**act, "action": "prompt_patch", "patch": pt, "how": "diagnosis -> R4"}
    return {**act, "action": "none", "why": "no clear cause"}


def merged_results(base_rep, sub_rep):
    rows = {r["case"]: r for r in base_rep["rows"]}
    rows.update({r["case"]: r for r in sub_rep["rows"]})
    rows = list(rows.values())
    return {**base_rep, "rows": rows, "passed": sum(r["pass"] for r in rows), "note": "rows of the last round come from the subset re-run"}


def repairs_from_build(build_dir):
    """Pointers the compiler's own policy_check had to default (the trace says `policy default: line X not found verbatim ...`)."""
    import re
    try:
        tr = Path(json.loads((Path(build_dir) / "build_report.json").read_text())["trace"]) / "trace.txt"
        return [f"/lines/{m.group(1)}/text" for m in re.finditer(r"policy default: line (\w+) not found verbatim", tr.read_text())]
    except Exception:  # noqa: BLE001
        return []


def precheck(policy, brief, build_dir=None):
    """No-sim static check, routed straight to R1/R3: (1) value vs its _source quote (numbers, dates, times; also a number WORD in the
    quote against an integer value), (2) a line marked verbatim that is not a brief sentence, (3) a line the compiler defaulted to the model's
    paraphrase because it was not in the brief."""
    out = list(extract.extract_vote.quote_mismatches(policy))
    W = extract.extract_vote._word_tokens
    for ptr, q in (policy.get("_source") or {}).items():
        v = get_ptr(policy, ptr)
        if isinstance(v, int) and not isinstance(v, bool) and isinstance(q, str) and ptr not in out:
            said = W(q)
            if said and not extract.extract_vote._tokens_text(q) and str(v) not in said and ptr.count("/") >= 2:
                out.append(ptr)
    for key, ln in (policy.get("lines") or {}).items():
        if isinstance(ln, dict) and ln.get("verbatim") and ln.get("text") and not policy_check.quote_in(ln["text"], brief):
            out.append(f"/lines/{key}/text")
    if build_dir:
        out += [p for p in repairs_from_build(build_dir) if p not in out]
    return [p for p in dict.fromkeys(out) if not any(p == q or p.startswith(q + "/") for q in LOCKED)]


def precheck_fix(policy, brief, build_dir=None):
    """R1 for every static mismatch: the verifier must give a brief quote. Returns (new policy, [{pointer, old, new, quote | why}])."""
    log, cur = [], policy
    for ptr in precheck(policy, brief, build_dir):
        new, why = verify_field(cur, ptr, "the value disagrees with its _source quote, or a verbatim line is not the brief's sentence", brief)
        if new is not None:  # a static change needs the verifier to agree with itself twice (a flaky verifier must not flip a field between runs)
            new2, _ = verify_field(cur, ptr, "the value disagrees with its _source quote, or a verbatim line is not the brief's sentence", brief)
            if new2 is None or get_ptr(new2, ptr) != get_ptr(new, ptr):
                new, why = None, "the verifier did not give the same value twice"
        if new is not None and ptr.startswith("/lines/") and ptr.endswith("/text"):
            key = ptr.split("/")[2]
            new["lines"][key]["verbatim"] = bool(policy_check.quote_in(new["lines"][key]["text"], brief))
        log.append({"pointer": ptr, "old": get_ptr(cur, ptr), **({"new": get_ptr(new, ptr), "quote": why} if new is not None else {"kept": why})})
        if new is not None:
            cur = new
    return cur, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--briefs", required=True)
    ap.add_argument("--api", required=True)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--deadline", type=float, default=0, help="epoch seconds (T-30): start no round that cannot finish before it")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--round-s", type=int, default=1500, help="expected seconds of one round, for the deadline check")
    a = ap.parse_args()
    green, work = Path(a.dir).resolve(), Path(a.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    briefs = [b for b in a.briefs.split(",") if b]
    api, cases_file = str(Path(a.api).resolve()), str(Path(a.cases).resolve())
    cases = {c["id"]: c for c in json.loads(Path(cases_file).read_text())}
    brief = tio.brief_text(tio.read_briefs([Path(b) for b in briefs]))
    report = {"start": str(green), "rounds": []}
    t_all, tried, single = time.time(), set(), False

    def finish(status):
        report.update({"status": status, "final": str(green), "seconds": round(time.time() - t_all)})
        (work / "patch_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
        print(f"{'GREEN' if status == 'green' else 'FINAL'} {green}")
        return 0

    for rno in range(1, a.rounds + 1):
        rep = results(green)
        if rep is None:
            print("no selftest/results.json in", green)
            return 2
        if is_outage(rep):  # outage signature: never repair from it
            report["outage"] = f"round {rno}: the results of {green} show the outage signature (no tool call in any scenario): MISSING, no action, build kept"
            return finish("outage")
        fails = [r for r in rep["rows"] if not r["pass"] and r["case"] not in (rep.get("missing") or [])]
        if not fails:
            return finish("green")
        if a.deadline and time.time() + a.round_s > a.deadline:
            report["stopped"] = f"round {rno} not started: T-30 is too close"
            break
        t0, st = time.time(), {}
        policy = json.loads((green / "policy.json").read_text())
        flags = active_flags(green)
        ok_rules = {cases[r["case"]]["rule"] for r in rep["rows"] if r["pass"]} - {cases[r["case"]]["rule"] for r in fails}
        protected = {p for rule in ok_rules for p in SOURCES.get(rule, [])} - {p for r in fails for p in SOURCES.get(cases[r["case"]]["rule"], [])}
        acts = []
        for row in fails:
            act = diagnose(row, cases[row["case"]], policy, brief, flags, green, protected)
            key = (act["case"], act["action"], act.get("pointer") or act.get("flag") or json.dumps(act.get("patch"), sort_keys=True))
            if act["action"] != "none" and key in tried:
                act = {**act, "action": "none", "why": "already tried in an earlier round"}
            acts.append(act)
            if act["action"] != "none":
                tried.add(key)
        st["diagnose"] = round(time.time() - t0)
        use = [x for x in acts if x["action"] != "none"]
        if single:
            use = use[:1]
        rr = {"round": rno, "actions": [{k: v for k, v in x.items() if k not in ("policy",)} for x in acts], "used": [x["case"] for x in use]}
        report["rounds"].append(rr)
        if not use:
            rr["result"] = "no allowed action: stop"
            break
        # one rebuild with every action of the round
        t1 = time.time()
        d = work / f"round{rno}"
        d.mkdir(parents=True, exist_ok=True)
        newpol = copy.deepcopy(policy)
        for x in use:
            if x["action"] in ("policy_field", "line_fix"):
                ptr = x["pointer"]
                val = get_ptr(x["policy"], ptr)
                extract._set(newpol, ptr, val)
                newpol.setdefault("_source", {})[ptr] = x["quote"]
        (d / "policy.in.json").write_text(json.dumps(newpol, indent=1, ensure_ascii=False))
        dis = ",".join(sorted({x["flag"] for x in use if x["action"] == "disable_flag"}))
        ok, tail = build(d, green, d / "policy.in.json", flags, dis or None, briefs, api)
        if ok:
            built_pol = json.loads((d / "policy.json").read_text())
            for x in use:
                if x["action"] in ("policy_field", "line_fix") and get_ptr(built_pol, x["pointer"]) != get_ptr(newpol, x["pointer"]):
                    x["reverted_by_policy_check"] = True
            if dis and any(f in active_flags(d) for f in dis.split(",")):
                rr["note"] = "a flag is still on"
            pats = [x["patch"] for x in use if x["action"] == "prompt_patch"]
            if pats:
                rr["patch_notes"] = apply_patches(d, pats, api)
        st["build"] = round(time.time() - t1)
        if not ok:
            rr["result"] = "build failed: " + tail[-160:]
            single = True
            continue
        # re-run ONLY the failed scenarios + 3 previously passing ones (x1)
        t2 = time.time()
        fail_ids = [r["case"] for r in fails]
        touched = {p for x in use for p in (SOURCES.get(x["rule"], []))}
        pass_rows = [r for r in rep["rows"] if r["pass"]]
        pass_rows.sort(key=lambda r: -len(touched & set(SOURCES.get(cases[r["case"]]["rule"], []))))
        sub = fail_ids + [r["case"] for r in pass_rows[:3]]
        (d / "selftest").mkdir(exist_ok=True)
        run_tests(d, cases_file, api, sub, "results.json", wait=1800).wait()
        sr = results(d)
        if sr and is_outage(sr):  # an outage run decides nothing: wait 60 s, re-run once if time allows, else drop the round
            rr["outage_first_run"] = True
            if room(a.round_s // 2):
                time.sleep(60)
                run_tests(d, cases_file, api, sub, "results.json", wait=1800).wait()
                sr = results(d)
                rr["outage_rerun"] = True
            if sr and is_outage(sr):
                rr.update({"result": "MISSING (outage signature): round discarded, last green build kept, no action taken from it", "outage": True,
                           "subset": sub, "stages_s": st})
                break
        st["test"] = round(time.time() - t2)
        if not sr:
            rr["result"] = "the test run produced no results"
            rr["stages_s"] = st
            continue
        now = passing(sr)
        fixed = [i for i in fail_ids if i in now]
        broken = [i for i in sub if i not in fail_ids and i not in now and i not in (sr.get("missing") or [])]
        if broken:  # simulation noise: one re-run of the broken ones
            run_tests(d, cases_file, api, broken, "recheck.json", wait=1800).wait()
            rc = results(d, "recheck.json")
            if rc:
                broken = [i for i in broken if i not in passing(rc)]
        keep = bool(fixed) and not broken
        rr.update({"subset": sub, "fixed": fixed, "broken": broken, "kept": keep, "stages_s": st, "seconds": round(time.time() - t0)})
        rr["result"] = "kept" if keep else "reverted to the last green build"
        if keep:
            (d / "selftest" / "results.json").write_text(json.dumps(merged_results(rep, sr), indent=1, ensure_ascii=False))
            green, single = d, False
            (work / "green.txt").write_text(str(green))  # a killed run (ship deadline) still leaves the last green build
        else:
            single = len(use) > 1
    return finish("done")


if __name__ == "__main__":
    sys.exit(main())
