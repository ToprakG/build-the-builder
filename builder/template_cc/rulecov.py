"""RULE COVERAGE: which normative sentences of a brief does the compiled workflow enforce in CODE?

    sentences = split_brief(text)                         # normative sentences (+ table rows) with section and line
    classify(sentences, brief_name, model, cache_dir)     # regex pre-pass, then ONE claude_cli:sonnet call per brief
    rows = check(sentences, policy, workflow)             # enforced-by: code | prompt | missing, plus the evidence

Reads briefs, policy.json and workflow.json only; never cases, configs or transcripts.

Vocabulary
- RULE TYPE: the fixed library below (what a sentence is about). A sentence in no type is UNKNOWN.
- CAPABILITY: one thing the compiler can enforce in code (a counter, a fixed line, an api node, a router precedence...).
  Each capability has a sentence pattern and a predicate on the compiled workflow. A sentence is
    code     every capability that matches it is engaged in this workflow,
    prompt   a capability matches but is not engaged, or none exists, and the sentence's key terms are in prompt text,
    missing  the compiler never mentions it.
  `capability == none` rows are the GAPS: no code path exists for them, whatever the brief says.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

TYPES = ["verbatim-line", "once-rule", "max-attempts-limit", "ordering", "conditional-route", "forbidden-disclosure",
         "deadline-date", "amount", "outcome-code", "note-rule", "side-intent", "multi-product"]
STAGES = ["global", "opening", "third_party", "identity", "debt", "negotiate", "hardship", "human", "close"]

# ----------------------------------------------------------------------------------------------------------------- split

NORMATIVE = re.compile(
    r"\b(must|never|only|once|twice|exactly|if\b|close as|transfer|say|do not|don't|does not|did not|may not|may\b|"
    r"ask|call|offer|propose|record|state|read|send|count|keep|add|stop|take|answer|describe|treat|applies|wins|"
    r"counts? as|means|before|after|first|then|else|otherwise|is closed|are closed|closes|no\b|not\b)", re.I)
DESCRIPTIVE_ONLY = re.compile(r"^(every rule of brief a|the outcome table of brief a|\w+ \w+ is fictional|every customer|this brief has|brief [ab] is|read all of it|the voice bot must follow)", re.I)

# regex pre-pass: type -> patterns. A sentence can have several candidate types.
HINTS = {
    "verbatim-line": [r"\bsay\b[^.]*[\"“`']", r"\bask\b[^.]*[\"“]", r"\bexactly\b[^.]*[\"“]", r"this sentence", r"[\"“][A-Z][^\"”]{8,}[\"”]"],
    "once-rule": [r"\bonce\b", r"\bonly once\b", r"\bexactly one\b", r"\bone promise\b", r"\bsecond (ask|refusal|such)\b", r"\bnot repeat"],
    "max-attempts-limit": [r"\b(twice|two|three|2|3)\b[^.]*(wrong|attempt|times|fails?|ask)", r"\battempts?\b", r"\bat most\b",
                           r"\bup to\b", r"\bmax(imum)?\b", r"\bwhen the attempts"],
    "ordering": [r"\bbefore\b", r"\bonly after\b", r"\bafter (both|the|a|you)", r"\bfirst\b[^.]*\bthen\b", r"\blast tool call\b",
                 r"\bin the order\b", r"\bone by one\b"],
    "conditional-route": [r"\bif\b[^.]*\b(call|transfer|close|route|offer|note)\b", r"\btransfer_to_agent\b", r"\bschedule_callback\b",
                          r"\bqueue\b", r"\bcall `?[a-z_]+`?"],
    "forbidden-disclosure": [r"\bnever\b", r"\bdo not (say|state|tell|mention|argue|offer|invent|describe|repeat)", r"\bmay not\b",
                             r"\bnot be told\b", r"\bno one\b|\bnobody\b", r"\bdiscount|waiver|instalment plan", r"\binvent\b"],
    "deadline-date": [r"\bdeadline\b", r"\bbusiness day", r"\bholiday", r"\bweekend\b", r"\bdate\b", r"\bweekday|monday|friday",
                      r"\bnext (week|month)|end of the month|vague", r"\b\d\d:\d\d\b", r"\bdays?_late\b|\bdays late\b"],
    "amount": [r"\bfloor\b", r"\bfull (past-due )?amount\b", r"\bminimum\b", r"\binstalment_amount|minimum_payment", r"\bamount\b",
               r"\bpast[- ]due\b"],
    "outcome-code": [r"\bclose as\b", r"\bclose_call\b", r"\boutcome\b", r"`(promise|paid|transferred|no_promise|not_verified|not_reached)`"],
    "note-rule": [r"\badd_note\b", r"\bnotes?\b"],
    "side-intent": [r"\brobot\b|\bAI\b|\bscam\b", r"\bhuman\b|\bperson\b|\bagent\b", r"\billness|death|job loss|hardship|sympathy",
                    r"\bdispute|not mine|never had", r"\balready paid\b|\bpaid\b", r"\bhas died\b|\bdeceased\b|\bdied\b",
                    r"\brestructur|payment plan|lower instalments", r"\bwrong number\b|does not belong",
                    r"\bdo(es)? not want to talk"],
    "multi-product": [r"\bproduct(s)?\b[^.]*\b(each|every|per|all|one by one|several)\b", r"\bper product\b", r"\beach product\b",
                      r"\ball of them\b", r"\bthree products\b"],
}
HINTS_RX = {t: [re.compile(p, re.I) for p in ps] for t, ps in HINTS.items()}


def regex_types(text: str) -> list[str]:
    return [t for t, rxs in HINTS_RX.items() if any(rx.search(text) for rx in rxs)]


def brief_label(paths) -> str:
    """Name of a build's brief material: the folder for .../brief.md, else the file stem; several joined by +."""
    return "+".join(Path(b).parent.name if Path(b).name == "brief.md" else Path(b).stem for b in paths)


