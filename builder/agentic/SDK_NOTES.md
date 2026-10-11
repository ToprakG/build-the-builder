# Claude Agent SDK: verified facts for the agentic builder

Installed: `claude-agent-sdk` **0.2.165** (`_version.py`), bundled CLI **2.1.294** (`_cli_version.py`), on
CPython **3.10.20** in `.venv310/` (the target machine runs 3.10.12). Paths below are relative to
`.venv310/lib/python3.10/site-packages/claude_agent_sdk/`. Read from source on 10 Oct 2026, not from memory.

## CLI

- The CLI ships inside the wheel: `_bundled/claude` (236 MB). `_find_cli` tries `cli_path`, then the bundled
  binary (`_internal/transport/subprocess_cli.py:255`, `:341`). No separate `claude` install is needed.
- `query()` always runs the CLI in streaming mode internally (`_internal/client.py:142`), so in-process SDK MCP
  servers work with a plain string prompt (`_internal/client.py:101-107`).

## `query` and `ClaudeAgentOptions` (`types.py`)

`query(*, prompt: str | AsyncIterable[dict], options: ClaudeAgentOptions | None = None, transport=None)`
(`query.py:13`), an async iterator of messages.

| Field | Line | Type and behaviour |
|---|---|---|
| `tools` | 1974 | `list[str] \| ToolsPreset \| None`. The **base set** of built-in tools (`--tools a,b`). `[]` = none. This is what removes Bash etc. |
| `allowed_tools` | 1985 | `list[str]`. Tools **auto-approved** (`--allowedTools`). Does not restrict which exist. |
| `system_prompt` | 1996 | `str \| SystemPromptPreset \| SystemPromptCustom \| SystemPromptFile \| None`. **`None` sends `--system-prompt ""`** (subprocess_cli.py:576-578): an empty system prompt, no Claude Code preset. |
| `append_system_prompt` | none | Not a field. Use `{"type": "preset", "preset": "claude_code", "append": "..."}` (`SystemPromptPreset`, line 40; mapped to `--append-system-prompt` at subprocess_cli.py:586). |
| `mcp_servers` | 2010 | `dict[str, McpServerConfig] \| str \| Path`. |
| `strict_mcp_config` | 2018 | `bool`. Ignore every MCP config except `mcp_servers`. |
| `permission_mode` | 2025 | `"default" \| "acceptEdits" \| "plan" \| "bypassPermissions" \| "dontAsk" \| "auto"` (line 25). `dontAsk` = "deny if not pre-approved". |
| `max_turns` | 2050 | `int \| None`. |
| `max_budget_usd` | 2056 | `float \| None` (`--max-budget-usd`). A hard cost cap per run. |
| `disallowed_tools` | 2063 | `list[str]`. Removed from the model's context. |
| `model` | 2070 | `str \| None`. |
| `cwd` | 2096 | `str \| Path \| None`. |
| `env` | 2124 | `dict[str, str]`, merged over the parent environment. |
| `hooks` | 2176 | `dict[HookEvent, list[HookMatcher]] \| None`. |
| `setting_sources` | 2297 | `list["user" \| "project" \| "local"] \| None`. `None` = all; `["project"]` = the cwd's `.claude/` only. Must include `"project"` for project skills and CLAUDE.md. |
| `skills` | 2309 | `list[str] \| "all" \| None`. A list injects `Skill(<name>)` into `--allowedTools` (subprocess_cli.py:527-567). It defaults `setting_sources` to `["user","project"]` **only when that is `None`**, so our explicit `["project"]` stands. The docstring says it is "a context filter, not a sandbox". |

## In-process MCP tools (`__init__.py`)

- `tool(name, description, input_schema: type | dict, annotations=None)` decorator (line 251) → `SdkMcpTool` (line 241).
  The handler is `async def h(args: dict) -> {"content": [{"type": "text", "text": ...}], "is_error"?: bool}`.
- `create_sdk_mcp_server(name, version="1.0.0", tools=[...]) -> McpSdkServerConfig` (line 491). It is passed as
  `mcp_servers={"fb": server}`.
