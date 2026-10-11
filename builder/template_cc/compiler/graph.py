"""The fixed collections-call graph (DESIGN.md §4), filled from policy.json and the tool API.

Deterministic: the same policy and API always give the same workflow JSON. Every model-facing text goes through the
ZoneBook so patches can only touch unlocked zones. Code and hooks come from compiler/snippets.py.
"""

from __future__ import annotations

import re
import textwrap

from builder.template_cc.compiler import i18n as I18N
from builder.template_cc.compiler import prompts as P
from builder.template_cc.compiler import runtime as RT
from builder.template_cc.compiler import snippets as S
from builder.template_cc.compiler import tools as T
from builder.template_cc.policy_check import CANONICAL_NOTES
from builder.template_cc.zones import ZoneBook, esc

MIN_CONF = 0.6
FREE_TEXT_CONF = 0.3  # a spoken answer copied as said: a discarded value looks like "nothing said" and makes the agent ask again
ROUTE_INTENTS = ["none", "asks_robot", "asks_scam", "wants_human", "goodbye", "finished"]
DEFAULT_PRECEDENCE = ["wants_human", "asks_scam", "asks_robot", "finished", "goodbye"]
CLOSING_PREFIX = {"promise": "promise_confirmation", "paid": "paid_reply", "nv_failed": "failed_id",
                  "nr_deceased": "condolence"}
# F1: the identity step says id_say as it is (tts). After each check the hook builds that spoken line from the result (code, not model).
SPOKEN_ID_SAY = (
    "        pr = str(I['prompts'].get(str(vars.get('id_q') or q), str(vars.get('id_q') or q)))\n"
    "        w = str(d.get('what_to_do_next') or '')\n"
    "        if w.startswith(MSG['next']):\n"
    "            vars['id_say'] = 'Thank you. ' + pr\n"
    "        elif w.startswith(MSG['hint']):\n"
    "            vars['id_say'] = str(CFG['lines'].get('hint_retry') or ('Could you say that again? ' + pr))\n"
    "        elif w.startswith(MSG['unreadable']):\n"
    "            vars['id_say'] = 'Sorry, I could not read that. ' + pr\n"
    "        elif w.startswith(MSG['service_retry']):\n"
    "            vars['id_say'] = 'The check did not go through. ' + pr\n"
    "        elif w.startswith(MSG['retry']):\n"
    "            vars['id_say'] = 'That answer does not match. ' + pr\n"
    "        else:\n"
    "            vars['id_say'] = pr\n")
GENERIC_PREFIX = {"np_service_error": "I am sorry, I could not complete that because of a system problem.",
                  "nr_callback": "Thank you, we will call back then.",
                  "tr_restructuring": "I am connecting you with a colleague now.", "tr_human": "I am connecting you with a colleague now.",
                  "tr_support": "I am connecting you with our support team now.",
                  "nr_wrong_number": "Sorry for the trouble."}


def field_kind(api, tool: str, answer_field: str, field: str, fallback: str) -> str:
    """F1: the kind of a security field, read from the tool API's own words for that field (in the answer's description)."""
    desc = api.field_desc(tool, answer_field) or ""
    parts = re.split(r"(?=\bFor [A-Za-z_]\w*\s*:)", desc)
    mine = [x for x in parts if x.lower().startswith("for " + field.lower())]
    kind = RT.kind_from_description(field + " " + (mine[0] if mine else ""))  # a shared, unstructured text says nothing per field
    return kind if kind != "text" else fallback


# --- condition helpers -----------------------------------------------------------------------------------------------

def always(target: str, desc: str = "always") -> dict:
    return {"target_node": target, "description": desc, "condition": {"operator": "always"}}


def eq(target: str, desc: str, var: str, *values) -> dict:
    return {"target_node": target, "description": desc, "condition": {
        "logic": "or", "conditions": [{"variable": var, "operator": "equals", "operand": v} for v in values]}}


def any_of(target: str, desc: str, leaves: list) -> dict:
    return {"target_node": target, "description": desc, "condition": {"logic": "or", "conditions": leaves}}


def leaf(var, op, operand=None) -> dict:
    d = {"variable": var, "operator": op}
    if operand is not None:
        d["operand"] = operand
    return d


def bool_true(var) -> list:
    return [leaf(var, "equals", "true"), leaf(var, "equals", "True")]


def bool_false(var) -> list:
    return [leaf(var, "equals", "false"), leaf(var, "equals", "False")]


# --- runtime config --------------------------------------------------------------------------------------------------

def runtime_cfg(policy: dict, api) -> dict:
    tools = policy["tools"]
    lines = {k: P.line(policy, k) for k in (policy.get("lines") or {})}
    note_map = {k: v for k, v in (policy.get("note_map") or {}).items() if v}
    return {
        "holidays": sorted(policy.get("holidays") or []), "bands": policy["bands"],
        "attempt_plan": policy.get("attempt_plan") or [], "weekday_words": policy.get("weekday_words") or {},
        "outcome_map": policy.get("outcome_map") or {}, "note_map": note_map,
        "close_rule": policy.get("close_rule") or {"kind": "first_non_paid"},
        "product_types": policy.get("product_types") or {}, "account_fields": policy.get("account_fields") or {},
        "product_id_format": policy.get("product_id_format") or "p{n}",
        "max_products": int(policy.get("max_products") or 1), "multi": bool(policy.get("multi_product")),
        "notes": bool(policy.get("multi_product")) and bool(note_map),
        "callback": policy["callback"], "retries": policy.get("retries") or {},
        "identity": {"questions": policy["identity"]["questions"], "max_wrong": policy["identity"]["max_wrong"],
                     "swap_hint": policy["identity"].get("swap_hint", False),
                     "kinds": policy["identity"].get("kinds") or {}, "prompts": policy["identity"].get("prompts") or {}},
        "id_roles": api.identity_roles(), "id_fields": tools["verify_identity"],
        "cp_fields": {**tools["create_promise"]}, "cb_fields": tools["schedule_callback"],
        "close_fields": tools["close_call"], "list_field": api.account_list_field(), "lines": lines,
        "stop_limit": int((policy.get("ask_limits") or {}).get("stop_talking", 2)),
        "default_key": "nr_unclear",
    }


def verified(policy: dict, *prefixes: str) -> bool:
    """A policy parameter counts only when its _source quote exists and was verified against the brief."""
    src = policy.get("_source") or {}
    return any(k.startswith(pre) and v and not str(v).startswith("UNVERIFIED") for k, v in src.items() for pre in prefixes)


