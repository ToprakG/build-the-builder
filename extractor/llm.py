"""One JSON call. Temperature 0. OpenRouter when that key is set, otherwise OpenAI."""
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from envfile import load_env

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
# Strongest model on the OpenRouter account. Override with EXTRACTOR_MODEL.
DEFAULT_MODEL = "anthropic/claude-opus-5.5"
OPENAI_MODEL = "gpt-5.6"


class LLMError(RuntimeError):
    pass


def model_name():
    load_env()
    if os.environ.get("EXTRACTOR_MODEL"):
        return os.environ["EXTRACTOR_MODEL"]
    if os.environ.get("OPENROUTER_API_KEY"):
        return DEFAULT_MODEL
    return OPENAI_MODEL


def _endpoint():
    load_env()
    if os.environ.get("OPENROUTER_API_KEY"):
        return OPENROUTER_URL, os.environ["OPENROUTER_API_KEY"]
    if os.environ.get("OPENAI_API_KEY"):
        return OPENAI_URL, os.environ["OPENAI_API_KEY"]
    raise LLMError("OPENROUTER_API_KEY and OPENAI_API_KEY are empty. Put one in .env.")


def _parse_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def complete(messages, purpose, temperature=0, timeout=180):
    """Return (parsed_json, trace_row). One retry if the body is not JSON."""
    url, key = _endpoint()
    model = model_name()
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_tokens": 8000,
    }
    started = time.time()
    last = None
    for attempt in range(4):
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode()[:800]
            retryable = error.code in (429, 502, 503, 529) or (error.code == 402 and "in_flight" in detail)
            if retryable and attempt < 3:
                wait = 90
                match = re.search(r'"Retry-After":\s*"(\d+)"', detail)
                if match:
                    wait = min(150, int(match.group(1)))
                time.sleep(wait)
                continue
            if "temperature" in detail.lower() and "temperature" in payload:
                payload.pop("temperature", None)
                continue
            raise LLMError(f"{purpose}: HTTP {error.code}: {detail}") from None
        try:
            content = body["choices"][0]["message"]["content"]
            parsed = _parse_json(content)
            row = {"model": model, "purpose": purpose, "seconds": round(time.time() - started, 1)}
            return parsed, row
        except (KeyError, json.JSONDecodeError, IndexError) as error:
            last = error
            messages = messages + [
                {"role": "assistant", "content": body.get("choices", [{}])[0].get("message", {}).get("content", "")},
                {"role": "user", "content": "That was not a JSON object. Return one JSON object and nothing else."},
            ]
            payload["messages"] = messages
    raise LLMError(f"{purpose}: model did not return JSON ({last})")
