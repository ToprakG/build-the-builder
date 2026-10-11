"""Shared by selftest_run / selftest_repair / final_trace: the Freya 5xx retry and the outage signature."""
import json
import re
import time

TRIES, WAIT = 3, 10  # every Freya call: up to 3 calls in all, 10 s apart, on a 5xx only


def retry_5xx(fn, *args, tries=TRIES, wait=WAIT, **kw):
    """Call fn; on a 5xx answer wait `wait` s and call again, `tries` calls at most. Any other error is raised at once."""
    for i in range(tries):
        try:
            return fn(*args, **kw)
        except Exception as e:  # noqa: BLE001
            if i == tries - 1 or not re.search(r"(?<!\d)5\d\d(?!\d)", str(e)):
                raise
            print(f"freya 5xx ({str(e)[:90]}); retry {i + 1}/{tries - 1} in {wait} s", flush=True)
            time.sleep(wait)


def install(freya_mod):
    """Make every call of the freya module (personas, simulations, iterations, calls, save, export) retry on a 5xx. Idempotent."""
    if getattr(freya_mod.api, "_retrying", False):
        return freya_mod
    raw = freya_mod.api

    def api(*a, **k):
        return retry_5xx(raw, *a, **k)
    api._retrying = True
    freya_mod.api = api
    return freya_mod


def outage_signature(rows, missing=()):
    """No tool call in ANY scenario (the agent did not act: a platform outage, not a build fault). Needs >= 2 scenarios or all MISSING."""
    n = len(rows) + len(missing)
    if n < 2:
        return False
    return (not rows) or all(not r.get("tools") for r in rows)


_INFRA = re.compile(r"function_call_error|Error code: 5\d\d|\bHTTP/?\s*5\d\d\b|Bad gateway|Gateway Time-?out|ECONNREFUSED|Connection refused|error code: 10\d\d", re.I)   # NOT the word cloudflare: every tool call carries the tunnel URL


def infra_error(messages) -> bool:
    """A transcript in which a tool call died on the platform / tunnel (530, 5xx, cloudflare): that scenario is MISSING, never a build fault.
    The partial form of the outage signature (the tunnel flaps: some scenarios call tools, some die on their first call)."""
    try:
        return bool(_INFRA.search(json.dumps(messages, ensure_ascii=False)))
    except (TypeError, ValueError):
        return False


def is_outage(report):
    return bool(report and (report.get("outage") or outage_signature(report.get("rows") or [], report.get("missing") or [])))
