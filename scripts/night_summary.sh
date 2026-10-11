#!/bin/bash
# Morning summary of the overnight eval queue (no LLM). Writes results/ablation/NIGHT_SUMMARY.md and prints it.
# Env: LADDER="f149br:f149b:1 p10r:p10:2 p10xr:p10x:2 p11r:p11:2 p6r:p6:2 p4r:p4:2 p2r:p2:2 p3r:p3:2"  (tag prefix : set directory under out/template_cc/ or "-" : reps)  REPS=default reps (2)
# The first entry is the baseline. A: baseline -> p4r (the verdict). B: p4r -> p4xr (cost of a fresh extraction) and C: p4xr -> p5r (effect of
# the intents) are information. p2r and p3r are judged (each against the last ACCEPTED step) only when p4r is REJECT.
cd "$(dirname "$0")/.." || exit 2
exec python3 - "$@" <<'PY'
import glob, json, os, re, subprocess, sys
from pathlib import Path

REPO = Path.cwd()
EV = REPO / "out/template_cc/eval/freya"
REPS = int(os.environ.get("REPS", "2"))  # 0 = tags without a rep number (s10, f14)
LADDER = [(x.split(":") + [str(REPS)])[:3] for x in os.environ.get("LADDER", "f149br:f149b:1 p10r:p10:2 p10xr:p10x:2 p11r:p11:2 p6r:p6:2 p4r:p4:2 p2r:p2:2 p3r:p3:2").split()]
NREPS = {pre: int(n) for pre, _, n in LADDER}
def rl(pre): return [""] if REPS == 0 else [str(r) for r in range(1, NREPS[pre] + 1)]
LADDER = [(pre, setdir) for pre, setdir, _ in LADDER]
BRIEFS = ["brief-a", "brief-b", "f1-nordhavn", "f2-ostral", "f3-kestrel", "f4-brightwell", "f5-halden-email", "f6-corvid-memo", "f7-lumen-codes"]
out = []
def say(s=""): out.append(s); print(s)
def run(*a):
    r = subprocess.run(["python3", *a], capture_output=True, text=True, cwd=REPO)
    return (r.stdout + r.stderr).strip()

say("# Night summary"); say()
say("## 1. Files present (briefs done / 9)"); say()
say("| tag | " + " | ".join(f"rep{r}" for r in range(1, max(NREPS.values()) + 1)) + " | complete |"); say("|---|" + "---|" * (max(NREPS.values()) + 1))
complete = {}
for pre, _ in LADDER:
    n = [sum((EV / f"{b}.{pre}{r}.json").exists() for b in BRIEFS) for r in rl(pre)]
    complete[pre] = all(x == 9 for x in n)
    cells = [f"{x}/9" for x in n] + ["-"] * (max(NREPS.values()) - len(n))
    say(f"| {pre} | " + " | ".join(cells) + f" | {'yes' if complete[pre] else 'NO - incomplete'} |")
q = REPO / "out/template_cc/eval/night_queue.log"
if q.exists():
    say(); say("Queue log (last 3 lines):"); say("```"); [say(l) for l in q.read_text().splitlines()[-3:]]; say("```")

say(); say("## 2. Stability per tag (solid / flaky / dead over the reps)"); say()
for pre, _ in LADDER:
    if complete[pre]:
        say(f"### {pre}"); say("```"); say(run("scripts/stability.py", *[f"{pre}{r}" for r in rl(pre)])); say("```")
    else:
        say(f"### {pre}: incomplete, skipped")

def grew(base, new):
    txt = run("scripts/failure_patterns.py", base, new)
    keep, cur = [], None
    for line in txt.splitlines():
        m = re.match(r"\[(\d+)\] .*\(was (\d+)\)", line)
        if m:
            cur = [line] if int(m.group(1)) > int(m.group(2)) else None
            if cur: keep.append(cur)
        elif cur is not None and line.strip():
            cur.append(line)
        elif not line.strip():
            cur = None
    return "\n".join("\n".join(c[:3]) for c in keep) or "(no cluster grew)"

