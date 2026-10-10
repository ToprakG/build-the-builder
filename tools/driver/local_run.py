"""Play a case on this machine.

An OpenRouter model is the agent and speaks first. The customer is the persona
from run_cases.py, on the same model. The seven tools run on the local bank.
The ledger score is score.score_ledger.

    python3 tools/driver/local_run.py --only PUB-01,PUB-02,PUB-03
"""
import argparse, json, os, pathlib, sys, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "driver"))
from envfile import load_env
load_env()
from score import score_ledger
from run_cases import persona_for
from mockbank import HANDLERS, Bank
from chat_driver import customer_reply

AGENT_MODEL = os.environ.get("OPENROUTER_AGENT_MODEL", "google/gemini-3-flash-preview")
MAX_TURNS = 16
TEAM = "local-run-team-key"

TOOLS = [
    {"type": "function", "function": {"name": "verify_identity", "description": "Check one security answer. date_of_birth is YYYY-MM-DD. phone_last4 is four digits.", "parameters": {"type": "object", "properties": {"field": {"type": "string", "enum": ["date_of_birth", "phone_last4"]}, "answer": {"type": "string"}}, "required": ["field", "answer"]}}},
    {"type": "function", "function": {"name": "get_account", "description": "Fetch the products after both security answers are verified.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "create_promise", "description": "Record one payment promise after the customer confirms the read-back.", "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}, "amount": {"type": "number"}, "date": {"type": "string"}}, "required": ["product_id", "amount", "date"]}}},
    {"type": "function", "function": {"name": "add_note", "description": "Record a note when there is no promise. reason is refused, hardship, dispute, paid, or service_error.", "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["product_id", "reason"]}}},
    {"type": "function", "function": {"name": "schedule_callback", "description": "Book a callback. date is YYYY-MM-DD. time is HH:MM.", "parameters": {"type": "object", "properties": {"date": {"type": "string"}, "time": {"type": "string"}}, "required": ["date", "time"]}}},
    {"type": "function", "function": {"name": "transfer_to_agent", "description": "Hand the call to a person. queue is restructuring, general, or support.", "parameters": {"type": "object", "properties": {"queue": {"type": "string"}}, "required": ["queue"]}}},
    {"type": "function", "function": {"name": "close_call", "description": "End the call. Always the last tool. outcome and reason come from the brief.", "parameters": {"type": "object", "properties": {"outcome": {"type": "string"}, "reason": {"type": "string"}}, "required": ["outcome"]}}},
]

def _post(payload):
    key = os.environ.get("OPENROUTER_API_KEY") or ""
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is empty.")
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"openrouter {error.code}: {error.read().decode()[:500]}") from None

def agent_turn(messages):
    body = _post({"model": AGENT_MODEL, "temperature": 0.2, "messages": messages, "tools": TOOLS})
    message = ((body.get("choices") or [{}])[0].get("message") or {})
    return message

def run_case(bank, case, brief):
    session = bank.open_session(TEAM, case["id"])
    variables = session["variables"]
    customer_id = variables["customer_id"]
    shown = {key: variables[key] for key in ("title", "first_name", "last_name", "now", "customer_id")}
    system = (
        "You are the phone agent. Follow the brief. Speak first, before the customer says anything. "
        "The first thing you say is the opening sentence, then ask whether you are speaking with the person named below. "
        "Call a tool when the brief says to. The bank adds customer_id. Do not invent a customer id, a ledger row, or a tool result.\n\n"
        "CALL VARIABLES: " + " ".join(f"{key}={value}" for key, value in shown.items())
        + "\n\n" + brief
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "The call is connected. Speak first."},
    ]
    prompt = persona_for(case, {"identity": case["identity"]})["prompt"]
    prompt += "\n\nWHEN YOU NAME A TIME: say the hour and the minutes, like 11:00. Do not say morning, afternoon, or around in place of that time."
    agent_lines, customer_lines, transcript = [], [], []
    for _ in range(MAX_TURNS):
        closed = False
        for _round in range(6):
            message = agent_turn(messages)
            calls = message.get("tool_calls") or []
            text = (message.get("content") or "").strip()
            messages.append({"role": "assistant", "content": text or None, "tool_calls": calls or None})
            if not calls:
                if text:
                    agent_lines.append(text)
                    transcript.append(("AGENT", text))
                    print(f"  AGENT: {text[:240]}")
                break
            for call in calls:
                name = (call.get("function") or {}).get("name")
                raw = (call.get("function") or {}).get("arguments") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else dict(raw)
                except json.JSONDecodeError:
                    args = {}
                args["customer_id"] = customer_id
                if name not in HANDLERS:
                    result, status = {"ok": False, "error": "unknown tool"}, 400
                else:
                    with bank.lock:
                        status, result = HANDLERS[name](session, args, bank.config)
                transcript.append(("TOOL", f"{name} {status} {json.dumps(result)[:180]}"))
                print(f"  TOOL {name} -> {json.dumps(result)[:180]}")
                messages.append({"role": "tool", "tool_call_id": call.get("id") or name, "content": json.dumps(result)})
                if name == "close_call" and result.get("ok") is True:
                    closed = True
        if closed or not agent_lines:
            break
        reply = customer_reply(prompt, agent_lines, customer_lines)
        customer_lines.append(reply)
        transcript.append(("CUSTOMER", reply))
        print(f"  CUSTOMER: {reply[:240]}")
        messages.append({"role": "user", "content": reply})
        calls = session["calls"]
        if calls and calls[-1].get("tool") == "close_call" and (calls[-1].get("result") or {}).get("ok") is True:
            break
    ledger = bank.ledger(TEAM, customer_id)
    scored = score_ledger(case, ledger)
    return {"id": case["id"], "pass": scored["pass"], "failed": scored["failed"], "transcript": transcript}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(ROOT / "public-test-set.json"))
    parser.add_argument("--brief-file", default=str(ROOT / "briefs" / "brief-a.md"))
    parser.add_argument("--only")
    parser.add_argument("--out")
    args = parser.parse_args()
    cases = json.loads(pathlib.Path(args.cases).read_text())
    if args.only:
        wanted = set(args.only.split(","))
        cases = [case for case in cases if case["id"] in wanted]
    brief = pathlib.Path(args.brief_file).read_text()
    bank = Bank(json.loads((ROOT / "public-test-set.json").read_text()))
    print(f"agent {AGENT_MODEL}. customer persona on {AGENT_MODEL}.")
    rows = []
    for case in cases:
        print(f"\n== {case['id']} {case['title']}")
        try:
            row = run_case(bank, case, brief)
        except Exception as error:
            print("FAIL", case["id"], error)
            continue
        rows.append(row)
        print(("PASS" if row["pass"] else "FAIL"), case["id"])
        for line in row["failed"]:
            print("   -", line[:220])
    print(f"\n{sum(row['pass'] for row in rows)} / {len(rows)} passed")
    if args.out:
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": AGENT_MODEL, "rows": rows}, indent=1))
        print(f"wrote {path}")

if __name__ == "__main__":
    main()
