"""VendorRunner: the self-test TestRunner built on build-the-builder's own tester (vendor/build-the-builder).

    runner = VendorRunner(slot, policy, api, trace, freya_mod)
    results = runner.run(workflow, cases, tool_api, policy, reps)     # -> list[CaseResult]
    runner.close()

1. The vendor mock bank serves T-CC's OWN generated cases, with our policy.json as its config (vendor_bankd.py), and
   is published with a cloudflared quick tunnel (the vendor's run_fake.py does the same).
2. The candidate workflow's tool URLs are pointed at the tunnel and it is saved to its own Freya slot
   `<slot>-selftest`, so the real agent is never touched.
3. Each case is played over the chat API with the vendor's LLM customer, the ledger is scored with the vendor's
   score_ledger and the conversation is judged by the vendor decision model (vendor_worker.py).
No testbed or kit case is used: only the cases testgen made from our policy. The bank and the chat driver run in
their own processes (the vendor modules use generic names such as freya, score and judge).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from builder.template_cc.io import REPO
from builder.template_cc.runner import CaseResult

VENDOR = REPO / "vendor" / "build-the-builder"
HERE = Path(__file__).resolve().parent
API_HANDLER = "__function__:api_call"


class RunnerUnavailable(RuntimeError):
    pass


def _has_fastapi(python: str) -> bool:
    try:
        return subprocess.run([python, "-c", "import fastapi, uvicorn"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_bank_python() -> str | None:
    """A Python that has FastAPI + uvicorn for the mock bank."""
    for cand in (os.environ.get("TCC_BANK_PYTHON"), str(REPO / ".venv" / "bin" / "python"),
                 str(VENDOR / ".venv" / "bin" / "python"), sys.executable, shutil.which("python3")):
        if cand and Path(cand).exists() and _has_fastapi(cand):
            return cand
    return None


def availability(vendor: Path = VENDOR) -> tuple[bool, str]:
    if not (vendor / "tools" / "driver" / "chat_driver.py").exists():
        return False, f"vendor tester not found at {vendor}"
    if not shutil.which("cloudflared"):
        return False, "cloudflared is not installed (the mock bank needs a public URL)"
    if not os.environ.get("FREYA_BANK_TEAM_KEY"):
        return False, "FREYA_BANK_TEAM_KEY is empty"
    if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        return False, "the vendor customer model needs OPENROUTER_API_KEY or GEMINI_API_KEY"
    if find_bank_python() is None:
        return False, "no Python with fastapi + uvicorn for the mock bank (set TCC_BANK_PYTHON)"
    return True, "ok"


def bank_config(policy: dict) -> dict:
    """Our policy is the mock bank's config schema; only a product id format without {n} needs a default."""
    cfg = copy.deepcopy(policy)
    if "{n}" not in str(cfg.get("product_id_format") or ""):
        cfg["product_id_format"] = "p{n}"
    return cfg


def _tools(wf: dict):
    for group in ("nodes", "global_nodes"):
        for node in (wf.get(group) or {}).values():
            for t in (node.get("tools") or []) + (node.get("pre_actions") or []):
                yield t
    for t in wf.get("tools") or []:
        yield t


def point_at(workflow: dict, origin: str) -> dict:
    """A copy whose bank tool URLs go to the mock bank (`<origin>/v1/<tool>`), without a baked customer_id."""
    wf = copy.deepcopy(workflow)
    for t in _tools(wf):
        fn = t.get("function") or {}
        if fn.get("handler") != API_HANDLER:
            continue
        url = ((fn.get("parameters") or {}).get("properties") or {}).get("url")
        if isinstance(url, dict) and "default" in url:
            url["default"] = origin.rstrip("/") + "/v1/" + fn["name"]
    tv = dict(wf.get("test_variables") or {})
    tv.pop("customer_id", None)
    wf["test_variables"] = tv
    return wf


def _wait_http(url: str, seconds: float):
    end, last = time.time() + seconds, ""
    while time.time() < end:
        try:
            req = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0 (tcc-selftest)"})
            with urllib.request.urlopen(req, timeout=8) as r:
                if r.status == 200:
                    return
        except Exception as e:  # noqa: BLE001
            last = str(e)[:200]
        time.sleep(1)
    raise RunnerUnavailable(f"no answer from {url}: {last}")


