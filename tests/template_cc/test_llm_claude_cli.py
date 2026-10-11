"""claude_cli provider: `claude -p` through subprocess.run, mocked (no real call)."""
import json
import os
import subprocess

import pytest

from builder.template_cc import llm

SCHEMA = {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]}


def completed(result, code=0, is_error=False, usage=None):
    out = {"type": "result", "result": result, "is_error": is_error, "total_cost_usd": 0.01,
           "usage": usage or {"input_tokens": 11, "output_tokens": 5}}
    return subprocess.CompletedProcess(["claude"], code, json.dumps(out), "")


@pytest.fixture
def run(monkeypatch):
    calls = []
    queue = []

    def fake(cmd, **kw):
        calls.append((cmd, kw))
        r = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(llm.subprocess, "run", fake)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    return calls, queue


def test_json_is_parsed_and_the_user_text_goes_on_stdin(run, monkeypatch):
    calls, queue = run
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    queue.append(completed('{"ok": "yes"}'))
    assert llm.call_llm("system text", "USER TEXT", "claude_cli:sonnet", json_schema=SCHEMA) == {"ok": "yes"}
    cmd, kw = calls[0]
    assert kw["input"] == "USER TEXT" and "USER TEXT" not in cmd
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--tools") + 1] == "" and "--output-format" in cmd
    assert "CLAUDECODE" not in kw["env"] and "CLAUDE_CODE_ENTRYPOINT" not in kw["env"]
    assert kw["cwd"] != os.getcwd()
    assert "--strict-mcp-config" in cmd and "--disable-slash-commands" in cmd and cmd[cmd.index("--setting-sources") + 1] == ""
    assert "ONE JSON object" in cmd[cmd.index("--system-prompt") + 1]


def test_fenced_json_is_parsed(run):
    _, queue = run
    queue.append(completed('```json\n{"ok": "fenced"}\n```'))
    assert llm.call_llm("s", "u", "claude_cli:sonnet", json_schema=SCHEMA) == {"ok": "fenced"}


def test_plain_text_and_usage_in_the_trace(run):
    _, queue = run
    queue.append(completed("hello"))

    class T:
        rec = []
        def model_call(self, *a, **k): self.rec.append(a)
        def warn(self, t): pass
    assert llm.call_llm("s", "u", "claude_cli:sonnet", trace=T()) == "hello"
    assert T.rec[0][2:5] == (11, 5, 0.01)


def test_a_rate_limit_is_retried(run):
    calls, queue = run
    queue[:] = [completed("API Error: 429 rate_limit_error", code=1, is_error=True), completed('{"ok": "after retry"}')]
    assert llm.call_llm("s", "u", "claude_cli:sonnet", json_schema=SCHEMA) == {"ok": "after retry"}
    assert len(calls) == 2


def test_a_usage_limit_raises_at_once_with_the_text(run):
    calls, queue = run
    queue.append(completed("Claude usage limit reached. Your limit resets at 3am", code=1, is_error=True))
    with pytest.raises(llm.LLMError) as e:
        llm.call_llm("s", "u", "claude_cli:sonnet")
    assert e.value.status == 401 and "resets at 3am" in str(e.value) and len(calls) == 1


def test_a_timeout_is_a_retryable_error(run):
    calls, queue = run
    queue[:] = [subprocess.TimeoutExpired("claude", 5), completed("fine")]
    assert llm.call_llm("s", "u", "claude_cli:sonnet") == "fine"


def test_the_defaults_use_the_cli():
    from builder.template_cc.io import Models
    m = Models()
    assert m.extract == m.analyze == m.judge == "claude_cli:sonnet" and m.patch == "sonnet"
