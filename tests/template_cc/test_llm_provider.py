"""llm.py provider choice: the documented path (anthropic:claude-sonnet-5-5 via ANTHROPIC_API_KEY) and TCC_LLM_PROVIDER. Mocked transport."""

import json

import pytest

from builder.template_cc import llm

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


@pytest.fixture
def transport(monkeypatch):
    calls = []

    def fake(url, headers, payload, timeout):
        calls.append((url, headers, payload))
        if "anthropic.com" in url:
            return {"content": [{"type": "text", "text": json.dumps({"ok": True})}], "usage": {"input_tokens": 3, "output_tokens": 2}}
        return {"choices": [{"message": {"content": json.dumps({"ok": True})}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
    monkeypatch.setattr(llm, "TRANSPORT", fake)
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    monkeypatch.delenv("TCC_LLM_PROVIDER", raising=False)
    return calls


def test_documented_path_anthropic_sonnet_with_api_key(transport, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    out = llm.call_llm("sys", "user", "anthropic:claude-sonnet-5-5", json_schema=SCHEMA)
    assert out == {"ok": True}
    url, headers, payload = transport[0]
    assert url == llm.ANTHROPIC_URL and headers["x-api-key"] == "test-key-not-real"
    assert payload["model"] == "claude-sonnet-5-5" and payload["messages"][0]["content"] == "user"


def test_anthropic_without_a_key_is_a_clear_error(transport, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY"):
        llm.call_llm("sys", "user", "anthropic:claude-sonnet-5-5", attempts=1)


def test_env_switch_forces_the_provider_and_spells_the_name(transport, monkeypatch):
    monkeypatch.setenv("TCC_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    llm.call_llm("sys", "user", "anthropic:claude-sonnet-5-5", json_schema=SCHEMA)
    url, headers, payload = transport[0]
    assert url == llm.OPENROUTER_URL and payload["model"] == "anthropic/claude-sonnet-5.5"
    assert llm.split_model("openrouter:anthropic/claude-sonnet-5.5") == ("openrouter", "anthropic/claude-sonnet-5.5")
    monkeypatch.setenv("TCC_LLM_PROVIDER", "anthropic")
    assert llm.split_model("openrouter:anthropic/claude-sonnet-5.5") == ("anthropic", "claude-sonnet-5-5")
    monkeypatch.setenv("TCC_LLM_PROVIDER", "claude_cli")
    assert llm.split_model("anthropic:claude-sonnet-5-5") == ("claude_cli", "claude-sonnet-5-5")
    monkeypatch.setenv("TCC_LLM_PROVIDER", "nonsense")
    with pytest.raises(ValueError, match="TCC_LLM_PROVIDER"):
        llm.split_model("anthropic:claude-sonnet-5-5")


def test_default_alias_becomes_an_api_model_id_when_the_provider_is_forced(monkeypatch):
    from builder.template_cc.io import Models

    assert Models().extract == "claude_cli:sonnet"            # the default needs a Claude Code login
    monkeypatch.setenv("TCC_LLM_PROVIDER", "anthropic")        # the documented path for a re-run without one
    assert llm.split_model(Models().extract) == ("anthropic", "claude-sonnet-5-5")
    monkeypatch.setenv("TCC_LLM_PROVIDER", "openrouter")
    assert llm.split_model(Models().extract) == ("openrouter", "anthropic/claude-sonnet-5.5")
