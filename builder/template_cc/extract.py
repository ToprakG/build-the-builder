"""EXTRACT: brief text + tool API summary -> policy.json in the canonical schema.

Two independent readings (A: plain, B: sections first + a quote per value) are merged field by field
(`extract_vote.merge`). Critical fields the readings disagree on go to one verifier call that must answer with a quote
found in the brief; otherwise reading A's value is kept. Deterministic rules follow (multi_product needs a statement in
the brief; a band/callback number must be in its quote). Pass 2 then checks every field against the brief, and
`policy_check.check` runs; violations go back to the model once as a fix request. Anything still invalid gets the
conservative default (policy_check.repair) and is logged. Every trace line says per field: agree / verified / fallback.
"""

from __future__ import annotations

import json
import os
import re

from builder.template_cc import brief_prep, extract_vote, policy_check, policy_fixups
from builder.template_cc.policy_check import (ATTEMPT_AMOUNTS, ATTEMPT_DATES, CANONICAL_NOTES, CANONICAL_OUTCOMES,
                                              CANONICAL_QUEUES, LINE_KEYS, SCHEMA)

RULES = f"""You read a bank's specification for an outbound collections phone call (a clean spec, or raw material:
mails, policy extracts, memos, code lists) and its tool API. You write policy.json: the facts a code generator needs
to build the call. Every value comes from the brief or the tool API. Never invent a value; never use an example value.

General rules
- The brief may be in any language (for example Turkish). Keep every JSON key in English. "language" is the ISO code of the language the brief is
  written in (detected from its text; "en" for English). Text values (lines, sentences, extra_rules, _source quotes, prompts) are copied
  verbatim from the brief in its own language, never translated; enum values stay exactly as the tool API spells them.
- Several brief parts are given in order: a later part, a later memo or a newer statement overrides an earlier one.
  Use the final value and record each override in "_overrides": [{{"key", "old", "new", "source"}}].
- Enum values (outcomes, reasons, codes, queues, note reasons, identity field values, product types) are copied exactly
  as the tool API spells them. Field names in "tools" and "account_fields" are the API's field names.
- "_source": map of JSON pointer (e.g. "/bands/0/deadline_business_days") -> an exact quote from the brief that states
  it. Quote short exact spans. Give a quote for every key the brief states.
- If the brief is silent, use null / [] / {{}} and say so in "extra_rules" with stage "global" only when it matters.

Keys
- currency: the currency symbol or word exactly as the brief writes it next to amounts (for example the symbol or the short word), null when the brief names none.
- bank, language (ISO code), address (how to address the customer with {{field}} placeholders from the call variables).
- call_variables: every call variable the brief lists.
- holidays: every listed non-business day as YYYY-MM-DD, sorted (convert odd formats).
- bands: [{{name, min_days, max_days, deadline_business_days, attempts}}] contiguous, in order.
- attempt1: the brief's rule for the first persuasion proposal in one sentence of your own words.
- attempt_plan: one object per persuasion attempt, in order: {{"date": one of {list(ATTEMPT_DATES)}, "amount": one of
  {list(ATTEMPT_AMOUNTS)}, "cap_at_deadline": bool}}. end_of_week_roll = last business day of this week, or of next
  week when today is that day; full_or_floor = the full amount, or the floor if the customer already said the full
  amount is too much.
- weekday_words: {{"next": "following_week"|"next_occurrence", "this": "current_week"|"next_occurrence"}}.
- identity: {{"questions": [API field values in asking order], "max_wrong": int, "swap_hint": bool (the API returns a
  hint the brief tells the agent to act on), "prompts": {{field: the question to ask}}, "kinds": {{field:
  "date"|"year"|"digits"|"code"|"text"}}}}.
- callback: {{"start": "HH:MM", "end": "HH:MM", "same_day": bool}}.
- outcome_map: canonical key -> [outcome, reason] (reason null when none) or [code, null] when close_call takes one
  code; null for a situation the brief does not have. Canonical keys: {", ".join(CANONICAL_OUTCOMES)}.
- queue_map: {{{", ".join(CANONICAL_QUEUES)}}} -> transfer queue value or null.
- note_map: {{{", ".join(CANONICAL_NOTES)}}} -> add_note reason value; {{}} when the brief never uses notes.
- close_rule: {{"kind": "first_non_paid"}} or {{"kind": "priority", "order": [canonical note keys]}} or {{"kind": "single"}}.
- notes_on_restructuring: canonical note key to note before a restructuring transfer, or null.
- product_types: canonical short key -> {{"enum": API type value, "label": how the agent names it ({{last4}} allowed),
  "floor_field": account field of the minimum amount, "last4_field": account field or null}}.
- account_fields: {{"product_id", "type", "past_due", "days_late"}} -> get_account field names.
- product_id_format: the id pattern, e.g. "x{{n}}".
- tools: {{"verify_identity": {{"field", "answer"}}, "create_promise": {{"product_id", "amount", "date",
  "amount_unit": "currency"|"cents"}}, "schedule_callback": {{"mode": "date_time", "date", "time"}} or {{"mode":
  "datetime", "datetime"}}, "close_call": {{"mode": "outcome_reason", "outcome", "reason"}} or {{"mode": "code",
  "code"}}, "transfer_to_agent": {{"queue"}}, "add_note": {{"product_id", "reason"}}}} (values are API field names).
- sentences: {{"opening", "promise_confirmation", "ai", "scam", "failed_id"}} short phrases as the brief states them.
- lines: speakable sentences the agent says, keyed by {", ".join(LINE_KEYS)}. Each {{"text", "verbatim": bool}}.
  verbatim=true only when the brief fixes the exact words (copy them exactly, with {{field}} placeholders for call
  variables); otherwise write one short spoken sentence that says exactly what the brief requires, nothing more.
  The only placeholders allowed in a line are call variables ({{field}}). Never put account values (product,
  amount, days late, dates) into a line: the code adds them.
  The opening line must contain everything the brief requires in the first sentence.
- forbidden_before_verify: words or topics not to say before identity is verified.
- multi_product (bool), max_products (int).
- retries: {{tool name: extra attempts after a failure}} as the brief or the API allows.
- ask_limits: {{"right_party", "id_decline", "callback_reask", "vague_date", "stop_talking", "human_offer"}} counts.
  Each count has its OWN meaning, do not mix them: right_party = the TOTAL number of times the agent asks the right-person question
  (the first ask plus every repeat: "ask once more" -> 2; a brief that never repeats it -> 1). id_decline = how many times the agent
  re-asks after the FIRST refusal of an identity question (the explanation is said once on the first refusal); the call closes on
  refusal number id_decline + 1 ("a second refusal closes" -> 1). callback_reask, vague_date = how many times the agent asks again
  for a callback time / for the exact day ("ask once for another time" -> 1).
- routes: {{"human_before_verify": false}} only when the brief says a request for a person must not be honoured before identity is verified (quote it in extra_rules).
- vague_date_offer: "deadline" | "attempt1" | "other": which date to offer when the customer gives no exact day (null if the brief is silent).
- unused_tools: tools the brief never uses.
- intents: for each of hardship, dispute, paid_already, restructuring, discount, human, stop_talking, refusal,
  callback_request that the brief has: {{"definition": the brief's own definition, "examples": 2-3 phrases the brief
  itself uses (or close paraphrases), "near_misses": 1-2 phrases the brief says are NOT that intent (empty list when
  the brief says none; never invent one), "_source": quote}}. Leave out an intent the brief does not mention.
- extra_rules: [{{"statement", "brief_quote", "stage": opening|third_party|identity|debt|negotiate|close|global}}]
  for every brief rule that the keys above do not capture. Short, concrete, in the brief's terms."""

