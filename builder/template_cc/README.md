# T-CC builder

Template compiler + Claude Code patcher + decision-model patch judge. Design: [DESIGN.md](DESIGN.md).

```
python3 -m builder.template_cc.run <brief> [<more brief material> ...] <tools.openapi.json> [options]
```

Run it with the repo's `.venv310/bin/python` (Python 3.10; claude-agent-sdk installed).

| Option | Default | Meaning |
|---|---|---|
| `--save freya\|none` | `freya` | save + export on Freya (`none` touches nothing on Freya) |
| `--slot NAME` | `tcc-<stem>` | Freya slot (`stem` = parent folder when the file is `brief.*`) |
| `--max-minutes N` | 25 | hard wall clock for the whole build |
| `--audit on\|off` | `on` | Claude Code policy audit, each fix judged |
| `--self-test none\|local` | `none` | `local` needs a TestRunner (`main(..., runner=...)`); it fails fast without one |
| `--max-iters N` | 3 | self-test iterations |
| `--models FILE` | env | JSON overriding `extract`, `analyze`, `patch`, `judge`, `judge_threshold`, `judge_votes`, `judge_vendor_model`, `audit_budget_usd`, `patch_budget_usd` |
| `--out DIR` | `out/template_cc/<slot>/` | outputs |
| `--close-form lifecycle\|node` | `lifecycle` | how close_call runs |
| `--keep-workspace` | off | keep the Claude Code temp workspaces |

Model env vars: `TCC_EXTRACT_MODEL`, `TCC_ANALYZE_MODEL`, `TCC_PATCH_MODEL` (Claude Code), `DECISION_MODEL`
(local judge). Keys: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`; the vendor judge uses `OPENAI_API_KEY` or
`OPENROUTER_API_KEY`; `FREYA_API_KEY` for saving and the validate dry run.

## Outputs (`--out`)

| File | What |
|---|---|
| `policy.pass1.json`, `policy.raw.json` | extractor pass 1, and the final extractor output after `policy_check` |
| `policy.json` | after the audit |
| `workflow.baseline.json` | compiled from the final policy, no prompt patches |
| `workflow.json` | the best (lint-clean) workflow |
| `zones.json`, `zones.final.json` | patch-zone books |
| `patches/` | audit proposals/verdicts/applied; per iteration: bugs, proposed, verdicts, applied; `loop.json` |
| `patch_log.jsonl` | one line per judged patch (inputs, every check, every answer) |
| `build_report.json` | summary |
| `<slot>.workflow.json` | `freya.export_workflow`, with `--save freya` |

Trace: `traces/tcc-<slot>-<YYYYmmdd-HHMMSS>/trace.txt` and `events.jsonl`. They record every model call with a
one-line purpose, every check, what Freya created, and every self-test run (or "no self-test (none)").

Exit code: 0 when a lint-clean workflow exists (and was saved, when asked); 2 for input errors or a missing local
tester; 1 otherwise.

## Judge agreement

```
python3 -m builder.template_cc.judge_agreement out/template_cc/<slot>/patch_log.jsonl labels.jsonl
```

## Tests

```
.venv310/bin/python -m pytest tests/template_cc -q
```

Offline: there is no network call and no real model, SDK or decision-model call in the tests. The testbed configs are used only in tests,
to derive policies for compiling and linting.
