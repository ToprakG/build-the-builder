# The final bank mock: what the organizers say (quotes) and what the kit already shows

Collected 19:2x Istanbul from (a) https://www.freyavoice.ai/hackathon (Data, Evaluation, Rules, FAQ, Final), fetched through a summarising page reader, so quotes
are as the reader returned them (short exact phrases; a few may be abridged: re-check on the page at 20:00 when the zip link appears), and (b) the kit v3 zip
(`/hackathon/build-the-builder-v3.zip`, `VERSION` = 3; its README is identical to our `KIT_README.md`, so nothing new in kit v3 about the final).
**Nothing on the public page describes the final mock's endpoints, state or auth: that is in "the service contract" inside the case zip, which opens 20:00 Istanbul.**

## 1. The final case (page)
- "The final is one case: Freya Bank's credit card statement-debt line, in Turkish." Practice calls are in English; "the final case is in Turkish."
- Zip: "a folder of the bank's documents in Markdown, Word, PDF and Excel." "Read BRIEF.md first; README.md says what each document is." "In your run command, {case_folder} is that folder."
- "The zip has the bank's test customers, for your mock, and six example conversations." (format of the test customers: not on the page; it is in the zip)
- Opens Sunday 17:00 UTC (20:00 Istanbul); closes 20:00 UTC (23:00 Istanbul). Link "from the Submit tab or the e-mail we send".

## 2. The mock (page)
- Step 2: "Write your mock of the bank's services on Cloudflare Workers, from the service contract and the test customers." "Deploy it before you run your builder."
- "We load the workflow JSON you submit into our own workspace, test it with simulated customers you never see." "During these conversations its tools call your mock, so a wrong or unreachable mock fails them."
- "our simulated customers reach it through your agent's tools."
- Uptime / persistence: "On Cloudflare Workers, up until the results are out. If it is down, the conversations that reach it fail." "Keep your mock running until the results are out." (Data, Rules)
- "The phone agent may only call your own mock during the final."
- Practice-bank wording (the organizers host the practice bank; for the final it is yours): "We host it, so we can see what was really recorded." The scorer checks "that record, not what the phone agent says it did." "You pick a team key, and your practice sessions and their records belong to that key."
- "The bank's API does not enforce the brief." (Evaluation): keeping the rules is the agent's job.
- NOT on the page: required endpoints, request/response shapes of the final mock, how the organizers read the recorded state in the final, authentication / team key for the final, persistence requirements, the format of the test customers.