PASS1 = RULES + "\n\nRead the whole brief. Fill every key."
PASS2 = RULES + """

You are given a draft policy.json. Check EVERY field against the brief and the tool API: values, enum spellings,
holiday dates, band limits, attempt counts, callback hours, which sentences are verbatim, overrides by later material.
Fix every error and add a quote to "_source" for each field you checked. Return the complete corrected policy.json."""
READING_B = RULES + """

Read the brief in this order. First write "_sections": one short line per section, message or memo of the brief
material, in order (later material overrides earlier material). Then fill the schema from them. For every value you
set, copy the exact sentence of the brief that states it into "_source" (pointer -> sentence); a value without such a
sentence is null / empty."""
VERIFY = """You settle disagreements between two readings of a bank's brief for a collections call. For each field below
you get the two candidate values with the quote each reading gave. Decide which value the brief supports (or give the
right one if neither is). Later material in the brief overrides earlier material. Answer every field with the value and
a quote copied EXACTLY from the brief that states it. Never use an example value. Reply {"answers": [{"pointer",
"value", "quote"}]}."""
FIX = RULES + """

A deterministic checker found the violations listed below in policy.json. Fix each one using the brief and the tool
API (an enum value must be one the API lists; a quote must be copied exactly from the brief). Return the complete
corrected policy.json."""


