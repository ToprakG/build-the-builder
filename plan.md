# T-CC: 90% on the 90-case set, without forcing the call

Target: **81/90** on the nine-brief sample (brief A, brief B, f1–f7, ten personas each). That sample is the private set as well as the kit briefs. The public 10 is a checkpoint, not the goal.

Today that sample is **59/90 (66%)** with the Freya judge. Amounts are 3/9, human request 4/9, dates 5/9. Deceased is already 9/9. Field accuracy is 78–92%. The real extraction misses are f7 (read as multi-product) and f5 (raw email chain).

A second model-written graph is the wrong way to close this. CC Brief A moved from 8/10 to 4/10 across two builds. T-CC’s compiler is stable once the policy is stable. The variance we still have comes from extraction, from a patcher that rewrites prompts freely, and from the model ignoring a rule that is only a sentence.

## What we will not do

- Hardcode a case, a persona, a bank name, or a testbed date into the builder.
- Turn every amount and date decision into a code gate. The facts node already computes the band, the deadline, and the date table. The model keeps the phrasing.
- Add a new canonical outcome key when a brief uses a new word. Unmapped values stay in `other_rules` and the tool enum.
- Let a reviewer pass block the build. Findings go to the patcher.
- Trust the current audit. On seven fake briefs it said the policy was fine and missed f7.
- Optimize for the local judge. It scores the same calls 44/90. The number we are chasing is the Freya judge. The local judge is only the cheap first pass.

## 1. Read the failures first

Before any new fragment, label the styled misses that are already on disk. Each one gets a family and a cause: prompt, plan field, graph order, or platform.

Platform misses stay out of the patch set. A sandbox that drops a hooked tool call will not move because a sentence changed. Both arms already lose that shape of case.

The three families to spend the budget on are amounts, human request, and dates.

## 2. Light, brief-agnostic fixes

These are guidance and order, not new business rules. None of the example phrases below are written into the compiler. The self-test generates them from the plan: the brief’s own intent examples, the brief’s date fallback, and the five styles. A brief that never says “real person” still gets a human-request case from whatever words that brief uses.

**Human request.** `g_human` already exists and is allowed during identity. Freya still lets the identity node’s own transitions win. Give the global node priority so “I want a real person” is handled before those transitions. Test that line before identity, in all five styles.

**Identity.** Add a guidance fragment, as prompt text. A question back gets a short answer and the same question again, and does not count as a failed attempt. A partial answer gets a request for the missing part only. `verify_identity` is called with the fields that question needs, not a half answer. A tiny code check may refuse an empty call. The model still says the words. Test `asks_back`, `pieces`, and `corrects`.

**Intents.** The extractor writes each intent with a definition and two or three examples from the brief, including a near-miss. “Can’t pay full” and “discount” must not become restructuring. The side-intent text already says this once. The extractor has to supply the brief’s own examples, or a renamed brief drifts.

**Vague dates.** The extractor records the brief’s fallback. If the brief is silent, the fallback is the deadline. The negotiate prompt already says to ask once and then propose the latest date. Test “sometime soon”, “next week”, and “end of month”. Leave the wording to the model.

## 3. Extractor

Run extraction twice at temperature 0. Where the two copies disagree, a verifier keeps a value only by quoting the brief. Quoted fields that the code reader already found still win over the model.

The plan object carries the new fields: intent definitions and examples, the date fallback, identity fields, and attempt limits.

Score per-field accuracy on f1–f7. Spelling of a key is not a miss. A wrong product count, a wrong identity field, a wrong band, or a wrong close code is a miss. Fix f7’s single-versus-multi-product read and f5’s raw email chain before measuring calls again.

This is the variance control. The compiler does not invent a different graph if the policy stays the same.

## 4. Builder

The T-CC compiler writes the structure. The model fills unlocked prompt zones from the plan and from fragments. It does not invent nodes.

A reviewer reads the brief and the workflow and does not see the builder’s own rule list. Its findings feed the patcher. Lint, a Freya dry run, publish, and export still happen. The export keeps the real bank host, `freya-bank-mock.freyavoice.workers.dev`.

## 5. Architecture

Two different runs, both required. A unit test is one turn. A Freya simulation is the whole call. A patch that passes unit tests and loses simulations is discarded.

The builder’s only inputs are the brief files and the tool OpenAPI. It never opens `cases.json`, `config.json`, `public-test-set.json`, or a gold workflow. There is no branch on brief id, bank name, or case id. A new brief goes through the same functions.

