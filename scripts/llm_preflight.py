"""One tiny LLM call with the provider the build will use; exit 0 = usable, exit 1 = say why, within ~30 s.

    .venv310/bin/python scripts/llm_preflight.py [model]      model default: $PREFLIGHT_MODEL, else claude_cli:sonnet (the builder's model)
TCC_LLM_PROVIDER (claude_cli | anthropic | openrouter) overrides the provider exactly as it does for the build (llm.py).
"""
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
LIMIT_S = 25


def preflight(model=None, limit=LIMIT_S):
    """(ok, message). Never raises."""
    from builder.template_cc import llm
    model = model or os.environ.get("PREFLIGHT_MODEL") or "claude_cli:sonnet"
    try:
        provider, name = llm.split_model(model)
    except ValueError as e:
        return False, f"LLM preflight FAILED: {e}"
    t0 = time.time()
    try:
        text = llm.call_llm("Reply with the single word OK.", "ping", model, max_tokens=16, purpose="preflight",
                            deadline=t0 + limit, attempts=1)
    except Exception as e:  # noqa: BLE001  (LLMError, OutOfTime, subprocess errors: all mean "do not start the build")
        hint = {"claude_cli": "is `claude` logged in and under its usage limit? (the final setup has no other provider: wait for the limit to reset, do not loop)",
                "anthropic": "is ANTHROPIC_API_KEY set and valid?",
                "openrouter": "is OPENROUTER_API_KEY set and valid?"}.get(provider, "check the provider settings")
        return False, f"LLM preflight FAILED: provider {provider}, model {name}: {str(e)[:300]}. {hint}"
    if not str(text).strip():
        return False, f"LLM preflight FAILED: provider {provider}, model {name} answered with an empty text"
    return True, f"LLM preflight ok: provider {provider}, model {name}, {time.time() - t0:.1f}s"


if __name__ == "__main__":
    ok, msg = preflight(sys.argv[1] if len(sys.argv) > 1 else None)
    print(msg, flush=True)
    sys.exit(0 if ok else 1)
