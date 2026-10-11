"""builder/build.py: the official run command wraps scripts/final_run.sh (the tcc variant is the default). No model, no Freya."""
import json
import subprocess
import sys
from pathlib import Path

import builder.build as B

REPO = Path(__file__).resolve().parents[2]


def parse(argv):
    return B.parse(argv)


def test_default_variant_is_tcc_and_the_command_is_final_run(tmp_path, monkeypatch):
    monkeypatch.delenv("FINAL_VOTES", raising=False)
    monkeypatch.delenv("FINAL_MODE", raising=False)
    a = parse(["briefs/brief-a.md", "tools/openapi.json"])
    assert a.variant == "tcc"
    a.max_minutes, a.out, a.slot = 30.0, str(tmp_path / "o"), "tcc-brief-a"
    cmd, env = B.tcc_command(a, [Path("briefs/brief-a.md")], Path("tools/openapi.json"))
    assert cmd[:2] == ["bash", str(REPO / "scripts" / "final_run.sh")] and "--offline" not in cmd
    assert cmd[2:6] == ["--budget-min", "30", "--ship-min", "26"] and cmd[-3:] == ["briefs/brief-a.md", "tools/openapi.json", str(tmp_path / "o")]
    assert env["FINAL_VOTES"] == "3" and env["FINAL_MODE"] == (REPO / "FINAL_MODE.txt").read_text().strip()


def test_save_none_means_offline(tmp_path):
    a = parse(["briefs/brief-b.md", "tools/openapi.json", "--save", "none"])
    a.max_minutes, a.out = 30.0, str(tmp_path)
    cmd, _ = B.tcc_command(a, [Path("briefs/brief-b.md")], Path("tools/openapi.json"))
    assert "--offline" in cmd


def test_run_tcc_reports_the_export_and_the_trace(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()

    def fake_run(cmd, cwd=None, env=None):
        (out / "workflow.json").write_text("{}")
        (out / "tcc-final-x.workflow.json").write_text('{"workflow_id": "w1"}')
        (out / "build_report.json").write_text(json.dumps({"status": "ok", "trace": "traces/tcc-final-x-1"}))
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(B.subprocess, "run", fake_run)
    monkeypatch.setattr(B, "REPO", tmp_path)          # the committed export path is workflows/ under the repo
    (tmp_path / "scripts").mkdir()
    a = parse(["briefs/brief-a.md", "tools/openapi.json", "--out", str(out)])
    a.max_minutes, a.slot = 30.0, "tcc-brief-a"
    rep = B.run_tcc(a, [Path("briefs/brief-a.md")], Path("tools/openapi.json"))
    assert rep["status"] == "ok" and rep["trace"] == "traces/tcc-final-x-1"
    assert Path(rep["saved"]) == tmp_path / "workflows" / "tcc-brief-a.workflow.json" and Path(rep["saved"]).exists()


def test_a_failed_run_is_reported_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(B.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1))
    a = parse(["briefs/brief-a.md", "tools/openapi.json", "--out", str(tmp_path / "o")])
    a.max_minutes, a.slot = 30.0, "tcc-brief-a"
    rep = B.run_tcc(a, [Path("briefs/brief-a.md")], Path("tools/openapi.json"))
    assert rep["status"] == "failed" and "exit 1" in rep["message"]
