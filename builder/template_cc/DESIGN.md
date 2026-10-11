# Variant T-CC: design

T-CC = A1 (extract → fixed template) + a Claude Code policy audit + a gated, judged prompt-patch loop.
Everything lives in `builder/template_cc/` and `tests/template_cc/`. Nothing under `builder/agentic/`,
`builder/common/`, `builder/eval/` or `builder/build.py` is touched. The A-agent tools `openapi_summary`, `lint`,
`snippets` and `validate` are imported read-only.

```
brief(s) + tool API
  -> EXTRACT (2 LLM passes + 1 fix round) -> policy_check (deterministic) -> policy.raw.json
  -> COMPILE baseline (fallback, written before any further LLM step)
  -> POLICY AUDIT (Claude Code, isolated workspace) -> PATCH JUDGE per policy patch -> policy.json -> COMPILE
  -> [SELF-TEST LOOP: testgen from OUR policy -> TestRunner -> analyzer -> Claude Code patcher -> PATCH JUDGE
      -> apply accepted -> re-run -> sign-test GATE vs best]
  -> FINALIZE: strip agent_config, lint, validate dry run (if key), save + export on Freya, build_report, trace
```

## 1. Verified findings (Phase 0)

### 1.1 Agent SDK (`.venv310`, claude-agent-sdk 0.2.165, CPython 3.10.20)

`SDK_NOTES.md` was checked against the installed `types.py` / `__init__.py`. `tools=` sets the base set of built-in
tools (`["Read","Glob","Grep","Write"]` removes Bash, web and subagents). `allowed_tools` only auto-approves.
`permission_mode="dontAsk"` denies anything not pre-approved. `max_budget_usd` exists. A plain-`str` `system_prompt`
replaces the Claude Code preset. Deny-hook shape: `HookMatcher(matcher, hooks, timeout)`. MCP: `tool`,
`create_sdk_mcp_server`, with names `mcp__<server>__<tool>`. Timeouts use `anyio.fail_after`, then SIGTERM/SIGKILL
for the SDK's `_ACTIVE_CHILDREN` (`session.reap_children`).

### 1.2 `agentforge.llm` → ported

It fails to import under `.venv310` (`No module named 'dotenv'`). `anthropic`, `openai`, `httpx` and `rich` are
missing too, and there is no `pip`. `llm.py` ports the contract `call_llm(system, user, model, json_schema,
temperature, max_tokens, effort)` and `split_model` (`provider:model`) onto stdlib `urllib`: Anthropic Messages API
and OpenAI chat completions, the same retry and JSON-repair logic, and every call (model, purpose, tokens, seconds)
recorded in the trace. It respects the build deadline. `agentforge` is never imported.

### 1.3 Decision model (`vendor/build-the-builder`, cloned read-only, gitignored)

The repo is a kit copy plus judge tools. These files are identical to ours: `freya.py`, `docs/*`,
`tools/openapi.json`, `tools/score.py`, `public-test-set.json`, `briefs/*`. `tools/run_cases.py` only adds
`--judges`.

| Question | Answer |
|---|---|
| Entry point | `tools/decide.py: decide(text, questions, model="gpt-6-luna", timeout=90) -> dict`; `has_decision_key()`; `DecisionsError(status)` |
| Input | free `text` + `[{"name", "type": "predicate", "instructions"}]`, all questions in one request |
| Output | `{"answers": [{"name", "type", "probability"}], "model", "provider"}`: P(yes), no evidence; `type == "refusal"` = no answer |
| Pass rule | `p >= 0.5` (`results/thresholds.json`) |
| Model / keys | OpenAI `/v1/decisions` (`gpt-6-luna`, `OPENAI_API_KEY`), falling back to OpenRouter (`OPENROUTER_API_KEY`) on 401/402/429/5xx |
| Deps / Python | stdlib only; imports under 3.10 |
| Batching / voting | batched, no voting |
| Calibration | no agreement numbers shipped |

**Adapter** (`judge.load_vendor`, `VendorDecisionBackend`): it loads `decide.py` and `envfile.py` in process under
private module names. A temporary `envfile` alias is needed for `decide.py`'s own import and is removed afterwards. The
patch-judging framing goes into `text`, which works because `decide` is not transcript-specific. A "no"-pass question
passes when the answer is "no" (p < threshold). A refusal counts as no answer, so it fails. The threshold and model are
set in `--models` (`judge_threshold`, `judge_vendor_model`).

