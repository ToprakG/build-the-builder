"""Self-test scenarios for ONE unseen brief: brief + extracted policy.json -> selftest/cases.json.

    .venv310/bin/python scripts/selftest_cases.py <brief file>... <policy.json> [--out selftest/cases.json] [--max 12]

Each scenario is a case in the vendor format (so mockbank, score_ledger and persona_for work unchanged):
  persona text   `script`   written by ONE claude_cli:sonnet call from facts that the code fixes (names, answers, amounts, dates)
  account data   `products` / `identity` / `variables`, generated from policy.json (field names, floor field, bands, holidays)
  ledger         `expected`, computed from policy.json: outcome_map, queue_map, note_map, notes_on_restructuring
  success        `checks`   ONLY sentences of the brief: every criterion carries an exact `brief_quote` that is verified against the
                 brief text; a criterion without a verifiable quote is dropped. Fallback: the policy's own `_source` quotes.
A scenario exists only when the policy has its rule type (outcome_map entry, multi_product, sentences.ai ...). No testbed
file is read: the scenarios come from the brief and from the policy extracted from it.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from builder.template_cc import io as tio  # noqa: E402
from builder.template_cc import llm  # noqa: E402
from builder.template_cc.policy_check import quote_in  # noqa: E402
from builder.template_cc.testgen import synth_now_dates  # noqa: E402

MODEL = os.environ.get("TCC_SELFTEST_MODEL") or "claude_cli:sonnet"  # TCC_LLM_PROVIDER (llm.py) overrides the provider of this string
FIRST = [("Daniel", "Reyes", "Mr"), ("Maria", "Costa", "Ms"), ("Owen", "Park", "Mr"), ("Lena", "Hoffmann", "Ms"),
         ("Samir", "Okafor", "Mr"), ("Ingrid", "Solheim", "Ms"), ("Tomas", "Varga", "Mr"), ("Aiko", "Mendes", "Ms"),
         ("Pavel", "Lindqvist", "Mr"), ("Nadia", "Rahman", "Ms"), ("Hugo", "Brandt", "Mr"), ("Clara", "Mercier", "Ms")]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
TEXT_WORDS = ["Willow", "Harbour", "Meadow", "Falcon", "Juniper", "Copper"]

# rule type -> (policy pointers whose _source quotes back it; used when the model gives no verifiable criterion)
SOURCES = {
    "identity_declined": ["/outcome_map/nv_declined", "/ask_limits/id_decline", "/identity"],
    "identity_failed": ["/outcome_map/nv_failed", "/identity/max_wrong", "/sentences/failed_id"],
    "robot_turn1": ["/sentences/ai", "/lines/ai", "/sentences/opening"],
    "robot_mid": ["/sentences/ai", "/lines/ai"],
    "human_then_pay": ["/ask_limits/human_offer", "/outcome_map/tr_human", "/queue_map/human", "/routes"],
    "hardship": ["/outcome_map/np_hardship", "/note_map/hardship", "/intents/hardship", "/lines/hardship_sympathy"],
    "vague_date": ["/ask_limits/vague_date", "/vague_date_offer", "/bands"],
    "pieces_identity": ["/identity", "/identity/questions"],
    "floor_amount": ["/product_types", "/attempt_plan", "/attempt1"],
    "dispute": ["/outcome_map/np_dispute", "/intents/dispute", "/note_map/dispute"],
    "already_paid": ["/outcome_map/paid", "/intents/paid_already", "/lines/paid_reply"],
    "restructuring": ["/outcome_map/tr_restructuring", "/queue_map/restructuring", "/intents/restructuring", "/notes_on_restructuring"],
    "deceased": ["/outcome_map/nr_deceased", "/lines/condolence"],
    "multi_product": ["/multi_product", "/close_rule", "/max_products"],
    "third_party": ["/outcome_map/nr_callback", "/callback", "/ask_limits/right_party"],
    "wrong_number": ["/outcome_map/nr_wrong_number"],
    "right_party": ["/ask_limits/right_party", "/outcome_map/nr_unclear", "/lines/ask_again", "/lines/right_party_question"],
}
PRIORITY = ["identity_declined", "identity_failed", "right_party", "robot_turn1", "dispute", "floor_amount", "vague_date", "hardship",
            "human_then_pay", "third_party", "restructuring", "pieces_identity", "already_paid", "deceased", "multi_product", "robot_mid",
            "wrong_number"]  # identity refusal/failed, right party, robot, dispute, floor, deadline (vague date), hardship, transfer/human, callback first


# --- small helpers ---------------------------------------------------------------------------------------------------
def money(x):
    return f"{x:,.2f}".replace(".00", "")


def date_words(iso):
    d = dt.date.fromisoformat(iso[:10])
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def is_bd(d, hol):
    return d.weekday() < 5 and d.isoformat() not in hol


def add_bd(d, n, hol):
    while n > 0:
        d += dt.timedelta(days=1)
        if is_bd(d, hol):
            n -= 1
    return d


def kind_of(policy, field):
    k = ((policy.get("identity") or {}).get("kinds") or {}).get(field)
    if k:
        return k
    f = field.lower()
    if "year" in f:
        return "year"
    if "date" in f or "birth" in f or "dob" in f:
        return "date"
    if "last4" in f or "digit" in f or "pin" in f:
        return "digits"
    return "code" if any(w in f for w in ("code", "number", "postcode", "zip", "id")) else "text"


def gen_value(kind, rng, avoid=None):
    for _ in range(100):
        if kind == "date":
            v = dt.date(rng.randint(1950, 2003), rng.randint(1, 12), rng.randint(1, 28)).isoformat()
        elif kind == "year":
            v = str(rng.randint(1950, 2003))
        elif kind == "digits":
            v = f"{rng.randint(0, 9999):04d}"
        elif kind == "text":
            v = rng.choice(TEXT_WORDS)
        else:
            v = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ23456789") for _ in range(6))
        if v != avoid and not (kind == "date" and avoid and v[:4] == avoid[:4] and v[5:7] == avoid[8:10] and v[8:10] == avoid[5:7]):
            return v
    return v


def say_identity(kind, v):
    if kind == "date":
        return date_words(v)
    return v


class Ctx:
    def __init__(self, policy, seed):
        self.p, self.rng = policy, random.Random(f"selftest-{seed}")
        self.hol = set(policy.get("holidays") or [])
        self.nows = synth_now_dates(policy, seed)
        self.n = 0
        self.names = list(FIRST)
        self.rng.shuffle(self.names)
        self.af = policy["account_fields"]
        fmt = str(policy.get("product_id_format") or "")
        self.pid_fmt = fmt if "{n}" in fmt else "p{n}"

    def band(self, days):
        for b in self.p.get("bands") or []:
            if b["min_days"] <= days <= b["max_days"]:
                return b
        return (self.p.get("bands") or [{}])[-1]

    def product(self, ptype=None, band_idx=0):
        types = self.p["product_types"]
        key = ptype or sorted(types)[0]
        t = types[key]
        bands = self.p.get("bands") or [{"min_days": 3, "max_days": 9}]
        b = bands[min(band_idx, len(bands) - 1)]
        days = self.rng.randint(b["min_days"], min(b["max_days"], b["min_days"] + 6))
        past_due = float(self.rng.randrange(300, 1200, 5))
        floor = round(round(past_due * 0.25 * 2) / 2, 2)
        row = {self.af["type"]: t["enum"], self.af["past_due"]: past_due, self.af["days_late"]: days, t["floor_field"]: floor}
        if t.get("last4_field"):
            row[t["last4_field"]] = f"{self.rng.randint(0, 9999):04d}"
        label = t["label"].format(last4=row.get(t.get("last4_field") or "", ""))
        return {"row": row, "past_due": past_due, "floor": floor, "days": days, "label": label, "type_key": key}

    def new_case(self, rule, title, product_list, now_idx=None):
        self.n += 1
        first, last, ttl = self.names[self.n % len(self.names)]
        now = self.nows[(now_idx if now_idx is not None else self.n) % len(self.nows)]
        qs = (self.p.get("identity") or {}).get("questions") or []
        ident = {q: gen_value(kind_of(self.p, q), self.rng) for q in qs}
        variables = {"first_name": first, "last_name": last, "title": ttl, "now": now}
        for v in self.p.get("call_variables") or []:
            variables.setdefault(v, v + "-selftest") if v != "customer_id" else None
        return {"id": f"ST-{self.n:02d}", "brief": "selftest", "family": rule, "rule": rule, "level": "selftest", "title": title,
                "variables": variables, "identity": ident, "products": [p["row"] for p in product_list], "_p": product_list,
                "source": "tcc-selftest"}

    def day_after_now(self, case, n=2):
        """A business day that is certainly inside the deadline of every band (n <= deadline_business_days)."""
        today = dt.date.fromisoformat(case["variables"]["now"][:10])
        lim = min([b.get("deadline_business_days") or 1 for b in self.p.get("bands") or [{}]] + [n])
        return add_bd(today, max(1, lim), self.hol).isoformat()


def outcome(policy, key):
    v = (policy.get("outcome_map") or {}).get(key)
    return (v[0], v[1]) if v else None


def expected(policy, key, promises=(), callbacks=(), transfer_key=None, note_keys=()):
    o = outcome(policy, key)
    nm = policy.get("note_map") or {}
    notes = [{"product_id": "p1", "reason": nm[k]} for k in note_keys if nm.get(k)]
    queue = (policy.get("queue_map") or {}).get(transfer_key) if transfer_key else None
    return {"outcome": o[0], "reason": o[1], "promises": list(promises), "notes": notes, "callbacks": list(callbacks),
            "transfers": [{"queue": queue}] if queue else []}


# --- scenario skeletons: (facts for the persona writer, expected ledger) ---------------------------------------------------
def unknown_skeletons(cx, extra_rules):
    """One judge-only scenario per brief rule that no rule type covers (rule_coverage UNKNOWN / prompt-only): {statement, brief_quote}."""
    S = []
    for i, er in enumerate(extra_rules, 1):
        c = cx.new_case("unknown_rule", f"Triggers a brief rule: {er['statement'][:60]}", [cx.product()])
        p = c["_p"][0]
        d = cx.day_after_now(c)
        q = (cx.p.get("identity") or {}).get("questions") or []
        ans = "; ".join(f"{x.replace('_', ' ')}: {say_identity(kind_of(cx.p, x), c['identity'][x])}" for x in q)
        c.update({"facts": f"Verified ({ans}). The customer behaves so that THIS brief rule applies to the agent: \"{er['statement']}\" "
                           f"(brief: \"{er['brief_quote']}\"). Otherwise they can pay {money(p['past_due'])} on {date_words(d)} if the agent gets that far.",
                  "expected": expected(cx.p, "promise", [{"product_id": cx.pid_fmt.format(n=1), "amount": p["past_due"], "date": d}]) if outcome(cx.p, "promise") else
                  {"outcome": "x", "reason": None, "promises": [], "notes": [], "callbacks": [], "transfers": []},
                  "style": None, "ledger": "judge", "allow": [], "pid": cx.pid_fmt.format(n=1), "unknown": True, "quotes": [er["brief_quote"]]})
        S.append(c)
    return S


def skeletons(policy, cx, only_present=True):
    S, om = [], policy.get("outcome_map") or {}
    pid = cx.pid_fmt.format(n=1)

    def promise(c, amount, date):
        return [{"product_id": pid, "amount": amount, "date": date}]

    def mk(rule, title, c, facts, exp, style=None, ledger="strict", allow=None, **extra):
        c["_p"] = [dict(p) for p in c["_p"]]
        c.update({"title": title, "facts": facts, "expected": exp, "style": style, "ledger": ledger, "allow": allow or [],
                  "pid": pid, **extra})
        S.append(c)

    def answers(c, how="gives every answer when asked"):
        q = (policy.get("identity") or {}).get("questions") or []
        return "; ".join(f"{x.replace('_', ' ')}: {say_identity(kind_of(policy, x), c['identity'][x])}" for x in q) + f" ({how})"

    def basic(rule, title):
        c = cx.new_case(rule, title, [cx.product()])
        return c, c["_p"][0]

    if outcome(policy, "promise"):
        # spoken identity answers in pieces (long numbers/dates in two parts), then a plain promise for the full amount
        c, p = basic("pieces_identity", "Gives the identity answers in pieces and pays the full amount")
        d = cx.day_after_now(c)
        mk("pieces_identity", c["title"], c, f"Verified: {answers(c, 'says each long answer in two parts, first half, then the rest when asked')}. "
           f"Says they can pay the full overdue amount ({money(p['past_due'])}) on {date_words(d)} and confirms the read-back.",
           expected(policy, "promise", promise(c, p["past_due"], d)), style="pieces")
        if (policy.get("sentences") or {}).get("ai") or (policy.get("lines") or {}).get("ai"):
            c, p = basic("robot_turn1", "Asks at the very first turn whether this is a robot, then pays")
            d = cx.day_after_now(c)
            mk("robot_turn1", c["title"], c, f"Their very first words are the question 'am I talking to a robot?'. After the answer they "
               f"confirm they are the person, {answers(c)}, then agree to pay the full amount ({money(p['past_due'])}) on {date_words(d)}.",
               expected(policy, "promise", promise(c, p["past_due"], d)))
            c, p = basic("robot_mid", "Asks mid-call whether this is a recording or a robot, then pays")
            d = cx.day_after_now(c)
            mk("robot_mid", c["title"], c, f"Confirms who they are and {answers(c)}. Once the amount is mentioned, asks 'wait, is this a real "
               f"person or a robot?'. Accepts the answer and agrees to pay the full amount ({money(p['past_due'])}) on {date_words(d)}.",
               expected(policy, "promise", promise(c, p["past_due"], d)))
        c, p = basic("vague_date", "Gives only a vague date first, then an exact one")
        d = cx.day_after_now(c)
        mk("vague_date", c["title"], c, f"Verified: {answers(c)}. Can pay the full amount ({money(p['past_due'])}). When asked when, first says "
           f"only 'sometime next week'. If asked for an exact day, gives {date_words(d)}. Confirms the read-back.",
           expected(policy, "promise", promise(c, p["past_due"], d)))
        c, p = basic("floor_amount", "Offers less than the minimum, then accepts the minimum")
        d = cx.day_after_now(c)
        low = round(p["floor"] / 2, 2)
        mk("floor_amount", c["title"], c, f"Verified: {answers(c)}. Says they cannot pay the full amount and first offers only {money(low)}. "
           f"When told that is not enough, accepts the minimum of {money(p['floor'])} on {date_words(d)}. Confirms the read-back.",
           expected(policy, "promise", promise(c, p["floor"], d)))
        if outcome(policy, "tr_human") and (policy.get("queue_map") or {}).get("human"):
            c, p = basic("human_then_pay", "Asks for a human, then agrees to pay")
            d = cx.day_after_now(c)
            offers = (policy.get("ask_limits") or {}).get("human_offer", 0)
            exp = expected(policy, "promise", promise(c, p["past_due"], d)) if offers else expected(policy, "tr_human", transfer_key="human")
            mk("human_then_pay", c["title"], c, f"Verified: {answers(c)}. After hearing the amount says 'I want to speak to a person'. "
               f"If the agent offers to carry on, they agree to pay the full amount ({money(p['past_due'])}) on {date_words(d)}. "
               "If the agent only transfers, they accept the transfer.", exp)
    if outcome(policy, "np_hardship"):
        c, p = basic("hardship", "Lost their job and names their own terms")
        d = cx.day_after_now(c, 3)
        half = round(p["floor"] / 2, 2)
        allow = [e for e in (expected(policy, "np_hardship", note_keys=("hardship",)),
                             expected(policy, "promise", promise(c, half, d)),
                             expected(policy, "tr_support", transfer_key="support") if outcome(policy, "tr_support") else None) if e]
        mk("hardship", c["title"], c, f"Verified: {answers(c)}. Says they lost their job this month and cannot pay what is asked. "
           f"Proposes their own terms: {money(half)} on {date_words(d)} and nothing more. Does not give up the proposal unless the agent offers something clearly better.",
           allow[0], ledger="loose", allow=allow)
    if outcome(policy, "np_dispute"):
        c, p = basic("dispute", "Disputes that the debt is theirs")
        mk("dispute", c["title"], c, f"Verified: {answers(c)}. Says this debt is not right, they never agreed to these charges, and they dispute it. "
           "Does not agree to pay anything.", expected(policy, "np_dispute", note_keys=("dispute",)))
    if outcome(policy, "paid"):
        c, p = basic("already_paid", "Says the amount was already paid")
        mk("already_paid", c["title"], c, f"Verified: {answers(c)}. Says they already paid the overdue amount last week and will not pay again. "
           "Does not offer a new payment.", expected(policy, "paid", note_keys=("paid",)))
    if outcome(policy, "tr_restructuring") and (policy.get("queue_map") or {}).get("restructuring"):
        c, p = basic("restructuring", "Asks to restructure the loan")
        nk = (policy.get("notes_on_restructuring"),) if policy.get("notes_on_restructuring") else ()
        mk("restructuring", c["title"], c, f"Verified: {answers(c)}. Says the payments are too high and asks to restructure or reschedule the whole "
           "loan with someone who can change the terms. Accepts a transfer.",
           expected(policy, "tr_restructuring", transfer_key="restructuring", note_keys=nk))
    if outcome(policy, "nr_deceased"):
        c, p = basic("deceased", "The person who answers says the customer has died")
        mk("deceased", c["title"], c, "Is NOT the customer: the person who answers says the customer died a few months ago and asks the caller "
           "to stop calling. Gives no security answers.", expected(policy, "nr_deceased"), third_party=True)
    if outcome(policy, "nr_wrong_number"):
        c, p = basic("wrong_number", "Wrong number")
        mk("wrong_number", c["title"], c, "Is NOT the customer: says the caller has the wrong number and nobody of that name lives or works there. "
           "Gives no security answers.", expected(policy, "nr_wrong_number"), third_party=True)
    if outcome(policy, "nr_callback"):
        c, p = basic("third_party", "A relative answers and names a callback time")
        cb = policy.get("callback") or {"start": "09:00", "end": "17:00"}
        h = min(int(cb["start"][:2]) + 1, int(cb["end"][:2]) - 1)
        t = f"{h:02d}:00"
        d = add_bd(dt.date.fromisoformat(c["variables"]["now"][:10]), 1, cx.hol).isoformat()
        mk("third_party", c["title"], c, f"Is NOT the customer (a relative who answered). Asks what the call is about and offers to pay for them. "
           f"Says the customer can be reached on {date_words(d)} at {t} (say the time as {h}:00). Gives no security answers.",
           expected(policy, "nr_callback", callbacks=[{"date": d, "time": t}]), third_party=True)
    if outcome(policy, "nr_unclear"):
        c, p = basic("right_party", "Never clearly says who they are")
        mk("right_party", c["title"], c, "Answers the first question only with 'hello?'. When asked again says 'who is this?'. If the agent explains and asks "
           "once more, says only 'who is this, what do you want?' and never says yes, that is me or their name. Gives no security answers.",
           expected(policy, "nr_unclear"), third_party=True)
    if outcome(policy, "nv_declined"):
        c, p = basic("identity_declined", "Refuses the security questions twice")
        mk("identity_declined", c["title"], c, "Confirms the name. Is asked for the first security answer and refuses ('I do not give that out on the "
           "phone'). After the agent explains why it is needed, refuses again. Never gives a security answer and ends the call.",
           expected(policy, "nv_declined"))
    if outcome(policy, "nv_failed"):
        c, p = basic("identity_failed", "Wrong answers until the check is over")
        mx = (policy.get("identity") or {}).get("max_wrong", 3)
        q = (policy.get("identity") or {}).get("questions") or []
        wrong = [say_identity(kind_of(policy, q[i % len(q)]), gen_value(kind_of(policy, q[i % len(q)]), cx.rng, c["identity"][q[i % len(q)]]))
                 for i in range(mx)]
        mk("identity_failed", c["title"], c, f"Confirms the name. Every security answer is wrong: " + "; ".join(f"{q[i % len(q)].replace('_', ' ')}: {w}" for i, w in enumerate(wrong)) +
           f" ({mx} wrong answers in total). Gets annoyed and asks how much is owed.", expected(policy, "nv_failed"))
    if policy.get("multi_product") and outcome(policy, "promise") and (policy.get("max_products") or 1) >= 2:
        types = sorted(policy["product_types"])
        c = cx.new_case("multi_product", "Two overdue products", [cx.product(types[0]), cx.product(types[-1], 1)])
        pa, pb = c["_p"]
        d = cx.day_after_now(c)
        allow = [expected(policy, "promise", promise(c, pa["past_due"], d))]
        mk("multi_product", c["title"], c, f"Verified: {answers(c)}. Has two overdue products: {pa['label']} ({money(pa['past_due'])} overdue) and "
           f"{pb['label']} ({money(pb['past_due'])} overdue). Agrees to pay the full amount of the FIRST product the agent mentions on {date_words(d)}, "
           "and says the other one will be dealt with later.", allow[0], ledger="loose", allow=allow)
    rank = {r: i for i, r in enumerate(PRIORITY)}
    S.sort(key=lambda c: rank.get(c["rule"], 99))
    return S


# --- the persona writer + brief-quoted criteria ----------------------------------------------------------------------------
SCHEMA = {"type": "object", "required": ["scenarios"], "properties": {"scenarios": {"type": "array", "items": {
    "type": "object", "required": ["id", "script", "criteria"], "properties": {
        "id": {"type": "string"}, "script": {"type": "string"},
        "criteria": {"type": "array", "items": {"type": "object", "required": ["criterion", "brief_quote"], "properties": {
            "criterion": {"type": "string"}, "brief_quote": {"type": "string"}}}}}}}}}
SYSTEM = """You prepare self-test scenarios for a phone agent that was built from the BRIEF below. A simulated customer will phone-play each
scenario, and a judge will grade the agent with your criteria. For EVERY scenario in the list, write:
- "script": what the customer does and says in this call, in third person, 2-5 short sentences, using ONLY the facts given for the
  scenario (names, answers, amounts, dates). Add no new amounts, dates or facts. The customer never reveals a rule of the brief.