```
brief files + tools.openapi.json
        │
        ▼
┌─ EXTRACT (outside the call) ─────────────────────────────────────────┐
│  pass 1 and pass 2, temperature 0                                    │
│  disagree → VERIFIER, keeps a value only with a quote from the brief │
│  code-read quoted fields override the model                          │
│  out: plan (policy)                                                  │
│       intent definitions + near-miss examples                        │
│       date fallback, identity fields, attempt limits                 │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ COMPILE (deterministic) ────────────────────────────────────────────┐
│  T-CC compiler writes every node, transition, tool URL, and enum     │
│  model fills unlocked zones from the plan and from fragments         │
│  baseline workflow is written before any audit or patch              │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ REVIEW (does not block) ────────────────────────────────────────────┐
│  a separate session reads the brief and the workflow                 │
│  it does not read the builder's own rule list                        │
│  findings are patch proposals, not a pass/fail                       │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ SELF-TEST CASES (from the plan only) ───────────────────────────────┐
│  testgen: each policy family × the 5 styles                          │
│  personas, amounts, and dates are synthesized from the plan          │
│  train / held-out split                                              │
│  families the generator cannot express are logged and skipped        │
└──────────────────────────────────────────────────────────────────────┘
        │
        ├─ LAYER 1  Freya unit test (one turn)
        │    seedCallId + the messages up to the failing turn
        │    asks: was this tool called, was this fact said
        │    cheap filter. Never the score we ship.
        │
        └─ LAYER 2  Freya simulation (whole call)     ← the score
             freya.run_simulation, 3 reps
             customer is Freya's persona for that generated case
             bank ledger + Freya judge
             a case counts only when all 3 reps pass
             store {case_id, family, style, passes}/3
             infra errors (5xx, tunnel, sandbox drop) are unscored,
             not failures, and are not sent to the patcher
        │
        ▼
┌─ PATCH LOOP (under 30 min, best workflow kept) ──────────────────────┐
│  1. Classify each simulation failure: prompt, plan field,            │
│     graph order, or platform. Platform rows are not patched.         │
│  2. Mint a unit test from the failing simulation. Keep it only if    │
│     it reproduces.                                                    │
│  3. Patcher may change prompts, plan fields, and fragments.          │
│     It cites the simulation and a brief clause.                      │
│     It must not name the case. No-leak check rejects it if it does.  │
│     A new fragment is allowed only when none of the existing ones    │
│     fits. The compiler still owns the graph.                         │
│  4. Judge each patch: brief supports it, it is not case-specific,    │
│     it touches an unlocked zone.                                     │
│  5. Re-run LAYER 1 on the minted tests. Any miss → discard.          │
│  6. Re-run LAYER 2 on train and held-out, 3 reps.                    │
│  7. Gate on LAYER 2 pass counts only: targeted cases improve,        │
│     no previously clean case loses a pass. Else discard.             │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ FINALIZE ───────────────────────────────────────────────────────────┐
│  lint, Freya dry run, publish, export                                │
│  tool URLs in the export stay on the real bank host                  │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ EXTERNAL SCORE (not an input to any step above) ────────────────────┐
│  9 briefs × 10 personas = 90. Freya simulation, Freya judge.         │
│  This is the only place the testbed cases are read.                  │
│  A and B use the hosted bank. f1–f7 use the mock bank for that       │
│  brief, reached through a tunnel. The tunnel is transport.           │
│  It is not a rule.                                                   │
└──────────────────────────────────────────────────────────────────────┘
```

What talks to Freya, and what does not:

| Step | Freya unit test | Freya simulation | Reads testbed cases |
|---|---|---|---|
| Extract, compile, review | no | no | no |
| Self-test, layer 1 | yes, one turn | no | no |
| Self-test, layer 2 | no | yes, full call, 3 reps | no |
| Gate | filter only | yes, this decides | no |
| External 90 | no | yes | yes, here only |

The code today has the two layers split the wrong way around. `NullRunner` refuses to fall back to Freya simulations. `VendorRunner.run` plays the call through the chat API and the local judge. `run_units` is the one-turn replay. The loop can stop on unit tests and never ask the simulator. The change is a `FreyaSimRunner` that implements `TestRunner.run` with `freya.run_simulation`, three reps, ledger plus Freya judge. Unit tests stay as `run_units`. The gate in `loop.py` already decides on `runner.run` pass counts. That runner has to be the simulator.

Bank routing is general. The compiled workflow’s tool URLs point at the real bank. For a private brief, the self-test publishes a copy whose tool URLs point at that brief’s mock bank. The original export is not rewritten. No brief id is special-cased inside the compiler.

## 6. Failure to fix

The patcher sees simulation failures, not a hand-picked list. The classifier’s labels are the same four for every brief: prompt, plan field, graph order, platform.

A unit test is how we check that a patch still reproduces. It is not how we decide the patch worked. That decision is the held-out simulations, three reps, pass counts compared with the best workflow so far.

Cap the rounds. Stay under 30 minutes. On error or timeout, keep the best workflow.

## How we know it worked

| Check | Done when |
|---|---|
| 90-case Freya score | 81/90 or better, one pass, Freya judge |
| Second build of the same briefs | within about 3 cases of the first |
| f7 policy | single product, matching the brief |
| f5 policy | the email chain’s real fields, not a guessed table |
| Human, before identity | passes in all 5 styles |
| Vague date | “sometime soon”, “next week”, “end of month” follow the brief’s fallback |
| Self-test vs 90 | layer 2 (Freya simulations, 3 reps) moves with the 90-case score on A, B, and a private slice |
| Unit tests alone | never enough to keep a patch |

Public A at 9/10 is a welcome checkpoint. It is not a substitute for the 90.