CRITICAL = ("/multi_product", "/max_products", "/identity/questions", "/identity/max_wrong", "/bands", "/holidays",
            "/callback", "/outcome_map", "/queue_map", "/note_map", "/close_rule", "/tools/create_promise/amount_unit",
            "/tools/schedule_callback", "/tools/close_call")
VERIFY_SCHEMA = {"type": "object", "required": ["answers"], "properties": {"answers": {"type": "array", "items": {
    "type": "object", "required": ["pointer", "value", "quote"],
    "properties": {"pointer": {"type": "string"}, "quote": {"type": "string"}}}}}}
QUOTE_MSG = "the number/date/time in _source differs from the value (fix the value or quote the right sentence)"


# whether a persuasion date is capped at the deadline, and how many times a refused security question is explained
CRITICAL_RE = (re.compile(r"^/attempt_plan/\d+/cap_at_deadline$"), re.compile(r"^/ask_limits/id_decline$"))


def is_critical(ptr: str) -> bool:
    return any(ptr == c or ptr.startswith(c + "/") for c in CRITICAL) or any(r.match(ptr) for r in CRITICAL_RE)


def _get(doc, ptr):
    return extract_vote._get(doc, ptr)


def _set(doc, ptr, value):
    """Set a pointer, creating a missing last key (dict) or leaving an out-of-range list index alone."""
    try:
        policy_check.set_pointer(doc, ptr, value)
    except KeyError:
        parent, last = ptr.rsplit("/", 1)
        target = _get(doc, parent) if parent else doc
        if isinstance(target, dict):
            target[last.replace("~1", "/").replace("~0", "~")] = value
    except (IndexError, ValueError, TypeError):
        pass


def _user(brief: str, api_text: str, extra: str = "") -> str:
    return f"# Brief\n\n{brief}\n\n# Tool API (summary)\n\n{api_text}\n" + (f"\n{extra}\n" if extra else "")


def _short(v) -> str:
    t = json.dumps(v, ensure_ascii=False)
    return t if len(t) <= 80 else t[:77] + "..."


def read_and_vote(brief: str, prepared: str, api_text: str, call_llm, model, trace, deadline, out_dir=None):
    """Readings A and B, the merge, the verifier. Returns (merged policy, pins {pointer: (value, quote)})."""
    kw = dict(json_schema=SCHEMA, temperature=0, max_tokens=16000, effort="high", trace=trace, deadline=deadline)
    a = call_llm(PASS1, _user(prepared, api_text), model, purpose="extract reading A: brief -> policy.json", **kw)
    b = call_llm(READING_B, _user(prepared, api_text), model, purpose="extract reading B: sections, then values with quotes", **kw)
    b.pop("_sections", None)
    if out_dir:
        (out_dir / "policy.pass1.json").write_text(json.dumps(a, indent=1, ensure_ascii=False), encoding="utf-8")
        (out_dir / "policy.readingB.json").write_text(json.dumps(b, indent=1, ensure_ascii=False), encoding="utf-8")
    merged, votes = extract_vote.merge([a, b], brief)
    agree = sum(v == "2/2" for v in votes.values())
    disputed = [ptr for ptr, v in votes.items() if v != "2/2"]
    crit = [ptr for ptr in disputed if is_critical(ptr)]
    trace.line(f"extract vote: {agree} field(s) agree, {len(disputed)} disagree ({len(crit)} critical)")
    pins: dict = {}
    if crit:
        items = []
        for ptr in crit[:60]:
            qa, qb = extract_vote._quote_for(a, ptr)[1], extract_vote._quote_for(b, ptr)[1]
            items.append({"pointer": ptr, "reading_A": {"value": _get(a, ptr) if _get(a, ptr) is not extract_vote.ABSENT else None, "quote": qa},
                          "reading_B": {"value": _get(b, ptr) if _get(b, ptr) is not extract_vote.ABSENT else None, "quote": qb}})
        ans = call_llm(VERIFY, f"# Brief\n\n{prepared}\n\n# Disputed fields\n\n" + json.dumps(items, ensure_ascii=False),
                       model, json_schema=VERIFY_SCHEMA, temperature=0, max_tokens=8000, effort="high", trace=trace,
                       purpose=f"extract verifier: {len(items)} disputed critical field(s)", deadline=deadline)
        given = {x.get("pointer"): x for x in (ans.get("answers") or []) if isinstance(x, dict)}
        for it in items:
            ptr, x = it["pointer"], given.get(it["pointer"])
            if x and policy_check.quote_in(str(x.get("quote") or ""), brief) and "value" in x:
                _set(merged, ptr, x["value"])
                merged.setdefault("_source", {})[ptr] = x["quote"]
                pins[ptr] = (x["value"], x["quote"])
                trace.line(f"extract field {ptr}: verified {_short(x['value'])} (A={_short(it['reading_A']['value'])}, B={_short(it['reading_B']['value'])})")
            else:
                av = _get(a, ptr)
                if av is not extract_vote.ABSENT:
                    _set(merged, ptr, av)
                trace.line(f"extract field {ptr}: fallback to reading A {_short(it['reading_A']['value'])}"
                           f" (verifier quote {'missing' if not x else 'not found in the brief'})")
        for ptr in crit[60:]:  # beyond the cap: reading A
            av = _get(a, ptr)
            if av is not extract_vote.ABSENT:
                _set(merged, ptr, av)
            trace.line(f"extract field {ptr}: fallback to reading A (over the verifier cap)")
    for ptr in disputed:
        if ptr not in crit:
            trace.line(f"extract field {ptr}: merged by quote/reading order")
    return merged, pins


