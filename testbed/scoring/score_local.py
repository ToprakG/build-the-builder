"""Score one local conversation against a case, for ANY brief in the testbed.

The kit's tools/score.py reads the bank's ledger in the kit's field names. Fake briefs rename fields and enums
(account_ref, amount_cents, callback_at, close_call.code, ...). This scorer maps each brief's tool calls back to
canonical records using that brief's config.json, then applies the same ledger checks as the kit.

    from score_local import score
    score(case, calls, cfg)   # calls: [{"tool": name, "args": {...}, "result": {...}}, ...] in call order
    -> {"pass": bool, "failed": [reasons], "records": {...}}

Transcript checks (policy, honesty) still need a judge; this covers the outcome part only.
"""


def records_from_calls(calls, cfg):
    t = cfg["tools"]
    cp, an, sc, cc = t["create_promise"], t["add_note"], t["schedule_callback"], t["close_call"]
    rec = {"outcome": None, "reason": None, "promises": [], "notes": [], "callbacks": [], "transfers": []}
    ok = lambda c: isinstance(c.get("result"), dict) and c["result"].get("ok") is True
    for c in calls:
        a, name = c.get("args", {}), c["tool"]
        if name == "create_promise" and ok(c):
            amt = a.get(cp["amount"])
            amt = (float(amt) / 100.0) if cp["amount_unit"] == "cents" and amt is not None else amt
            rec["promises"].append({"product_id": a.get(cp["product_id"]), "amount": amt, "date": a.get(cp["date"])})
        elif name == "add_note" and ok(c):
            rec["notes"].append({"product_id": a.get(an["product_id"]), "reason": a.get(an["reason"])})
        elif name == "schedule_callback" and ok(c):
            if sc["mode"] == "datetime":
                v = str(a.get(sc["datetime"], ""))
                rec["callbacks"].append({"date": v[:10], "time": v[11:16]})
            else:
                rec["callbacks"].append({"date": a.get(sc["date"]), "time": a.get(sc["time"])})
        elif name == "transfer_to_agent" and ok(c):
            rec["transfers"].append({"queue": a.get(t["transfer_to_agent"]["queue"])})
        elif name == "close_call":
            if cc["mode"] == "code":
                rec["outcome"], rec["reason"] = a.get(cc["code"]), None
            else:
                rec["outcome"], rec["reason"] = a.get(cc["outcome"]), a.get(cc["reason"]) or None
    return rec


def score(case, calls, cfg):
    exp, bad = case["expected"], []
    rec = records_from_calls(calls, cfg)
    if (rec["outcome"], rec["reason"]) != (exp["outcome"], exp.get("reason")):
        bad.append(f"outcome {(rec['outcome'], rec['reason'])}, expected {(exp['outcome'], exp.get('reason'))}")
    key = lambda p: (p["product_id"], round(float(p["amount"]), 2) if p["amount"] is not None else None, p["date"])
    if sorted(map(key, rec["promises"])) != sorted(map(key, exp["promises"])):
        bad.append(f"promises {sorted(map(key, rec['promises']))}, expected {sorted(map(key, exp['promises']))}")
    nk = lambda n: (n.get("product_id"), n["reason"])
    if sorted(map(nk, rec["notes"])) != sorted(map(nk, exp["notes"])):
        bad.append(f"notes {sorted(map(nk, rec['notes']))}, expected {sorted(map(nk, exp['notes']))}")
    ck = lambda c: (c["date"], c["time"])
    if sorted(map(ck, rec["callbacks"])) != sorted(map(ck, exp["callbacks"])):
        bad.append(f"callbacks {rec['callbacks']}, expected {exp['callbacks']}")
    if sorted(x["queue"] for x in rec["transfers"]) != sorted(x["queue"] for x in exp["transfers"]):
        bad.append(f"transfers {rec['transfers']}, expected {exp['transfers']}")
    if not calls or calls[-1]["tool"] != "close_call":
        bad.append("the call was not closed with close_call as the last tool call")
    if sum(c["tool"] == "close_call" for c in calls) > 1:
        bad.append("close_call called more than once")
    for c in calls:
        if c["tool"] in ("get_account", "create_promise") and isinstance(c.get("result"), dict) and c["result"].get("error") == "identity not verified":
            bad.append(f"{c['tool']} called before identity was verified")
            break
    if case["oracle"]["outcome_key"].startswith("nr_") and any(c["tool"] in ("verify_identity", "get_account") for c in calls):
        bad.append("identity or account tools used although the customer was not on the line")
    return {"pass": not bad, "failed": bad, "records": rec}


if __name__ == "__main__":
    # Self-test: the expected records, fed back as perfect tool calls, must score as a pass for every case.
    import json, pathlib, sys
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "..")
    sets = [(root / "personas" / "brief-a.cases.json", root / "configs" / "brief-a.config.json"),
            (root / "personas" / "brief-b.cases.json", root / "configs" / "brief-b.config.json")]
    sets += [(d / "cases.json", d / "config.json") for d in sorted((root / "fake_briefs").iterdir())]
    total = fails = 0
    for cpath, gpath in sets:
        cfg = json.load(open(gpath))
        t = cfg["tools"]
        for case in json.load(open(cpath)):
            e, calls = case["expected"], [{"tool": "verify_identity", "args": {}, "result": {"ok": True}}] if not case["oracle"]["outcome_key"].startswith("nr_") else []
            for p in e["promises"]:
                amt = int(round(p["amount"] * 100)) if t["create_promise"]["amount_unit"] == "cents" else p["amount"]
                calls.append({"tool": "create_promise", "args": {t["create_promise"]["product_id"]: p["product_id"], t["create_promise"]["amount"]: amt, t["create_promise"]["date"]: p["date"]}, "result": {"ok": True}})
            for n in e["notes"]:
                calls.append({"tool": "add_note", "args": {t["add_note"]["product_id"]: n["product_id"], t["add_note"]["reason"]: n["reason"]}, "result": {"ok": True}})
            for cb in e["callbacks"]:
                sc = t["schedule_callback"]
                args = {sc["datetime"]: f"{cb['date']}T{cb['time']}"} if sc["mode"] == "datetime" else {sc["date"]: cb["date"], sc["time"]: cb["time"]}
                calls.append({"tool": "schedule_callback", "args": args, "result": {"ok": True}})
            for tr in e["transfers"]:
                calls.append({"tool": "transfer_to_agent", "args": {"queue": tr["queue"]}, "result": {"ok": True}})
            cc = t["close_call"]
            calls.append({"tool": "close_call", "args": {cc["code"]: e["outcome"]} if cc["mode"] == "code" else {cc["outcome"]: e["outcome"], cc["reason"]: e["reason"] or ""}, "result": {"ok": True}})
            r = score(case, calls, cfg)
            total += 1
            if not r["pass"]:
                fails += 1
                print(case["id"], r["failed"])
    print(f"self-test: {total - fails}/{total} perfect replays pass")
