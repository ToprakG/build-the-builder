"""Offline patcher loop on recorded faulty-build self-tests (no simulation; seconds per iteration, plus the model calls).

    .venv310/bin/python scripts/patcher_eval.py [--faults all|id,id] [--abstain] [--no-llm]

Per recording (out/template_cc/faults/recordings/<brief>/<fault>/): static pre-check, then diagnose() of selftest_repair on every failed
scenario. Table: fault | detected? | action chosen | correct? | patched policy fixes the field?.
detected  = the static pre-check or a diagnosis points at the fault's pointer (R1/R3), or proposes a prompt patch for the removed rule (R4)
correct   = chosen action class == fault.expected_action (and the pointer matches for R1/R3)
fixes     = applying the action gives the clean value at the pointer (R4: the patch contains the removed rule's brief quote)
--abstain: the p6 DEV failure records (compiler bugs, out/template_cc/eval/freya/*.p6*.json, failed rows with a transcript on disk or
           fetched): the right answer is "not patchable"; prints every case where a policy/flag/line/patch is proposed.
"""
import argparse, glob, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts"))
import selftest_repair as R  # noqa: E402
from builder.template_cc import io as tio  # noqa: E402
from builder.template_cc import policy_check  # noqa: E402

REC = REPO / "out" / "template_cc" / "faults" / "recordings"
LABEL = {"policy_field": "R1", "disable_flag": "R2", "line_fix": "R3", "prompt_patch": "R4", "none": "-"}


def brief_files(brief):
    return [f"briefs/{brief}.md"] if brief.startswith("brief-") else [f"testbed/fake_briefs/{brief}/brief.md"]


def under(ptr, q):
    return ptr == q or ptr.startswith(q + "/") or q.startswith(ptr + "/")


def eval_fault(d):
    """One recording = one build with 1..n faults; every fault is scored on its own against the shared failures."""
    F = json.loads((d / "fault.json").read_text())
    faults = F.get("faults") or [F]
    brief_name = F["brief"] if "brief" in F else faults[0]["brief"]
    brief = tio.brief_text(tio.read_briefs([REPO / x for x in brief_files(brief_name)]))
    policy = json.loads((d / "policy.json").read_text())
    rep = json.loads((d / "results.json").read_text())
    cases = {c["id"]: c for c in json.loads((d / "cases.json").read_text())}
    flags = R.active_flags(d)
    static = R.precheck(policy, brief, d)
    fails = [r for r in rep["rows"] if not r["pass"] and r["case"] not in (rep.get("missing") or [])]
    ok_rules = {cases[r["case"]]["rule"] for r in rep["rows"] if r["pass"]} - {cases[r["case"]]["rule"] for r in fails}
    protected = {p for rule in ok_rules for p in R.SOURCES.get(rule, [])} - {p for r in fails for p in R.SOURCES.get(cases[r["case"]]["rule"], [])}
    acts = [R.diagnose(r, cases[r["case"]], policy, brief, flags, d, protected) for r in fails]
    rows = []
    for f in faults:
        ptr0, exp = f["pointers"][0], f["expected_action"]
        pre = None
        if any(under(ptr0, p) for p in static):
            fixed, log = R.precheck_fix(policy, brief, d)
            ok = [x for x in log if x["pointer"] == ptr0 and "new" in x]
            pre = {"action": "line_fix" if ptr0.startswith("/lines/") else "policy_field", "pointer": ptr0, "policy": fixed, "quote": ok[0]["quote"]} if ok else None
        hit = None
        for a in ([pre] if pre else []) + acts:
            if a is None:
                continue
            if exp in ("R1", "R3") and a["action"] in ("policy_field", "line_fix") and under(ptr0, a.get("pointer") or ""):
                hit = a; break
            if exp == "R4" and a["action"] == "prompt_patch" and any(policy_check.norm_text(q["brief_quote"]) in policy_check.norm_text(a["patch"]["after"]) for q in f["extra_rules"]):
                hit = a; break
        chosen = hit or next((a for a in acts if a["action"] != "none"), None)
        fix = None
        if hit and exp in ("R1", "R3"):
            fix = R.get_ptr(hit["policy"], ptr0) == f["clean_value"]
        elif hit and exp == "R4":
            fix = True
        rows.append({"fault": f"{brief_name}/{f['fault_id']}", "expected": exp, "detected": hit is not None, "correct": hit is not None, "fixes": fix,
                     "chosen": (LABEL[chosen["action"]] + " " + (chosen.get("pointer") or chosen.get("flag") or "")) if chosen else "-",
                     "static": [p for p in static if under(ptr0, p)], "failed": [r["case"] + ":" + cases[r["case"]]["rule"] for r in fails],
                     "how": (hit or {}).get("how") or ("static pre-check -> R1" if pre else ""), "why": [a.get("why") or a.get("diagnosis") or "" for a in acts][:3]})
    return rows


