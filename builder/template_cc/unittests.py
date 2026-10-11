"""Freya unit tests for a T-CC workflow: a fast regression gate for the self-test loop.

A unit test is one scripted turn on the real engine: the conversation so far (positioned in one of OUR nodes, with a
variable snapshot computed by running our own setup and facts code), the call variables, and checks on the next turn
(a tool call and its body, a node entered or not, a sentence for the judge). The suite is generated from policy.json
and the compiled workflow, so it works for any brief. The API is the one build-the-builder's freya.py wraps.

    suite = UnitSuite(slot, policy, api, freya_mod, trace)
    report = suite.check(workflow)        # {"passed", "total", "results": [{name, outcome, reason}]}
    UnitSuite.regressions(best_report, candidate_report) -> names that passed before and fail now
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import time

from builder.template_cc import policy_check, testgen
from builder.template_cc.compiler import prompts as P
from builder.template_cc.io import REPO

WAIT_S = 300


# --- the Freya unit-test API (same endpoints as build-the-builder's freya.py) ----------------------------------------

def _body(test: dict) -> dict:
    return {"name": test["name"], "expectation": test["expectation"], "assertions": test["assertions"],
            "tags": test["tags"], "input": {"schemaVersion": 1, "callType": "web", "messages": test["messages"],
                                            "customerVariables": test["customer_variables"]},
            "judgeContext": {"includeRoutingEvents": True, "includeExtractionEvents": True, "includeToolCalls": True,
                             "includeOtherEvents": False}}


def sync(fm, workflow_id: str, tests: list[dict]) -> dict:
    ws = fm.workspace_id()
    existing, offset = [], 0
    while True:
        page = fm.api("GET", "/api/v2/unit-tests", workspaceId=ws, entityType="workflow", entityId=workflow_id,
                      includeArchived="true", limit=200, offset=offset)
        rows = page.get("tests") or []
        existing += rows
        offset += len(rows)
        if len(rows) < 200:
            break
    by_name = {r["name"]: r for r in existing}
    ids = {}
    for t in tests:
        cur = by_name.get(t["name"])
        if cur is None:
            ids[t["name"]] = fm.api("POST", "/api/v2/unit-tests", {"workspaceId": ws, "entityType": "workflow",
                                                                   "entityId": workflow_id, **_body(t)})["id"]
        else:
            fm.api("PATCH", f"/api/v2/unit-tests/{cur['id']}", {**_body(t), "isArchived": False})
            ids[t["name"]] = cur["id"]
    wanted = {t["name"] for t in tests}
    for r in existing:
        if not r.get("isArchived") and "tcc" in (r.get("tags") or []) and r["name"] not in wanted:
            fm.api("PATCH", f"/api/v2/unit-tests/{r['id']}", {"isArchived": True})
    return ids


def mocks(policy: dict, product: dict) -> dict:
    """Mocked bank results ({tool name: result envelope}) so one turn never depends on a live bank."""
    def env(data):
        return {"status": "success", "data": {"ok": True, **data}, "message": "ok", "status_code": 200}
    lf = "products"
    return {"verify_identity": env({"verified": True, "all_verified": False, "wrong_answers_left": 3}),
            "get_account": env({lf: [product]}), "create_promise": env({"promise_id": "pr1"}),
            "schedule_callback": env({}), "transfer_to_agent": env({}), "add_note": env({}), "close_call": env({})}


def run(fm, workflow_id: str, tests: list[dict], ids: dict, wait_s: float = WAIT_S, mock: dict | None = None) -> list[dict]:
    body = {"workspaceId": fm.workspace_id(), "entityType": "workflow", "entityId": workflow_id,
            "unitTestIds": list(ids.values())}
    if mock:
        body["mockToolResults"] = mock
    enq = fm.api("POST", "/api/v2/unit-tests/run-all", body)
    by_test = {e["unitTestId"]: e.get("runIds") or [] for e in enq.get("enqueued") or []}
    pending = {rid for rs in by_test.values() for rid in rs}
    done, end = {}, time.time() + wait_s
    while pending and time.time() < end:
        for rid in list(pending):
            row = fm.api("GET", f"/api/v2/unit-tests/runs/{rid}")
            if row.get("outcome") in ("pass", "fail", "error"):
                done[rid] = row
                pending.discard(rid)
        if pending:
            time.sleep(3)
    out = []
    for t in tests:
        runs = [done[r] for r in by_test.get(ids[t["name"]], []) if r in done]
        if not runs:
            out.append({"name": t["name"], "outcome": "error", "reason": "not run or timed out"})
            continue
        r = runs[-1]
        out.append({"name": t["name"], "outcome": r.get("outcome"), "reason": (r.get("verdictReason") or "")[:300],
                    "failed": [a for a in r.get("assertionResults") or [] if a.get("passed") is False][:4],
                    "agent": (r.get("agentResponse") or "")[:300]})
    return out


# --- the suite ------------------------------------------------------------------------------------------------------

def _speak(role: str, text: str, node: str | None = None, variables: dict | None = None) -> dict:
    row = {"role": role, "message": text}
    if node:
        row["nodeId"] = node
    if node and variables is not None:
        row["messageId"] = "m-" + hashlib.sha1(f"{role}|{node}|{text}".encode()).hexdigest()[:12]
        row["workflowSnapshot"] = {"currentNode": node, "nodeTurnCount": 1, "totalTurnCount": 2, "transitionHistory": [],
                                   "enteredNodes": [node], "variableTable": {
                                       k: {"value": v, "confidence": 1, "source": "tool_result", "source_node": node}
                                       for k, v in variables.items()}}
    return row


def _tool(name: str, body: dict | None = None, negate: bool = False) -> dict:
    row = {"type": "tool_call", "tool": name}
    if negate:
        row["negate"] = True
    if body is not None:  # Freya records the model's own arguments (before our pre-hooks), not a body wrapper
        row["args"] = body
        row["argsMatch"] = "subset"
    return row


def _route(target: str, negate: bool = False) -> dict:
    row = {"type": "routing", "signal": "node_entered", "target": target}
    if negate:
        row["negate"] = True
    return row


def _exec(wf: dict, node: str, variables: dict) -> dict:
    from builder.agentic.tools.lint import _code_of
    from builder.agentic.tools.snippets import find_tool
    ns = {"vars": copy.deepcopy(variables)}
    exec(compile(_code_of(find_tool(wf, node, None)), node, "exec"), ns)  # our own generated code, run locally
    return ns["vars"]


def _say_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d:%A} {d.day} {d:%B} {d.year}"


def build_suite(policy: dict, wf: dict) -> list[dict]:
    """About ten one-turn tests from the policy and the compiled workflow. Values come from the policy only."""
    p = policy
    now = testgen.synth_now_dates(p)[0]
    today = dt.date.fromisoformat(now[:10])
    cv = {k: "Sample" for k in p.get("call_variables") or []}
    cv.update({"customer_id": "c_unit", "now": now, "first_name": "Maria", "last_name": "Costa", "title": "Ms"})
    base = _exec(wf, "setup", {**cv, "who1": "customer"})
    af, types = p["account_fields"], list(p["product_types"].values())
    t0 = types[0]
    fmt = p.get("product_id_format") if "{n}" in str(p.get("product_id_format") or "") else "p{n}"
    product = {af["product_id"]: fmt.replace("{n}", "1"), af["type"]: t0["enum"], af["past_due"]: 420.0,
               af["days_late"]: int(p["bands"][0]["min_days"]), t0["floor_field"]: 100.0}
    if t0.get("last4_field"):
        product[t0["last4_field"]] = "4321"
    facts = _exec(wf, "facts", {**base, "products_raw": [product]})
    row = json.loads(facts["products"])[0]
    pid, label = row["pid"], row["label"]
    hol = {dt.date.fromisoformat(h) for h in p.get("holidays") or []}
    nbd = today + dt.timedelta(days=1)
    while nbd.weekday() >= 5 or nbd in hol:
        nbd += dt.timedelta(days=1)
    pay_day = nbd.isoformat() if nbd.isoformat() <= row["deadline"] else row["deadline"]
    tools = p["tools"]
    cents = tools["create_promise"].get("amount_unit") == "cents"
    opener = wf["nodes"]["opening"]["on_enter"]["text"].replace("{{title}}", "Ms").replace("{{last_name}}", "Costa")
    neg_open = (facts.get("debt_statement", "") + " " + facts.get("first_ask_line", "")).strip()
    tests: list[dict] = []

    def add(name, expectation, assertions, messages):
        tests.append({"name": f"tcc {name}", "expectation": expectation, "assertions": assertions, "messages": messages,
                      "customer_variables": cv, "tags": ["generated", "tcc"]})

    pre = [_speak("assistant", opener, "opening"), _speak("user", "Yes, speaking.")]
    q0 = p["identity"]["questions"][0]
    prompt0 = p["identity"].get("prompts", {}).get(q0, q0)
    kind0 = p["identity"].get("kinds", {}).get(q0, "text")
    if kind0 == "date":
        add("identity: a date said in words goes to verify_identity as YYYY-MM-DD",
            "Call verify_identity once for the first security question, with the date as YYYY-MM-DD.",
            [_tool("verify_identity", {tools["verify_identity"]["field"]: q0}),
             _tool("get_account", negate=True)],
            pre + [_speak("assistant", prompt0, "identity", base), _speak("user", "The thirtieth of January, nineteen eighty-four.")])
    add("identity: a refusal is explained, not routed to the account",
        "Do not open the account. Explain once why the questions are asked.",
        [_route("identity_explain"), _tool("get_account", negate=True)],
        pre + [_speak("assistant", prompt0, "identity", base), _speak("user", "Why should I tell you that? What is this about?")])
    add("opening: a question back is not a confirmation",
        "Answer the question briefly without saying why you are calling, and ask again whether this is the customer.",
        [_tool("verify_identity", negate=True), _tool("get_account", negate=True)],
        [_speak("assistant", opener, "opening", {**cv}), _speak("user", "Who is this?")])
    cb = p["tools"]["schedule_callback"]
    start = p["callback"]["start"]
    hh = f"{min(int(start[:2]) + 1, int(p['callback']['end'][:2])):02d}:00"
    body = {cb["datetime"]: f"{nbd.isoformat()}T{hh}"} if cb.get("mode") == "datetime" else {cb["date"]: nbd.isoformat(), cb["time"]: hh}
    add("third party: a callback inside the window is booked as said",
        f"Book the callback for {_say_date(nbd.isoformat())} at {hh}. Do not verify identity or open the account.",
        [_tool("schedule_callback", body), _tool("verify_identity", negate=True), _tool("get_account", negate=True)],
        [_speak("assistant", opener, "opening"), _speak("user", "No, I am her husband."),
         _speak("assistant", "When would be a good time to reach her?", "third_party", base),
         _speak("user", f"She can be reached on {_say_date(nbd.isoformat())} at {hh}.")])
    del cents  # the model sends major units; our pre-hook converts, so the check is on product and date only
    confirm = P.line(p, "confirm_question") or "Do you confirm?"
    readback = f"For your {label}, the amount is 420 and the date is {_say_date(pay_day)}. {confirm}"
    add("negotiate: a confirmed read-back records exactly that promise",
        "Call create_promise once with the product, amount and date that were read back.",
        [_tool("create_promise", {tools["create_promise"]["product_id"]: pid, tools["create_promise"]["date"]: pay_day})],
        pre + [_speak("assistant", neg_open, "negotiate", facts), _speak("user", f"I can pay all of it on {_say_date(pay_day)}."),
               _speak("assistant", readback, "negotiate", facts), _speak("user", "Yes, I confirm.")])
    neg = pre + [_speak("assistant", neg_open, "negotiate", facts)]
    add("negotiate: 'too much' is not a restructuring request",
        "Do not transfer the call and do not record a promise; offer the minimum amount.",
        [_tool("transfer_to_agent", negate=True), _tool("create_promise", negate=True), _route("transfer_restructuring", negate=True)],
        neg + [_speak("user", "That is too much money for me right now.")])
    add("negotiate: already paid goes to its own route",
        "Do not ask for a promise; the payment claim is handled.",
        [_route("side_mark"), _tool("create_promise", negate=True)],
        neg + [_speak("user", "I already paid this yesterday in the app.")])
    add("negotiate: a hardship goes to the hardship step",
        "Show sympathy and make no persuasion attempt.",
        [_route("hardship"), _tool("create_promise", negate=True)],
        neg + [_speak("user", "I lost my job last month and I am struggling.")])
    if (p.get("queue_map") or {}).get("restructuring"):
        target = "pre_transfer" if "pre_transfer" in wf["nodes"] else "transfer_restructuring"
        add("negotiate: a new payment plan request is transferred",
            "Do not describe a plan; hand the call over.",
            [_route(target), _tool("create_promise", negate=True)],
            neg + [_speak("user", "I want a new payment plan with lower instalments.")])
    if (p.get("queue_map") or {}).get("human"):
        add("negotiate: a request for a person gets one offer to help first",
            "Ask once whether you can help first; do not transfer yet.",
            [_route("human_offer"), _tool("transfer_to_agent", negate=True)],
            neg + [_speak("user", "I want to talk to a real person.")])
    add("negotiate: a robot question is answered, not transferred",
        "Say that you are the bank's digital assistant. Do not transfer the call.",
        [_tool("transfer_to_agent", negate=True), _route("human_offer", negate=True)],
        neg + [_speak("user", "Wait, am I talking to a robot?")])
    for t in tests:
        t["product"] = product
    return tests


class UnitSuite:
    def __init__(self, slot: str, policy: dict, api, freya_mod, trace, origin: str = "https://unit-tests.invalid"):
        self.slot, self.policy, self.api, self.fm, self.trace, self.origin = f"{slot}-unit", policy, api, freya_mod, trace, origin

    def check(self, workflow: dict) -> dict:
        from builder.template_cc.vendor_runner import point_at
        p = policy_check.complete(self.policy, self.api)
        tests = build_suite(p, workflow)
        saved = self.fm.save_workflow(self.slot, point_at(workflow, self.origin), state_file=str(REPO / ".freya-agents.json"))
        ids = sync(self.fm, saved["workflow_id"], tests)
        t0 = time.time()
        results = run(self.fm, saved["workflow_id"], tests, ids, mock=mocks(p, tests[0]["product"]) if tests else None)
        passed = sum(r["outcome"] == "pass" for r in results)
        self.trace.selftest(f"unit tests: {passed}/{len(results)} passed in {time.time() - t0:.0f}s on {self.slot}")
        for r in results:
            if r["outcome"] != "pass":
                self.trace.line(f"  unit FAIL {r['name']}: {r.get('reason', '')[:160]}")
        return {"passed": passed, "total": len(results), "results": results}

    @staticmethod
    def regressions(best: dict, cand: dict) -> list[str]:
        ok = {r["name"] for r in best.get("results") or [] if r["outcome"] == "pass"}
        return sorted(r["name"] for r in cand.get("results") or [] if r["name"] in ok and r["outcome"] != "pass")