def apply_rules(policy: dict, brief: str, pins: dict, trace) -> None:
    for ptr, (value, quote) in pins.items():  # a later pass must not undo a verified value
        if _get(policy, ptr) != value:
            _set(policy, ptr, value)
    note = policy_check.apply_multi_product_rule(policy, brief)
    if note:
        trace.line("extract rule: " + note)
    for c in ([] if os.environ.get("TCC_NO_FIXUPS") else policy_fixups.apply(policy, brief)):  # TCC_NO_FIXUPS=1: measure the raw extraction  # id_decline / cap_at_deadline from explicit brief phrases (value + quote)
        trace.line(f"extract fixup {c['field']}: {c['before']!r} -> {c['after']!r} ({c['quote'][:80]})")


def extract(brief: str, api, call_llm, model: str, trace, deadline: float | None = None, out_dir=None) -> dict:
    """Returns {"policy": final, "pass1": merged readings, "violations": [...], "repairs": [...]}. `brief` stays the
    reference for quote checks; the readings see brief_prep.prepare(brief)."""
    api_text = api.render()
    prepared = brief_prep.prepare(brief)
    if prepared != brief:
        trace.line("extract: brief_prep reordered / marked the brief material")
    p1, pins = read_and_vote(brief, prepared, api_text, call_llm, model, trace, deadline, out_dir)
    apply_rules(p1, brief, pins, trace)
    p2 = call_llm(PASS2, _user(prepared, api_text, "# Draft policy.json\n\n" + json.dumps(p1, ensure_ascii=False)), model,
                  json_schema=SCHEMA, temperature=0, max_tokens=16000, effort="high", trace=trace,
                  purpose="extract pass 2: check every field against the brief", deadline=deadline)
    p2.pop("_sections", None)
    apply_rules(p2, brief, pins, trace)
    policy = policy_check.complete(p2, api)
    violations = policy_check.check(policy, api, brief)
    qm = [{"path": ptr, "msg": QUOTE_MSG} for ptr in extract_vote.quote_mismatches(policy)]
    trace.check("policy_check after pass 2", not violations and not qm, f"{len(violations)} violation(s), {len(qm)} quote/number mismatch(es)")
    first = list(violations) + qm
    if first:
        listing = "\n".join(f"- {v['path']}: {v['msg']}" for v in first)
        fixed = call_llm(FIX, _user(prepared, api_text, "# policy.json\n\n" + json.dumps(policy, ensure_ascii=False)
                                    + "\n\n# Violations\n\n" + listing), model, json_schema=SCHEMA, temperature=0,
                         max_tokens=16000, effort="high", trace=trace,
                         purpose=f"extract fix round: {len(first)} violation(s)", deadline=deadline)
        fixed.pop("_sections", None)
        apply_rules(fixed, brief, pins, trace)
        policy = policy_check.complete(fixed, api)
        violations = policy_check.check(policy, api, brief)
        trace.check("policy_check after fix round", not violations, f"{len(violations)} violation(s)")
    repairs: list[str] = []
    if violations:
        policy, repairs = policy_check.repair(policy, violations, api)
        policy = policy_check.complete(policy, api)
        for r in repairs:
            trace.line(f"policy default: {r}")
        left = [v for v in policy_check.check(policy, api, brief) if not v["path"].startswith("/_source/")]
        trace.check("policy_check after defaults", not left, f"{len(left)} violation(s) left")
    return {"policy": policy, "pass1": p1, "violations": first, "repairs": repairs}
