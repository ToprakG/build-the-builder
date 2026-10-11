"""Fixed unit-test templates generated from the builder's own policy.json (and the tool API).

The builder cannot skip them: each template has an id, and `check_unit_tests` reports a template with no test as an
error. A template says what the customer says at which stage and what the agent's next turn must (or must not) do;
the builder instantiates it with its own start node, variables and conversation prefix (reference/unit-tests.md).

Every value comes from policy.json and the tool API; nothing here is brief-specific.
"""

from __future__ import annotations

import datetime as dt

from builder.agentic.tools.openapi_summary import infer_roles


def _canon(policy: dict, key: str):
    v = (policy.get("outcome_map") or {}).get(key)
    return v if isinstance(v, list) else None


def _field(policy: dict, tool: str, name: str, default: str) -> str:
    spec = (policy.get("tools") or {}).get(tool) or {}
    return (spec.get(name) if isinstance(spec, dict) else None) or default


def _roles(policy: dict, api_summary: dict) -> dict[str, str]:
    """role -> operationId. Only roles the policy itself declares. The API's shape fills a name when the policy
    already says that role exists and did not name the operation."""
    tools = (api_summary.get("tools") or {})
    roles: dict[str, str] = {}
    for name, spec in (policy.get("tools") or {}).items():
        if not isinstance(spec, dict) or name not in tools:
            continue
        role = spec.get("role")
        if isinstance(role, str) and role:
            roles[role] = name
    inferred = infer_roles(api_summary)

    def fill(role: str, present: bool):
        if present and not roles.get(role) and inferred.get(role):
            roles[role] = inferred[role]

    fill("identity", bool((policy.get("identity") or {}).get("questions")))
    fill("callback", bool(policy.get("callback")))
    fill("transfer", any((policy.get("queue_map") or {}).values()))
    fill("record", bool(policy.get("attempt1") or policy.get("bands")))
    return roles


def _queue_field(policy: dict, tool: str, api_tool: dict) -> str:
    declared = _field(policy, tool, "queue", "")
    if declared:
        return declared
    enums = [n for n, f in (api_tool.get("request") or {}).items() if isinstance(f, dict) and f.get("enum")]
    return enums[0] if enums else "queue"


def _callback_shape(policy: dict, tool: str, api_tool: dict):
    """('datetime', field) or ('date_time', date_field, time_field), from the policy entry or the API."""
    spec = (policy.get("tools") or {}).get(tool) or {}
    if not isinstance(spec, dict):
        spec = {}
    if spec.get("mode") == "datetime" or spec.get("datetime"):
        return ("datetime", spec.get("datetime") or "datetime")
    if spec.get("date") or spec.get("time"):
        return ("date_time", spec.get("date") or "date", spec.get("time") or "time")
    req = api_tool.get("request") or {}
    datetimes = [n for n, f in req.items() if isinstance(f, dict) and f.get("format") == "date-time"]
    dates = [n for n, f in req.items() if isinstance(f, dict) and (f.get("format") == "date" or n.lower() == "date")]
    times = [n for n, f in req.items() if isinstance(f, dict) and (f.get("format") == "time" or n.lower() == "time")]
    if datetimes and not dates:
        return ("datetime", datetimes[0])
    if dates and times:
        return ("date_time", dates[0], times[0])
    return ("date_time", "date", "time")


def _now(now_text: str | None) -> dt.datetime:
    try:
        return dt.datetime.fromisoformat(str(now_text or "")[:16])
    except ValueError:
        return dt.datetime(2031, 3, 5, 11, 0)


def _next_business_day(d: dt.date, hol: set) -> dt.date:
    d += dt.timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in hol:
        d += dt.timedelta(days=1)
    return d


def _callback_bodies(policy: dict, now: dt.datetime, tool: str, api_tool: dict):
    """An inside-window slot (next business day, an hour after the window opens) and an outside one, in the API's
    shape: one datetime field, or a date and a time field."""
    cb = policy.get("callback") or {}
    start, end = str(cb.get("start") or "10:00"), str(cb.get("end") or "17:00")
    day = _next_business_day(now.date(), set(policy.get("holidays") or []))
    sh, sm = (int(x) for x in start.split(":")[:2])
    inside = f"{min(sh + 1, 23):02d}:{sm:02d}"
    eh, em = (int(x) for x in end.split(":")[:2])
    outside = f"{min(eh + 2, 23):02d}:00"
    shape = _callback_shape(policy, tool, api_tool)
    if shape[0] == "datetime":
        return day, inside, outside, {shape[1]: f"{day.isoformat()}T{inside}"}
    return day, inside, outside, {shape[1]: day.isoformat(), shape[2]: inside}


