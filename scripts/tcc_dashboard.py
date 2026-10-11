"""Live dashboard of every T-CC run on this machine: http://127.0.0.1:8900 (refreshes every 10 s).

Reads: finished eval results (out/template_cc/eval/*.json, out/template_cc/eval/freya/*.json), live per-case results
of running workers (temp tcc-selftest-*/job-*.out.json.partial.jsonl), the newest trace line per run, the running
processes, the run logs, and the OpenRouter spend. Standard library only.

    .venv310/bin/python scripts/tcc_dashboard.py
"""
import glob
import html
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVAL = REPO / "out" / "template_cc" / "eval"
BRIEFS = ["brief-a", "brief-b", "f1-nordhavn", "f2-ostral", "f3-kestrel", "f4-brightwell", "f5-halden-email",
          "f6-corvid-memo", "f7-lumen-codes"]
_cache = {"t": 0, "usage": None, "credits": None}


def env_key(name):
    for line in (REPO / ".env").read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(name, "")


def openrouter():
    if time.time() - _cache["t"] > 30:
        key = env_key("OPENROUTER_API_KEY")
        try:
            for url, k in (("https://openrouter.ai/api/v1/key", "usage"), ("https://openrouter.ai/api/v1/credits", "credits")):
                req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
                with urllib.request.urlopen(req, timeout=10) as r:
                    d = json.load(r)["data"]
                _cache[k] = d.get("usage") if k == "usage" else (d.get("total_credits"), d.get("total_usage"))
            _cache["t"] = time.time()
        except Exception:
            pass
    return _cache["usage"], _cache["credits"]


def live_rows():
    """(brief, tag) -> rows from running workers."""
    out = {}
    for part in glob.glob(os.path.join(tempfile.gettempdir(), "tcc-*", "job-*.out.json.partial.jsonl")):
        try:
            job = json.loads(Path(part[: -len(".out.json.partial.jsonl")] + ".json").read_text())
            label = tuple(job.get("label") or ("?", "?"))
            out.setdefault(label, []).extend(json.loads(l) for l in open(part) if l.strip())
        except Exception:
            continue
    return out


def results():
    rows = []
    live = live_rows()
    for b in BRIEFS:
        for route, pattern in (("chat + local judge", f"{b}.*.json"), ("Freya sim + judges", f"freya/{b}.*.json")):
            for f in sorted(EVAL.glob(pattern)):
                if f.name.endswith(".partial.jsonl"):
                    continue
                try:
                    r = json.loads(f.read_text())
                except Exception:
                    continue
                cases = r.get("rows") or []
                if route.startswith("chat"):
                    sc = [c for c in cases if not c.get("infra")]
                    rows.append(dict(brief=b, route=route, tag=f.name.split(".")[1], state="done",
                                     passed=sum(c["passed"] for c in sc), scored=len(sc), unscored=len(cases) - len(sc),
                                     local=None, cases=[dict(id=c["case_id"], family=c.get("family"),
                                                             res="UNSCORED" if c.get("infra") else ("PASS" if c["passed"] else "FAIL"),
                                                             why=c.get("failed_reasons") or []) for c in cases]))
                else:
                    rows.append(dict(brief=b, route=route, tag=r.get("tag"), state="done", passed=r["passed"],
                                     scored=r["cases"], unscored=0, local=f"{r['local_passed']}/{r['local_judged']}",
                                     cases=[dict(id=c["case"], family=c["family"], res="PASS" if c["pass"] else "FAIL",
                                                 why=c["failed"]) for c in r["rows"]]))
        for (lb, tag), rs in live.items():
            if lb == b and not (EVAL / f"{b}.{tag}.json").exists():
                sc = [c for c in rs if not c.get("infra")]
                rows.append(dict(brief=b, route="chat + local judge", tag=tag, state="running",
                                 passed=sum(c["passed"] for c in sc), scored=len(sc), unscored=len(rs) - len(sc),
                                 local=None, cases=[dict(id=c["case_id"], family=c.get("family"),
                                                         res="UNSCORED" if c.get("infra") else ("PASS" if c["passed"] else "FAIL"),
                                                         why=c.get("failed_reasons") or []) for c in rs]))
    return rows


def trace_tail(brief):
    ds = sorted(glob.glob(str(REPO / "traces" / f"tcc-eval-*{brief}-2026*")), key=os.path.getmtime)
    if not ds:
        return ""
    try:
        return Path(ds[-1], "trace.txt").read_text().strip().splitlines()[-1][:170]
    except Exception:
        return ""


def processes():
    try:
        ps = subprocess.run(["ps", "-eo", "pid,etime,command"], capture_output=True, text=True).stdout.splitlines()
    except Exception:
        return []
    keep = ("tcc_eval", "template_cc.run", "template_cc.sims", "vendor_worker", "vendor_bankd", "run_cases.py",
            "cloudflared tunnel", "localhost.run", "tcc_shared_bank")
    out = []
    for line in ps[1:]:
        if any(k in line for k in keep) and "grep" not in line:
            parts = line.split(None, 2)
            cmd = parts[2]
            mine = "template_cc" in cmd or "tcc_" in cmd or "localhost.run" in cmd
            out.append((parts[0], parts[1], cmd.replace(str(REPO) + "/", "")[:150], mine))
    return out


