"""A local copy of the seven bank tools, plus the practice session and ledger routes, for ANY
brief: which field each tool uses, how identity and closing are shaped, and the business-day-free
bookkeeping rules are all read from a config (the same canonical schema as seed/policy_ab.py and
testbed/*/config.json). The response text matches the hosted bank for Brief A/B's own field names
(the default config); for a fake or final brief, request/response bodies use that brief's own
field names instead, read straight from its tools.openapi.json at /tools/openapi.json.

The ledger endpoint always reports promises/notes/callbacks/transfers/outcome/reason in the kit's
own canonical key names (product_id/amount/date, product_id/reason, date/time, queue,
outcome/reason) -- only the VALUES are the brief's own (a note reason of "contested" for f3, a
close code of "10-PTP" for f7) -- so tools/score.py's score_ledger works unchanged against any
brief's ledger, exactly as it does against the hosted bank's.

A case fault {"tool": "create_promise", "fail_first_n": 1} makes the first N calls to that tool
return HTTP 500, same shape as the kit's.

    python3 tools/driver/mockbank.py                        # serves public-test-set.json, Brief A/B field names
    python3 tools/driver/mockbank.py --brief f3-kestrel      # serves that fake brief's cases.json + config.json
    python3 tools/driver/mockbank.py --self-test             # the kit-shape behavioural check (unchanged)
    python3 tools/driver/mockbank.py --self-test-testbed     # replays every testbed case's expected records; wants 510/510
"""
import hashlib, json, os, re, secrets, sys, threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from seed.policy_ab import POLICY_A as DEFAULT_CONFIG

TESTBED = ROOT / "testbed"
TOOLS = ("verify_identity", "get_account", "create_promise", "add_note", "schedule_callback", "transfer_to_agent", "close_call")
DATE_ERROR = "answer is not a date. Send it as YYYY-MM-DD."
CLOSED = "the call is already closed"
NOT_VERIFIED = "identity not verified"


def _num(value):
    number = float(value)
    return int(number) if number.is_integer() else number


def _products(case, config):
    """Case products already carry the brief's own field names (testbed/generator/cases.py
    writes them that way): a card's floor field is "min_due" in the case dict for a brief whose
    config says so. Only product_id is missing from the case and is assigned here, positionally,
    in the brief's own id format and field name."""
    af = config["account_fields"]
    fmt = config.get("product_id_format", "p{n}")
    rows = []
    for index, product in enumerate(case.get("products") or [], 1):
        row = {af["product_id"]: fmt.format(n=index)}
        for key, value in product.items():
            row[key] = _num(value) if isinstance(value, (int, float)) else value
        rows.append(row)
    return rows


def _classify_date(answer, expected):
    """Return ok, swap, wrong, or bad. A swapped day and month is not a wrong answer."""
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", (answer or "").strip())
    if not match:
        return "bad"
    year, month, day = (int(part) for part in match.groups())
    try:
        sent = date(year, month, day)
    except ValueError:
        sent = None
    try:
        swapped = date(year, day, month)
    except ValueError:
        swapped = None
    real = date.fromisoformat(expected)
    if sent == real:
        return "ok"
    if swapped == real:
        return "swap"
    if sent is None:
        return "bad"
    return "wrong"


