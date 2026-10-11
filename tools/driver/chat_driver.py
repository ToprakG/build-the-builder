"""Run practice cases through a workflow one chat turn at a time.

Each case opens a bank session, plays the customer with the persona text from run_cases.py,
and talks to the workflow with POST /api/v2/chat/message. At most four calls run at once.
The ledger score is score.score_ledger. The conversation score is judge.judge_transcript.

    python3 tools/driver/chat_driver.py --workflow-id WORKFLOW --brief A
    python3 tools/driver/chat_driver.py --compare-banks

--compare-banks replays the saved transcripts' tool calls on the hosted bank and on this mock.
It compares both ledgers with the ledger lines in results/*.json. It does not call a model.
The customer model is gemini-3-flash-preview. A Gemini key is used when it is set.
Otherwise the same model is called through OpenRouter.
--customer-model deepseek sends the customer through OpenRouter as deepseek/deepseek-v4.1-flash,
even when a Gemini key is set. A full OpenRouter id is accepted too.
A case style, when the case has one, is the same HOW YOU TALK block run_cases.py uses.
"""
import argparse, json, os, pathlib, re, sys, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "driver"))
from envfile import load_env
load_env()
import freya
from score import score_ledger
from run_cases import persona_for
from serialize_call import parse_messages
from judge import judge_transcript
from bank_url import origin

GEMINI_MODEL = "gemini-3-flash-preview"
OPENROUTER_CUSTOMER = "google/gemini-3-flash-preview"
# Short names for --customer-model. Anything with a slash is used as an OpenRouter id.
CUSTOMER_MODELS = {
    "gemini": "google/gemini-3-flash-preview",
    "deepseek": "deepseek/deepseek-v4.1-flash",
    "deepseek-v4.1-flash": "deepseek/deepseek-v4.1-flash",
}
MAX_TURNS = 30
# The chat route needs one user message before it will speak the opener. That line is not
# the customer. Saved simulations start with the agent, so the judge must not see it.
STARTERS = {"[call connected]", "Hello?"}

def _gemini_key():
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