def split_brief(text: str, brief: str = "") -> list[dict]:
    """Normative sentences, in order. Table rows become one sentence each ("Table <headers>: <cells>")."""
    out: list[dict] = []
    section, fence, headers = "", False, None
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("```"):
            fence = not fence
            continue
        if fence or not line:
            headers = None if not line else headers
            continue
        if line.startswith("#"):
            section = line.lstrip("# ").strip()
            headers = None
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if headers is None:
                headers = cells
                continue
            sent = "Table [" + " | ".join(headers) + "]: " + " | ".join(cells)
            out.append({"text": sent, "section": section, "line": ln, "num": None, "table": True, "cue": True})
            continue
        headers = None
        m = re.match(r"^(?:[-*]\s+)?(?:(\d+)\.\s+)?(.*)$", line)
        num, body = (int(m.group(1)) if m.group(1) else None), m.group(2)
        body = re.sub(r"\s+", " ", body).strip()
        # sentence split: a full stop followed by a capital / quote / backtick, not after a digit-dot list index or e.g.
        parts = re.split(r"(?<=[.!?])(?<!\be\.g\.)(?<!\bi\.e\.)\s+(?=[A-Z\"`“*])", body)
        for p in parts:
            p = p.strip()
            if len(p) < 12 or DESCRIPTIVE_ONLY.match(p):
                continue
            # every sentence goes to the classifier (recall); `cue` is the regex guess used when there is no LLM
            out.append({"text": p, "section": section, "line": ln, "num": num, "table": False, "cue": bool(NORMATIVE.search(p))})
    for i, s in enumerate(out):
        s["id"] = i
        s["brief"] = brief
        s["regex_types"] = regex_types(s["text"])
        s["stage_guess"] = stage_guess(s)
    return out


def stage_guess(s: dict) -> str:
    sec, t = s["section"].lower(), s["text"].lower()
    for key, st in (("opening", "opening"), ("someone else", "third_party"), ("third", "third_party"), ("identity", "identity"),
                    ("stating", "debt"), ("dates", "negotiate"), ("amounts", "negotiate"), ("getting the promise", "negotiate"),
                    ("confirming", "negotiate"), ("other things", "global"), ("closing", "close")):
        if key in sec:
            return st
    return "negotiate" if "promise" in t or "amount" in t else "global"


# --------------------------------------------------------------------------------------------------------------- classify

CLASSIFY_SYSTEM = (
    "You label the normative sentences of a phone-collections agent brief. For every sentence give: normative (true when it "
    "constrains what the agent must/must not say, ask, call, record or close as; false for pure description), types (zero or "
    "more of the LIBRARY, most relevant first), and stage (where in the call it applies). Be liberal: give the closest "
    "type(s) whenever a sentence even loosely belongs to one (a limit on how often something happens is once-rule or "
    "max-attempts-limit, 'cannot be changed' is once-rule, 'say X' is verbatim-line, 'ask when the customer can be reached' "
    "is conditional-route). Only a normative sentence that fits NO library type at all gets types [] (it is UNKNOWN, a human "
    "reviews it). Never invent a type.\n\nLIBRARY:\n"
    "- verbatim-line: the agent must say a given sentence or exact wording.\n"
    "- once-rule: something may or must happen exactly once (ask once, offer once, one promise per product, one close_call).\n"
    "- max-attempts-limit: a counted limit (three wrong answers, persuasion attempts per band, retries, twice).\n"
    "- ordering: X only/before/after Y, tool-call order, say-then-close, state before asking.\n"
    "- conditional-route: if situation X then call a tool / use a queue / close with an outcome / go to a step.\n"
    "- forbidden-disclosure: things that must not be said, offered, revealed or invented (also to whom, and before when).\n"
    "- deadline-date: business days, holidays, deadline by band, callback window, weekday words, vague dates, date format.\n"
    "- amount: full amount, floor / minimum, what may be accepted or offered, who proposes amounts.\n"
    "- outcome-code: the outcome/reason a call is closed with, the outcome table, priority between reasons.\n"
    "- note-rule: when add_note is called or not, with which reason.\n"
    "- side-intent: AI/robot question, scam question, human request, hardship/illness, dispute, already paid, deceased, "
    "restructuring, wrong number, 'does not want to talk'.\n"
    "- multi-product: rules that exist because a customer can be late on several products.\n"
    f"Stages: {', '.join(STAGES)}.")
CLASSIFY_SCHEMA = {"type": "object", "required": ["sentences"], "properties": {"sentences": {"type": "array", "items": {
    "type": "object", "required": ["id", "normative", "types", "stage"],
    "properties": {"id": {"type": "integer"}, "normative": {"type": "boolean"},
                   "types": {"type": "array", "items": {"type": "string", "enum": TYPES}},
                   "stage": {"type": "string", "enum": STAGES}}}}}}


