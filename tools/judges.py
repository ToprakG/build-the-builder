"""Two conversation judges.

Judge C codes the mechanical checks and sends the other checks to POST /v1/decisions.
Judge B sends the same criteria text Freya uses to gemini-3-flash-preview.
The ledger score stays in score.py. These judges do not change that score.
"""
import json, os, re, urllib.error, urllib.request
from envfile import load_env
from serialize_call import render
from checks import criteria_text, lookup
from decide import MODEL, DecisionsError, decide, has_decision_key

ROOT_THRESHOLDS = __import__("pathlib").Path(__file__).resolve().parent.parent / "results" / "thresholds.json"
GEMINI_MODEL = "gemini-3-flash-preview"

def load_thresholds(path=None):
    path = path or ROOT_THRESHOLDS
    if not path.exists():
        return {"global": 0.5, "family": {}}
    data = json.loads(path.read_text())
    data.setdefault("global", 0.5)
    data.setdefault("family", {})
    return data

def threshold_for(family, table):
    return float(table.get("family", {}).get(family, table.get("global", 0.5)))

def _row(name, text, family, source, probability, table):
    limit = threshold_for(family, table)
    passed = None if probability is None else probability >= limit
    return {"name": name, "text": text, "family": family, "source": source, "p": probability, "threshold": limit, "pass": passed}

def judge_c(messages, case, thresholds=None, model=True):
    """One transcript. Coded checks are 0 or 1. Model checks are probabilities."""
    table = thresholds or load_thresholds()
    text, turns = render(messages, case, facts=True)
    rows, questions = [], []
    for index, check in enumerate(case["checks"], 1):
        family, source, payload = lookup(check)
        name = f"c{index}"
        if source == "code":
            rows.append(_row(name, check, family, "code", 1.0 if payload(turns, case) else 0.0, table))
        else:
            questions.append({"name": name, "type": "predicate", "instructions": payload, "text": check, "family": family})
    status, used_model, used_provider = "ok", MODEL, None
    if questions:
        if not model or not has_decision_key():
            status = "coded_only" if not model else "need_api_key"
            for item in questions:
                rows.append(_row(item["name"], item["text"], item["family"], "model", None, table))
        else:
            try:
                body = decide(text, [{"name": item["name"], "type": "predicate", "instructions": item["instructions"]} for item in questions])
            except DecisionsError as error:
                status = "decisions_error"
                for item in questions:
                    rows.append(_row(item["name"], item["text"], item["family"], "model", None, table))
                rows.sort(key=lambda row: int(row["name"][1:]))
                known = [row["p"] for row in rows if row["p"] is not None]
                return {"judge": "C", "model": MODEL, "status": status, "pass": None, "min_p": min(known) if known else None, "checks": rows, "input": text, "error": str(error)}
            used_model = body.get("model") or MODEL
            used_provider = body.get("provider")
            by_name = {answer.get("name"): answer for answer in body.get("answers") or []}
            for item in questions:
                answer = by_name.get(item["name"]) or {}
                probability = None if answer.get("type") == "refusal" else answer.get("probability")
                rows.append(_row(item["name"], item["text"], item["family"], "model" if probability is not None else "refusal", probability, table))
    rows.sort(key=lambda row: int(row["name"][1:]))
    known = [row["p"] for row in rows if row["p"] is not None]
    passed = None if any(row["pass"] is None for row in rows) else all(row["pass"] for row in rows)
    return {
        "judge": "C",
        "model": used_model,
        "provider": used_provider,
        "status": status,
        "pass": passed,
        "min_p": min(known) if known else None,
        "checks": rows,
        "input": text,
    }

def _gemini_key():
    load_env()
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

def judge_b(messages, case, timeout=90):
    """Same criteria lines as Freya. No FACTS block. Needs GEMINI_API_KEY or GOOGLE_API_KEY."""
    key = _gemini_key()
    transcript, _ = render(messages, case, facts=False)
    criteria = criteria_text(case)
    if not key:
        return {"judge": "B", "model": GEMINI_MODEL, "status": "need_gemini_key", "pass": None, "failed": [], "criteria": criteria}
    prompt = (
        transcript
        + "\n\n"
        + criteria
        + "\n\nReply with JSON only, in this shape: {\"pass\": true, \"failed\": []}. "
        + "pass is true only when every line under 'All of these are true' is true. "
        + "Each false line is copied into failed."
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={key}"
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0}}
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode()[:1500]
        return {"judge": "B", "model": GEMINI_MODEL, "status": f"http_{error.code}", "pass": None, "failed": [], "error": detail, "criteria": criteria}
    parts = (((body.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])
    raw = "\n".join(part.get("text") or "" for part in parts).strip()
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {"judge": "B", "model": GEMINI_MODEL, "status": "bad_json", "pass": None, "failed": [], "raw": raw[:800], "criteria": criteria}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"judge": "B", "model": GEMINI_MODEL, "status": "bad_json", "pass": None, "failed": [], "raw": raw[:800], "criteria": criteria}
    return {
        "judge": "B",
        "model": GEMINI_MODEL,
        "status": "ok",
        "pass": bool(parsed.get("pass")),
        "failed": list(parsed.get("failed") or []),
        "criteria": criteria,
    }

def judge_both(messages, case, thresholds=None):
    return {"C": judge_c(messages, case, thresholds), "B": judge_b(messages, case)}
