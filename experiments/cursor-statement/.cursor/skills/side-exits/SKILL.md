---
name: side-exits
description: Add the same side exits to every conversation node of a banking voice agent. Use when a caller changes the subject, asks for a human, or is not the customer.
---

# Side exits

On every conversation node, extract these when the documents have them, and route each to its own end:

- human agent, now
- payment
- dispute or a date-explanation the documents refuse to give
- abuse, with the documents' calming lines first and transfer only after the stated count
- a language the documents do not serve
- a third party (spouse, child, parent, deceased cardholder, "I am not the customer")
- out of scope, then a choice: menu, or continue the original task. Continuing must not re-enter the out-of-scope node.
- an older period than the documents allow

The exit sentence is the documents' sentence, written by code or a fixed line, not improvised. Do not invent an IBAN, a branch, an app path, a one-time code, or a new destination address. Sending goes only to the address the service returns, and only after a clear yes.