def _spoken(text):
    """Keep the words the customer says. Drop a label, a code fence, or a stage direction."""
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text).strip()
    text = text.strip('"').strip()
    text = re.sub(r"^(Customer|User)\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"\[[^\]]*\]", "", text)
    lines = [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    return " ".join(" ".join(lines).split())

def for_judge(messages):
    rows = list(messages or [])
    if rows and rows[0].get("role") == "user" and (rows[0].get("message") or "").strip() in STARTERS:
        rows = rows[1:]
    return rows

def bank_call(host, path, body=None):
    headers = {"user-agent": "freya-bank-scorer/0.1", "content-type": "application/json", "x-team-key": os.environ["FREYA_BANK_TEAM_KEY"]}
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(host + path, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, {"ok": False, "error": raw[:400]}

def _customer_gemini(prompt, contents):
    key = _gemini_key()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={key}"
    payload = {
        "systemInstruction": {"parts": [{"text": prompt}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.4},
    }
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"gemini {error.code}: {error.read().decode()[:500]}") from None
    parts = (((body.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])
    return "\n".join(part.get("text") or "" for part in parts).strip()

def resolve_customer_model(name):
    """A short name, or an OpenRouter id such as deepseek/deepseek-v4.1-flash."""
    name = (name or "").strip()
    if not name:
        return OPENROUTER_CUSTOMER
    if "/" in name:
        return name
    if name not in CUSTOMER_MODELS:
        known = ", ".join(sorted(CUSTOMER_MODELS))
        raise RuntimeError(f"unknown customer model {name}. Use one of {known}, or a full OpenRouter id.")
    return CUSTOMER_MODELS[name]

def _customer_openrouter(prompt, agent_lines, customer_lines, model=None):
    key = os.environ.get("OPENROUTER_API_KEY") or ""
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is empty. The customer model needs it.")
    messages = [{"role": "system", "content": prompt}]
    for index, line in enumerate(agent_lines):
        messages.append({"role": "user", "content": line})
        if index < len(customer_lines):
            messages.append({"role": "assistant", "content": customer_lines[index]})
    payload = {"model": model or OPENROUTER_CUSTOMER, "temperature": 0.4, "messages": messages, "max_tokens": 600}
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"openrouter {error.code}: {error.read().decode()[:500]}") from None
    choice = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return choice.strip()

def customer_reply(prompt, agent_lines, customer_lines, model=None):
    """model forces OpenRouter. Without it, a Gemini key is used when one is set."""
    chosen = model or os.environ.get("OPENROUTER_CUSTOMER_MODEL") or ""
    if chosen:
        raw = _customer_openrouter(prompt, agent_lines, customer_lines, resolve_customer_model(chosen))
        if not _spoken(raw):
            raw = _customer_openrouter(prompt, agent_lines, customer_lines, resolve_customer_model(chosen))
    elif _gemini_key():
        contents = []
        for index, line in enumerate(agent_lines):
            contents.append({"role": "user", "parts": [{"text": line}]})
            if index < len(customer_lines):
                contents.append({"role": "model", "parts": [{"text": customer_lines[index]}]})
        raw = _customer_gemini(prompt, contents)
    else:
        raw = _customer_openrouter(prompt, agent_lines, customer_lines)
    text = _spoken(raw)
    if not text:
        raise RuntimeError("the customer model returned no spoken line")
    return text

def chat_turn(workflow_id, message, variables, call_id=None):
    body = {"workflowId": workflow_id, "message": message, "customerVariables": variables}
    if call_id:
        body = {"workflowId": workflow_id, "message": message, "callId": call_id}
    last = None
    for attempt in range(5):
        try:
            result = freya.api("POST", "/api/v2/chat/message", body)
            return result.get("callId") or call_id, (result.get("response") or "").strip(), result
        except RuntimeError as error:
            last = error
            if "-> 503" not in str(error) and "-> 502" not in str(error) and "-> 504" not in str(error):
                raise
            import time
            time.sleep(2 * (attempt + 1))
    raise last

def run_case(workflow_id, case, host, style=None, customer_model=None):
    status, opened = bank_call(host, "/v1/practice/sessions", {"case": case["id"]})
    if status != 200:
        raise RuntimeError(f"session {case['id']} -> {status} {opened}")
    variables = opened["variables"]
    prompt = persona_for(case, {"identity": case["identity"]}, style)["prompt"]
    prompt += "\n\nWHEN YOU NAME A TIME: say the hour and the minutes, like 11:00. Do not say morning, afternoon, or around in place of that time."
    call_id, agent, _ = chat_turn(workflow_id, "Hello?", variables)
    agent_lines, customer_lines = [], []
    if agent:
        agent_lines.append(agent)
    for _ in range(MAX_TURNS):
        if not agent_lines:
            break
        reply = customer_reply(prompt, agent_lines, customer_lines, customer_model)
        customer_lines.append(reply)
        call_id, agent, _ = chat_turn(workflow_id, reply, variables, call_id)
        if agent:
            agent_lines.append(agent)
        _, ledger = bank_call(host, "/v1/practice/ledger/" + variables["customer_id"])
        calls = ledger.get("calls") or []
        if calls and calls[-1].get("tool") == "close_call" and (calls[-1].get("result") or {}).get("ok") is True:
            break
        if not agent:
            break
    try:
        freya.api("POST", "/api/v2/chat/end", {"callId": call_id})
    except Exception:
        pass
    _, ledger = bank_call(host, "/v1/practice/ledger/" + variables["customer_id"])
    ledger_score = score_ledger(case, ledger)
    messages = []
    if call_id:
        try:
            messages = freya.get_call(call_id).get("messages") or []
        except Exception:
            messages = []
    judged = judge_transcript(for_judge(messages), case) if messages else {"pass": None, "status": "no_transcript", "checks": [], "min_p": None}
    passed = ledger_score["pass"] and judged["pass"] is True
    return {
        "id": case["id"],
        "customer_id": variables["customer_id"],
        "call_id": call_id,
        "pass": passed,
        "ledger": ledger_score,
        "judge": {"pass": judged["pass"], "status": judged["status"], "min_p": judged["min_p"], "checks": judged["checks"]},
    }

def _ledger_lines(failed):
    return [line for line in failed if not str(line).startswith("conversation:")]

def _replay(host, case, turns, customer_id=None):
    if customer_id is None:
        status, opened = bank_call(host, "/v1/practice/sessions", {"case": case["id"]})
        if status != 200:
            raise RuntimeError(f"session {status} {opened}")
        customer_id = opened["variables"]["customer_id"]
    for turn in turns:
        if turn.kind != "tool" or turn.name not in ("verify_identity", "get_account", "create_promise", "add_note", "schedule_callback", "transfer_to_agent", "close_call"):
            continue
        bank_call(host, f"/v1/{turn.name}", {"customer_id": customer_id, **turn.args})
    _, ledger = bank_call(host, "/v1/practice/ledger/" + customer_id)
    return score_ledger(case, ledger), ledger

def compare_banks():
    """Replay saved tool calls. The mock and the hosted bank should score the same ledger lines."""
    from fastapi.testclient import TestClient
    from mockbank import create_app
    load_env()
    os.environ.setdefault("FREYA_BANK_TEAM_KEY", "team-key-for-compare-01")
    cases = {case["id"]: case for case in json.loads((ROOT / "public-test-set.json").read_text())}
    saved = {}
    for path in (ROOT / "results").glob("brief-*-run.json"):
        data = json.loads(path.read_text())
        for rep in data.get("all_rows") or []:
            for row in rep:
                saved[row["call_id"]] = row
    client = TestClient(create_app())
    host = origin()
    same = differ = 0
    print(f"{'call':<22} {'case':<8} mock ledger vs saved / hosted")
    for path in sorted((ROOT / "results" / "transcripts").glob("*.json")):
        if path.name.startswith("_"):
            continue
        blob = json.loads(path.read_text())
        case = cases[blob["case_id"]]
        turns = parse_messages(blob["messages"])
        row = saved.get(blob["call_id"])
        expected = _ledger_lines(row["failed"]) if row else None
        mock_score = _replay_client(client, case, turns)
        live_score, _ = _replay(host, case, turns)
        mock_ok = expected is None or mock_score["failed"] == expected
        live_ok = mock_score["failed"] == live_score["failed"]
        mark = "same" if mock_ok and live_ok else "DIFFER"
        if mark == "same":
            same += 1
        else:
            differ += 1
            print(f"{blob['call_id'][:8]:<22} {case['id']:<8} {mark}")
            print("  saved", expected)
            print("  mock ", mock_score["failed"])
            print("  live ", live_score["failed"])
    print(f"\n{same} calls match. {differ} calls differ.")
    return differ

def _replay_client(client, case, turns):
    headers = {"x-team-key": os.environ["FREYA_BANK_TEAM_KEY"]}
    opened = client.post("/v1/practice/sessions", json={"case": case["id"]}, headers=headers)
    customer_id = opened.json()["variables"]["customer_id"]
    for turn in turns:
        if turn.kind != "tool" or turn.name not in ("verify_identity", "get_account", "create_promise", "add_note", "schedule_callback", "transfer_to_agent", "close_call"):
            continue
        client.post(f"/v1/{turn.name}", json={"customer_id": customer_id, **turn.args})
    ledger = client.get(f"/v1/practice/ledger/{customer_id}", headers=headers).json()
    return score_ledger(case, ledger)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-id")
    parser.add_argument("--brief")
    parser.add_argument("--cases", default=str(ROOT / "public-test-set.json"))
    parser.add_argument("--only")
    parser.add_argument("--style", choices=["pieces", "corrects", "talkative", "asks_back", "in_words"])
    parser.add_argument("--customer-model", help="deepseek (deepseek/deepseek-v4.1-flash), gemini, or an OpenRouter id")
    parser.add_argument("--compare-banks", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.compare_banks:
        sys.exit(compare_banks())
    if not args.workflow_id:
        parser.error("--workflow-id is required for a chat run")
    cases = [case for case in json.loads(pathlib.Path(args.cases).read_text()) if (not args.brief or case["brief"] == args.brief) and (not args.only or case["id"] in args.only.split(","))]
    host = origin()
    if args.customer_model:
        print("customer", resolve_customer_model(args.customer_model), "via OpenRouter")
    workers = max(1, min(args.workers, 4))
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_case, args.workflow_id, case, host, args.style, args.customer_model): case for case in cases}
        for future in as_completed(futures):
            case = futures[future]
            try:
                row = future.result()
            except Exception as error:
                print("FAIL", case["id"], error)
                continue
            rows.append(row)
            label = "PASS" if row["pass"] else "FAIL"
            print(label, row["id"], "ledger", row["ledger"]["pass"], "judge", row["judge"]["pass"], row["judge"]["status"])
            for line in row["ledger"]["failed"]:
                print("       -", line[:260])
            for check in row["judge"]["checks"]:
                if check["answer"] == "no":
                    print("       -", check["check"])
    print(f"\n{sum(row['pass'] for row in rows)} / {len(rows)} passed")
    if args.out:
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"workflow_id": args.workflow_id, "passed": sum(row["pass"] for row in rows), "total": len(rows), "rows": rows}, indent=1))
        print(f"wrote {path}")

if __name__ == "__main__":
    main()
