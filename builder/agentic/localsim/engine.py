"""A local Freya engine: runs a workflow.json the way docs/workflow-format.md and docs/tools-and-code.md describe the
platform, with any chat model (DeepSeek v4.1 Flash on OpenRouter by default) in the three model roles.

Per user message: extraction (the node's extraction_schema) -> routing (deterministic conditions in order; intents in
one model call; global nodes; max_node_turns) -> dialogue (system_prompt + role_message + task, the node's tools).
On entry: code nodes run their snippet, api_request nodes run their pre_actions, passthrough nodes route at once,
conversation nodes speak their on_enter (tts / llm / silent), end_call nodes end the call. Tool results are flattened
into `<tool>_response.*` / `<node>_response.*` with booleans as 'true'/'false', `<tool>_args.*`, success and failure
counters; post- and pre-hooks run locally. At the end the workflow-level end_of_call tools run.

Platform facts it mimics (reference/platform-facts.md): a node-level mapping of a list leaves the variable unset.
What it cannot mimic: the platform's own agent model. Structural bugs reproduce; model behaviour may differ.
"""

from __future__ import annotations

import json
import re

from builder.agentic.tools import snippets

PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
API, CODE, END = "__function__:api_call", "__function__:code_exec", "__function__:end_call"
MAX_HOPS = 40


def _fn(t):
    return (t or {}).get("function") or {}


def _props(t):
    return ((_fn(t).get("parameters") or {}).get("properties")) or {}


