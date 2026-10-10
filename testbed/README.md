# Testbed: fake briefs and personas

510 test cases for the local tester and for checking that the builder generalizes:

- **Brief A:** 100 cases
- **Brief B:** 60 cases
- **7 fake briefs:** 50 cases each, the same kind of brief as the final ones

Every case uses the kit's `public-test-set.json` format, so `run_cases.py`'s `persona_for()` can turn it into a simulated customer as-is.

## What's in here

```
personas/brief-a.cases.json      100 cases, Brief A (single product)
personas/brief-b.cases.json       60 cases, Brief B (30 multi-product, 30 other families with 1-3 products)
configs/brief-{a,b}.config.json  correct configs for the kit briefs
fake_briefs/<id>/brief.md            the brief (the builder's input)
fake_briefs/<id>/tools.openapi.json  its tool API: same 7 tool names, changed fields and enums
fake_briefs/<id>/config.json         the hand-written correct config (ground truth for the extractor)
fake_briefs/<id>/cases.json          50 cases
scoring/score_local.py           scores a local conversation for any brief (maps renamed fields back)
generator/                       the code that made all of this; rerun to regenerate
```

## The 7 fake briefs

| id | Form | What changes compared with Brief A |
|---|---|---|
| `f1-nordhavn` | clean | Other holidays. **3 bands** (10th, 6th, 3rd business day; 1/2/1 attempts). Attempt 1 is capped at the deadline. Floors `min_due` / `monthly_instalment`. An `overdraft` product. `get_account` fields renamed (`overdue_amount`, `days_past_due`, `card_ending`). `create_promise.promise_date`. Callbacks 09:00–18:00. |
| `f2-ostral` | clean | **3 identity questions** (date of birth, postcode, phone). **2 wrong answers** end the check. Queues `agent` / `welfare`. Callbacks until 20:00. Other forbidden words (`arrears`, `overdue`). |
| `f3-kestrel` | clean | **Multi-product** like Brief B, but other note reasons (`declined`, `financial_difficulty`, `contested`, `already_paid`, `system_error`). The no-promise close uses a **priority order** instead of the first product. **Notes before a restructuring transfer.** `account_ref` ids (`acc-1`…). **`amount_cents`** as an integer. |
| `f4-brightwell` | clean | **Business customers:** identity by `company_number` + `founded_year` (`verify_identity.check` / `.value`). `company_name` variable and address. Business card, business loan, overdraft (`minimum_repayment`). Multi-product. Queue `relationship_manager`. Callbacks 08:30–17:30. |
| `f5-halden-email` | **raw** | An email thread with the rules scattered through it. Identity: **postcode + birth year**. Bands 8th / 4th business day. **`callback_at` as a single `YYYY-MM-DDTHH:MM` field.** Email confirmation sentence. |
| `f6-corvid-memo` | **raw** | Policy extract plus a **memo that changes 2 rules**: late deadline 7 → **5** business days, and **no same-day callbacks**. Queue `hardship` for support. |
| `f7-lumen-codes` | **raw** | Dialler config export. Product codes `CC`/`PL`/`AL`. 3 bands in an odd table. Holidays as `FRI 23.10.26`. **`close_call` takes one `code`** (`10-PTP`, `41-NP-REF`, …). |

## Cases

- **Families (all 18):**
  - happy path
  - third party
  - identity
  - dates
  - hardship
  - amounts
  - already paid
  - restructuring
  - tool failure
  - multi-product (only in multi-product briefs)
  - dispute
  - human request
  - scam question
  - refusal
  - declined questions
  - deceased
  - wrong number
  - unclear

  Each family has several variants. For example, identity covers failing on the 3rd wrong answer, recovering after one wrong answer, the day/month swap hint, and two wrong answers, which end the check in F2 but not elsewhere.
- **Talking styles:** all 5 of the kit's styles plus "none", about 1/6 each. The `style` field is applied by the simulator (`STYLES` in `run_cases.py`), so scripts stay factual.
- **"now" dates:** 5 different dates and times per brief. They're picked to hit Fridays, holidays, the last business day of the week and late afternoons near the callback cut-off.
- **Tool failures:** in `faults`, the same format as the kit's: `{"tool": X, "fail_first_n": N}`.
- **Third-party flag:** third party, deceased and wrong-number cases carry `"third_party": true`, so `persona_for()` plays someone who isn't the customer.
- **`oracle` field (debug only, never show it to the agent):** per product it holds the band, deadline, attempts, attempt-1 date, floor and past-due amount, plus the canonical outcome key. Use it to see *why* an expected result is what it is.

## How correctness was checked

1. **No expected result is written by hand.** Every case is generated from structured customer behaviour. A rule engine computes the expected records from the brief's config: business-day calendar, deadlines, attempts, callback windows and closing rules.
2. **The engine reproduces the kit:** it matches all 13 `public-test-set.json` cases (deadlines, holiday shifts, attempt dates, callbacks).
3. **`generator/validate.py`** re-checks every case:
   - promise dates are business days within the deadline
   - amounts are between the floor and the full amount
   - callbacks are inside the window
   - outcome, reason, note and queue values come from the brief's own tables
   - in multi-product briefs every product gets a promise or a note
   - pre-verification outcomes carry no records
4. **`scoring/score_local.py`** self-test: replaying each case's expected records as perfect tool calls scores 510/510.
5. **An independent audit:** a separate agent that never saw the code worked out the expected result from the brief text for 51 random cases (weighted to hard ones, across all 9 briefs). It agreed on 51/51.

One limit remains: the raw briefs (F5–F7) only say "retry once" for `create_promise`. Cases where `get_account` or `verify_identity` fail once rely on the tool API's "the call may be repeated" wording.

## Use

```bash
# score a local conversation
python3 -c "import json,sys; sys.path.insert(0,'scoring'); from score_local import score; ..."

# regenerate (needs the kit for the public-set check and the base tool API)
cd generator && KIT_DIR=/path/to/build-the-builder python3 validate.py && python3 write.py ..
```

**Rules for using this set:**
- The builder sees only `brief.md` and `tools.openapi.json`. Never `config.json`, `cases.json` or the `oracle` field.
- `config.json` is for scoring the **extractor**: compare its Policy output field by field.
- Nothing from these fake briefs may be hardcoded into the builder. They exist to catch hardcoding.