class Bank:
    def __init__(self, cases, config=None):
        self.cases = {case["id"]: case for case in cases}
        self.config = config or DEFAULT_CONFIG
        self.sessions = {}
        self.lock = threading.Lock()

    def open_session(self, team, case_id):
        case = self.cases.get(case_id)
        if case is None:
            return None
        cfg = self.config
        customer_id = f"c_{secrets.token_hex(4)}-{secrets.token_hex(2)[:3]}"
        variables = {"customer_id": customer_id, **case["variables"]}
        faults = [{"tool": item["tool"], "left": int(item.get("fail_first_n") or 0)} for item in case.get("faults") or []]
        questions = cfg["identity"]["questions"]
        session = {
            "team": team,
            "case_id": case_id,
            "identity": dict(case["identity"]),
            "products": _products(case, cfg),
            "variables": variables,
            "verified": {q: False for q in questions},
            "wrong": 0,
            "hinted": False,
            "closed": False,
            "outcome": None,
            "reason": None,
            "promises": [],
            "notes": [],
            "callbacks": [],
            "transfers": [],
            "failures": {name: 0 for name in TOOLS},
            "calls": [],
            "faults": faults,
            "next_promise": 1,
        }
        with self.lock:
            self.sessions[customer_id] = session
        return session

    def session_for(self, customer_id):
        return self.sessions.get(customer_id)

    def ledger(self, team, customer_id):
        session = self.sessions.get(customer_id)
        if session is None or session["team"] != team:
            return None
        return {
            "ok": True,
            "case": session["case_id"],
            "team": hashlib.sha256(team.encode()).hexdigest()[:24],
            "now": session["variables"].get("now"),
            "verified": dict(session["verified"]),
            "wrong": session["wrong"],
            "hinted": session["hinted"],
            "promises": list(session["promises"]),
            "notes": list(session["notes"]),
            "callbacks": list(session["callbacks"]),
            "transfers": list(session["transfers"]),
            "outcome": session["outcome"],
            "reason": session["reason"],
            "failures": dict(session["failures"]),
            "calls": list(session["calls"]),
        }


def _args(body):
    return {key: value for key, value in body.items() if key != "customer_id"}


def _log(session, tool, args, status, result):
    session["calls"].append({"n": len(session["calls"]) + 1, "tool": tool, "args": args, "status": status, "result": result})
    if status >= 500:
        session["failures"][tool] = session["failures"].get(tool, 0) + 1


def _fault(session, tool, args):
    for fault in session["faults"]:
        if fault["tool"] == tool and fault["left"] > 0:
            fault["left"] -= 1
            result = {"ok": False, "error": "service unavailable, nothing was recorded"}
            _log(session, tool, args, 500, result)
            return 500, result
    return None


def _closed(session, tool, args):
    if not session["closed"]:
        return None
    result = {"ok": False, "error": CLOSED}
    _log(session, tool, args, 200, result)
    return 200, result


def verify_identity(session, body, config):
    vi = config["tools"]["verify_identity"]
    args = _args(body)
    hit = _fault(session, "verify_identity", args) or _closed(session, "verify_identity", args)
    if hit:
        return hit
    field = body.get(vi["field"])
    answer = "" if body.get(vi["answer"]) is None else str(body.get(vi["answer"]))
    questions = config["identity"]["questions"]
    max_wrong = config["identity"]["max_wrong"]
    swap_hint = bool(config["identity"].get("swap_hint"))
    if field not in questions:
        result = {"ok": False, "error": f"{vi['field']} must be one of: " + ", ".join(questions)}
        _log(session, "verify_identity", args, 200, result)
        return 200, result
    if session["wrong"] >= max_wrong:
        result = {"ok": False, "verified": False, "wrong_answers_left": 0, "error": "identity check is over"}
        _log(session, "verify_identity", args, 200, result)
        return 200, result
    # A field that already passed stays passed. A later wrong answer does not wipe it.
    if session["verified"][field]:
        result = {"ok": True, "verified": True, "wrong_answers_left": max(0, max_wrong - session["wrong"]), "all_verified": all(session["verified"].values())}
        _log(session, "verify_identity", args, 200, result)
        return 200, result
    if field == "date_of_birth":
        kind = _classify_date(answer, session["identity"]["date_of_birth"])
        if kind == "bad":
            result = {"ok": False, "error": DATE_ERROR}
            _log(session, "verify_identity", args, 200, result)
            return 200, result
        if kind == "swap" and swap_hint:
            session["hinted"] = True
            result = {"ok": True, "verified": False, "wrong_answers_left": max_wrong - session["wrong"], "hint": "day_month_swapped"}
            _log(session, "verify_identity", args, 200, result)
            return 200, result
        if kind == "ok":
            session["verified"]["date_of_birth"] = True
        else:
            session["wrong"] += 1
    else:
        expected = str(session["identity"].get(field) or "").strip().casefold()
        if answer.strip().casefold() == expected:
            session["verified"][field] = True
        else:
            session["wrong"] += 1
    result = {
        "ok": True,
        "verified": session["verified"][field],
        "wrong_answers_left": max(0, max_wrong - session["wrong"]),
        "all_verified": all(session["verified"].values()),
    }
    _log(session, "verify_identity", args, 200, result)
    return 200, result


