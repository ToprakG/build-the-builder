"""Proxy check (an LLM call through claude_cli, NOT Freya): which `side` value do synthetic already-paid / dispute utterances get with the
`side` description of two compiled workflows?   python3 scripts/probe_side.py <workflow A.json> <workflow B.json>   (e.g. p6 and m1 f3)"""
import json
import sys

sys.path.insert(0, ".")
from builder.template_cc import llm  # noqa: E402


def side_description(path):
    raw = open(path).read()
    i = raw.find("What the customer's last message is about")
    k = i
    while True:
        k = raw.find('"', k + 1)
        if raw[k - 1] != "\\":
            break
    return json.loads('"' + raw[i:k] + '"')


PATHS = dict(zip(("p6", "m1"), sys.argv[1:3]))
U = [("already_paid", "I already paid this."), ("already_paid", "it's paid"), ("already_paid", "I paid it off last week."),
     ("already_paid", "I paid that yesterday, check your records."), ("already_paid", "I've already settled this amount, you should have it."),
     ("already_paid", "I paid it off already, why are you calling?"),
     ("dispute", "That's not my debt."), ("dispute", "I never had this card."), ("dispute", "The account is closed."),
     ("dispute", "I closed that account years ago."), ("dispute", "This is fraud, I never opened this."),
     ("mixed", "I paid that off last year, it should be closed. I am not discussing payment."),
     ("mixed", "I already paid it and I won't talk about this any more.")]
SCHEMA = {"type": "object", "required": ["rows"], "properties": {"rows": {"type": "array", "items": {"type": "object", "required": ["i", "side"], "properties": {"i": {"type": "integer"}, "side": {"type": "string"}}}}}}
out = {}
for name in ("p6", "m1"):
    desc = side_description(PATHS[name])
    system = ("You fill ONE field of a phone-call extraction for each customer message below, independently. The field is `side`. "
              "Its description: " + desc + " Allowed values: none, hardship, dispute, paid_already, restructuring, discount, human, stop_talking, refused_1, refused_2, refused_3. "
              "Reply {\"rows\": [{\"i\": index, \"side\": value}]} for every message.")
    user = "\n".join(f"{i}. {u}" for i, (_, u) in enumerate(U))
    runs = []
    for rep in range(2):
        r = llm.call_llm(system, user, "claude_cli:sonnet", json_schema=SCHEMA, purpose=f"side probe {name} {rep}")
        runs.append({x["i"]: x["side"] for x in r["rows"]})
    out[name] = runs
print(f"{'kind':13s} {'utterance':72s} {'p6':>14s} {'m1':>14s}")
for i, (k, u) in enumerate(U):
    f = lambda n: "/".join(sorted({r.get(i, '?') for r in out[n]}))
    print(f"{k:13s} {u[:70]:72s} {f('p6'):>14s} {f('m1'):>14s}")
