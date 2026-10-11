"""Merge the fix branches into the current branch with the known, mechanical conflict resolutions (tested in a scratch worktree).

    python3 scripts/merge_final.py [branch ...]        default order: fix-identity fix-negotiation rule-coverage final-selftest
Resolutions: policy.schema.json -> ours; RULES.md -> both sides; builder/template_cc/features.py -> union of the flag lists
(+ OPT_IN, short-id map); scripts/final_build.sh -> ours (vote) + the POLICY= hook. Any other conflicting file aborts that merge
(`git merge --abort`) and the script stops. Needs a clean tracked tree.
"""
import re
import subprocess
import sys

BRANCHES = sys.argv[1:] or ["fix-identity", "fix-negotiation", "rule-coverage", "final-selftest"]


def sh(*a, check=True):
    r = subprocess.run(a, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"{' '.join(a)}: {r.stdout}{r.stderr}")
    return r


def hunks(text):
    return list(re.finditer(r"<<<<<<< [^\n]*\n(.*?)=======\n(.*?)>>>>>>> [^\n]*\n", text, re.S))


def features_resolution(path):
    """Union of the flag lists of both versions (ours = stage 2, theirs = stage 3), written into our version's header block."""
    def region(t):
        i = t.index("ALL = [")
        j = t.index("\n\n\ndef _one") if "\n\n\ndef _one" in t else t.index("def _one")
        return i, j

    def flags(t):
        i, j = region(t)
        reg = t[i:j]
        opt_m = re.search(r"OPT_IN\s*=\s*\[([^\]]*)\]", reg)
        opt = re.findall(r'"(f\d+_[a-z0-9_]+)"', opt_m.group(1)) if opt_m else []
        allm = re.search(r"ALL\s*=\s*\[([^\]]*)\]", reg)
        return re.findall(r'"(f\d+_[a-z0-9_]+)"', allm.group(1)), opt

    ours, theirs = (sh("git", "show", f":{n}:{path}").stdout for n in (2, 3))
    (a1, o1), (a2, o2) = flags(ours), flags(theirs)
    opt = o1 + [n for n in o2 if n not in o1]
    names = [n for n in a1 + [n for n in a2 if n not in a1] if n not in opt]   # an opt-in flag is never part of ALL
    lines, cur = [], "ALL = ["
    for n in names:
        piece = f'"{n}", '
        if len(cur) + len(piece) > 118:
            lines.append(cur.rstrip())
            cur = "       "
        cur += piece
    lines.append(cur.rstrip().rstrip(",") + "]")
    block = "\n".join(lines) + "\n"
    if opt:
        block += '# opt-in: never part of "all"; a flag with no verified policy parameter changes nothing for that brief\n'
        block += "OPT_IN = [" + ", ".join(f'"{n}"' for n in opt) + "]\n"
    block += "_SHORT: dict = {}\nfor _n in ALL" + (" + OPT_IN" if opt else "") + ":\n"
    block += '    _SHORT.setdefault(_n.split("_", 1)[0], _n)  # a short id names the first flag that has it (f16 = f16_intents)\n'
    i, j = region(ours)
    return ours[:i] + block.rstrip("\n") + ours[j:]


def resolve(path):
    text = open(path, encoding="utf-8").read()
    if path == "builder/template_cc/policy.schema.json":
        sh("git", "checkout", "--ours", path)
    elif path == "RULES.md":
        open(path, "w", encoding="utf-8").write(re.sub(r"<<<<<<< [^\n]*\n(.*?)=======\n(.*?)>>>>>>> [^\n]*\n", lambda m: m.group(1) + m.group(2), text, flags=re.S))
    elif path == "builder/template_cc/features.py":
        out = features_resolution(path)
        open(path, "w", encoding="utf-8").write(out)
    elif path == "scripts/final_build.sh":
        m = hunks(text)[0]
        head = m.group(1).replace("VOTES=${FINAL_VOTES:-3}; POLICY_ARG=()\n", 'VOTES=${FINAL_VOTES:-3}; POLICY_ARG=()\n#   POLICY=file: skip extraction and the vote, compile this policy.json (self-test repairs; same flags, checked by policy_check)\n[ -n "${POLICY:-}" ] && { VOTES=1; POLICY_ARG=(--policy "$POLICY"); }\n', 1)
        open(path, "w", encoding="utf-8").write(text[:m.start()] + head + text[m.end():])
    else:
        return False
    sh("git", "add", path)
    return True


for b in BRANCHES:
    r = sh("git", "merge", "--no-edit", "--no-ff", b, check=False)
    conflicts = [x for x in sh("git", "diff", "--name-only", "--diff-filter=U").stdout.split() if x]
    if r.returncode and not conflicts:
        sys.exit(f"merge {b} failed: {r.stdout}{r.stderr}")
    for f in conflicts:
        if not resolve(f):
            sh("git", "merge", "--abort")
            sys.exit(f"merge {b}: unexpected conflict in {f}; merge aborted, resolve by hand")
    if conflicts:
        sh("git", "commit", "--no-edit")
    print(f"merged {b}" + (f" (resolved {', '.join(conflicts)})" if conflicts else ""))
