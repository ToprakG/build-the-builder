"""call_llm for T-CC: a stdlib port of agentforge.llm (same contract, same provider:model convention).

`.venv310` has no anthropic/openai/httpx/dotenv packages, so this talks HTTPS with urllib. Every call is recorded in
the trace (model, purpose, tokens, seconds) and is bounded by the build's wall clock (`deadline`, a time.time()).
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from builder.template_cc.io import REPO

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class OutOfTime(RuntimeError):
    pass


def load_env(path: Path | None = None) -> None:
    """KEY=VALUE / export KEY=VALUE lines from the repo .env. A value already in the process wins."""
    path = Path(path) if path else REPO / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and not os.environ.get(k):
            os.environ[k] = v


PROVIDERS = ("anthropic", "openrouter", "claude_cli", "openai")


ALIASES = {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "haiku": "claude-haiku-5-5"}  # Claude Code aliases -> API ids


def _openrouter_slug(name: str) -> str:
    """claude-sonnet-5-5 -> anthropic/claude-sonnet-5.5 (OpenRouter's spelling); a name with a "/" is already a slug."""
    if "/" in name:
        return name
    m = re.fullmatch(r"(claude-(?:opus|sonnet|haiku))-(\d+)-(\d+)", name)
    return f"anthropic/{m[1]}-{m[2]}.{m[3]}" if m else f"anthropic/{name}"


def split_model(model: str) -> tuple[str, str]:
    """(provider, name) for "openai:gpt-x", "anthropic:claude-x", or a bare name (claude* -> anthropic).

    TCC_LLM_PROVIDER=anthropic|openrouter|claude_cli (the README's one switch) overrides the provider of every model string; the
    model name is kept (claude-sonnet-5-5), and is spelled the way the chosen provider wants it."""
    if ":" in model:
        provider, name = model.split(":", 1)
        provider, name = provider.strip().lower(), name.strip()
    else:
        provider, name = ("anthropic", model) if model.lower().startswith("claude") else ("openai", model)
    forced = os.environ.get("TCC_LLM_PROVIDER", "").strip().lower()
    if forced:
        if forced not in PROVIDERS:
            raise ValueError(f"TCC_LLM_PROVIDER={forced!r}: use one of {', '.join(PROVIDERS)}")
        if forced in ("anthropic", "openrouter"):
            name = ALIASES.get(name, name)  # "sonnet" (the default alias for claude -p) is not an API model id
        if forced == "openrouter" and provider != "openrouter":
            name = _openrouter_slug(name)
        elif forced in ("anthropic", "claude_cli") and "/" in name:  # an OpenRouter slug -> the provider's own name
            name = re.sub(r"^anthropic/", "", name).replace(".", "-")
        provider = forced
    return provider, name


def _post(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise LLMError(f"POST {url} -> {e.code}: {e.read().decode(errors='replace')[:800]}", status=e.code) from None
    except urllib.error.URLError as e:
        raise LLMError(f"POST {url}: {e.reason}") from None


# Tests replace this to avoid any network call.
TRANSPORT = _post


def _anthropic(system, user, model, max_tokens, effort, timeout) -> tuple[str, dict]:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise LLMError("ANTHROPIC_API_KEY is empty", status=401)
    body = TRANSPORT(ANTHROPIC_URL, {"x-api-key": key, "anthropic-version": "2023-06-01"}, {
        "model": model, "system": system, "messages": [{"role": "user", "content": user}],
        "max_tokens": max(max_tokens, 16000), "output_config": {"effort": effort}}, timeout)
    text = "".join(b.get("text", "") for b in body.get("content") or [] if b.get("type") == "text")
    u = body.get("usage") or {}
    return text, {"in": u.get("input_tokens"), "out": u.get("output_tokens")}


def _reasoning_model(model: str) -> bool:
    """OpenAI reasoning models (gpt-5+, o-series) reject temperature and want max_completion_tokens."""
    name = model.split("/")[-1].lower()
    return name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4", "o5"))


def _openai(system, user, model, temperature, max_tokens, json_mode, timeout, router=False) -> tuple[str, dict]:
    var = "OPENROUTER_API_KEY" if router else "OPENAI_API_KEY"
    key = os.environ.get(var, "")
    if not key:
        raise LLMError(f"{var} is empty", status=401)
    payload: dict[str, Any] = {"model": model,
                               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if _reasoning_model(model) and not router:
        payload["max_completion_tokens"] = max(max_tokens, 16000)
    else:
        payload["max_tokens"] = max(max_tokens, 16000) if router else max_tokens
        if not _reasoning_model(model):
            payload["temperature"] = temperature
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    body = TRANSPORT(OPENROUTER_URL if router else OPENAI_URL, {"Authorization": "Bearer " + key}, payload, timeout)
    text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    u = body.get("usage") or {}
    return text, {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens")}


_CLI_WARNED = False
_RATE = re.compile(r"rate.?limit|429|overloaded|too many requests|try again", re.I)
_USAGE = re.compile(r"usage limit|hit your limit|limit reached|resets? (?:at|in)|out of (?:extra )?(?:credits|usage)", re.I)


def _claude_cli(system: str, user: str, model: str, max_tokens: int, timeout: float, trace=None) -> tuple[str, dict]:
    """One call through the local Claude Code CLI (the user's Claude subscription): `claude -p`, no tools, user text on stdin.

    The empty working directory keeps this repo's CLAUDE.md and skills out of the call; the cleaned environment lets it nest
    inside a Claude Code session. temperature and max_tokens are not options of the CLI and are ignored."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")
           and not (k == "ANTHROPIC_API_KEY" and not v)}
    # no MCP servers, skills or user/project settings: they cost ~270k tokens per call otherwise (measured), ~600 with these flags
    cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--system-prompt", system, "--tools", "",
           "--no-session-persistence", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands",
           "--setting-sources", ""]
    try:
        r = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=timeout, cwd=tempfile.mkdtemp(), env=env)
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"claude -p timed out after {timeout:.0f}s", status=504) from e
    except FileNotFoundError as e:
        raise LLMError("the claude CLI is not installed", status=401) from e
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = {}
    text = out.get("result") if isinstance(out.get("result"), str) else ""
    err = r.returncode != 0 or bool(out.get("is_error")) or not out
    if err:
        msg = (text or r.stderr or r.stdout or "no output")[:400]
        if _USAGE.search(msg) and not re.search(r"\b429\b|rate_limit", msg):
            raise LLMError("claude -p usage limit: " + msg, status=401)
        if _RATE.search(msg):
            raise LLMError("claude -p rate limit: " + msg, status=429)
        raise LLMError(f"claude -p failed (exit {r.returncode}): {msg}", status=500)
    u = out.get("usage") or {}
    usage = {"in": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
             + (u.get("cache_creation_input_tokens") or 0), "out": u.get("output_tokens"), "cost": out.get("total_cost_usd")}
    return text, usage


def _retryable(error: Exception) -> bool:
    status = getattr(error, "status", None)
    return status is None or status == 429 or status >= 500


def call_llm(system: str, user: str, model: str, json_schema: dict | None = None, temperature: float = 0.2,
             max_tokens: int = 4096, effort: str = "medium", *, trace=None, purpose: str = "llm call",
             deadline: float | None = None, attempts: int = 4):
    """Return text, or a parsed dict when json_schema is given. Retries 429/5xx and unparsable JSON."""
    load_env()
    provider, name = split_model(model)
    if json_schema is not None:
        system = (f"{system}\n\nReply with ONE JSON object only, no prose. The JSON Schema below describes that "
                  f"object; return an instance with real values, NOT the schema itself:\n{json.dumps(json_schema)}")
    last: Exception | None = None
    for attempt in range(attempts):
        left = None if deadline is None else deadline - time.time()
        if left is not None and left < 5:
            raise OutOfTime(f"no time left for {purpose}")
        timeout = 600 if left is None else max(5.0, min(600.0, left))
        t0 = time.time()
        try:
            if provider == "anthropic":
                text, usage = _anthropic(system, user, name, max_tokens, effort, timeout)
            elif provider == "claude_cli":
                global _CLI_WARNED
                if not _CLI_WARNED and trace:
                    _CLI_WARNED = True
                    trace.warn("claude_cli ignores temperature and max_tokens")
                text, usage = _claude_cli(system, user, name, max_tokens, timeout, trace)
            elif provider in ("openai", "openrouter"):
                text, usage = _openai(system, user, name, temperature, max_tokens, json_schema is not None, timeout,
                                      router=provider == "openrouter")
            else:
                raise ValueError(f"unknown LLM provider: {provider}")
            if trace:
                trace.model_call(model, purpose + (f" (retry {attempt})" if attempt else ""), usage.get("in"),
                                 usage.get("out"), usage.get("cost"), time.time() - t0)
            if json_schema is None:
                return text
            return unwrap_schema_echo(parse_json(text), json_schema)
        except ValueError as e:  # unparsable JSON: ask again
            last = e
            if trace:
                trace.warn(f"{purpose}: {e}")
        except LLMError as e:
            last = e
            if trace:
                trace.model_call(model, purpose, seconds=time.time() - t0, error=str(e))
            if not _retryable(e):
                raise
        if attempt < attempts - 1:
            time.sleep(min(2 ** attempt, 20) + random.random())
    raise last if last else RuntimeError("call_llm failed")


def unwrap_schema_echo(payload: dict, schema: dict) -> dict:
    """Some models answer with the schema shape, values under properties.<key>.value. Recover the instance."""
    required = schema.get("required") or []
    if all(k in payload for k in required) or not isinstance(payload.get("properties"), dict):
        return payload
    return {k: v.get("value", v.get("default")) if isinstance(v, dict) and ("value" in v or "default" in v) else v
            for k, v in payload["properties"].items()}


def parse_json(text: str) -> dict:
    """A JSON object from model output, tolerating fences and stray prose."""
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    cands = [s]
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        cands.append(s[a: b + 1])
    for c in cands:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError(f"model output was not a JSON object: {text[:200]!r}")
