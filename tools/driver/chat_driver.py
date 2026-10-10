"""Run practice cases through a workflow one chat turn at a time.

Each case opens a bank session, plays the customer with the persona text from run_cases.py,
and talks to the workflow with POST /api/v2/chat/message. At most four calls run at once.
The ledger score is score.score_ledger. The conversation score is judge.judge_transcript.

    python3 tools/driver/chat_driver.py --workflow-id WORKFLOW --brief A
    python3 tools/driver/chat_driver.py --compare-banks

--compare-banks replays the saved transcripts' tool calls on the hosted bank and on this mock.
It compares both ledgers with the ledger lines in results/*.json. It does not call a model.
The customer model is gemini-3-flash-preview and needs GEMINI_API_KEY or GOOGLE_API_KEY.
"""
import argparse, json, os, pathlib, sys, urllib.error, urllib.request
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
MAX_TURNS = 30

def _gemini_key():
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

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

def customer_reply(prompt, agent_lines, customer_lines):
    key = _gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is empty. The customer model needs it.")
    contents = []
    for index, line in enumerate(agent_lines):
        contents.append({"role": "user", "parts": [{"text": line}]})
        if index < len(customer_lines):
            contents.append({"role": "model", "parts": [{"text": customer_lines[index]}]})
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
    text = "\n".join(part.get("text") or "" for part in parts).strip()
    if not text:
        raise RuntimeError("gemini returned no customer line")
    return text

def chat_turn(workflow_id, message, variables, call_id=None):
    body = {"workflowId": workflow_id, "message": message, "customerVariables": variables}
    if call_id:
        body = {"workflowId": workflow_id, "message": message, "callId": call_id}
    result = freya.api("POST", "/api/v2/chat/message", body)
    return result.get("callId") or call_id, (result.get("response") or "").strip(), result

def run_case(workflow_id, case, host, style=None):
    status, opened = bank_call(host, "/v1/practice/sessions", {"case": case["id"]})
    if status != 200:
        raise RuntimeError(f"session {case['id']} -> {status} {opened}")
    variables = opened["variables"]
    prompt = persona_for(case, {"identity": case["identity"]}, style)["prompt"]
    call_id, agent, _ = chat_turn(workflow_id, "[call connected]", variables)
    agent_lines, customer_lines = [], []
    if agent:
        agent_lines.append(agent)
    for _ in range(MAX_TURNS):
        if not agent_lines:
            break
        reply = customer_reply(prompt, agent_lines, customer_lines)
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
    judged = judge_transcript(messages, case) if messages else {"pass": None, "status": "no_transcript", "checks": [], "min_p": None}
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
    parser.add_argument("--compare-banks", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.compare_banks:
        sys.exit(compare_banks())
    if not args.workflow_id:
        parser.error("--workflow-id is required for a chat run")
    cases = [case for case in json.loads(pathlib.Path(args.cases).read_text()) if (not args.brief or case["brief"] == args.brief) and (not args.only or case["id"] in args.only.split(","))]
    host = origin()
    workers = max(1, min(args.workers, 4))
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_case, args.workflow_id, case, host, args.style): case for case in cases}
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

if __name__ == "__main__":
    main()
