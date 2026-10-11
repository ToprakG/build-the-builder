"""Freya unit tests written by the builder: one scripted turn, checked on the real engine.

The builder writes `out/unit_tests.json` in a simple format (reference/unit-tests.md):

    {"name": "refusal moves to the next proposal", "rule": "R31",
     "start_node": "offer", "variables": {"offer_text": "..."},
     "messages": [{"role": "assistant", "text": "Could you pay 412.50 by Wednesday?"}, {"role": "user", "text": "No."}],
     "expect": "The agent does not repeat the same proposal.",
     "checks": [{"node": "next_offer"}, {"tool": "store_record", "absent": true},
                {"tool": "check_party", "body": {"field": "member_code"}}, {"var": "offer_answer", "equals": "refused"}],
     "mock": {"check_party": {"ok": true, "verified": false}}}

`validate` checks a suite offline against the workflow and the tool API. `to_freya` converts one test to the
platform's unit-test format (the last assistant message carries the node and a variable snapshot, so the run starts
mid-call). `sync_and_run` creates, updates or archives the tests on a workflow, runs them and reads the verdicts,
through the kit's `freya.api` (the unit-test routes are not wrapped in the kit's freya.py).
"""

from __future__ import annotations

import hashlib
import json
import time

TAG = "agentic"
CHECK_KEYS = {"tool", "node", "var"}


def _nodes(wf: dict) -> set:
    return set(wf.get("nodes") or {}) | set(wf.get("global_nodes") or {})


def check_templates(tests: list, required: list[dict]) -> list[dict]:
    """Every required template has a test that keeps all the template's checks (the builder may add, not remove)."""
    errors = []
    by_template = {t.get("template"): t for t in tests if isinstance(t, dict) and t.get("template")}
    for r in required:
        t = by_template.get(r["template"])
        if t is None:
            errors.append({"test": r["template"], "msg": f"required template {r['template']!r} has no test (see policy_unit_suite)"})
            continue
        have = [json.dumps(c, sort_keys=True) for c in t.get("checks") or []]
        lost = [c for c in r["checks"] if json.dumps(c, sort_keys=True) not in have]
        if lost:
            errors.append({"test": t.get("name") or r["template"], "msg": f"keep the template's checks: missing {lost}"})
    return errors


def validate(tests, wf: dict, api_summary: dict, required: list[dict] | None = None) -> dict:
    """{errors, warnings}. Errors make a test unusable; the suite is still run without them."""
    errors, warnings = [], []
    if isinstance(tests, dict):
        tests = tests.get("tests")
    if not isinstance(tests, list) or not tests:
        return {"errors": [{"test": None, "msg": "unit_tests.json must be a non-empty list of tests"}], "warnings": []}
    nodes, tools = _nodes(wf), set(api_summary["tools"])
    names = set()
    for i, t in enumerate(tests):
        name = t.get("name") if isinstance(t, dict) else None
        label = name or f"#{i}"
        if not isinstance(t, dict) or not name:
            errors.append({"test": label, "msg": "a test needs a name"})
            continue
        if name in names:
            errors.append({"test": label, "msg": "duplicate name"})
        names.add(name)
        if t.get("start_node") not in nodes:
            errors.append({"test": label, "msg": f"start_node {t.get('start_node')!r} is not a node of the workflow"})
        msgs = t.get("messages") or []
        if not msgs or not all(isinstance(m, dict) and m.get("role") in ("assistant", "user") and str(m.get("text", "")).strip() for m in msgs):
            errors.append({"test": label, "msg": "messages: a list of {role: assistant|user, text}"})
        elif msgs[-1]["role"] != "user":
            errors.append({"test": label, "msg": "the last message must be the customer's (the test checks the agent's next turn)"})
        elif not any(m["role"] == "assistant" for m in msgs):
            errors.append({"test": label, "msg": "at least one assistant message before the customer's line"})
        checks = t.get("checks") or []
        if not checks and not str(t.get("expect") or "").strip():
            errors.append({"test": label, "msg": "give checks, an expect sentence, or both"})
        for c in checks:
            if not isinstance(c, dict) or len(CHECK_KEYS & set(c)) != 1:
                errors.append({"test": label, "msg": f"each check has exactly one of tool / node / var: {c}"})
                continue
            if "tool" in c:
                if c["tool"] not in tools and c["tool"] != "end_call":
                    errors.append({"test": label, "msg": f"tool {c['tool']!r} is not in the tool API"})
                body = c.get("body")
                if body is not None:
                    fields = api_summary["tools"].get(c["tool"], {}).get("request", {})
                    bad = [k for k in body if k not in fields]
                    if bad:
                        errors.append({"test": label, "msg": f"{c['tool']} body fields not in the API: {bad}"})
            elif "node" in c and c["node"] not in nodes:
                errors.append({"test": label, "msg": f"node {c['node']!r} is not a node of the workflow"})
            elif "var" in c and "equals" not in c:
                errors.append({"test": label, "msg": f"var check {c['var']!r} needs 'equals'"})
        for tool_name in (t.get("mock") or {}):
            if tool_name not in tools:
                errors.append({"test": label, "msg": f"mock for {tool_name!r}, which is not in the tool API"})
        if not t.get("rule"):
            warnings.append({"test": label, "msg": "no rule id: say which rules.json rule this test covers"})
    if required:
        errors += check_templates(tests, required)
    return {"errors": errors, "warnings": warnings, "tests": len(tests)}


