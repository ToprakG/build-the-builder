"""Run the vendor chat worker with our choice of customer model (default DeepSeek v4.1 Flash on OpenRouter).

Called by the self-test runner as its worker, with the vendor worker's own arguments:
    worker_patch.py <vendor_worker.py> --vendor <repo> --job job.json --out out.json
The vendor's chat_driver hardcodes its customer model; this sets it before the worker imports it, without editing
the vendor code. Gemini keys are dropped so the OpenRouter path is used.
"""
import os
import runpy
import sys
from pathlib import Path

CUSTOMER_MODEL = os.environ.get("LOCAL_CUSTOMER_MODEL", "deepseek/deepseek-v4.1-flash")


def main():
    script, args = sys.argv[1], sys.argv[2:]
    vendor = Path(args[args.index("--vendor") + 1]).resolve()
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        os.environ.pop(k, None)
    for p in (vendor / "tools" / "driver", vendor / "tools", vendor):
        sys.path.insert(0, str(p))
    import chat_driver  # noqa: PLC0415  (vendor module, patched before the worker imports it)

    chat_driver.OPENROUTER_CUSTOMER = CUSTOMER_MODEL
    sys.argv = [script] + args
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
