"""Evaluate T-CC agents on the testbed with FREYA SIMULATIONS (Freya plays the customer and judges the conversation),
plus build-the-builder's local judge (Jev) under a hard OpenRouter budget.

DEV EVALUATION ONLY, outside the builder: it reads the testbed answers to score the agent.

Per brief (one Freya simulation at a time per workspace, so briefs run in sequence):
  vendor mock bank with that brief's cases + ground-truth config behind a cloudflared tunnel -> the compiled T-CC
  workflow published to `tcc-<brief>-eval`, pointed at that bank -> personas (kit persona_for) -> one simulation with
  every chosen case -> ledger scored with score_ledger + Freya's own conversation verdict -> the vendor local judge
  (Judge C / Jev) on each transcript while the OpenRouter spend of this run stays under --or-budget.

    .venv310/bin/python scripts/tcc_eval_freya.py --brief all --limit 10 --or-budget 3
"""
import argparse
import re
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "vendor" / "build-the-builder"
for p in (str(REPO), str(VENDOR / "tools" / "driver"), str(VENDOR / "tools"), str(VENDOR)):  # vendor first: its freya
    sys.path.insert(0, p)

import freya  # noqa: E402  (vendor freya.py: simulations + personas)
from judge import judge_transcript  # noqa: E402
from run_cases import persona_for  # noqa: E402
from score import score_ledger  # noqa: E402

from builder.template_cc import freya_retry  # noqa: E402
from builder.template_cc.apimodel import ApiModel  # noqa: E402
from builder.template_cc.trace import Trace  # noqa: E402
from builder.template_cc.vendor_runner import VendorRunner, point_at  # noqa: E402

freya_retry.install(freya)  # GET/PUT 5xx: 3 tries, 10 s apart (the kit's freya.py does not retry)

ALL = ["brief-a", "brief-b"] + sorted(p.name for p in (REPO / "testbed" / "fake_briefs").iterdir() if p.is_dir())
SHARED = ""
OUT = REPO / "out" / "template_cc" / "eval" / "freya"
STATE_FILE = os.environ.get("FREYA_STATE_FILE") or str(REPO / ".freya-agents.json")  # another workspace gets its own slots
PERSONAS = OUT / ("personas.json" if not os.environ.get("FREYA_STATE_FILE")
                  else "personas." + Path(os.environ["FREYA_STATE_FILE"]).stem.strip(".") + ".json")  # our own persona cache (per workspace)


def sources(brief):
    tb = REPO / "testbed"
    if brief in ("brief-a", "brief-b"):
        x = brief[-1]
        return (json.loads((tb / "personas" / f"{brief}.cases.json").read_text()),
                json.loads((tb / "configs" / f"{brief}.config.json").read_text()),
                REPO / "tools" / "openapi.json", REPO / "out" / "template_cc" / f"judge-{x}" / "workflow.json")
    d = tb / "fake_briefs" / brief
    return (json.loads((d / "cases.json").read_text()), json.loads((d / "config.json").read_text()),
            d / "tools.openapi.json", REPO / "out" / "template_cc" / "fake" / brief / "workflow.json")


def pick(cases, limit, skip=0):
    by = {}
    for c in sorted(cases, key=lambda c: c["id"]):
        by.setdefault(c["family"], []).append(c)
    order, i = [], 0
    while any(i < len(v) for v in by.values()):
        for fam in sorted(by):
            if i < len(by[fam]):
                order.append(by[fam][i])
        i += 1
    order = order[skip:]
    return order[:limit] if limit else order


class Budget:
    """The OpenRouter spend of this run, read from the key's usage counter."""

    def __init__(self, dollars):
        self.cap, self.start = dollars, self.usage()

    @staticmethod
    def usage():
        req = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                     headers={"Authorization": "Bearer " + os.environ.get("OPENROUTER_API_KEY", "")})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return float(json.load(r)["data"]["usage"])
        except Exception:
            return None

    def spent(self):
        now = self.usage()
        return None if now is None or self.start is None else now - self.start

    def left(self):
        s = self.spent()
        return s is not None and s < self.cap


def personas_for(cases):
    OUT.mkdir(parents=True, exist_ok=True)
    store = json.loads(PERSONAS.read_text()) if PERSONAS.exists() else {}
    spec = {c["id"]: persona_for(c, {"identity": c["identity"]}, c.get("style")) for c in cases}
    key = {i: i + ":" + hashlib.sha1(json.dumps(s, sort_keys=True).encode()).hexdigest()[:10] for i, s in spec.items()}
    new = [c for c in cases if key[c["id"]] not in store]
    for k in range(0, len(new), 25):
        chunk = new[k:k + 25]
        made = freya.create_personas([spec[c["id"]] for c in chunk])["personas"]
        store.update({key[c["id"]]: p["id"] for c, p in zip(chunk, made)})
        PERSONAS.write_text(json.dumps(store, indent=1))
    return {c["id"]: store[key[c["id"]]] for c in cases}


