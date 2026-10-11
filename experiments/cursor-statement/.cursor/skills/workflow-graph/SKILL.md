---
name: workflow-graph
description: Shape a Freya workflow so facts, classification, and exits stay apart. Use when writing out/workflow.json for a banking voice agent.
---

# Graph

Three layers, kept apart:

- Facts: tool calls are engine steps. The model does not decide whether to call them. The request body matches the tool API envelope, including a `data` object when the API requires one.
- Classification: conversation nodes extract an enum. Put the specific value first and an intent match second. A vague answer is its own value, not yes.
- Exits: every conversation node can leave for the side intents the documents name (human, payment, dispute, abuse, another language, someone else's account, out of scope, an older document).

Build rules:

- An unconditional transition and a conditional transition must not share a target. Give the unconditional edge its own end node.
- A node must not route back to itself. Call `mcp__fb__side_exit_check` after the graph exists and remove every self-edge it reports.
- Cap each conversation node. On the cap, use the documents' "cannot continue" handoff, not a silent stop.
- Prefer code, then a route, then a fixed line, then a prompt. A number the caller must trust does not live only in a prompt.
- Leave `agent_config` out. No nulls.
