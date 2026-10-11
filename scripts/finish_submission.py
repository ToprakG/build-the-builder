"""Complete submission.json in the form the hackathon page shows, after builder/build.py <case_folder> <mock_url> and AFTER the commit of the workflow + trace.

    python3 scripts/finish_submission.py --team "TEAM" --member "Ada Lovelace:ada@example.com" [--member "Name:email" ...] [--out submission.json] [--check-only]

Reads <repo>/submission.json written by build.py (workflow, trace, mock_url), then writes: team, members [{name, email}], repo (origin URL without credentials), commit
(HEAD, short), run_command ("python3 builder/build.py {case_folder} {mock_url}"), workflow and trace as repo-relative paths (trace dir -> its trace.txt), mock_url.
Checks (exit 1 on a PROBLEM): workflow and trace files exist and are tracked by git at HEAD, workflow looks like a Freya export, mock_url is https, members/emails present,
no key value from .env in the workflow or trace. No network call.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True).stdout.strip()


def rel(p):
    p = Path(str(p))
    try:
        return str(p.resolve().relative_to(REPO))
    except ValueError:
        return str(p)


def secrets():
    out = set()
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#") and re.search(r"KEY|TOKEN|SECRET|PASSWORD", line.split("=", 1)[0]):
                v = line.split("=", 1)[1].strip().strip("\"'")
                if len(v) >= 8:
                    out.add(v)
    return out


def build(args, base):
    members = []
    for m in args.member:
        name, _, email = m.partition(":")
        members.append({"name": name.strip(), "email": email.strip()})
    trace = rel(base.get("trace") or "")
    if trace and (REPO / trace).is_dir():
        trace = trace.rstrip("/") + "/trace.txt"
    sub = {"team": args.team, "members": members,
           "repo": re.sub(r"//[^/@]*@", "//", git("remote", "get-url", "origin")),
           "commit": git("rev-parse", "--short", "HEAD"),
           "run_command": "python3 builder/build.py {case_folder} {mock_url}",
           "workflow": rel(base.get("workflow") or ""), "trace": trace, "mock_url": base.get("mock_url") or ""}
    return sub


def check(sub):
    bad = []
    if not sub["team"] or sub["team"] == "TEAM_NAME":
        bad.append("team name is a placeholder")
    if not sub["members"] or any(not m["name"] or "@" not in m["email"] for m in sub["members"]):
        bad.append("members: every member needs a name and an email (Solo teams list one member)")
    if not str(sub["mock_url"]).startswith("https://"):
        bad.append(f"mock_url is not an https URL: {sub['mock_url']!r}")
    for k in ("workflow", "trace"):
        p = REPO / sub[k] if sub[k] else None
        if not p or not p.is_file():
            bad.append(f"{k} file missing: {sub[k]!r}")
        elif not git("ls-files", "--error-unmatch", sub[k]):
            bad.append(f"{k} {sub[k]} is not tracked by git: git add it and commit before finishing")
        elif git("status", "--porcelain", "--", sub[k]):
            bad.append(f"{k} {sub[k]} has uncommitted changes")
    wf = REPO / sub["workflow"] if sub["workflow"] else None
    if wf and wf.is_file():
        try:
            d = json.loads(wf.read_text())
            d = d.get("workflow", d) if isinstance(d, dict) else {}
            if "nodes" not in d:
                bad.append("workflow file has no 'nodes': is it a Freya export?")
        except ValueError:
            bad.append("workflow file is not JSON")
        for s in secrets():
            for k in ("workflow", "trace"):
                p = REPO / sub[k] if sub[k] else None
                if p and p.is_file() and s in p.read_text(errors="ignore"):
                    bad.append(f"a key value from .env appears in {sub[k]}: remove it before committing")
                    break
    if not re.fullmatch(r"[0-9a-f]{7,40}", sub["commit"] or ""):
        bad.append("no commit hash")
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default="TEAM_NAME")
    ap.add_argument("--member", action="append", default=[], help='"Name:email"')
    ap.add_argument("--in", dest="inp", default=str(REPO / "submission.json"))
    ap.add_argument("--out", default=str(REPO / "submission.json"))
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args(argv)
    base = json.loads(Path(a.inp).read_text())
    sub = build(a, base)
    bad = check(sub)
    print(json.dumps(sub, indent=1, ensure_ascii=False))
    for b in bad:
        print("PROBLEM", b, file=sys.stderr)
    if not a.check_only and not bad:
        Path(a.out).write_text(json.dumps(sub, indent=1, ensure_ascii=False))
        print(f"written: {a.out}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
