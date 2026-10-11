# Self-check before you finish

Answer every line, in order, against your own files. Fix what fails, then run `lint_workflow` and `check_coverage`
again. A line you cannot satisfy goes into `rules.json` as an `assumption` with the reason.

## Inputs

- [ ] I read every brief file, in order, and applied later overrides (and recorded them in `_overrides`).
- [ ] `policy.json` has every key of `reference/policy-schema.md`; every enum value is spelled as in the tool API.
- [ ] `rules.json` has one rule per brief rule (numbered lines, table rows, memo changes); each quote is exact.

## Tools

- [ ] Every tool field and enum value in the workflow comes from `openapi_summary` (renamed fields, cents, a single
      datetime field, a single close code: whatever this API has).
- [ ] Every URL is `servers[0].url` + the tool's path; `url`, `method`, headers and body leaves use `default`.
- [ ] Every call variable the documents list is baked into the tool field of the same name. The agent does not ask for it.
- [ ] Model-filled fields have an `enum` when the value set is known, and a description of the exact format.
- [ ] Writes have `maxRetries: 0`; the brief's retry rule is implemented with counters or hooks instead.
- [ ] Every tool the model calls has a post-hook with `what_to_do_next` for success and failure.

## Numbers and dates

- [ ] Every date rule starts from the call's time variable; no extraction field of type `date` decides a date, and nothing reads the system clock.
- [ ] Holidays, bands, deadlines, attempt dates, floors, callback windows are computed in code and shown as facts.
- [ ] I ran `run_snippet` on every code node and hook with at least 3 `now` values: a Friday, the day before a
      holiday, a late afternoon near the end of a time window the documents define. Results are correct by hand-check.
- [ ] I ran the facts node with 1 product and with the maximum product count, and with renamed fields if the API has
      them, passing the products **only as flattened keys** (`<node id>_response.data.<list>.0.<field>`), as the
      platform delivers them. Amounts in cents convert with `round()`.

## Wording

- [ ] Every sentence the brief fixes word for word is used verbatim (tts line, a terminal `spoken_message`, or a
      `what_to_do_next` that quotes it).
- [ ] The opening says only what the brief allows before verification; forbidden words are listed in
      `system_prompt` and avoided in every pre-verification line.
- [ ] The agent speaks the brief's language everywhere, including fixed lines and spoken dates.
- [ ] No `{{var}}` in a tts line or a `spoken_message` can be unset when it is spoken.

## Routes and endings

- [ ] Every route where the brief makes the agent say or offer something first (sympathy + offer, explanation,
      question) enters a stage node that says it on entry; the ending depends only on the customer's answer there.
- [ ] The brief's own proposal sequence (per product) runs in an offer loop: code picks each proposal, the agent
      speaks it verbatim, a refusal goes back to code. A question the brief asks once is not repeated per product.
- [ ] Every write whose values code can decide (promises after a confirmed read-back, notes for products out of
      proposals) is engine-run from an `api_request` node; the model's conversation nodes have no write tools there.
- [ ] Every conversation node where the call can finish offers an `end_call` tool; completion flags count successes
      and do not depend on which product a single tool call was for.
- [ ] Every offer the brief prescribes is a complete sentence in the facts (with all its required clauses) and the
      prompt says to use it verbatim.

- [ ] Every row of the brief's closing table is reachable, and each one sets the right close values.
- [ ] Every side route of the brief (hardship, dispute, already paid, restructuring, human, scam, robot, refusal,
      declined, unclear, third party, wrong number, deceased, callback, service error) exists, or is `n/a` with a reason.
- [ ] Exactly one close record per call, as the last bank tool. No bank tool can run after it.
- [ ] Notes (if the brief has them): one per product without a promise, none where the brief forbids them.
- [ ] Multi-product: per-product attempts, one combined read-back, one write per product, the brief's close rule.
- [ ] Every passthrough node ends with `always`; every node can reach a terminal; no node is unreachable.

## Last

- [ ] `lint_workflow`: 0 errors. Warnings read and either fixed or explained in `rules.json`.
- [ ] `check_coverage`: 0 errors.
- [ ] `check_unit_tests`: 0 errors, and every decisive rule has a unit test that starts in its node.
- [ ] `run_local_sims`: every structural failure (route, write, values, stuck node) fixed and re-run once.
- [ ] `validate_workflow`: accepted, or skipped (no key).
- [ ] `out/workflow.json`, `out/policy.json`, `out/rules.json` all exist.