def logs():
    out = []
    for name in ("freya-run-f2f7.log", "freya-run.log", "chat-fakes.log", "chat-shared.log"):
        p = EVAL / name
        if p.exists():
            lines = [l for l in p.read_text().splitlines() if "another simulation" not in l]
            out.append((name, lines[-6:]))
    return out


CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e4e3de;--ok:#1f7a4d;--bad:#b3261e;--warn:#8a6d00;--run:#2453a6}
@media (prefers-color-scheme: dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ececE8;--mute:#9a9a93;--line:#33332f;--ok:#5cc28c;--bad:#ff7a70;--warn:#e0c050;--run:#7aa7ff}}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,system-ui,Segoe UI,sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:20px;margin:0 0 4px} h2{font-size:15px;margin:22px 0 8px}
.sub{color:var(--mute);font-size:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.kpi b{display:block;font-size:22px} .kpi span{color:var(--mute);font-size:12px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top;font-size:13px}
th{color:var(--mute);font-weight:600;font-size:12px}
.PASS{color:var(--ok);font-weight:600}.FAIL{color:var(--bad);font-weight:600}.UNSCORED{color:var(--warn)}
.running{color:var(--run);font-weight:600}.done{color:var(--ok)}
details summary{cursor:pointer;color:var(--run)} .why{color:var(--mute);font-size:12px;margin-left:12px}
pre{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;overflow-x:auto;font-size:12px;white-space:pre-wrap}
.mine{color:var(--ink)} .other{color:var(--mute)}
"""


def page():
    usage, credits = openrouter()
    rs = results()
    done = [r for r in rs if r["state"] == "done"]
    tot_p, tot_s = sum(r["passed"] for r in rs), sum(r["scored"] for r in rs)
    left = f"${credits[0] - credits[1]:.2f}" if credits and None not in credits else "?"
    h = [f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<meta http-equiv=refresh content=10><title>T-CC runs</title><style>{CSS}</style></head><body><main>",
         f"<h1>T-CC runs</h1><div class=sub>Live, refreshes every 10 s · {time.strftime('%H:%M:%S')}</div>",
         "<div class=kpis>",
         f"<div class=kpi><b>{tot_p}/{tot_s}</b><span>passed / scored (all rows)</span></div>",
         f"<div class=kpi><b>{len([r for r in rs if r['state'] == 'running'])}</b><span>runs in progress</span></div>",
         f"<div class=kpi><b>{len(done)}</b><span>runs finished</span></div>",
         f"<div class=kpi><b>{left}</b><span>OpenRouter credit left (usage {usage if usage is None else round(usage, 2)})</span></div>",
         "</div><h2>Per brief</h2><table><tr><th>brief</th><th>route</th><th>tag</th><th>state</th><th>passed</th>"
         "<th>scored</th><th>unscored</th><th>local judge</th><th>latest trace line</th></tr>"]
    for r in rs:
        h.append(f"<tr><td>{r['brief']}</td><td>{r['route']}</td><td>{r['tag']}</td><td class={r['state']}>{r['state']}</td>"
                 f"<td>{r['passed']}</td><td>{r['scored']}</td><td>{r['unscored']}</td><td>{r['local'] or ''}</td>"
                 f"<td class=sub>{html.escape(trace_tail(r['brief']) if r['state'] == 'running' else '')}</td></tr>")
    h.append("</table><h2>Cases</h2>")
    for r in rs:
        fails = [c for c in r["cases"] if c["res"] != "PASS"]
        h.append(f"<details><summary>{r['brief']} · {r['route']} · {r['tag']} — {r['passed']}/{r['scored']} "
                 f"({len(fails)} not passed)</summary><table><tr><th>case</th><th>family</th><th>result</th><th>why</th></tr>")
        for c in r["cases"]:
            why = "".join(f"<div class=why>{html.escape(str(x)[:220])}</div>" for x in c["why"][:4])
            h.append(f"<tr><td>{c['id']}</td><td>{c['family']}</td><td class={c['res']}>{c['res']}</td><td>{why}</td></tr>")
        h.append("</table></details>")
    h.append("<h2>Processes</h2><table><tr><th>pid</th><th>running for</th><th>command</th></tr>")
    for pid, et, cmd, mine in processes():
        h.append(f"<tr class={'mine' if mine else 'other'}><td>{pid}</td><td>{et}</td><td>{html.escape(cmd)}"
                 f"{'' if mine else ' <span class=sub>(other session)</span>'}</td></tr>")
    h.append("</table><h2>Run logs</h2>")
    for name, lines in logs():
        h.append(f"<div class=sub>{name}</div><pre>{html.escape(chr(10).join(lines))}</pre>")
    h.append("</main></body></html>")
    return "".join(h)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = page().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8900), H).serve_forever()