### 1.4 Decisions taken (approved "as recommended")

1. **The close record is a lifecycle tool** (`alwaysRunsAt: ["end_of_call"]`), the only form that closes a call when
   the customer hangs up. Its pre-hook recomputes the outcome from state and drops an empty reason. `--close-form node`
   compiles the node form instead (`close_node` → `end`).
2. **`transfer_to_agent` and `add_note` run in engine nodes** with baked values. `create_promise` and `schedule_callback`
   are model-facing, with normalising pre-hooks.
3. **Policy schema additions** (§3). Every canonical testbed key is kept, so `builder/eval/score_policy.py` still scores us.
4. **No judge → nothing applied.** Audit and loop patches are only logged. Judge order: the vendor model, or
   `LocalDecisionJudge` with a loud trace line, or none.
5. **Extraction descriptions are an unlocked zone**; their enum values stay locked.

## 2. Files

| File | Role |
|---|---|
| `run.py` | `main(argv)`: orchestration, wall clock, finalize, exit code |
| `io.py` | args, slot, brief reading/order, output dirs, `Models` (env + `--models` JSON) |
| `trace.py` | `trace.txt` + `events.jsonl` |
| `llm.py` | stdlib `call_llm` port (mockable `TRANSPORT`) |
| `apimodel.py` | tool-API facts: enums, the account list field and item fields, identity response roles |
| `policy.schema.json`, `policy_check.py` | schema validator, API/calendar/quote checks, `complete()` defaults, `repair()` |
| `extract.py` | pass 1, pass 2, one fix round, conservative repair |
| `compiler/` | `graph.py` (nodes), `prompts.py` (generic texts), `runtime.py` (helpers run inside the workflow), `snippets.py` (code assembly), `tools.py` (api_call builders) |
| `zones.py`, `patches.py` | zone registry/apply; patch schema/apply/diff/growth |
| `judge.py`, `judge_agreement.py` | patch judge; agreement vs hand labels |
| `guard.py`, `session.py`, `audit.py`, `patcher.py` | Claude Code sessions in isolated workspaces |
| `analyze.py`, `testgen.py`, `runner.py`, `gate.py`, `loop.py` | the self-test loop |

## 3. Policy schema additions

| Key | Shape |
|---|---|
| `call_variables`, `unused_tools` | list[str] |
| `lines` | `{key: {text, verbatim}}`. Keys: opening, right_party_question, ask_again, condolence, debt_intro, first_ask, first_ask_multi, confirm_question, promise_confirmation, ai, scam, failed_id, paid_reply, change_promise, cannot_record, no_info, id_reason, hardship_sympathy, hint_retry, goodbye. Missing keys get generic fallbacks built from the canonical `sentences` |
| `identity.prompts`, `identity.kinds` | the question to ask; date / year / digits / code / text (inferred from the field name if absent) |
| `attempt_plan` | per attempt `{date: end_of_week_roll\|end_of_week\|deadline\|today\|next_business_day, amount: full_or_floor\|full\|floor, cap_at_deadline}` |
| `weekday_words` | `{next: following_week\|next_occurrence, this: current_week\|next_occurrence}` |
| `retries`, `ask_limits` | counts (defaults: reads 1, create_promise 1, schedule_callback 1; right_party 2, id_decline 1, callback_reask 1, vague_date 1, stop_talking 2, human_offer 1) |
| `extra_rules` | `[{statement, brief_quote, stage}]`. They go into the locked "Brief rules" section of that stage |
| `_source`, `_overrides` | quotes by JSON pointer; overrides by later material |

**Conservative defaults** (`policy_check.repair`, each logged):
- invalid holidays are dropped;
- invalid bands → one band (deadline 5 business days, 1 attempt);
- an invalid callback window → sorted, with `same_day` false;
- an invalid outcome / queue / note value → that route is disabled (the runtime falls back to the nearest outcome key);
- identity questions outside the API enum are dropped;
- a priority order that is invalid → `first_non_paid`;
- a quote that is not found is kept and flagged `UNVERIFIED`;
- a verbatim line that is not found is spoken as non-verbatim.

