# Variant A-agent: the skill-driven agentic builder

Claude (through the Claude Agent SDK) reads a bank's documents and its tool API file, then writes a complete
Freya workflow, guided by our skill `build-phone-agent` and checked by our own tools. Nothing here runs a
simulation.

## Run

Python 3.10+, `pip install claude-agent-sdk` (it bundles the Claude CLI), a Claude login or `ANTHROPIC_API_KEY`,
and for `--save freya` an exported `FREYA_API_KEY`.

```bash
# a fake brief, files only (nothing on Freya)
python3 builder/build.py testbed/fake_briefs/f5-halden-email/brief.md testbed/fake_briefs/f5-halden-email/tools.openapi.json --save none
python3 builder/eval/score_policy.py out/agentic/agentic-f5-halden-email/policy.json testbed/fake_briefs/f5-halden-email/config.json

# Brief A, saved to Freya (the same slot always updates the same workflow)
python3 builder/build.py briefs/brief-a.md tools/openapi.json --save freya --slot agentic-brief-a

# Brief B builds on Brief A: pass both, in override order
python3 builder/build.py briefs/brief-a.md briefs/brief-b.md tools/openapi.json --save freya --slot agentic-brief-b
```

Arguments: the tool API is the one file whose JSON has a top-level `openapi` key, in any position. Every other file
is brief material (`.md`, `.txt`, `.eml`, `.csv`, `.json`, ...), read as UTF-8 in the given order.

| Option | Default | |
|---|---|---|
| `--variant` | `agentic` | `build.py` is a dispatcher; other variants register in `VARIANTS`. |
| `--save` | `freya` | `none`: write files only. |
| `--slot` | `agentic-<stem>` | `<stem>` = the last brief's stem, or its folder when the stem is `brief`. |
| `--max-minutes` | `16` | Hard wall clock for the build agent. With review (5) and fix (7) the total stays under the kit's 30. |
| `--review` | `on` | Fresh-context review and at most one fix round (see below). |
| `--review-minutes`, `--fix-minutes` | `5`, `7` | Wall clocks of the review and the fix round. |
| `--model` | `$BUILDER_MODEL` or `claude-opus-5-5` | |
| `--max-usd` | none | Optional cost cap (`max_budget_usd`). |
| `--out` | `out/agentic/<slot>/` | |
| `--self-test` | `none` | The only value for now. |
| `--keep-workspace` | off | Keep the temp workspace for inspection. |

Exit code 0 when a lint-clean workflow was produced (and saved with `--save freya`); 1 otherwise; 2 for bad arguments.

## Outputs

In `--out`:
- `workflow.json`: the final workflow, without `agent_config`. The local tester consumes this file.
- `policy.json`: the extraction in our canonical schema (`skill/.../reference/policy-schema.md`).
- `rules.json`: every rule with its quote and where it is implemented.
- `review.json`, `findings.json`: the reviewer's own rule list and the findings code derived from it.
- `build_report.json`: status, timings, lint counts and warnings, validate result, fallback used, the Freya
  `workflow_id` / `agent_id` / version when saved, and the agent's turns, cost and usage.
- `workflow.rejected.json`: the agent's final file when it did not lint and `last_good` was used instead.
- `<slot>.workflow.json`: with `--save freya`, the workflow as stored on the platform (`freya.export_workflow`).

## Trace

`traces/<slot>-<YYYYmmdd-HHMMSS>/` (committed, as the kit asks):
- `messages.jsonl`: every SDK message plus builder events (workspace, tool checks, denials, Freya actions).
- `trace.txt`: start and end time; one line per model call (model + first words, or the tool it called); tool results
  in short; denials; every lint / coverage / snippet / validate check; what was created or updated on Freya; the
  explicit "Simulations: none" note; turns, cost and usage from the `ResultMessage`.

## How a build runs

1. `builder/common/io.py` splits the arguments, names the slot and fills a fresh `tempfile.mkdtemp(prefix="fb-build-")`
   with exactly: `input/brief-N.<ext>` (renamed), `input/tools.openapi.json`, `docs/` (the three kit docs),
   `examples/pattern_demo.*` (invented values), `.claude/skills/build-phone-agent/`, and an empty `out/`.
