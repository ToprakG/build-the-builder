# RUNBOOK_FINAL: the 20:00 final (Istanbul time), zip case folder, Turkish, our own Cloudflare mock

Facts (hackathon page, see `mock/REQUIREMENTS.md`): one case, "Freya Bank's credit card statement-debt line, in Turkish". Zip = a folder of the bank's documents (md, docx, pdf, xlsx), "Read BRIEF.md
first; README.md says what each document is", plus "the bank's test customers, for your mock, and six example conversations" and the service contract. We "write your mock of the bank's services on
Cloudflare Workers", deploy it, run `python3 builder/build.py {case_folder} {mock_url}`, commit the exported workflow + the trace, and send `submission.json` **before 23:00 Istanbul (20:00 UTC)**.
The organizers load our workflow into their workspace; its tools call OUR mock: "If it is down, the conversations that reach it fail" - it must stay up until the results are out.
Target: build started 20:25, submitted by 21:30. Hard stop 23:00.

## 20:00 mock commands (in this order; the spec name is `spec.freyabank.json` everywhere)
```
mock/convert_case.sh case/<folder> freyabank                              # -> mock/spec.freyabank.json (+ --check)
scripts/mock_load_case.sh mock/spec.freyabank.json freyabank              # PUT /admin/spec -> reset -> e2e -> reset; prints mock_url
# mock_url = https://bank-mock.tccbank2026-yc.workers.dev/freyabank
python3 builder/build.py case/<folder> https://bank-mock.tccbank2026-yc.workers.dev/freyabank --members "..."
```

## Who does what
| who | job |
|---|---|
| **you (Ali)** | 20:00 download the zip (Submit tab / the e-mail), unzip into `case/`; have the Cloudflare login ready (`wrangler whoami` ok) and `ADMIN_TOKEN` chosen; at the end: team name + member names/emails, send `submission.json` to the address in the kit e-mail |
| **S0** | owns `build.py`/flags/mode (frozen: `FINAL_FLAGS.txt`, `FINAL_MODE.txt`), runs the build, go/no-go calls, merges, the final commit/tag |
| **S3** | `mock/convert.py`: the zip's test customers + service contract -> `mock/spec.<id>.json` (see `mock/SPEC.md`) |
| **S2** | the Worker (`mock/`, Durable Object ledger): deploy, upload the spec, verify every endpoint, reset state, keep it up |
| **S4 (me)** | checks (`check_turkish.py`, `dress_check_case.py`), packaging, `scripts/finish_submission.py`, the commit commands, submission validation |
| S1 (suggested) | read `BRIEF.md`, `README.md` and the six example conversations; list for S0 every difference from the kit's contract (tool names, fields, enums, identity questions, outcomes) in the first 10 minutes |

