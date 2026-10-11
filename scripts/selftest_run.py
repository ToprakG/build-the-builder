"""Run the self-test scenarios of one build on Freya and score them (ledger of our own mock bank + Freya's judge verdict).

    .venv310/bin/python scripts/selftest_run.py --out-dir <build dir> --cases cases.json --api tools.openapi.json [--only ST-01,ST-02]

Needs <build dir>/policy.json and workflow.json. Starts a mock bank for THIS policy and the scenarios (vendor mockbank, in its own
process) behind a cloudflared quick tunnel, saves the workflow to its own Freya slot with the tools pointed at that bank, creates one
Freya persona per scenario (prompt = persona text, successCriteria = the brief-quoted criteria), runs ONE simulation, then reads
every ledger. Freya plays the customer and judges; no OpenRouter call is made. Writes <build dir>/selftest/results.json and
selftest/transcripts/<id>.json. Wrap it in selftest_run.sh: that takes a lane lock, so no two evals ever share a workspace.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "vendor" / "build-the-builder"
for p in (str(REPO), str(VENDOR / "tools" / "driver"), str(VENDOR / "tools"), str(VENDOR)):  # vendor first: its freya
    sys.path.insert(0, p)

import freya  # noqa: E402  (vendor freya.py: simulations + personas)
from selftest_common import install as _retry_freya, outage_signature, infra_error  # noqa: E402

_retry_freya(freya)  # every Freya call: 3 tries, 10 s apart, on a 5xx
from run_cases import persona_for  # noqa: E402
from score import score_ledger  # noqa: E402

from builder.template_cc.apimodel import ApiModel  # noqa: E402
from builder.template_cc.trace import Trace  # noqa: E402
from builder.template_cc.vendor_runner import VendorRunner, point_at  # noqa: E402

STATE_FILE = os.environ.get("FREYA_STATE_FILE") or str(REPO / ".freya-agents.json")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def bank(origin, path, body=None):
    req = urllib.request.Request(origin + path, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET",
                                 headers={"content-type": "application/json", "user-agent": "Mozilla/5.0 (tcc-selftest)",
                                          "x-team-key": os.environ["FREYA_BANK_TEAM_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def persona_cache():
    """Personas belong to a workspace: one cache file per Freya key."""
    key = hashlib.sha1(os.environ.get("FREYA_API_KEY", "").encode()).hexdigest()[:8]
    root = Path(os.environ.get("SELFTEST_PERSONA_DIR") or REPO / "out" / "template_cc" / "eval" / "freya")
    root.mkdir(parents=True, exist_ok=True)
    return root / f"selftest_personas.{key}.json"


def personas_for(cases):
    path = persona_cache()
    store = json.loads(path.read_text()) if path.exists() else {}
    spec = {}
    for c in cases:
        s = persona_for({**c, "checks": c["checks"] or ["The call is conducted normally."]}, {"identity": c["identity"]}, c.get("style"))
        s["tags"] = ["hackathon", "selftest"]
        spec[c["id"]] = s
    key = {i: i + ":" + hashlib.sha1(json.dumps(s, sort_keys=True).encode()).hexdigest()[:10] for i, s in spec.items()}
    new = [c for c in cases if key[c["id"]] not in store]
    for k in range(0, len(new), 25):
        chunk = new[k:k + 25]
        made = freya.create_personas([spec[c["id"]] for c in chunk])["personas"]
        store.update({key[c["id"]]: p["id"] for c, p in zip(chunk, made)})
        path.write_text(json.dumps(store, indent=1))
    return {c["id"]: store[key[c["id"]]] for c in cases}


def score_case(case, ledger):
    """strict: the vendor score_ledger against case.expected. loose: pass when ANY of case.allow passes (outcome class is what counts)."""
    if case.get("ledger") == "judge":  # only the structural checks of score_ledger (closed, no early account); the Freya judge decides the rest
        led = ledger
        own = {"outcome": led.get("outcome"), "reason": led.get("reason"), "promises": led.get("promises") or [], "notes": led.get("notes") or [],
               "callbacks": led.get("callbacks") or [], "transfers": led.get("transfers") or []}
        return score_ledger({**case, "expected": own}, ledger)
    if case.get("ledger") != "loose":
        return score_ledger(case, ledger)
    best = None
    for alt in case.get("allow") or [case["expected"]]:
        r = score_ledger({**case, "expected": alt}, ledger)
        if r["pass"]:
            return r
        best = best or r
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--api", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--tag", default="st")
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--results", default="results.json", help="file name inside <out-dir>/selftest/")
    a = ap.parse_args()
    out = Path(a.out_dir)
    sd = out / "selftest"
    (sd / "transcripts").mkdir(parents=True, exist_ok=True)
    policy = json.loads((out / "policy.json").read_text())
    wf = json.loads((out / "workflow.json").read_text())
    cases = json.loads(Path(a.cases).read_text())
    only = {x for x in a.only.split(",") if x}
    chosen = [c for c in cases if not only or c["id"] in only]
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # `timeout` kills the run: the finally block still stops bank + tunnel
    T = {}
    t0 = time.time()
    trace = Trace(REPO / "traces" / f"tcc-selftest-{out.name}-{time.strftime('%Y%m%d-%H%M%S')}", {"out": str(out), "cases": len(chosen)})
    slot = os.environ.get("SELFTEST_SLOT") or "st-" + hashlib.sha1(str(out.resolve()).encode()).hexdigest()[:6]  # one slot per brief run: final_run.sh sets it
    runner = VendorRunner(slot, policy, ApiModel.load(a.api), trace, freya, port=free_port(), slot_suffix="")
    rows, missing, infra, sim_id, status = [], [], [], None, None
    try:
        runner._ensure(chosen)  # mock bank + cloudflared tunnel
        T["bank_tunnel_s"] = round(time.time() - t0)
        saved = freya.save_workflow(runner.slot, point_at(wf, runner.origin), state_file=STATE_FILE)
        T["publish_s"] = round(time.time() - t0) - T["bank_tunnel_s"]
        ids = personas_for(chosen)
        sessions = {c["id"]: bank(runner.origin, "/v1/practice/sessions", {"case": c["id"]})["variables"] for c in chosen}
        t1 = time.time()
        sim_id, it_id, status = freya.run_simulation(saved["agent_id"], f"selftest {out.name} {a.tag}",
                                                     {ids[c["id"]]: sessions[c["id"]] for c in chosen},
                                                     concurrency=min(a.concurrency, 10))
        T["simulation_s"] = round(time.time() - t1)
        t_wait = time.time() + 120  # the analysis can arrive late: a scenario without a verdict is MISSING, neither pass nor fail
        while True:
            verdicts = {r["personaId"]: r for r in freya.get_iteration(sim_id, it_id)["runs"]}
            if all(v.get("outcome") not in (None, "queued", "running") for v in verdicts.values()) or time.time() > t_wait:
                break
            time.sleep(10)
        for c in chosen:
            ledger = bank(runner.origin, "/v1/practice/ledger/" + sessions[c["id"]]["customer_id"])
            scored = score_case(c, ledger)
            v = verdicts.get(ids[c["id"]], {})
            if v.get("outcome") in (None, "queued", "running"):
                missing.append(c["id"])
                continue
            freya_ok = v.get("outcome") == "pass"
            failed = list(scored["failed"])
            if not freya_ok:
                failed.append("conversation (Freya judge): " + " ".join(str(v.get("reason") or "no verdict").split())[:400])
            transcript = []
            if v.get("callId"):
                try:
                    transcript = freya.get_call(v["callId"]).get("messages") or []
                except Exception as e:  # noqa: BLE001
                    transcript = [{"error": repr(e)[:200]}]
            if infra_error(transcript):   # a tool call died on the tunnel / platform (530, 5xx): MISSING, not a failure, never repaired from
                missing.append(c["id"]); infra.append(c["id"])
                (sd / "transcripts" / f"{c['id']}.infra.json").write_text(json.dumps({"messages": transcript, "ledger": ledger}, indent=1, ensure_ascii=False))
                continue
            (sd / "transcripts" / f"{c['id']}.json").write_text(json.dumps({"messages": transcript, "ledger": ledger}, indent=1, ensure_ascii=False))
            rows.append({"case": c["id"], "rule": c.get("rule"), "pass": scored["pass"] and freya_ok, "ledger_pass": scored["pass"],
                         "freya_judge": freya_ok, "failed": failed, "call_id": v.get("callId"),
                         "ledger": {k: ledger.get(k) for k in ("outcome", "reason", "promises", "notes", "callbacks", "transfers")},
                         "tools": [x["tool"] for x in ledger.get("calls") or []]})
    finally:
        runner.close()
        T["total_s"] = round(time.time() - t0)
        rep = {"outage": outage_signature(rows, missing) or 2 * len(infra) >= len(chosen) > 1, "infra_errors": infra, "tag": a.tag, "cases": len(chosen), "passed": sum(r["pass"] for r in rows), "missing": missing, "simulation": sim_id,
               "status": status, "seconds": T, "rows": rows}
        (sd / a.results).write_text(json.dumps(rep, indent=1, ensure_ascii=False))
        trace.close("ok", passed=rep["passed"], cases=rep["cases"])
    if rep["outage"]:
        print("selftest_run: OUTAGE SIGNATURE (no tool call in any scenario): this run is MISSING, not a result", flush=True)
    print(f"selftest_run: {rep['passed']}/{rep['cases']} passed, missing {len(missing)}, {T}")
    for r in rows:
        print(f"  {r['case']} {r['rule']:18s} {'PASS' if r['pass'] else 'FAIL'}" + ("" if r["pass"] else "  " + " | ".join(r["failed"])[:240]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
