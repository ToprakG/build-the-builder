"""Tool base URL. The builder reads it when it writes the graph.

FREYA_BANK_HOST is the origin, for example http://127.0.0.1:8787.
The tool URL is that origin plus /v1. The same graph points at the real bank or the mock.
"""
import os
from envfile import load_env

REAL_ORIGIN = "https://freya-bank-mock.freyavoice.workers.dev"

def origin():
    load_env()
    return os.environ.get("FREYA_BANK_HOST", REAL_ORIGIN).rstrip("/")

def tool_base():
    host = origin()
    return host if host.endswith("/v1") else host + "/v1"

def retarget(workflow, base=None):
    """Point every baked tool URL in a workflow at this base. Returns the same object."""
    base = (base or tool_base()).rstrip("/")
    nodes = workflow.get("nodes") or {}
    for node in nodes.values():
        for tool in node.get("tools") or []:
            props = ((tool.get("function") or {}).get("parameters") or {}).get("properties") or {}
            url = props.get("url")
            if not isinstance(url, dict) or "/v1/" not in str(url.get("default") or ""):
                continue
            name = str(url["default"]).rstrip("/").split("/")[-1]
            url["default"] = f"{base}/{name}"
    return workflow
