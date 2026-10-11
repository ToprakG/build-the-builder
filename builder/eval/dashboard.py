"""A live local dashboard of every run of the Claude Code builder: builds, Freya simulations, local simulations.

    python3 builder/eval/dashboard.py            # then open http://localhost:8765 (refreshes every 10 s)

It only reads files the runs already write (traces/, out/, results/) and the process list; it changes nothing.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
E = html.escape


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _age(ts: float) -> str:
    s = int(time.time() - ts)
    return f"{s}s" if s < 90 else f"{s // 60}m" if s < 5400 else f"{s // 3600}h{(s % 3600) // 60:02d}m"


def processes() -> list[tuple[str, str, str]]:
    out = subprocess.run(["ps", "-axo", "etime=,command="], capture_output=True, text=True).stdout.splitlines()
    rows = []
    for line in out:
        etime, _, cmd = line.strip().partition(" ")
        if "grep" in cmd or "shell-snapshots" in cmd or cmd.startswith(("/bin/zsh", "zsh ")):
            continue
        kind = ("build" if "builder/build.py" in cmd else "freya sims" if "tools/run_cases.py" in cmd else
                "local sims" if "localsim.sim" in cmd else "orchestrator" if re.search(r"out/(run|fakes|localsim_all|freya_all)", cmd) else None)
        if kind:
            label = re.sub(r"/Users/[^ ]+?/yc-hackathon-vapi/", "", cmd)
            label = re.sub(r"\.venv\d*/bin/python3? ", "", label)
            if not any(r[0] == kind and r[2] == label[:150] for r in rows):  # subshells repeat the same command
                rows.append((kind, etime, label[:150]))
    return rows


def builds() -> list[dict]:
    latest = {}
    for d in sorted((REPO / "traces").glob("agentic-*-20*"), key=lambda p: p.stat().st_mtime):
        slot = re.sub(r"-\d{8}-\d{6}$", "", d.name)
        latest[slot] = d
    rows = []
    for slot, d in sorted(latest.items(), key=lambda kv: -kv[1].stat().st_mtime):
        events, result_costs, start = [], [], None
        msgs = d / "messages.jsonl"
        if msgs.exists():
            for line in msgs.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if m.get("type") == "start":
                    start = m.get("time")
                if m.get("type") == "event" and m.get("kind") in ("phase", "review", "rollback", "timeout", "error") or \
                        (m.get("type") == "event" and str(m.get("kind", "")).startswith(("unit (", "local sims ("))):
                    events.append((m.get("kind"), str(m.get("detail"))[:160]))
                if m.get("type") == "ResultMessage":
                    result_costs.append(m.get("total_cost_usd") or 0)
        done = (d / "trace.txt").exists()
        report = _json(REPO / "out" / "agentic" / slot / "build_report.json") if done else None
        phase = next((det for k, det in reversed(events) if k == "phase"), "starting")
        rows.append({"slot": slot, "dir": d.name, "start": start, "done": done, "phase": phase, "events": events[-8:],
                     "cost": round(sum(c for c in result_costs if c), 2), "age": _age(d.stat().st_mtime),
                     "status": (report or {}).get("status") if done else "running",
                     "review": (report or {}).get("review") or {}})
    return rows


def freya_scores() -> dict:
    hist = defaultdict(dict)
    for f in (REPO / "results").glob("agentic_*_run*.json"):
        m = re.match(r"agentic_([AB])_run(\d+)\.json", f.name)
        d = _json(f)
        if m and d and "passed" in d:
            hist[m.group(1)][int(m.group(2))] = (d["passed"], d["total"], d.get("runs", 1))
    return hist


def freya_all() -> list[dict]:
    rows = []
    folder = REPO / "out" / "freya_all"
    for cases in sorted(folder.glob("*.cases.json")):
        brief = cases.name.replace(".cases.json", "")
        log = folder / f"{brief}.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        m = re.search(r"(\d+) / (\d+) passed", text)
        fails = re.findall(r"^FAIL (\S+) +\S+ +([^|]+)\|", text, re.M)
        rows.append({"brief": brief, "score": f"{m.group(1)}/{m.group(2)}" if m else "running", "fails": fails})
    return rows


def local_runs() -> tuple[list[dict], Counter, dict]:
    rows, checks, fam = [], Counter(), defaultdict(lambda: [0, 0])
    folder = REPO / "out" / "localsim_all"
    for log in sorted(folder.glob("*.log")):
        brief = log.stem
        res = _json(folder / f"{brief}.json")
        if res is None:
            rows.append({"brief": brief, "score": "running", "records": "", "judge": ""})
            continue
        n = len(res)
        rows.append({"brief": brief, "score": f"{sum(r['pass'] for r in res)}/{n}",
                     "records": f"{sum(bool(r.get('ledger_pass')) for r in res)}/{n}",
                     "judge": f"{sum(r.get('judge_pass') is True for r in res)}/{n}"})
        for r in res:
            f = fam[r.get("family") or "?"]
            f[0] += r["pass"]
            f[1] += 1
            for c in r.get("failed_checks") or []:
                checks[c[:100]] += 1
            for c in r.get("ledger_failed") or []:
                checks["record: " + c.split(",")[0][:90]] += 1
    return rows, checks, fam


CSS = """body{font:13px/1.45 -apple-system,Segoe UI,sans-serif;margin:18px;color:#1d1d1f;background:#fafafa}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:22px 0 6px;border-bottom:1px solid #ddd;padding-bottom:3px}
table{border-collapse:collapse;background:#fff;margin:4px 0}td,th{border:1px solid #e3e3e3;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f2f2f4}.ok{color:#137333;font-weight:600}.bad{color:#b3261e;font-weight:600}.run{color:#a15c00;font-weight:600}
.muted{color:#777}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}code{font-size:12px}"""


def score_class(s: str) -> str:
    m = re.match(r"(\d+)/(\d+)", s or "")
    if not m:
        return "run"
    a, b = int(m.group(1)), int(m.group(2))
    return "ok" if b and a / b >= 0.8 else "bad" if b and a / b < 0.5 else ""


def page() -> str:
    out = [f"<html><head><meta charset='utf-8'><meta http-equiv='refresh' content='10'><title>CC builder runs</title>"
           f"<style>{CSS}</style></head><body><h1>Claude Code builder: live runs</h1>"
           f"<div class='muted'>{time.strftime('%Y-%m-%d %H:%M:%S')} · refreshes every 10 s · reads traces/, out/, results/</div>"]
    procs = processes()
    out.append("<h2>Running now</h2>")
    if procs:
        out.append("<table><tr><th>kind</th><th>for</th><th>command</th></tr>" + "".join(
            f"<tr><td class='run'>{E(k)}</td><td>{E(t)}</td><td><code>{E(c)}</code></td></tr>" for k, t, c in procs) + "</table>")
    else:
        out.append("<div class='muted'>nothing running</div>")

    out.append("<h2>Builds (latest per slot)</h2><table><tr><th>slot</th><th>state</th><th>phase</th><th>checks before → after fix</th>"
               "<th>Claude $</th><th>recent events</th></tr>")
    for b in builds()[:14]:
        rv = b["review"]

        def pair(key):
            be, af = (rv.get(f"{key}_before") or {}).get("summary") or {}, (rv.get(f"{key}_after") or {}).get("summary") or {}
            if not be:
                return ""
            return f"{key}: {be.get('passed')}/{be.get('total')} → {af.get('passed', '–')}/{af.get('total', '–')}"
        checks = " · ".join(x for x in (pair("unit"), pair("local")) if x)
        if rv.get("rolled_back"):
            checks += " · <span class='bad'>rolled back</span>"
        state = "<span class='ok'>ok</span>" if b["status"] == "ok" else f"<span class='run'>{E(str(b['status']))}</span>" \
            if b["status"] == "running" else f"<span class='bad'>{E(str(b['status']))}</span>"
        ev = "<br>".join(f"<b>{E(k)}</b> {E(d)}" for k, d in b["events"][-4:])
        out.append(f"<tr><td><b>{E(b['slot'])}</b><div class='muted'>{E(b['dir'][-15:])} · {b['age']} ago</div></td><td>{state}</td>"
                   f"<td>{E(str(b['phase']))}</td><td>{checks}</td><td>{b['cost']}</td><td>{ev}</td></tr>")
    out.append("</table>")

    out.append("<div class='grid'><div><h2>Freya: practice cases, strict (all reps must pass)</h2><table><tr><th>run</th><th>Brief A</th><th>Brief B</th></tr>")
    hist = freya_scores()
    for run in sorted({r for v in hist.values() for r in v}, reverse=True):
        cells = []
        for brief in ("A", "B"):
            v = hist[brief].get(run)
            s = f"{v[0]}/{v[1]}" if v else ""
            cells.append(f"<td class='{score_class(s)}'>{s}{f' <span class=muted>×{v[2]}</span>' if v and v[2] > 1 else ''}</td>")
        out.append(f"<tr><td>run {run}</td>{''.join(cells)}</tr>")
    out.append("</table></div><div><h2>Freya: all briefs (testbed sample, 1 rep)</h2><table><tr><th>brief</th><th>score</th><th>failed cases</th></tr>")
    for r in freya_all():
        fails = ", ".join(f"{i} ({f.strip()})" for i, f in r["fails"][:8])
        out.append(f"<tr><td>{E(r['brief'])}</td><td class='{score_class(r['score'])}'>{r['score']}</td><td>{E(fails)}</td></tr>")
    out.append("</table></div></div>")

    rows, checks, fam = local_runs()
    out.append("<h2>Local simulations (DeepSeek agent, Jev judge, no Freya)</h2><div class='grid'><div><table>"
               "<tr><th>brief</th><th>passed</th><th>records ok</th><th>judge ok</th></tr>")
    for r in rows:
        out.append(f"<tr><td>{E(r['brief'])}</td><td class='{score_class(r['score'])}'>{r['score']}</td><td>{r['records']}</td><td>{r['judge']}</td></tr>")
    out.append("</table><h2>By family</h2><table><tr><th>family</th><th>passed</th></tr>")
    for k, (p, n) in sorted(fam.items(), key=lambda kv: kv[1][0] / max(kv[1][1], 1)):
        out.append(f"<tr><td>{E(k)}</td><td class='{score_class(f'{p}/{n}')}'>{p}/{n}</td></tr>")
    out.append("</table></div><div><h2>Most failed checks</h2><table><tr><th>#</th><th>check</th></tr>")
    for c, k in checks.most_common(18):
        out.append(f"<tr><td>{k}</td><td>{E(c)}</td></tr>")
    out.append("</table></div></div></body></html>")
    return "".join(out)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body = page().encode()
            self.send_response(200)
        except Exception as e:  # noqa: BLE001  (show the error instead of dying)
            body = f"<pre>{E(repr(e))}</pre>".encode()
            self.send_response(500)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"dashboard on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
