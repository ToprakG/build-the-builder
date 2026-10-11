# mock spec (JSON) — contract between the converter (S3, `mock/convert.py`) and the Worker (S2, `mock/src`)

One file per bank: `mock/spec.<id>.json`.

```
{
  "spec_version": 1,
  "id": "brief-a", "name": "Freya Bank",
  "config":   { ... },   // the CANONICAL bank config (same schema as testbed/fake_briefs/*/config.json and vendor/.../seed/policy_ab.py:POLICY_A)
  "openapi":  { ... },   // the service contract as OpenAPI 3; served verbatim at GET /openapi.json (paths/field names are the bank's own)
  "customers": [         // seed accounts; identical shape to a testbed case minus script/expected
    { "customer_id": "c_ab12cd34",
      "variables": {"first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"},
      "identity":  {"date_of_birth": "1988-03-14", "phone_last4": "4471"},        // keys = config.identity.questions
      "products":  [{"type": "credit_card", "card_last4": "4821", "past_due": 420.0, "days_late": 6, "minimum_payment": 126.0}] }
  ]
}
```

## What the Worker reads from `config` (all keys optional unless marked; defaults = Brief A)
- `tools.<tool>`: the request field names the bank uses. `verify_identity {field, answer}`; `create_promise {product_id, amount, date}`;
  `schedule_callback {mode: "datetime"|"date_time", datetime | date,time}`; `close_call {mode: "outcome_reason"|"code", outcome, reason | code}`;
  `transfer_to_agent {queue}`; `add_note {product_id, reason}`.
- `account_fields {product_id, type, past_due, days_late}`, `product_id_format` ("p{n}"), `product_types.*.floor_field`.
- `identity {questions[], max_wrong, swap_hint}`; `callback {start, end, same_day}`; `holidays[]`.
- `outcome_map` (key -> [outcome, reason]), `queue_map`, `note_map` — the ledger reports canonical keys (see mockbank.py docstring).
- Behaviour to port: vendor/build-the-builder/tools/driver/mockbank.py (`Bank`, `HANDLERS`): identity (date swap hint, max_wrong lock), get_account (verified only), create_promise (dates/amount checks, per-product),
  add_note, schedule_callback (window), transfer_to_agent, close_call (once), ledger. Dispute = add_note with the dispute reason. Where this file and mockbank.py disagree on enforcement, \"does not enforce the brief\" wins.

## Endpoints the Worker serves (aligned with yc-s4 mock/REQUIREMENTS.md, section 5: the kit's practice bank is the best model of the final contract)
- Base path `/v1` (as `openapi.servers`): `POST /v1/<tool>` for the 7 tools (`verify_identity`, `get_account`, `create_promise`, `add_note`, `schedule_callback`, `transfer_to_agent`, `close_call`);
  200 + `{ok:false, error}` when the action was not carried out (`error: "identity not verified"` before both questions are passed); 500 = nothing recorded; nothing accepted after `close_call`.
- `GET /openapi.json` (and `/v1/openapi.json`): `spec.openapi` verbatim.
- Read side: `GET /v1/practice/ledger/<customer_id>` -> `{outcome, reason, promises[{product_id, amount, date}], notes[{product_id, reason}], callbacks[{date, time}], transfers[{queue}], calls[{tool, result}] in call order}`;
  alias `GET /records` (all customers) and `GET /records/<customer_id>`. Optional header `x-team-key` (16+ chars) only if the final contract says so; default open.
- `GET /customers` (seed ids only). Optional `POST /v1/practice/sessions {"case": id}` -> `{variables:{customer_id,...}}` for a local driver.
- The service does NOT enforce the brief (records a promise on a holiday or past the deadline); it enforces only identity (3 wrong answers end the check), one promise per product, and the call-closed rule.
- State per customer lives in KV / Durable Object / D1 (Workers are stateless); the seed is loaded from the spec at deploy; it must survive restarts until the results are out.

Validate a spec with `python3 mock/convert.py --check mock/spec.<id>.json`.

## Worker deployment (S2) — what is where
- Tenant = `spec.id`. The mock URL to give `build.py` is **`https://<worker>/<tenant>`** (e.g. `https://bank-mock.<acct>.workers.dev/brief-a`).
  `GET <mock_url>/openapi.json` is `spec.openapi` verbatim, with `servers[0].url` rewritten to `<mock_url>/v1` (the base the agent calls).
- Bundled at deploy: `mock/specs/*.json` (`node scripts/bundle.mjs`, run by `npm run deploy`). Live, no redeploy: `PUT <mock_url>/admin/spec` with the converter JSON (Bearer `ADMIN_TOKEN`); `PUT /admin/customers` merges customers; `POST /admin/reset[?customer_id=]`.
- Read side is open (no token): `GET <mock_url>/v1/practice/ledger/<customer_id>`, `GET <mock_url>/ledger/<id>`, `GET <mock_url>/records[?customer_id=&limit=]` (all calls, oldest first), `GET <mock_url>/state`. Faults (`500`, nothing recorded) from a top-level `faults: [{op, customer_id|null, times}]` in the spec or header `x-mock-fail: 1`.
- Ledger keys: `{outcome, reason, promises[{promise_id,product_id,type,amount,date}], notes[{product_id,reason,text}], callbacks[{date,time}], transfers[{queue}], disputes[], calls[{seq,ts,tool,args,result}], closed}`; post-close calls are rejected and not listed in `calls`.
- Differences from mockbank.py worth knowing: schema check is only required+enum; a day/month swap is not counted as a wrong answer (as mockbank); dispute is an `add_note` reason.
- State: one Durable Object (SQLite) per tenant; survives redeploys. Tests: `cd mock && npm test` (engine), `test/e2e.sh <url> <ADMIN_TOKEN>` (live).