- "criteria": 2-4 items {"criterion", "brief_quote"}. "criterion" is one observable thing the AGENT must do or avoid in this exact call,
  as a short sentence; it must follow from the brief and must be checkable from the conversation. "brief_quote" is an EXACT span copied
  from the brief (5-40 words) that states that rule. Write no criterion that you cannot back with an exact brief sentence. Do not
  criticise style; do not invent rules; if the brief is silent on a point, leave the point out."""


def write_personas(brief, cases, trace_dir):
    payload = [{"id": c["id"], "rule_type": c["rule"], "facts": c["facts"], "expected_close": c["expected"]["outcome"],
                "expected_reason": c["expected"]["reason"]} for c in cases]
    user = f"# BRIEF\n\n{brief}\n\n# SCENARIOS\n\n{json.dumps(payload, indent=1)}"
    last = None
    for _ in range(2):
        try:
            return llm.call_llm(SYSTEM, user, MODEL, json_schema=SCHEMA, purpose="selftest scenarios", attempts=2)
        except Exception as e:  # noqa: BLE001
            last = e
    print(f"WARNING: the persona writer failed ({last!r}); using the facts as scripts and the policy quotes as criteria", file=sys.stderr)
    return {"scenarios": []}


def fallback_quotes(policy, rule, brief):
    src = policy.get("_source") or {}
    out = []
    for ptr in SOURCES.get(rule, []):
        for k, q in src.items():
            if (k == ptr or k.startswith(ptr + "/")) and q and quote_in(str(q), brief) and q not in out:
                out.append(q)
    return out[:3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="brief file(s) then policy.json")
    ap.add_argument("--out", default="selftest/cases.json")
    ap.add_argument("--max", type=int, default=0, help="0 = no cap: one scenario per rule type present (the default)")
    ap.add_argument("--extra", type=int, default=0, help="extra variants (other speaking style) on top of the one-per-rule-type set, when time allows")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--extra-rules", help="JSON list of {statement, brief_quote}: brief rules no rule type covers; one judge-only scenario each")
    a = ap.parse_args()
    *brief_files, pol = a.inputs
    t0 = time.time()
    policy = json.loads(Path(pol).read_text(encoding="utf-8"))
    brief = tio.brief_text(tio.read_briefs([Path(b) for b in brief_files]))
    try:  # S3's rule coverage, once it exists: restrict to the rule types it finds in the brief
        sys.path.insert(0, str(REPO / "scripts"))
        import rule_coverage  # noqa: PLC0415
        present = set(rule_coverage.present_rule_types(brief, policy))
    except Exception:  # noqa: BLE001
        present = None
    cx = Ctx(policy, a.seed)
    sk = [c for c in skeletons(policy, cx) if present is None or c["rule"] in present]
    if a.max:
        sk = sk[:a.max]
    for c, style in zip([c for c in sk if c.get("ledger") == "strict" and not c.get("third_party")][:a.extra], ["in_words", "talkative", "corrects", "asks_back"] * 4):
        v = cx.new_case(c["rule"], c["title"] + f" [{style}]", [])
        v.update({k: copy.deepcopy(x) for k, x in c.items() if k not in ("id", "title", "style")})
        v.update({"title": c["title"] + f" [{style}]", "style": style, "variant_of": c["id"]})
        sk.append(v)
    if a.extra_rules:
        sk += unknown_skeletons(cx, json.loads(Path(a.extra_rules).read_text()))
    for i, c in enumerate(sk, 1):
        c["id"] = f"ST-{i:02d}"
    if len(sk) < 8:
        print(f"WARNING: only {len(sk)} scenarios are possible for this policy (fewer rule types present)", file=sys.stderr)
    made = {s["id"]: s for s in write_personas(brief, sk, None).get("scenarios", []) if isinstance(s, dict)}
    cases, dropped = [], 0
    for c in sk:
        m = made.get(c["id"]) or {}
        crit, seen = [], set()
        for x in m.get("criteria") or []:
            if isinstance(x, dict) and quote_in(str(x.get("brief_quote") or ""), brief) and x.get("criterion") and x["criterion"] not in seen:
                crit.append({"criterion": x["criterion"].strip(), "brief_quote": x["brief_quote"].strip()})
                seen.add(x["criterion"])
            else:
                dropped += 1
        if not crit:
            crit = [{"criterion": f"The agent follows this rule of the brief: \"{q}\"", "brief_quote": q} for q in (c.get("quotes") or fallback_quotes(policy, c["rule"], brief))]
        c["script"] = (m.get("script") or "").strip() or c["facts"]
        c["criteria"] = crit
        c["checks"] = [x["criterion"] for x in crit]
        c.pop("_p", None)
        cases.append(c)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    noc = [c["id"] for c in cases if not c["checks"]]
    print(f"selftest_cases: {len(cases)} scenarios -> {out} in {time.time() - t0:.0f}s; criteria without a brief quote dropped: {dropped}"
          + (f"; NO criteria for {','.join(noc)} (judged by the ledger only)" if noc else ""))
    for c in cases:
        print(f"  {c['id']} {c['rule']:18s} expect {c['expected']['outcome']}/{c['expected']['reason']} ({c['ledger']}), {len(c['checks'])} criteria")


if __name__ == "__main__":
    main()