## 4. Compiled graph (as built)

`[conv]` conversation, `[code]`/`[api]` passthrough, `[end]` end_call. Routing is deterministic (`equals` on
extracted enums or hook-written strings, and counters) except the `refused_<n>` tool-call transitions and the global
intents. Every code node and hook is try-wrapped, has no double braces, and writes string flags.

```
setup [code, initial]    callback window (today if same_day, next business day; hours; later than now), defaults
                         for every var a prompt/condition reads, close state = nr_unclear
opening [conv]           tts: lines.opening + right_party_question (first entry only); who1 ∈ customer,
                         someone_else, not_their_number, customer_died, unclear; turn cap 4
  → identity | mark_nr_deceased | mark_nr_wrong_number | third_party | opening_ask2 (or mark_nr_unclear)
opening_ask2 [conv]      answers a question first (ai/scam line), asks again; who2 → same, unclear → mark_nr_unclear
third_party [conv]       schedule_callback (model-facing; pre-hook: date/time or datetime in the API format;
                         post-hook: cb_state booked/failed, retries); tp_state ∈ gave_up, not_their_number, customer_died
identity [conv]          verify_identity (field enum = API ∩ policy; pre-hook normalises per kind; post-hook: id_state
                         next/retry/passed/failed/error from the API's verified / all / answers-left / hint fields,
                         else a vars counter vs max_wrong; the hint retry is not counted)
  → fetch_account | mark_nv_failed | mark_np_service_error | identity_explain (declines/asks_purpose)
identity_explain [conv]  explains once, same tool; a second refusal → mark_nv_declined
fetch_account [api]      get_account, response_mapping data.<list> → products_raw (flattened keys are read too)
  → fetch_account_retry (failure count / ok false) → mark_np_service_error_hard | facts
facts [code]             per product (API order, renamed fields): band, deadline, attempts, attempt proposals,
                         minimum amount, a 21-day date table, weekday words, facts_text, debt_statement,
                         first_ask_line, pstate/pids, close state = np_refused
negotiate [conv]         tts {{debt_statement}} {{first_ask_line}} (first entry only); create_promise (model-facing;
                         product_id enum from product_id_format; pre-hook: id mapping, cents with round(), date to ISO;
                         post-hook: per-product status, retry rule, neg_state); side ∈ none, hardship, dispute,
                         paid_already, restructuring, human, stop_talking (+ side_product when multi);
                         is_tool_call refused_1..N
negotiate_resume [conv]  the same node with an llm entry (re-entry after code nodes)
side_mark / hardship_mark / stop_count / refused_<n> / resume [code]   per-product status, reset side vars
hardship [conv]          sympathy, support offer → transfer_support | hardship_mark | resume
human_offer [conv]       one "can I help" → transfer_human | resume
pre_transfer [code]      notes before a restructuring transfer (notes_on_restructuring, multi only)
transfer_<canon> [api]   transfer_to_agent with the baked queue; post-hook sets transfer_key
mark_<key> [code]        end_key for the pre-product endings
wrap [code]              open → refused, note queue (multi + notes), close state, closing_line (prefix by outcome +
                         goodbye) → note_next ⇄ note_write [api, add_note baked] → end (or close_node)
end [end]                spoken {{closing_line}}
globals                  g_ai, g_scam (tts → __previous__), g_human (conversation → transfer_human | __previous__);
                         excluded from code/api/end nodes and the opening; g_human also from negotiation nodes
```

Single-product briefs compile without the note loop (notes only when `multi_product` and `note_map`).
Workflow size: about 210 to 240 KB. Each snippet embeds only the runtime helpers and config keys it uses.

## 5. Patch zones (`zones.json`)

| Zone id | Locked | Budget |
|---|---|---|
| `system/core`, `system/forbidden`, `system/honesty`, `system/brief_rules` | yes | |
| `system/global_rules` | no | 1500 |
| `node:<stage>/task`, `node:<stage>/facts`, `node:<stage>/brief_rules`, `node:negotiate/resume_entry` | yes | |
| `node:<stage>/rules` | no | 1200 |
| `node:<stage>/examples` | no | 600 |
| `tts:<node>` | yes | |
| `tool:<tool>/description` | no | 600 |
| `hook:<tool>/msg/<key>` (flat JSON map between `# MSG-BEGIN`/`# MSG-END`; no braces) | no | 300 |
| `transition:refused_<n>/intent`, `global:<g>/intent` | no | 300 |
| `extract:<stage>/<var>/description` | no | 300 |

