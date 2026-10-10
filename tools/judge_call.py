"""Score one saved call with Judge C, Judge B, or both.

    python3 tools/judge_call.py results/transcripts/A-PUB-01-rep1-0e37aeb3.json
    python3 tools/judge_call.py results/transcripts/A-PUB-05-rep1-188337f5.json --judge both
    python3 tools/judge_call.py --audit

Judge C uses OPENROUTER_API_KEY from .env and calls Jev for the semantic checks.
Coded checks run with no key. Judge B uses GEMINI_API_KEY or GOOGLE_API_KEY.
--audit compares coded checks with Freya on every saved transcript. It does not call an API.
"""
import argparse, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from judges import judge_b, judge_c

CASES = {case["id"]: case for case in json.loads((ROOT / "public-test-set.json").read_text())}

def show(verdict, show_input=False):
    label = {True: "PASS", False: "FAIL"}.get(verdict.get("pass"), "WAIT")
    print(f"Judge {verdict['judge']} {label} status={verdict['status']} model={verdict.get('model')}")
    if verdict["judge"] == "C":
        print(f"min_p={verdict['min_p']}")
        for row in verdict["checks"]:
            mark = {True: "PASS", False: "FAIL"}.get(row["pass"], "WAIT")
            print(f"  {mark} {row['p']} {row['source']:<6} {row['family']:<12} {row['text']}")
        if verdict.get("error"):
            print(verdict["error"])
    elif verdict.get("failed"):
        for line in verdict["failed"]:
            print("  -", line)
        if verdict.get("error"):
            print(verdict["error"])
    if show_input and verdict.get("input"):
        print(verdict["input"])

def audit():
    """Freya's judge passed means every coded check must pass. A miss here is a code bug."""
    misses = checked = 0
    folder = ROOT / "results" / "transcripts"
    for path in sorted(folder.glob("*.json")):
        if path.name.startswith("_"):
            continue
        saved = json.loads(path.read_text())
        case = CASES[saved["case_id"]]
        verdict = judge_c(saved["messages"], case, model=False)
        freya_pass = not any(str(item).startswith("conversation:") for item in saved.get("failed_reasons") or [])
        coded = [row for row in verdict["checks"] if row["source"] == "code"]
        checked += 1
        bad = [row["text"] for row in coded if row["pass"] is False]
        if freya_pass and bad:
            misses += 1
            print(f"MISS {path.name} Freya judge passed, code failed: {bad}")
        elif bad:
            print(f"fail {path.name} code: {bad}")
    print(f"\n{checked} calls. {misses} calls where Freya passed and a coded check failed.")
    return misses

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript", nargs="?")
    parser.add_argument("--judge", choices=("c", "b", "both"), default="c")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--show-input", action="store_true")
    args = parser.parse_args()
    if args.audit:
        sys.exit(audit())
    if not args.transcript:
        parser.error("give a transcript file, or pass --audit")
    saved = json.loads(pathlib.Path(args.transcript).read_text())
    case = CASES[saved["case_id"]]
    messages = saved["messages"]
    if args.judge in ("c", "both"):
        show(judge_c(messages, case), args.show_input)
    if args.judge in ("b", "both"):
        show(judge_b(messages, case), args.show_input)

if __name__ == "__main__":
    main()