class Graph:
    def __init__(self, policy: dict, api, close_form: str = "lifecycle", features=frozenset()):
        self.p, self.api, self.close_form = policy, api, close_form
        self.f = frozenset(features)
        self.cfg = runtime_cfg(policy, api)
        self.lang = str(policy.get("language") or "en").lower()[:2]
        if self.lang in I18N.SUPPORTED:
            self.cfg["lang"] = self.lang
            self.cfg["currency"] = str(policy.get("currency") or "").strip()
        if "f1_id_complete" in self.f:
            ident = self.cfg["identity"]
            ans = policy["tools"]["verify_identity"]["answer"]
            ident["kinds"] = {q: field_kind(api, "verify_identity", ans, q, ident["kinds"].get(q, "text"))
                              for q in ident["questions"]}
        self.book = ZoneBook()
        self.nodes: dict[str, dict] = {}
        self.globals: dict[str, dict] = {}
        self.multi = bool(policy.get("multi_product"))
        self.nmax = max(1, int(policy.get("max_products") or 1)) if self.multi else 1
        om = policy.get("outcome_map") or {}
        self.has = lambda key: bool(om.get(key))
        self.queues = {k: v for k, v in (policy.get("queue_map") or {}).items() if v}
        self.limits = policy.get("ask_limits") or {}
        self.non_conv: list[str] = []
        if "f13_verifier" in self.f and "f8_fixed_lines" in self.f and P.line(policy, "hardship_sympathy"):
            self.cfg["required_lines"] = {"np_hardship": "hardship_sympathy"}
        if "f11_promise_typed" in self.f:
            self.cfg["vague_limit"] = int(self.limits.get("vague_date", 1))
            self.cfg["vague_offer"] = str(policy.get("vague_date_offer") or "deadline")
        routes = policy.get("routes") or {}  # F3: a request for a person is honoured before identity is verified
        self.human_first = ("f3_human_first" in self.f and "human" in self.queues
                            and routes.get("human_before_verify", True) is not False)

    # --- zones -----------------------------------------------------------------------------------------------------
    def _ptr(self, nid: str, is_global: bool = False) -> str:
        return f"/{'global_nodes' if is_global else 'nodes'}/{esc(nid)}"

    def task_zones(self, stage: str, nid: str, task: str, facts: str = "", is_global: bool = False,
                   brief_stage=None):
        """task_message = task + facts + brief rules (locked) + rules + examples (unlocked)."""
        b = self.book
        parts = [{"zone": b.add(f"node:{stage}/task", task, True, purpose="the compiled job of this step")}]
        if "f12_slot_summary" in self.f and nid in self.SUMMARY_PREFIX:
            parts.insert(0, {"zone": b.add("node:state/summary", "State:\n{{slot_summary}}", True, purpose="what is known and what is missing")})
        if facts:
            parts.append({"zone": b.add(f"node:{stage}/facts", facts, True, purpose="computed facts"), "title": "Facts"})
        br = P.brief_rules(self.p, brief_stage or stage)
        parts.append({"zone": b.add(f"node:{stage}/brief_rules", br, True, purpose="rules from the brief"), "title": "Brief rules"})
        parts.append({"zone": b.add(f"node:{stage}/rules", "", False, P.BUDGET["rules"], "extra if/then rules"), "title": "Rules"})
        parts.append({"zone": b.add(f"node:{stage}/examples", "", False, P.BUDGET["examples"], "sample phrasing"), "title": "Examples"})
        b.target(self._ptr(nid, is_global) + "/task_message", "sections", parts)

    TR_SAY = ("Say the following to the customer in natural, polite Turkish, using the formal siz form. Keep every number, amount, date, time, name and "
              "code exactly as given, add nothing, leave nothing out, and say nothing else:\n")

    def _tr_llm(self) -> bool:
        return "f35_tr_llm_lines" in self.f and str(self.p.get("language", "")).lower().startswith("tr")

    def tts(self, nid: str, text: str, is_global: bool = False) -> dict:
        if self._tr_llm():   # f35: the fixed English line becomes an LLM-spoken Turkish step
            self.book.add(f"tts:{nid}", self.TR_SAY + text, True, purpose="fixed spoken line, spoken in Turkish by the model (f35)")
            self.book.target(self._ptr(nid, is_global) + "/on_enter/prompt", "text", [{"zone": f"tts:{nid}"}])
            return {"type": "llm", "prompt": self.TR_SAY + text}
        self.book.add(f"tts:{nid}", text, True, purpose="fixed spoken line")
        self.book.target(self._ptr(nid, is_global) + "/on_enter/text", "text", [{"zone": f"tts:{nid}"}])
        return {"type": "tts", "text": text}

    def extraction(self, stage: str, nid: str, fields: list[tuple[str, list[str]]], is_global: bool = False,
                   descs: dict | None = None, budgets: dict | None = None) -> list:
        out = []
        for i, (name, values) in enumerate(fields):
            text = (descs or {}).get(name) or P.EXTRACT[name]
            z = self.book.add(f"extract:{stage}/{name}/description", text, False,
                              max((budgets or {}).get(name, 0), P.BUDGET["extract"]),
                              "extraction field description (enum values locked)")
            self.book.target(self._ptr(nid, is_global) + f"/extraction_schema/{i}/description", "text", [{"zone": z}])
            item = {"name": name, "type": "string"}
            if values is not None:  # None = free text (a spoken answer)
                item["enum_values"] = values
            out.append({**item, "description": text, "min_confidence": MIN_CONF if values is not None else FREE_TEXT_CONF})
        return out

    def intent(self, zid: str, ptrs: list[str], text: str):
        self.book.add(zid, text, False, P.BUDGET["intent"], "intent description read by the routing model")
        for ptr in ptrs:
            self.book.target(ptr, "text", [{"zone": zid}])

    def tool_zones(self, nid: str, idx: int, tool: dict, msgs: dict | None):
        name = tool["function"]["name"]
        z = self.book.add(f"tool:{name}/description", tool["function"]["description"], False, P.BUDGET["tool"],
                          "model-facing tool description")
        base = self._ptr(nid) + f"/tools/{idx}"
        self.book.target(base + "/function/description", "text", [{"zone": z}])
        if msgs:
            parts = []
            for k, v in msgs.items():
                parts.append({"zone": self.book.add(f"hook:{name}/msg/{k}", v, False, P.BUDGET["hook"], "what_to_do_next text"),
                              "key": k})
            self.book.target(base + "/post_process_code", "hook_msgs", parts)

    SUMMARY_PREFIX = ("identity", "negotiate", "negotiate_resume", "promise_confirm", "hardship")
    SUMMARY_NODES = ("setup", "id_check", "facts", "promise_check", "after_promise", "side_mark", "hardship_mark")

    # --- node constructors ------------------------------------------------------------------------------------------
    def conv(self, nid: str, *, on_enter=None, transitions=(), tools=(), extraction=(), max_turns=None, turn_target=None,
             first_only=False, passthrough_to=None, is_global=False, **extra) -> dict:
        n = {"name": nid, "node_type": "conversation", "task_message": "", "transitions": list(transitions)}
        if self._tr_llm() and isinstance(on_enter, dict) and on_enter.get("type") == "tts":   # a tts dict built without self.tts()
            on_enter = {"type": "llm", "prompt": self.TR_SAY + on_enter.get("text", "")}
        if on_enter is not None:
            n["on_enter"] = on_enter
        if first_only:
            n["on_enter_first_entry_only"] = True
        if tools:
            n["tools"] = list(tools)
        if extraction:
            n["extraction_schema"] = list(extraction)
        if max_turns:
            n["max_node_turns"] = max_turns
            n["max_turn_transition"] = turn_target
        if passthrough_to:
            n["passthrough"] = True
            n["passthrough_default_transition"] = passthrough_to
        n.update(extra)
        (self.globals if is_global else self.nodes)[nid] = n
        return n

    def code(self, nid: str, body: str, transitions: list, uses=(), fallback="pass", on_fail: str = "wrap") -> dict:
        trs = list(transitions)
        if "f12_slot_summary" in self.f and nid in self.SUMMARY_NODES:
            body = textwrap.dedent(body).strip("\n") + "\nvars['slot_summary'] = slot_summary(vars, CFG)"
            uses = tuple(uses) + ("slot_summary",)
            fallback = textwrap.dedent(fallback).strip("\n") + "\nvars['slot_summary'] = str(vars.get('slot_summary') or '')"
        if nid not in ("setup", "wrap", "wrap_close"):  # the sandbox can be unreachable: never loop, close the call
            trs.insert(0, any_of(on_fail, "the code step failed", [leaf(f"{nid}_failure_count", "greater_than", 0)]))
        default = trs[-1]["target_node"]
        n = {"name": nid, "node_type": "code", "passthrough": True, "passthrough_default_transition": default,
             "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
             "pre_actions": [{"type": "function", "function": {
                 "name": "code_exec", "handler": "__function__:code_exec",
                 "parameters": {"type": "object", "properties": {
                     "code": {"type": "string", "default": S.build(body, self.cfg, uses, fallback)},
                     "timeout_ms": {"type": "number", "default": 10000}}}}}],
             "transitions": trs}
        self.nodes[nid] = n
        self.non_conv.append(nid)
        return n

    def api_node(self, nid: str, tool: dict, transitions: list, response_mapping=None) -> dict:
        trs = list(transitions)
        n = {"name": nid, "node_type": "api_request", "passthrough": True,
             "passthrough_default_transition": trs[-1]["target_node"], "on_enter": {"type": "silent"},
             "disable_inactivity_monitor": True, "pre_actions": [tool], "transitions": trs}
        if response_mapping:
            n["response_mapping"] = response_mapping
        self.nodes[nid] = n
        self.non_conv.append(nid)
        return n

    def end_node(self, nid: str = "end"):
        self.nodes[nid] = {"name": nid, "node_type": "end_call", "on_enter": {"type": "silent"},
                           "disable_inactivity_monitor": True, "transitions": [],
                           "terminal": {"type": "function", "function": {
                               "name": "call_finished", "handler": "__function__:end_call",
                               "parameters": {"type": "object", "properties": {
                                   "message_mode": {"type": "string", "default": "custom"},
                                   "spoken_message": {"type": "string", "default": "{{closing_line}}"}}}}}}
        self.non_conv.append(nid)

    # --- hooks ------------------------------------------------------------------------------------------------------
    def hook(self, body: str, uses=(), msgs=None) -> str:
        return S.build(body, self.cfg, uses, "pass", msgs)

    # --- the graph --------------------------------------------------------------------------------------------------
    def build(self) -> tuple[dict, ZoneBook]:
        self._setup()
        self._opening()
        self._third_party()
        self._identity()
        self._account_and_facts()
        self._negotiation()
        self._transfers()
        self._marks()
        self._wrap_and_close()
        self._globals()
        self._finish_routes()
        if "f7_router" in self.f:
            self._router()
            if "id_resume" in self.nodes:
                for nid in ("identity", "identity_explain"):
                    if nid in self.nodes:
                        # a turn with nothing to act on resumes the step (F19: the second in a row after the explanation is a refusal)
                        self.nodes[nid]["transitions"].append(always("id_resume"))
        if "f20_once_flags" in self.f and "human_gate" in self.nodes:  # every way into the help offer goes through the gate
            for coll in (self.nodes, self.globals):
                for nid, node in coll.items():
                    if nid == "human_gate":
                        continue
                    for t in node.get("transitions") or []:
                        if t.get("target_node") == "human_offer":
                            t["target_node"] = "human_gate"
            if "human_offer" in self.nodes and "human_gate" in self.non_conv:
                pass
        wf = {"initial_node": "start", "system_prompt": "", "nodes": self.nodes, "global_nodes": self.globals,
              "test_variables": self._test_variables()}
        if self.close_form == "lifecycle":
            wf["tools"] = [self._close_tool(lifecycle=True)]
        self._system_prompt()
        return wf, self.book

    def _test_variables(self) -> dict:
        out = {}
        for v in self.p.get("call_variables") or []:
            out[v] = {"customer_id": "c_test", "now": "2031-03-05T10:00"}.get(v, "Sample")
        return out

    def _system_prompt(self):
        b = self.book
        parts = [{"zone": b.add("system/core", P.system_core(self.p), True, purpose="identity, language, tone")},
                 {"zone": b.add("system/forbidden", P.system_forbidden(self.p, "f31_forbidden_products" in self.f), True, purpose="words forbidden before verification")},
                 {"zone": b.add("system/honesty", P.system_honesty(self.p, "f7_router" in self.f, "f20_once_flags" in self.f), True, purpose="honesty and tool discipline")},
                 {"zone": b.add("system/brief_rules", P.brief_rules(self.p, "global"), True, purpose="global brief rules"),
                  "title": "Brief rules"},
                 {"zone": b.add("system/global_rules", "", False, P.BUDGET["global_rules"], "extra global rules"),
                  "title": "Rules"}]
        b.target("/system_prompt", "sections", parts)

    # setup ------------------------------------------------------------------------------------------------------------
    def _setup(self):
        body = """
        now = parse_now(vars.get('now'))
        today = now.date()
        hol = hol_set(CFG)
        cb = CFG['callback']
        days = [today] if cb.get('same_day') else []
        days.append(next_bd_after(today, hol))
        parts = []
        allowed = []
        for d in days:
            start = cb['start']
            if d == today:
                t = now + datetime.timedelta(minutes=1)
                later = '%02d:%02d' % (t.hour, t.minute)
                start = later if later > start else start
            if start <= cb['end']:
                rel = 'today' if d == today else ('tomorrow' if d == today + datetime.timedelta(days=1) else 'the next business day')
                parts.append(rel + ', ' + spoken_full(d) + ', from ' + start + ' to ' + cb['end'])
                allowed.append(dict(date=d.isoformat(), start=start, end=cb['end']))
        head = 'Today is ' + spoken_full(today) + ', ' + now.strftime('%H:%M') + '. '
        vars['cb_window_text'] = head + (('Allowed callback times: ' + '; or '.join(parts) + '. Never outside these times. Resolve words like today and tomorrow with these dates.') if parts else 'No callback time is possible now.')
        jput(vars, 'cb_allowed', allowed)
        vars['today_iso'] = today.isoformat()
        vars['today_spoken'] = spoken(today)
        vars['phase'] = 'pre'
        vars['end_key'] = CFG['default_key']
        vars['transfer_key'] = 'none'
        jput(vars, 'pstate', dict())
        jput(vars, 'pids', [])
        jput(vars, 'products', [])
        jput(vars, 'id_passed', [])
        vars['id_state'] = 'pending'
        vars['id_wrong'] = '0'
ID_INIT        vars['cb_state'] = 'none'
        vars['neg_state'] = 'open'
        vars['facts_state'] = 'pending'
        vars['stop_n'] = '0'
        vars['side'] = 'none'
        vars['side_product'] = ''
        vars['hs_choice'] = 'none'
        vars['human_choice'] = 'none'
        vars['notes_state'] = 'done'
        vars['after_notes'] = 'close'
        jput(vars, 'note_queue', [])
        vars['note_pid'] = ''
        vars['note_reason'] = ''
        vars['facts_text'] = ''
        vars['debt_statement'] = ''
        vars['first_ask_line'] = CFG['lines'].get('first_ask', '')
        vars['closing_line'] = CFG['lines'].get('goodbye', 'Goodbye.')
        refresh_close(vars, CFG)
        """
        fallback = """
        vars['cb_window_text'] = 'No callback time is possible now.'
        vars['phase'] = 'pre'
        vars['end_key'] = 'nr_unclear'
        vars['transfer_key'] = 'none'
        vars['id_state'] = 'pending'
        vars['neg_state'] = 'open'
        vars['facts_state'] = 'pending'
        vars['side'] = 'none'
        vars['notes_state'] = 'done'
        vars['after_notes'] = 'close'
        vars['debt_statement'] = ''
        vars['first_ask_line'] = ''
        vars['facts_text'] = ''
        vars['closing_line'] = 'Goodbye.'
        """
        # The opening is spoken first (call variables only, no code): a code step before the first line makes the
        # first chat turn wait on the code sandbox (seen at 37-41 s, and 504s from the chat gateway). setup runs
        # after the customer's first reply and routes on who answered.
        if "f1_id_complete" in self.f:
            scam_say = ("        if str(vars.get('scam_asked')) == 'yes' and CFG['lines'].get('scam'):\n"
                        "            vars['id_say'] = str(CFG['lines']['scam']) + ' ' + str(vars['id_say'])\n"
                        if "f29_scam_open" in self.f and P.line(self.p, "scam") and P.line(self.p, "ai") else "")
            body = body.replace("ID_INIT", """        vars['id_q'] = CFG['identity']['questions'][0]
        vars['id_say'] = CFG['identity']['prompts'].get(vars['id_q'], vars['id_q'])
        if str(vars.get('ai_asked')) == 'yes' and CFG['lines'].get('ai'):
            vars['id_say'] = str(CFG['lines']['ai']) + ' ' + str(vars['id_say'])
SCAM_SAY
        vars['id_partial'] = ''
        vars['id_answer_text'] = ''
        vars['id_send_field'] = ''
        vars['id_send_value'] = ''
        vars['id_route'] = ''
        vars['id_intent'] = 'other'
        vars['id_inc'] = '0'
        vars['id_declines'] = '0'
        vars['id_q_pending'] = '0'
        vars['aside_line'] = ''
        vars['aside_open'] = ''
        vars['resume_step'] = 'identity'
""").replace("SCAM_SAY\n", scam_say)
        elif "f2_question_not_refusal" in self.f:
            body = body.replace("ID_INIT", """        vars['id_intent'] = 'other'
        vars['id_declines'] = '0'
        vars['id_q_pending'] = '0'
""")
        else:
            body = body.replace("ID_INIT", "")
        extra = ""
        if "f11_promise_typed" in self.f and self.multi:
            body = body.replace("vars['side_product'] = ''", "vars['side_product'] = 'none'")
        if self.f & {"f4_discount", "f11_promise_typed", "f8_fixed_lines"}:
            extra += "        vars['say_line'] = ''\n"
        if "f11_promise_typed" in self.f:
            extra += ("        vars['offer_intent'] = 'none'\n        vars['offer_amount_text'] = ''\n"
                      "        vars['offer_date_text'] = ''\n        vars['rb_answer'] = 'other'\n")
        if extra:
            body = body.replace("        vars['id_wrong'] = '0'\n", "        vars['id_wrong'] = '0'\n" + extra, 1)
        second = int(self.limits.get("right_party", 2)) >= 2
        routes = self._who_transitions("who1", "opening_ask2" if second else "mark_nr_unclear")
        self.code("setup", body, routes + [always("mark_nr_unclear", "nobody confirmed")],
                  uses=("parse_now", "hol_set", "next_bd_after", "spoken_full", "spoken", "jput", "refresh_close"),
                  fallback=fallback)

    # opening ----------------------------------------------------------------------------------------------------------
    def _who_transitions(self, var: str, unclear_target: str) -> list:
        out = [eq("identity", "the customer confirmed", var, "customer")]
        out.append(eq("mark_nr_deceased", "the customer has died", var, "customer_died"))
        out.append(eq("mark_nr_wrong_number", "not the customer's number", var, "not_their_number"))
        out.append(eq("third_party", "someone else answered", var, "someone_else"))
        out.append(eq(unclear_target, "no clear confirmation", var, "unclear"))
        return out

    def _opening(self):
        # A silent passthrough first step with no code: the opening must be entered through a transition, or the
        # chat route skips its spoken line (seen on the platform), and no code may run before the first line.
        self.nodes["start"] = {"name": "start", "node_type": "conversation", "task_message": "No tool call is allowed in this turn.",
                               "on_enter": {"type": "silent"}, "passthrough": True,
                               "passthrough_default_transition": "opening", "transitions": [always("opening")]}
        opening = " ".join(x for x in (P.line(self.p, "opening"), P.line(self.p, "right_party_question")) if x)
        second = int(self.limits.get("right_party", 2)) >= 2
        who = ["customer", "someone_else", "not_their_number", "customer_died", "unclear"]
        hf = self.human_first
        # F1: the identity questions are spoken by the system, so a robot question in the first reply is answered with the brief's line
        ai_ask = "f1_id_complete" in self.f and bool(P.line(self.p, "ai"))
        # F18: a first reply that gets no decision (extraction did not fire: a death, a robot question, chatter) is not left to a
        # free model turn: the robot answer is kept as the side line and the second ask is the policy's fixed line
        scam_ask = "f29_scam_open" in self.f and ai_ask and bool(P.line(self.p, "scam"))  # F29 (opt-in): a scam question in the first reply gets the brief's line too
        f18_open = ("f18_side_resume" in self.f and "f18_opening_fallback" in self.f and "f1_id_complete" in self.f and second
                    and bool(P.line(self.p, "ask_again")))
        self.conv("opening", on_enter=self.tts("opening", opening), first_only=True,
                  extraction=self.extraction("opening", "opening", [("who1", who)] + ([("who_wants_human", ["yes", "no"])] if hf else [])
                                             + ([("ai_asked", ["yes", "no"])] if ai_ask else [])
                                             + ([("scam_asked", ["yes", "no"])] if scam_ask else [])),
                  transitions=([eq("human_offer", "asks for a person", "who_wants_human", "yes")] if hf else [])
                  + [any_of("setup", "the person answered", [leaf("who1", "is_set")])]
                  + ([always("opening_resume")] if f18_open else []),
                  max_turns=4, turn_target="setup")
        if f18_open:
            self.code("opening_resume", "vars['aside_open'] = str(CFG['lines'].get('ai') or '') if str(vars.get('ai_asked')) == 'yes' else ''\n"
                      + ("if str(vars.get('scam_asked')) == 'yes' and CFG['lines'].get('scam'):\n"
                         "    vars['aside_open'] = (vars['aside_open'] + ' ' + str(CFG['lines']['scam'])).strip()\n" if scam_ask else "")
                      + "vars['ai_asked'] = 'no'\n" + ("vars['scam_asked'] = 'no'\n" if scam_ask else ""), [always("opening_ask2")], fallback="vars['aside_open'] = ''")
        self.task_zones("opening", "opening", P.opening_task(self.p))
        if second:
            self.conv("opening_ask2", on_enter=self.tts("opening_ask2", "{{aside_open}} " + P.line(self.p, "ask_again")) if f18_open else {"type": "llm"},
                      extraction=[{"name": "who2", "type": "string", "enum_values": who, "description": P.EXTRACT["who1"],
                                   "min_confidence": MIN_CONF}]
                      + ([{"name": "who_wants_human", "type": "string", "enum_values": ["yes", "no"],
                           "description": P.EXTRACT["who_wants_human"], "min_confidence": MIN_CONF}] if hf else []),
                      transitions=([eq("human_offer", "asks for a person", "who_wants_human", "yes")] if hf else [])
                      + self._who_transitions("who2", "mark_nr_unclear"), max_turns=3, turn_target="mark_nr_unclear")
            self.book.target("/nodes/opening_ask2/extraction_schema/0/description", "text",
                             [{"zone": "extract:opening/who1/description"}])
            if hf:
                self.book.target("/nodes/opening_ask2/extraction_schema/1/description", "text",
                                 [{"zone": "extract:opening/who_wants_human/description"}])
            self.task_zones("opening_ask2", "opening_ask2", P.opening_ask2_task(self.p), brief_stage="opening")

    # third party ------------------------------------------------------------------------------------------------------
    def _third_party(self):
        f = self.p["tools"]["schedule_callback"]
        if f.get("mode") == "datetime":
            body = {f["datetime"]: T.model_leaf(self.api, "schedule_callback", f["datetime"], "Day and time of the callback as YYYY-MM-DDTHH:MM, inside the allowed times")}
            hint = f"{f['datetime']} as YYYY-MM-DDTHH:MM"
        else:
            body = {f["date"]: T.model_leaf(self.api, "schedule_callback", f["date"], "Day of the callback as YYYY-MM-DD, inside the allowed times"),
                    f["time"]: T.model_leaf(self.api, "schedule_callback", f["time"], "Time of the callback as HH:MM (24 hour), inside the allowed times")}
            hint = f"{f['date']} as YYYY-MM-DD and {f['time']} as HH:MM"
        pre = self.hook("""
        b = config.get('body') or dict()
        F = CFG['cb_fields']
        today = parse_now(vars.get('now')).date()
        if F.get('mode') == 'datetime':
            raw = str(b.get(F['datetime']) or '').strip()
            bits = re.split(r'[T ]', raw, 1)
            d = to_iso(bits[0], today) or to_iso(raw, today)
            m = re.search(r'\\d{1,2}[:.]\\d{2}\\s*(?:am|pm)?|\\d{1,2}\\s*(?:am|pm)', raw.lower())
            t = to_hhmm(m.group(0)) if m else (to_hhmm(bits[1]) if len(bits) > 1 else '')
            if d and t:
                b[F['datetime']] = d + 'T' + t
        else:
            d = to_iso(b.get(F['date']), today)
            t = to_hhmm(b.get(F['time']))
            if d:
                b[F['date']] = d
            if t:
                b[F['time']] = t
        config['body'] = b
        """, uses=("parse_now", "to_iso", "to_hhmm"))
        msgs = {"booked": "The callback is booked. Thank the person briefly and say goodbye.",
                "retry": "The callback was NOT booked. Do not say it was booked. CALL schedule_callback once more with the same values.",
                "failed": "The callback could not be booked. Do not say it was booked. Thank the person and say goodbye."}
        post = self.hook("""
        result.pop('header', None)
        d = result.get('data') if isinstance(result.get('data'), dict) else dict()
        if truthy(d.get('ok')):
            vars['cb_state'] = 'booked'
            vars['end_key'] = 'nr_callback'
            d['what_to_do_next'] = MSG['booked']
        else:
            n = int(num(vars.get('cb_fail'))) + 1
            vars['cb_fail'] = str(n)
            if n <= int(CFG['retries'].get('schedule_callback', 1)):
                d['what_to_do_next'] = MSG['retry']
            else:
                vars['cb_state'] = 'failed'
                vars['end_key'] = 'nr_no_callback'
                d['what_to_do_next'] = MSG['failed']
        result['data'] = d
        refresh_close(vars, CFG)
        """, uses=("truthy", "num", "refresh_close"), msgs=msgs)
        if self._tp_flags():
            return self._third_party_code(f, post, msgs)
        tool = T.api_tool(self.api, "schedule_callback", {**T.customer_leaf(self.api, "schedule_callback"), **body},
                          description="Book a callback for the customer. CALL it as soon as the person names a day and time inside the allowed callback times. Never call it for a time outside them.",
                          pre=pre, post=post)
        trs = [eq("mark_nr_callback", "the callback is booked", "cb_state", "booked"),
               eq("mark_nr_no_callback", "the callback could not be booked", "cb_state", "failed"),
               eq("mark_nr_wrong_number", "not the customer's number", "tp_state", "not_their_number"),
               eq("mark_nr_deceased", "the customer has died", "tp_state", "customer_died"),
               eq("mark_nr_no_callback", "no time inside the window", "tp_state", "gave_up")]
        self.conv("third_party", on_enter={"type": "llm"}, tools=[tool],
                  extraction=self.extraction("third_party", "third_party", [("tp_state", ["gave_up", "not_their_number", "customer_died", "none"])]),
                  transitions=trs, max_turns=8, turn_target="mark_nr_no_callback")
        self.tool_zones("third_party", 0, tool, msgs)
        self.task_zones("third_party", "third_party", P.third_party_task(self.p, hint))

    # F22 ----------------------------------------------------------------------------------------------------------
    def _tp_flags(self) -> bool:
        """F22 once its policy parameters are verified (banned words before verification, the callback window); else p6."""
        cb = self.p.get("callback") or {}
        return "f22_forbidden_disclosure" in self.f and bool(self.p.get("forbidden_before_verify")) and bool(cb.get("start") and cb.get("end"))

    def _third_party_code(self, f: dict, post: str, msgs: dict):
        """Third-party step in code (F22): every reply is a fixed line, and the time the person names is checked against the
        allowed times (a code-booked callback needs it) before schedule_callback runs. The model only extracts tp_state and the day and time the person named."""
        f22 = True
        lines = self.p.get("lines") or {}
        ask = (lines.get("third_party_ask") or {}).get("text") if isinstance(lines.get("third_party_ask"), dict) else lines.get("third_party_ask")
        self.cfg["tp"] = {"reask": int((self.p.get("ask_limits") or {}).get("callback_reask", 1)),
                          "banned": list(self.p.get("forbidden_before_verify") or []) if f22 else [],
                          "ask": ask or I18N.localize("Could you tell me when {first_name} {last_name} can be reached?", self.lang),
                          "fallback": I18N.localize("Could you tell me when they can be reached?", self.lang)}
        if f["mode"] == "datetime":
            body = {f["datetime"]: T.baked(self.api, "schedule_callback", f["datetime"], "{{cb_date}}T{{cb_time}}")}
        else:
            body = {f["date"]: T.baked(self.api, "schedule_callback", f["date"], "{{cb_date}}"),
                    f["time"]: T.baked(self.api, "schedule_callback", f["time"], "{{cb_time}}")}
        tool = T.api_tool(self.api, "schedule_callback", {**T.customer_leaf(self.api, "schedule_callback"), **body},
                          description="Book a callback for the customer.", post=post)
        self.api_node("tp_book", tool, [
            eq("mark_nr_callback", "the callback is booked", "cb_state", "booked"),
            eq("mark_nr_no_callback", "the callback could not be booked", "cb_state", "failed"),
            any_of("mark_nr_no_callback", "the call to the tool failed", [leaf("tp_book_failure_count", "greater_than", 0)]),
            always("tp_book")])
        self.tool_zones_hook("tp_book", tool, msgs)
        self.code("tp_init", """
        T = CFG['tp']
        line = T['ask'].replace('{first_name}', str(vars.get('first_name') or '')).replace('{last_name}', str(vars.get('last_name') or '')).replace('{title}', str(vars.get('title') or '')).strip()
        vars['tp_say'] = clean_line(line, T['banned'], T['fallback'])
        vars['tp_n'] = '0'
        vars['tp_m'] = '0'
        vars['cb_day_in'] = ''
        vars['cb_time_in'] = ''
        """, [always("third_party")], uses=("clean_line",), fallback="vars['tp_say'] = CFG['tp']['fallback']")
        route = "f7_router" in self.f
        body = """
        T = CFG['tp']
        now = parse_now(vars.get('now'))
        today = now.date()
        allowed = jget(vars, 'cb_allowed', [])
        dtxt, ttxt = str(vars.get('cb_day_in') or ''), str(vars.get('cb_time_in') or '')
        vars['cb_day_in'] = ''
        vars['cb_time_in'] = ''
        d, t, inside = cb_pick(dtxt, ttxt, allowed, today)
        window = cb_window_line(allowed, today)
        n, m = int(num(vars.get('tp_n'))), int(num(vars.get('tp_m')))
        if str(vars.get('turn_intent') or 'none') != 'none':
            vars['tp_route'] = 'router'
        elif inside:
            vars['cb_date'], vars['cb_time'] = d, t
            vars['tp_route'] = 'book'
        elif not allowed:
            vars['tp_route'] = 'giveup'
        elif d and t:
            n += 1
            vars['tp_n'] = str(n)
            vars['tp_route'] = 'giveup' if n > T['reask'] else 'ask'
            vars['tp_say'] = clean_line(window + ' Could you give me another time?', T['banned'], T['fallback'])
        else:
            m += 1
            vars['tp_m'] = str(m)
            vars['tp_route'] = 'giveup' if m > T['reask'] + 1 else 'ask'
            vars['tp_say'] = clean_line((window + ' ' if window else '') + 'Which day and time would suit?', T['banned'], T['fallback'])
        """
        trs = ([eq("route_third_party", "the customer said something the router handles", "tp_route", "router")] if route else []) + [
            eq("tp_book", "a time inside the allowed times", "tp_route", "book"),
            eq("mark_nr_no_callback", "no usable time", "tp_route", "giveup"),
            always("third_party")]
        self.code("tp_check", body, trs, uses=("parse_now", "jget", "num", "cb_pick", "cb_window_line", "clean_line"),
                  fallback="vars['tp_route'] = 'giveup'")
        # who reaches the step now goes through tp_init (the first line is fixed text)
        for nid, n in self.nodes.items():
            if nid in ("tp_init", "tp_check", "tp_book"):
                continue
            for tr in n.get("transitions") or []:
                if tr.get("target_node") == "third_party":
                    tr["target_node"] = "tp_init"
            if n.get("passthrough_default_transition") == "third_party":
                n["passthrough_default_transition"] = "tp_init"
        descs = {"tp_state": "gave_up = the person gave a second time outside the allowed times, or cannot or will not say when the customer can be "
                             "reached; not_their_number = not the customer's number; customer_died = the customer has died; gave_time = the person "
                             "named a day and/or a time for the call back; none = anything else.",
                 "cb_day_in": "The day the person offers for the call back, as said or as YYYY-MM-DD; empty when no day was named.",
                 "cb_time_in": "The time the person offers for the call back, as said or as HH:MM; empty when no time was named."}
        trs = [eq("mark_nr_wrong_number", "not the customer's number", "tp_state", "not_their_number"),
               eq("mark_nr_deceased", "the customer has died", "tp_state", "customer_died"),
               eq("mark_nr_no_callback", "no time inside the window", "tp_state", "gave_up"),
               eq("tp_check", "the person named a day or a time", "tp_state", "gave_time")]
        if f22:  # every other turn is answered by the fixed re-ask line, never by the model
            trs.append(eq("tp_check", "anything else", "tp_state", "none"))
        ext = self.extraction("third_party", "third_party", [("tp_state", ["gave_up", "not_their_number", "customer_died", "gave_time", "none"]),
                                                             ("cb_day_in", None), ("cb_time_in", None)], descs=descs)
        self.conv("third_party", on_enter={"type": "tts", "text": "{{tp_say}}"}, extraction=ext, transitions=trs, max_turns=8,
                  turn_target="mark_nr_no_callback")
        tr = self._tr_llm()   # f35: conv() turned the tts dict into an llm prompt
        self.book.add("tts:third_party", (self.TR_SAY if tr else "") + "{{tp_say}}", True, purpose="fixed spoken line")
        self.book.target(self._ptr("third_party") + ("/on_enter/prompt" if tr else "/on_enter/text"), "text", [{"zone": "tts:third_party"}])
        task = ("You are not speaking with the customer. Do not say why you are calling and give no account details, even if the person "
                "offers to pay or to answer questions for the customer. Never ask them security questions. Ask only when the customer can be "
                "reached; the callback itself is booked by the system.")
        self.task_zones("third_party", "third_party", task)

    def tool_zones_hook(self, nid: str, tool: dict, msgs: dict):
        """The what_to_do_next texts of a code-called tool stay patchable zones (the tool is not in a conversation node)."""
        name = tool["function"]["name"]
        parts = []
        for k, v in msgs.items():
            parts.append({"zone": self.book.add(f"hook:{name}/msg/{k}", v, False, P.BUDGET["hook"], "what_to_do_next text"), "key": k})
        self.book.target(self._ptr(nid) + "/pre_actions/0/post_process_code", "hook_msgs", parts)

    # identity ---------------------------------------------------------------------------------------------------------
    def _identity_tool(self):
        f = self.p["tools"]["verify_identity"]
        qs = self.p["identity"]["questions"]
        body = {**T.customer_leaf(self.api, "verify_identity"),
                f["field"]: T.model_leaf(self.api, "verify_identity", f["field"], "Which question the answer is for: " + ", ".join(qs), enum=qs),
                f["answer"]: T.model_leaf(self.api, "verify_identity", f["answer"], "The customer's answer. A date as YYYY-MM-DD (convert spoken words to digits), digits as digits")}
        pre = self.hook("""
        b = config.get('body') or dict()
        F = CFG['id_fields']
        q = str(b.get(F['field']) or '')
        kind = CFG['identity']['kinds'].get(q, 'text')
        today = parse_now(vars.get('now')).date()
        b[F['answer']] = norm_answer(kind, b.get(F['answer']), today)
        vars['id_last_field'] = q
        config['body'] = b
        """, uses=("parse_now", "norm_answer"))
        msgs = {"next": "Correct. Now ask the next question:",
                "passed": "All security questions are answered correctly. Do not say anything yet.",
                "retry": "That answer does not match. Ask the same question once more. Never say the correct answer.",
                "hint": "That answer is not counted as wrong.",
                "unreadable": "The answer could not be read. Ask the customer to say it again clearly. This is not counted as wrong.",
                "failed": "Too many wrong answers. The checks have failed. Do not ask any more questions and say nothing about the account.",
                "service_retry": "The check did not go through. Ask the customer for the same answer again.",
                "service_failed": "The check is not available. Apologise briefly."}
        post_body = """
        result.pop('header', None)
        d = result.get('data') if isinstance(result.get('data'), dict) else dict()
        R = CFG['id_roles']
        I = CFG['identity']
        qs = I['questions']
        passed = jget(vars, 'id_passed', [])
        q = str(vars.get('id_last_field') or '')
        status = str(result.get('status') or 'success').lower()
        code = int(num(result.get('status_code') or 200))
        okv = truthy(d.get('ok'))
        ver = truthy(d.get(R['verified'])) if R.get('verified') else okv
        hint = str(d.get(R['hint']) or '') if R.get('hint') else ''
        if status not in ('success', 'ok') or code >= 500:
            n = int(num(vars.get('id_fail'))) + 1
            vars['id_fail'] = str(n)
            if n <= int(CFG['retries'].get('verify_identity', 1)):
                vars['id_state'] = 'retry'
                d['what_to_do_next'] = MSG['service_retry']
            else:
                vars['id_state'] = 'error'
                vars['end_key'] = 'np_service_error'
                d['what_to_do_next'] = MSG['service_failed']
        elif ver:
            if q and q not in passed:
                passed.append(q)
            jput(vars, 'id_passed', passed)
            allv = truthy(d.get(R['all'])) if R.get('all') else False
            if allv or all(x in passed for x in qs):
                vars['id_state'] = 'passed'
                d['what_to_do_next'] = MSG['passed']
            else:
                nxt = [x for x in qs if x not in passed][0]
                vars['id_state'] = 'next'
                d['what_to_do_next'] = MSG['next'] + ' ' + str(I['prompts'].get(nxt, nxt)) + ' (field: ' + nxt + ')'
        elif hint and I.get('swap_hint'):
            vars['id_state'] = 'retry'
            d['what_to_do_next'] = MSG['hint'] + ' ' + CFG['lines'].get('hint_retry', '')
        elif not okv:
            vars['id_state'] = 'retry'
            d['what_to_do_next'] = MSG['unreadable']
        else:
            w = int(num(vars.get('id_wrong'))) + 1
            vars['id_wrong'] = str(w)
            left = d.get(R['left']) if R.get('left') else None
            left = int(num(left)) if left is not None and str(left) != '' else int(I['max_wrong']) - w
            if left <= 0 or w >= int(I['max_wrong']):
                vars['id_state'] = 'failed'
                vars['end_key'] = 'nv_failed'
                d['what_to_do_next'] = MSG['failed']
            else:
                vars['id_state'] = 'retry'
                d['what_to_do_next'] = MSG['retry']
        result['data'] = d
        refresh_close(vars, CFG)
        """
        aside_say = ("        if str(vars.get('id_state')) in ('next', 'retry'):\n"
                     "            vars['id_say'] = with_aside(vars, vars.get('id_say'))\n") \
            if "f18_side_resume" in self.f and "f7_router" in self.f else ""
        if "f1_id_complete" in self.f:  # the next line to say and the next question go to variables (no model-facing tool)
            post_body = post_body.replace("                vars['id_state'] = 'next'\n",
                                          "                vars['id_state'] = 'next'\n                vars['id_q'] = nxt\n")
            post_body = post_body.replace("        result['data'] = d\n",
                                          SPOKEN_ID_SAY + aside_say
                                          + ("        vars['slot_summary'] = slot_summary(vars, CFG)\n" if "f12_slot_summary" in self.f else "")
                                          + "        result['data'] = d\n")
        post_uses = ("truthy", "num", "jget", "jput", "refresh_close")
        if "f1_id_complete" in self.f and "f12_slot_summary" in self.f:
            post_uses += ("slot_summary",)
        if aside_say:
            post_uses += ("with_aside",)
        post = self.hook(post_body, uses=post_uses, msgs=msgs)
        if "f1_id_complete" in self.f:  # run by the engine from id_verify with the complete answer id_check hands over
            body = {**T.customer_leaf(self.api, "verify_identity"),
                    f["field"]: {"type": "string", "default": "{{id_send_field}}"},
                    f["answer"]: {"type": "string", "default": "{{id_send_value}}"}}
            pre = None
        tool = T.api_tool(self.api, "verify_identity", body, retries=0,
                          description="Check one security answer. CALL it every time the customer answers a security question, with that question's field.",
                          pre=pre, post=post)
        return tool, msgs

    def _id_state_transitions(self) -> list:
        roles = self.api.identity_roles()
        passed = [leaf("id_state", "equals", "passed")]
        if roles.get("all"):
            passed += bool_true(f"verify_identity_response.data.{roles['all']}")
        return [any_of("fetch_account", "the identity checks passed", passed),
                eq("mark_nv_failed", "the identity checks failed", "id_state", "failed"),
                eq("mark_np_service_error", "the identity service is not available", "id_state", "error")]

    def _identity_transitions(self, refusal_var: str, refusal_target: str) -> list:
        return self._id_state_transitions() + [
            eq(refusal_target, "the customer declines the questions", refusal_var, "declines", "asks_purpose")]

    def _id_say_note(self):  # (placeholder for readers: the spoken lines of the identity step are built in code, see SPOKEN_ID_SAY)
        return SPOKEN_ID_SAY

    def _id_refusal_count(self):
        """F1/F2: count refusals in code. A question back is answered once; only a repeat counts (F2)."""
        f1, f2 = "f1_id_complete" in self.f, "f2_question_not_refusal" in self.f
        limit = int(self.limits.get("id_decline", 1))
        # spoken text, said as it is by the identity step (tts): the brief's reason line, then the question again
        say_ask = "vars['id_say'] = (str(CFG['lines'].get('id_reason') or '') + ' ' + prompt).strip()\n" if f1 else ""
        say_explain = "vars['id_say'] = (str(CFG['lines'].get('id_reason') or '') + ' ' + prompt).strip()\n" if f1 else ""
        f18 = f1 and "f18_side_resume" in self.f
        uses_extra = ()
        f19 = "f19_refusal_rule" in self.f  # F19: a question back is a refusal like any other; the counter counts every one
        pend = "intent == 'asks_question' and not pending and int(num(vars.get('id_declines'))) == 0" if f2 and not f19 else "False"
        body = """
        intent = str(vars.get('id_intent') or '')
        pending = str(vars.get('id_q_pending')) == '1'
        q = str(vars.get('id_q') or '') or CFG['identity']['questions'][0]
        prompt = str(CFG['identity']['prompts'].get(q, q))
        vars['id_intent'] = 'other'
        vars['id_route'] = 'ask'
        if PEND:
            vars['id_q_pending'] = '1'
            SAY_ASK
        else:
            n = int(num(vars.get('id_declines'))) + 1
            vars['id_declines'] = str(n)
            vars['id_q_pending'] = '0'
            if n <= EXPLAIN_N:
                vars['id_route'] = 'explain'
                SAY_EXPLAIN
            else:
                vars['id_route'] = 'decline'
        """
        if f19:
            # id_decline = the number of re-asks after the FIRST refusal; the explanation is said once, on refusal 1; the call
            # closes (the outcome of the refusal path) on refusal number id_decline + 1 ("explain once, close after the second refusal" -> id_decline = 1)
            body = body.replace("""            if n <= EXPLAIN_N:
                vars['id_route'] = 'explain'
                SAY_EXPLAIN
            else:""", """            if n <= EXPLAIN_N:
                if n == 1 and said_once(vars, 'id_reason'):
                    vars['id_route'] = 'explain'
                    SAY_EXPLAIN
                else:
                    vars['id_say'] = prompt
            else:""")
            uses_extra = ("said_once",)
        body = body.replace("PEND", pend).replace("EXPLAIN_N", str(limit if f19 else min(limit, 1))).replace("SAY_ASK\n", say_ask or "pass\n") \
           .replace("SAY_EXPLAIN\n", say_explain or "pass\n")
        body = body.replace("SAY_ASK", say_ask.strip() or "pass").replace("SAY_EXPLAIN", say_explain.strip() or "pass")
        uses = ("num",) + (uses_extra if f19 else ())
        if f19:
            body = body.rstrip() + "\n        vars['id_fill'] = '0'\n"
        if f18:  # F18: a side answer that is waiting is said first; the step to resume is remembered
            body = body.rstrip() + ("\n        vars['resume_step'] = 'identity_explain' if vars['id_route'] == 'explain' else 'identity'\n"
                                    "        if vars['id_route'] in ('ask', 'explain'):\n"
                                    "            vars['id_say'] = with_aside(vars, vars.get('id_say'))\n")
            uses += ("with_aside",)
        trs = ([eq("identity_explain", "explain once", "id_route", "explain")] if limit >= 1 else []) + [
            eq("mark_nv_declined", "the customer keeps declining", "id_route", "decline"), always("identity")]
        self.code("id_refusal_count", body, trs, uses=uses, fallback="vars['id_route'] = 'decline'")

    def _identity_f1_f2(self, tool, msgs):
        """The identity step when F1 (complete answers verified by the engine) and/or F2 (a question is not a refusal) is on."""
        f1, f2, f3 = ("f1_id_complete" in self.f, "f2_question_not_refusal" in self.f, self.human_first)
        explain = int(self.limits.get("id_decline", 1)) >= 1
        vals = ["answer"] + (["asks_question", "refuses"] if f2 else ["declines", "asks_purpose"]) \
            + (["wants_human"] if f3 else []) + ["other"]
        descs = {"id_intent": P.id_intent_desc(f2, f3)}
        fields = [("id_intent", vals)] + ([("id_answer_text", None)] if f1 else [])
        refusal = ("refuses", "asks_question") if f2 else ("declines", "asks_purpose")
        trs = ([any_of("id_check", "the customer gave an answer", [leaf("id_intent", "equals", "answer"),
                                                                    leaf("id_answer_text", "not_equals", "")])] if f1
               else self._id_state_transitions())
        f18 = f1 and "f18_side_resume" in self.f and "f7_router" in self.f
        asides = [leaf("turn_intent", "equals", v) for v, k in (("asks_robot", "ai"), ("asks_scam", "scam")) if P.line(self.p, k)]
        f18 = f18 and bool(asides)  # no ai / scam line in the policy: nothing to say, nothing changes
        trs = ([eq("human_offer", "asks for a person", "id_intent", "wants_human")] if f3 else []) \
            + ([any_of("id_aside", "a robot or scam question: answer it, then go on with the step", asides)] if f18 else []) + trs \
            + [eq("id_refusal_count", "the customer declines or does not answer", "id_intent", *refusal)]
        tools = [] if f1 else [tool]
        self.conv("identity", on_enter=self.tts("identity", "{{id_say}}") if f1 else {"type": "llm"}, tools=tools,
                  extraction=self.extraction("identity", "identity", fields, descs=descs), transitions=trs,
                  max_turns=16, turn_target="mark_nv_declined")
        if not f1:
            self.tool_zones("identity", 0, tool, msgs)
        self.task_zones("identity", "identity", P.identity_task_f1(self.p, f2) if f1 else P.identity_task(self.p) + (
            " If the customer asks a question instead of answering, answer it briefly without the reason for the call, "
            "then ask the pending question again." if f2 else ""))
        if explain:
            ext = [{"name": n, "type": "string", **({"enum_values": v} if v is not None else {}),
                    "description": descs.get(n) or P.EXTRACT[n],
                    "min_confidence": MIN_CONF if v is not None else FREE_TEXT_CONF} for n, v in fields]
            self.conv("identity_explain", on_enter=self.tts("identity_explain", "{{id_say}}") if f1 else {"type": "llm"},
                      tools=tools, extraction=ext, transitions=list(trs), max_turns=12, turn_target="mark_nv_declined")
            for i, (n, _) in enumerate(fields):
                self.book.target(f"/nodes/identity_explain/extraction_schema/{i}/description", "text",
                                 [{"zone": f"extract:identity/{n}/description"}])
            if not f1:
                self.tool_zones("identity_explain", 0, tool, msgs)
            self.task_zones("identity_explain", "identity_explain",
                            P.identity_explain_task_f1(self.p) if f1 else P.identity_explain_task(self.p), brief_stage="identity")
        self._id_refusal_count()
        if f1:
            self._id_check_and_verify(tool, msgs)
        if f18 or (f1 and "f19_refusal_rule" in self.f and "f7_router" in self.f):
            self._id_side_nodes(refusal, aside=f18)

    def _id_check_and_verify(self, tool, msgs):
        body = """
        field = str(vars.get('id_q') or '') or CFG['identity']['questions'][0]
        kind = str(CFG['identity']['kinds'].get(field, 'text'))
        prompt = str(CFG['identity']['prompts'].get(field, field))
        alone = str(vars.get('id_answer_text') or '').strip()
        partial = str(vars.get('id_partial') or '').strip()
        today = parse_now(vars.get('now')).date()
        INTENT0vars['id_intent'] = 'other'
        vars['id_answer_text'] = ''
        vars['id_q_pending'] = '0'

        def attempt(text):
            if not text:
                return '', False
            if kind == 'digits':
                d = digit_string(text)
                return d, bool(d)
            v, ok = answer_from_speech(kind, text)
            if ok and not (kind in ('date', 'year', 'alnum_code') or kind.startswith('digits:')):
                v = norm_answer(kind, v, today)
            return v, ok

        value, complete = attempt(alone)
        if not complete and partial:
            value, complete = attempt((partial + ' ' + alone).strip())
        if not complete and kind == 'date' and alone and str(vars.get('id_send_field')) == field:
            year = str(vars.get('id_send_value') or '')[:4]
            if year.isdigit():
                value, complete = attempt(alone + ' ' + year)
        n = int(num(vars.get('id_inc')))
        REFUSE_ALONEif not complete and alone:
            n += 1
            if n >= 3:
                value = (digit_string(alone) or alone) if kind.startswith('digits') else norm_answer(kind, alone, today)
                complete = True
        if complete:
            RESET_STREAKvars['id_send_field'] = field
            vars['id_send_value'] = str(value)
            vars['id_last_field'] = field
            vars['id_partial'] = ''
            vars['id_inc'] = '0'
            vars['id_route'] = 'send'
        REFUSE_ROUTEelse:
            vars['id_route'] = 'ask'
            if not alone:
                vars['id_say'] = 'Sorry, I did not catch that. ' + prompt
            elif n == 1:
                vars['id_inc'] = '1'
                vars['id_partial'] = (partial + ' ' + alone).strip()
                vars['id_say'] = 'Thank you. Could you give me the rest of that, please?'
            else:
                vars['id_inc'] = str(n)
                vars['id_partial'] = ''
                vars['id_say'] = 'Let us try that once more. ' + prompt
        """
        uses = ("parse_now", "digit_string", "answer_from_speech", "norm_answer", "num")
        if "f21_spoken_answers" in self.f:  # F21: digit-by-digit years, spelling alphabets in codes
            body = body.replace("v, ok = answer_from_speech(kind, text)", "v, ok = answer_from_spoken(kind, text)")
            uses += ("answer_from_spoken",)
        if "f18_side_resume" in self.f and "f7_router" in self.f:
            uses += ("with_aside",)
        f19 = "f19_refusal_rule" in self.f
        f18 = "f18_side_resume" in self.f and "f7_router" in self.f
        refusing = "intent0 in ('refuses', 'asks_question', 'declines', 'asks_purpose')" if f19 else "False"
        body = body.replace("RESET_STREAK", "vars['id_declines'] = '0'\n            vars['id_fill'] = '0'\n            " if f19 else "")
        body = body.replace("INTENT0", "intent0 = str(vars.get('id_intent') or '')\n        " if f19 else "")
        body = body.replace("REFUSE_ALONE", ("if not complete and " + refusing + ":\n            alone = ''\n        ") if f19 else "")
        body = body.replace("REFUSE_ROUTE", ("elif " + refusing + ":\n            vars['id_route'] = 'refuse'\n        ") if f19 else "")
        trs = [eq("id_verify", "a complete answer to send", "id_route", "send")] \
            + ([eq("id_refusal_count", "a refusal, not an answer", "id_route", "refuse")] if f19 else []) + [always("identity")]
        if f18:
            body = body.rstrip() + ("\n        vars['resume_step'] = 'identity'\n"
                                    "        if vars['id_route'] == 'ask':\n"
                                    "            vars['id_say'] = with_aside(vars, vars.get('id_say'))\n")
        self.code("id_check", body, trs,
                  uses=uses, fallback="vars['id_route'] = 'ask'")
        self.api_node("id_verify", tool, self._id_state_transitions() + [always("identity")],
                      response_mapping=[{"path": "data.ok", "variable": "id_ok"}])
        parts = [{"zone": self.book.add(f"hook:verify_identity/msg/{k}", v, False, P.BUDGET["hook"], "what_to_do_next text"),
                  "key": k} for k, v in msgs.items()]
        self.book.target("/nodes/id_verify/pre_actions/0/post_process_code", "hook_msgs", parts)

    def _identity(self):
        tool, msgs = self._identity_tool()
        if (self.f & {"f1_id_complete", "f2_question_not_refusal"}) or self.human_first:
            return self._identity_f1_f2(tool, msgs)
        explain = int(self.limits.get("id_decline", 1)) >= 1
        values = ["declines", "asks_purpose", "answering"]
        self.conv("identity", on_enter={"type": "llm"}, tools=[tool],
                  extraction=self.extraction("identity", "identity", [("id_refusal", values)]),
                  transitions=self._identity_transitions("id_refusal", "identity_explain" if explain else "mark_nv_declined"),
                  max_turns=16, turn_target="mark_nv_declined")
        self.tool_zones("identity", 0, tool, msgs)
        self.task_zones("identity", "identity", P.identity_task(self.p))
        if explain:
            self.conv("identity_explain", on_enter={"type": "llm"}, tools=[tool],
                      extraction=[{"name": "id_refusal2", "type": "string", "enum_values": values,
                                   "description": P.EXTRACT["id_refusal"], "min_confidence": MIN_CONF}],
                      transitions=self._identity_transitions("id_refusal2", "mark_nv_declined"),
                      max_turns=12, turn_target="mark_nv_declined")
            self.book.target("/nodes/identity_explain/extraction_schema/0/description", "text",
                             [{"zone": "extract:identity/id_refusal/description"}])
            self.tool_zones("identity_explain", 0, tool, msgs)
            self.task_zones("identity_explain", "identity_explain", P.identity_explain_task(self.p), brief_stage="identity")

    # account + facts --------------------------------------------------------------------------------------------------
    def _account_and_facts(self):
        lf = self.api.account_list_field()
        retries = int((self.p.get("retries") or {}).get("get_account", 1))

        def fetch(nid, fail_target):
            tool = T.api_tool(self.api, "get_account", T.customer_leaf(self.api, "get_account"), retries=0,
                              description="Get the late products.")
            fails = [leaf(f"{nid}_failure_count", "greater_than", 0)] + bool_false(f"{nid}_response.data.ok") + \
                bool_false("get_account_response.data.ok")
            self.api_node(nid, tool, [any_of(fail_target, "the account could not be read", fails), always("facts")],
                          response_mapping=[{"path": f"data.{lf}", "variable": "products_raw"}])

        if retries >= 1:
            fetch("fetch_account", "fetch_account_retry")
            fetch("fetch_account_retry", "mark_np_service_error_hard")
        else:
            fetch("fetch_account", "mark_np_service_error_hard")
        n = self.nmax
        labels = "\n".join(f"vars['label_{i}'] = rows[{i - 1}]['label'] if len(rows) > {i - 1} else ''" for i in range(1, n + 1))
        body = """
        rows = product_rows(vars, CFG)
        if not rows:
            vars['facts_state'] = 'error'
            vars['end_key'] = 'np_service_error_hard'
        else:
            now = parse_now(vars.get('now'))
            today = now.date()
            L = CFG['lines']
            pids = [r['pid'] for r in rows]
            jput(vars, 'pids', pids)
            jput(vars, 'pstate', dict((p, 'open') for p in pids))
            jput(vars, 'products', [dict(pid=r['pid'], label=r['label'], past_due=r['past_due'], floor=r['floor'], deadline=r['deadline']ATT1) for r in rows])
            vars['product_count'] = str(len(rows))
            vars['phase'] = 'products'
            vars['end_key'] = 'np_refused'
            out = ['Today is ' + spoken_full(today) + ', time ' + now.strftime('%H:%M') + '.']
            out.append('Products, in this order (product_id in brackets):')
            say = []
            for r in rows:
                out.append(str(r['index']) + '. ' + r['label'] + ' [' + r['pid'] + ']: past due ' + money(r['past_due']) + ', ' + str(r['days']) + ' days late. Minimum amount: ' + money(r['floor']) + '. Latest promise date: ' + r['deadline_spoken'] + ' (' + r['deadline'] + '). Persuasion attempts allowed: ' + str(r['attempts']) + '.')
                for k, pr in enumerate(r['proposals']):
                    if pr['amount'] == 'full':
                        what = 'the full amount ' + money(r['past_due'])
                    elif pr['amount'] == 'floor':
                        what = 'the minimum amount ' + money(r['floor'])
                    else:
                        what = 'the full amount ' + money(r['past_due']) + ' (or the minimum amount ' + money(r['floor']) + ' if the customer already said the full amount is too much)'
                    out.append('   Attempt ' + str(k + 1) + ': propose ' + what + ' on ' + pr['spoken'] + ' (' + pr['date'] + ').')
                say.append('Your ' + r['label'] + ': ' + money(r['past_due']) + ' past due, ' + str(r['days']) + ' days late.')
            if CFG['cp_fields'].get('amount_unit') == 'cents':
                out.append('Say and send every amount in currency units exactly as written here.')
            out.append('Date table (use it; never compute dates yourself):')
            out.append(date_table(CFG, rows, today))
            out.append('Weekday words:')
            out.append(weekday_words(CFG, today))
            vars['facts_text'] = '\\n'.join(out)
            vars['debt_statement'] = (L.get('debt_intro', '') + ' ' + ' '.join(say)).strip()ASIDE_DEBT
            vars['first_ask_line'] = L.get('first_ask_multi', '') if len(rows) > 1 and L.get('first_ask_multi') else L.get('first_ask', '')
            LABELS
            neg_flag(vars)
            vars['facts_state'] = 'ok'
        refresh_close(vars, CFG)
        """.replace("LABELS", labels.replace("\n", "\n            "))
        fallback = """
        vars['facts_state'] = 'error'
        vars['end_key'] = 'np_service_error_hard'
        """
        aside_f = "f18_side_resume" in self.f and "f7_router" in self.f
        body = body.replace("ASIDE_DEBT", "\n            vars['debt_statement'] = with_aside(vars, vars['debt_statement'])" if aside_f else "")
        body = body.replace("ATT1", ", att1=(r['proposals'][0]['date'] if r['proposals'] else r['deadline'])" if "f11_promise_typed" in self.f else "")
        self.code("facts", body, [eq("mark_np_service_error_hard", "no products could be read", "facts_state", "error"),
                                  always("negotiate")],
                  uses=("product_rows", "parse_now", "jput", "spoken_full", "money", "date_table", "weekday_words",
                        "neg_flag", "refresh_close") + (("with_aside",) if aside_f else ()), fallback=fallback)

    def _id_side_nodes(self, refusal, aside=True):
        """F18: a robot / scam question inside the identity step. The policy's line is stored as the side answer, the turn's
        own content (an answer, a refusal) is still processed, and the step resumes with its pending question."""
        body = """
        t = str(vars.get('turn_intent') or '')
        line = str(CFG['lines'].get('ai' if t == 'asks_robot' else 'scam') or '')
        vars['turn_intent'] = 'none'
        if line:
            vars['aside_line'] = (str(vars.get('aside_line') or '') + ' ' + line).strip()
        intent = str(vars.get('id_intent') or '')
        if intent == 'answer' or str(vars.get('id_answer_text') or '').strip():
            vars['id_route'] = 'answer'
        elif intent in REFUSALS or (QUESTION_BACK and t == 'asks_scam'):
            vars['id_route'] = 'refuse'
        else:
            vars['id_route'] = 'resume'
        """.replace("REFUSALS", repr(tuple(refusal))).replace("QUESTION_BACK", "True" if "f19_refusal_rule" in self.f else "False")
        if aside:
            self.code("id_aside", body, [eq("id_check", "the turn holds an answer", "id_route", "answer"),
                                         eq("id_refusal_count", "the turn holds a refusal", "id_route", "refuse"), always("id_resume")],
                      fallback="vars['id_route'] = 'resume'")
        f19 = "f19_refusal_rule" in self.f
        self.code("id_resume", """
        field = str(vars.get('id_q') or '') or CFG['identity']['questions'][0]
        prompt = str(CFG['identity']['prompts'].get(field, field))
        vars['id_intent'] = 'other'
        vars['id_answer_text'] = ''
        vars['id_route'] = 'resume'
        vars['resume_step'] = 'identity_explain' if (EXPLAINED and int(num(vars.get('id_declines'))) >= 1) else 'identity'
        FILL
        if str(vars.get('aside_line') or '').strip():
            vars['id_say'] = with_aside(vars, prompt)
        else:
            vars['id_say'] = 'Sorry, I did not catch that. ' + prompt
        """.replace("EXPLAINED", str(int(self.limits.get("id_decline", 1)) >= 1)).replace("FILL", """vars['id_fill'] = str(int(num(vars.get('id_fill'))) + 1)
        if int(num(vars.get('id_fill'))) >= 2:
            vars['id_route'] = 'refuse'""" if f19 else "pass"),
                  [eq("id_refusal_count", "told why and still no answer", "id_route", "refuse"),
                   eq("identity_explain", "the step was the explanation", "resume_step", "identity_explain"), always("identity")],
                  uses=("with_aside", "num"), fallback="pass")

    # negotiation ------------------------------------------------------------------------------------------------------
    def _promise_tool(self):
        f = self.p["tools"]["create_promise"]
        fmt = self.p.get("product_id_format") or ""
        enum = [fmt.replace("{n}", str(i)) for i in range(1, self.nmax + 1)] if "{n}" in fmt else None
        cents = f.get("amount_unit") == "cents"
        body = {**T.customer_leaf(self.api, "create_promise"),
                f["product_id"]: T.model_leaf(self.api, "create_promise", f["product_id"], "product_id of the product, copied from the facts", enum=enum),
                f["amount"]: T.model_leaf(self.api, "create_promise", f["amount"], "Amount the customer confirmed, in currency units as written in the facts"),
                f["date"]: T.model_leaf(self.api, "create_promise", f["date"], "Date the customer confirmed, YYYY-MM-DD from the date table")}
        if cents and body[f["amount"]].get("type") == "integer":
            body[f["amount"]]["type"] = "number"
        pre_body = """
        b = config.get('body') or dict()
        F = CFG['cp_fields']
        rows = jget(vars, 'products', [])
        pids = [r['pid'] for r in rows]
        raw = str(b.get(F['product_id']) or '').strip()
        pid = raw
        if raw not in pids:
            m = re.search(r'(\\d+)\\s*$', raw)
            hit = [r['pid'] for r in rows if raw and raw.lower() in str(r['label']).lower()]
            if m and 1 <= int(m.group(1)) <= len(pids):
                pid = pids[int(m.group(1)) - 1]
            elif hit:
                pid = hit[0]
            elif open_pids(vars):
                pid = open_pids(vars)[0]
        b[F['product_id']] = pid
        vars['cp_pid'] = pid
        amt = num(b.get(F['amount']))
        #F9-AMOUNT
        if F.get('amount_unit') == 'cents':
            row = [r for r in rows if r['pid'] == pid]
            top = float(row[0]['past_due']) if row else max([float(r['past_due']) for r in rows] or [0.0])
            b[F['amount']] = int(round(amt)) if top and amt > top * 1.5 + 1 else int(round(amt * 100))
        else:
            b[F['amount']] = round(amt, 2)
        today = parse_now(vars.get('now')).date()
        dte = to_iso(b.get(F['date']), today)
        #F9-DATE
        if dte:
            b[F['date']] = dte
        config['body'] = b
        """
        uses = ("jget", "open_pids", "num", "parse_now", "to_iso")
        if "f9_number_input" in self.f:
            # F9: an amount or date the model passed in words is read with the spoken-number helpers
            pre_body = pre_body.replace("        #F9-AMOUNT\n", (
                "        raw_amt = str(b.get(F['amount']) or '')\n"
                "        if re.search(r'[a-z]', raw_amt.lower()):\n"
                "            sa, sc = amount_from_speech(raw_amt)\n"
                "            if sa is not None and sc is True:\n"
                "                amt = float(sa)\n"))
            pre_body = pre_body.replace("        #F9-DATE\n", (
                "        if not dte:\n"
                "            sd, sdc = date_from_speech(str(b.get(F['date']) or ''), vars.get('now'))\n"
                "            if sd and sdc is True:\n"
                "                dte = sd\n"))
            uses = uses + ("amount_from_speech", "date_from_speech")
        else:
            pre_body = pre_body.replace("        #F9-AMOUNT\n", "").replace("        #F9-DATE\n", "")
        pre = self.hook(pre_body, uses=uses)
        msgs = {"recorded": "The promise is recorded.",
                "more": "If another agreed promise is not recorded yet, CALL create_promise for it now; otherwise continue with the next open product.",
                "retry": "A technical problem stopped this; it did not go through. Do not ask the customer again: say in a few words that there was a technical problem and you are trying again, and CALL create_promise once more now with the same values. Do not use the word recorded.",
                "dup": "This product already has a recorded promise. Do not call create_promise for it again.",
                "failed": "It did not go through a second time. Do not try again. Tell the customer you could not save it because of a technical problem."}
        post = self.hook("""
        result.pop('header', None)
        d = result.get('data') if isinstance(result.get('data'), dict) else dict()
        pid = str(vars.get('cp_pid') or '')
        if jget(vars, 'pstate', dict()).get(pid) == 'promised':
            d['what_to_do_next'] = MSG['dup'] + (' ' + MSG['more'] if open_pids(vars) else '')
        elif truthy(d.get('ok')):
            set_status(vars, pid, 'promised')
            vars['promises_n'] = str(int(num(vars.get('promises_n'))) + 1)
            d['what_to_do_next'] = MSG['recorded'] + (' ' + MSG['more'] if open_pids(vars) else '')
        else:
            fails = jget(vars, 'cp_fails', dict())
            n = int(fails.get(pid, 0)) + 1
            fails[pid] = n
            jput(vars, 'cp_fails', fails)
            if n <= int(CFG['retries'].get('create_promise', 1)):
                d['what_to_do_next'] = MSG['retry']
            else:
                set_status(vars, pid, 'service_error')
                d['what_to_do_next'] = MSG['failed'] + (' ' + MSG['more'] if open_pids(vars) else '')
        result['data'] = d
        neg_flag(vars)
        refresh_close(vars, CFG)
        """, uses=("truthy", "num", "jget", "jput", "set_status", "open_pids", "neg_flag", "refresh_close"), msgs=msgs)
        desc = ("Record one payment promise for one product. CALL it only after the customer gave a clear yes to the "
                "read-back; one call per product.")
        tool = T.api_tool(self.api, "create_promise", body, description=desc, pre=pre, post=post)
        return tool, msgs, cents

    def _side_transitions(self, nid: str, with_offer: bool = True) -> list:
        out = [eq("wrap", "every product is resolved", "neg_state", "done"),
               eq("hardship", "the customer mentioned a hardship", "side", "hardship"),
               eq("side_mark", "dispute or already paid", "side", "dispute", "paid_already")]
        if "f4_discount" in self.f:
            out.append(eq("discount_reply", "asks to reduce or waive part of the amount", "side", "discount"))
        if "restructuring" in self.queues:
            first = "pre_transfer" if self._restructuring_notes() else "transfer_restructuring"
            out.append(eq(first, "asks for a new plan or lower instalments", "side", "restructuring"))
        if "human" in self.queues:
            out.append(eq("human_offer", "asks for a person", "side", "human"))
        out.append(eq("stop_count", "does not want to talk", "side", "stop_talking"))
        if "f11_promise_typed" in self.f and with_offer:
            out.append(eq("promise_check", "the customer names, accepts or leaves vague an amount or a date", "offer_intent",
                          "proposes", "accepts_offer", "vague"))
            if self.day_once:  # the exact day was asked once: a turn without a day is 'still none', so the date is offered
                out.append(any_of("promise_check", "the exact day was asked and the customer gave no day", [
                    {"logic": "and", "conditions": [leaf("day_asked", "equals", "1"), leaf("offer_intent", "equals", "none"),
                                                    leaf("side", "equals", "none"), leaf("turn_intent", "equals", "none")]}]))
        for i in range(1, self.nmax + 1):
            k = len(out)
            desc = (f"Product {i} of the facts: its persuasion attempts are used up and the customer still refuses, "
                    "or refused its last offer")
            if "f7_router" in self.f:  # a value of the side enum, not a model-called tool
                out.append(eq(f"refused_{i}", desc, "side", f"refused_{i}"))
                continue
            out.append({"target_node": f"refused_{i}", "description": desc, "is_tool_call": True,
                        "condition": {"operator": "matches_intent", "description": desc}})
            self.intent(f"transition:refused_{i}/intent",
                        [f"/nodes/{nid}/transitions/{k}/description", f"/nodes/{nid}/transitions/{k}/condition/description"], desc)
        return out

    def _promise_nodes(self, cents: bool, fields: list, f4: bool, side_descs=None, side_budget=None):
        """F11: the model only extracts what the customer said; code checks it against the product, the customer
        confirms a read-back, and the engine records the promise."""
        cp = self.p["tools"]["create_promise"]
        check = """
        rows = jget(vars, 'products', [])
        op = open_pids(vars)
        pids = pick_products(vars, vars.get('side_product'))
        pid = pids[0] if pids else ''
        if op and pid not in op:
            pid = op[0]
        row = [r for r in rows if r['pid'] == pid]
        at = str(vars.get('offer_amount_text') or '').strip() or str(vars.get('pend_amt') or '')
        dt = str(vars.get('offer_date_text') or '').strip() or str(vars.get('pend_date') or '')
        AMTCUT
        if at:
            vars['said_amount'] = at[:40]
        if dt:
            vars['said_date'] = dt[:40]
        vars['offer_intent'] = 'none'
        vars['offer_amount_text'] = ''
        vars['offer_date_text'] = ''
        vars['rb_answer'] = 'other'
        vars['side'] = 'none'
        vars['hs_choice'] = 'none'
        vars['human_choice'] = 'none'
        vars['cp_route'] = 'back'
        DAYOFF
        say = ''
        if row:
            r = row[0]
            now = parse_now(vars.get('now'))
            today = now.date()
            hol = hol_set(CFG)
            past = float(r['past_due'])
            floor = float(r['floor'])
            dl = datetime.date.fromisoformat(r['deadline'])
            amt, ac = amount_from_speech(at) if at else (None, False)
            if ac == 'full':
                amt = past
            AMTJOIN
            d_iso, dc = date_from_speech(dt, vars.get('now')) if dt else (None, False)
            if not d_iso and dt:
                d_iso = to_iso(dt, today)
            if not d_iso:
                n = int(num(vars.get('vague_n'))) + 1
                vars['vague_n'] = str(n)
                if n <= int(CFG['vague_limit']):
                    say = 'On which day exactly would you like to pay it?'
                    DAYON
                elif CFG['vague_offer'] == 'attempt1' and r.get('att1'):
                    say = 'The date I can record is ' + spoken(datetime.date.fromisoformat(r['att1'])) + '. Could you pay on that date?'
                else:
                    say = 'The latest date I can record is ' + spoken(dl) + '. Could you pay on that date?'
            elif amt is None:
                n = int(num(vars.get('amt_ask_n'))) + 1
                vars['amt_ask_n'] = str(n)
                say = 'Which amount would you like to pay?' if n == 1 else ''
            else:
                amt = float(amt)
                vars['amt_ask_n'] = '0'
                d = datetime.date.fromisoformat(d_iso)
                if amt < floor - 0.005:
                    say = 'I cannot record less than ' + money(floor) + '. Could you pay that amount?'
                elif amt > past + 0.005:
                    say = 'The amount owed is ' + money(past) + ', so I cannot record more than that. Could you pay ' + money(past) + '?'
                elif d < today:
                    say = 'I need a date from today on. The next business day is ' + spoken(bd_on_or_after(today, hol)) + '. Would that work?'
                elif d > dl:
                    say = 'The latest date I can record is ' + spoken(dl) + '. Could you pay on that date?'
                elif not is_bd(d, hol):
                    nb = bd_on_or_after(d, hol)
                    nb = dl if nb > dl else nb
                    say = 'That day is not a business day. The next business day is ' + spoken(nb) + '. Would that work?'
                else:
                    cents = CFG['cp_fields'].get('amount_unit') == 'cents'
                    vars['rb_pid'] = pid
                    vars['rb_amount'] = str(int(round(amt * 100))) if cents else str(round(amt, 2))
                    vars['rb_date'] = d.isoformat()
                    vars['rb_text'] = money(amt) + ' on ' + spoken(d)
                    vars['cp_route'] = 'confirm'
                    say = 'So that is ' + money(amt) + ' on ' + spoken(d) + ' for ' + r['label'] + '. ' + str(CFG['lines'].get('confirm_question') or 'Shall I record that?')
        if row:
            ok_a = amt is not None and floor - 0.005 <= float(amt) <= past + 0.005
            ok_d = False
            if d_iso:
                d2 = datetime.date.fromisoformat(d_iso)
                ok_d = today <= d2 <= dl and is_bd(d2, hol)
            keep = vars['cp_route'] != 'confirm'
            vars['pend_amt'] = at if (keep and ok_a) else ''
            vars['pend_date'] = dt if (keep and ok_d) else ''
        vars['say_line'] = say
        """
        if self.amount_assembly:
            check = check.replace("        AMTCUT\n", (
                "        frag = str(vars.get('amt_frag') or '')\n"
                "        vars['amt_frag'] = ''\n"
                "        raw_at = str(vars.get('offer_amount_text') or '').strip()\n"
                "        if raw_at and re.search(r'(?:sorry|i mean|no wait|wait no|actually|rather|make that|correction)\\W*(?:i\\W*)?$', raw_at.lower()):\n"
                "            at = ''  # cut off in the middle of a self-correction: the amount is not settled\n"
                "            vars['pend_amt'] = ''\n")).replace("            AMTJOIN\n", (
                "            if frag and amt is not None and ac is True and amt < floor - 0.005 and not re.search(r'\\b(?:no|sorry|i mean|actually|wait|rather)\\b', at.lower()):\n"
                "                fa = amount_from_speech(frag)[0]\n"
                "                if fa and float(fa).is_integer() and float(amt).is_integer():\n"
                "                    cand = float(str(int(fa)) + str(int(amt)))\n"
                "                    if floor - 0.005 <= cand <= past + 0.005:\n"
                "                        amt = cand\n"
                "                        at = str(int(cand))\n"
                "            if amt is not None and ac is True and amt < floor - 0.005 and float(amt).is_integer() and amt < 1000:\n"
                "                vars['amt_frag'] = str(int(amt))\n"))
        else:
            check = check.replace("        AMTCUT\n", "").replace("            AMTJOIN\n", "")
        check = (check.replace("DAYOFF", "vars['day_asked'] = '0'").replace("DAYON", "vars['day_asked'] = '1'") if self.day_once
                 else check.replace("        DAYOFF\n", "").replace("                    DAYON\n", ""))
        self.code("promise_check", check, [eq("promise_confirm", "a valid promise to read back", "cp_route", "confirm"),
                                           always("negotiate_resume")],
                  uses=("jget", "open_pids", "pick_products", "parse_now", "hol_set", "amount_from_speech", "date_from_speech",
                        "to_iso", "num", "money", "spoken", "bd_on_or_after", "is_bd"),
                  fallback="vars['cp_route'] = 'back'\nvars['say_line'] = ''")
        # the read-back is spoken from say_line; the customer answers yes / no / change
        text = "{{say_line}}"
        self.conv("promise_confirm", on_enter=self.tts("promise_confirm", text),
                  extraction=self.extraction("promise_confirm", "promise_confirm",
                                             [("rb_answer", ["yes", "no", "change", "other"])] + fields,
                                             descs=side_descs, budgets=side_budget),
                  transitions=[eq("promise_try" if self.tool_fail else "promise_write", "a clear yes", "rb_answer", "yes"),
                               eq("promise_check", "a different amount or date", "rb_answer", "change"),
                               eq("negotiate_resume", "no", "rb_answer", "no")],
                  max_turns=6, turn_target="negotiate_resume")
        tr = self._side_transitions("promise_confirm", with_offer=False)
        node = self.nodes["promise_confirm"]
        node["transitions"] = tr + node["transitions"]
        # the intent zones of the tool-call transitions point at the side list, which now starts at index 0 again
        self.task_zones("promise", "promise_confirm", "The customer is answering the read-back you just said. Say nothing "
                        "about recording or saving. No tool call is allowed in this turn.", brief_stage=("debt", "negotiate"))
        # the engine records it
        body = {**T.customer_leaf(self.api, "create_promise"),
                cp["product_id"]: T.baked(self.api, "create_promise", cp["product_id"], "{{rb_pid}}"),
                cp["amount"]: T.baked(self.api, "create_promise", cp["amount"], "{{rb_amount}}"),
                cp["date"]: T.baked(self.api, "create_promise", cp["date"], "{{rb_date}}")}
        if cents and body[cp["amount"]].get("type") == "integer":
            body[cp["amount"]]["type"] = "number"
        msgs = {"recorded": "Your promise is recorded.", "dup": "That product already has a recorded promise.",
                "failed": "I could not save that because of a technical problem."}
        post = self.hook("""
        result.pop('header', None)
        d = result.get('data') if isinstance(result.get('data'), dict) else dict()
        pid = str(vars.get('rb_pid') or '')
        vars['cp_state'] = 'failed'
        if jget(vars, 'pstate', dict()).get(pid) == 'promised':
            vars['cp_state'] = 'dup'
            vars['say_line'] = MSG['dup']
        elif truthy(d.get('ok')):
            set_status(vars, pid, 'promised')
            vars['promises_n'] = str(int(num(vars.get('promises_n'))) + 1)
            vars['cp_state'] = 'recorded'
            vars['say_line'] = MSG['recorded']
            ag = jget(vars, 'agreed', dict())
            ag[pid] = str(vars.get('rb_text') or '')
            jput(vars, 'agreed', ag)
        else:
            fails = jget(vars, 'cp_fails', dict())
            n = int(fails.get(pid, 0)) + 1
            fails[pid] = n
            jput(vars, 'cp_fails', fails)
            if n <= int(CFG['retries'].get('create_promise', 1)):
                vars['cp_state'] = 'retry'
            else:
                set_status(vars, pid, 'service_error')
                vars['say_line'] = MSG['failed']
        result['data'] = d
        neg_flag(vars)
        refresh_close(vars, CFG)
        SUMMARY
        """.replace("SUMMARY", "vars['slot_summary'] = slot_summary(vars, CFG)" if "f12_slot_summary" in self.f else "pass"),
                         uses=("truthy", "num", "jget", "jput", "set_status", "neg_flag", "refresh_close")
                         + (("slot_summary",) if "f12_slot_summary" in self.f else ()), msgs=msgs)
        tool = T.api_tool(self.api, "create_promise", body, description="Record one payment promise.", post=post)
        pw_trs = [eq("promise_write", "the call failed once: try again", "cp_state", "retry"), always("after_promise")]
        if self.tool_fail:
            # F28: on the platform a failed call (5xx) raises function_call_error: the post-process hook does not run and no response
            # variable is set. cp_state is set to 'none' before the call; if it is still 'none' afterwards the call gave no answer.
            self.code("promise_try", "vars['cp_state'] = 'none'\nvars['cp_route'] = ''\n", [always("promise_write")],
                      fallback="vars['cp_state'] = 'none'", on_fail="promise_write")  # a failed code step: the normal path, as without F28
            pw_trs = [eq("promise_error", "the call gave no answer (service error)", "cp_state", "none")] + pw_trs
            self.code("promise_error", """
            pid = str(vars.get('rb_pid') or '')
            fails = jget(vars, 'cp_fails', dict())
            n = int(fails.get(pid, 0)) + 1
            fails[pid] = n
            jput(vars, 'cp_fails', fails)
            vars['cp_state'] = 'none'
            vars['cp_route'] = 'after'
            if n <= int(CFG['retries'].get('create_promise', 1)):
                vars['cp_route'] = 'retry'
            else:
                set_status(vars, pid, 'service_error')
                vars['cp_state'] = 'failed'
                vars['say_line'] = str(CFG['lines'].get('cannot_record') or 'I could not save that because of a technical problem.')
                neg_flag(vars)
                refresh_close(vars, CFG)
            """, [eq("promise_write", "try the call once more", "cp_route", "retry"), always("after_promise")],
                      uses=("num", "jget", "jput", "set_status", "neg_flag", "refresh_close"),
                      fallback="vars['cp_route'] = 'after'", on_fail="after_promise")
        self.api_node("promise_write", tool, pw_trs, response_mapping=[{"path": "data.ok", "variable": "cp_ok"}])
        parts = [{"zone": self.book.add(f"hook:create_promise/msg/{k}", v, False, P.BUDGET["hook"], "line said after the call"),
                  "key": k} for k, v in msgs.items()]
        self.book.target("/nodes/promise_write/pre_actions/0/post_process_code", "hook_msgs", parts)
        self.code("after_promise", """
        vars['amt_ask_n'] = '0'
        vars['pend_amt'] = ''
        vars['pend_date'] = ''
        neg_flag(vars)
        refresh_close(vars, CFG)
        """, [eq("wrap", "every product is resolved", "neg_state", "done"), always("negotiate_resume")],
                  uses=("neg_flag", "refresh_close"))

    def _reindex(self, nid: str, mapping: dict):
        """Zone targets point at transitions by index; keep them right after transitions were dropped or inserted."""
        pat = re.compile("^/nodes/" + re.escape(esc(nid)) + r"/transitions/(\d+)(/.*)$")
        out = []
        for t in self.book.targets:
            m = pat.match(t["pointer"])
            if m:
                old = int(m.group(1))
                new = mapping.get(old, old)
                if new is None:
                    continue
                t = dict(t, pointer=f"/nodes/{esc(nid)}/transitions/{new}{m.group(2)}")
            out.append(t)
        self.book.targets = out

    def _router(self):
        """F7: routing that was a model decision (matches_intent: finish, goodbye, robot, scam, human) becomes one extracted
        enum per node and a code router with a precedence table. The model only extracts and speaks."""
        ai, scam = P.line(self.p, "ai"), P.line(self.p, "scam")
        human_ok = "human" in self.queues
        self.cfg["precedence"] = list(self.p.get("precedence") or DEFAULT_PRECEDENCE)
        self.cfg["route_human"] = human_ok
        body = """
        it = str(vars.get('turn_intent') or 'none')
        cand = [it]
        if str(vars.get('side')) == 'human':
            cand.append('wants_human')
        pick = 'none'
        for k in CFG['precedence']:
            if k in cand and (k != 'wants_human' or CFG['route_human']):
                pick = k
                break
        vars['turn_intent'] = 'none'
        vars['route_to'] = pick
        """
        routed = [n for n in self.FINISH_NODES + ("promise_confirm",) if n in self.nodes]
        for nid in routed:
            node = self.nodes[nid]
            back = "negotiate_resume" if nid == "negotiate" else nid
            trs = []
            if human_ok and nid != "human_offer":
                trs.append(eq("human_offer", "asks for a person", "route_to", "wants_human"))
            if ai:
                trs.append(eq(f"ai_{nid}", "asks whether this is a robot", "route_to", "asks_robot"))
                self.conv(f"ai_{nid}", on_enter=self.tts(f"ai_{nid}", ai), passthrough_to=back, transitions=[always(back)])
                self.non_conv.append(f"ai_{nid}")
            if scam:
                trs.append(eq(f"scam_{nid}", "asks whether this is a scam", "route_to", "asks_scam"))
                self.conv(f"scam_{nid}", on_enter=self.tts(f"scam_{nid}", scam), passthrough_to=back, transitions=[always(back)])
                self.non_conv.append(f"scam_{nid}")
            trs += [eq("wrap", "the conversation is finished", "route_to", "finished", "goodbye"), always(back)]
            self.code(f"route_{nid}", body, trs, fallback="vars['route_to'] = 'none'")
            # the router comes first; the model-called finish transitions go away
            old = node["transitions"]
            kept = [i for i, t in enumerate(old) if not (t.get("is_tool_call") and t.get("target_node") == "wrap")]
            first = any_of(f"route_{nid}", "the customer said something the router handles", [
                leaf("turn_intent", "equals", v) for v in ROUTE_INTENTS if v != "none"])
            node["transitions"] = [old[i] for i in kept] + [first]  # last: the node's own decisions win (yes + goodbye, insists + thanks)
            self._reindex(nid, {i: (kept.index(i) if i in kept else None) for i in range(len(old))})
            schema = node.setdefault("extraction_schema", [])
            z = self.book.add("extract:router/turn_intent/description", P.EXTRACT["turn_intent"], False, 1000,
                              "extraction field description (enum values locked)")
            self.book.target(f"/nodes/{esc(nid)}/extraction_schema/{len(schema)}/description", "text", [{"zone": z}])
            schema.append({"name": "turn_intent", "type": "string", "enum_values": list(ROUTE_INTENTS),
                           "description": P.EXTRACT["turn_intent"], "min_confidence": MIN_CONF})
        for g in self.globals.values():
            g["excluded_nodes"] = sorted(set(g.get("excluded_nodes") or []) | set(routed) | set(self.non_conv))
        used = {part["zone"] for t in self.book.targets for part in t["parts"]}
        if "transition:finish/intent" not in used:
            self.book.zones.pop("transition:finish/intent", None)

    @property
    def dispute_first(self) -> bool:
        """F25: needs the brief's dispute outcome with a verified quote."""
        return "f25_dispute_wins" in self.f and bool((self.p.get("outcome_map") or {}).get("np_dispute")) \
            and verified(self.p, "/outcome_map/np_dispute")

    @property
    def amount_assembly(self) -> bool:
        """F26: amount fragments are joined and a cut-off self-correction is not taken; needs the F9 helpers and a verified floor."""
        return ("f26_amount_assembly" in self.f and "f11_promise_typed" in self.f and "f9_number_input" in self.f
                and verified(self.p, "/product_types"))

    @property
    def day_once(self) -> bool:
        """F24 (rule E): "ask the exact day once" is counted in code. Needs the router (turn_intent) and a verified limit."""
        return ("f24_day_once" in self.f and "f11_promise_typed" in self.f and "f7_router" in self.f
                and verified(self.p, "/ask_limits/vague_date") and "vague_limit" in self.cfg)

    @property
    def tool_fail(self) -> bool:
        """F28 (opt-in): a create_promise call that gives no answer is counted and retried per the brief's retry rule, then closed as
        the brief's service error. Needs the brief's retry rule (verified) and a service-error outcome."""
        return ("f28_tool_failure" in self.f and "f11_promise_typed" in self.f
                and verified(self.p, "/retries/create_promise") and bool((self.p.get("outcome_map") or {}).get("np_service_error")))

    @property
    def vague_signal(self) -> bool:
        """F27 (opt-in, needs f24): only when the brief has the vague-day rule (day_once: the day-ask limit verified against the brief text)."""
        return "f27_vague_signal" in self.f and self.day_once

    def _restructuring_notes(self) -> bool:
        return bool(self.multi and self.p.get("notes_on_restructuring") and self.cfg["notes"]
                    and (self.p.get("note_map") or {}).get(self.p["notes_on_restructuring"]))

    def _negotiation(self):
        tool, msgs, cents = self._promise_tool()
        f4 = "f4_discount" in self.f
        f11 = "f11_promise_typed" in self.f
        f8 = "f8_fixed_lines" in self.f
        sl = f4 or f11 or f8
        said_hs = f8 and bool(P.line(self.p, "hardship_sympathy"))
        f7 = "f7_router" in self.f
        f15 = "f15_hardship_terms" in self.f
        fields = [("side", ["none", "hardship", "dispute", "paid_already", "restructuring", "human", "stop_talking"]
                   + (["discount"] if f4 else []) + ([f"refused_{i}" for i in range(1, self.nmax + 1)] if f7 else []))]
        f16 = "f16_intents" in self.f
        side_text = P.side_desc(f4, self.nmax if f7 else 0, P.intents_text(self.p, fields[0][1]) if f16 else "",
                                dispute_first=self.dispute_first)
        side_descs = {"side": side_text} if (f4 or f7 or f16 or self.dispute_first) else None
        side_budget = {"side": len(side_text) + 300} if f16 else None
        if self.vague_signal:  # F24: a "not today, but later" turn is vague, so the exact day is asked instead of a first offer
            side_descs = dict(side_descs or {}, offer_intent=P.EXTRACT["offer_intent"] + P.VAGUE_SIGNAL)
            side_budget = dict(side_budget or {}, offer_intent=len(P.EXTRACT["offer_intent"] + P.VAGUE_SIGNAL) + 100)
        if self.amount_assembly:  # the tail of a cut-off self-correction must reach the code
            side_descs = dict(side_descs or {}, offer_amount_text=P.EXTRACT["offer_amount_text"] + P.AMOUNT_TAIL)
        if self.multi:
            fields.append(("side_product", [str(i) for i in range(1, self.nmax + 1)] + ["all"]
                           + (["none"] if "f11_promise_typed" in self.f else [])))
        offer_fields = [("offer_intent", ["proposes", "accepts_offer", "vague", "none"]), ("offer_amount_text", None),
                        ("offer_date_text", None)]
        if f11:
            fields = fields + offer_fields
        refusal = ("When a product's persuasion attempts are used up and the customer still refuses, CALL "
                   + " / ".join(f"transition_to_refused_{i}" for i in range(1, self.nmax + 1))
                   + " (the number is the product's position in the facts).")
        task = P.negotiate_task(self.p, self.multi, typed=f11, routed=f7) + ("" if f7 else "\n" + refusal)
        if not f11:
            task += "\n" + P.negotiate_tool_hint(self.p, cents)
        for nid, entry in (("negotiate", self.tts("negotiate", "{{debt_statement}} {{first_ask_line}}")),
                           ("negotiate_resume", {"type": "llm", "prompt": P.resume_entry(self.p, sl, f7, f15)})):
            self.conv(nid, on_enter=entry, first_only=(nid == "negotiate"), tools=[] if f11 else [tool],
                      extraction=self.extraction("negotiate", nid, fields, descs=side_descs, budgets=side_budget),
                      transitions=self._side_transitions(nid),
                      max_turns=40, turn_target="wrap")
            if not f11:
                self.tool_zones(nid, 0, tool, msgs)
            self.task_zones("negotiate", nid, task, facts="{{facts_text}}", brief_stage=("debt", "negotiate"))
        self.book.add("node:negotiate/resume_entry", P.resume_entry(self.p, sl, f7, f15), True, purpose="entry line on re-entry")
        self.book.target("/nodes/negotiate_resume/on_enter/prompt", "text", [{"zone": "node:negotiate/resume_entry"}])
        reset = """
        vars['side'] = 'none'
        vars['side_product'] = ''
        vars['hs_choice'] = 'none'
        vars['human_choice'] = 'none'
        """
        if f11 and self.multi:  # '' is not in the enum and the platform drops the write: reset to a value inside it
            reset = reset.replace("vars['side_product'] = ''", "vars['side_product'] = 'none'")
        if sl:
            reset += "vars['say_line'] = ''\n        "
        if f11:
            reset += ("vars['offer_intent'] = 'none'\n        vars['offer_amount_text'] = ''\n"
                      "        vars['offer_date_text'] = ''\n        ")
        if f11:
            self._promise_nodes(cents, fields, f4, side_descs, side_budget)
        if f4:
            nd = P.line(self.p, "no_discount") if f8 else ""
            nd = nd if nd and "{" not in nd else (I18N.TR_NO_DISCOUNT if self.lang == "tr" else P.NO_DISCOUNT)
            self.code("discount_reply", reset + "vars['say_line'] = " + repr(nd) + "\n", [always("negotiate_resume")],
                      fallback=reset)
        back = [eq("wrap", "every product is resolved", "neg_state", "done"), always("negotiate_resume")]
        paid_say = ""
        if f8 and P.line(self.p, "paid_reply") and "{" not in P.line(self.p, "paid_reply"):
            paid_say = "vars['say_line'] = " + repr(P.line(self.p, "paid_reply")) + " if status == 'paid' else ''\n        "
        self.code("side_mark", """
        status = 'dispute' if str(vars.get('side')) == 'dispute' else 'paid'
        for pid in pick_products(vars, vars.get('side_product')):
            set_status(vars, pid, status)
        """ + reset + paid_say + """
        neg_flag(vars)
        refresh_close(vars, CFG)
        """, back, uses=("pick_products", "set_status", "neg_flag", "refresh_close"), fallback=reset)
        self.code("hardship_mark", """
        for pid in open_pids(vars):
            set_status(vars, pid, 'hardship')
        """ + (("said_once(vars, 'hardship_sympathy')\n        " if "f20_once_flags" in self.f else "vars['said_hardship_sympathy'] = '1'\n        ") if said_hs else "") + reset + """
        neg_flag(vars)
        refresh_close(vars, CFG)
        """, back, uses=("open_pids", "set_status", "neg_flag", "refresh_close") + (("said_once",) if said_hs and "f20_once_flags" in self.f else ()), fallback=reset)
        self.code("stop_count", """
        n = int(num(vars.get('stop_n'))) + 1
        vars['stop_n'] = str(n)
        if n >= int(CFG.get('stop_limit', 2)):
            for pid in open_pids(vars):
                set_status(vars, pid, 'refused')
        """ + reset + """
        neg_flag(vars)
        refresh_close(vars, CFG)
        """, back, uses=("num", "open_pids", "set_status", "neg_flag", "refresh_close"), fallback=reset)
        for i in range(1, self.nmax + 1):
            self.code(f"refused_{i}", f"""
        pids = jget(vars, 'pids', [])
        if len(pids) >= {i}:
            set_status(vars, pids[{i - 1}], 'refused')
        """ + reset + """
        neg_flag(vars)
        refresh_close(vars, CFG)
        """, back, uses=("jget", "set_status", "neg_flag", "refresh_close"), fallback=reset)
        if not (f11 and f15) or ("human" in self.queues and not self.human_first):  # else nothing routes to it
            self.code("resume", reset, [always("negotiate_resume")])

        has_support = "support" in self.queues
        hs_trs = []
        terms = f11 and f15  # F15: terms named in the same turn win over "no thanks" to the support offer
        if terms:
            hs_trs.append(eq("promise_check", "the customer names or accepts an amount or a date", "offer_intent",
                             "proposes", "accepts_offer"))
        if has_support:
            hs_trs.append(eq("transfer_support", "accepts the support team", "hs_choice", "accepts"))
        hs_trs += [eq("hardship_mark", "declines", "hs_choice", "declines"),
                   eq("promise_check" if terms else "resume", "names a date or amount", "hs_choice", "names_terms")]
        said = P.line(self.p, "hardship_sympathy") if f8 else ""
        if said and has_support and "?" not in said:  # the fixed line must also make the offer the model would have made
            said = said.rstrip() + " " + (I18N.TR_HARDSHIP_OFFER if self.lang == "tr" else "Would you like me to connect you with the support team?")
        self.conv("hardship", on_enter=self.tts("hardship", said) if said else {"type": "llm"}, first_only=bool(said),
                  extraction=self.extraction("hardship", "hardship", [("hs_choice", ["accepts", "declines", "names_terms", "none"])]
                                             + (offer_fields if terms else [])),
                  transitions=hs_trs, max_turns=6, turn_target="hardship_mark")
        self.task_zones("hardship", "hardship", P.hardship_task(self.p, has_support, said), brief_stage="negotiate")
        if "human" in self.queues:
            if self.human_first:  # the request may come before the products are loaded: go back to where the call was
                back = """
        vars['side'] = 'none'
        vars['human_choice'] = 'none'
        vars['who_wants_human'] = 'no'
        vars['id_intent'] = 'other'
        RESET_OFFER
        if str(vars.get('phase')) == 'products':
            vars['back_to'] = 'products'
        elif str(vars.get('who1')) == 'customer' or str(vars.get('who2')) == 'customer':
            vars['back_to'] = 'identity'
        else:
            vars['back_to'] = 'opening'
        """
                back = back.replace("        RESET_OFFER\n", ("        vars['offer_intent'] = 'none'\n        vars['offer_amount_text'] = ''\n"
                                                 "        vars['offer_date_text'] = ''\n") if "f11_promise_typed" in self.f else "")
                self.code("human_back", back, [eq("negotiate_resume", "the products were already discussed", "back_to", "products"),
                                               eq("identity", "the customer is confirmed", "back_to", "identity"),
                                               always("opening_ask2" if int(self.limits.get("right_party", 2)) >= 2 else "mark_nr_unclear")],
                          fallback="vars['back_to'] = 'products'")
            ho_terms = f11 and f15  # F15: terms named while accepting help are checked and read back, not lost
            self.conv("human_offer", on_enter={"type": "llm"},
                      extraction=self.extraction("human", "human_offer", [("human_choice", ["insists", "continues", "none"])]
                                                 + (offer_fields if ho_terms else [])),
                      transitions=[eq("transfer_human", "insists on a person", "human_choice", "insists")]
                      + ([eq("promise_check", "the customer names or accepts an amount or a date", "offer_intent",
                             "proposes", "accepts_offer")] if ho_terms else [])
                      + [eq("human_back" if self.human_first else "resume", "accepts help", "human_choice", "continues")],
                      max_turns=3, turn_target="transfer_human")
            self.task_zones("human", "human_offer", P.human_task(self.p, "f20_once_flags" in self.f), brief_stage="global")
            if "f20_once_flags" in self.f:  # F20: the help offer is made once; a later request for a person is an insistence
                limit = int(self.limits.get("human_offer", 1))
                self.code("human_gate", ("vars['gate'] = 'offer' if said_once(vars, 'human_offer') else 'transfer'\n" if limit >= 1
                                         else "vars['gate'] = 'transfer'\n")
                          + ("vars['turn_intent'] = 'none'\n" if "f7_router" in self.f else ""),
                          [eq("transfer_human", "the offer was made already", "gate", "transfer"), always("human_offer")],
                          uses=("said_once",), fallback="vars['gate'] = 'offer'")

    # transfers --------------------------------------------------------------------------------------------------------
    def _transfers(self):
        qf = self.p["tools"]["transfer_to_agent"]["queue"]
        for canon, value in self.queues.items():
            post = self.hook(f"""
        result.pop('header', None)
        vars['transfer_key'] = '{canon}'
        refresh_close(vars, CFG)
        """, uses=("refresh_close",))
            tool = T.api_tool(self.api, "transfer_to_agent",
                              {**T.customer_leaf(self.api, "transfer_to_agent"), qf: T.baked(self.api, "transfer_to_agent", qf, value)},
                              description="Transfer the call.", post=post)
            self.api_node(f"transfer_{canon}", tool, [always("wrap")])
        if self._restructuring_notes():
            canon = self.p["notes_on_restructuring"]
            self.code("pre_transfer", f"""
        pst = jget(vars, 'pstate', dict())
        reason = CFG['note_map'].get('{canon}', '')
        queue = [[p, reason] for p in jget(vars, 'pids', []) if pst.get(p, 'open') != 'promised' and reason]
        jput(vars, 'note_queue', queue)
        vars['after_notes'] = 'transfer'
        vars['side'] = 'none'
        """, [always("note_next")], uses=("jget", "jput"), fallback="vars['after_notes'] = 'transfer'")

    # marks ------------------------------------------------------------------------------------------------------------
    def _marks(self):
        for key in ("nr_unclear", "nr_wrong_number", "nr_deceased", "nr_callback", "nr_no_callback", "nv_failed",
                    "nv_declined", "np_service_error", "np_service_error_hard"):
            self.code(f"mark_{key}", f"""
        vars['end_key'] = '{key}'
        refresh_close(vars, CFG)
        """, [always("wrap")], uses=("refresh_close",), fallback=f"vars['end_key'] = '{key}'")

    # wrap, notes, close -----------------------------------------------------------------------------------------------
    def _wrap_and_close(self):
        L = self.cfg["lines"]
        prefix = {k: L.get(v, "") for k, v in CLOSING_PREFIX.items()}
        for k, v in GENERIC_PREFIX.items():
            prefix.setdefault(k, I18N.localize(v, self.lang))
        self.cfg["closing_prefix"] = prefix
        body = """
        queue = []
        if str(vars.get('phase')) == 'products' and str(vars.get('transfer_key') or 'none') == 'none':
            for pid in open_pids(vars):
                set_status(vars, pid, 'refused')
            if CFG.get('notes'):
                pst = jget(vars, 'pstate', dict())
                for pid in jget(vars, 'pids', []):
                    reason = CFG['note_map'].get(pst.get(pid, 'refused'), '')
                    if pst.get(pid) != 'promised' and reason:
                        queue.append([pid, reason])
        jput(vars, 'note_queue', queue)
        vars['after_notes'] = 'close'
        vars['notes_state'] = 'pending' if queue else 'done'
        key = refresh_close(vars, CFG)
        pre = CFG['closing_prefix'].get(key, '')
        if key == 'promise' and not int(num(vars.get('promises_n'))):
            pre = ''
        vars['closing_line'] = (pre + ' ' + CFG['lines'].get('goodbye', 'Goodbye.')).strip()
        """
        after = "close_node" if self.close_form == "node" else "end"
        wrap_trs = [eq("note_next", "notes to write", "notes_state", "pending")] if self.cfg["notes"] else []
        if "f13_verifier" in self.f:
            self._verifier()
        self.code("wrap_close" if "f13_verifier" in self.f else "wrap", body, wrap_trs + [always(after)],
                  uses=("open_pids", "set_status", "jget", "jput", "refresh_close", "num"),
                  fallback="vars['notes_state'] = 'done'")
        if self.cfg["notes"]:
            self.code("note_next", """
        queue = jget(vars, 'note_queue', [])
        if queue:
            item = queue.pop(0)
            vars['note_pid'] = str(item[0])
            vars['note_reason'] = str(item[1])
            vars['notes_state'] = 'go'
        else:
            vars['notes_state'] = 'done'
        jput(vars, 'note_queue', queue)
        """, [eq("note_write", "write the next note", "notes_state", "go")]
                + ([eq("transfer_restructuring", "notes before the transfer are done", "after_notes", "transfer")]
                   if self._restructuring_notes() else []) + [always(after)],
                uses=("jget", "jput"), fallback="vars['notes_state'] = 'done'")
            f = self.p["tools"]["add_note"]
            tool = T.api_tool(self.api, "add_note", {**T.customer_leaf(self.api, "add_note"),
                                                     f["product_id"]: T.baked(self.api, "add_note", f["product_id"], "{{note_pid}}"),
                                                     f["reason"]: T.baked(self.api, "add_note", f["reason"], "{{note_reason}}")},
                              description="Note a product.")
            self.api_node("note_write", tool, [always("note_next")])
        if self.close_form == "node":
            self.api_node("close_node", self._close_tool(lifecycle=False), [always("end")])
        self.end_node("end")

    def _verifier(self):
        """F13: every path into the close passes through here (the node is named wrap; the close computation is wrap_close).
        Every product has a status, and a required line that was not said is said once before the call closes."""
        body = """
        vars['vc_route'] = 'go'
        if str(vars.get('phase')) == 'products' and str(vars.get('transfer_key') or 'none') == 'none':
            for pid in open_pids(vars):
                set_status(vars, pid, 'refused')
        key = refresh_close(vars, CFG)
        req = (CFG.get('required_lines') or dict()).get(key)
        if req and str(vars.get('said_' + req)) != '1' and int(num(vars.get('say_miss_n'))) < 1:
            vars['say_miss_n'] = '1'
            vars['miss_line'] = str(CFG['lines'].get(req) or '')
            if vars['miss_line']:
                vars['vc_route'] = 'say'
        """
        self.code("wrap", body, [eq("say_missing", "a required line was not said yet", "vc_route", "say"), always("wrap_close")],
                  uses=("open_pids", "set_status", "refresh_close", "num"), fallback="vars['vc_route'] = 'go'")
        self.conv("say_missing", on_enter=self.tts("say_missing", "{{miss_line}}"), passthrough_to="wrap", transitions=[always("wrap")])
        self.non_conv.append("say_missing")

    def _close_tool(self, lifecycle: bool) -> dict:
        f = self.p["tools"]["close_call"]
        if f.get("mode") == "code":
            body = {f["code"]: T.baked(self.api, "close_call", f["code"], "{{close_code}}")}
        else:
            body = {f["outcome"]: T.baked(self.api, "close_call", f["outcome"], "{{close_outcome}}"),
                    f["reason"]: T.baked(self.api, "close_call", f["reason"], "{{close_reason}}")}
        pre = self.hook("""
        refresh_close(vars, CFG)
        b = config.get('body') or dict()
        F = CFG['close_fields']
        if F.get('mode') == 'code':
            b[F['code']] = vars.get('close_code')
        else:
            b[F['outcome']] = vars.get('close_outcome')
            if vars.get('close_reason'):
                b[F['reason']] = vars.get('close_reason')
            else:
                b.pop(F['reason'], None)
        config['body'] = b
        """, uses=("refresh_close",))
        extra = {"alwaysRunsAt": ["end_of_call"]} if lifecycle else None
        return T.api_tool(self.api, "close_call", {**T.customer_leaf(self.api, "close_call"), **body},
                          description="Close the call record.", pre=pre, config_extra=extra)

    # ending the call --------------------------------------------------------------------------------------------------
    FINISH_NODES = ("third_party", "identity", "identity_explain", "negotiate", "negotiate_resume", "hardship", "human_offer")

    def _finish_routes(self):
        """Every conversation node after the opening can end the call: the model calls transition_to_wrap when the
        conversation is over, and a global catches a customer's goodbye after the agent's own goodbye. Without these,
        a finished call loops on 'goodbye' until the turn cap (seen on the platform)."""
        for nid in self.FINISH_NODES + (("promise_confirm",) if "f11_promise_typed" in self.f else ()):
            node = self.nodes.get(nid)
            if node is None:
                continue
            k = len(node["transitions"])
            node["transitions"].append({"target_node": "wrap", "description": P.FINISH, "is_tool_call": True,
                                        "condition": {"operator": "matches_intent", "description": P.FINISH}})
            self.intent("transition:finish/intent", [f"/nodes/{nid}/transitions/{k}/description",
                                                     f"/nodes/{nid}/transitions/{k}/condition/description"], P.FINISH)
        excluded = sorted(set(self.non_conv) | {"start", "opening", "opening_ask2"} | (set(self.globals) - {"g_goodbye"}))
        self.globals["g_goodbye"] = {
            "name": "g_goodbye", "node_type": "conversation", "task_message": "", "on_enter": {"type": "silent"},
            "passthrough": True, "passthrough_default_transition": "wrap", "transitions": [always("wrap")],
            "description": P.GOODBYE_INTENT, "condition": {"operator": "matches_intent", "description": P.GOODBYE_INTENT},
            "excluded_nodes": excluded}
        self.intent("global:g_goodbye/intent", ["/global_nodes/g_goodbye/condition/description",
                                                "/global_nodes/g_goodbye/description"], P.GOODBYE_INTENT)
        self.task_zones("g_goodbye", "g_goodbye", "The call is ending. No tool call is allowed in this turn.",
                        is_global=True, brief_stage="close")

    # globals ----------------------------------------------------------------------------------------------------------
    def _globals(self):
        excluded = sorted(set(self.non_conv) | {"start", "opening", "opening_ask2"})
        for gid, key, intent in (("g_ai", "ai", "The person asks whether they are talking to a robot, a computer, a machine or an AI."),
                                 ("g_scam", "scam", "The person asks whether this call is a scam or doubts that the call is really from the bank.")):
            text = P.line(self.p, key)
            if not text:
                continue
            self.conv(gid, on_enter=self.tts(gid, text, is_global=True), passthrough_to="__previous__", is_global=True,
                      transitions=[always("__previous__")],
                      description=intent, condition={"operator": "matches_intent", "description": intent},
                      excluded_nodes=excluded + sorted(set(self.globals) - {gid}))
            self.intent(f"global:{gid}/intent", [f"/global_nodes/{gid}/condition/description", f"/global_nodes/{gid}/description"], intent)
            self.task_zones(gid, gid, "Answer the person's question with the fixed line, then continue. No tool call is allowed in this turn.",
                            is_global=True, brief_stage="global")
        if "human" in self.queues:
            intent = "The person asks to speak to a human, a person, an agent or a manager."
            self.conv("g_human", on_enter={"type": "llm"}, is_global=True,
                      extraction=[{"name": "human_choice", "type": "string", "enum_values": ["insists", "continues", "none"],
                                   "description": P.EXTRACT["human_choice"], "min_confidence": MIN_CONF}],
                      transitions=[eq("transfer_human", "insists on a person", "human_choice", "insists"),
                                   eq("__previous__", "accepts help", "human_choice", "continues")],
                      max_turns=3, turn_target="transfer_human",
                      description=intent, condition={"operator": "matches_intent", "description": intent},
                      excluded_nodes=excluded + ["negotiate", "negotiate_resume", "human_offer", "hardship", "g_ai", "g_scam"]
                      + (["promise_confirm"] if "f11_promise_typed" in self.f else []))
            self.book.target("/global_nodes/g_human/extraction_schema/0/description", "text",
                             [{"zone": "extract:human/human_choice/description"}])
            self.intent("global:g_human/intent", ["/global_nodes/g_human/condition/description", "/global_nodes/g_human/description"], intent)
            self.task_zones("human", "g_human", P.human_task(self.p, "f20_once_flags" in self.f), is_global=True, brief_stage="global")