def get_account(session, body, config):
    args = _args(body)
    hit = _fault(session, "get_account", args) or _closed(session, "get_account", args)
    if hit:
        return hit
    if not all(session["verified"].values()):
        result = {"ok": False, "error": NOT_VERIFIED}
    else:
        result = {"ok": True, "products": session["products"]}
    _log(session, "get_account", args, 200, result)
    return 200, result


def create_promise(session, body, config):
    cp = config["tools"]["create_promise"]
    af = config["account_fields"]
    args = _args(body)
    hit = _fault(session, "create_promise", args) or _closed(session, "create_promise", args)
    if hit:
        return hit
    pid = body.get(cp["product_id"])
    if not all(session["verified"].values()):
        result = {"ok": False, "error": NOT_VERIFIED}
    elif not any(p[af["product_id"]] == pid for p in session["products"]):
        result = {"ok": False, "error": f"unknown {cp['product_id']}"}
    elif any(item["product_id"] == pid for item in session["promises"]):
        result = {"ok": False, "error": "this product already has a promise"}
    else:
        promise_id = f"pr{session['next_promise']}"
        session["next_promise"] += 1
        product = next(p for p in session["products"] if p[af["product_id"]] == pid)
        amount_raw = body.get(cp["amount"])
        amount = (float(amount_raw) / 100.0) if cp.get("amount_unit") == "cents" and amount_raw is not None else _num(amount_raw)
        session["promises"].append({
            "promise_id": promise_id,
            "product_id": pid,
            "type": product.get(af["type"]),
            "amount": amount,
            "date": body.get(cp["date"]),
        })
        result = {"ok": True, "promise_id": promise_id}
    _log(session, "create_promise", args, 200, result)
    return 200, result


def add_note(session, body, config):
    an = config["tools"]["add_note"]
    af = config["account_fields"]
    args = _args(body)
    hit = _fault(session, "add_note", args) or _closed(session, "add_note", args)
    if hit:
        return hit
    pid = body.get(an["product_id"])
    if not all(session["verified"].values()):
        result = {"ok": False, "error": NOT_VERIFIED}
    elif not any(p[af["product_id"]] == pid for p in session["products"]):
        result = {"ok": False, "error": f"unknown {an['product_id']}"}
    else:
        note = {"product_id": pid, "reason": body.get(an["reason"])}
        if body.get("text"):
            note["text"] = body["text"]
        session["notes"].append(note)
        result = {"ok": True}
    _log(session, "add_note", args, 200, result)
    return 200, result


def schedule_callback(session, body, config):
    sc = config["tools"]["schedule_callback"]
    args = _args(body)
    hit = _fault(session, "schedule_callback", args) or _closed(session, "schedule_callback", args)
    if hit:
        return hit
    if sc["mode"] == "datetime":
        combined = str(body.get(sc["datetime"]) or "")
        parts = combined.split("T", 1)
        day, time_ = parts[0], (parts[1][:5] if len(parts) > 1 else "")
    else:
        day, time_ = body.get(sc["date"]), body.get(sc["time"])
    session["callbacks"].append({"date": day, "time": time_})
    result = {"ok": True}
    _log(session, "schedule_callback", args, 200, result)
    return 200, result