2. `run.py` starts the SDK with `cwd` = that directory, `setting_sources=["project"]`, `skills=[...]`, base tools
   `Read Write Edit Glob Grep Skill` only, our MCP tools, `permission_mode="dontAsk"`, `strict_mcp_config`, the
   Claude Code system prompt plus a short role (`prompt.py`), and the task prompt.
3. The agent follows `SKILL.md`: read → extract (`policy.json`, `rules.json`) → design → build → check and fix with
   `lint_workflow`, `run_snippet`, `check_coverage`, `validate_workflow`, `time_left`.
4. A wall clock (`anyio.fail_after`) stops the agent; any CLI child still alive gets SIGTERM, then SIGKILL.
4b. **Review** (`review.py`): a separate `query()` in its own temp workspace with its own guard. It gets the briefs,
   the tool API, the kit docs and the workflow, **not** `rules.json` or `policy.json`, so it does not inherit the
   builder's reading of the brief. It writes its own rule list with verdicts and node ids (`review.json`). Code
   compares that list with the builder's `rules.json`: brief rules the builder never listed and defects the reviewer
   found become `findings.json`. If there are findings, **one** fix round resumes the builder's own session
   (`resume=<session id>`) with the findings; finalize then lints as usual.
5. Finalize (deterministic, in `run.py`): take `out/workflow.json` if it lints with 0 errors, else
   `out/last_good.workflow.json`; strip `agent_config`; validate dry run if a key is set; with `--save freya`
   `freya.save_workflow(slot, wf, state_file=<repo>/.freya-agents.json)` and `freya.export_workflow`; copy the
   outputs; write the report; close the trace; delete the workspace.

## Isolation guarantees

- **The builder never sees test answers.** Its workspace is a temp directory outside the repo, holding only the files
  listed above. Briefs are renamed, so file and folder names leak nothing. The kit's `base-agent.json` is not copied
  (it carries Brief A's values); the example is our invented `pattern_demo`.
- **Guard hook** (`guard.py`, PreToolUse, on every tool): Read/Glob/Grep only inside the workspace (symlinks resolved,
  `..` and absolute patterns refused); Write/Edit only under `out/`; any path naming `testbed`, `cases.json`,
  `config.json`, `oracle`, `public-test-set`, `.env` or `agentforge` is denied even inside. Every denial is traced.
- **No shell, no web, no subagents**: they are not in the base tool set, are listed in `disallowed_tools`, and
  `dontAsk` refuses anything not pre-approved.
- **MCP tools take no paths**: each is bound to the workspace's fixed files; the agent passes only node ids, tool
  names and JSON values.
- **No hardcoding**: `tests/agentic/test_no_leak.py` builds a blacklist from every testbed config (bank names,
  holidays in ISO and spoken form, enum / queue / note / code values, identity fields, renamed tool fields, fixed
  sentences) plus Brief A/B's bank and holidays, and asserts none appears in the skill, the templates, the task prompt
  or the pattern_demo files. Generic kit vocabulary and the schema's canonical keys are exempt; the few generic words
  that remain are in `tests/agentic/no_leak_allowlist.json` with a reason each.

## Files

```
builder/build.py                 CLI dispatcher (variant registry)
builder/common/io.py             argument split, slot naming, workspace copy
builder/common/trace.py          messages.jsonl + trace.txt
builder/agentic/run.py           SDK options, timeout, finalize
builder/agentic/guard.py         PreToolUse guard
builder/agentic/mcp_server.py    in-process MCP server `fb` (BoundTools holds the logic)
builder/agentic/prompt.py        task prompt and role
builder/agentic/tools/           stdlib-only: openapi_summary, lint, snippets, coverage, validate, clock
builder/agentic/skill/build-phone-agent/   SKILL.md, reference/, templates/calendar.py, templates/fragments/
builder/agentic/SDK_NOTES.md     what the installed SDK really does, with file:line references
builder/eval/score_policy.py     policy.json vs testbed config.json, field by field
tests/agentic/                   offline tests; fixtures/make_pattern_demo.py regenerates the demo and fragments
```

## Tests

```bash
python3 -m pytest tests/agentic -q      # offline: no network, no SDK calls
```

`lint` is calibrated on the kit's `examples/base-agent.json` (accepted by the platform: 0 errors) and on about 27
broken mutations of it. If a lint rule ever flags `base-agent.json`, the rule is wrong.
