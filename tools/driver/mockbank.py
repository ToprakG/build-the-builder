"""A local copy of the seven bank tools, plus the practice session and ledger routes.

The response text matches the hosted bank. score.py reads GET /v1/practice/ledger/<customer_id>.
Cases come from a JSON file in the public-test-set shape. A case fault
{"tool": "create_promise", "fail_first_n": 1} makes the first N calls return HTTP 500.

    python3 tools/driver/mockbank.py
    python3 tools/driver/mockbank.py --self-test
"""
import hashlib, json, os, re, secrets, threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ("verify_identity", "get_account", "create_promise", "add_note", "schedule_callback", "transfer_to_agent", "close_call")
DATE_ERROR = "answer is not a date. Send it as YYYY-MM-DD."
CLOSED = "the call is already closed"
NOT_VERIFIED = "identity not verified"

def _num(value):
    number = float(value)
    return int(number) if number.is_integer() else number

def _products(case):
    rows = []
    for index, product in enumerate(case.get("products") or [], 1):
        row = {"product_id": f"p{index}", "type": product["type"], "past_due": _num(product["past_due"]), "days_late": int(product["days_late"])}
        if product.get("card_last4"):
            row["card_last4"] = product["card_last4"]
        if "minimum_payment" in product:
            row["minimum_payment"] = _num(product["minimum_payment"])
        if "instalment_amount" in product:
            row["instalment_amount"] = _num(product["instalment_amount"])
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
    def __init__(self, cases):
        self.cases = {case["id"]: case for case in cases}
        self.sessions = {}
        self.lock = threading.Lock()

    def open_session(self, team, case_id):
        case = self.cases.get(case_id)
        if case is None:
            return None
        customer_id = f"c_{secrets.token_hex(4)}-{secrets.token_hex(2)[:3]}"
        variables = {"customer_id": customer_id, **case["variables"]}
        faults = [{"tool": item["tool"], "left": int(item.get("fail_first_n") or 0)} for item in case.get("faults") or []]
        session = {
            "team": team,
            "case_id": case_id,
            "identity": dict(case["identity"]),
            "products": _products(case),
            "variables": variables,
            "verified": {"date_of_birth": False, "phone_last4": False},
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

def verify_identity(session, body):
    args = _args(body)
    hit = _fault(session, "verify_identity", args) or _closed(session, "verify_identity", args)
    if hit:
        return hit
    field, answer = body.get("field"), "" if body.get("answer") is None else str(body.get("answer"))
    if field not in ("date_of_birth", "phone_last4"):
        result = {"ok": False, "error": "field must be date_of_birth or phone_last4"}
        _log(session, "verify_identity", args, 200, result)
        return 200, result
    if session["wrong"] >= 3:
        result = {"ok": False, "verified": False, "wrong_answers_left": 0, "error": "identity check is over"}
        _log(session, "verify_identity", args, 200, result)
        return 200, result
    # A field that already passed stays passed. A later wrong answer does not wipe it.
    if session["verified"][field]:
        result = {"ok": True, "verified": True, "wrong_answers_left": max(0, 3 - session["wrong"]), "all_verified": all(session["verified"].values())}
        _log(session, "verify_identity", args, 200, result)
        return 200, result
    if field == "date_of_birth":
        kind = _classify_date(answer, session["identity"]["date_of_birth"])
        if kind == "bad":
            result = {"ok": False, "error": DATE_ERROR}
            _log(session, "verify_identity", args, 200, result)
            return 200, result
        if kind == "swap":
            session["hinted"] = True
            result = {"ok": True, "verified": False, "wrong_answers_left": 3 - session["wrong"], "hint": "day_month_swapped"}
            _log(session, "verify_identity", args, 200, result)
            return 200, result
        if kind == "ok":
            session["verified"]["date_of_birth"] = True
        else:
            session["wrong"] += 1
            session["verified"]["date_of_birth"] = False
    elif answer == str(session["identity"].get("phone_last4")):
        session["verified"]["phone_last4"] = True
    else:
        session["wrong"] += 1
    result = {
        "ok": True,
        "verified": session["verified"][field],
        "wrong_answers_left": max(0, 3 - session["wrong"]),
        "all_verified": all(session["verified"].values()),
    }
    _log(session, "verify_identity", args, 200, result)
    return 200, result

def get_account(session, body):
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

def create_promise(session, body):
    args = _args(body)
    hit = _fault(session, "create_promise", args) or _closed(session, "create_promise", args)
    if hit:
        return hit
    if not all(session["verified"].values()):
        result = {"ok": False, "error": NOT_VERIFIED}
    elif not any(product["product_id"] == body.get("product_id") for product in session["products"]):
        result = {"ok": False, "error": "unknown product_id"}
    elif any(item["product_id"] == body.get("product_id") for item in session["promises"]):
        result = {"ok": False, "error": "this product already has a promise"}
    else:
        promise_id = f"pr{session['next_promise']}"
        session["next_promise"] += 1
        product = next(item for item in session["products"] if item["product_id"] == body.get("product_id"))
        session["promises"].append({
            "promise_id": promise_id,
            "product_id": body.get("product_id"),
            "type": product["type"],
            "amount": _num(body.get("amount")),
            "date": body.get("date"),
        })
        result = {"ok": True, "promise_id": promise_id}
    _log(session, "create_promise", args, 200, result)
    return 200, result

def add_note(session, body):
    args = _args(body)
    hit = _fault(session, "add_note", args) or _closed(session, "add_note", args)
    if hit:
        return hit
    if not all(session["verified"].values()):
        result = {"ok": False, "error": NOT_VERIFIED}
    elif not any(product["product_id"] == body.get("product_id") for product in session["products"]):
        result = {"ok": False, "error": "unknown product_id"}
    else:
        note = {"product_id": body.get("product_id"), "reason": body.get("reason")}
        if body.get("text"):
            note["text"] = body["text"]
        session["notes"].append(note)
        result = {"ok": True}
    _log(session, "add_note", args, 200, result)
    return 200, result

def schedule_callback(session, body):
    args = _args(body)
    hit = _fault(session, "schedule_callback", args) or _closed(session, "schedule_callback", args)
    if hit:
        return hit
    session["callbacks"].append({"date": body.get("date"), "time": body.get("time")})
    result = {"ok": True}
    _log(session, "schedule_callback", args, 200, result)
    return 200, result

def transfer_to_agent(session, body):
    args = _args(body)
    hit = _fault(session, "transfer_to_agent", args) or _closed(session, "transfer_to_agent", args)
    if hit:
        return hit
    session["transfers"].append({"queue": body.get("queue")})
    result = {"ok": True}
    _log(session, "transfer_to_agent", args, 200, result)
    return 200, result

def close_call(session, body):
    args = _args(body)
    hit = _fault(session, "close_call", args) or _closed(session, "close_call", args)
    if hit:
        return hit
    session["closed"] = True
    session["outcome"] = body.get("outcome")
    session["reason"] = body.get("reason")
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

def create_app(cases=None, cases_path=None):
    if cases is None:
        path = Path(cases_path) if cases_path else ROOT / "public-test-set.json"
        cases = json.loads(Path(path).read_text())
    bank = Bank(cases)
    app = FastAPI()
    app.state.bank = bank

    def team_of(request):
        return request.headers.get("x-team-key") or ""

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
                status, result = HANDLERS[name](session, body)
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

if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        self_test()
    else:
        import uvicorn
        port = int(os.environ.get("PORT", "8787"))
        uvicorn.run(create_app(), host="127.0.0.1", port=port)