def verdict_of(base_pre, new_pre):
    txt = run("scripts/stability.py", "--base", ",".join(f"{base_pre}{r}" for r in rl(base_pre)), "--new", ",".join(f"{new_pre}{r}" for r in rl(new_pre)))
    return ("ACCEPT" if re.search(r"^ACCEPT", txt, re.M) else "REJECT"), txt

def step(title, base_pre, new_pre, note=""):
    """One comparison; prints the stability output and the clusters that grew (rep 1). Returns the verdict or None if incomplete."""
    if not complete.get(base_pre) or not complete.get(new_pre):
        missing = [p for p in (base_pre, new_pre) if not complete.get(p)]
        say(f"### {title}: INCOMPLETE ({', '.join(missing)} not finished), no verdict"); return None
    verdict, txt = verdict_of(base_pre, new_pre)
    say(f"### {title}: **{verdict}**" + (f"  ({note})" if note else "")); say("```"); say(txt); say("```")
    say("Clusters that grew (rep 1):"); say("```"); say(grew(f"{base_pre}{rl(base_pre)[0]}", f"{new_pre}{rl(new_pre)[0]}")); say("```")
    return verdict

say(); say("## 3. Comparisons"); say()
names = [pre for pre, _ in LADDER]
setdir = dict(LADDER)
base, p4, p4x, p5 = (names + [None] * 4)[0], None, None, None
get = lambda n: n if n in names else None
base, p4, p4x, p5, p2, p3 = (get(n) for n in ("f149br", "p6r", "p6xr", "p7r", "p2r", "p3r"))  # p6 = all flags + the fixes of the first p4 run
old_p4 = get("p4r")
base = base or names[0]
decisions = {}
if p4:
    decisions["A"] = step("A. baseline -> p10 (all flags, cached policies)", base, p4)
if p4 and p4x:
    decisions["B"] = step("B. p10 -> p10x (cost of a fresh extraction: same flags, x1 policies)", p4, p4x, "information")
if p4x and p5:
    decisions["C"] = step("C. p10x -> p11 (effect of the intents, f16_intents)", p4x, p5, "information")
if old_p4:
    decisions["A0"] = step("A0. baseline -> p6 (the same flags before the last fixes; information)", base, old_p4, "information")
kept = p4 if decisions.get("A") == "ACCEPT" else None
if decisions.get("A") == "REJECT":
    say(); say("p10 is REJECT: judging the earlier flag sets against the baseline."); say()
    kept = base
    for pre in (p2, p3):
        if pre:
            v = step(f"D. {kept} -> {pre}", kept, pre)
            decisions[pre] = v
            if v == "ACCEPT":
                kept = pre
elif decisions.get("A") == "ACCEPT":
    say(); say("p10 is ACCEPT: p2 and p3 are not needed.")

say(); say("## 4. Kept flags"); say()
if decisions.get("A") is None:
    say("No verdict on p10 yet (incomplete)."); kept_set = None
else:
    kept_set = kept
    if kept == p4 and decisions.get("C") == "ACCEPT" and p5:
        kept_set = p5
        say(f"p11 (p10 + intents) beats p10x too, so the intents stay on.")
    elif kept == p4 and decisions.get("B") == "REJECT":
        say("Note: p10x (fresh extraction) is worse than p10 (cached policies): the extraction costs score; look at section B.")
if kept_set:
    sd = setdir.get(kept_set, "-")
    rep = REPO / "out/template_cc" / sd / "brief-a/build_report.json"
    feats = json.loads(rep.read_text()).get("features") if sd != "-" and rep.exists() else None
    say(f"Last accepted step: **{kept_set}** (set {sd})")
    say(f"Flags: `{','.join(feats)}`" if feats else "Flags: (no build report for this set)")
say(); say("Decisions: " + (", ".join(f"{k}={v}" for k, v in decisions.items()) or "none"))
(REPO / "results/ablation").mkdir(parents=True, exist_ok=True)
(REPO / "results/ablation/NIGHT_SUMMARY.md").write_text("\n".join(out) + "\n")
PY