- Tool names seen by the model and used in `allowed_tools`: **`mcp__<server key>__<tool>`**, e.g.
  `mcp__fb__lint_workflow`. The format string `mcp__<name>__<tool>` is in the bundled CLI's own strings.

## Hooks (`types.py`)

- `HookMatcher(matcher: str | None, hooks: list[HookCallback], timeout: float | None = 60)` (line 606). The matcher
  is a tool name or `A|B|C`.
- `HookCallback = Callable[[HookInput, str | None, HookContext], Awaitable[HookJSONOutput]]` (line 594).
- `PreToolUseHookInput`: `tool_name`, `tool_input: dict`, `tool_use_id`, plus `session_id`, `cwd`, ... (line 333).
- **Deny** (line 437 and line 541):
  ```python
  return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                 "permissionDecision": "deny",
                                 "permissionDecisionReason": "outside the workspace"}}
  ```
  Return `{}` to let the normal permission flow decide.
- PreToolUse hooks match MCP tool names too (`mcp__fb__.*`). We still give the MCP tools no path arguments, so
  the guard does not have to parse their inputs.

## Messages (`types.py`)

- `AssistantMessage(content: list[TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock], model: str, usage, message_id, stop_reason, ...)` (line 1139).
- `UserMessage(content, tool_use_result, ...)` (line 1124): carries the tool results.
- `SystemMessage(subtype, data)` (line 1154). The first one is `init`.
- `ResultMessage(subtype, duration_ms, duration_api_ms, is_error, num_turns, session_id, stop_reason, total_cost_usd, usage, result, model_usage, permission_denials, errors, terminal_reason, ...)` (line 1340).
- Blocks: `TextBlock.text` (955), `ThinkingBlock.thinking` (962), `ToolUseBlock(id, name, input)` (970),
  `ToolResultBlock(tool_use_id, content, is_error)` (979). All are dataclasses, so `dataclasses.asdict` works for the jsonl trace (as in the kit's `agent_builder.py`).

## Shutdown and timeouts (`_internal/transport/subprocess_cli.py`)

- `close()` (line 962) closes stdin, waits, then escalates to `terminate()` and then `kill()`, inside an **anyio** shielded
  scope. Its docstring warns that a **raw asyncio cancellation (`asyncio.wait_for`) can skip that escalation**. The
  child then stays in `_ACTIVE_CHILDREN` and is only SIGTERMed by an `atexit` reaper (lines 54-68).
- So the run timeout uses **`anyio.fail_after`** (an anyio cancel scope, which the shield honours) around the
  consumption coroutine instead of `asyncio.wait_for`. As a backstop, after the timeout `run.py` sends SIGTERM and then
  SIGKILL to anything still left in `_ACTIVE_CHILDREN` (a private name, read with `getattr` and a default).

## Decisions taken from these facts

1. `tools=["Read","Write","Edit","Glob","Grep","Skill"]` removes Bash, WebFetch, WebSearch, Task/Agent and
   NotebookEdit from the model's context. `disallowed_tools` repeats them as a second layer.
   `allowed_tools` = the same five file tools + `mcp__fb__*` names. `permission_mode="dontAsk"`.
2. `system_prompt={"type":"preset","preset":"claude_code","append": <short role>}`. Leaving it `None`, as the kit
   example does, would run the builder with an **empty** system prompt.
3. `skills=["build-phone-agent"]` with `setting_sources=["project"]`. Whether `Skill` must be listed in
   `--tools` cannot be checked offline, so the task prompt also names `.claude/skills/build-phone-agent/SKILL.md`
   for a direct Read. The build works whether or not the Skill tool loads.
4. `strict_mcp_config=True`, so no stray `.mcp.json` can add servers.
5. `max_budget_usd` is exposed as `--max-usd` (optional).

## Not verifiable offline

- That `--tools` accepts `Skill` and that the skill appears in the listing.
- The real turn count, wall time and cost of a build.
- Whether the guard hook fires before the CLI's own permission check for every tool (documented, not exercised).
