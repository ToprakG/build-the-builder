"""One decisions call.

OpenAI is first: POST https://api.openai.com/v1/decisions with model gpt-6-luna.
When that key is missing, rejected, or out of credits, the same questions go to
OpenRouter: POST https://openrouter.ai/api/alpha/decisions
model openai/gpt-6-luna-decisions. A yes/no question there is type noul.
The returned body always uses OpenAI's answers list: [{name, probability}].
"""
import json, os, urllib.error, urllib.request
from envfile import load_env

OPENAI_URL = "https://api.openai.com/v1/decisions"
OPENAI_MODEL = "gpt-6-luna"
OPENROUTER_URL = "https://openrouter.ai/api/alpha/decisions"
OPENROUTER_MODEL = "openai/gpt-6-luna-decisions"
MODEL = OPENAI_MODEL

class DecisionsError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

def api_key():
    load_env()
    return os.environ.get("OPENAI_API_KEY") or ""

def openrouter_key():
    load_env()
    return os.environ.get("OPENROUTER_API_KEY") or ""

def has_decision_key():
    return bool(api_key() or openrouter_key())

def _post(url, key, payload, timeout):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode()[:1500]
        raise DecisionsError(f"POST {url} -> {error.code}: {detail}", status=error.code) from None

def _fallback(error):
    return error.status in (401, 402, 429) or (error.status or 0) >= 500

def _from_openrouter(body):
    """Map {answers: {name: {type, noul}}} onto the OpenAI answers list."""
    raw = body.get("answers") or {}
    answers = []
    if isinstance(raw, dict):
        for name, answer in raw.items():
            answer = answer or {}
            probability = answer.get("noul")
            if probability is None:
                probability = answer.get("probability")
            kind = "refusal" if answer.get("type") not in (None, "noul") and probability is None else answer.get("type")
            if answer.get("type") == "noul":
                kind = "predicate"
            answers.append({"name": name, "type": kind, "probability": probability})
    elif isinstance(raw, list):
        answers = raw
    return {"answers": answers, "model": body.get("model") or OPENROUTER_MODEL, "provider": "openrouter"}

def _from_openai(body):
    body = dict(body)
    body["provider"] = "openai"
    body.setdefault("model", OPENAI_MODEL)
    return body

def decide(text, questions, model=OPENAI_MODEL, timeout=90):
    """questions: [{name, type, instructions}]. type predicate is sent as noul on OpenRouter."""
    load_env()
    openai = api_key()
    router = openrouter_key()
    if not openai and not router:
        raise DecisionsError("OPENAI_API_KEY and OPENROUTER_API_KEY are empty. Put one in .env.")
    openai_error = None
    if openai:
        try:
            return _from_openai(_post(OPENAI_URL, openai, {"model": model, "input": text, "questions": questions}, timeout))
        except DecisionsError as error:
            if not _fallback(error) or not router:
                raise
            openai_error = error
    try:
        named = {}
        for item in questions:
            named[item["name"]] = {"type": "noul", "instructions": item["instructions"]}
        return _from_openrouter(_post(OPENROUTER_URL, router, {"model": OPENROUTER_MODEL, "state": text, "questions": named}, timeout))
    except DecisionsError as error:
        if openai_error:
            raise DecisionsError(f"OpenAI failed ({openai_error}). OpenRouter failed ({error}).", status=error.status) from None
        raise