def render(report: dict) -> str:
    lines = [f"{len(report['errors'])} error(s), {len(report['warnings'])} warning(s); {report.get('tests', 0)} tests"]
    lines += [f"ERROR {e['test']}: {e['msg']}" for e in report["errors"][:40]]
    lines += [f"WARNING {w['test']}: {w['msg']}" for w in report["warnings"][:20]]
    return "\n".join(lines)


def _speak(role, text, node=None, variables=None):
    row = {"role": role, "message": text}
    if node:
        row["nodeId"] = node
        row["messageId"] = "m-" + hashlib.sha1(f"{role}|{node}|{text}".encode()).hexdigest()[:12]
        row["workflowSnapshot"] = {
            "currentNode": node, "nodeTurnCount": 1, "totalTurnCount": 2, "transitionHistory": [], "enteredNodes": [node],
            "variableTable": {k: {"value": v, "confidence": 1, "source": "tool_result", "source_node": node}
                              for k, v in (variables or {}).items()}}
    return row


def _assertion(c: dict) -> dict:
    if "tool" in c:
        row = {"type": "tool_call", "tool": c["tool"]}
        if c.get("absent"):
            row["negate"] = True
        if c.get("body") is not None:
            row["args"] = {"body": c["body"]}
            row["argsMatch"] = "subset"
        return row
    if "node" in c:
        row = {"type": "routing", "signal": "node_entered", "target": c["node"]}
        if c.get("absent"):
            row["negate"] = True
        return row
    return {"type": "extraction", "name": c["var"], "match": "equals", "value": c["equals"]}


def to_freya(test: dict, customer_variables: dict) -> dict:
    """The platform's unit-test shape. The last assistant message starts the run in `start_node`."""
    msgs = test["messages"]
    last_assistant = max(i for i, m in enumerate(msgs) if m["role"] == "assistant")
    messages = [_speak(m["role"], m["text"], test["start_node"], test.get("variables") or {}) if i == last_assistant
                else _speak(m["role"], m["text"]) for i, m in enumerate(msgs)]
    row = {"name": test["name"], "expectation": test.get("expect") or "", "assertions": [_assertion(c) for c in test.get("checks") or []],
           "messages": messages, "customer_variables": customer_variables, "tags": [TAG, *(test.get("tags") or [])]}
    if test.get("mock"):
        row["mock"] = {k: {"status": "success", "data": v, "message": "ok", "status_code": 200} for k, v in test["mock"].items()}
    return row


# --- the platform routes (through the kit's freya.api) --------------------------------------------------------------