## Timeline
| time | step | owner |
|---|---|---|
| 19:45 | freeze (flags, code). Only the runbook steps below from now on | S0 |
| 19:50 | `scripts/prefinal_tunnel.sh` (fresh tunnel check, the self-test uses a quick tunnel) | S0 |
| **20:00** | zip opens. `mkdir case && unzip <zip> -d case/` (find the folder: `ls case`). Read `BRIEF.md`, `README.md`, the service contract, the test customers file, the six example conversations | you + S1 |
| 20:00-20:10 | compare the contract with the kit's (`mock/REQUIREMENTS.md` section 5): same 7 tools? same fields/enums? identity questions? `FINAL_MODE`/self-test decision (Risk R2) | S1, S0 |
| 20:05-20:20 | mock: S3 converts, S2 deploys + uploads the spec | S3, S2 |
| 20:20-20:25 | verify the mock (below), reset its state, **GO/NO-GO** | S2, S4, S0 |
| 20:25-20:55 | `python3 builder/build.py case/<folder> <mock_url> --members "..."` (30 min budget, ships at minute 26) | S0 |
| 20:55-21:10 | checks (below), Turkish check, export present | S4 |
| 21:10-21:25 | commit, `finish_submission.py`, final mock smoke + reset | S4, S2 |
| **21:30** | send `submission.json` | you |
| 21:30-22:45 | slack: re-run the build once if a check failed (keep the best run's workflow + its trace) | S0 |
| 23:00 | closed | |

## 1. Unzip and read (20:00)
```bash
cd <repo>                       # the checkout at the frozen commit
mkdir -p case && unzip -q ~/Downloads/<zip name>.zip -d case/ && find case -maxdepth 3 | head -50
```
`{case_folder}` = the one folder inside (the page example: `FreyaBank-Ekstre-Borc/`). Do NOT write anything from the final case into the builder (rule). Fixing a crash on a file type is allowed.

## 2. The mock (S3 -> S2), 20:05-20:25
S3 (one command; dry-run tested on `mock/rehearsal-tr/FreyaBank-Test`: 10 customers, `--check` OK; contract files = BRIEF.md, README.md, then every other md/docx/pdf in the folder except the example conversations; customers = the only xlsx/csv; openapi = a `*openapi*.json|yaml` in the folder, else derived from the contract by the LLM):
```bash
mock/convert_case.sh case/<folder> freyabank        # -> mock/spec.freyabank.json, then runs --check (prints OK or the problems)
# several customer files / a specific api file:  CUSTOMERS=case/<folder>/<file>.xlsx OPENAPI=case/<folder>/<api>.json mock/convert_case.sh case/<folder> freyabank
# same thing spelled out: python3 mock/convert.py --id freyabank --name "Freya Bank" --contract case/<folder>/BRIEF.md case/<folder>/README.md <contract> <announcements> <Q&A> \
#      [--openapi <file>] --customers <test customers file> && python3 mock/convert.py --check mock/spec.freyabank.json
```
Read the output lines `contract:` / `customers:` / `openapi:` (the files it picked), and the `--check` result; any `FAILED:` (a required customer column not found even after the LLM mapping) stops the run - tell S2/S0, do not load a partial spec.
S2 (see `mock/SPEC.md`: one spec = one tenant; `mock_url` = `https://bank-mock.tccbank2026-yc.workers.dev/<tenant>`). The Worker is ALREADY deployed; a new bank is one command, no redeploy:
```bash
scripts/mock_load_case.sh mock/spec.freyabank.json freyabank     # PUT /admin/spec -> reset -> test/e2e.sh (21 checks) -> final reset -> prints mock_url=...
```
It reads `MOCK_ADMIN_TOKEN` from the repo `.env` (gitignored; never commit it). Exit code 0 = all checks passed. The e2e uses the spec's first customer through every tool. Only redeploy (`cd mock && npm run deploy`, needs `wrangler login`; the account has the `tccbank2026-yc` workers.dev subdomain) if the Worker code itself must change.
Admin by hand: `H="Authorization: Bearer $MOCK_ADMIN_TOKEN"; M=<mock_url>`; `curl -s -X PUT -H "$H" --data-binary @mock/spec.freyabank.json $M/admin/spec`; `curl -s -X POST -H "$H" $M/admin/reset` (all) or `$M/admin/reset?customer_id=<id>`; `curl -s -X PUT -H "$H" -d '[{"customer_id":...}]' $M/admin/customers` (merge more test customers); fault injection: spec `faults:[{op,customer_id|null,times}]` or header `x-mock-fail: 1`.
Read side (no token, for organisers and the scorer): `$M/v1/practice/ledger/<customer_id>`, `$M/ledger/<id>`, `$M/records[?customer_id=&limit=]`, `$M/state`.
**Verify (S2 runs, S4 re-checks), with `M=<mock_url>`:**
1. `curl -s $M/openapi.json | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['servers'],sorted(d['paths']))"` - the paths are exactly the contract's tools and `servers[0].url` is the Worker URL (the workflow's tool URLs come from it; `dress_check_case.py` refuses tunnel/localhost URLs).
2. One test customer through every tool with curl in the contract's field names: identity ok -> account -> promise -> note -> callback/transfer if in the contract -> close; then `curl -s $M/ledger/<customer_id>` shows outcome, reason, promises, notes, callbacks, transfers and `calls` in order. Refusals use the contract's error text (kit: `identity not verified`, `call already closed`).
3. A wrong identity answer x3 ends the check; `get_account` before verification is refused; a second `close_call` is refused; unknown customer -> `ok:false`.
4. Persistence: call twice from two different networks (phone hotspot) - the ledger of the first call is still there (Durable Object, not memory).
5. `POST $M/admin/reset` (Bearer `$MOCK_ADMIN_TOKEN`; `mock_load_case.sh` already ends with it) - state clean before the build and again before sending.
6. Write the Worker URL, tenant, Cloudflare account (`tccbank2026-yc`; token = `MOCK_ADMIN_TOKEN` in `.env`) in `out/final/MOCK.txt` (not committed) so anyone can redeploy.
**NO-GO if** a tool of the contract is missing in the Worker, `/openapi.json` is not served, or the ledger does not persist. Then S0 decides: fix (max 15 min) or build against the best available URL and fix the mock in parallel (the build only reads `/openapi.json`).