def transfer_to_agent(session, body, config):
    tf = config["tools"]["transfer_to_agent"]
    args = _args(body)
    hit = _fault(session, "transfer_to_agent", args) or _closed(session, "transfer_to_agent", args)
    if hit:
        return hit
    session["transfers"].append({"queue": body.get(tf["queue"])})
    result = {"ok": True}
    _log(session, "transfer_to_agent", args, 200, result)
    return 200, result


def close_call(session, body, config):
    cc = config["tools"]["close_call"]
    args = _args(body)
    hit = _fault(session, "close_call", args) or _closed(session, "close_call", args)
    if hit:
        return hit
    session["closed"] = True
    if cc["mode"] == "code":
        session["outcome"] = body.get(cc["code"])
        session["reason"] = None
    else:
        session["outcome"] = body.get(cc["outcome"])
        session["reason"] = body.get(cc["reason"]) or None
    result = {"ok": True}
    _log(session, "close_call", args, 200, result)
    return 200, result


HANDLERS = {
    "verify_identity": verify_identity,
    "get_account": get_account,
    "create_promise": create_promise,
    "add_note": add_note,
    "schedule_callback": schedule_callback,
    "transfer_to_agent": transfer_to_agent,
    "close_call": close_call,
}


def load_testbed(brief_id):
    """Resolve (cases, config) for 'brief-a', 'brief-b', or any fake_briefs id, e.g. 'f3-kestrel'."""
    if brief_id in ("brief-a", "brief-b"):
        cases = json.loads((TESTBED / "personas" / f"{brief_id}.cases.json").read_text())
        config = json.loads((TESTBED / "configs" / f"{brief_id}.config.json").read_text())
    else:
        d = TESTBED / "fake_briefs" / brief_id
        cases = json.loads((d / "cases.json").read_text())
        config = json.loads((d / "config.json").read_text())
    return cases, config


def _tool_api(config):
    os.environ.setdefault("KIT_DIR", str(ROOT))
    sys.path.insert(0, str(TESTBED / "generator"))
    import toolapi
    return toolapi.build(config)


def create_app(cases=None, cases_path=None, config=None, config_path=None):
    if config is None:
        config = json.loads(Path(config_path).read_text()) if config_path else DEFAULT_CONFIG
    if cases is None:
        path = Path(cases_path) if cases_path else ROOT / "public-test-set.json"
        cases = json.loads(Path(path).read_text())
    bank = Bank(cases, config)
    app = FastAPI()
    app.state.bank = bank

    def team_of(request):
        return request.headers.get("x-team-key") or ""

    @app.get("/tools/openapi.json")
    def tools_openapi():
        return _tool_api(bank.config)

    @app.post("/v1/practice/sessions")
    async def sessions(request: Request):
        team = team_of(request)
        if not team:
            return JSONResponse({"ok": False, "error": "team key required"}, status_code=401)
        body = await request.json()
        session = bank.open_session(team, (body or {}).get("case"))
        if session is None:
            return JSONResponse({"ok": False, "error": "unknown practice case"}, status_code=404)
        return {"ok": True, "variables": session["variables"]}

    @app.get("/v1/practice/ledger/{customer_id}")
    def ledger(customer_id: str, request: Request):
        team = team_of(request)
        if not team:
            return JSONResponse({"ok": False, "error": "team key required"}, status_code=401)
        found = bank.ledger(team, customer_id)
        if found is None:
            return JSONResponse({"ok": False, "error": "unknown customer_id for this team key"}, status_code=404)
        return found

    def route(name):
        async def endpoint(request: Request):
            body = await request.json()
            if not isinstance(body, dict):
                return JSONResponse({"ok": False, "error": "JSON object required"}, status_code=400)
            session = bank.session_for(body.get("customer_id"))
            if session is None:
                return JSONResponse({"ok": False, "error": "unknown customer_id"}, status_code=404)
            with bank.lock:
                status, result = HANDLERS[name](session, body, bank.config)
            return JSONResponse(result, status_code=status)
        endpoint.__name__ = name
        return endpoint

    for name in TOOLS:
        app.add_api_route(f"/v1/{name}", route(name), methods=["POST"])
    return app