def flatten(prefix: str, value, out: dict):
    if isinstance(value, dict):
        for k, v in value.items():
            flatten(f"{prefix}.{k}", v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            flatten(f"{prefix}.{i}", v, out)
    elif isinstance(value, bool):
        out[prefix] = "true" if value else "false"
    else:
        out[prefix] = value


class Engine:
    def __init__(self, workflow: dict, call_vars: dict, bank_call, model, max_turns_per_call: int = 60):
        self.wf, self.bank_call, self.model = workflow, bank_call, model
        self.nodes = workflow.get("nodes") or {}
        self.gnodes = workflow.get("global_nodes") or {}
        self.vars = {**(workflow.get("test_variables") or {}), **call_vars}
        self.history: list[dict] = []   # chat-format history for the dialogue model
        self.events: list[dict] = []    # Freya-format messages (for the judge and the transcript)
        self.node = None
        self.prev = None
        self.entries: dict[str, int] = {}
        self.node_turns: dict[str, int] = {}
        self.ended = False
        self.spoken: list[str] = []
        self.max_turns = max_turns_per_call
        self.errors: list[str] = []
        self.turn_log: list[dict] = []  # per customer turn: node, variables, the agent's previous line, the customer's line

    # --- helpers ----------------------------------------------------------------------------------------------------
    def subst(self, text) -> str:
        if not isinstance(text, str):
            return ""

        def one(m):
            name, _, fmt = m.group(1).partition(":")
            if name not in self.vars:
                return m.group(0)
            v = self.vars[name]
            return json.dumps(v) if fmt == "json" else str(v)
        return PLACEHOLDER.sub(one, text)

    def _get(self, nid):
        return self.nodes.get(nid) or self.gnodes.get(nid)

    def say(self, text):
        text = (text or "").strip()
        if text:
            self.spoken.append(text)
            self.history.append({"role": "assistant", "content": text})
            self.events.append({"role": "assistant", "message": text, "nodeId": self.node})

    def _event(self, **e):
        self.events.append({"role": "system", "message": json.dumps(e, default=str)})

    # --- tools --------------------------------------------------------------------------------------------------------
    def run_snippet(self, tool, kind, node_id, result=None, config=None):
        code = tool.get("pre_process_code" if kind == "pre_hook" else "post_process_code") if kind != "code" else snippets._code_of(tool)
        if not isinstance(code, str) or not code.strip():
            return None
        if node_id == "workflow":
            wf = {"tools": [tool]}
        elif kind == "code":
            wf = {"nodes": {node_id: {"pre_actions": [tool]}}}
        else:
            wf = {"nodes": {node_id: {"tools": [tool]}}}
        res = snippets.run_snippet(wf, node_id, dict(self.vars), kind=kind, tool=_fn(tool).get("name"), result=result)
        if res.get("exception") or res.get("error"):
            self.errors.append(f"{kind} {node_id}/{_fn(tool).get('name')}: {(res.get('exception') or res.get('error'))[-300:]}")
        self.vars.update(res.get("vars_written") or {})
        return res

    def exec_api(self, tool, model_args: dict, owner: str, placement: str):
        """One api_call: substitution, pre-hook, the bank, post-hook, flattened results and counters."""
        name = _fn(tool).get("name")
        merged = {**self.vars, **(model_args or {})}
        config = snippets.build_config(tool, merged)
        if tool.get("pre_process_code"):
            saved = dict(self.vars)
            self.vars.update(model_args or {})
            res = self.run_snippet(tool, "pre_hook", owner)
            if res and isinstance(res.get("config"), dict):
                config = res["config"]
            for k in (model_args or {}):
                if k not in saved:
                    self.vars.pop(k, None)
        body = config.get("body") if isinstance(config.get("body"), dict) else {}
        self._event(event="function_call_start", function=name, args={"url": config.get("url"), "body": body})
        status, data = self.bank_call(name, body)
        envelope = {"status": "success" if status < 400 else "error", "data": data, "message": "ok" if status < 400 else "failed",
                    "status_code": status, "header": {"Content-Type": "application/json"}}
        if tool.get("post_process_code"):
            res = self.run_snippet(tool, "post_hook", owner, result=envelope)
            if res and isinstance(res.get("result"), dict):
                envelope = res["result"]
        self._event(event="function_call_complete", function=name, result=envelope)
        prefix = owner if placement == "pre_actions" else name  # a pre_actions node's keys are named after the node
        flat = {}
        flatten(f"{prefix}_response", {k: v for k, v in envelope.items() if k != "header"}, flat)
        self.vars.update(flat)
        self.vars[f"{prefix}_status_code"] = status
        key = "success" if status < 400 else "failure"
        self.vars[f"{prefix}_{key}_count"] = int(self.vars.get(f"{prefix}_{key}_count") or 0) + 1
        for k, v in (model_args or {}).items():
            self.vars[f"{name}_args.{k}"] = v
        if placement == "tools":
            self._map(tool.get("response_mapping"), envelope)
        elif placement == "pre_actions":
            self._map((self._get(owner) or {}).get("response_mapping"), envelope)  # end_of_call tools map nothing
        return envelope

    def _map(self, mappings, envelope):
        for m in mappings or []:
            cur = envelope
            for part in str(m.get("path", "")).split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None or isinstance(cur, (list, dict)):
                continue  # platform fact 1: a mapped list (or object) stays unset
            self.vars[m["variable"]] = ("true" if cur else "false") if isinstance(cur, bool) else cur

    def model_tools(self, node):
        """Function schemas the dialogue model sees: model-filled body fields, end_call, tool-call transitions."""
        specs, index = [], {}
        for t in node.get("tools") or []:
            fn, handler = _fn(t), _fn(t).get("handler")
            if handler == END:
                specs.append({"type": "function", "function": {"name": fn["name"], "description": fn.get("description") or "End the call.",
                                                               "parameters": {"type": "object", "properties": {}}}})
                index[fn["name"]] = ("end", t)
            elif handler == API:
                body = _props(t).get("body") or {}
                params, required = {}, []
                for field, leaf in (body.get("properties") or {}).items():
                    d = leaf.get("default") if isinstance(leaf, dict) else None
                    m = PLACEHOLDER.fullmatch(d.strip()) if isinstance(d, str) else None
                    var = m.group(1).split(":")[0] if m else None
                    if d is None or (var and var not in self.vars):
                        pname = var or field
                        params[pname] = {k: v for k, v in leaf.items() if k in ("type", "description", "enum")} or {"type": "string"}
                        if field in (body.get("required") or []):
                            required.append(pname)
                specs.append({"type": "function", "function": {"name": fn["name"], "description": fn.get("description") or "",
                                                               "parameters": {"type": "object", "properties": params, "required": required}}})
                index[fn["name"]] = ("api", t)
        for tr in node.get("transitions") or []:
            if tr.get("is_tool_call"):
                tname = f"transition_to_{tr['target_node']}"
                specs.append({"type": "function", "function": {"name": tname, "description": tr.get("description") or "",
                                                               "parameters": {"type": "object", "properties": {}}}})
                index[tname] = ("transition", tr)
        return specs, index

    # --- conditions and routing ---------------------------------------------------------------------------------------
    def _leaves(self, cond):
        if isinstance(cond, dict) and "conditions" in cond:
            for c in cond["conditions"]:
                yield from self._leaves(c)
        elif isinstance(cond, dict):
            yield cond

    def _eval(self, cond, intents: dict) -> bool | None:
        if not isinstance(cond, dict):
            return False
        if "conditions" in cond:
            vals = [self._eval(c, intents) for c in cond["conditions"]]
            if any(v is None for v in vals):
                return None
            return any(vals) if cond.get("logic", "or") == "or" else all(vals)
        op = cond.get("operator")
        if op == "always":
            return True
        if op == "matches_intent":
            return intents.get(cond.get("description")) if intents is not None else None
        var, operand = cond.get("variable"), cond.get("operand")
        present = var in self.vars and self.vars[var] is not None
        value = self.vars.get(var)
        try:
            if op == "is_set":
                return present
            if op == "is_not_set":
                return not present
            if not present:
                return op == "not_equals"
            if op == "equals":
                return str(value) == str(operand)
            if op == "not_equals":
                return str(value) != str(operand)
            if op == "contains":
                return str(operand).lower() in str(value).lower()
            if op == "greater_than":
                return float(value) > float(operand)
            if op == "less_than":
                return float(value) < float(operand)
        except (TypeError, ValueError):
            return False
        return False

    def route(self, user_text: str | None):
        """The next node, or None. Deterministic first; intents in one model call (only after a user message)."""
        node = self._get(self.node) or {}
        trans = [t for t in node.get("transitions") or [] if isinstance(t, dict)]
        for t in trans:  # deterministic transitions, in list order
            if not any(l.get("operator") == "matches_intent" for l in self._leaves(t.get("condition"))):
                if self._eval(t.get("condition"), {}):
                    return t["target_node"]
        if user_text is None:
            return None
        globals_ = [(gid, g) for gid, g in self.gnodes.items() if self.node not in (g.get("excluded_nodes") or []) and gid != self.node]
        for gid, g in globals_:
            if not any(l.get("operator") == "matches_intent" for l in self._leaves(g.get("condition"))) and self._eval(g.get("condition"), {}):
                return gid
        intents = []
        if node.get("loop_condition"):
            intents.append(node["loop_condition"])
        for t in trans:
            intents += [l.get("description") for l in self._leaves(t.get("condition")) if l.get("operator") == "matches_intent"]
        for _, g in globals_:
            intents += [l.get("description") for l in self._leaves(g.get("condition")) if l.get("operator") == "matches_intent"]
        intents = [i for i in dict.fromkeys(intents) if i]
        if not intents:
            return None
        verdict = self.model.intents(self._recent(), user_text, intents)
        if node.get("loop_condition") and verdict.get(node["loop_condition"]):
            return None
        for t in trans:
            if any(l.get("operator") == "matches_intent" for l in self._leaves(t.get("condition"))) and self._eval(t.get("condition"), verdict):
                return t["target_node"]
        for gid, g in globals_:
            if self._eval(g.get("condition"), verdict):
                return gid
        return None

    def _recent(self, n=10):
        return [m for m in self.history if m["role"] in ("user", "assistant") and m.get("content")][-n:]

    # --- nodes --------------------------------------------------------------------------------------------------------
    def enter(self, nid: str, hops: int = 0, from_user: bool = False):
        if nid == "__previous__":
            nid = self.prev or self.wf.get("initial_node")
        node = self._get(nid)
        if node is None or hops > MAX_HOPS or self.ended:
            if node is None:
                self.errors.append(f"unknown node {nid}")
            return
        if nid in self.gnodes and self.node not in self.gnodes:
            self.prev = self.node
        first = nid not in self.entries
        self.entries[nid] = self.entries.get(nid, 0) + 1
        self.node = nid
        self.node_turns[nid] = 0
        self._event(event="node_enter", node=nid)
        ntype = node.get("node_type") or "conversation"
        if ntype == "end_call":
            fn = _fn(node.get("terminal"))
            props = (fn.get("parameters") or {}).get("properties") or {}
            if (props.get("message_mode") or {}).get("default") == "custom":
                self.say(self.subst((props.get("spoken_message") or {}).get("default")))
            self.ended = True
            return
        if ntype == "code":
            for t in node.get("pre_actions") or []:
                if _fn(t).get("handler") == CODE:
                    self.run_snippet(t, "code", nid)
                    self.vars[f"{nid}_success_count"] = int(self.vars.get(f"{nid}_success_count") or 0) + 1
        elif ntype == "api_request":
            for t in node.get("pre_actions") or []:
                if _fn(t).get("handler") == API:
                    self.exec_api(t, {}, nid, "pre_actions")
        if node.get("passthrough"):
            on_enter = node.get("on_enter") or {}
            if on_enter.get("type") == "tts":
                self.say(self.subst(on_enter.get("text")))
            target = self.route(None) or node.get("passthrough_default_transition")
            if target:
                self.enter(target, hops + 1)
            return
        if ntype != "conversation":
            return
        on_enter = node.get("on_enter") or {"type": "llm"}
        if node.get("on_enter_first_entry_only") and not first:
            if from_user:
                self.dialogue(hops)
            return
        kind = on_enter.get("type", "llm")
        if kind == "tts":
            self.say(self.subst(on_enter.get("text")))
        elif kind == "llm":
            self.dialogue(hops, entry_prompt=on_enter.get("prompt"))
        elif from_user:
            self.dialogue(hops)

    def dialogue(self, hops: int = 0, entry_prompt: str | None = None):
        node = self._get(self.node) or {}
        task = node.get("task_message") or ""
        if node.get("loop_prompt") and self.node_turns.get(self.node, 0) >= 1:
            task = node["loop_prompt"]
        system = "\n\n".join(x for x in (self.subst(self.wf.get("system_prompt")), self.subst(node.get("role_message")),
                                          "## Current Task\n" + self.subst(task)) if x.strip())
        if entry_prompt:
            system += "\n\n## On entering this step\n" + self.subst(entry_prompt)
        specs, index = self.model_tools(node)
        for _ in range(6):
            msg = self.model.chat(system, self.history, specs)
            calls = msg.get("tool_calls") or []
            text = (msg.get("content") or "").strip()
            if not calls:
                self.say(text)
                return
            if text:
                self.say(text)
            self.history.append({"role": "assistant", "content": text or None, "tool_calls": calls})
            moved = None
            for c in calls:
                name = (c.get("function") or {}).get("name")
                try:
                    args = json.loads((c.get("function") or {}).get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                kind, obj = index.get(name, (None, None))
                if kind == "end":
                    self._event(event="function_call_start", function=name, args={})
                    self.history.append({"role": "tool", "tool_call_id": c.get("id") or name, "content": '{"ok": true}'})
                    self.ended = True
                    return
                if kind == "transition":
                    self.history.append({"role": "tool", "tool_call_id": c.get("id") or name, "content": '{"ok": true}'})
                    moved = obj["target_node"]
                    continue
                if kind != "api":
                    self.history.append({"role": "tool", "tool_call_id": c.get("id") or name,
                                         "content": json.dumps({"error": f"The function `{name}` is not currently available."})})
                    continue
                env = self.exec_api(obj, args, self.node, "tools")
                self.history.append({"role": "tool", "tool_call_id": c.get("id") or name,
                                     "content": json.dumps({k: v for k, v in env.items() if k != "header"}, default=str)})
            target = moved or self.route(None)  # routing runs again after a tool result
            if target:
                self.enter(target, hops + 1)
                return

    # --- the call -----------------------------------------------------------------------------------------------------
    def start(self) -> str:
        self.spoken = []
        self.enter(self.wf.get("initial_node"))
        return " ".join(self.spoken)

    def user_says(self, text: str) -> str:
        self.spoken = []
        if self.ended:
            return ""
        last_agent = next((m["content"] for m in reversed(self.history) if m["role"] == "assistant" and m.get("content")), "")
        self.turn_log.append({"node": self.node, "vars": {k: v for k, v in self.vars.items() if isinstance(v, (str, int, float))},
                              "agent": last_agent, "user": text})
        self.history.append({"role": "user", "content": text})
        self.events.append({"role": "user", "message": text})
        node = self._get(self.node) or {}
        self.node_turns[self.node] = self.node_turns.get(self.node, 0) + 1
        schema = node.get("extraction_schema") or []
        if schema:
            found = self.model.extract(self._recent(), schema)
            for f in schema:
                v = found.get(f.get("name"))
                if v in (None, ""):
                    continue
                if f.get("enum_values") and str(v) not in [str(e) for e in f["enum_values"]]:
                    continue
                self.vars[f["name"]] = v
                self._event(event="variable_extracted", name=f["name"], value=v)
        cap = int(node.get("max_node_turns") or 10)
        if self.node_turns[self.node] > cap and node.get("max_turn_transition"):
            self.enter(node["max_turn_transition"], from_user=True)
            return " ".join(self.spoken)
        target = self.route(text)
        if target:
            self.enter(target, from_user=True)
        else:
            self.dialogue()
        return " ".join(self.spoken)

    def finish(self):
        """The call ended: run the end_of_call lifecycle tools (unresolved single placeholders are dropped)."""
        for t in self.wf.get("tools") or []:
            if "end_of_call" in ((t.get("config") or {}).get("alwaysRunsAt") or []) and _fn(t).get("handler") == API:
                self.exec_api(t, {}, "workflow", "workflow")
