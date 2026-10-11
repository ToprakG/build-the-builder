# `out/policy.json`: the canonical extraction

Write this file **before** any workflow JSON. It forces one careful reading of the brief, and it is scored field by
field against a hand-written answer. Use the brief's own values on the right; the **keys** below are fixed.

Every example value on this page is invented ("Example Bank", 2031 dates, `outcome_x`). Never copy an example value:
take every value from the brief and the tool API.

## Rules for filling it

- Every key is present. If the brief is silent, use `null` (or `[]` / `{}`) and say so in `rules.json` with kind
  `assumption`.
- A value may carry its evidence: put a `"_source"` key next to it inside objects, or a top-level
  `"_sources": {"<key>": "<exact quote>"}` map. Quotes are copied from the brief.
- Raw briefs (mail threads, memos, code lists): a later or newer statement overrides an earlier one. Use the final
  value and record each override in `"_overrides": [{"key": "...", "old": ..., "new": ..., "source": "<quote>"}]`.
- Several brief files: they are given in override order. A later file overrides an earlier one.
- Enum values (outcomes, reasons, queues, note reasons, codes, product types) are copied **exactly** as the tool API
  spells them. When the brief and the tool API disagree on spelling, the tool API wins; record it in `_overrides`.

## Keys

| Key | Type | Meaning |
|---|---|---|
| `bank` | string | The bank's name as the agent says it. |
| `language` | string | ISO code of the language the agent speaks (`en`, `de`, ...). |
| `address` | string | How to address the customer, with `{field}` placeholders from the call variables, e.g. `"{title} {last_name}"`. |
| `call_variables` | list[string] | Every variable the documents say a call starts with, and no others. A time variable is included only when the documents name one. Lint treats this list as given. |
| `holidays` | list[ISO date] | Every non-business day the brief lists, as `YYYY-MM-DD`, sorted. Convert odd formats (`MON 03.03.31`). |
| `bands` | list[object] | One per lateness band, in order: `{"name", "min_days", "max_days", "deadline_business_days", "attempts"}`. |
| `attempt1` | string | The brief's rule for the first persuasion proposal, in one sentence of your own words. |
| `identity` | object or null | Null when the documents do not ask the assistant to check identity. Otherwise `{"questions": [<field values in order>], "max_wrong": int, "swap_hint": bool}`. `questions` are the tool API's values. `swap_hint` is true when the API returns a hint the documents tell the agent to act on. |
| `callback` | object | `{"start": "HH:MM", "end": "HH:MM", "same_day": bool}` plus anything else the brief fixes (e.g. `"days": "today or next business day"`). |
| `outcome_map` | object | Canonical key → the brief's own value: `[outcome, reason]` (reason `null` when none), or `[code, null]` when the close tool takes one code. Keys below. |
| `queue_map` | object | `{"restructuring": ..., "human": ..., "support": ...}` → the queue value of the transfer tool. `null` for a route the brief does not have. |
| `note_map` | object | `{"refused", "hardship", "dispute", "paid", "service_error"}` → the note tool's reason value. Empty `{}` when the brief never uses notes. |
| `close_rule` | object | How to close when several products end differently: `{"kind": "first_non_paid"}` or `{"kind": "priority", "order": [<canonical note keys>]}`, or `{"kind": "single"}`. |
| `notes_on_restructuring` | string or null | The canonical note key to add before a restructuring transfer, if the brief asks for notes then; `null` if it forbids them. |
| `product_types` | object | Canonical product key → `{"enum": <API type value>, "label": "<how the agent names it, {last4} allowed>", "floor_field": "<account field>", "last4_field": "<account field or null>"}`. |
| `account_fields` | object | `{"product_id", "type", "past_due", "days_late"}` → the account tool's field names. |
| `product_id_format` | string | The id pattern seen in the API or brief, e.g. `"r{n}"`. |
| `tools` | object | Per tool, the field names to send (see below), plus `amount_unit` and the callback and close modes. |
| `sentences` | object | Sentences the brief fixes, verbatim: `opening`, `promise_confirmation`, `ai`, `scam`, `failed_id`, and any other (e.g. `deceased`). |
| `forbidden_before_verify` | list[string] | Words or topics the agent must not say before identity is verified. Empty when the documents have no identity check. |
| `multi_product` | bool | True when a customer can have more than one product. |
| `max_products` | int | The most products one customer can have. |
| `unused_tools` | list[string] | Tools the brief never uses (e.g. notes in a brief that forbids them). Lint stops warning about them. |

### Canonical `outcome_map` keys

| Key | Situation |
|---|---|
| `promise` | at least one promise recorded |
| `paid` | the customer already paid (every product, in multi-product briefs) |
| `tr_restructuring`, `tr_human`, `tr_support` | a transfer to restructuring, to a person, to support |
| `np_refused`, `np_hardship`, `np_dispute`, `np_service_error` | verified, no promise: refused, hardship declined, disputed, a write failed |
| `nv_failed`, `nv_declined` | identity failed, identity declined |
| `nr_callback`, `nr_no_callback`, `nr_wrong_number`, `nr_deceased`, `nr_unclear` | the customer was not reached |