def _body(freya, wid: str, t: dict, create: bool) -> dict:
    body = {"name": t["name"], "expectation": t["expectation"], "assertions": t["assertions"], "tags": t["tags"],
            "input": {"schemaVersion": 1, "callType": "web", "messages": t["messages"], "customerVariables": t["customer_variables"]},
            "judgeContext": {"includeRoutingEvents": True, "includeExtractionEvents": True, "includeToolCalls": True,
                             "includeOtherEvents": False}}
    if create:
        body.update(workspaceId=freya.workspace_id(), entityType="workflow", entityId=wid)
    else:
        body["isArchived"] = False
    return body


def _list(freya, wid: str) -> list:
    rows, offset = [], 0
    while True:
        page = freya.api("GET", "/api/v2/unit-tests", workspaceId=freya.workspace_id(), entityType="workflow", entityId=wid,
                         includeArchived="true", limit=200, offset=offset)
        items = page.get("unitTests") or page.get("items") or (page if isinstance(page, list) else [])
        rows += items
        if len(items) < 200:
            return rows
        offset += 200


def sync(freya, wid: str, tests: list[dict]) -> dict:
    """Create or update every test by name; archive our own tests that the suite no longer has. Returns name -> id."""
    existing = {r["name"]: r for r in _list(freya, wid)}
    ids = {}
    for t in tests:
        cur = existing.get(t["name"])
        if cur is None:
            ids[t["name"]] = freya.api("POST", "/api/v2/unit-tests", _body(freya, wid, t, True))["id"]
        else:
            freya.api("PATCH", f"/api/v2/unit-tests/{cur['id']}", _body(freya, wid, t, False))
            ids[t["name"]] = cur["id"]
    wanted = {t["name"] for t in tests}
    for name, row in existing.items():
        if name not in wanted and TAG in (row.get("tags") or []) and not row.get("isArchived"):
            freya.api("PATCH", f"/api/v2/unit-tests/{row['id']}", {"isArchived": True})
    return ids


def run(freya, wid: str, tests: list[dict], ids: dict, timeout_s: float = 240) -> list[dict]:
    """Run-all, grouped by mock map (one mock map applies to a whole run-all call); poll the verdicts."""
    groups = {}
    for t in tests:
        groups.setdefault(json.dumps(t.get("mock") or {}, sort_keys=True), []).append(t)
    results = []
    for raw, group in groups.items():
        body = {"workspaceId": freya.workspace_id(), "entityType": "workflow", "entityId": wid,
                "unitTestIds": [ids[t["name"]] for t in group]}
        if json.loads(raw):
            body["mockToolResults"] = json.loads(raw)
        enq = freya.api("POST", "/api/v2/unit-tests/run-all", body)
        by_test = {e["unitTestId"]: e.get("runIds") or [] for e in enq.get("enqueued") or []}
        pending = {r for runs in by_test.values() for r in runs}
        done, end = {}, time.time() + timeout_s
        while pending and time.time() < end:
            for rid in list(pending):
                row = freya.api("GET", f"/api/v2/unit-tests/runs/{rid}")
                if row.get("outcome") in ("pass", "fail", "error"):
                    done[rid] = row
                    pending.discard(rid)
            if pending:
                time.sleep(3)
        for t in group:
            runs = [done[r] for r in by_test.get(ids[t["name"]], []) if r in done]
            if not runs:
                results.append({"name": t["name"], "outcome": "error", "reason": "not run or timed out"})
                continue
            r = runs[-1]
            results.append({"name": t["name"], "outcome": r.get("outcome"), "reason": r.get("verdictReason") or "",
                            "agent": (r.get("agentResponse") or "")[:400],
                            "failed_checks": [a for a in r.get("assertionResults") or [] if a.get("passed") is False]})
    return results


def sync_and_run(freya, wid: str, suite: list[dict], customer_variables: dict) -> list[dict]:
    tests = [to_freya(t, customer_variables) for t in suite]
    return run(freya, wid, tests, sync(freya, wid, tests))


def summary(results: list[dict]) -> dict:
    return {"passed": sum(r["outcome"] == "pass" for r in results), "failed": sum(r["outcome"] == "fail" for r in results),
            "errors": sum(r["outcome"] == "error" for r in results), "total": len(results)}