def classify(sentences: list[dict], brief_name: str, model: str = "claude_cli:sonnet", cache_dir: Path | None = None,
             use_llm: bool = True, trace=None) -> str:
    """Fill s['types'], s['stage'], s['normative']. Returns the source: 'llm', 'cache' or 'regex'."""
    payload = [{"id": s["id"], "section": s["section"], "text": s["text"], "regex_hint": s["regex_types"]} for s in sentences]
    key = hashlib.sha256(json.dumps([CLASSIFY_SYSTEM, model, payload], sort_keys=True).encode()).hexdigest()[:16]
    cache = (cache_dir / f"{brief_name}.{key}.json") if cache_dir else None
    result = None
    source = "regex"
    if cache and cache.exists():
        result, source = json.loads(cache.read_text()), "cache"
    elif use_llm:
        try:
            from builder.template_cc import llm

            r = llm.call_llm(CLASSIFY_SYSTEM, "Sentences of brief " + brief_name + ":\n" + json.dumps(payload, ensure_ascii=False),
                             model, json_schema=CLASSIFY_SCHEMA, trace=trace, purpose=f"rule coverage classify {brief_name}",
                             attempts=3)
            result, source = r, "llm"
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(r, indent=1))
        except Exception as e:  # noqa: BLE001 - the regex pre-pass still gives a classification
            print(f"rule_coverage: LLM classification of {brief_name} failed ({str(e)[:120]}); regex pre-pass only")
    by_id = {x["id"]: x for x in (result or {}).get("sentences", []) if isinstance(x, dict) and "id" in x}
    for s in sentences:
        x = by_id.get(s["id"])
        if x:
            s["normative"], s["types"] = bool(x["normative"]), [t for t in x.get("types", []) if t in TYPES]
            s["stage"] = x.get("stage") if x.get("stage") in STAGES else s["stage_guess"]
            # the regex pre-pass keeps a sentence the model dropped only when it has a hard modal cue
            if not s["normative"] and re.search(r"\b(must|never|do not|only|exactly)\b", s["text"], re.I):
                s["normative"] = True
        else:
            s["normative"], s["types"], s["stage"] = s["cue"], list(s["regex_types"]), s["stage_guess"]
    return source


# ------------------------------------------------------------------------------------------------------------- the facts

def _walk(o, fn):
    if isinstance(o, dict):
        for k, v in o.items():
            fn(k, v)
            _walk(v, fn)
    elif isinstance(o, list):
        for v in o:
            _walk(v, fn)


STOP = set("that this with from they them have will your what when then into only also each their there which about would could "
           "should customer says said call".split())


def content_words(t: str) -> set[str]:
    return {w[:5] for w in squash(t).split() if len(w) > 3 and w not in STOP}


def squash(s: str) -> str:
    """Comparable text: placeholders -> @, case, punctuation and spacing folded."""
    s = re.sub(r"\{+[^{}]*\}+|<[^<>]{1,30}>", "@", s or "")
    s = s.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^a-z0-9@']+", " ", s).strip()


class Facts:
    def __init__(self, policy: dict, wf: dict):
        self.p, self.wf = policy, wf
        self.nodes = {**(wf.get("nodes") or {}), **(wf.get("global_nodes") or {})}
        codes: list[str] = []
        self.api_nodes: dict[str, set[str]] = {}      # tool -> nodes that call it from code
        self.llm_tools: dict[str, set[str]] = {}      # tool -> conversation nodes that expose it to the model
        self.trans_vars: set[str] = set()
        self.enums: set[str] = set()
        self.tts: list[str] = []
        prompts: list[str] = [wf.get("system_prompt") or ""]
        self.edges: dict[str, set[str]] = {}
        for nid, n in self.nodes.items():
            def grab(k, v, nid=nid):
                if k == "post_process_code" and isinstance(v, str):
                    codes.append(v)
                if k == "code" and isinstance(v, dict) and isinstance(v.get("default"), str):
                    codes.append(v["default"])
            _walk(n, grab)
            ntype = n.get("node_type")
            for a in n.get("pre_actions") or []:
                name = (a.get("function") or {}).get("name")
                if name and name != "code_exec":
                    self.api_nodes.setdefault(name, set()).add(nid)
            for t in n.get("tools") or []:
                name = (t.get("function") or {}).get("name")
                if name:
                    self.llm_tools.setdefault(name, set()).add(nid)
            if ntype == "conversation":
                prompts.append(n.get("task_message") or "")
            oe = n.get("on_enter") or {}
            if oe.get("type") in ("tts", "say") and oe.get("text"):
                self.tts.append(oe["text"])
            for e in n.get("extraction_schema") or []:
                self.enums.update(map(str, e.get("enum_values") or []))
                prompts.append(e.get("description") or "")
            tgt = set()
            for tr in n.get("transitions") or []:
                if tr.get("target_node"):
                    tgt.add(tr["target_node"])
                for c in (tr.get("condition") or {}).get("conditions") or []:
                    if c.get("variable"):
                        self.trans_vars.add(c["variable"].split(".")[0])
            if n.get("passthrough_default_transition"):
                tgt.add(n["passthrough_default_transition"])
            self.edges[nid] = tgt
        for t in wf.get("tools") or []:
            name = (t.get("function") or {}).get("name")
            if name:
                self.api_nodes.setdefault(name, set()).add("<call-level>")
        self.code = "\n".join(codes)
        lits = (re.findall(r"'([^'\n]{18,})'", self.code) + re.findall(r'"([^"\n]{18,})"', self.code)
                + re.findall(r'\\"([^"\\\n]{18,})\\"', self.code))
        self.fixed_sets = [cs for cs in (content_words(x) for x in self.tts + lits) if len(cs) >= 3]
        self.code_sq = squash(self.code)
        self.tts_sq = squash(" ".join(self.tts))
        self.prompt = "\n".join(prompts)
        self.prompt_sq = squash(self.prompt)
        self.cfg_multi = bool(re.search(r'\\"multi\\": true', self.code))
        self.start = wf.get("initial_node") or "start"
        self.queues = set(re.findall(r'"queue": \{[^}]*?"default": "([^"]+)"', json.dumps(self.nodes))) | set(
            re.findall(r"'queue': '([^']+)'", self.code))

    def has(self, *names) -> bool:
        return all(n in self.code for n in names)

    def node(self, *ids) -> bool:
        return all(i in self.nodes for i in ids)

    def reachable_without(self, target: str, blocked: set[str]) -> bool:
        seen, stack = set(), [self.start]
        while stack:
            n = stack.pop()
            if n in seen or n in blocked:
                continue
            if n == target:
                return True
            seen.add(n)
            stack.extend(self.edges.get(n, ()))
        return False

    def code_only_tool(self, tool: str) -> bool:
        """The tool exists in the workflow only as a code call (api node), never exposed to the model."""
        return tool in self.api_nodes and tool not in self.llm_tools


