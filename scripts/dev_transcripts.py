"""Fetch the REAL Freya transcripts (GET call, read-only) of the failing p6 DEV repeats of every dead (0/2) and flaky (1/2) DEV case.

    .venv310/bin/python scripts/dev_transcripts.py
Writes out/template_cc/eval/dev_transcripts/<brief>/<case>.<tag>.json (messages with role / text / nodeId / seconds; the big variable
tables are dropped) and out/template_cc/eval/dev_cases.json (one record per case: p6 verdicts, the Freya judge text, and the
pass/fail history of the case over every other eval tag). DEV cases only (tags p6r1 / p6r2); GATE and sealed files are never read.
"""
import glob
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "vendor" / "build-the-builder"))
EVAL = REPO / "out/template_cc/eval"
OUT = EVAL / "dev_transcripts"
BRIEFS = ["brief-a", "brief-b", "f1-nordhavn", "f2-ostral", "f3-kestrel", "f4-brightwell", "f5-halden-email", "f6-corvid-memo", "f7-lumen-codes"]


def tag_rows(tag):
    out = {}
    for f in glob.glob(str(EVAL / "freya" / f"*.{tag}.json")):
        d = json.load(open(f))
        if d.get("sealed"):
            continue
        for r in d["rows"]:
            out[(d["brief"], r["case"])] = r
    return out


def main():
    r1, r2 = tag_rows("p6r1"), tag_rows("p6r2")
    others = {}
    for f in glob.glob(str(EVAL / "freya" / "*.json")):
        tag = Path(f).name.split(".", 1)[1].rsplit(".json", 1)[0]
        if tag in ("p6r1", "p6r2") or re.search(r"g\d+$", tag) or tag.endswith("t1") or "personas" in f or "freya-agents" in f:
            continue                                   # GATE / TEST (…g1, …t1) are never read
        try:
            d = json.load(open(f))
        except Exception:  # noqa: BLE001
            continue
        if d.get("sealed") or "rows" not in d:
            continue
        for r in d["rows"]:
            others.setdefault((d["brief"], r["case"]), {})[tag] = bool(r["pass"])
    cases = []
    for k in sorted(set(r1) & set(r2)):
        n = bool(r1[k]["pass"]) + bool(r2[k]["pass"])
        if n == 2:
            continue
        cases.append({"brief": k[0], "case": k[1], "family": r1[k].get("family"), "kind": "flaky" if n == 1 else "dead",
                      "p6r1": bool(r1[k]["pass"]), "p6r2": bool(r2[k]["pass"]),
                      "judge": {"p6r1": r1[k]["failed"], "p6r2": r2[k]["failed"]}, "tools": {"p6r1": r1[k].get("tools"), "p6r2": r2[k].get("tools")},
                      "history": others.get(k, {})})
        cases[-1]["call_id"] = {"p6r1": r1[k].get("call_id"), "p6r2": r2[k].get("call_id")}
    json.dump(cases, open(EVAL / "dev_cases.json", "w"), indent=1, ensure_ascii=False)
    print(len(cases), "cases (dead/flaky):", sum(c["kind"] == "dead" for c in cases), "dead,", sum(c["kind"] == "flaky" for c in cases), "flaky", flush=True)

    import freya  # noqa: PLC0415
    from builder.template_cc import freya_retry  # noqa: PLC0415
    freya_retry.install(freya, sleep=lambda s: time.sleep(5))
    todo = [(c, t) for c in cases for t in ("p6r1", "p6r2") if not c[t] and c["call_id"][t]]
    done = 0
    for c, t in todo:
        path = OUT / c["brief"] / f"{c['case']}.{t}.json"
        if path.exists():
            done += 1
            continue
        for attempt in range(1, 5):
            try:
                call = freya.get_call(c["call_id"][t])
                break
            except Exception as e:  # noqa: BLE001
                print(f"  {c['brief']} {c['case']} {t}: attempt {attempt} failed: {str(e)[:70]}", flush=True)
                time.sleep(10)
        else:
            continue
        msgs = []
        for m in call.get("messages") or []:
            text = m.get("message") or ""
            if m.get("role") == "system":
                try:
                    ev = json.loads(text)
                    ev.pop("variable_table", None)
                    for b in ev.get("branches") or []:
                        b.pop("condition_results", None)
                    text = json.dumps(ev, ensure_ascii=False)
                except Exception:  # noqa: BLE001
                    pass
            msgs.append({"role": m.get("role"), "text": text[:1500], "node": m.get("nodeId"), "t": round(m.get("secondsFromStart") or 0, 1)})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"brief": c["brief"], "case": c["case"], "tag": t, "ended": call.get("endedReason"), "messages": msgs}, ensure_ascii=False))
        done += 1
        print(f"  saved {c['brief']} {c['case']} {t} ({len(msgs)} messages) [{done}/{len(todo)}]", flush=True)


main()