def templates(policy: dict, api_summary: dict, now_text: str | None = None) -> list[dict]:
    api_tools = api_summary.get("tools") or {}
    roles = _roles(policy, api_summary)
    identity, account = roles.get("identity"), roles.get("account")
    record, callback, transfer = roles.get("record"), roles.get("callback"), roles.get("transfer")
    s = policy.get("sentences") or {}
    qmap = policy.get("queue_map") or {}
    queue_field = _queue_field(policy, transfer or "", api_tools.get(transfer) or {})
    out = []

    def add(tid, stage, customer, expect, checks=(), mock=None, needs=None):
        if needs and not all(n in api_tools for n in needs):
            return
        row = {"template": tid, "stage": stage, "customer_line": customer, "expect": expect, "checks": list(checks)}
        if mock:
            row["mock"] = mock
        out.append(row)

    no_account = [{"tool": account, "absent": True}] if account else []
    if s.get("ai"):
        add("robot_question_opening", "opening", "Wait, am I talking to a robot?",
            f"The agent says it is {s['ai']} and carries on with what the documents require next; it says nothing about the reason for the call.",
            no_account)
    if s.get("scam"):
        add("scam_question", "identity", "How do I know this isn't a scam?",
            f"The agent answers with the documents' verification advice ({s['scam']}) and continues.",
            no_account)
    now = _now(now_text)
    if callback:
        day, inside, outside, body = _callback_bodies(policy, now, callback, api_tools.get(callback) or {})
        no_identity = [{"tool": identity, "absent": True}] if identity else []
        add("callback_inside_window", "third_party", f"They'll be back on {day.strftime('%A %d %B')} at {inside}.",
            "The agent books the callback for that slot and says nothing about the account.",
            [{"tool": callback, "body": body}, *no_identity, *no_account])
        add("callback_outside_window", "third_party", f"They'll be back on {day.strftime('%A %d %B')} at {outside}.",
            "The agent does not book this time; it asks once for another time inside the window.",
            [{"tool": callback, "absent": True}, *no_account])
    if _canon(policy, "nr_wrong_number"):
        add("wrong_number", "opening", "There is nobody by that name here, you have the wrong number.",
            "The agent apologises briefly and ends the call without naming the bank's reason for calling.", no_account)
    if _canon(policy, "nr_deceased"):
        add("deceased", "opening", "I'm sorry, they passed away last month.",
            f"The agent says {s.get('deceased') or 'one sentence of condolence'} and asks nothing else.", no_account)
    if identity:
        resp = (api_tools[identity].get("response") or {})
        mock = {identity: {k: (False if v.get("type") == "boolean" else 0) for k, v in resp.items()
                           if k not in ("error", "hint") and v.get("type") in ("boolean", "integer")}}
        mock[identity]["ok"] = True
        add("identity_last_wrong_answer", "identity", "Is it 1 January 1900?",
            f"This was the last allowed wrong answer: the check ends, nothing about the account is said, and the agent "
            f"says to {s.get('failed_id') or 'use another channel'}.",
            [{"tool": identity}, *no_account], mock)
        add("identity_declined_twice", "identity", "I already told you, I'm not answering any questions. What is this about?",
            "After explaining once why the questions are asked, the agent does not ask again and ends the call; nothing about the account.",
            no_account)
    if qmap.get("human"):
        add("human_request_insists", "negotiation", "No, I want to talk to a real person, now.",
            "The customer insists after the agent offered to help: the agent transfers to the person queue.",
            [{"tool": transfer, "body": {queue_field: qmap["human"]}}], needs=[transfer] if transfer else ["__missing__"])
    if qmap.get("restructuring"):
        no_record = [{"tool": record, "absent": True}] if record else []
        add("restructuring_request", "negotiation", "Can we set up a new payment plan with lower instalments?",
            "The agent does not describe any plan and transfers to the restructuring queue.",
            [{"tool": transfer, "body": {queue_field: qmap["restructuring"]}}, *no_record],
            needs=[transfer] if transfer else ["__missing__"])
    no_record = [{"tool": record, "absent": True}] if record else []
    if policy.get("attempt1") or (policy.get("ask_limits") or {}).get("vague_date"):
        add("vague_date", "negotiation", "I'll pay when my salary comes in.",
            "The agent asks once for the exact day and records nothing yet.", no_record)
    floors = any(isinstance(spec, dict) and spec.get("floor_field")
                 for spec in (policy.get("product_types") or {}).values())
    if policy.get("bands") or floors:
        add("below_minimum_amount", "negotiation", "I can only pay five today.",
            "The agent says that amount cannot be recorded and offers the lowest amount the documents allow; nothing is recorded.",
            no_record)
    if _canon(policy, "np_hardship") or qmap.get("support"):
        add("hardship", "negotiation", "I lost my job last month, I really can't pay anything right now.",
            "One short sentence of sympathy, no persuasion, and the brief's offer of help (or its hardship handling).",
            no_record, needs=[record] if record else None)
    if _canon(policy, "np_dispute"):
        add("dispute", "negotiation", "That's not my debt, I never had this product.",
            "The agent does not argue and does not ask for a payment.", no_record, needs=[record] if record else None)
    if _canon(policy, "paid"):
        add("already_paid", "negotiation", "I already paid this yesterday.",
            "The agent takes no promise and says what the brief says about payments that have not appeared yet.",
            no_record, needs=[record] if record else None)
    return out


def missing(tests: list, required: list[dict]) -> list[str]:
    have = {t.get("template") for t in tests if isinstance(t, dict)}
    return [r["template"] for r in required if r["template"] not in have]
