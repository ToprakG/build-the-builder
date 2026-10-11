"""The builder's tools as an in-process SDK MCP server named `fb` (model-facing names `mcp__fb__<tool>`).

The tools take **no path arguments**: each is bound at startup to the workspace's fixed files (input/*,
out/workflow.json, out/rules.json, out/policy.json). The agent passes only node ids, tool names and JSON values.
`BoundTools` holds the logic and needs no SDK; `build_server` wraps it.
"""

from __future__ import annotations

import json
from pathlib import Path

from builder.agentic.tools import clock as clock_mod
from builder.agentic.tools import coverage, lint, openapi_summary, policy_suite, snippets, unit_tests, validate

SERVER = "fb"
OBJ = {"type": "object", "properties": {}, "additionalProperties": False}

TOOL_SPECS = {
    "openapi_summary": ("The bank's tool API (input/tools.openapi.json): per tool the URL, request fields with type, "
                        "required flag, enum and description, response fields and status texts.", OBJ),
    "lint_workflow": ("Static checks of out/workflow.json against the tool API and the kit docs. Returns errors and "
                      "warnings with JSON paths. On 0 errors it saves out/last_good.workflow.json.", OBJ),
    "run_snippet": ("Run a code node or a hook of out/workflow.json locally, as the platform would. kind: code (a code "
                    "node), pre_hook or post_hook (a tool's pre/post_process_code; give tool). node_id 'workflow' "
                    "addresses workflow-level tools. vars_json: JSON object merged over test_variables. result_json "
                    "(post_hook): the API body, or a full envelope {status, data, message, status_code}.",
                    {"type": "object", "properties": {
                        "node_id": {"type": "string", "description": "Node id, or 'workflow'."},
                        "kind": {"type": "string", "enum": ["code", "pre_hook", "post_hook"]},
                        "tool": {"type": "string", "description": "Tool name, for hooks."},
                        "vars_json": {"type": "string", "description": "JSON object of variables, e.g. {\"now\": \"2031-03-07T11:00\"}."},
                        "result_json": {"type": "string", "description": "JSON of the API result, for post_hook."}},
                     "required": ["node_id", "kind"], "additionalProperties": False}),
    "check_coverage": ("Checks out/rules.json: every source_quote is in the brief, every implemented_in location "
                       "exists in out/workflow.json, n/a rules have a reason; reports numbered brief lines not cited.", OBJ),
    "policy_unit_suite": ("The required unit-test templates generated from out/policy.json and the tool API: what the "
                          "customer says at which stage and what your agent's next turn must or must not do. Write one test "
                          "per template in out/unit_tests.json with \"template\": <id>, your own start_node, variables and "
                          "messages, and keep the template's checks (you may add more). check_unit_tests enforces it.", OBJ),
    "check_unit_tests": ("Checks out/unit_tests.json offline: every start_node and checked node exists in out/workflow.json, "
                         "every tool, body field and mock is in the tool API, messages end with the customer's line. "
                         "The tests run on Freya after the build; write them per reference/unit-tests.md.", OBJ),
    "run_local_sims": ("Plays hard simulated calls against out/workflow.json on this machine (no Freya): a local engine "
                       "with DeepSeek v4.1 Flash as the agent model, the mock bank, an LLM customer in hard talking styles "
                       "(numbers in pieces, self-corrections, amounts in words, questions back), scored on the bank records "
                       "and by the Jev decision-model judge. Cases are generated from out/policy.json. Takes 1-3 minutes. "
                       "Returns each failed case with its failed checks, the nodes it passed and the end of its transcript.",
                       {"type": "object", "properties": {
                           "n_cases": {"type": "integer", "description": "How many cases (default 10, max 20)."},
                           "seed": {"type": "integer", "description": "Change it to get other cases (default 7)."}},
                        "additionalProperties": False}),
    "validate_workflow": ("Freya dry-run validation of out/workflow.json (stores nothing). Returns the platform's answer, "
                          "or skipped when no key is set.", OBJ),
    "time_left": ("Minutes left on this build's wall clock and the phase plan. Call it at every phase boundary.", OBJ),
}


