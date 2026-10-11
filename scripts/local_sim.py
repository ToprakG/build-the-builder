"""Local Freya-like simulator (diagnosis only; ship decisions come from Freya runs). See docs/LOCAL_SIM.md.

    python3 scripts/local_sim.py --set p6 --briefs brief-a,f3-kestrel [--cases ID,ID] [--rep 1] [--parallel 3] [--max 60] [--pilot]

Roles: customer and judge = google/gemini-3-flash-preview on OpenRouter (Freya's own SIM_LLM; customer temperature 0.7, judge temperature 0);
agent (extraction, routing, dialogue) = Sonnet through the claude_cli provider of builder/template_cc/llm.py (NOT OpenRouter). The judge
reads the persona's successCriteria and the tool-call ledger. The platform engine is builder/agentic/localsim/engine.py (imported, not edited). DEV cases only: the first 10 of the
family-spread order of a brief (what `tcc_eval_freya.py --limit 10` picks); the script refuses anything else and never reads sealed files.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
VENDOR = REPO / "vendor" / "build-the-builder"
for p in (VENDOR / "tools" / "driver", VENDOR / "tools", VENDOR):
    sys.path.insert(0, str(p))

from builder.agentic.localsim.engine import Engine  # noqa: E402  (imported, never edited)
from builder.template_cc import llm  # noqa: E402

OUT = REPO / "out" / "template_cc" / "localsim"
CUSTOMER_MODEL = "google/gemini-3-flash-preview"   # Freya's SIM_LLM (plays the customer and judges)
CUSTOMER_TEMP = 0.7
JUDGE_MODEL = "google/gemini-3-flash-preview"
JUDGE_TEMP = 0.0
MAX_CUSTOMER_TURNS = 30           # GUESS: Freya's text simulations end by maxCallDurationSeconds (420); a turn cap is the local stand-in
CALL_TIMEOUT_S = 150
TEAM = "localsim-team"
BUDGET = OUT / "_budget"          # shared by every session that runs this script: one counter, N concurrency slots, one STOP file
TOTAL_CAP = int(os.environ.get("LOCALSIM_CAP", "300"))     # conversations in total, all sessions together
SLOTS = int(os.environ.get("LOCALSIM_SLOTS", "4"))         # conversations running at the same time, all sessions together


class _Stop:
    """Set on the first claude usage/rate-limit warning of ANY run: the STOP file stops every local sim run, nothing new starts."""

    def __init__(self):
        self._ev = threading.Event()

    def is_set(self):
        return self._ev.is_set() or (BUDGET / "STOP").exists()

    def set(self, reason="claude usage / rate-limit warning"):
        self._ev.set()
        BUDGET.mkdir(parents=True, exist_ok=True)
        (BUDGET / "STOP").write_text(f"{time.strftime('%F %T')} {reason}\n")


STOP = _Stop()

# Quota protection (the claude_cli quota is the only path to the 20:00 final build):
#   normal conversations are refused from LOCALSIM_CUTOFF (18:30); the --sealed check may still run until LOCALSIM_HARD_STOP (19:15);
#   no claude call of this script happens after 19:15 at all.
CUTOFF = int(os.environ.get("LOCALSIM_CUTOFF", "1830"))
HARD_STOP = int(os.environ.get("LOCALSIM_HARD_STOP", "1915"))


def hhmm() -> int:
    return int(time.strftime("%H%M"))


class Cutoff(Exception):
    pass


def budget_claim(sealed: bool = False) -> int | None:
    """Count one more conversation against the shared cap; returns the new total, or None when the cap or the time cutoff is reached."""
    if hhmm() >= (HARD_STOP if sealed else CUTOFF):
        return None
    BUDGET.mkdir(parents=True, exist_ok=True)
    with open(BUDGET / "count.json", "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        txt = f.read().strip()
        n = int(json.loads(txt)["used"]) if txt else 0
        if n >= TOTAL_CAP:
            return None
        f.seek(0)
        f.truncate()
        f.write(json.dumps({"used": n + 1, "cap": TOTAL_CAP}))
        return n + 1


class Slot:
    """One of SLOTS cross-process concurrency slots (an flock on slot<i>.lock); waits, polling, while all are taken."""

    def __enter__(self):
        BUDGET.mkdir(parents=True, exist_ok=True)
        while not STOP.is_set():
            for i in range(SLOTS):
                fd = open(BUDGET / f"slot{i}.lock", "a+")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.fd = fd
                    return self
                except OSError:
                    fd.close()
            time.sleep(2)
        raise LimitHit("stopped after a limit warning")

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        self.fd.close()
_LIMIT = re.compile(r"usage limit|hit your limit|limit reached|rate.?limit|overloaded|too many requests|429", re.I)
_COUNT = {"claude_calls": 0, "or_prompt": 0, "or_completion": 0}
_LOCK = threading.Lock()
_VENDOR = {}


class LimitHit(Exception):
    pass


def vendor():
    if not _VENDOR:
        for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            os.environ.pop(k, None)
        import chat_driver  # noqa: PLC0415
        import mockbank  # noqa: PLC0415
        import run_cases  # noqa: PLC0415
        from score import score_ledger  # noqa: PLC0415

        _VENDOR.update(chat_driver=chat_driver, mockbank=mockbank, persona_for=run_cases.persona_for, score=score_ledger)
    return _VENDOR


# --- the cases: DEV only ---------------------------------------------------------------------------------------------------------
def sources(brief):
    tb = REPO / "testbed"
    if brief in ("brief-a", "brief-b"):
        return json.loads((tb / "personas" / f"{brief}.cases.json").read_text()), json.loads((tb / "configs" / f"{brief}.config.json").read_text())
    d = tb / "fake_briefs" / brief
    return json.loads((d / "cases.json").read_text()), json.loads((d / "config.json").read_text())


def dev_cases(cases, limit=10, skip=0):
    """The same family-spread order as tcc_eval_freya.pick, first `limit` (skip 0) = the DEV cases. GATE cases (skip 10) are only selected by --sealed."""
    by = {}
    for c in sorted(cases, key=lambda c: c["id"]):
        by.setdefault(c["family"], []).append(c)
    order, i = [], 0
    while any(i < len(v) for v in by.values()):
        for fam in sorted(by):
            if i < len(by[fam]):
                order.append(by[fam][i])
        i += 1
    return order[skip:skip + limit]


# --- model calls ------------------------------------------------------------------------------------------------------------------
def claude_json(system: str, user: str, schema: dict | None, purpose: str) -> dict:
    """One `claude -p` call (Sonnet). No retry on a limit warning: it stops the whole run. One retry on a timeout."""
    if STOP.is_set():
        raise LimitHit("stopped after a limit warning")
    if hhmm() >= HARD_STOP:
        raise Cutoff(f"no claude call of local_sim after {HARD_STOP}")
    if schema is not None:
        system = (f"{system}\n\nReply with ONE JSON object only, no prose. It must match this JSON Schema (return an instance with real "
                  f"values, not the schema):\n{json.dumps(schema)}")
    for attempt in range(2):
        try:
            with _LOCK:
                _COUNT["claude_calls"] += 1
            text, _usage = llm._claude_cli(system, user, "sonnet", 4096, CALL_TIMEOUT_S)
            return llm.parse_json(text) if schema is not None else {"text": text}
        except llm.LLMError as e:
            if _LIMIT.search(str(e)) or getattr(e, "status", None) in (401, 429):
                STOP.set()
                raise LimitHit(str(e)[:200]) from None
            if attempt == 1:
                raise
        except ValueError:
            if attempt == 1:
                return {}
    return {}


class ClaudeModel:
    """The engine's model interface (extract / intents / chat) on Sonnet via claude_cli. In Freya all three roles use the agent model."""

    def __init__(self):
        self.calls = 0

    def extract(self, recent, schema):
        props = {f["name"]: ({"type": "string", "enum": [str(e) for e in f["enum_values"]] + [""]} if f.get("enum_values") else {"type": "string"})
                 for f in schema}
        fields = [{k: f.get(k) for k in ("name", "type", "description", "enum_values") if f.get(k) is not None} for f in schema]
        convo = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in recent)
        system = ("You extract variables from a phone conversation. Return one JSON object with one key per field. Use \"\" when the "
                  "conversation does not clearly give a value. For a field with enum_values, answer exactly one of them.")
        self.calls += 1
        return claude_json(system, f"FIELDS:\n{json.dumps(fields, ensure_ascii=False)}\n\nCONVERSATION:\n{convo}",
                           {"type": "object", "properties": props}, "extract")

    def intents(self, recent, last_user, descriptions):
        convo = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in recent[-6:])
        listing = "\n".join(f"{i + 1}. {d}" for i, d in enumerate(descriptions))
        system = ("You route a phone conversation. Decide which of the numbered descriptions the user's LAST message matches. "
                  'Return JSON {"matches": [numbers]}; an empty list when none matches.')
        self.calls += 1
        out = claude_json(system, f"CONVERSATION:\n{convo}\n\nLAST USER MESSAGE: {last_user}\n\nDESCRIPTIONS:\n{listing}",
                          {"type": "object", "properties": {"matches": {"type": "array", "items": {"type": "integer"}}}}, "route")
        nums = {int(n) for n in out.get("matches") or [] if str(n).lstrip("-").isdigit()}
        return {d: (i + 1) in nums for i, d in enumerate(descriptions)}

    def chat(self, system, history, tools):
        specs = [{"name": (t.get("function") or {}).get("name"), "description": (t.get("function") or {}).get("description"),
                  "parameters": (t.get("function") or {}).get("parameters")} for t in tools or []]
        lines = []
        for m in history:
            if m["role"] == "tool":
                lines.append(f"[tool result] {str(m.get('content'))[:1500]}")
            elif m.get("tool_calls"):
                calls = [f"{(c.get('function') or {}).get('name')}({(c.get('function') or {}).get('arguments')})" for c in m["tool_calls"]]
                lines.append(f"AGENT: {m.get('content') or ''} [called: {', '.join(calls)}]".strip())
            else:
                lines.append(f"{'AGENT' if m['role'] == 'assistant' else 'CUSTOMER'}: {m.get('content') or ''}")
        sys_prompt = (system + "\n\nYou are the voice agent on this phone call. Reply with JSON {\"say\": \"the words you say aloud next (may be empty "
                      "when you only call a tool)\", \"tool_calls\": [{\"name\": ..., \"arguments\": {...}}]}. Call a tool only when your task says so; "
                      "use the exact argument names. Tools available: " + json.dumps(specs, ensure_ascii=False))
        self.calls += 1
        out = claude_json(sys_prompt, "CONVERSATION SO FAR:\n" + "\n".join(lines[-40:]) + "\n\nWhat do you do next?",
                          {"type": "object", "properties": {"say": {"type": "string"}, "tool_calls": {"type": "array", "items": {
                              "type": "object", "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}}}}}}, "dialogue")
        calls = [{"id": f"call_{i}", "type": "function", "function": {"name": c.get("name"), "arguments": json.dumps(c.get("arguments") or {})}}
                 for i, c in enumerate(out.get("tool_calls") or []) if c.get("name")]
        return {"content": (out.get("say") or "").strip(), "tool_calls": calls}


def customer_reply(persona: str, agent_lines: list[str], customer_lines: list[str]) -> str:
    key = os.environ.get("OPENROUTER_API_KEY") or ""
    messages = [{"role": "system", "content": persona}]
    for i, line in enumerate(agent_lines):
        messages.append({"role": "user", "content": line})
        if i < len(customer_lines):
            messages.append({"role": "assistant", "content": customer_lines[i]})
    body = json.dumps({"model": CUSTOMER_MODEL, "temperature": CUSTOMER_TEMP, "messages": messages}).encode()
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body, method="POST",
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.load(r)
            u = out.get("usage") or {}
            with _LOCK:
                _COUNT["or_prompt"] += u.get("prompt_tokens") or 0
                _COUNT["or_completion"] += u.get("completion_tokens") or 0
            raw = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            text = vendor()["chat_driver"]._spoken(raw.strip())
            if text:
                return text
            last = RuntimeError("empty customer line")
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 * (attempt + 1))
    raise last


# --- judge ------------------------------------------------------------------------------------------------------------------------
def openrouter_json(system: str, user: str, model: str, temperature: float) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY") or ""
    body = json.dumps({"model": model, "temperature": temperature, "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body, method="POST",
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                out = json.load(r)
            u = out.get("usage") or {}
            with _LOCK:
                _COUNT["or_prompt"] += u.get("prompt_tokens") or 0
                _COUNT["or_completion"] += u.get("completion_tokens") or 0
            text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
            return json.loads(text[text.index("{"):text.rindex("}") + 1])
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


def judge(persona: dict, transcript: list[tuple[str, str]], ledger_calls: list) -> dict:
    """GUESS of Freya's judge: it reads the conversation and the persona's successCriteria (Freya: gemini-3-flash-preview). Here the model
    also gets the recorded tool calls as text, and answers yes/no per criterion."""
    criteria = [x.strip("- ").strip() for x in persona["successCriteria"].splitlines() if x.strip().startswith("-")]
    conv = "\n".join(f"{'AGENT' if r == 'assistant' else 'CUSTOMER'}: {t}" for r, t in transcript)
    calls = json.dumps(ledger_calls, ensure_ascii=False, default=str)[:3500]
    system = ("You judge one phone call between a bank's voice agent and a customer. For each numbered criterion answer yes or no, strictly, "
              "from the conversation and the recorded tool calls. Reply JSON {\"criteria\": [{\"n\": 1, \"answer\": \"yes\"|\"no\", \"why\": \"short\"}]}.")
    user = (f"CONVERSATION:\n{conv}\n\nRECORDED TOOL CALLS (what the bank saw):\n{calls}\n\nCRITERIA:\n"
            + "\n".join(f"{i + 1}. {c}" for i, c in enumerate(criteria)))
    out = openrouter_json(system, user, JUDGE_MODEL, JUDGE_TEMP)
    rows = {int(r.get("n", 0)): r for r in out.get("criteria") or [] if str(r.get("n", "")).isdigit()}
    checks = [{"check": c, "answer": (rows.get(i + 1) or {}).get("answer"), "why": (rows.get(i + 1) or {}).get("why")} for i, c in enumerate(criteria)]
    return {"pass": bool(checks) and all(c["answer"] == "yes" for c in checks), "checks": checks, "model": JUDGE_MODEL}


# --- one conversation -------------------------------------------------------------------------------------------------------------
def run_conversation(workflow, case, config, bank, rep, wall_limit=900):
    V = vendor()
    t0 = time.time()
    session = bank.open_session(TEAM, case["id"])
    variables = session["variables"]

    def bank_call(name, body):
        handler = V["mockbank"].HANDLERS.get(name)
        if handler is None:
            return 404, {"ok": False, "error": f"unknown tool {name}"}
        body = dict(body or {})
        target = bank.session_for(str(body.get("customer_id") or "")) or session
        with bank.lock:
            return handler(target, body, config)

    model = ClaudeModel()
    engine = Engine(workflow, dict(variables), bank_call, model)
    persona = V["persona_for"](case, {"identity": case["identity"]}, case.get("style"))   # exactly what Freya gets
    agent_lines, customer_lines, error = [], [], None
    try:
        first = engine.start()
        if first:
            agent_lines.append(first)
        for _ in range(MAX_CUSTOMER_TURNS):
            if engine.ended or not agent_lines or time.time() - t0 > wall_limit:
                break
            reply = customer_reply(persona["prompt"], agent_lines, customer_lines)
            customer_lines.append(reply)
            said = engine.user_says(reply)
            if said:
                agent_lines.append(said)
            elif not engine.ended:
                break
        engine.finish()
    except LimitHit:
        raise
    except Exception as e:  # noqa: BLE001
        error = repr(e)[:300]
    ledger = bank.ledger(TEAM, variables["customer_id"])
    led = V["score"](case, ledger)
    transcript = [(e["role"], e["message"]) for e in engine.events if e["role"] in ("user", "assistant")]
    verdict = judge(persona, transcript, ledger.get("calls") or [])
    nodes = [json.loads(e["message"])["node"] for e in engine.events if e["role"] == "system" and '"node_enter"' in e["message"]]
    sys_events = [json.loads(e["message"]) for e in engine.events if e["role"] == "system" and e["message"].startswith("{")]
    passed = bool(led["pass"] and verdict["pass"] and not error)
    return {"case": case["id"], "family": case["family"], "rep": rep, "pass": passed, "ledger_pass": led["pass"], "ledger_failed": led.get("failed") or [],
            "judge": verdict, "error": error, "nodes": nodes, "transcript": transcript,
            "variables": [e for e in sys_events if e.get("event") == "variable_extracted"],
            "tool_calls": [e for e in sys_events if e.get("event") in ("function_call_start", "function_call_complete")],
            "agent_model_calls": model.calls, "seconds": round(time.time() - t0, 1)}


# --- driver -----------------------------------------------------------------------------------------------------------------------
def credit():
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + os.environ.get("OPENROUTER_API_KEY", "")})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)["data"]
        return {"usage": d.get("usage"), "limit": d.get("limit"), "limit_remaining": d.get("limit_remaining")}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:80]}


def model_price():
    try:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=20) as r:
            for m in json.load(r)["data"]:
                if m["id"] == CUSTOMER_MODEL:   # customer and judge are the same model
                    return float(m["pricing"]["prompt"]), float(m["pricing"]["completion"])
    except Exception:  # noqa: BLE001
        pass
    return 0.0, 0.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", required=True, help="a compiled set under out/template_cc/, e.g. p6, m1, m1b")
    ap.add_argument("--briefs", required=True)
    ap.add_argument("--cases", default="", help="comma-separated case ids (must be DEV cases)")
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--max", type=int, default=60, help="hard cap of conversations for this invocation")
    ap.add_argument("--pilot", action="store_true", help="run ONE conversation (the first DEV case) and print the projection")
    ap.add_argument("--sealed", action="store_true", help="GATE cases 11-20, COUNTS ONLY: no transcript, no failed criterion is written or printed; "
                    "writes pass/fail counts per brief to out/template_cc/localsim/sealed_counts.json (the 18:45 m2-vs-m1 check)")
    a = ap.parse_args(argv)
    llm.load_env()
    print("OpenRouter credit before:", json.dumps(credit()))
    pin, pout = model_price()
    jobs = []
    for brief in a.briefs.split(","):
        cases, config = sources(brief)
        dev = dev_cases(cases, 10, 10 if a.sealed else 0)
        want = set(a.cases.split(",")) if a.cases else None
        if want and a.sealed:
            print("refused: --sealed runs the whole GATE slice, not chosen cases", file=sys.stderr)
            return 2
        if want and not want <= {c["id"] for c in dev}:
            print(f"refused: {sorted(want - {c['id'] for c in dev})} are not DEV cases of {brief}", file=sys.stderr)
            return 2
        wf = json.loads((REPO / "out" / "template_cc" / a.set / brief / "workflow.json").read_text())
        wf = wf.get("workflow", wf)
        bank = vendor()["mockbank"].Bank(dev, config)
        for c in dev:
            if not want or c["id"] in want:
                jobs.append((wf, c, config, bank, brief))
    jobs = jobs[:1] if a.pilot else jobs[: a.max]
    out_dir = OUT / a.set
    t0 = time.time()
    results = []

    def one(job):
        wf, case, config, bank, brief = job
        if STOP.is_set():
            return None
        try:
            with Slot():
                n_used = budget_claim(a.sealed)
                if n_used is None:
                    why = f"time cutoff ({HARD_STOP if a.sealed else CUTOFF})" if hhmm() >= (HARD_STOP if a.sealed else CUTOFF) else f"cap reached: {TOTAL_CAP} conversations used in total (all sessions)"
                    print(f"not started: {why}", flush=True)
                    return None
                r = run_conversation(wf, case, config, bank, a.rep)
        except Cutoff as e:
            print("not finished:", e, flush=True)
            return None
        except LimitHit as e:
            STOP.set(str(e)[:120])
            print("STOP: claude limit warning (all local sim runs stop):", e, flush=True)
            return None
        r["brief"], r["set"] = brief, a.set
        if a.sealed:      # GATE slice: only the verdict leaves this function
            print(f"{brief:16s} (sealed) counted", flush=True)
            return {"brief": brief, "pass": bool(r["pass"]), "sealed": True}
        d = out_dir / brief
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{case['id']}.{a.rep}.json").write_text(json.dumps(r, indent=1, ensure_ascii=False, default=str))
        print(f"{brief:16s} {case['id']:8s} {'PASS' if r['pass'] else 'FAIL'}  {r['seconds']:6.0f}s  agent calls {r['agent_model_calls']:3d}  "
              f"failed: {'; '.join(r['ledger_failed'] + [c['check'][:50] for c in r['judge']['checks'] if c['answer'] != 'yes'])[:150]}", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=max(1, a.parallel)) as pool:
        results = [r for r in pool.map(one, jobs) if r]
    wall = time.time() - t0
    if a.sealed:
        counts_f = OUT / "sealed_counts.json"
        data = json.loads(counts_f.read_text()) if counts_f.exists() else {}
        by = data.setdefault(a.set, {})
        for r in results:
            b = by.setdefault(r["brief"], {"passed": 0, "cases": 0})
            b["passed"] += int(r["pass"])
            b["cases"] += 1
        counts_f.write_text(json.dumps(data, indent=1))
        print(f"sealed counts for set {a.set}: " + ", ".join(f"{b} {v['passed']}/{v['cases']}" for b, v in sorted(by.items())) +
              f" (total {sum(v['passed'] for v in by.values())}/{sum(v['cases'] for v in by.values())})")
        return 1 if STOP.is_set() else 0
    cost = _COUNT["or_prompt"] * pin + _COUNT["or_completion"] * pout
    n = max(len(results), 1)
    print(f"\n{len(results)}/{len(jobs)} conversations, {wall:.0f}s wall; claude calls {_COUNT['claude_calls']} ({_COUNT['claude_calls'] / n:.0f}/conversation); "
          f"customer tokens {_COUNT['or_prompt']}+{_COUNT['or_completion']} = ${cost:.4f} (${cost / n:.4f}/conversation)")
    print("OpenRouter credit after:", json.dumps(credit()))
    if a.pilot:
        print(f"PROJECTION for 20 conversations at concurrency 3: ~{_COUNT['claude_calls'] / n * 20:.0f} claude calls, ~{wall * 20 / 3 / 60:.0f} min")
    return 1 if STOP.is_set() else 0


if __name__ == "__main__":
    sys.exit(main())