# ------------------------------------------------------------------------------------------------------------ capabilities

class Cap:
    def __init__(self, cid, types, rx, pred, label):
        self.id, self.types, self.rx, self.pred, self.label = cid, types, re.compile(rx, re.I), pred, label


def _quoted(text: str) -> list[str]:
    q = re.findall(r"[\"“]([^\"”]{6,})[\"”]", text)
    return [x for x in q if len(x.split()) >= 2]


def _spoken_quotes(text: str) -> list[str]:
    """Quoted wording that follows a say/ask/read verb (a quote that is only a trigger phrase is not a line to say)."""
    out = []
    for m in re.finditer(r"\b(say|ask|read|sentence|exactly)\b([^\"“]{0,40})[\"“]([^\"”]{6,})[\"”]", text, re.I):
        if len(m.group(3).split()) >= 2:
            out.append(m.group(3))
    return out


def _pairs(text: str) -> list[tuple[str, str | None]]:
    """(outcome, reason) pairs named in a sentence or table row."""
    outs = ["promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"]
    found = []
    for m in re.finditer(r"`(" + "|".join(outs) + r")`(?:\s*/\s*`([a-z_]+)`)?", text):
        found.append((m.group(1), m.group(2)))
    if text.startswith("Table") and "Outcome" in text:
        cells = [c.strip() for c in text.split("]:", 1)[1].split("|")]
        if len(cells) >= 2 and cells[0].strip("` ") in outs:
            reasons = re.findall(r"`([a-z_]+)`", cells[1])
            return [(cells[0].strip("` "), r) for r in reasons] or [(cells[0].strip("` "), None)]
    return found


