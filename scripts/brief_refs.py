"""Brief references: a brief that builds on another one ("Brief B is Brief A with ...") is built WITH the brief it refers to.

    python3 scripts/brief_refs.py --pool FILE... --repo briefs -- FILE...        -> prints the resolved file list (one path per line),
                                                                                     writes the log as JSON to --log when given
detect(text, own)   ids the text refers to ("A", "7"): "Brief X" in a reference context ("with", "same as", "changes to", "based on",
                    "applies", "is not supplied" ...), never the brief's own id. Pure text, no model call.
resolve(files, pool, repo)  for every file: referenced briefs first (found in the POOL = the briefs passed in this call, then in the
                    repo's briefs/*.md by title letter or file name brief-<id>), then the file itself. A reference that cannot be found
                    is a WARN and the file builds alone. No brief names are hard-coded: ids come from the titles.
"""
import argparse
import json
import re
import sys
from pathlib import Path

TITLE = re.compile(r"^\s*#\s*Brief\s+([A-Za-z0-9]{1,3})\b", re.M)
REF = re.compile(r"\bBrief\s+([A-Z0-9]{1,2})\b")
CONTEXT = re.compile(r"(?:\bwith\b|same as|changes? to|based on|builds? on|variant of|version of|extends?|applies|apply|replaces?|"
                     r"unless|is not supplied|not supplied|except|from)", re.I)
UNSUPPLIED = re.compile(r"\bBrief\s+[A-Z0-9]{1,2}\b[^.\n]{0,60}\b(?:is not supplied|not supplied|not provided)", re.I)


def title_id(text):
    m = TITLE.search(text or "")
    return m.group(1).upper() if m else ""


def detect(text, own=""):
    """Ids of other briefs this text builds on, in order of first mention."""
    own = (own or title_id(text)).upper()
    out = []
    for m in REF.finditer(text or ""):
        i = m.group(1).upper()
        if i == own or i in out:
            continue
        s = text[max(0, m.start() - 60):m.end() + 60]
        if CONTEXT.search(s):
            out.append(i)
    return out


def _find(i, pool, repo):
    """(path, where) of the brief with id i: pool first, then repo/briefs by title id or file stem."""
    for where, files in (("call", pool), ("repo", sorted(Path(repo).glob("*.md")) if repo else [])):
        for f in files:
            f = Path(f)
            try:
                t = f.read_text(errors="ignore")
            except OSError:
                continue
            if title_id(t) == i or f.stem.lower() in (f"brief-{i.lower()}", f"brief_{i.lower()}", i.lower()):
                return f, where
    return None, ""


def resolve(files, pool=(), repo="briefs"):
    """-> (ordered file list, log entries). Referenced briefs come before the file that refers to them; each file appears once."""
    log, order, seen = [], [], set()

    def add(f, depth=0):
        f = Path(f)
        key = f.resolve()
        if key in seen:
            return
        text = f.read_text(errors="ignore")
        seen.add(key)   # marked before the references: a cycle (A refers to B, B to A) stops here
        for i in detect(text, title_id(text)):
            g, where = _find(i, pool, repo)
            if g is None or g.resolve() in seen:
                if g is None:
                    log.append({"file": str(f), "ref": i, "status": "WARN", "msg": f"refers to Brief {i}, not found in this call or in briefs/: building alone"})
                continue
            log.append({"file": str(f), "ref": i, "status": "resolved", "found_in": where, "path": str(g)})
            if depth < 4:
                add(g, depth + 1)
        order.append(str(f))

    for f in files:
        add(f)
    return order, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", nargs="*", default=[])
    ap.add_argument("--repo", default="briefs")
    ap.add_argument("--log", default="")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    order, log = resolve(a.files, a.pool, a.repo)
    for e in log:
        print(("WARN " if e["status"] == "WARN" else "ref  ") + json.dumps(e), file=sys.stderr)
    if a.log:
        Path(a.log).write_text(json.dumps({"input": a.files, "resolved": order, "log": log}, indent=1))
    print("\n".join(order))


if __name__ == "__main__":
    main()
