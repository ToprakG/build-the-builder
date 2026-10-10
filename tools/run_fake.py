"""Score a testbed brief the same way the final is scored.

The mock bank for that brief is opened on this machine and published with a
cloudflared URL. Personas come from the testbed cases. Each case is one Freya
simulation, one at a time. The ledger is scored with tools/score.py.

    python3 tools/run_fake.py --brief f1-nordhavn --limit 1
    python3 tools/run_fake.py --brief f1-nordhavn --public-url https://already-up.example

Run seed/precheck_cases.py first. It checks the oracle and does not call Freya.
"""
import argparse, hashlib, json, os, pathlib, signal, subprocess, sys, time, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from envfile import load_env
load_env()
import freya
from score import score_ledger
from run_cases import persona_for
from tools.driver.mockbank import load_testbed

PORT = int(os.environ.get("PORT", "8787"))


def _python():
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv if venv.exists() else pathlib.Path(sys.executable))


def tools_for(brief_id):
    if brief_id in ("brief-a", "brief-b"):
        return ROOT / "tools" / "openapi.json"
    return ROOT / "testbed" / "fake_briefs" / brief_id / "tools.openapi.json"


def bank_http(origin, path, body=None):
    headers = {
        "user-agent": "Mozilla/5.0 (build-the-builder)",
        "content-type": "application/json",
        "x-team-key": os.environ["FREYA_BANK_TEAM_KEY"],
    }
    request = urllib.request.Request(
        origin.rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        return json.load(error)


def wait_http(url, seconds=60):
    deadline = time.time() + seconds
    last = "no attempt"
    while time.time() < deadline:
        request = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0 (build-the-builder)"})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                if response.status == 200:
                    return
                last = f"status {response.status}"
        except Exception as error:
            last = str(error)[:200]
        time.sleep(1)
    raise RuntimeError(f"no answer from {url}: {last}")


def _log(name):
    path = ROOT / "results" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "w")


