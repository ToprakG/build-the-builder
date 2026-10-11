# Freya cookbook: verified patterns

Each pattern names the kit doc section it comes from (`wf` = `docs/workflow-format.md`, `tc` =
`docs/tools-and-code.md`). The working example of all of them together is `examples/pattern_demo.workflow.json`
(with its invented tool API `examples/pattern_demo.openapi.json`). Ready-made pieces are in
`.claude/skills/build-phone-agent/templates/fragments/`. Copy shapes, never values: every URL, field, enum,
operationId and sentence comes from your brief and `input/tools.openapi.json`. Names in the fragments are placeholders.

## Workflow shape (wf "Top-level keys", "Node types")

- Top level: `initial_node`, `nodes` (object keyed by node id), `system_prompt`, `global_nodes` (object),
  `test_variables`, `tools`. **No `agent_config`.** No `null` values: leave an optional field out.
- Node key = node `name`, `snake_case`. `node_type` is `conversation`, `api_request`, `code` or `end_call`.
- `tools` only on `conversation` nodes. `pre_actions` only on `api_request` and `code` nodes. The platform silently
  drops the other combination.
- `test_variables`: declare **every** call variable the documents list, with an invented sample value. Do not add a variable they do not list.

## 1. An `api_call` tool (tc "Tool function shape", "How a parameter gets its value", "api_call body field rules")

```json
{"type": "function", "config": {"timeoutMs": 12000, "maxRetries": 0},
 "function": {"name": "<tool name from the API>", "handler": "__function__:api_call",
   "description": "<what it does>. CALL it <trigger>.",
   "parameters": {"type": "object", "properties": {
     "url": {"type": "string", "default": "<servers[0].url + path>"},
     "method": {"type": "string", "default": "POST"},
     "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
     "body": {"type": "object", "required": ["<required fields>"], "properties": {
       "<call variable>": {"type": "string", "default": "{{<call variable>}}"},
       "<model field>": {"type": "string", "enum": ["<subset of the API enum>"], "description": "<what to put>", "default": "{{<model field>}}"}
     }}}}}}
```

- `url` and `method` use `default`. **`const` does not bake** and an `api_call` with a `const` URL fails.
- Each body field is its own leaf under `body.properties` with its own `default`. Body keys must be API fields;
  every API-required field must be present.
- A call variable the documents list is baked under its own name: `"default": "{{<call variable>}}"`.
- A model-filled field: `"default": "{{<field name>}}"` where no variable of that name is ever set. If a variable of
  that name exists when the node is entered, the value is baked and the model never sees the field.
- Narrow an enum to the values this node may use. It must stay a subset of the API's enum.
- `maxRetries: 0` on every tool whose HTTP method writes. A GET or HEAD may retry only as the brief allows.
- The model's arguments land in `<tool>_args.<field>`; results in `<tool>_response.data.<field>` (flattened, booleans
  as strings).

## 2. Passthrough `api_request` node (tc "A pre_actions node", wf "Passthrough nodes")

`passthrough: true`, `passthrough_default_transition`, `on_enter: {"type": "silent"}`, the call in `pre_actions`,
**`response_mapping` at node level** (a mapping nested in the pre_action is ignored), paths start with `data.`, and a
**final `always` transition** (or the call deadlocks). Fragment: `passthrough_api_node.json`.

Route on the node's counters: `<node id>_failure_count` greater than 0, before the `always`.

**Lists from a `pre_actions` call (seen in a practice run, 10 October):** a node-level mapping of a list
(`data.<list>` → a variable) left the variable **unset**. The list existed only as flattened keys named after the
**node**: `<node id>_response.data.<list>.0.<field>`, `<node id>_response.data.<list>.1.<field>`, ... Scalars map
fine. Always rebuild lists in code with `list_from_vars(vars, '<mapped name>', ['<node id>_response.data.<list>',
'<tool name>_response.data.<list>'])` from `templates/calendar.py`, and test the facts node with `run_snippet` on the
flattened form too (pass the flattened keys in `vars_json`, without the mapped variable).

## 3. Code node (tc "Code nodes and code_exec", "Rules for snippets")

