import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import finish_submission as F  # noqa: E402


def test_build_and_check_with_a_temp_repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"; (r / "workflows").mkdir(parents=True); (r / "traces" / "t1").mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
    run("init", "-q"); run("config", "user.email", "a@b.c"); run("config", "user.name", "x")
    run("remote", "add", "origin", "https://user:tok@github.com/o/n.git")
    (r / "workflows" / "w.workflow.json").write_text(json.dumps({"nodes": {"a": {}}}))
    (r / "traces" / "t1" / "trace.txt").write_text("start\nend\n")
    (r / ".env").write_text("FREYA_API_KEY=abcdefgh12345\n")
    monkeypatch.setattr(F, "REPO", r)
    base = {"workflow": str(r / "workflows" / "w.workflow.json"), "trace": str(r / "traces" / "t1"), "mock_url": "https://m.example.workers.dev/t"}
    args = type("A", (), {"team": "Takim", "member": ["Ada Lovelace:ada@example.com"]})()
    sub = F.build(args, base)
    assert sub["workflow"] == "workflows/w.workflow.json" and sub["trace"] == "traces/t1/trace.txt" and sub["repo"] == "https://github.com/o/n.git"
    assert sub["members"] == [{"name": "Ada Lovelace", "email": "ada@example.com"}] and sub["run_command"].endswith("{case_folder} {mock_url}")
    assert any("not tracked" in b for b in F.check({**sub, "commit": "abcdef1"}))        # nothing committed yet
    run("add", "workflows", "traces"); run("commit", "-qm", "x")
    sub = F.build(args, base)
    assert F.check(sub) == []
    (r / "traces" / "t1" / "trace.txt").write_text("token abcdefgh12345 leaked")          # a key value in the trace: refused (and uncommitted)
    assert any("key value" in b for b in F.check(sub))
    assert any("placeholder" in b for b in F.check({**sub, "team": "TEAM_NAME"}))
    assert any("https" in b for b in F.check({**sub, "mock_url": "http://x"}))
    assert any("email" in b for b in F.check({**sub, "members": [{"name": "A", "email": ""}]}))