## 3. Build (S0), 20:25
```bash
cd <repo>
mkdir -p out/final
python3 builder/build.py case/<folder> <mock_url> --members "Name One,Name Two" --out out/final/build > out/final/build.out 2>&1; tail -1 out/final/build.out | tee out/final/build_py.json
# ONE command, no manual step; budget 30 min, shipped by minute 26 (watch it with: tail -f out/final/build/final_run.log)
```
`build.py` ingests the folder (BRIEF.md, README.md first, then md/docx/pdf/xlsx/csv/html as text with `=== FILE:` headers), takes the tool API from `<mock_url>/openapi.json` (else a spec file in the folder),
runs `final_run.sh` (flags `FINAL_FLAGS.txt`, mode `FINAL_MODE.txt`, claude_cli only), saves + exports the workflow, writes `submission.json` (members, workflow, trace, mock_url) and prints one JSON line
`{"status","slot","saved","trace","submission"}`. Prerequisites as always: `claude` logged in, `cloudflared`, `FREYA_API_KEY` + `FREYA_BANK_TEAM_KEY` in `.env`, no other eval on `lane1.lock`. Provider unusable: the preflight stops within 30 s ("STOP: no usable LLM provider").
Watch `out/final/build/final_run.log`: `stage build`, `LLM preflight ok`, `policy sanity: ok` (a `DEGENERATE:` line = look at the brief ingest), `stage run_r0`, `repair`, `finalize`.

## 4. Checks (S4), 20:55-21:10
```bash
OUT=out/final/build
python3 scripts/dress_check_case.py out/final FINAL_FLAGS.txt   # (expects out/final/build and out/final/build_py.json from step 3) build ok, lint 0, flags == FINAL_FLAGS.txt, ingest order, submission.json, export present, tool URLs == OpenAPI servers[].url
python3 scripts/check_trace.py $OUT --freya --selftest           # trace fields (start/end, model calls, Freya, self-test)
python3 scripts/check_turkish.py $OUT/workflow.json --policy $OUT/policy.json     # spoken texts in Turkish? (Risk R1)
python3 -c "import json;p=json.load(open('$OUT/policy.json'));print(p['language'],p['bank'])"   # language must be 'tr'
```
Also read `$OUT/selftest_report.md` and `$OUT/patch_report.json` (what the repair changed; no brief-specific edits).

## 5. Commit and submit (S4 + you), 21:10
```bash
SLOT=$(python3 -c "import json;print(json.load(open('out/final/build_py.json'))['slot'])")      # case-<folder name>
git status --short | grep -v '^??'                                  # review; never `git add .`, never the whole traces/ or out/
git add workflows/$SLOT.workflow.json traces/<trace dir printed by build.py>        # the export + the trace (trace.txt, events.jsonl)
git commit -m "final: workflow and trace for the case" && git push origin <branch>
python3 scripts/finish_submission.py --team "<team name>" --member "Name One:one@example.com" --member "Name Two:two@example.com"
git tag final-submit && git push origin final-submit               # the commit hash in submission.json is HEAD, which contains the workflow and trace
```
`finish_submission.py` completes `submission.json` in the page's format (`team`, `members[{name,email}]`, `repo`, `commit`, `run_command` = `python3 builder/build.py {case_folder} {mock_url}`, `workflow`, `trace`, `mock_url`) and refuses (exit 1) on: placeholder team, missing member email, a workflow/trace file that is missing, untracked or modified, a key value from `.env` inside them, a non-https `mock_url`. Run `--check-only` first.
Then: S2 final smoke + `admin/reset` of the mock; you send `submission.json` (the file at the repo root) to the address in the kit e-mail before 23:00 Istanbul. Keep the Worker, the tunnel-less deploy and the Cloudflare login alive until the results are out.

## Risks to decide before 20:00 (owner)
- **R1 (S0, critical) Turkish.** The compiler speaks English in its code nodes: re-asks, read-backs, date/amount offers and the closing lines are English string literals in `graph.py`/`runtime.py` (`check_turkish.py` on a normal build: 38 distinct English spoken texts, English weekday/month words in dates), the number/date parsers read English words, and `policy.language` only steers the model's prompt. A Turkish brief gives Turkish policy lines (opening, goodbye ...), but every code-driven turn would still be spoken in English. Options: (a) accept and measure with the check; (b) `python3 builder/build.py ... --variant agentic` (Claude Code builder, writes the workflow itself; slower, unproven here); (c) a localisation table for the code strings. Decide at 20:10 from the first contract read; nothing of this can be tested before the case exists.
- **R2 (S0) self-test target.** The build's Freya self-test runs against OUR local mock bank in the kit's shape, not against the Worker. If the zip's contract differs from the kit's 7 tools, its scenarios may fail for the wrong reason and the patcher could act on them: consider `FINAL_MODE=patcher_off` (no repair) for that case.
- **R3 (S2) Workers state.** The ledger must be in a Durable Object / KV, not in memory; the contract may need endpoints we do not have (`kind: record` generic ops exist in `mock/SPEC.md`).
- **R4 (you) Cloudflare login/token** before 20:00 (`wrangler whoami`), and the e-mail address from the kit e-mail for the submission.
- **R5 (S4) emails:** `members[].email` are required by the page's example; get them now.

The previous (pre-zip) runbook is kept as `docs/RUNBOOK_old_pre_zip_rules.md`.
