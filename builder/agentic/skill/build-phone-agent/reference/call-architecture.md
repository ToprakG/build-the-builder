# Call architecture

Tool names in this page are roles. The operationId comes from the tool API (`identity`, `account`, `record`,
`note`, `callback`, `transfer`, `close` in `openapi_summary`). A brief that has no tool for a role has no such stage.

A menu of stages. Keep the stages the documents have, drop the ones they do not, add the ones they have and this page lacks,
and fill every value from the documents and the tool API. Nothing here is a value: words in `<angle brackets>` are slots.
**Where this page and the documents differ, the documents win**: these stages describe a common shape, not the required one.
Include a stage only when the documents require it. Identity is not a default: if the documents say the caller is already
identified, do not ask.

The scoring checks three things on every simulated call: the **bank records** (which tools were called, with which
values, in which order), **policy** (the brief's rules) and **honesty** (nothing claimed that did not happen). So the
design goal is: **every record and every number is decided by code or by a routed state, not by the model's memory.**

## Principles

1. **Deterministic first.** Dates, deadlines, amounts, attempt counts, time windows, item order and the final
   outcome are computed in code nodes and hooks from the call's time variable and the tool results. The model gets finished
   facts as text and picks from them. Never read the system clock.
2. **Route on tool results**, never on what the model claims. A post-hook turns each result into a string state.
3. **One stage per node** where a stage has strict rules (right party, identity, negotiation). Interrupts that can
   happen anywhere are global nodes.
4. **Say less.** One or two short sentences per turn; one question at a time.
5. **Honesty.** Say a record was made only after the tool returned success. Never invent figures, dates, terms or
   reasons. If something is unknown, say so.

## Stage 0: setup (code node, `initial_node`)

- Parse `now` (`parse_now`), the brief's holiday list, today's spoken date.
- Precompute what the early stages need: the callback window as text (today and/or the next business day, the hours,
  "later than now"), the allowed callback days as ISO dates.
- Set pessimistic defaults for the close state (`close_outcome`/`close_reason` or the single code) so a call that ends
  early still closes correctly: usually the brief's "not reached, unclear" row.
- Set every variable a later prompt or condition reads, with a fallback.

## Stage 1: opening and right-party check (conversation)

Skip a right-party check when the documents do not describe one. Speak the opening the documents fix, verbatim.

- `on_enter` **tts** with the brief's opening, word for word where the brief fixes it (whatever disclosures the brief
  requires, the bank's name) and the right-party question with the brief's address form (e.g. `{{title}} {{last_name}}`).
- `system_prompt` + task: nothing about the reason for the call, the product, money or the forbidden words before
  verification. This holds for anyone on the line.
- Extract who answered as an enum: `customer`, `someone_else`, `not_their_line`, `deceased`, `unclear` (only the values
  the brief needs). Only the confirmations the brief lists count as `customer`.
- The brief's ask limit ("ask once more", "answer their question first, the next ask is the second"): a
  `max_node_turns` + `max_turn_transition` to the "unclear" ending, or an extracted counter.
- Robot/AI question and "is this a scam?" are global nodes with the brief's sentence, returning to `__previous__`.

## Stage 1b: someone else / wrong number / deceased

- Someone else: say nothing beyond the opening. Ask when the customer can be reached. Only times inside the computed
  window are bookable (day rule, hours both ends as the brief says, later than `now`). Book with
  the callback tool, in the API's format (separate date and time, or one datetime field). One retry ask, then the
  brief's "no callback" ending.
- Relatives may not answer security questions or hear account details, even when they offer to pay.
- Deceased: the brief's sentence verbatim, nothing else, then its ending. Wrong number: its ending.

## Stage 2: identity (only when the documents check identity)

Skip this stage when the documents do not ask the assistant to check identity, or when they say that check already happened.
When they do ask for it, this is a conversation node whose tool is the identity operation.

- Ask the questions in the documents' order, one at a time. One identity-tool call per answer, with the API's field
  value for that question. Normalise the answer in a pre-hook to the format the brief/API asks for (dates as
  `YYYY-MM-DD`, numbers-only dates read day/month/year in the brief's convention, digits only, upper case, ...).
- A post-hook turns the result into `id_state` (`passed`, `retry`, `failed`) and a `what_to_do_next`:
  correct → next question; wrong → the brief's retry rule; a hint the API returns (e.g. a swapped day/month) → the
  brief's instruction for it, which may not count as a wrong answer; the last allowed wrong answer → `failed`. Use
  the API's own counters (e.g. answers left, all verified) rather than counting in the model.
- Declining: explain once why the questions are asked (the brief's wording); the brief's second refusal (often also
  "what is this about?" instead of an answer) → the "declined" ending.
- Failed: say only what the brief allows (e.g. where to go instead). No account details.
- Exclude this node from global nodes that would leak or derail (e.g. a "wants a person" interrupt may still apply if
  the brief says it applies before verification: then keep it).

## Stage 3: account (api_request, the account tool in `pre_actions`)

- Route on `<node>_failure_count`. The product list reaches later code only as flattened keys
  `<node id>_response.data.<list field>.<i>.<field>` (a mapped list stayed unset in a practice run): the facts node
  reads it with `list_from_vars`.
- Retry only as the brief or the API's 5xx wording allows ("the call may be repeated"): `maxRetries` on this read, or a
  second api node. After the last failure: say it truthfully and take the brief's service-error ending.

## Stage 4: facts (code node)

Per product, in the API's order:
- Field names from the API (`account_fields`), product type → spoken label (with the last digits where the brief
  says), floor field per product type, past-due amount, days late.
- Band from days late, deadline = N-th business day after today, attempts allowed, the attempt-1 date by the brief's
  rule (e.g. last business day of this week, next week's when today is that day, never after the deadline), the
  attempt-2 proposal.
- A **date table** the model can look up instead of computing: for today and the next 2–3 weeks, each date with its
  weekday, whether it is a business day, whether it is within each product's deadline, and how "this <weekday>" and
  "next <weekday>" resolve under the brief's definition. Include the next business day after each weekend/holiday.
- Amounts in the brief's currency and format; cents converted with `round()` when the API wants minor units.
- Write: `facts_text` (finished, human-readable, used verbatim in prompts), `facts_json`, `product_count`, string flags.

## Stage 5: negotiation and promise

**The brief defines the negotiation; do not assume any particular flow.** Briefs differ in: whether there is an opening
question (and which), whether the full amount comes first, whether a lower amount, a plan, a discount or a deadline
offer exists, how many proposals are allowed and whether that depends on the product, how several products are
handled, and when a refusal ends the negotiation. Write the brief's own sequence down in `rules.json` first. Then:

- **Proposal sequence in code.** The facts node writes, per product in the brief's order, the proposals the brief
  allows: each with its sentence, amount and date (`offers_json`). An **offer loop** runs it:
  - `next_offer` (code) picks the current product and step from events (`start`, `refused`, `agreed`, `noted`) and
    writes `offer_text`, or `note` (out of proposals), or `readback` / `done` (all products handled);
  - `offer` (conversation) speaks `{{offer_text}}` as a `tts` line on every entry and only **extracts** the answer:
    `agreed` (a yes, or the customer's own amount and/or date, with `agreed_amount` and `agreed_date_words`),
    `refused`, or `stay`. It has no write tools;
  - `store_agreement` (code) checks an agreement against the facts with the brief's rules (dates from `now` with
    `resolve_date_words`, business days, the deadline, the minimum amount, the maximum) and either stores it or writes
    the brief's correction as the next `offer_text` (a correction is not a new attempt), then clears the extracted
    fields;
  - `confirm` speaks the read-back built in code (`readback_text`; one joint read-back if the brief asks for it) and
    extracts `yes` / `change`;
  - **writes are engine-run**: `record_next` (code) picks the next agreement and `record_one` (api_request) sends
    the record tool with values baked from code; a post-hook writes the result; `record_next` applies the brief's
    retry rule. Notes go the same way (`note_one`).

  The model never decides what comes next, never counts, and never sends a write. Seen in practice runs: with limits
  in text the model made extra attempts, re-asked the opening question per product, lost a promise on the customer's
  own date, and sent a note for the wrong product. Fragments: `offer_loop_code_node.json`, `offer_node.json`,
  `agreement_code_node.json`, `readback_node.json`, `engine_write_node.json`; the full loop is in
  `examples/pattern_demo.workflow.json`.
- **Corrections follow the brief's limits too.** If the brief allows an offer once (e.g. a lower amount), the code
  keeps track of what was already offered for each product; a correction never repeats it, and the customer turning
  it down again counts as the brief says (often a refusal). Seen in a practice run: a minimum-amount correction
  re-offered a floor the customer had already been offered, and the judge failed the call.
- **Date words:** use `resolve_date_words` for the customer's own day; an explicit day of the month ("Monday the
  26th", "the thirtieth") always wins over a weekday name. Test it with spoken ordinals before you rely on it.
- **A question the brief asks once** (e.g. an opening question covering every product) lives in its own node before
  the loop and is never part of the per-product steps.
- **Adapt the loop to the brief:** single or several products, one read-back per product or one joint read-back, the
  brief's own correction sentences, the brief's retry rule, which refusals end the negotiation, what happens to
  agreements when a transfer comes first. The loop is the shape; the brief supplies every rule in it.
- **Write results:** the write's post-hook counts successes and sets the close state; after the last write the
  agent (or the end node's fixed line) says the brief's confirmation sentence, verbatim, only for writes that
  succeeded; on the final failure it says the truthful failure line.
- **Products without a promise** get the brief's note (if it has notes) with the brief's reason, and only where the
  brief allows (some briefs forbid notes around transfers; some require one before a transfer).

## Offer sentences (part of the facts node)

The model paraphrases offers and drops clauses. For every sentence the brief prescribes for the negotiation, the facts
node writes it **complete**, with every clause the brief attaches to it, and the prompt says to use it verbatim. Which
sentences exist depends on the brief, for example: an opening question, each proposal of the offer loop, a
lower-amount offer with what the brief says must accompany it, the "latest date I can record" answer, the read-back
and its confirmation question. Put them in `facts_text` under a heading such as "Say these sentences exactly when
they apply".

## Stage 6: side routes (global nodes or branches)

**A side route with required agent lines needs its own node.** An extraction or intent that leads to an ending is
evaluated on the very message that triggers it, so the agent never gets to speak first. If the brief says the agent
must say or offer something on a trigger (sympathy + an offer of help, an explanation, a question), route the trigger
to a **stage node** whose `on_enter` says it (a `tts` line for fixed wording, or an `llm` prompt), and decide the
ending only from the customer's **answer** in that node (an enum such as `accepted` / `declined` / `stay`, with
`stay` as the default). Never put "trigger happened and the customer declined the offer" into one extraction value
in the node where the trigger occurs.

Map each to the brief's handling, its sentence and its close row:
- hardship (illness, bereavement, job loss): sympathy sentence, no persuasion, offer the brief's team, transfer or not.
- dispute / not mine / closed long ago: no argument, no promise, note or close as the brief says.
- already paid: the brief's sentence about processing time, no promise.
- restructuring / new plan / lower instalments: no plan talk, transfer to the brief's queue; what happens to promises
  already recorded and those agreed but not recorded.
- wants a person: the brief's "ask once whether you can help first", then transfer; before or after verification.
- scam question, robot question, a question the tools cannot answer ("I do not have that information").
- refuses to talk (twice), before vs after verification → different endings.
- forbidden offers: no discounts, waivers, plans or terms the brief does not list.

## Stage 7: closing

- **End deterministically, with a safety net.** Route to the end node on state, and also give every conversation node
  where the call can finish an `end_call` tool (handler `__function__:end_call`, `message_mode: "none"`) that the
  agent calls right after its goodbye. Seen in a practice run: a completion flag that never turned true left the agent
  saying goodbye in a loop until the turn cap. Set `max_node_turns` + `max_turn_transition` on long nodes too.
- **Completion state must not depend on attributing each tool call.** The model may make several calls of one tool in
  one message; `<tool>_args` then holds only one call's values and a "pending product" variable goes stale (seen in a
  practice run). Count outcomes instead (promises recorded + notes added >= product count), or keep a queue: the
  pre-hook appends the product id from `config['body']`, the post-hook pops the oldest.

- Thank and say goodbye **before** the close record when the brief asks it.
- The close record: exactly one, last, with the brief's outcome/reason (or code). Compute it deterministically:
  keep `close_*` state in vars from post-hooks and code nodes, and resolve the brief's precedence in the close tool's
  pre-hook: e.g. any recorded promise wins unless a transfer followed; a transfer reason wins over a promise; with no
  promise, all products paid → paid, else the brief's rule (first non-paid product's reason, or a priority order).
- Lifecycle form (`alwaysRunsAt: ["end_of_call"]`) guarantees exactly one close even when the customer hangs up.
- `end_call` nodes per ending; their `function.name` names the outcome.

## What to leave to the model

Only conversation: wording, empathy, answering questions from the facts, choosing among routed options. Never
arithmetic, calendar logic, enum spelling or the close outcome.