def bank(origin, path, body=None):
    req = urllib.request.Request(origin + path, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET",
                                 headers={"content-type": "application/json", "user-agent": "Mozilla/5.0 (tcc-eval)",
                                          "x-team-key": os.environ["FREYA_BANK_TEAM_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


class NoCases(Exception):
    pass


def category(reason: str) -> str:
    """The failed criterion without its text: outcome / promises / notes / transfers / judge."""
    w = re.match(r"\w+", reason or "")
    w = w.group(0) if w else "other"
    return "judge" if w == "conversation" else w


def evaluate(brief, limit, skip, port, budget, concurrency, tag, only=None, sealed=False):
    cases, config, api_path, wf_path = sources(brief)
    chosen = [c for c in sorted(cases, key=lambda c: c["id"]) if c["id"] in only] if only else pick(cases, limit, skip)
    if not chosen:
        raise NoCases(brief)
    tdir = (OUT.parent / "sealed" / "traces") if sealed else (REPO / "traces")  # sealed runs: nothing readable outside sealed/
    trace = Trace(tdir / f"tcc-eval-freya-{brief}-{time.strftime('%Y%m%d-%H%M%S')}", {"brief": brief, "cases": len(chosen)})
    runner = VendorRunner(f"tcc-{brief}", config, ApiModel.load(api_path), trace, freya, port=port, slot_suffix="-eval")
    if SHARED:
        runner.use_shared(SHARED.rstrip("/") + "/" + brief)
    t0 = time.time()
    try:
        runner._ensure(chosen)  # the vendor mock bank with these cases, behind a tunnel
        wf = json.loads(wf_path.read_text())
        saved = freya.save_workflow(runner.slot, point_at(wf, runner.origin), state_file=STATE_FILE)
        agent_id = saved["agent_id"]
        trace.freya("save_workflow (eval slot)", {"slot": runner.slot, **saved})
        ids = personas_for(chosen)
        sessions = {c["id"]: bank(runner.origin, "/v1/practice/sessions", {"case": c["id"]})["variables"] for c in chosen}
        sim_id, it_id, status = freya.run_simulation(agent_id, f"tcc eval {brief} {tag}",
                                                     {ids[c["id"]]: sessions[c["id"]] for c in chosen},
                                                     concurrency=min(concurrency, 10))
        # the analysis of a finished run can arrive late: wait at most 120 s; a run without a verdict is MISSING (neither pass nor fail)
        t_wait = time.time() + 120
        while True:
            verdicts = {r["personaId"]: r for r in freya.get_iteration(sim_id, it_id)["runs"]}
            if all(v.get("outcome") not in (None, "queued", "running") for v in verdicts.values()) or time.time() > t_wait:
                break
            time.sleep(10)
        missing = []
        rows = []
        for c in chosen:
            ledger = bank(runner.origin, "/v1/practice/ledger/" + sessions[c["id"]]["customer_id"])
            scored = score_ledger(c, ledger)
            v = verdicts.get(ids[c["id"]], {})
            if v.get("outcome") in (None, "queued", "running") or (v.get("outcome") == "error" and "analysis never arrived" in str(v.get("reason"))):
                # the analysis never arrived (Freya's analysis service, seen 'Call ended but analysis never arrived (300s)'): not a failure
                missing.append(c["id"])
                continue
            freya_ok = v.get("outcome") == "pass"
            local = None
            if v.get("callId") and budget.left():
                try:
                    msgs = freya.get_call(v["callId"]).get("messages") or []
                    j = judge_transcript(msgs, c)
                    local = {"pass": j["pass"], "status": j["status"],
                             "failed": [x["check"] for x in j["checks"] if x["answer"] != "yes"]}
                except Exception as e:  # noqa: BLE001
                    local = {"pass": None, "status": f"error {e!r}"[:120], "failed": []}
            failed = list(scored["failed"])
            if not freya_ok:
                failed.append("conversation (Freya judge): " + " ".join(str(v.get("reason") or "no verdict").split())[:300])
            rows.append({"case": c["id"], "family": c["family"], "level": c.get("level"), "pass": scored["pass"] and freya_ok,
                         "ledger_pass": scored["pass"], "freya_judge": freya_ok, "local_judge": local, "failed": failed,
                         "call_id": v.get("callId"), "tools": [x["tool"] for x in ledger.get("calls") or []]})
    finally:
        runner.close()
    if freya_retry.is_outage(rows) or (not rows and missing):  # (also: every case without a verdict, e.g. a cancelled queue that never started)  # no customer made any tool call: a Freya outage, not an agent failure -> every case MISSING, no result file
        missing, rows = [c["id"] for c in chosen], []
        print(f"{brief}: FREYA OUTAGE signature (no tool call in any of {len(chosen)} cases): all MISSING, not counted", flush=True)
        (OUT).mkdir(parents=True, exist_ok=True)
        (OUT / f"{brief}.{tag}.outage.json").write_text(json.dumps({"brief": brief, "tag": tag, "cases": len(chosen), "missing": missing,
                                                                    "outage": True, "simulation": sim_id}, indent=1))
        trace.close("outage", cases=len(chosen))
        return {"brief": brief, "tag": tag, "cases": len(chosen), "passed": 0, "local_passed": 0, "local_judged": 0, "seconds": round(time.time() - t0),
                "missing": missing, "outage": True, "rows": []}
    rep = {"brief": brief, "tag": tag, "cases": len(chosen), "passed": sum(r["pass"] for r in rows),
           "local_judged": sum(1 for r in rows if r["local_judge"] and r["local_judge"]["pass"] is not None),
           "local_passed": sum(1 for r in rows if r["local_judge"] and r["local_judge"]["pass"] is True and r["ledger_pass"]),
           "seconds": round(time.time() - t0), "simulation": sim_id, "status": status, "missing": missing, "rows": rows}
    OUT.mkdir(parents=True, exist_ok=True)
    if sealed:  # GATE / TEST: the full record stays in sealed/; the shared folder only gets pass/fail and the failed criterion category
        (OUT.parent / "sealed").mkdir(parents=True, exist_ok=True)
        (OUT.parent / "sealed" / f"{brief}.{tag}.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
        out_rep = {**{k: rep[k] for k in ("brief", "tag", "cases", "passed", "seconds", "status")}, "sealed": True,
               "rows": [{"case": r["case"], "family": r["family"], "level": r["level"], "pass": r["pass"],
                         "failed": sorted({category(x) for x in r["failed"]})} for r in rows]}
    else:
        out_rep = rep
    (OUT / f"{brief}.{tag}.json").write_text(json.dumps(out_rep, indent=1, ensure_ascii=False))
    trace.close("ok", passed=rep["passed"], cases=rep["cases"])
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", default="all")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--skip", type=int, default=0, help="skip the first N cases of the family-spread order")
    ap.add_argument("--or-budget", type=float, default=3.0, help="max OpenRouter dollars for the local judge in this run")
    ap.add_argument("--concurrency", type=int, default=10, help="parallel calls inside one Freya simulation")
    ap.add_argument("--port", type=int, default=8830)
    ap.add_argument("--tag", default="s10")
    ap.add_argument("--tag-suffix", default="", help="appended to --tag")
    ap.add_argument("--cases", default="", help="comma-separated case ids (instead of --skip/--limit); briefs without any are skipped")
    ap.add_argument("--sealed", action="store_true", help="GATE/TEST run: transcripts and reasons go to sealed/ (also automatic for tags ending in g<n>/t<n>)")
    ap.add_argument("--shared-origin", default="")
    a = ap.parse_args()
    global SHARED
    SHARED = a.shared_origin
    _orig_api = freya.api

    def _api(method, path, *args, **kw):  # remember every simulation id: a stuck one can then be looked up (documented GET) and reported
        r = _orig_api(method, path, *args, **kw)
        if method == "POST" and path.rstrip("/") == "/api/v2/simulations":
            sim = (r.get("simulation") or r) if isinstance(r, dict) else {}
            body = args[0] if args and isinstance(args[0], dict) else kw.get("body") or {}
            with open(OUT.parent / "sim_ids.log", "a") as f:
                f.write(f"{time.strftime('%m-%d %H:%M:%S')} {sim.get('id')} {body.get('name')}\n")
        return r
    freya.api = _api
    budget = Budget(a.or_budget)
    print(f"OpenRouter usage at start: {budget.start}; local-judge budget ${a.or_budget}", flush=True)
    tag = a.tag + a.tag_suffix
    only = {x.strip() for x in a.cases.split(",") if x.strip()} or None
    sealed = a.sealed or bool(re.search(r"[gt]\d+$", tag))
    for b in (ALL if a.brief == "all" else a.brief.split(",")):
        try:
            r = evaluate(b, a.limit, a.skip, a.port, budget, a.concurrency, tag, only, sealed)
            sp = budget.spent()
            print(f"{b}: {r['passed']}/{r['cases']} passed (bank records + Freya judge); local judge + records "
                  f"{r['local_passed']}/{r['local_judged']} judged; {r['seconds']}s; OpenRouter spent so far "
                  f"${sp if sp is None else round(sp, 2)}" + (f"; MISSING {len(r['missing'])}: {','.join(r['missing'])}" if r.get("missing") else ""), flush=True)
        except NoCases:
            print(f"{b}: no matching cases, skipped", flush=True)
        except Exception as e:  # noqa: BLE001 - one brief failing must not stop the others
            print(f"{b}: ERROR {e!r}"[:300], flush=True)


if __name__ == "__main__":
    main()
