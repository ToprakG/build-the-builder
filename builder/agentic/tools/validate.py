"""Freya dry-run validation (`freya.validate_workflow`, docs/api.md). Stores nothing. Never raises.

Skips cleanly without FREYA_API_KEY. `freya.py` reads the key when it is imported, so the check comes first.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def validate(wf: dict, name: str = "dry-run") -> dict:
    if not os.environ.get("FREYA_API_KEY"):
        return {"skipped": "no key"}
    try:
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        import freya  # noqa: PLC0415  (kit file, imported only when a key is set)

        body = {k: v for k, v in wf.items() if k != "agent_config"}
        res = freya.validate_workflow(name, body)
        text = json.dumps(res, default=str)
        return {"ok": True, "response": text[:3000]}
    except Exception as e:  # noqa: BLE001  (the agent reads the message and fixes the workflow)
        return {"ok": False, "error": str(e)[:3000]}
