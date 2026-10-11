"""Start ONE vendor mock bank for several testbed briefs (each under /<brief>) behind ONE cloudflared tunnel.

Cloudflare's quick tunnels are rate limited: one tunnel per brief fails when many briefs start at once.
Prints the public origin; the briefs are at <origin>/<brief>. Runs until killed.

    .venv310/bin/python scripts/tcc_shared_bank.py --port 8860 --limit 10 f1-nordhavn f2-ostral ...
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
from builder.template_cc.vendor_runner import VENDOR, _wait_http, bank_config, find_bank_python  # noqa: E402
from tcc_eval_testbed import pick, sources  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("briefs", nargs="+")
    ap.add_argument("--port", type=int, default=8860)
    ap.add_argument("--origin-file", default="", help="where to write the tunnel origin (default out/template_cc/eval/shared_origin.txt, the main bank's)")
    ap.add_argument("--limit", type=int, default=0, help="0 = every case of each brief")
    a = ap.parse_args()
    d = Path(tempfile.mkdtemp(prefix="tcc-sharedbank-"))
    multi = {}
    for b in a.briefs:
        cases, config, _, _ = sources(b)
        (d / f"{b}.cases.json").write_text(json.dumps(cases))  # every case: the eval picks its own subset
        (d / f"{b}.config.json").write_text(json.dumps(bank_config(config)))
        multi[b] = {"cases": str(d / f"{b}.cases.json"), "config": str(d / f"{b}.config.json")}
    (d / "multi.json").write_text(json.dumps(multi))
    bank = subprocess.Popen([find_bank_python(), str(REPO / "builder" / "template_cc" / "vendor_bankd.py"), "--vendor",
                             str(VENDOR), "--multi", str(d / "multi.json"), "--port", str(a.port)],
                            stdout=(d / "bank.log").open("w"), stderr=subprocess.STDOUT, start_new_session=True)
    _wait_http(f"http://127.0.0.1:{a.port}/{a.briefs[0]}/openapi.json", 90)
    log = d / "cloudflared.log"
    tun = subprocess.Popen(["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{a.port}"], stdout=log.open("w"),
                           stderr=subprocess.STDOUT, start_new_session=True)
    origin = None
    for _ in range(240):
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", log.read_text() if log.exists() else "")
        if m:
            origin = m.group(0)
            break
        time.sleep(0.5)
    if not origin:
        print("ERROR no tunnel URL", flush=True)
        sys.exit(1)
    _wait_http(f"{origin}/{a.briefs[0]}/openapi.json", 120)
    print(f"ORIGIN {origin}", flush=True)
    (Path(a.origin_file) if a.origin_file else REPO / "out" / "template_cc" / "eval" / "shared_origin.txt").write_text(origin)
    try:
        while bank.poll() is None and tun.poll() is None:
            time.sleep(5)
    finally:
        for p in (tun, bank):
            if p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    main()