class VendorRunner:
    def __init__(self, slot: str, policy: dict, api, trace, freya_mod, vendor: Path = VENDOR, port: int = 8787,
                 concurrency: int = 2, run_timeout_s: float = 1500, worker_python: str | None = None,
                 infra_retries: int = 1, slot_suffix: str = "-selftest"):
        self.slot, self.policy, self.api, self.trace, self.freya = f"{slot}{slot_suffix}", policy, api, trace, freya_mod
        self.vendor, self.port, self.concurrency, self.run_timeout_s = Path(vendor), port, concurrency, run_timeout_s
        self.worker_python = worker_python or sys.executable
        self.infra_retries = infra_retries
        self.dir = Path(tempfile.mkdtemp(prefix="tcc-selftest-"))
        self.bank = self.tunnel = None
        self.origin = None
        self.loaded: dict[str, dict] = {}
        self.published_hash = None
        self.workflow_id = None

    # --- bank + tunnel --------------------------------------------------------------------------------------------
    def _start_bank(self):
        if self.bank and self.bank.poll() is None:
            os.killpg(self.bank.pid, signal.SIGTERM)
            self.bank.wait(timeout=10)
        (self.dir / "cases.json").write_text(json.dumps(list(self.loaded.values())), encoding="utf-8")
        (self.dir / "config.json").write_text(json.dumps(bank_config(self.policy)), encoding="utf-8")
        py = find_bank_python()
        if py is None:
            raise RunnerUnavailable("no Python with fastapi + uvicorn for the mock bank")
        log = (self.dir / "bank.log").open("w")
        self.bank = subprocess.Popen([py, str(HERE / "vendor_bankd.py"), "--vendor", str(self.vendor), "--cases",
                                      str(self.dir / "cases.json"), "--config", str(self.dir / "config.json"),
                                      "--port", str(self.port)], stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        _wait_http(f"http://127.0.0.1:{self.port}/openapi.json", 60)  # FastAPI's own schema route
        self.trace.selftest(f"mock bank up on 127.0.0.1:{self.port} with {len(self.loaded)} of our own case(s)")

    def _start_tunnel(self):
        log_path = self.dir / "cloudflared.log"
        self.tunnel = subprocess.Popen(["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{self.port}"],
                                       stdout=log_path.open("w"), stderr=subprocess.STDOUT, start_new_session=True)
        end = time.time() + 60
        while time.time() < end and self.tunnel.poll() is None:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", log_path.read_text() if log_path.exists() else "")
            if m:
                self.origin = m.group(0)
                break
            time.sleep(0.5)
        if not self.origin:
            raise RunnerUnavailable("cloudflared did not print a public URL")
        _wait_http(self.origin + "/openapi.json", 90)
        self.trace.selftest(f"mock bank public at {self.origin}")

    def use_shared(self, origin: str):
        """Use a bank someone else started (a shared multi-brief bank): no bank or tunnel of our own."""
        self.origin, self.shared = origin.rstrip("/"), True

    def _ensure(self, cases: list[dict]):
        if getattr(self, "shared", False):
            return
        new = [c for c in cases if c["id"] not in self.loaded]
        if new or self.bank is None or self.bank.poll() is not None:
            self.loaded.update({c["id"]: c for c in cases})
            self._start_bank()
        if self.tunnel is None or self.tunnel.poll() is not None:
            self.origin = None
            self._start_tunnel()

    # --- workflow -------------------------------------------------------------------------------------------------
    def _publish(self, workflow: dict) -> str:
        wf = point_at(workflow, self.origin)
        h = hashlib.sha256(json.dumps(wf, sort_keys=True).encode()).hexdigest()
        if h != self.published_hash:
            saved = self.freya.save_workflow(self.slot, wf, state_file=str(REPO / ".freya-agents.json"))
            self.workflow_id = saved["workflow_id"]
            self.published_hash = h
            self.trace.freya("save_workflow (self-test slot)", {"slot": self.slot, **saved})
        return self.workflow_id

    # --- TestRunner -------------------------------------------------------------------------------------------------
    customer_model: str | None = None  # set to e.g. "deepseek/deepseek-v4.1-flash" to play the customer with that model
    label: list | None = None  # [brief, tag] for the live scoreboard
    max_concurrency: int | None = None  # adaptive ceiling; None = 3 x the start (the worker halves on 5xx)

    def _work(self, wid: str, cases: list[dict], reps: int, concurrency: int) -> list[dict]:
        job = self.dir / f"job-{int(time.time() * 1000)}.json"
        out = job.with_suffix(".out.json")
        job.write_text(json.dumps({"workflow_id": wid, "origin": self.origin, "cases": cases, "reps": reps,
                                   "concurrency": concurrency, "max_concurrency": self.max_concurrency, "label": self.label,
                                   "customer_model": self.customer_model,
                                   "language": str(self.policy.get("language") or "en")}), encoding="utf-8")
        proc = subprocess.run([self.worker_python, str(HERE / "vendor_worker.py"), "--vendor", str(self.vendor),
                               "--job", str(job), "--out", str(out)], capture_output=True, text=True,
                              timeout=self.run_timeout_s, env=os.environ.copy())
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"vendor worker failed: {proc.stderr[-800:]}")
        return json.loads(out.read_text(encoding="utf-8"))

    def run(self, workflow: dict, cases: list[dict], tool_api: dict, policy: dict, reps: int) -> list[CaseResult]:
        self._ensure(cases)
        wid = self._publish(workflow)
        t0 = time.time()
        rows = self._work(wid, cases, reps, self.concurrency)
        by_id = {c["id"]: c for c in cases}
        for attempt in range(self.infra_retries):  # the chat API can answer 502/504: replay those runs one by one
            bad = [i for i, r in enumerate(rows) if r.get("infra")]
            if not bad:
                break
            self.trace.selftest(f"{len(bad)} run(s) hit an infrastructure error; retrying them one at a time")
            for i in bad:
                again = self._work(wid, [by_id[rows[i]["case_id"]]], 1, 1)[0]
                again["rep"] = rows[i]["rep"]
                rows[i] = again
        results = [CaseResult(r["case_id"], r["rep"], bool(r["passed"]), r.get("failed_reasons") or [],
                              r.get("transcript") or [], r.get("tool_calls") or [], bool(r.get("infra")))
                   for r in rows]
        scored = [r for r in results if not r.infra]
        self.trace.selftest(f"{len(cases)} case(s) x {reps} rep(s) via the vendor chat driver: "
                            f"{sum(r.passed for r in scored)}/{len(scored)} passed, {len(results) - len(scored)} "
                            f"unscored (infrastructure), in {time.time() - t0:.0f}s "
                            "(customer: vendor LLM persona; judge: vendor decision model)")
        return results

    def close(self):
        for p in (self.tunnel, self.bank):
            if p is not None and p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                    p.wait(timeout=10)
                except Exception:  # noqa: BLE001
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except Exception:  # noqa: BLE001
                        pass
        shutil.rmtree(self.dir, ignore_errors=True)