def build_caps() -> list[Cap]:
    C: list[Cap] = []

    def cap(cid, types, rx, label):
        def deco(fn):
            C.append(Cap(cid, types, rx, fn, label))
            return fn
        return deco

    # --- verbatim-line -------------------------------------------------------------------------------------------------
    def negated(t):
        return bool(re.match(r"\s*(do not|don't|never|no\b)", t, re.I)) or bool(re.search(r"\b(do not|never) (say|ask|use)\b", t, re.I))

    def fixed_line(f, s):
        if negated(s["text"]) or not re.search(r"\b(say|ask|read|sentence|exactly|tell|state)\b", s["text"], re.I):
            return None
        q = [squash(x) for x in _spoken_quotes(s["text"])]
        if not q:
            return None
        return all(x in f.tts_sq or x in f.code_sq for x in q)
    cap("fixed-line", ["verbatim-line"], r"[\"“][^\"”]{6,}[\"”]", "quoted wording is a fixed tts line or a code-built line")(fixed_line)

    def fixed_say(f, s):
        t = s["text"]
        m = re.search(r"\b(say(?: that| this sentence:?)?|tell(?: the customer)?(?: that)?|state|offer to|ask (?:them|the customer|the person) to|first sentence:? say|read[^.]*back)\b(.*)", t, re.I)
        if not m or negated(t) or _spoken_quotes(t) or re.search(r"\b(only after|only when|before|until)\b|read-?back", t, re.I):
            return None
        clause = re.split(r"[,;:]| and (?=close|call|take|offer|make|ask|continue|record|do not)| then ", m.group(2))[0]
        if re.match(r"\s*(one|a) (short )?sentence", clause, re.I):
            return None
        want = content_words(clause)
        if len(want) < 2:
            return None
        return max((len(want & fs) / len(want) for fs in f.fixed_sets), default=0) >= 0.6
    cap("fixed-say", ["verbatim-line"], r"\b(say|tell|state|offer to|ask (them|the customer|the person) to|first sentence)\b", "the wording is a fixed tts line or a code-built line")(fixed_say)
    # --- once / limits ------------------------------------------------------------------------------------------------
    al = lambda f, k: bool((f.p.get("ask_limits") or {}).get(k))
    cap("lim-right-party", ["once-rule", "max-attempts-limit"], r"second ask|ask once more|still unclear|confirmation", "right-party re-ask limit")(
        lambda f, s: f.node("opening_ask2") and al(f, "right_party") or False)
    cap("lim-id-wrong", ["max-attempts-limit"], r"wrong answers?|three wrong|day_month_swapped", "wrong-answer counter")(
        lambda f, s: ((f.p.get("identity") or {}).get("max_wrong") and f.has("id_wrong")) or False)
    cap("lim-id-decline", ["once-rule", "max-attempts-limit"], r"explain once|protect their account|second refusal|declin", "identity decline counter")(
        lambda f, s: (al(f, "id_decline") and f.has("id_declines")) or False)
    cap("lim-callback-reask", ["once-rule"], r"ask once for another time|another time|outside it", "callback re-ask counter")(
        lambda f, s: (al(f, "callback_reask") and f.has("cb_state")) or False)
    cap("lim-vague", ["once-rule"], r"vague|exact day", "vague-date counter")(
        lambda f, s: (al(f, "vague_date") and f.has("vague_n")) or False)
    cap("lim-stop", ["max-attempts-limit", "once-rule"], r"do not want to talk|says? twice|twice that", "stop-talking counter")(
        lambda f, s: (al(f, "stop_talking") and f.has("stop_n")) or False)
    cap("lim-human", ["once-rule"], r"request for a human|ask once whether you can help", "human-offer once")(
        lambda f, s: (f.node("human_offer") and al(f, "human_offer")) or False)
    cap("lim-persuasion", ["max-attempts-limit"], r"persuasion|attempts? (are|is|used)|attempt [12]|flat (refusal|no)|non-answer|1 attempt", "persuasion attempt plan")(
        lambda f, s: (f.p.get("attempt_plan") and f.p.get("bands") and f.has("def attempt(")) or False)
    cap("lim-floor-once", ["once-rule"], r"floor, once|offer the floor once|floor once|once\.", "floor offered once")(
        lambda f, s: f.has("amt_ask_n") or False)
    cap("lim-retry", ["max-attempts-limit", "once-rule"], r"try once more|fails? (twice|again)|could not record|twice", "api retry then service_error")(
        lambda f, s: f.node("mark_np_service_error") or False)
    cap("lim-one-promise", ["once-rule"], r"one promise per product|cannot be changed|cannot be cancel", "one promise per product")(
        lambda f, s: (f.has("promises_n") or f.has("open_pids")) or False)
    cap("lim-one-close", ["once-rule", "ordering"], r"exactly one `?close_call|last tool call", "one close_call at end of call")(
        lambda f, s: ("close_call" in f.api_nodes and "close_call" not in f.llm_tools) or False)
    # --- ordering ------------------------------------------------------------------------------------------------------
    def tool_after_verify(f, s):
        tools = re.findall(r"`?(get_account|create_promise|add_note|transfer_to_agent|schedule_callback|verify_identity)`?", s["text"])
        if not tools:
            return None
        if not all(f.code_only_tool(t) for t in tools):
            return False
        if "get_account" in tools and "verify_identity" in f.api_nodes:
            blk = f.api_nodes["verify_identity"]
            return not any(f.reachable_without(n, blk) for n in f.api_nodes["get_account"])
        return True
    cap("ord-tool-code", ["ordering", "conditional-route"], r"\b(call|only after|before|after security)\b[^.]*`?(get_account|create_promise|add_note|verify_identity)`?",
        "tool called by a code node only, in graph order")(tool_after_verify)
    cap("ord-goodbye-close", ["ordering"], r"goodbye.*close_call|thank the customer", "closing line before close_call")(
        lambda f, s: (f.has("closing_line") and f.node("wrap_close")) or False)
    cap("ord-state-first", ["ordering"], r"state every|before asking for anything|one sentence each", "debt statement before the ask")(
        lambda f, s: (f.has("debt_statement") and "{{debt_statement}}" in " ".join(f.tts)) or False)
    cap("ord-product-order", ["ordering", "multi-product"], r"in the order|one by one|get_account.*returned", "products processed in get_account order")(
        lambda f, s: (f.cfg_multi and f.has("open_pids", "pick_products")) or False)
    # --- conditional route ---------------------------------------------------------------------------------------------
    def queue_ok(f, s):
        qs = re.findall(r"queue `?([a-z_]+)`?|transfer_to_agent\(([a-z_]+)\)", s["text"])
        qs = [a or b for a, b in qs]
        if not qs:
            return None
        qm = set((f.p.get("queue_map") or {}).values())
        return all(q in qm and q in f.queues for q in qs)
    cap("route-queue", ["conditional-route", "side-intent"], r"queue `?[a-z_]+`?|transfer_to_agent\([a-z_]+\)", "transfer node with that queue")(queue_ok)
    cap("route-callback", ["conditional-route"], r"schedule_callback", "callback booked by a code-checked node")(
        lambda f, s: False if "schedule_callback" in f.llm_tools else ("schedule_callback" in f.api_nodes or False))
    # --- forbidden -----------------------------------------------------------------------------------------------------
    cap("forb-before-verify", ["forbidden-disclosure"], r"before (the )?identity|until|not (be )?told|nothing about the account|do not state the debt|anything about the account|relative|husbands|whoever picks up|before security",
        "account facts only exist after verification (get_account behind verify_identity)")(
        lambda f, s: ("get_account" in f.api_nodes and "verify_identity" in f.api_nodes and f.code_only_tool("get_account")
                      and not any(f.reachable_without(n, f.api_nodes["verify_identity"]) for n in f.api_nodes["get_account"])) or False)
    cap("forb-discount", ["forbidden-disclosure"], r"discount|waiver|instalment plan|not in this brief", "discount/plan request answered by a code route")(
        lambda f, s: f.node("discount_reply") or False)
    cap("forb-wordlist", ["forbidden-disclosure"], r"do not say[^.]*[\"“]|never say these words|do not say \"", "banned-word scan on model output")(
        lambda f, s: False)       # no output filter exists in the compiler: the word list is prompt text only
    cap("forb-invent", ["forbidden-disclosure"], r"do not invent|never invent|invent figures|making things up|never invent", "no invented figures (needs an output check)")(
        lambda f, s: False)
    # --- date ---------------------------------------------------------------------------------------------------------
    cap("date-deadline", ["deadline-date"], r"deadline|business days?|holiday|weekend|Table \[.*deadline", "bands + holidays computed in code")(
        lambda f, s: (f.p.get("bands") and f.has("add_bd", "is_bd", "band_for")) or False)
    cap("date-weekday-words", ["deadline-date"], r"next <?weekday|this <?weekday|\"next\"|following calendar week|following week|next friday", "next/this weekday in code")(
        lambda f, s: (f.p.get("weekday_words") and f.has("weekday_words")) or False)
    cap("date-callback-window", ["deadline-date"], r"callback|\b10:00\b|19:00", "callback window checked in code")(
        lambda f, s: False if "schedule_callback" in f.llm_tools and not f.has("cb_window") else (f.has("cb_window_text") or False))
    cap("date-now", ["deadline-date"], r"`now`|real clock", "today from the now variable")(
        lambda f, s: f.has("today_iso", "parse_now") or False)
    cap("date-format", ["deadline-date"], r"YYYY-MM-DD|day, then month|numbers as day", "date normalised in code")(
        lambda f, s: f.has("to_iso") or False)
    # --- amount -------------------------------------------------------------------------------------------------------
    cap("amt-floor", ["amount"], r"floor|minimum_payment|instalment_amount", "floor per product type in code")(
        lambda f, s: (any((v or {}).get("floor_field") for v in (f.p.get("product_types") or {}).values()) and f.has("floor")) or False)
    cap("amt-accept-range", ["amount"], r"from the floor up|below the floor|cannot be recorded", "accepted amount range checked in code")(
        lambda f, s: (f.has("pend_amt") and f.has("floor")) or False)
    cap("amt-never-ask", ["amount", "forbidden-disclosure"], r"never ask|how much can you pay|the agent proposes every amount", "agent proposes amounts (needs an output check)")(
        lambda f, s: False)
    # --- outcome ------------------------------------------------------------------------------------------------------
    def outcome_ok(f, s):
        codes = re.findall(r"\b\d\d-[a-z]{2,5}(?:-[a-z]{1,4})?\b", s["text"].lower())
        if codes:
            blob = json.dumps(f.p.get("outcome_map") or {}).lower()
            return all(c in blob for c in codes)
        pairs = _pairs(s["text"])
        if not pairs:
            return None
        om = {tuple(v) if v[1] else (v[0], None) for v in (f.p.get("outcome_map") or {}).values()}
        om |= {(v[0], None) for v in (f.p.get("outcome_map") or {}).values()}
        return all((o, r) in om or (r is None and any(x[0] == o for x in om)) for o, r in pairs)
    cap("out-map", ["outcome-code"], r"close as|`(promise|paid|transferred|no_promise|not_verified|not_reached)`|\b\d\d-[a-z]{2,5}\b|disposition codes", "outcome/reason pair in outcome_map")(outcome_ok)
    cap("out-priority", ["outcome-code", "multi-product"], r"first product|wins whenever|with no promise recorded|several products", "close_rule priority across products")(
        lambda f, s: ((f.p.get("close_rule") or {}).get("kind") not in (None, "single") and f.has("final_key")) or False)
    # --- notes --------------------------------------------------------------------------------------------------------
    def note_ok(f, s):
        t = s["text"]
        if re.search(r"(never|not|n't) uses? `?add_note|add_note`? is not used|no notes", t, re.I) and not re.search(r"transfer", t, re.I):
            return "add_note" not in f.api_nodes or False
        if re.search(r"transfer", t, re.I) and re.search(r"note", t, re.I):
            return ("notes_on_restructuring" in f.p and f.has("after_notes")) or False
        reasons = re.findall(r"`([a-z_]+)`", t)
        nm = f.p.get("note_map") or {}
        return ("add_note" in f.api_nodes and f.has("note_reason") and all(r in nm for r in reasons if r in
                ("refused", "hardship", "dispute", "paid", "service_error"))) or False
    cap("note", ["note-rule"], r"add_note|\bnotes? (for|with)|add (a )?notes?|\bno notes\b", "add_note called from a code node with the mapped reason")(note_ok)
    # --- side intents -------------------------------------------------------------------------------------------------
    for cid, rx, enum, node, label in (
            ("si-ai", r"robot|\bAI\b|digital assistant", "asks_robot", "g_ai", "AI question routed in code to a fixed line"),
            ("si-scam", r"scam", "asks_scam", "g_scam", "scam question routed in code to a fixed line"),
            ("si-human", r"request for a human|ask to speak|human", "wants_human", "human_offer", "human request routed in code"),
            ("si-deceased", r"has died|deceased|died", "customer_died", "mark_nr_deceased", "death routed to a fixed close"),
            ("si-wrong-number", r"does not belong|wrong number|wrong_number", "not_their_number", "mark_nr_wrong_number", "wrong number routed"),
            ("si-restructuring", r"restructur|payment plan|lower instalments", None, "transfer_restructuring", "restructuring routed to its queue"),
            ("si-hardship", r"illness|hardship|job loss|sympathy", None, "hardship", "hardship routed to its node"),
            ("si-dispute", r"not mine|never had|dispute", None, "mark_np_dispute", "dispute routed"),
            ("si-paid", r"already paid|already_paid", None, "mark_paid", "already-paid routed")):
        def pred(f, s, enum=enum, node=node, cid=cid):
            ok = (enum is None or enum in f.enums or enum in f.code) and (f.node(node) or node in f.code or
                                                                          (cid in ("si-dispute", "si-paid", "si-hardship") and bool(f.trans_vars)))
            if cid == "si-dispute":
                ok = "dispute" in f.code and "side" in f.trans_vars | set(re.findall(r"vars\['(side)'\]", f.code))
            if cid == "si-paid":
                ok = "paid" in f.code and "side" in f.trans_vars | set(re.findall(r"vars\['(side)'\]", f.code))
            return ok or False
        C.append(Cap(cid, ["side-intent"], rx, pred, label))
    # --- structural caps added after reading the p6 workflows ---------------------------------------------------------
    cap("ord-id-order", ["ordering"], r"one at a time|security questions|first the date of birth", "identity questions asked in policy order by code (id_q)")(
        lambda f, s: bool((f.p.get("identity") or {}).get("questions")) and f.has("id_q"))
    cap("amt-statement", ["amount", "ordering"], r"state the past-due amount|ask for the full (past-due|overdue)|full (past-due|overdue|past_due) amount first|ask full past_due|how many days late|days_late", "debt statement and first ask are code-built lines")(
        lambda f, s: f.has("debt_statement", "first_ask_line") and "{{debt_statement}}" in " ".join(f.tts))
    cap("not-refusal", ["deadline-date", "amount", "max-attempts-limit"], r"never a refusal", "a proposed date/amount goes to promise_check, not to the refusal counter")(
        lambda f, s: f.has("offer_intent") and f.node("promise_check"))
    cap("no-repeat-offer", ["once-rule", "max-attempts-limit"], r"repeat an offer|already refused", "attempt plan: each attempt is a distinct code-computed offer")(
        lambda f, s: bool(f.p.get("attempt_plan")) and f.has("def attempt("))
    cap("multi-restructuring", ["multi-product", "conditional-route"], r"before the request|not yet recorded|promises recorded", "restructuring transfer is a code node: recorded promises stay, pending ones are never written")(
        lambda f, s: f.node("transfer_restructuring") and "create_promise" not in json.dumps(f.nodes["transfer_restructuring"]))
    cap("note-all", ["note-rule", "multi-product"], r"no product gets a promise|only one product|without a promise|note it and continue", "notes for every product without a promise come from the product-status code")(
        lambda f, s: "add_note" in f.api_nodes and f.has("note_reason", "notes_state"))
    def precedence(f, s):
        t = f.nodes
        def idx(nid, target):
            tr = [x["target_node"] for x in (t.get(nid) or {}).get("transitions") or []]
            return tr.index(target) if target in tr else None
        if re.search(r"before and after verification", s["text"], re.I):
            return bool(idx("identity", "human_offer") is not None and idx("negotiate", "human_offer") is not None)
        h, r, st = idx("negotiate", "hardship"), idx("negotiate", "transfer_restructuring"), idx("negotiate", "stop_count")
        return h is not None and all(x is None or h < x for x in (r, st))
    cap("ord-precedence", ["ordering"], r"comes before rules?|applies before|before and after verification", "router precedence = transition order in the node")(precedence)
    cap("hardship-offer", ["conditional-route"], r"still names? a date or an amount|names own date|still name a date", "hardship node routes a named date/amount to promise_check")(
        lambda f, s: f.node("hardship") and "promise_check" in f.edges.get("hardship", ()))
    cap("confirm-change", ["conditional-route"], r"changes the date or the amount at this step", "promise_confirm loops to promise_check")(
        lambda f, s: "promise_check" in f.edges.get("promise_confirm", ()))
    cap("pid-in-tools", ["multi-product"], r"that id in every tool call|product_id", "product_id is filled by code in create_promise/add_note")(
        lambda f, s: f.code_only_tool("create_promise") and "add_note" not in f.llm_tools)
    cap("multi-first-ask", ["multi-product", "conditional-route"], r"pay all of them today|all of them today", "one first ask for all products (first_ask_line)")(
        lambda f, s: bool(f.cfg_multi) and f.has("first_ask_line"))
    cap("ord-recorded-ok", ["ordering"], r"recorded (only )?(on|once)[^.]*ok|only (say|after)[^.]*ok:? ?true|only after `?create_promise`? returns", "promise is announced from the create_promise result by a code node")(
        lambda f, s: "promise_write" in f.api_nodes.get("create_promise", ()) and f.has("say_line"))
    cap("fixed-first-ask", ["verbatim-line", "ordering"], r"make this payment today", "first ask is a fixed code line")(
        lambda f, s: f.has("first_ask_line") and "can you make this payment today" in f.code_sq)
    cap("hardship-sympathy", ["side-intent", "verbatim-line"], r"sympathy", "hardship sympathy is a fixed tts line said once (said_hardship_sympathy)")(
        lambda f, s: f.has("said_hardship_sympathy") and "sorry" in f.tts_sq and f.node("hardship"))
    def id_fields(f, s):
        named = [k for k, rx in (("date_of_birth", r"date of birth|\bdob\b"), ("phone_last4", r"phone digits|phone number")) if re.search(rx, s["text"], re.I)]
        if not named or not re.search(r"do not use|don't use|any more|no longer", s["text"], re.I):
            return None
        return not any(k in ((f.p.get("identity") or {}).get("questions") or []) for k in named)
    cap("id-fields", ["forbidden-disclosure", "ordering"], r"do not use|any more|no longer", "identity questions come from policy.identity.questions")(id_fields)
    cap("date-rows", ["deadline-date"], r"\bB\d\b.*\d|\bN=\d+|BANK CLOSED|\bsections? 7 and 8\b", "band/holiday rows come from policy.bands and policy.holidays; named dates go to promise_check")(
        lambda f, s: _date_rows(f, s["text"]))
    cap("single-product", ["multi-product"], r"one product per customer", "single-product brief: policy.max_products == 1")(
        lambda f, s: (f.p.get("max_products") or 1) == 1)
    # --- multi-product ------------------------------------------------------------------------------------------------
    def multi_pred(f, s):
        if not (f.cfg_multi or f.p.get("multi_product")):
            return None
        return f.has("open_pids", "product_count")
    cap("multi", ["multi-product"], r"per product|each product|every (late )?product|several products|one by one|three products|for each product|one to three",
        "per-product loop, pids and statuses in code")(multi_pred)
    cap("multi-readback", ["multi-product", "deadline-date"], r"own deadline|read-?back|read all promises|keep the date", "read-back built from the per-product table in code")(
        lambda f, s: bool(f.cfg_multi) and f.has("rb_text", "band_for"))
    cap("out-transfer-wins", ["outcome-code"], r"transfer follows a recorded promise|close as the transfer|transfer after a recorded promise", "a transfer key overrides the promise key at close")(
        lambda f, s: f.has("transfer_key", "final_key"))
    return C


