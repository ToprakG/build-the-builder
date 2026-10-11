"""Play a case on this machine.

An OpenRouter model is the agent and speaks first. The customer is the persona
from run_cases.py, on the same model. The seven tools run on the local bank.
--model deepseek uses deepseek/deepseek-v4.1-flash for both sides.
The ledger score is score.score_ledger. --judge sends the transcript to Jev (Judge C).

    python3 tools/driver/local_run.py --only PUB-01,PUB-02,PUB-03
"""
import argparse, json, os, pathlib, subprocess, sys, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "driver"))
from envfile import load_env
load_env()
from score import score_ledger
from run_cases import persona_for
from mockbank import HANDLERS, Bank
from chat_driver import customer_reply, resolve_customer_model
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

CC_MODEL = "claude-sonnet-5-5"


def _claude_agent(messages):
    """The phone agent is local Claude Code, Sonnet 5.5. No OpenRouter call."""
    system = next((item.get("content") or "" for item in messages if item.get("role") == "system"), "")
    names = ", ".join(tool["function"]["name"] for tool in TOOLS)
    system += (
        "\n\nTools: " + names + ". "
        "Reply with one JSON object only: {\"say\": \"words you speak, or empty\", "
        "\"tool\": null or {\"name\": \"tool name\", \"arguments\": {}}} . "
        "Speak or call one tool. Do not do both. Do not invent a tool result."
    )
    lines = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        if role == "user":
            lines.append("CUSTOMER: " + (message.get("content") or ""))
        elif role == "assistant":
            if message.get("content"):
                lines.append("YOU SAID: " + message["content"])
            for call in message.get("tool_calls") or []:
                fn = call.get("function") or {}
                lines.append(f"YOU CALLED {fn.get('name')} {fn.get('arguments')}")
        elif role == "tool":
            lines.append("TOOL RESULT: " + (message.get("content") or ""))
    cmd = [
        "claude", "-p", "--model", CC_MODEL, "--effort", "low", "--tools", "",
        "--system-prompt", system, "--output-format", "json",
        "--no-session-persistence", "--disable-slash-commands",
        "--permission-mode", "dontAsk", "--setting-sources", "user", "--strict-mcp-config",
    ]
    proc = subprocess.run(cmd, input="\n".join(lines) + "\n\nYour next JSON object:", text=True, capture_output=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exit {proc.returncode}: {(proc.stderr or '')[-240:]}")
    payload = json.loads(proc.stdout or "{}")
    if payload.get("is_error"):
        raise RuntimeError(str(payload.get("result") or "")[:240])
    text = payload.get("result") or ""
    if not isinstance(text, str):
        body = text
    else:
        start, end = text.find("{"), text.rfind("}")
        body = json.loads(text[start:end + 1] if start >= 0 else text)
    say = str(body.get("say") or "").strip()
    tool = body.get("tool") if isinstance(body.get("tool"), dict) else None
    calls = []
    if tool and tool.get("name"):
        calls.append({
            "id": tool["name"],
            "function": {"name": tool["name"], "arguments": json.dumps(tool.get("arguments") or {})},
        })
    return {"content": say, "tool_calls": calls}


def agent_turn(messages, model):
    if model == "cc":
        return _claude_agent(messages)
    body = _post({"model": model, "temperature": 0.2, "messages": messages, "tools": TOOLS, "max_tokens": 400})
    message = ((body.get("choices") or [{}])[0].get("message") or {})
    return message

def workflow_tool_map(workflow):
    found = {}
    for group in ("nodes", "global_nodes"):
        nodes = (workflow or {}).get(group) or {}
        items = nodes.values() if isinstance(nodes, dict) else nodes
        for node in items:
            if not isinstance(node, dict):
                continue
            for tool in node.get("tools") or []:
                name = ((tool.get("function") or {}).get("name"))
                if name and name not in found:
                    found[name] = tool
    return found


def agent_script(workflow):
    parts = []
    for group in ("nodes", "global_nodes"):
        nodes = (workflow or {}).get(group) or {}
        items = nodes.items() if isinstance(nodes, dict) else enumerate(nodes)
        for name, node in items:
            if not isinstance(node, dict):
                continue
            task = (node.get("task_message") or "").strip()
            if task:
                parts.append(f"## {name}\n{task}")
    return "\n\n".join(parts)


def run_workflow_tool(tool, session, bank, call_vars, name, args):
    """Run the compiled pre-hook, the local bank, then the post-hook."""
    config = {"body": {key: value for key, value in args.items() if key != "customer_id"}}
    namespace = {"config": config, "vars": call_vars}
    pre = (tool or {}).get("pre_process_code") or ""
    if pre:
        exec(pre, namespace)
    body = dict(namespace["config"].get("body") or {})
    body["customer_id"] = session["variables"]["customer_id"]
    if name not in HANDLERS:
        status, data = 400, {"ok": False, "error": "unknown tool"}
    else:
        with bank.lock:
            status, data = HANDLERS[name](session, body, bank.config)
    result = {"data": data, "status_code": status, "status": "success" if status < 500 else "error"}
    namespace["result"] = result
    post = (tool or {}).get("post_process_code") or ""
    if post:
        exec(post, namespace)
    data = namespace["result"].get("data") if isinstance(namespace["result"].get("data"), dict) else data
    return status, data, str(data.get("what_to_do_next") or ""), body


def run_case(bank, case, brief, model, customer_model=None, tool_runner=None):
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
    chat = [
        {"role": "system", "content": system},
        {"role": "user", "content": "The call is connected. Speak first."},
    ]
    prompt = persona_for(case, {"identity": case["identity"]})["prompt"]
    prompt += "\n\nWHEN YOU NAME A TIME: say the hour and the minutes, like 11:00. Do not say morning, afternoon, or around in place of that time."
    prompt += "\n\nReply with only the words you say on the call. No notes and no headings."
    agent_lines, customer_lines, transcript, spoken = [], [], [], []
    for _ in range(MAX_TURNS):
        closed = False
        for _round in range(6):
            message = agent_turn(chat, model)
            calls = message.get("tool_calls") or []
            text = (message.get("content") or "").strip()
            chat.append({"role": "assistant", "content": text or None, "tool_calls": calls or None})
            if not calls:
                if text:
                    agent_lines.append(text)
                    transcript.append(("AGENT", text))
                    spoken.append({"role": "assistant", "message": text})
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
                note = ""
                if tool_runner is not None:
                    status, result, note, sent = tool_runner(name, args, session)
                    if sent.get("answer") and sent.get("answer") != args.get("answer"):
                        print(f"  HOOK {name} answer {args.get('answer')!r} -> {sent.get('answer')!r}")
                elif name not in HANDLERS:
                    result, status = {"ok": False, "error": "unknown tool"}, 400
                else:
                    with bank.lock:
                        status, result = HANDLERS[name](session, args, bank.config)
                transcript.append(("TOOL", f"{name} {status} {json.dumps(result)[:180]}"))
                shown_args = {key: value for key, value in args.items() if key != "customer_id"}
                spoken.append({"role": "system", "message": json.dumps({"event": "function_call_start", "function": name, "args": shown_args})})
                spoken.append({"role": "system", "message": json.dumps({"event": "function_call_complete", "function": name, "result": {"data": result, "status_code": status}})})
                print(f"  TOOL {name} -> {json.dumps(result)[:180]}")
                payload = dict(result)
                if note:
                    payload["what_to_do_next"] = note
                chat.append({"role": "tool", "tool_call_id": call.get("id") or name, "content": json.dumps(payload)})
                if name == "close_call" and result.get("ok") is True:
                    closed = True
        if closed or not agent_lines:
            break
        reply = customer_reply(prompt, agent_lines, customer_lines, customer_model or model)
        customer_lines.append(reply)
        transcript.append(("CUSTOMER", reply))
        spoken.append({"role": "user", "message": reply})
        print(f"  CUSTOMER: {reply[:240]}")
        chat.append({"role": "user", "content": reply})
        calls = session["calls"]
        if calls and calls[-1].get("tool") == "close_call" and (calls[-1].get("result") or {}).get("ok") is True:
            break
    ledger = bank.ledger(TEAM, customer_id)
    scored = score_ledger(case, ledger)
    return {"id": case["id"], "pass": scored["pass"], "failed": scored["failed"], "transcript": transcript, "messages": spoken}


def play_workflow(workflow, cases, policy, customer_model="meta-llama/llama-3.1-8b-instruct"):
    """Play repair scenarios on this machine. Claude Code is the agent, Llama the customer, Jev the judge."""
    from builder.template_cc.vendor_runner import bank_config
    from judges import judge_c

    tools = workflow_tool_map(workflow)
    script = agent_script(workflow)
    bank = Bank(cases, bank_config(policy))
    rows = []
    for case in cases:
        call_vars = {"now": "2026-10-11T09:00:00"}
        print(f"\n== {case['id']} {case.get('rule')}", flush=True)

        def caller(name, args, session, _tools=tools, _vars=call_vars, _bank=bank):
            return run_workflow_tool(_tools.get(name), session, _bank, _vars, name, args)

        try:
            played = run_case(bank, case, script, "cc", customer_model, tool_runner=caller)
            verdict = judge_c(played["messages"], case)
        except Exception as error:
            print(f"FAIL {case['id']} {error}", flush=True)
            rows.append({
                "case_id": case["id"], "passed": False, "infra": True,
                "failed_reasons": [f"runner error: {error!r}"[:200]],
            })
            continue
        unknown = bool(case.get("unknown")) or case.get("rule") == "unknown"
        reasons = [] if unknown else list(played["failed"])
        for check in verdict.get("checks") or []:
            if check.get("pass") is False:
                reasons.append(f"conversation: {check.get('text')}")
        if verdict.get("pass") is None:
            reasons.append(f"conversation: Jev {verdict.get('status')}")
        passed = verdict.get("pass") is True if unknown else bool(played["pass"]) and verdict.get("pass") is True
        print(f"{'PASS' if passed else 'FAIL'} {case['id']} jev={verdict.get('pass')}", flush=True)
        for line in reasons:
            print("   -", line[:220], flush=True)
        rows.append({"case_id": case["id"], "passed": passed, "failed_reasons": reasons, "infra": False})
    return rows

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(ROOT / "public-test-set.json"))
    parser.add_argument("--brief-file", default=str(ROOT / "briefs" / "brief-a.md"))
    parser.add_argument("--only")
    parser.add_argument("--agent", choices=("openrouter", "cc"), default="openrouter",
                        help="cc is local Claude Code, Sonnet 5.5. openrouter uses --model.")
    parser.add_argument("--model", default=os.environ.get("OPENROUTER_AGENT_MODEL", "google/gemini-3-flash-preview"),
                        help="deepseek (deepseek/deepseek-v4.1-flash), gemini, or an OpenRouter id. This is the agent when --agent openrouter.")
    parser.add_argument("--customer-model", default="",
                        help="OpenRouter id for the customer. The agent model is used when this is empty.")
    parser.add_argument("--config", default="", help="mock bank config. The cases file is then the bank's case list.")
    parser.add_argument("--judge", action="store_true", help="also score the conversation with Jev")
    parser.add_argument("--out")
    args = parser.parse_args()
    model = "cc" if args.agent == "cc" else resolve_customer_model(args.model)
    customer_model = resolve_customer_model(args.customer_model) if args.customer_model else model
    cases = json.loads(pathlib.Path(args.cases).read_text())
    if args.only:
        wanted = set(args.only.split(","))
        cases = [case for case in cases if case["id"] in wanted]
    brief = pathlib.Path(args.brief_file).read_text()
    if args.config:
        config = json.loads(pathlib.Path(args.config).read_text())
        bank = Bank(cases, config)
    else:
        bank = Bank(json.loads((ROOT / "public-test-set.json").read_text()))
    agent_name = f"Claude Code {CC_MODEL}" if model == "cc" else model
    print(f"agent {agent_name}. customer {customer_model}. judge {'Jev' if args.judge else 'ledger only'}.")
    rows = []
    for case in cases:
        print(f"\n== {case['id']} {case['title']}")
        try:
            row = run_case(bank, case, brief, model, customer_model)
        except Exception as error:
            print("FAIL", case["id"], error)
            continue
        if args.judge:
            from judges import judge_c
            verdict = judge_c(row.pop("messages"), case)
            row["judge"] = {"pass": verdict["pass"], "min_p": verdict["min_p"], "status": verdict["status"]}
            row["ledger_pass"] = row["pass"]
            if verdict["pass"] is False:
                row["failed"].append("conversation: " + ", ".join(item["text"] for item in verdict["checks"] if item["pass"] is False)[:300])
                row["pass"] = False
            elif verdict["pass"] is None:
                row["failed"].append(f"conversation: Jev {verdict['status']}")
                row["pass"] = False
            label = {True: "PASS", False: "FAIL"}.get(verdict["pass"], "WAIT")
            print(f"  judge Jev {label} min_p={verdict['min_p']} {verdict['status']}")
        else:
            row.pop("messages", None)
        rows.append(row)
        print(("PASS" if row["pass"] else "FAIL"), case["id"])
        for line in row["failed"]:
            print("   -", line[:220])
    print(f"\n{sum(row['pass'] for row in rows)} / {len(rows)} passed")
    if args.out:
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": model, "customer_model": customer_model, "judge": "jev" if args.judge else None, "rows": rows}, indent=1))
        print(f"wrote {path}")

if __name__ == "__main__":
    main()