```json
{"name": "facts", "node_type": "code", "passthrough": true, "passthrough_default_transition": "<next>",
 "on_enter": {"type": "silent"},
 "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
   "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": "<python>"},
                                                   "timeout_ms": {"type": "number", "default": 10000}}}}}],
 "transitions": [{"target_node": "<next>", "description": "always", "condition": {"operator": "always"}}]}
```

- Read with `vars.get('x')`, write with `vars['x'] = ...`. Every write becomes a variable for later nodes and
  `{{x}}` in prompts. `json`, `re`, `datetime` are importable.
- **No `{{` or `}}` anywhere in the code**: a nested dict literal ending in `}}` counts. Use `dict(...)` or add a space.
- Wrap the body in `try`/`except Exception` and write a fallback for every variable a later prompt or condition reads.
- Write **strings** for routing flags (`'passed'`, `'failed'`), never Python booleans.
- A code node's automatic keys are named after the **node**: `<node id>_success_count`.
- A list result may be missing under its mapped name and present only as flattened keys
  (`<node id>_response.data.<list>.<i>.<field>`): read it with `list_from_vars` from `templates/calendar.py`.
- Paste helpers from `templates/calendar.py` above your snippet. Test every snippet with `run_snippet`.

## 4. Post-hook: `what_to_do_next` (tc "Pre and post Python hooks", "Tool results and the next turn")

The agent model sometimes says nothing after a tool result, or ignores a negative result. Fix: a
`post_process_code` on the tool that drops headers and adds one plain instruction:

```python
try:
    result.pop('header', None)
    d = result.get('data')
    if isinstance(d, dict):
        if str(d.get('ok')).lower() == 'true':
            d['what_to_do_next'] = 'Recorded. Say the confirmation sentence, then continue.'
        else:
            d['what_to_do_next'] = 'NOT recorded. Do not say it was recorded. Try once more.'
except Exception:
    pass
```

- `result` is the envelope `{status, data, message, status_code, header}`; the API body is `result['data']`.
- A post-hook may read and write `vars` (seen on 10 October): use it to keep state, e.g. a promise counter, a
  failure counter, the close outcome so far.
- Hooks exist only on `api_call` tools; the platform strips them from other handlers.
- Say in the prompt: "Follow what_to_do_next in a tool result."

## 5. Pre-hook: normalise the model's values (tc "Pre and post Python hooks")

`pre_process_code` gets `vars` and `config` (`url`, `method`, `headers`, `body` after substitution). Rewrite
`config['body']` in place:

- amount in major units → integer cents with `round()`, never `int()` (`to_cents`)
- a date the model wrote as words → `YYYY-MM-DD`; a time → `HH:MM`; a date and time → `YYYY-MM-DDTHH:MM`
- an identity answer: strip spaces, upper-case codes, keep only digits where the API wants digits
- drop a field the brief says to leave out for some values (e.g. no reason with a given outcome)

A pre-hook cannot stop a call. Do not silently change a value the customer confirmed: validate before the read-back.

## 6. Transitions on tool results (wf "Do not route on a boolean", tc "Routing on tool results")

- Routing runs again right after a tool result: a transition on a result variable fires in the same turn.
- Booleans arrive as `"true"` **or** `"True"`: test both, in one `or` group, or route on a string your post-hook
  writes (`vars['id_state'] = 'passed'`). Prefer the string.
- Variable conditions go inside `{"logic": "or", "conditions": [...]}`. For AND, nest an `and` group inside the
  top-level `or`. Never `and` at the top level.
- Counters: `<tool>_success_count`, `<tool>_failure_count` (tools in `tools`), `<node>_success_count` (pre_actions).
  Use `greater_than` with them.
- Deterministic conditions first, `matches_intent` last. `matches_intent` judges only the **latest user message**:
  never use it to leave a node after an action; extract a follow-up enum with a default such as `stay` and branch
  with `equals`.

## 7. Fork on an extracted enum (wf "Fork on an extracted enum")

`extraction_schema: [{"name": "who", "type": "string", "enum_values": [...], "description": "...",
"min_confidence": 0.6}]`, then one `equals` transition per value. **Set `min_confidence` on every field** (the default
0.95 silently drops values). Fragment: `conversation_node_extraction_fork.json`.