def _date_rows(f, text: str):
    t = text.strip()
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{2})\b", t)
    if "BANK CLOSED" in t and m:
        iso = f"20{m.group(3)}-{m.group(2)}-{m.group(1)}"
        return iso in (f.p.get("holidays") or [])
    if re.search(r"sections? 7 and 8", t):
        return f.node("promise_check")
    nums = {int(x) for x in re.findall(r"\d+", t)}
    bands = f.p.get("bands") or []
    have = {b.get(k) for b in bands for k in ("min_days", "max_days", "deadline_business_days", "attempts")}
    return bool(nums) and nums <= have


CAPS = build_caps()


# ------------------------------------------------------------------------------------------------------------------ check

def key_terms(text: str) -> list[str]:
    terms = [squash(x) for x in re.findall(r"`([^`]+)`", text)] + [squash(x) for x in _quoted(text)]
    return [t for t in terms if len(t) > 3]


def check(sentences: list[dict], policy: dict, wf: dict) -> list[dict]:
    f = Facts(policy, wf)
    rows = []
    for s in sentences:
        if not s.get("normative", True):
            continue
        types = s.get("types") or []
        matched = [c for c in CAPS if c.rx.search(s["text"]) and (set(c.types) & set(types) or not types or True)]
        # a capability counts for a sentence when its pattern matches AND (its type is among the sentence's types or the
        # sentence has no type); capabilities of other types only add evidence, they never demote a sentence
        relevant = matched
        row = {"brief": s["brief"], "id": s["id"], "line": s["line"], "num": s["num"], "section": s["section"], "text": s["text"],
               "types": types, "stage": s.get("stage", s["stage_guess"]), "caps": [], "capability": "none"}
        if not types:
            row.update(enforced_by="unknown", note="fits no library type")
            rows.append(row)
            continue
        results = []
        for c in relevant:
            try:
                r = c.pred(f, s)
            except Exception as e:  # noqa: BLE001 - a broken predicate must not hide the sentence
                r = None
                row["note"] = f"predicate {c.id} failed: {e}"
            results.append((c, r))
        results = [(c, r) for c, r in results if r is not None]
        row["caps"] = [{"id": c.id, "engaged": bool(r), "label": c.label} for c, r in results]
        if results:
            row["capability"] = "+".join(c.id for c, _ in results)
        if results and all(r for _, r in results):
            row["enforced_by"] = "code"
        else:
            terms = key_terms(s["text"])
            words = [w for w in squash(s["text"]).split() if len(w) > 4]
            in_prompt = (terms and all(t in f.prompt_sq for t in terms)) or (
                sum(w in f.prompt_sq for w in words) >= max(2, int(0.5 * len(words))))
            row["enforced_by"] = "prompt" if in_prompt else "missing"
        if results and not all(r for _, r in results):
            row["why"] = "capability not engaged: " + ", ".join(c.id for c, r in results if not r)
        elif not results:
            row["why"] = "no compiler capability for this sentence"
        rows.append(row)
    return rows


