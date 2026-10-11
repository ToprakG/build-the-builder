# `out/unit_tests.json`: unit tests for your own workflow

After the build, these tests run on Freya's real engine, on a copy of your workflow. Each test starts the agent
**in a node you name, with variables you set**, gives the customer's next line, and checks the agent's next turn.
Failures come back to you in the fix round. A fixed workflow is kept only if it passes no fewer tests.

Write one test for every **decisive** rule: each route that must fire, each write that must or must not happen, each
limit (attempts, wrong answers, windows, minimums), each fixed sentence the brief requires at a turn. 10 to 25 tests
are typical. They are cheap; a missed route is expensive.

## Format

A JSON list. Every value below is invented; use your brief's own values and your own node ids.

```json
[
  {"name": "refusal goes to the next proposal", "rule": "R31",
   "start_node": "offer",
   "variables": {"offer_text": "For your item ending 1234, could you pay 412.50 by Wednesday 12 March?",
                 "offer_ref": "r1", "offer_item": "0", "offer_step": "0"},
   "messages": [{"role": "assistant", "text": "For your item ending 1234, could you pay 412.50 by Wednesday 12 March?"},
                {"role": "user", "text": "No, I can't do that."}],
   "expect": "The agent does not repeat the proposal and does not record a promise.",
   "checks": [{"var": "offer_answer", "equals": "refused"}, {"node": "next_offer"},
              {"tool": "store_record", "absent": true}]},

  {"name": "last wrong answer ends the check", "rule": "R14",
   "start_node": "check",
   "variables": {"wrong_total": "1"},
   "messages": [{"role": "assistant", "text": "What is your six-digit member code?"},
                {"role": "user", "text": "One two three four five six."}],
   "mock": {"check_party": {"ok": true, "verified": false, "tries_left": 0}},
   "expect": "The agent ends the check without naming any item or amount.",
   "checks": [{"tool": "check_party", "body": {"question": "member_code"}}, {"tool": "load_items", "absent": true}]}
]
```

| Field | Meaning |
|---|---|
| `name` | unique, says what is checked |
| `rule` | the `rules.json` id the test covers |
| `start_node` | the node the agent is in when the customer speaks (a node id of your workflow) |
| `variables` | the variable table at that moment: everything the node's prompt and conditions read (facts, flags, counters) |
| `messages` | the conversation so far; the last one is the customer's line under test |
| `mock` | tool results for this test (`{tool: API body}`), so no real bank is needed. Tests with the same mock run together |
| `expect` | one sentence a judge checks against the agent's turn |
| `checks` | `{"tool": X}` must be called (optionally `"body": {...}`, matched as a subset; `"absent": true` = must not be called); `{"node": N}` must be entered (`"absent": true` = must not); `{"var": V, "equals": value}` must be extracted |

## Required templates (`policy_unit_suite`)

`policy_unit_suite` returns templates only for facts present in your `policy.json`. It does not require an identity
check, a record tool, or any fixed tool list. A policy with no identity check gets no identity template. Templates
that can appear, when the policy has the matching fact, are: a robot question, a scam question, callbacks inside and
outside the window, wrong number, deceased, the last wrong identity answer, a declined identity check, a person
requested, restructuring, hardship, dispute, already paid, a vague date, an amount below the minimum. Each gives the
stage, the customer's line, the checks and the judge
sentence. For **each** template write one test with `"template": "<id>"`: pick your node for that stage, set the
variables your node reads at that point, put the customer's line last (translate it if your brief's language differs,
keep its meaning), and **keep the template's checks** (add more if you like). `check_unit_tests` reports a missing
template, or a dropped check, as an error.

## Derived regression tests

Every `run_local_sims` failure is turned into regression tests by code (`out/derived_unit_tests.json`, not yours to
edit): the failed call's last turn and the first turn where the agent repeated itself, each starting in that turn's
node with that turn's variables. They run on Freya with your tests after the build, before and after the fix.

## Rules

- A test checks **one turn**: the agent's reply to the last customer line, with the tool calls and routing of that
  turn. Start the test in the node where that turn happens, with the variables your code would have set by then.
- Use your workflow's real node ids, variable names and the tool API's field names and enum values.
- Prefer `checks` (exact) over `expect` (judged); use both when a sentence matters.
- Run `check_unit_tests` until it reports 0 errors. It checks node ids, tool names, body fields and mocks offline.