A brief may lack some situations: map them to `null`.

### `tools` object

Keys are the tool API's operationIds, whatever they are called. Each entry has a `role`:
`identity`, `account`, `record`, `note`, `callback`, `transfer`, or `close`. Omit a role the brief does not have.
The names below are invented.

```json
{
  "check_party": {"role": "identity", "field": "question", "answer": "reply"},
  "load_items": {"role": "account"},
  "store_agreement": {"role": "record", "product_id": "item_ref", "amount": "amount_minor", "date": "pay_on", "amount_unit": "cents"},
  "book_slot": {"role": "callback", "mode": "datetime", "datetime": "slot"},
  "end_contact": {"role": "close", "mode": "outcome_reason", "outcome": "outcome", "reason": "detail"},
  "hand_off": {"role": "transfer", "queue": "desk"},
  "write_note": {"role": "note", "product_id": "item_ref", "reason": "code"}
}
```

- `amount_unit`: `"currency"` (major units, e.g. 412.50) or `"cents"` (integer minor units). On the `record` tool.
- `callback` `mode`: `"date_time"` with `"date"` and `"time"` field names, or `"datetime"` with one field name.
- `close` `mode`: `"outcome_reason"` with both field names, or `"code"` with `"code": "<field name>"`.

## A complete invented example

```json
{
  "bank": "Example Bank",
  "language": "en",
  "address": "{title} {last_name}",
  "call_variables": ["callerRef", "first_name", "last_name", "title", "now"],
  "holidays": ["2031-03-14", "2031-04-02"],
  "bands": [
    {"name": "first", "min_days": 1, "max_days": 20, "deadline_business_days": 5, "attempts": 1},
    {"name": "second", "min_days": 21, "max_days": 60, "deadline_business_days": 3, "attempts": 2}
  ],
  "attempt1": "the last business day of the current week, or of next week when today is that day; never after the deadline",
  "identity": {"questions": ["member_code", "birth_month"], "max_wrong": 2, "swap_hint": false},
  "callback": {"start": "09:00", "end": "17:00", "same_day": true, "days": "today or the next business day"},
  "outcome_map": {
    "promise": ["outcome_x", null], "paid": ["outcome_v", null],
    "tr_restructuring": ["outcome_y", "detail_r"], "tr_human": ["outcome_y", "detail_a"], "tr_support": ["outcome_y", "detail_b"],
    "np_refused": ["outcome_u", "detail_f"], "np_hardship": ["outcome_u", "detail_g"], "np_dispute": ["outcome_u", "detail_h"],
    "np_service_error": ["outcome_u", "detail_c"],
    "nv_failed": ["outcome_z", "detail_a"], "nv_declined": ["outcome_z", "detail_e"],
    "nr_callback": ["outcome_w", "detail_a"], "nr_no_callback": ["outcome_w", "detail_d"],
    "nr_wrong_number": ["outcome_w", "detail_b"], "nr_deceased": null, "nr_unclear": ["outcome_w", "detail_c"]
  },
  "queue_map": {"restructuring": null, "human": "desk_x", "support": "desk_y"},
  "note_map": {"refused": "note_x", "hardship": "note_z", "dispute": "note_w", "paid": "note_y", "service_error": "note_v"},
  "close_rule": {"kind": "priority", "order": ["dispute", "hardship", "refused", "service_error"]},
  "notes_on_restructuring": null,
  "product_types": {
    "a": {"enum": "kind_a", "label": "item ending {last4}", "floor_field": "floor_amount", "last4_field": "ending"},
    "b": {"enum": "kind_b", "label": "second item", "floor_field": "floor_amount", "last4_field": null}
  },
  "account_fields": {"product_id": "item_ref", "type": "kind", "past_due": "overdue", "days_late": "days_over"},
  "product_id_format": "r{n}",
  "tools": {
    "check_party": {"role": "identity", "field": "question", "answer": "reply"},
    "load_items": {"role": "account"},
    "store_agreement": {"role": "record", "product_id": "item_ref", "amount": "amount_minor", "date": "pay_on", "amount_unit": "cents"},
    "book_slot": {"role": "callback", "mode": "datetime", "datetime": "slot"},
    "end_contact": {"role": "close", "mode": "outcome_reason", "outcome": "outcome", "reason": "detail"},
    "hand_off": {"role": "transfer", "queue": "desk"},
    "write_note": {"role": "note", "product_id": "item_ref", "reason": "code"}
  },
  "sentences": {
    "opening": "the automated assistant of Example Bank; the call is recorded",
    "promise_confirmation": "You will get a letter about this promise.",
    "ai": "Example Bank's automated assistant",
    "scam": "check the call on the number printed on your member card",
    "failed_id": "visit a branch"
  },
  "forbidden_before_verify": ["money", "item", "late"],
  "multi_product": true,
  "max_products": 2,
  "unused_tools": [],
  "_overrides": [{"key": "callback.end", "old": "18:00", "new": "17:00", "source": "from Monday the line closes at 17:00"}]
}
```