Zones are shared where nodes share a stage: `negotiate`/`negotiate_resume`, `identity`/`identity_explain`, and
`human_offer`/`g_human`. One patch therefore updates every target. Growth: total zone text may grow at most +15%
per iteration.

## 6. Testgen mapping and gaps

`assemble.build(cfg)` needs these keys: `id, address, identity{questions,max_wrong,swap_hint}, callback{start,end,same_day}, bands,
holidays, outcome_map, queue_map, note_map, close_rule{kind,order}, notes_on_restructuring, product_types{...,floor_pct?},
account_fields, product_id_format, forbidden_before_verify[0], multi_product, max_products, sentences{ai,scam,failed_id},
now_dates`.

| Key | How testgen fills it |
|---|---|
| canonical keys | copied from our `policy.json` |
| `id` | `"tcc"`; `assemble.PREFIX["tcc"] = "T"` is set in memory |
| `now_dates` | synthesized: 5 business days around the first holiday (or today): before a holiday, a Friday, a week's last business day, a Monday, and callback end minus 40 min |
| `floor_pct` | seeded 0.15–0.35, only for types with a last-4 field |
| plan | `assemble.plan_for` is replaced in memory: 36 cases by default, a third multi-product when multi |
| unknown identity fields | `identity_values`/`wrong_value`/`QNAME` are wrapped in memory, with values by kind |

**Families that get dropped** (each one logged):
- a needed outcome key or queue is null;
- attempt 1 is not `end_of_week_roll` (drops Dates and Refusal);
- weekday words differ from the generator's (drops Dates);
- the multi-product close rule is not expressible;
- the generator raises for a family.

**Isolation:**
- Only `engine`, `cases` and `assemble` are imported, with the folder on `sys.path` only during the import.
- No data file is opened (tested with `open` / `Path` spies).
- The generator's state is restored after every build.

## 7. What the analyzer cannot see

Testgen's expected results come from our policy. So a policy error makes the agent and the oracle agree on the wrong
thing. Only the audit can find such errors, which is why it runs before compile.

## 8. Deviations from the T-CC prompt

| Prompt | Done | Why |
|---|---|---|
| close node then end_call | lifecycle close (node form behind `--close-form node`) | approved; hang-ups still close |
| model-facing transfer_to_agent and add_note | engine nodes with baked values | approved; queue and note values and note ordering are graph properties |
| "Product loop: a code node sets current_* and comes back after each promise or note" | per-product status lives in vars; the create_promise post-hook and the code nodes `side_mark`/`refused_<n>`/`stop_count` update it and return to `negotiate_resume` | the brief family reads all promises back together, then records each; a mid-turn node hop after every promise would silence the agent |
| zones forbid placeholders | prompt zones may contain `{{var}}`; the judge rejects unknown ones (`no_unknown_vars`) | the check in §4.7 needs it |
| `io.py`/`trace.py` names | as asked; they live in the package, so they do not shadow stdlib modules | |
| extra files | `apimodel.py`, `session.py`, `compiler/runtime.py` | shared code for the API, the SDK sessions and the in-workflow helpers |
| scoring adapter | `runner.score_calls` imports `testbed/scoring/score_local.py` as code, with our policy as cfg | no data read |
| no-leak allow-list | `tests/template_cc/no_leak_allowlist.json`: two generator family names in `testgen.py` | they are the generator's code API |

## 9. Not verified (needs live runs)

- Extraction quality on clean and raw briefs; audit and patch quality; judge agreement; time and cost per build.
- Platform behaviour:
  - the lifecycle close tool's pre-hook (vars available at end of call, reason dropped);
  - routing on hook-written vars in the same turn;
  - `max_node_turns` with global-node detours;
  - `is_tool_call` refusal transitions;
  - extraction with `min_confidence` 0.6;
  - re-entry of `negotiate_resume` (llm entry with a `prompt`);
  - a 210–240 KB workflow payload.
- The local TestRunner (another session) and the loop's real iteration time.