## 3. Scoring (page)
- "Only the final phone agent is scored." The builder's trace "is not scored"; reviewers read it (hand edits).
- Three checks, a case "passes only when three checks all pass. There is no partial credit." Outcome ("Promise on the right product, date and amount, recorded once."; from the bank's records), policy ("No debt read before the identity check, no date past the limit."; judged from the transcript), honesty ("Never says 'recorded' when the tool call failed."; judged from the transcript). The page does not say these are the final's checks, only the practice's.
- "`--reps 3` counts a customer only when every run passes." Final: "tested ... on several calendar days, three times each. A customer counts only when all three runs pass." (KIT_README, "The final")
- "We do not publish the test customers, the success criteria or how the parts are weighted." Number of final customers, ranking formula: not on the page. Practice day: "Every case runs on Wednesday 14 October 2026 at 11:00."
- KIT_README, "The final": customers "can have up to three products", "talk like people": numbers "in two parts", self-corrections, side talk, questions back, "amounts and dates in words".
- KIT_README rules: "Do not edit a phone agent by hand after it is created." "Nothing from the final case may be written into the builder" (a crash fix on an unexpected file type is allowed). Equal scores: total promised amount on passed customers, then shorter build time, then earlier submission.

## 4. Submission (page)
`submission.json` fields: `team`, `members` (list of `name`, `email`; "Solo teams list one member."), `repo`, `commit`, `run_command` (`python3 builder/build.py {case_folder} {mock_url}`), `workflow` (e.g. `submission/final.workflow.json`), `trace` (e.g. `traces/final.log`), `mock_url`.
Run command example: `python3 builder/build.py FreyaBank-Ekstre-Borc/ https://your-mock.your-subdomain.workers.dev 2>&1 | tee traces/final.log`. "Export the phone agent with freya.export_workflow and commit the file with the trace." "Plain text is fine; remove API keys before you commit." "Before 20:00 UTC (23:00 Istanbul), send submission.json to the address in your kit email."

## 5. What the kit's practice bank does (kit v3 `tools/openapi.json`, `tools/score.py`, `tools/run_cases.py`): the best available model of the final contract
The final contract is "the service contract" in the zip; the practice one is probably its ancestor. Verbatim from the kit:
- `servers`: `https://freya-bank-mock.freyavoice.workers.dev/v1` (a Cloudflare Worker too). `info.description`: "Mock of the bank's systems for the Build the Builder hackathon. We host it. Every call is written to the ledger the scorer reads. The service does not enforce the brief: it records a promise on a holiday or past the deadline without complaint. Following the brief is the agent's job. The seven tool calls need no key: the customer_id in the body is the secret of one call."
- Seven POST tools under `/v1`: `verify_identity` {customer_id, field in (date_of_birth, phone_last4), answer} -> {ok, error, verified, wrong_answers_left, all_verified, hint} ("Check one security answer. Three wrong answers in total end the check."); `get_account` {customer_id} -> {ok, error, products[]} ("The late products. Refused until both questions are passed."); `create_promise` {customer_id, product_id, amount (number), date} -> {ok, error, promise_id} ("Record a payment promise. One per product."); `add_note` {customer_id, product_id, reason in (refused, hardship, dispute, paid, service_error), text}; `schedule_callback` {customer_id, date, time}; `transfer_to_agent` {customer_id, queue in (restructuring, general, support)}; `close_call` {customer_id, outcome in (promise, paid, transferred, no_promise, not_verified, not_reached), reason} ("End the call. Always the last tool call. Nothing is accepted after it.").
- Every 200 response: "`ok` is false when the action was not carried out." Every 500: "The service failed. Nothing was recorded. The call may be repeated."
- Practice-only endpoints used by `run_cases.py` / `score.py`, header `x-team-key: <FREYA_BANK_TEAM_KEY>` (any secret of 16+ characters), user-agent must look like a browser (`error code: 1010` otherwise): `POST /v1/practice/sessions` {"case": id} -> {"variables": {customer_id, ...}} (the variables are what the simulation passes to the agent); `GET /v1/practice/ledger/<customer_id>` -> the recorded state.
- What the scorer reads from the ledger (`score_ledger`): `outcome`, `reason` (from close_call), `promises[]` {product_id, amount (rounded to 2), date}, `notes[]` {product_id, reason}, `callbacks[]` {date, time}, `transfers[]` {queue}, `calls[]` {tool, result} in order; fails if the last call is not `close_call`; fails if `get_account` or `create_promise` returned `error == "identity not verified"`; fails on identity/account tools for a customer who was not on the line. So the recorded state must hold exactly these, per customer_id, in call order, and `error: "identity not verified"` must be the refusal text before both questions are passed.

## 6. Consequences for the Worker (checklist for S2/S3; derived, not quoted)
1. Workers are stateless between requests: the ledger per `customer_id` must live in KV / Durable Object / D1; a restart or a second isolate must not lose it. Keep it deployed and warm until the results are out.
2. Serve the seven tools exactly as the zip's contract says (names, fields, enums, `ok:false` + `error` text, 500 only for real faults, nothing recorded on 500) and an OpenAPI document at the same base URL (our builder reads the tool API from the case folder or `{mock_url}/openapi.json`: check which, from the zip's README).
3. Provide the read side the organizers/our own checks need (a ledger GET by `customer_id`, protected by a team key if the contract says so). Unknown until the zip: whether the organizers read the ledger through an endpoint of ours, or only replay conversations.
4. Test customers from the zip must be loaded as data (customer_id -> identity answers, products with amounts/dates/floors, per-customer holidays/`now`).
5. Do not enforce the brief in the mock (the page says the bank API does not); only record faithfully.
