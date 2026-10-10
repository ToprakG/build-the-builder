"""Score the 39 saved calls with two OpenRouter decision models.

Each question is the case check text, unchanged. A call passes when every
probability is at least 0.5. Freya's label is the conversation verdict only.

    python3 tools/compare_judges.py
"""
import json, os, pathlib, sys, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from envfile import load_env
from serialize_call import render

LUNA = "openai/gpt-6-luna-decisions"
JEV = "typesafe/jev-1.13"
URLS = {
    LUNA: "https://openrouter.ai/api/alpha/decisions",
    JEV: "https://openrouter.ai/api/v1/systemone",
}
THRESHOLD = 0.5

def freya_conversation_pass(failed):
    return not any(str(item).startswith("conversation:") for item in failed or [])

def ask(model, state, checks):
    load_env()
    key = os.environ["OPENROUTER_API_KEY"]
    questions = {f"c{i}": {"type": "noul", "instructions": text} for i, text in enumerate(checks, 1)}
    payload = {"model": model, "state": state, "questions": questions}
    request = urllib.request.Request(URLS[model], data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode()[:400]
        raise RuntimeError(f"{error.code} {detail}") from None
    raw = body.get("answers") or {}
    probs = []
    for i, text in enumerate(checks, 1):
        answer = raw.get(f"c{i}") or {}
        probability = answer.get("noul")
        if probability is None:
            probability = answer.get("probability")
        probs.append({"check": text, "p": probability})
    known = [item["p"] for item in probs if item["p"] is not None]
    passed = None if len(known) != len(checks) else all(item >= THRESHOLD for item in known)
    return {"pass": passed, "min_p": min(known) if known else None, "checks": probs, "model": body.get("model")}

def kappa(pairs):
    """pairs: (freya_pass, model_pass) booleans."""
    n = len(pairs)
    if n == 0:
        return None
    agree = sum(a == b for a, b in pairs) / n
    freya_yes = sum(a for a, _ in pairs) / n
    model_yes = sum(b for _, b in pairs) / n
    chance = freya_yes * model_yes + (1 - freya_yes) * (1 - model_yes)
    if chance == 1:
        return 1.0
    return (agree - chance) / (1 - chance)

def summarize(name, rows):
    usable = [row for row in rows if row["pass"] is not None]
    pairs = [(row["freya"], row["pass"]) for row in usable]
    agree = sum(a == b for a, b in pairs)
    false_pass = sum((not a) and b for a, b in pairs)
    false_fail = sum(a and (not b) for a, b in pairs)
    print(f"\n{name}")
    print(f"  scored {len(usable)} / {len(rows)}")
    if not usable:
        return
    print(f"  agreement {agree} / {len(usable)} = {agree / len(usable):.1%}")
    print(f"  false pass {false_pass}   false fail {false_fail}")
    print(f"  Cohen kappa {kappa(pairs):.3f}")

def main():
    cases = {case["id"]: case for case in json.loads((ROOT / "public-test-set.json").read_text())}
    jobs = []
    for path in sorted((ROOT / "results" / "transcripts").glob("*.json")):
        if path.name.startswith("_"):
            continue
        saved = json.loads(path.read_text())
        case = cases[saved["case_id"]]
        state, _ = render(saved["messages"], case, facts=False)
        jobs.append({
            "file": path.name,
            "id": case["id"],
            "freya": freya_conversation_pass(saved.get("failed_reasons")),
            "state": state,
            "checks": case["checks"],
        })
    print(f"{len(jobs)} calls. Luna and Jev. Questions are the case check lines.")

    def one(job, model):
        try:
            verdict = ask(model, job["state"], job["checks"])
        except Exception as error:
            return {"file": job["file"], "id": job["id"], "freya": job["freya"], "model": model, "error": str(error), "pass": None, "min_p": None, "checks": []}
        verdict.update({"file": job["file"], "id": job["id"], "freya": job["freya"], "model": model})
        return verdict

    rows = {LUNA: [], JEV: []}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = []
        for job in jobs:
            for model in (LUNA, JEV):
                futures.append(pool.submit(one, job, model))
        done = 0
        for future in as_completed(futures):
            row = future.result()
            rows[row["model"]].append(row)
            done += 1
            if row.get("error"):
                print(f"  fail {row['model']} {row['id']} {row['error'][:180]}")
            elif done % 10 == 0:
                print(f"  {done} / {len(futures)}", flush=True)
    for model in (LUNA, JEV):
        summarize(model, rows[model])
        print("  disagreements:")
        for row in sorted(rows[model], key=lambda item: item["file"]):
            if row["pass"] is None or row["pass"] == row["freya"]:
                continue
            kind = "false pass" if row["pass"] and not row["freya"] else "false fail"
            print(f"    {kind} {row['id']} {row['file'][:28]} freya={row['freya']} min_p={row['min_p']}")
    out = ROOT / "results" / "judge-compare.json"
    slim = {model: [{k: row[k] for k in ("file", "id", "freya", "pass", "min_p", "checks", "error") if k in row} for row in rows[model]] for model in rows}
    out.write_text(json.dumps(slim, indent=1))
    print(f"\nWrote {out}")

if __name__ == "__main__":
    main()
