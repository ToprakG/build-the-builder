"""Finish a final_run: put the self-test into the build's own trace and save the last-green workflow to Freya.

    .venv310/bin/python scripts/final_trace.py --out <out> --base <first build dir> --green <last-green build dir> [--slot final-X]

1. TRACE: appends to the trace that run.py wrote for the first build (build_report.json["trace"]: trace.txt + events.jsonl) one event per
   Freya self-test scenario (round, id, name, rule type, pass/fail, judge verdict summary, ledger diff) and one per patcher action
   (round, action, field, kept/reverted, fixed, broken), plus the Freya save (workflow id, agent id).
2. FREYA: if the last-green build is not the one run.py already saved (a patched round, a rebuilt policy, or --from-set), it is saved to the
   same slot (updated in place) and exported; FINAL_SAVE=none skips this. The ids go into <out>/build_report.json["saved"].
3. <out>/build_report.json = the first build's report + self_test summary + saved + green; the trace path stays the run.py trace.
"""
import argparse, json, os, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
from selftest_common import retry_5xx  # noqa: E402  (3 tries, 10 s apart, on a 5xx)


def summary(row):
    """(judge verdict summary, ledger diff) of one scenario row."""
    judge = next((x for x in row.get("failed", []) if x.startswith("conversation")), "")
    ledger = [x for x in row.get("failed", []) if not x.startswith("conversation")]
    return (" ".join(judge.split())[:300] if judge else ("pass" if row.get("freya_judge") else "no verdict")), "; ".join(ledger)[:300]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--green", required=True)
    ap.add_argument("--slot", default="")
    ap.add_argument("--stages", default="", help="name=seconds ...: logged as a final_stages event")
    a = ap.parse_args()
    out, base, green = Path(a.out).resolve(), Path(a.base).resolve(), Path(a.green).resolve()
    rep = json.loads((base / "build_report.json").read_text())
    tdir = Path(rep.get("trace") or "")
    cases = {}
    if (out / "selftest" / "cases.json").exists():
        cases = {c["id"]: c for c in json.loads((out / "selftest" / "cases.json").read_text())}
    events, lines = [], []

    def ev(kind, **d):
        events.append({"type": kind, "t": round(time.time(), 3), **d})

    def scen(rno, row, label):
        c = cases.get(row["case"], {})
        j, l = summary(row)
        ev("selftest_scenario", round=rno, run=label, scenario=row["case"], name=c.get("title"), rule_type=row.get("rule") or c.get("rule"),
           passed=row["pass"], judge_verdict=j, ledger_diff=l)
        lines.append(f"selftest {label} {row['case']} [{row.get('rule')}] {c.get('title', '')}: {'PASS' if row['pass'] else 'FAIL'} | judge: {j[:120]} | ledger: {l[:120]}")

    # the 3 extractions of the final vote ran as separate builds with their own traces: their model calls belong to this build's trace
    tot = {"model_calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    for rp in sorted(base.glob("ext*/build_report.json")):
        try:
            et = Path(json.loads(rp.read_text()).get("trace") or "")
            for x in (et / "events.jsonl").read_text().split("\n"):  # not splitlines(): U+2028 etc. inside a JSON string would cut a line
                if not x.strip():
                    continue
                e = json.loads(x)
                if e.get("type") == "model_call":
                    ev("model_call", source=f"final vote {rp.parent.name}", **{k: v for k, v in e.items() if k not in ("type", "t")})
                    tot["model_calls"] += 1
                    tot["tokens_in"] += e.get("tokens_in") or 0
                    tot["tokens_out"] += e.get("tokens_out") or 0
                    tot["cost_usd"] += e.get("cost_usd") or 0.0
            lines.append(f"model calls of {rp.parent.name} (final vote extraction, trace {et.name}) merged into this trace")
        except Exception as e:  # noqa: BLE001
            lines.append(f"could not merge the trace of {rp.parent.name}: {e!r}"[:200])
    if tot["model_calls"]:
        ev("totals_extraction", **tot)
        lines.append("totals of the final-vote extractions: " + " ".join(f"{k}={v}" for k, v in tot.items()))
    br = out / "brief_resolution.json"   # brief references resolved by final_run.sh (scripts/brief_refs.py)
    if br.exists():
        r = json.loads(br.read_text())
        ev("brief_resolution", input=r.get("input"), resolved=r.get("resolved"), log=r.get("log"))
        for e in r.get("log") or []:
            lines.append(f"brief reference: {e.get('file')} refers to Brief {e.get('ref')}: {e.get('status')} {e.get('found_in') or ''} {e.get('path') or e.get('msg') or ''}".strip())
        lines.append("briefs built together: " + ", ".join(r.get("resolved") or []))
    for nm, label in (("sanity.first.json", "first extraction"), ("sanity.json", "compiled policy")):
        sf = base / nm
        if sf.exists():
            r = json.loads(sf.read_text())
            ev("policy_sanity", which=label, degenerate=r.get("degenerate"), reasons=r.get("reasons"))
            lines.append(f"policy sanity ({label}): " + ("DEGENERATE: " + "; ".join(r.get("reasons") or []) if r.get("degenerate") else "ok"))
    if a.stages:
        st = dict(x.split("=", 1) for x in a.stages.split() if "=" in x)
        ev("final_stages", seconds=st)
        lines.append("final_run stages (s): " + " ".join(f"{k}={v}" for k, v in st.items()))
    first = base / "selftest" / "results.json"
    if first.exists():
        r = json.loads(first.read_text())
        lines.append(f"selftest first run: {r['passed']}/{r['cases']} passed, seconds {r.get('seconds')}")
        if r.get("outage"):
            ev("outage", round=0, note="outage signature in the first self-test run (no tool call in any scenario): MISSING, nothing repaired from it")
            lines.append("selftest first run: OUTAGE SIGNATURE (no tool call in any scenario): MISSING, nothing repaired from it")
        ev("selftest_run", round=0, passed=r["passed"], cases=r["cases"], missing=r.get("missing"), seconds=r.get("seconds"), simulation=r.get("simulation"))
        for row in r["rows"]:
            scen(0, row, "round0")
    pr = out / "patch_report.json"
    if pr.exists():
        p = json.loads(pr.read_text())
        for rd in p.get("rounds", []):
            n = rd["round"]
            if rd.get("outage") or rd.get("outage_first_run"):
                ev("outage", round=n, rerun=bool(rd.get("outage_rerun")), result=rd.get("result"))
                lines.append(f"patcher round {n}: outage signature (rerun after 60 s: {bool(rd.get('outage_rerun'))}) -> {rd.get('result') or 'run repeated'}")
            d = Path(p["start"]).parent if False else None
            for fn in (out / "repair" / f"round{n}" / "selftest" / "results.json", ):
                if fn.exists():
                    for row in json.loads(fn.read_text())["rows"]:
                        if row["case"] in (rd.get("subset") or []):
                            scen(n, row, f"round{n}")
            for act in rd.get("actions", []):
                field = act.get("pointer") or act.get("flag") or (act.get("patch") or {}).get("zone") or ""
                used = act["case"] in (rd.get("used") or [])
                kept = bool(rd.get("kept")) and used and act["action"] != "none"
                ev("patcher_action", round=n, scenario=act["case"], action=act["action"], field=field, how=act.get("how"), used=used, kept=kept,
                   reverted=(act["action"] != "none" and used and not kept), fixed=rd.get("fixed"), broken=rd.get("broken"), result=rd.get("result"),
                   why=(act.get("diagnosis") or act.get("why") or "")[:300])
                lines.append(f"patcher round {n} {act['case']}: {act['action']} {field} -> {'KEPT' if kept else ('REVERTED' if used and act['action'] != 'none' else 'not used')}"
                             f" (round: {rd.get('result')}, fixed {rd.get('fixed')}, broken {rd.get('broken')})")
    saved = rep.get("saved") if isinstance(rep.get("saved"), dict) else None
    slot = a.slot or rep.get("slot") or ""
    if os.environ.get("FINAL_SAVE", "freya") == "freya" and os.environ.get("FREYA_API_KEY") and (green != base or not saved):
        try:
            import freya  # noqa: PLC0415
            wf = json.loads((green / "workflow.json").read_text())
            saved = retry_5xx(freya.save_workflow, slot, wf, state_file=str(REPO / ".freya-agents.json"))
            exp = out / f"{slot}.workflow.json"
            retry_5xx(freya.export_workflow, saved.get("workflow_id"), str(exp))
            ev("freya", what="save_workflow (last-green)", slot=slot, **{k: v for k, v in saved.items() if k in ("workflow_id", "agent_id", "version")})
            lines.append(f"freya: saved the last-green workflow to slot {slot}: {saved}")
        except Exception as e:  # noqa: BLE001
            ev("freya_error", error=repr(e)[:300])
            lines.append(f"freya: saving the last-green workflow failed: {e!r}"[:300])
    elif saved:
        ev("freya", what="save_workflow (run.py, first build; last-green is the same build)", slot=slot, **{k: v for k, v in saved.items() if k in ("workflow_id", "agent_id", "version")})
        lines.append(f"freya: workflow saved by the build: {saved}")
        if os.environ.get("FINAL_SAVE", "freya") == "freya" and os.environ.get("FREYA_API_KEY") and saved.get("workflow_id"):
            # the first build is the last-green one: export it too, builder/build.py copies <out>/<slot>.workflow.json to workflows/
            try:
                import freya  # noqa: PLC0415
                retry_5xx(freya.export_workflow, saved["workflow_id"], str(out / f"{slot}.workflow.json"))
                lines.append(f"freya: exported workflow {saved['workflow_id']} to {out / (slot + '.workflow.json')}")
            except Exception as e:  # noqa: BLE001
                ev("freya_error", error=repr(e)[:300])
                lines.append(f"freya: exporting the workflow failed: {e!r}"[:300])
    if tdir.is_dir():
        with (tdir / "events.jsonl").open("a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
        with (tdir / "trace.txt").open("a", encoding="utf-8") as f:
            f.write("".join(f"{time.strftime('%H:%M:%S')} {l}\n" for l in lines))
    final = {**rep, "green": str(green), "saved": saved or rep.get("saved"), "self_test": {
        "scenarios": len(cases), "first_run": json.loads(first.read_text()).get("passed") if first.exists() else None,
        "events_in_trace": len(events), "final_status": "patched" if green != base else "unchanged"}}
    (out / "build_report.json").write_text(json.dumps(final, indent=1, ensure_ascii=False, default=str))
    print(f"final_trace: {len(events)} events appended to {tdir}; saved={saved}")


if __name__ == "__main__":
    main()
