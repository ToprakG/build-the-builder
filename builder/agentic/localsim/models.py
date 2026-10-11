"""Chat-model calls on OpenRouter for the local engine's three roles. Standard library only."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"


class OpenRouterModel:
    def __init__(self, model: str | None = None, temperature: float = 0.2, timeout: float = 90):
        self.model = model or os.environ.get("LOCALSIM_AGENT_MODEL", DEFAULT_MODEL)
        self.temperature, self.timeout = temperature, timeout
        self.calls = 0
        self.tokens = {"prompt": 0, "completion": 0}

    def _post(self, payload: dict) -> dict:
        key = os.environ.get("OPENROUTER_API_KEY") or ""
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is empty")
        body = json.dumps({"model": self.model, "temperature": self.temperature, **payload}).encode()
        last = None
        for attempt in range(4):
            req = urllib.request.Request(URL, data=body, method="POST",
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    out = json.load(r)
                self.calls += 1
                u = out.get("usage") or {}
                self.tokens["prompt"] += u.get("prompt_tokens") or 0
                self.tokens["completion"] += u.get("completion_tokens") or 0
                return out
            except urllib.error.HTTPError as e:
                last = RuntimeError(f"openrouter {e.code}: {e.read().decode()[:300]}")
                if e.code < 500 and e.code != 429:
                    raise last from None
            except (urllib.error.URLError, TimeoutError) as e:
                last = RuntimeError(f"openrouter: {e}")
            time.sleep(2 * (attempt + 1))
        raise last

    def _json(self, system: str, user: str) -> dict:
        out = self._post({"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                          "response_format": {"type": "json_object"}})
        text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
        try:
            start, end = text.index("{"), text.rindex("}") + 1
            return json.loads(text[start:end])
        except ValueError:
            return {}

    def chat(self, system: str, history: list[dict], tools: list[dict]) -> dict:
        payload = {"messages": [{"role": "system", "content": system}, *history]}
        if tools:
            payload["tools"] = tools
        out = self._post(payload)
        return ((out.get("choices") or [{}])[0].get("message")) or {}

    def extract(self, recent: list[dict], schema: list[dict]) -> dict:
        fields = [{k: f.get(k) for k in ("name", "type", "description", "enum_values") if f.get(k) is not None} for f in schema]
        convo = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in recent)
        system = ("You extract variables from a phone conversation. Return one JSON object with one key per field. Use null when "
                  "the conversation does not clearly give a value. For a field with enum_values, answer exactly one of them.")
        return self._json(system, f"FIELDS:\n{json.dumps(fields, ensure_ascii=False)}\n\nCONVERSATION:\n{convo}")

    def intents(self, recent: list[dict], last_user: str, descriptions: list[str]) -> dict:
        convo = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in recent[-6:])
        listing = "\n".join(f"{i + 1}. {d}" for i, d in enumerate(descriptions))
        system = ("You route a phone conversation. Decide which of the numbered descriptions the user's LAST message matches. "
                  'Return JSON {"matches": [numbers]}; an empty list when none matches.')
        out = self._json(system, f"CONVERSATION:\n{convo}\n\nLAST USER MESSAGE: {last_user}\n\nDESCRIPTIONS:\n{listing}")
        nums = {int(n) for n in out.get("matches") or [] if str(n).isdigit()}
        return {d: (i + 1) in nums for i, d in enumerate(descriptions)}
