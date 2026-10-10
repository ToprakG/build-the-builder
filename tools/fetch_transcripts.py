"""Pull every call transcript referenced in results/*.json, pair it with its judge verdict,
and save a calibration set. Also prints a ledger-vs-judge split of all failures.

    python3 tools/fetch_transcripts.py
"""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import freya

OUT = ROOT / "results" / "transcripts"
OUT.mkdir(parents=True, exist_ok=True)

def load(run_file):
    d = json.load(open(run_file))
    rows = [r for rep in d["all_rows"] for r in rep]
    return d, rows

def classify(failed):
    ledger = [f for f in failed if not f.startswith("conversation:")]
    judge = [f for f in failed if f.startswith("conversation:")]
    return ledger, judge

def main():
    all_rows = []
    for name, path in [("A", ROOT / "results" / "brief-a-run.json"), ("B", ROOT / "results" / "brief-b-run.json")]:
        d, rows = load(path)
        for i, r in enumerate(rows):
            r["brief"] = name
            r["rep"] = i // (len(rows) // 3) + 1
        all_rows += rows

    ledger_only = both = judge_only = 0
    print(f"{'brief':5} {'id':8} {'rep':4} {'pass':5} ledger / judge")
    for r in all_rows:
        ledger, judge = classify(r["failed"])
        if not r["failed"]:
            kind = "-"
        elif ledger and judge:
            kind = "both"; both += 1
        elif ledger:
            kind = "ledger-only"; ledger_only += 1
        else:
            kind = "judge-only"; judge_only += 1
        if r["failed"]:
            print(f"{r['brief']:5} {r['id']:8} {r['rep']:<4} {str(r['pass']):5} {kind}")
    n_fail = ledger_only + both + judge_only
    print(f"\n{n_fail} failing call instances out of {len(all_rows)} total.")
    print(f"  ledger-only (judge agreed it passed): {ledger_only}")
    print(f"  ledger + judge both flagged it:       {both}")
    print(f"  judge-only (ledger record was fine):  {judge_only}")

    print(f"\nFetching {len(all_rows)} transcripts...")
    manifest = []
    for r in all_rows:
        call_id = r["call_id"]
        try:
            call = freya.get_call(call_id)
        except Exception as e:
            print(f"  ! {call_id} failed: {e}"); continue
        fname = f"{r['brief']}-{r['id']}-rep{r['rep']}-{call_id[:8]}.json"
        json.dump({"brief": r["brief"], "case_id": r["id"], "rep": r["rep"], "pass": r["pass"],
                    "failed_reasons": r["failed"], "tools": r["tools"], "call_id": call_id,
                    "messages": call.get("messages", []), "ended_reason": call.get("endedReason")},
                   open(OUT / fname, "w"), indent=1)
        manifest.append(fname)
        print(f"  saved {fname}")
    json.dump(manifest, open(OUT / "_manifest.json", "w"), indent=1)
    print(f"\nSaved {len(manifest)} transcripts to {OUT}")

if __name__ == "__main__": main()
