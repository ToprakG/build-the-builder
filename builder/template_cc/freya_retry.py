"""Our wrapper around the kit's freya.py (which does not retry), and the Freya-outage signature.

install(freya_module): GET and PUT calls that fail with a 5xx are tried 3 times, 10 s apart. POST is never blindly retried (a repeated
create could start a second simulation or workflow). The one exception is publishing a workflow version, which is check-then-retry:
after a 5xx on `POST /api/v2/versions/workflow/{id}/publish` the versions are read (GET, retried like any GET); if the version we
tried to publish is already published the call counts as done, else the publish is repeated ONCE. Idempotent: installing twice wraps once.

is_outage(rows): a run in which NO customer made any tool call is a Freya outage, not an agent failure (a working agent calls at least
the identity tool in every case). Such a run is reported MISSING for all cases and is never counted as a pass or a fail.
"""

from __future__ import annotations

import re
import time

TRIES, PAUSE_S = 3, 10
_5XX = re.compile(r"->\s*5\d\d\b")
_PUBLISH = re.compile(r"/api/v2/versions/workflow/([^/]+)/publish$")


def install(freya_module, sleep=time.sleep):
    original = freya_module.api
    if getattr(original, "_tcc_retry", False):
        return freya_module

    def get_retrying(path, **params):
        for attempt in range(1, TRIES + 1):
            try:
                return original("GET", path, None, **params)
            except RuntimeError as e:
                if not _5XX.search(str(e)) or attempt == TRIES:
                    raise
                sleep(PAUSE_S)

    def api(method, path, body=None, **params):
        m = _PUBLISH.search(path) if method.upper() == "POST" else None
        if m:  # check-then-retry for a version publish
            try:
                return original(method, path, body, **params)
            except RuntimeError as e:
                if not _5XX.search(str(e)):
                    raise
            wid = m.group(1)
            ws = params.get("workspaceId") or (freya_module.workspace_id() if hasattr(freya_module, "workspace_id") else None)
            versions = get_retrying(f"/api/v2/versions/workflow/{wid}", **({"workspaceId": ws} if ws else {})).get("versions") or []
            vid = (body or {}).get("versionId")
            mine = next((v for v in versions if v.get("id") == vid), None)
            if mine and mine.get("isPublished"):
                return {"success": True, "recovered": "the version was already published after the 5xx"}
            sleep(PAUSE_S)
            return original(method, path, body, **params)   # once; a second failure is raised
        for attempt in range(1, TRIES + 1):
            try:
                return original(method, path, body, **params)
            except RuntimeError as e:
                retryable = method.upper() in ("GET", "PUT") and _5XX.search(str(e))
                if not retryable or attempt == TRIES:
                    raise
                sleep(PAUSE_S)

    api._tcc_retry = True
    freya_module.api = api
    return freya_module


def is_outage(rows: list[dict]) -> bool:
    return bool(rows) and all(not (r.get("tools") or []) for r in rows)
