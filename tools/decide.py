"""One decisions call.

The judge uses TypeSafe Jev on OpenRouter:
POST https://openrouter.ai/api/v1/systemone
model typesafe/jev-1.13. A yes/no question there is type noul.

When OPENROUTER_API_KEY is empty and OPENAI_API_KEY is set, the call goes to
POST https://api.openai.com/v1/decisions with model gpt-6-luna.
The returned body always uses OpenAI's answers list: [{name, probability}].
"""
import json, os, urllib.error, urllib.request
from envfile import load_env

OPENAI_URL = "https://api.openai.com/v1/decisions"
OPENAI_MODEL = "gpt-6-luna"
OPENROUTER_URL = "https://openrouter.ai/api/v1/systemone"
OPENROUTER_MODEL = "typesafe/jev-1.13"
MODEL = OPENROUTER_MODEL

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

def decide(text, questions, model=None, timeout=90):
    """questions: [{name, type, instructions}]. type predicate is sent as noul on OpenRouter."""
    load_env()
    openai = api_key()
    router = openrouter_key()
    if not openai and not router:
        raise DecisionsError("OPENAI_API_KEY and OPENROUTER_API_KEY are empty. Put one in .env.")
    if router:
        named = {}
        for item in questions:
            named[item["name"]] = {"type": "noul", "instructions": item["instructions"]}
        chosen = model or OPENROUTER_MODEL
        return _from_openrouter(_post(OPENROUTER_URL, router, {"model": chosen, "state": text, "questions": named}, timeout))
    return _from_openai(_post(OPENAI_URL, openai, {"model": model or OPENAI_MODEL, "input": text, "questions": questions}, timeout))