def gaps(rows: list[dict]) -> dict:
    """type -> {'rows': [...]} for the non-code rows; a row with no capability at all is a hard gap."""
    out: dict[str, dict] = {}
    for r in rows:
        if r["enforced_by"] in ("code", "unknown"):
            continue
        for t in r["types"][:1] or ["?"]:
            g = out.setdefault(t, {"rows": [], "briefs": set(), "no_capability": 0})
            g["rows"].append(r)
            g["briefs"].add(r["brief"])
            g["no_capability"] += r["capability"] == "none"
    return out


def summary(rows: list[dict]) -> dict:
    tab = {t: {"sentences": 0, "code": 0, "prompt": 0, "missing": 0} for t in TYPES}
    for r in rows:
        if not r["types"]:
            continue
        t = r["types"][0]
        tab[t]["sentences"] += 1
        tab[t][r["enforced_by"]] += 1
    return tab


def scan_stage_nodes(wf: dict, stage: str) -> list[str]:
    """Conversation nodes whose prompt carries the brief rules of a stage (mirrors compiler/graph.py brief_stage)."""
    names = {
        "opening": ["opening", "opening_ask2"],
        "third_party": ["third_party"],
        "identity": ["identity", "identity_explain"],
        "debt": ["negotiate", "negotiate_resume"],
        "negotiate": ["negotiate", "negotiate_resume", "promise_confirm"],
        "hardship": ["hardship"],
        "human": ["human_offer", "g_human"],
        "close": ["g_goodbye"],
    }
    allnodes = {**(wf.get("nodes") or {}), **(wf.get("global_nodes") or {})}
    return [n for n in names.get(stage, []) if n in allnodes and allnodes[n].get("task_message") is not None]