def self_test():
    """Replay the hosted bank's observed answers. Exit 0 when the text matches."""
    from fastapi.testclient import TestClient
    client = TestClient(create_app())
    headers = {"x-team-key": "team-key-for-self-test"}
    opened = client.post("/v1/practice/sessions", json={"case": "PUB-01"}, headers=headers)
    assert opened.status_code == 200, opened.text
    customer = opened.json()["variables"]["customer_id"]

    def post(path, body):
        response = client.post(path, json={"customer_id": customer, **body})
        return response.status_code, response.json()

    assert post("/v1/verify_identity", {"field": "date_of_birth", "answer": "1990-01-01"})[1]["wrong_answers_left"] == 2
    code, body = post("/v1/verify_identity", {"field": "date_of_birth", "answer": "1988-14-03"})
    assert body.get("hint") == "day_month_swapped" and body["wrong_answers_left"] == 2, body
    assert post("/v1/verify_identity", {"field": "date_of_birth", "answer": "nope"})[1]["error"] == DATE_ERROR
    assert post("/v1/verify_identity", {"field": "date_of_birth", "answer": "1988-03-14"})[1]["all_verified"] is False
    assert post("/v1/get_account", {})[1]["error"] == NOT_VERIFIED
    assert post("/v1/verify_identity", {"field": "phone_last4", "answer": "4471"})[1]["all_verified"] is True
    assert post("/v1/get_account", {})[1]["products"][0]["product_id"] == "p1"
    assert post("/v1/create_promise", {"product_id": "p1", "amount": 420, "date": "2026-10-14"})[1]["promise_id"] == "pr1"
    assert post("/v1/create_promise", {"product_id": "p1", "amount": 10, "date": "2026-10-15"})[1]["error"] == "this product already has a promise"
    assert post("/v1/close_call", {"outcome": "promise"})[0] == 200
    assert post("/v1/add_note", {"product_id": "p1", "reason": "refused"})[1]["error"] == CLOSED

    opened = client.post("/v1/practice/sessions", json={"case": "PUB-10"}, headers=headers)
    customer = opened.json()["variables"]["customer_id"]
    post = lambda path, body, customer=customer: client.post(path, json={"customer_id": customer, **body})
    assert post("/v1/verify_identity", {"field": "date_of_birth", "answer": "1989-02-23"}).json()["verified"] is True
    assert post("/v1/verify_identity", {"field": "phone_last4", "answer": "9904"}).json()["all_verified"] is True
    failed = post("/v1/create_promise", {"product_id": "p1", "amount": 365, "date": "2026-10-16"})
    assert failed.status_code == 500 and failed.json()["error"] == "service unavailable, nothing was recorded"
    assert post("/v1/create_promise", {"product_id": "p1", "amount": 365, "date": "2026-10-16"}).status_code == 200
    missing = client.get("/v1/practice/ledger/c_missing", headers=headers)
    assert missing.status_code == 404
    unknown = client.post("/v1/practice/sessions", json={"case": "NOPE"}, headers=headers)
    assert unknown.status_code == 404 and unknown.json()["error"] == "unknown practice case"
    print("self-test passed")


