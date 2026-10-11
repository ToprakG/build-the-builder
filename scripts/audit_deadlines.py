"""Offline audit: does the compiled workflow's promise_check offer the brief's deadline? (read-only, no model calls)

    .venv310/bin/python scripts/audit_deadlines.py --set pipe9fx
For every brief of the set and every DEV case (plus a synthetic days_late grid at the band edges), run the compiled `setup`, `facts` and
`promise_check` code nodes in process with a vague day (asked once, then no day) and compare the date in the spoken offer with
deadline = add_bd(call date, band.deadline_business_days, holidays) computed from the testbed's hand-written config. Prints mismatches."""
import argparse
import copy
import datetime
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
from builder.template_cc.compiler import runtime as RT  # noqa: E402
from tests.template_cc.helpers import run_code, call_vars  # noqa: E402
import local_sim as L  # noqa: E402

BRIEFS = ["brief-a", "brief-b", "f1-nordhavn", "f2-ostral", "f3-kestrel", "f4-brightwell", "f5-halden-email", "f6-corvid-memo", "f7-lumen-codes"]


def band_of(bands, days):
    return next((b for b in bands if b["min_days"] <= days <= b["max_days"]), None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="pipe9fx")
    a = ap.parse_args()
    bad = 0
    for b in BRIEFS:
        cases, cfg = L.sources(b)
        pol = json.loads((REPO / "out/template_cc" / a.set / b / "policy.json").read_text())
        wf = json.loads((REPO / "out/template_cc" / a.set / b / "workflow.json").read_text())
        wf = wf.get("workflow", wf)
        hol = {datetime.date.fromisoformat(h) for h in cfg["holidays"]}
        af, fmt = pol["account_fields"], pol.get("product_id_format") or "p{n}"
        jobs = []
        for c in L.dev_cases(cases):
            if len(c["products"]) == 1:
                jobs.append((c["id"], c["variables"]["now"], c["products"][0]))
        # synthetic grid: each band edge, on a Friday, a Monday before a holiday-adjacent date and the first of a month
        for now in ("2026-11-20T15:10", "2026-11-17T11:30", "2026-10-30T09:00"):
            for bd in cfg["bands"]:
                for d in (bd["min_days"], bd["max_days"]):
                    p0 = dict(cases[0]["products"][0])
                    p0["days_late"] = d
                    jobs.append((f"grid:{now[:10]}:{d}d", now, p0))
        n_ok = 0
        for jid, now, prod in jobs:
            row = dict(prod)
            row.setdefault(af["product_id"], fmt.replace("{n}", "1"))
            try:
                v = run_code(wf, "setup", call_vars(pol, now))
                v["products_raw"] = [row]
                v = run_code(wf, "facts", v)
                v1 = run_code(wf, "promise_check", dict(v, offer_intent="vague"))
                v2 = run_code(wf, "promise_check", dict(v1, offer_intent="none"))
                said = v2.get("say_line", "")
            except Exception as e:  # noqa: BLE001
                said = f"EXC {e!r}"
            bd = band_of(cfg["bands"], int(row[af["days_late"]]))
            exp = RT.add_bd(datetime.datetime.fromisoformat(now).date(), bd["deadline_business_days"], hol)
            want = f"{exp.strftime('%A')} {exp.day} {exp.strftime('%B')}"
            if want in said:
                n_ok += 1
            else:
                bad += 1
                print(f"MISMATCH {b} {jid} now={now[:10]} days_late={row[af['days_late']]} band={bd['name']}: expected {want}; said {said[:110]!r}")
        print(f"{b}: {n_ok}/{len(jobs)} offers carry the brief's deadline")
    print("TOTAL mismatches:", bad)


main()