def abstain(args):
    import freya  # noqa: PLC0415
    from concurrent.futures import ThreadPoolExecutor
    cases = {}
    for fn in glob.glob(str(REPO / "testbed" / "personas" / "*.json")) + glob.glob(str(REPO / "testbed" / "fake_briefs" / "*" / "cases.json")):
        for c in json.load(open(fn)):
            cases[c["id"]] = c
    jobs, seen = [], set()
    for fn in sorted(glob.glob(str(REPO / "out" / "template_cc" / "eval" / "freya" / "*.p6*.json"))):
        d = json.load(open(fn))
        if d.get("sealed"):
            continue
        for r in d["rows"]:
            if not r["pass"] and r["case"] in cases and (d["brief"], r["case"]) not in seen:
                seen.add((d["brief"], r["case"]))
                jobs.append((d["brief"], r))

    def one(job):
        b, r = job
        bd = Path("/Users/alinebidal/yc-hackathon-vapi/out/template_cc/p6") / b
        if not (bd / "policy.json").exists():
            return None
        policy = json.loads((bd / "policy.json").read_text())
        brief = tio.brief_text(tio.read_briefs([REPO / x for x in brief_files(b)]))
        c = cases[r["case"]]
        case = {"rule": c["family"], "script": c["script"], "expected": c["expected"], "criteria": [{"criterion": x, "brief_quote": ""} for x in c["checks"]]}
        tmp = REPO / "out" / "template_cc" / "faults" / "abstain_tmp" / r["case"]
        (tmp / "selftest" / "transcripts").mkdir(parents=True, exist_ok=True)
        try:
            msgs = (freya.get_call(r["call_id"]).get("messages") or []) if r.get("call_id") else []
        except Exception:  # noqa: BLE001
            msgs = []
        (tmp / "selftest" / "transcripts" / f"{r['case']}.json").write_text(json.dumps({"messages": msgs}))
        (tmp / "zones.final.json").write_text((bd / "zones.final.json").read_text())
        row = {"case": r["case"], "failed": r["failed"], "ledger": {}, "tools": r.get("tools", [])}
        return b, r["case"], R.diagnose(row, case, policy, brief, R.active_flags(bd), tmp, set())

    out = []
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(one, jobs):
            if res is None:
                continue
            b, cid, a = res
            out.append(res)
            print(f"{b:18s} {cid:8s} {LABEL[a['action']]:3s} {(a.get('pointer') or a.get('flag') or '')[:34]:34s} {(a.get('diagnosis') or a.get('why') or '')[:110]}", flush=True)
    bad = [x for x in out if x[2]["action"] != "none"]
    print(f"\nabstain errors: {len(bad)}/{len(out)} dev failures got a proposal (each would be a harmful patch at the final)")


def static_eval(briefs):
    """No simulation: the static pre-check (+ its R1/R3 verifier) on the faulty builds; faults it misses are 'needs a run'."""
    rows = []
    for b in briefs:
        d = REPO / "out" / "template_cc" / "faults" / b / "combo"
        F = json.loads((d / "fault.json").read_text())
        brief = tio.brief_text(tio.read_briefs([REPO / x for x in brief_files(b)]))
        policy = json.loads((d / "policy.json").read_text())
        static = R.precheck(policy, brief, d)
        fixed, log = R.precheck_fix(policy, brief, d)
        for f in F["faults"]:
            ptr0 = f["pointers"][0]
            ok = [x for x in log if under(ptr0, x["pointer"]) and "new" in x]
            det = bool(ok) and f["expected_action"] in ("R1", "R3")
            rows.append({"fault": f"{b}/{f['fault_id']}", "expected": f["expected_action"], "static_hit": [p for p in static if under(ptr0, p)],
                         "detected": det, "fixes": (R.get_ptr(fixed, ptr0) == f["clean_value"]) if det else None,
                         "needs_run": not det})
    print(f"{'fault':40s} {'exp':3s} {'static detected':15s} fixes  needs a Freya run")
    for r in rows:
        print(f"{r['fault']:40s} {r['expected']:3s} {str(r['detected']):15s} {str(r['fixes']):6s} {r['needs_run']}   static={r['static_hit']}")
    n = len(rows)
    print(f"static-only: detected {sum(r['detected'] for r in rows)}/{n}, fixes {sum(bool(r['fixes']) for r in rows)}/{n}, still need a run {sum(r['needs_run'] for r in rows)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--static", default="", help="brief,brief: static-only eval of the faulty combo builds (no simulation)")
    ap.add_argument("--faults", default="all")
    ap.add_argument("--abstain", action="store_true")
    a = ap.parse_args()
    if a.static:
        return static_eval(a.static.split(","))
    if a.abstain:
        return abstain(a)
    dirs = sorted(p.parent for p in REC.glob("*/*/results.json"))
    if a.faults != "all":
        dirs = [d for d in dirs if f"{d.parent.name}/{d.name}" in a.faults.split(",")]
    rows = [r for d in dirs for r in eval_fault(d)]
    print(f"{'fault':40s} {'exp':3s} {'detected':8s} {'action chosen':42s} {'correct':7s} fixes")
    for r in rows:
        print(f"{r['fault']:40s} {r['expected']:3s} {str(r['detected']):8s} {r['chosen'][:42]:42s} {str(r['correct']):7s} {r['fixes']}   static={r['static'][:2]} failed={r['failed']}")
    n = len(rows) or 1
    print(f"detection {100 * sum(r['detected'] for r in rows) // n}% | correct-action {100 * sum(r['correct'] for r in rows) // n}% | "
          f"fixes-the-field {100 * sum(bool(r['fixes']) for r in rows) // n}%  (n={len(rows)})")


main()
