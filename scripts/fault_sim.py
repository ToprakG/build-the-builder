"""Tool-failure probe: a DEV happy-path case with a bank fault injected the way a 'Tool failure' case carries it
({"tool": ..., "fail_first_n": N}); prints the tool calls, the node path, the agent lines and the closing outcome. No judge (diagnosis only).

    .venv310/bin/python scripts/fault_sim.py --set m1g3 --brief brief-a --fault create_promise:2 [--case A-001] [--rep 1]
DEV cases only (the same dev_cases() cut as local_sim.py); uses the shared local-sim budget."""
import argparse
import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import local_sim as L  # noqa: E402


def platform_errors():
    """Emulate the real platform on a 5xx: function_call_error, NO post-process hook, no response variables, no mapping, only the failure counter
    (seen in real Freya calls: the event list has function_call_start then function_call_error and no function_call_complete)."""
    from builder.agentic.localsim import engine as E
    orig = E.Engine.exec_api

    def exec_api(self, tool, model_args, owner, placement):
        name = E._fn(tool).get("name")
        merged = {**self.vars, **(model_args or {})}
        config = E.snippets.build_config(tool, merged)
        body = config.get("body") if isinstance(config.get("body"), dict) else {}
        probe_vars = dict(self.vars)
        status, data = self.bank_call(name, body)
        if status < 500:
            # replay the normal path (the bank call above already ran once for a 2xx: re-run via the original would double-call), so inline it
            self._event(event="function_call_start", function=name, args={"url": config.get("url"), "body": body})
            envelope = {"status": "success" if status < 400 else "error", "data": data, "message": "ok", "status_code": status,
                        "header": {"Content-Type": "application/json"}}
            if tool.get("post_process_code"):
                res = self.run_snippet(tool, "post_hook", owner, result=envelope)
                if res and isinstance(res.get("result"), dict):
                    envelope = res["result"]
            self._event(event="function_call_complete", function=name, result=envelope)
            prefix = owner if placement == "pre_actions" else name
            flat = {}
            E.flatten(f"{prefix}_response", {k: v for k, v in envelope.items() if k != "header"}, flat)
            self.vars.update(flat)
            self.vars[f"{prefix}_status_code"] = status
            key = "success" if status < 400 else "failure"
            self.vars[f"{prefix}_{key}_count"] = int(self.vars.get(f"{prefix}_{key}_count") or 0) + 1
            if placement == "tools":
                self._map(tool.get("response_mapping"), envelope)
            elif placement == "pre_actions":
                self._map((self._get(owner) or {}).get("response_mapping"), envelope)
            return envelope
        self._event(event="function_call_start", function=name, args={"url": config.get("url"), "body": body})
        self._event(event="function_call_error", function=name, error=f"API request failed: {data}")
        prefix = owner if placement == "pre_actions" else name
        self.vars[f"{prefix}_failure_count"] = int(self.vars.get(f"{prefix}_failure_count") or 0) + 1
        return {"status": "error", "data": data, "message": "failed", "status_code": status}

    E.Engine.exec_api = exec_api


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--brief", required=True)
    ap.add_argument("--fault", required=True, help="tool:N[,tool:N]")
    ap.add_argument("--case", default="")
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--platform-errors", action="store_true", help="a 5xx behaves like the real platform (no hook, no response variables)")
    a = ap.parse_args()
    if a.platform_errors:
        platform_errors()
    L.llm.load_env()
    cases, config = L.sources(a.brief)
    dev = L.dev_cases(cases)
    case = next(c for c in dev if (c["id"] == a.case if a.case else c["family"] == "Happy path"))
    case = copy.deepcopy(case)
    case["faults"] = [{"tool": t.split(":")[0], "fail_first_n": int(t.split(":")[1])} for t in a.fault.split(",")]
    wf = json.loads((REPO / "out/template_cc" / a.set / a.brief / "workflow.json").read_text())
    wf = wf.get("workflow", wf)
    bank = L.vendor()["mockbank"].Bank([case], config)
    with L.Slot():
        if L.budget_claim() is None:
            print("cap reached"); return 2
        r = L.run_conversation(wf, case, config, bank, a.rep)
    tag = a.fault.replace(":", "").replace(",", "+") + ("P" if a.platform_errors else "")
    out = REPO / "out/template_cc/localsim/faults" / a.set / a.brief
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{case['id']}.{tag}.{a.rep}.json").write_text(json.dumps(r, indent=1, default=str, ensure_ascii=False))
    calls = [(e["function"], (e.get("args") or {}).get("body") if e["event"] == "function_call_start" else None)
             for e in r["tool_calls"] if e["event"] == "function_call_start"]
    print(f"== {a.brief} {case['id']} fault {a.fault}  error={r['error']}")
    for fn, body in calls:
        print("   call", fn, json.dumps(body)[:150])
    for who, m in r["transcript"][-8:]:
        print(f"   {who[:1].upper()}: {m[:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
