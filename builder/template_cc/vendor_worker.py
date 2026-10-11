"""Play T-CC's own cases against a published workflow with build-the-builder's chat driver (run as a script).

    python vendor_worker.py --vendor <repo> --job job.json --out results.json

job.json: {"workflow_id", "origin" (the mock bank's public URL), "cases": [...], "reps": n, "concurrency": n}
For each case and rep: open a bank session, play the vendor's LLM customer (persona_for + customer_reply) against the
workflow over the chat API, read the bank ledger, score it with score_ledger, and judge the conversation with the
vendor decision-model judge (judge_transcript). Writes one row per case and rep, with the transcript and tool calls.
Runs in its own process because the vendor modules use generic names (freya, score, judge).
"""
import argparse
import json
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CLOSE_WAIT_S = 20
INFRA = re.compile(r"/api/v2/chat/\S* -> 5\d\d|timed out|Connection reset|Temporary failure|Max retries", re.I)


class Adaptive:
    """A concurrency limit that halves on an infrastructure error (chat API 5xx) and grows by one after a few clean
    runs: as fast as the platform allows, without flooding it. Same cases, same judge: only the pace changes."""

    def __init__(self, start: int, cap: int):
        import threading
        self.limit, self.cap, self.active, self.clean = start, cap, 0, 0
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)

    def __enter__(self):
        with self.cond:
            while self.active >= self.limit:
                self.cond.wait()
            self.active += 1

    def __exit__(self, *exc):
        with self.cond:
            self.active -= 1
            self.cond.notify_all()

    def report(self, infra: bool):
        with self.cond:
            if infra:
                self.limit, self.clean = max(1, self.limit // 2), 0
            else:
                self.clean += 1
                if self.clean >= 2 and self.limit < self.cap:
                    self.limit, self.clean = self.limit + 1, 0
            self.cond.notify_all()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vendor", required=True)
    ap.add_argument("--job", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    root = Path(a.vendor).resolve()
    for p in (root, root / "tools", root / "tools" / "driver"):
        sys.path.insert(0, str(p))
    import freya
    from chat_driver import MAX_TURNS, _spoken, bank_call, chat_turn, customer_reply, for_judge
    from judge import judge_transcript
    from run_cases import persona_for
    from score import score_ledger

    job = json.loads(Path(a.job).read_text())
    wid, origin = job["workflow_id"], job["origin"].rstrip("/")
    language = str(job.get("language") or "en").lower()[:2]
    names = {"tr": "Turkish", "de": "German", "fr": "French", "es": "Spanish", "nl": "Dutch", "it": "Italian"}
    customer_model = job.get("customer_model")  # e.g. deepseek/deepseek-v4.1-flash on OpenRouter; None = vendor default

    def customer(prompt, agent_lines, customer_lines):
        if not customer_model:
            return customer_reply(prompt, agent_lines, customer_lines)
        import os
        import urllib.request
        messages = [{"role": "system", "content": prompt}]
        for i, line in enumerate(agent_lines):  # the agent speaks as "user", the customer answers as "assistant"
            messages.append({"role": "user", "content": line})
            if i < len(customer_lines):
                messages.append({"role": "assistant", "content": customer_lines[i]})
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", method="POST",
                                     data=json.dumps({"model": customer_model, "temperature": 0.5, "messages": messages}).encode(),
                                     headers={"Authorization": "Bearer " + os.environ.get("OPENROUTER_API_KEY", ""),
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.load(r)
        text = _spoken(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        if not text:
            raise RuntimeError("the customer model returned no spoken line")
        return text

    def closed(ledger):
        calls = ledger.get("calls") or []
        return bool(calls) and calls[-1].get("tool") == "close_call" and (calls[-1].get("result") or {}).get("ok") is True

    def play(case, rep):
        row = {"case_id": case["id"], "rep": rep, "passed": False, "failed_reasons": [], "transcript": [], "tool_calls": [],
               "style": case.get("style"), "family": case.get("family"), "level": case.get("level")}
        try:
            status, opened = bank_call(origin, "/v1/practice/sessions", {"case": case["id"]})
            if status != 200:
                raise RuntimeError(f"bank session -> {status} {opened}")
            variables = opened["variables"]
            prompt = persona_for(case, {"identity": case["identity"]}, case.get("style") or None)["prompt"]
            prompt += ("\n\nWHEN YOU NAME A TIME: say the hour and the minutes, like 11:00. Do not say morning, "
                       "afternoon, or around in place of that time.")
            if language != "en":  # the call is in the brief's language: the persona speaks it too (numbers, dates, names as people say them)
                prompt += f"\n\nThe whole call is in {names.get(language, language)}. Speak only {names.get(language, language)}."
            call_id, agent, _ = chat_turn(wid, "Alo?" if language == "tr" else "Hello?", variables)
            agent_lines, customer_lines = ([agent] if agent else []), []
            for _ in range(MAX_TURNS):
                if not agent_lines:
                    break
                reply = customer(prompt, agent_lines, customer_lines)
                customer_lines.append(reply)
                call_id, agent, _ = chat_turn(wid, reply, variables, call_id)
                if agent:
                    agent_lines.append(agent)
                _, ledger = bank_call(origin, "/v1/practice/ledger/" + variables["customer_id"])
                if closed(ledger) or not agent:
                    break
            try:
                freya.api("POST", "/api/v2/chat/end", {"callId": call_id})
            except Exception:
                pass
            t0 = time.time()  # the close record is a lifecycle tool: it lands after the chat ends
            while True:
                _, ledger = bank_call(origin, "/v1/practice/ledger/" + variables["customer_id"])
                if closed(ledger) or time.time() - t0 > CLOSE_WAIT_S:
                    break
                time.sleep(2)
            scored = score_ledger(case, ledger)
            messages = []
            if call_id:
                try:
                    messages = freya.get_call(call_id).get("messages") or []
                except Exception:
                    messages = []
            judged = (judge_transcript(for_judge(messages), case) if messages
                      else {"pass": None, "status": "no_transcript", "checks": []})
            failed = list(scored["failed"])
            for c in judged.get("checks") or []:
                if c.get("answer") != "yes":
                    failed.append(f"conversation: {c.get('check')} (answer {c.get('answer')}, p={c.get('p')})")
            if judged.get("pass") is None and judged.get("status") not in ("ok",):
                failed.append(f"conversation: judge status {judged.get('status')}")
            row.update(passed=bool(scored["pass"]) and judged.get("pass") is True, failed_reasons=failed,
                       call_id=call_id, judge_status=judged.get("status"),
                       tool_calls=[{"tool": c.get("tool"), "args": c.get("args"), "result": c.get("result")}
                                   for c in ledger.get("calls") or []],
                       transcript=[{"role": m.get("role"), "text": m.get("message") or m.get("content") or ""}
                                   for m in for_judge(messages) if m.get("role") in ("assistant", "user")])
        except Exception as e:  # noqa: BLE001 - one broken case is a failed case, not a broken run
            row["failed_reasons"] = [f"runner error: {e!r}"]
            row["error"] = traceback.format_exc(limit=3)
            row["infra"] = bool(INFRA.search(repr(e)))  # the platform or network failed, not the agent
        return row

    jobs = [(c, r) for c in job["cases"] for r in range(1, int(job.get("reps", 1)) + 1)]
    start = max(1, int(job.get("concurrency", 2)))
    cap = max(start, int(job.get("max_concurrency") or start * 3))
    gate = Adaptive(start, cap)
    partial = Path(a.out + ".partial.jsonl")

    def one(cr):
        with gate:
            row = play(*cr)
        gate.report(bool(row.get("infra")))
        with gate.lock:  # every finished run is visible at once (live scoreboard)
            with partial.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return row

    with ThreadPoolExecutor(max_workers=cap) as pool:
        rows = list(pool.map(one, jobs))
    Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
