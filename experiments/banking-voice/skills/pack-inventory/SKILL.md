---
name: pack-inventory
description: Inventory a banking voice-agent pack before writing a workflow. Use when input/ holds briefs, a conflict ledger, and a tool API.
---

# Inventory the pack

Read `input/conflict_ledger.json` first, then call `mcp__fb__ledger_digest`. Then read every `input/brief-*` file.

- Later dated material overrides earlier material. A mail or a meeting note dated after a document overrides that document. A customer-facing page and a drawing are not authoritative.
- Follow the ledger's chosen sentence. When a rule is marked open, hand the call to a human if the documents allow that, and record the choice in `out/rules.json`.
- The tool API is `input/tools.openapi.json`. Operation ids, paths, and the request envelope come only from there. Call `mcp__fb__openapi_summary`.
- Call variables are only the ones the documents say the call already has. Never ask the caller for a value the call already carries.
- Examples in the brief are illustrations. Callers who are not in those examples will be scored later. Do not hardcode a customer number, an amount, a card name, or a date from an example.