def start_bank(brief_id):
    log = _log("mockbank.log")
    proc = subprocess.Popen(
        [_python(), "tools/driver/mockbank.py", "--brief", brief_id],
        cwd=ROOT,
        env={**os.environ, "PORT": str(PORT)},
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        wait_http(f"http://127.0.0.1:{PORT}/tools/openapi.json")
    except Exception:
        proc.kill()
        log.flush()
        tail = (ROOT / "results" / "mockbank.log").read_text()[-400:]
        raise RuntimeError(f"mock bank did not start. {tail}") from None
    return proc


def start_tunnel():
    import re
    log = _log("cloudflared.log")
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{PORT}"],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    url = None
    deadline = time.time() + 40
    path = ROOT / "results" / "cloudflared.log"
    while time.time() < deadline and proc.poll() is None:
        text = path.read_text() if path.exists() else ""
        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
        if match:
            url = match.group(0)
            break
        time.sleep(0.5)
    if not url:
        proc.kill()
        raise RuntimeError("cloudflared did not print a public URL")
    wait_http(url + "/tools/openapi.json", seconds=90)
    return proc, url


def publish(brief_id, config, public_origin):
    from seed.graph import build_workflow
    tools_api = json.loads(tools_for(brief_id).read_text())
    tools_api["servers"] = [{"url": public_origin.rstrip("/") + "/v1"}]
    workflow = build_workflow(config, tools_api)
    variables = dict(workflow.get("test_variables") or {})
    variables.pop("customer_id", None)
    workflow["test_variables"] = variables
    state_path = ROOT / ".freya-agents.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if brief_id in state:
        workflow_id = state[brief_id]["workflow_id"]
        freya.api("PUT", f"/api/v2/workflow/{workflow_id}", {"workflow": workflow})
    else:
        created = freya.create_workflow(f"{brief_id} {time.strftime('%m%d-%H%M%S')}", workflow)
        state[brief_id] = {"workflow_id": created["workflow_id"], "agent_id": created["agent_id"]}
        workflow_id = created["workflow_id"]
    versions = freya.api("GET", f"/api/v2/versions/workflow/{workflow_id}", workspaceId=freya.workspace_id())["versions"]
    newest = max(versions, key=lambda item: item["versionNumber"])
    if not newest.get("isPublished"):
        freya.api("POST", f"/api/v2/versions/workflow/{workflow_id}/publish", {"versionId": newest["id"]})
    state[brief_id]["version"] = newest["versionNumber"]
    state_path.write_text(json.dumps(state, indent=1))
    return state[brief_id]


def personas_for(cases):
    store_path = ROOT / ".personas.json"
    store = json.loads(store_path.read_text()) if store_path.exists() else {}
    spec = {case["id"]: persona_for(case, {"identity": case["identity"]}) for case in cases}
    key = {case_id: case_id + ":" + hashlib.sha1(json.dumps(item, sort_keys=True).encode()).hexdigest()[:10] for case_id, item in spec.items()}
    new = [case for case in cases if key[case["id"]] not in store]
    if new:
        made = freya.create_personas([spec[case["id"]] for case in new])["personas"]
        store.update({key[case["id"]]: item["id"] for case, item in zip(new, made)})
        store_path.write_text(json.dumps(store, indent=1))
    return {case["id"]: store[key[case["id"]]] for case in cases}


def score_case(origin, agent_id, case, persona_id):
    variables = bank_http(origin, "/v1/practice/sessions", {"case": case["id"]})["variables"]
    sim_id, it_id, status = freya.run_simulation(
        agent_id, case["id"], {persona_id: variables}, max_seconds=180, concurrency=1,
    )
    runs = freya.get_iteration(sim_id, it_id)["runs"]
    judge = next((item for item in runs if item.get("personaId") == persona_id), {})
    ledger = bank_http(origin, "/v1/practice/ledger/" + variables["customer_id"])
    if not isinstance(ledger, dict) or "calls" not in ledger:
        return {
            "id": case["id"], "pass": False, "failed": [f"ledger unavailable: {ledger}"],
            "simulation_id": sim_id, "iteration_id": it_id, "status": status,
            "call_id": judge.get("callId"), "customer_id": variables["customer_id"], "silent": False,
        }
    scored = score_ledger(case, ledger)
    if judge.get("outcome") != "pass":
        scored["failed"].append("conversation: " + " ".join(str(judge.get("reason") or "no verdict").split())[:400])
        scored["pass"] = False
    call_id = judge.get("callId")
    silent = False
    if call_id:
        try:
            call = freya.get_call(call_id)
            tools = [event for event in call.get("messages") or [] if '"function_call_start"' in (event.get("message") or "")]
            silent = call.get("endedReason") == "customer-ended-call" and not tools
        except Exception:
            silent = False
    return {
        "id": case["id"],
        "pass": scored["pass"],
        "failed": scored["failed"],
        "simulation_id": sim_id,
        "iteration_id": it_id,
        "status": status,
        "call_id": call_id,
        "customer_id": variables["customer_id"],
        "silent": silent,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only")
    parser.add_argument("--public-url")
    parser.add_argument("--out")
    args = parser.parse_args()
    cases, config = load_testbed(args.brief)
    if args.only:
        wanted = set(args.only.split(","))
        cases = [case for case in cases if case["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]
    bank = tunnel = None
    try:
        print(f"mock bank for {args.brief} on 127.0.0.1:{PORT}", flush=True)
        bank = start_bank(args.brief)
        if args.public_url:
            origin = args.public_url.rstrip("/")
        else:
            tunnel, origin = start_tunnel()
        print(f"public {origin}", flush=True)
        saved = publish(args.brief, config, origin)
        agent_id = saved["agent_id"]
        print(f"workflow {saved.get('workflow_id')} agent {agent_id} version {saved.get('version')}", flush=True)
        ids = personas_for(cases)
        rows = []
        for case in cases:
            print(f"\n== {case['id']} {case['title']}", flush=True)
            row = score_case(origin, agent_id, case, ids[case["id"]])
            if row["silent"]:
                print("   silent hangup, one retry", flush=True)
                row = score_case(origin, agent_id, case, ids[case["id"]])
            rows.append(row)
            print(("PASS" if row["pass"] else "FAIL"), case["id"], flush=True)
            for line in row["failed"]:
                print("   -", line[:220], flush=True)
        passed = sum(row["pass"] for row in rows)
        print(f"\n{passed} / {len(rows)} passed", flush=True)
        if args.out:
            path = pathlib.Path(args.out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"brief": args.brief, "public": origin, "agent_id": agent_id, "passed": passed, "total": len(rows), "rows": rows}, indent=1))
            print(f"wrote {path}")
    finally:
        for proc in (tunnel, bank):
            if proc and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)


if __name__ == "__main__":
    main()