def _replay_expected(client, headers, case, config):
    """Replay case["expected"] as a perfect run: verify identity for real (so get_account-gated
    calls succeed), then one tool call per expected promise/note/callback/transfer, then
    close_call. Skips verify_identity/get_account entirely for a not-reached case, since the
    scorer forbids using them there.

    A "Tool failure" family case carries a fault (fail_first_n: 1 or 2) on verify_identity or
    create_promise; a perfect run retries the same call until it stops 500ing, same as the brief's
    own "if it fails, try once more" rule -- so this replay does too, capped well above the
    highest fail_first_n seen in the testbed (2)."""
    opened = client.post("/v1/practice/sessions", json={"case": case["id"]}, headers=headers)
    assert opened.status_code == 200, opened.text
    customer_id = opened.json()["variables"]["customer_id"]
    exp = case["expected"]
    oracle_key = (case.get("oracle") or {}).get("outcome_key", "")

    def post(path, body):
        response = client.post(path, json={"customer_id": customer_id, **body})
        for _ in range(4):
            if response.status_code != 500:
                break
            response = client.post(path, json={"customer_id": customer_id, **body})
        return response

    if not oracle_key.startswith("nr_"):
        vi = config["tools"]["verify_identity"]
        for q in config["identity"]["questions"]:
            post("/v1/verify_identity", {vi["field"]: q, vi["answer"]: case["identity"][q]})
        cp = config["tools"]["create_promise"]
        for p in exp["promises"]:
            amount = int(round(p["amount"] * 100)) if cp.get("amount_unit") == "cents" else p["amount"]
            post("/v1/create_promise", {cp["product_id"]: p["product_id"], cp["amount"]: amount, cp["date"]: p["date"]})
        an = config["tools"]["add_note"]
        for n in exp["notes"]:
            post("/v1/add_note", {an["product_id"]: n["product_id"], an["reason"]: n["reason"]})
        tf = config["tools"]["transfer_to_agent"]
        for tr in exp["transfers"]:
            post("/v1/transfer_to_agent", {tf["queue"]: tr["queue"]})
    sc = config["tools"]["schedule_callback"]
    for cb in exp["callbacks"]:
        body = {sc["datetime"]: f"{cb['date']}T{cb['time']}"} if sc["mode"] == "datetime" else {sc["date"]: cb["date"], sc["time"]: cb["time"]}
        post("/v1/schedule_callback", body)
    cc = config["tools"]["close_call"]
    close_body = {cc["code"]: exp["outcome"]} if cc["mode"] == "code" else {cc["outcome"]: exp["outcome"], cc["reason"]: exp.get("reason") or ""}
    post("/v1/close_call", close_body)
    return client.get(f"/v1/practice/ledger/{customer_id}", headers=headers).json()


def self_test_testbed():
    """Every testbed case's expected records, replayed as real HTTP calls against this server and
    scored with the kit's own tools/score.py (unchanged). Wants 510/510, same bar as
    testbed/scoring/score_local.py's in-memory self-test, but end to end over the wire."""
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(ROOT / "tools"))
    from score import score_ledger

    sets = [("brief-a", 100), ("brief-b", 60)]
    sets += [(d.name, 50) for d in sorted((TESTBED / "fake_briefs").iterdir())]
    total = failed_total = 0
    for brief_id, expected_count in sets:
        cases, config = load_testbed(brief_id)
        assert len(cases) == expected_count, f"{brief_id}: expected {expected_count} cases, found {len(cases)}"
        client = TestClient(create_app(cases=cases, config=config))
        headers = {"x-team-key": f"team-key-for-{brief_id}"}
        fails = []
        for case in cases:
            ledger = _replay_expected(client, headers, case, config)
            result = score_ledger(case, ledger)
            total += 1
            if not result["pass"]:
                failed_total += 1
                fails.append((case["id"], result["failed"]))
        status = "OK" if not fails else f"{len(fails)} FAILED"
        print(f"{brief_id:16} {len(cases) - len(fails):3}/{len(cases):<3} {status}")
        for case_id, reasons in fails[:5]:
            print("   -", case_id, reasons)
    print(f"\n{total - failed_total} / {total} perfect replays scored as a pass")
    return failed_total == 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    elif "--self-test-testbed" in sys.argv:
        ok = self_test_testbed()
        sys.exit(0 if ok else 1)
    else:
        import uvicorn
        cases = config = None
        if "--brief" in sys.argv:
            cases, config = load_testbed(sys.argv[sys.argv.index("--brief") + 1])
        port = int(os.environ.get("PORT", "8787"))
        uvicorn.run(create_app(cases=cases, config=config), host="127.0.0.1", port=port)