Dates: `type: "date"` resolves "next Friday" against the platform's **real clock**, not the call's `now`. Extract
the customer's words as a `string` and resolve them in a code node or in the facts block from `vars['now']`.

## 8. Global nodes (wf "Global nodes")

An interrupt that can happen anywhere (robot question, scam question, wants a person): `condition` (often
`matches_intent`), `description`, `excluded_nodes` (every terminal, every code/api node, and nodes where it would
harm, e.g. identity), `passthrough: true` + `passthrough_default_transition: "__previous__"` + a final
`{"target_node": "__previous__", ... "always"}` for a fixed reply. `__previous__` is allowed only in global nodes.
Fragment: `global_node.json`.

**Stage node for a route with required lines** (sympathy + an offer, an explanation): the trigger routes to a node
whose `on_enter` speaks first; its own enum (`stay` / `accepted` / `declined`) decides the ending. Fragment:
`stage_node_offer.json` (with the code node in front that sets the close state).

## 9. Ending the call (wf "End-call nodes", tc "Lifecycle tools")

- `end_call` node: `on_enter: {"type": "silent"}`, `transitions: []`, `terminal` with handler
  `__function__:end_call`. The farewell comes from `terminal.function.parameters.properties.spoken_message`
  (`message_mode: "custom"`), or the agent says goodbye in the previous node (`message_mode: "none"`).
  The terminal's `function.name` becomes the call's `endedReason`.
- A conversation node may also offer an `end_call` tool (a terminal in its own right).
- **Close record, recommended form:** the close tool (the API's operationId) as a workflow-level lifecycle tool,
  `config.alwaysRunsAt: ["end_of_call"]`. The engine runs it once, after the call, even when the customer hangs up.
  Bake its fields from variables your code and hooks keep up to date (`{{close_outcome}}`); a `pre_process_code`
  derives the final values from state and drops fields that must be absent. Do **not** also offer the close tool in a
  node. If a placeholder is still unset at the end, one model call fills it from the conversation.
- Node form (only when the brief forces it): a passthrough `api_request` node that runs the close tool, followed only
  by an `end_call` node. Every path must pass one.

## 10. Prompts (wf "Prompts")

- `system_prompt`: identity, language, tone, forbidden words before verification, honesty rules, "after every tool
  result speak or call the next tool; one tool per message; follow what_to_do_next".
- `task_message`: this node's job only. Name each tool of the node and when to call it. Never name another node. In a
  node without tools say "No tool call is allowed in this turn."
- `on_enter: {"type": "tts", "text": ...}` for a sentence the brief fixes word for word. `{{var}}` works, but an
  unset variable is **spoken aloud**. `on_enter_first_entry_only: true` on nodes that can be re-entered.
- `loop_prompt` replaces `task_message` from the second turn; make it stand alone.
- `max_node_turns` caps user turns in a node; give it a `max_turn_transition`.

## Known pitfalls (all from the kit docs)

| Pitfall | Fix |
|---|---|
| `tools` on a non-conversation node, `pre_actions` on a conversation node | silently dropped: move them |
| `const` for url or a body value | use `default` |
| `response_mapping` inside a pre_action | node level |
| extraction `type: "date"` | real clock: resolve from `now` in code |
| unset `{{var}}` in a tts line | spoken aloud: set a fallback in an earlier code node |
| `{{var}}` in code | crashes when unset: read `vars` |
| `matches_intent` after an action | re-tests the latest message only: extract a follow-up enum |
| booleans | `"true"` and `"True"`: test both, or route on strings |
| a passthrough node without a final `always` | deadlock |
| bare data as a tool result | the model goes silent: add `what_to_do_next` |
| an extraction value that ends the call, set on the trigger message ("hardship, declined") | the agent never says the brief's lines: route the trigger to a stage node that speaks on entry, end on the answer |
| offers worded by the model | clauses get dropped: write each offer sentence in the facts node, say it verbatim |
| a completion flag set by attributing each tool call to a product | several calls in one message break it: count successes; add an `end_call` tool as a safety net |
| attempt limits stated only in text | the model exceeds them: count refusals in a code node |
| a mapped list read as `vars['x']` | unset on the platform: rebuild it with `list_from_vars` from the flattened keys |