class BoundTools:
    def __init__(self, workspace: Path, clock: clock_mod.Clock, on_event=None, validate_fn=validate.validate):
        ws = Path(workspace)
        self.ws = ws
        self.api_path = ws / "input" / "tools.openapi.json"
        self.workflow = ws / "out" / "workflow.json"
        self.last_good = ws / "out" / "last_good.workflow.json"
        self.rules = ws / "out" / "rules.json"
        self.policy = ws / "out" / "policy.json"
        self.unit_tests = ws / "out" / "unit_tests.json"
        self.briefs = sorted((ws / "input").glob("brief-*"), key=lambda p: int(p.stem.split("-")[1]))
        self.clock = clock
        self.on_event = on_event or (lambda kind, detail: None)
        self.validate_fn = validate_fn
        self._api = None

    @property
    def api(self) -> dict:
        if self._api is None:
            self._api = openapi_summary.load(self.api_path)
        return self._api

    def _wf(self):
        try:
            return json.loads(self.workflow.read_text(encoding="utf-8")), None
        except FileNotFoundError:
            return None, "out/workflow.json does not exist yet"
        except json.JSONDecodeError as e:
            return None, f"out/workflow.json is not valid JSON: {e}"

    def openapi_summary(self, args=None) -> str:
        return openapi_summary.render(self.api)

    def lint_workflow(self, args=None) -> str:
        report = lint.lint_file(self.workflow, self.api, self.policy, self.last_good)
        self.on_event("check", {"lint": f"{len(report['errors'])} errors, {len(report['warnings'])} warnings"})
        return lint.render(report)

    def run_snippet(self, args) -> str:
        args = args or {}
        wf, err = self._wf()
        if err:
            return err
        try:
            variables = json.loads(args.get("vars_json") or "{}")
            result = json.loads(args["result_json"]) if args.get("result_json") else None
        except json.JSONDecodeError as e:
            return f"vars_json/result_json is not valid JSON: {e}"
        if not isinstance(variables, dict):
            return "vars_json must be a JSON object"
        res = snippets.run_snippet(wf, str(args.get("node_id", "")), variables, kind=args.get("kind", "code"),
                                   tool=args.get("tool") or None, result=result)
        self.on_event("check", {"run_snippet": f"{args.get('node_id')}/{args.get('kind')}",
                                "exception": bool(res.get("exception") or res.get("error"))})
        return snippets.render(res)

    def check_coverage(self, args=None) -> str:
        wf, err = self._wf()
        try:
            rules = json.loads(self.rules.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "out/rules.json does not exist yet"
        except json.JSONDecodeError as e:
            return f"out/rules.json is not valid JSON: {e}"
        texts = [p.read_text(encoding="utf-8") for p in self.briefs]
        report = coverage.check(rules, wf or {}, texts, api_text=self.api_path.read_text(encoding="utf-8"))
        if err:
            report["errors"].insert(0, {"rule": None, "msg": err})
        self.on_event("check", {"coverage": f"{len(report['errors'])} errors, {len(report['warnings'])} warnings"})
        return coverage.render(report)

    def _required(self, wf):
        try:
            policy = json.loads(self.policy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return policy_suite.templates(policy, self.api, (wf.get("test_variables") or {}).get("now"))

    def policy_unit_suite(self, args=None) -> str:
        wf, err = self._wf()
        required = self._required(wf or {})
        if required is None:
            return "write out/policy.json first: the templates are generated from it"
        return json.dumps(required, indent=1, ensure_ascii=False)

    def check_unit_tests(self, args=None) -> str:
        wf, err = self._wf()
        if err:
            return err
        try:
            suite = json.loads(self.unit_tests.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "out/unit_tests.json does not exist yet"
        except json.JSONDecodeError as e:
            return f"out/unit_tests.json is not valid JSON: {e}"
        report = unit_tests.validate(suite, wf, self.api, self._required(wf))
        self.on_event("check", {"unit_tests": f"{len(report['errors'])} errors, {report.get('tests', 0)} tests"})
        return unit_tests.render(report)

    def run_local_sims(self, args=None) -> str:
        from builder.agentic.localsim import sim  # noqa: PLC0415  (imports the vendor tester lazily)
        from builder.template_cc.vendor_runner import bank_config  # noqa: PLC0415

        ok, why = sim.available()
        if not ok:
            return f"local simulations unavailable: {why}"
        wf, err = self._wf()
        if err:
            return err
        try:
            policy = json.loads(self.policy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return f"out/policy.json is needed to generate cases: {e}"
        args = args or {}
        n = max(1, min(int(args.get("n_cases") or 10), 20))
        try:
            cases = sim.cases_from_policy(policy, n, int(args.get("seed") or 7))
        except Exception as e:  # noqa: BLE001
            return f"could not generate cases from policy.json (check its keys against reference/policy-schema.md): {e!r}"[:600]
        results = sim.run_cases(wf, cases[:n], bank_config(policy), parallel=5)
        (self.ws / "out" / "localsim_results.json").write_text(json.dumps(results, indent=1, default=str))
        derived_path = self.ws / "out" / "derived_unit_tests.json"
        try:
            old = json.loads(derived_path.read_text(encoding="utf-8")) if derived_path.exists() else []
        except json.JSONDecodeError:
            old = []
        new = sim.derive_tests(results, wf)
        merged = {t["name"]: t for t in old + new}
        derived_path.write_text(json.dumps(list(merged.values())[-12:], indent=1, ensure_ascii=False, default=str))
        self.on_event("check", {"local_sims": sim.summary(results), "derived_unit_tests": len(new)})
        note = (f"\n\n{len(new)} regression unit test(s) were derived from the failed turns (out/derived_unit_tests.json). "
                "They run on Freya after your build together with your own tests, before and after the fix.") if new else ""
        return sim.render(results) + note

    def validate_workflow(self, args=None) -> str:
        wf, err = self._wf()
        if err:
            return err
        res = self.validate_fn(wf, "dry-run")
        self.on_event("check", {"validate": res})
        return json.dumps(res)

    def time_left(self, args=None) -> str:
        return json.dumps(self.clock.time_left())

    def call(self, name: str, args: dict | None = None) -> str:
        if name not in TOOL_SPECS:
            return f"unknown tool {name}"
        try:
            return getattr(self, name)(args or {})
        except Exception as e:  # noqa: BLE001  (a tool error goes back to the agent as text)
            self.on_event("error", {"tool": name, "error": repr(e)})
            return f"tool error: {e!r}"


def tool_names() -> list[str]:
    return [f"mcp__{SERVER}__{name}" for name in TOOL_SPECS]


def build_server(bound: BoundTools):
    """The SDK server config for ClaudeAgentOptions.mcp_servers[SERVER]."""
    from claude_agent_sdk import create_sdk_mcp_server, tool  # noqa: PLC0415

    def make(name, description, schema):
        @tool(name, description, schema)
        async def handler(args):
            text = bound.call(name, args)
            return {"content": [{"type": "text", "text": text}]}

        return handler

    return create_sdk_mcp_server(SERVER, "1.0.0", [make(n, d, s) for n, (d, s) in TOOL_SPECS.items()])
