"""Fault injection for the patcher: clean dev policy + ONE known fault -> compiled faulty build + fault.json (expected repair action).

    .venv310/bin/python scripts/inject_faults.py <set> <brief> [--n 5]       e.g.  p10 f5-halden-email ; p10 brief-a
Writes out/template_cc/faults/<brief>/<fault_id>/{policy.json, workflow.json, zones.final.json, build_report.json, fault.json,
policy.clean.json}. fault.json: {fault_id, kind, pointers, clean_value, faulty_value, expected_action: R1|R2|R3|R4, rules: [scenario
rule types that exercise it], extra_rules (R4: the removed brief rule, for selftest_cases --extra-rules)}.
Faults keep the policy valid (enum values from the policy itself), and leave `_source` untouched like a wrong extraction would.
"""
import argparse, copy, json, subprocess, sys, os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN = Path(subprocess.run(["git", "-C", str(REPO), "rev-parse", "--git-common-dir"], capture_output=True, text=True).stdout.strip()).resolve().parent
sys.path.insert(0, str(REPO))


def faults(p):
    out = []

    def add(fid, kind, ptrs, newp, action, rules, **kw):
        out.append({"fault_id": fid, "kind": kind, "pointers": ptrs, "policy": newp, "expected_action": action, "rules": rules, **kw})

    if (p.get("ask_limits") or {}).get("right_party") == 1:  # a REAL extraction fault of f5: "ask once more" was read as one ask in total
        add("right_party_real", "REAL: right_party ask limit extracted as 1 (the brief allows a second ask)", ["/ask_limits/right_party"], copy.deepcopy(p),
            "R1", ["right_party"], clean_override={"/ask_limits/right_party": 2}, real=True)
    q = copy.deepcopy(p)
    if p.get("bands"):
        q["bands"][0]["deadline_business_days"] = int(q["bands"][0]["deadline_business_days"]) + 3
        add("deadline_shift", "deadline parameter shifted", ["/bands/0/deadline_business_days"], q, "R1", ["vague_date", "pieces_identity", "floor_amount"])
    qm = p.get("queue_map") or {}
    if qm.get("restructuring"):
        other = [v for k, v in qm.items() if v and v != qm["restructuring"]]
        if other:
            q = copy.deepcopy(p); q["queue_map"]["restructuring"] = other[0]
            add("wrong_queue", "wrong transfer queue", ["/queue_map/restructuring"], q, "R1", ["restructuring"])
    om = p.get("outcome_map") or {}
    if om.get("np_dispute") and om.get("np_refused") and om["np_dispute"] != om["np_refused"]:
        q = copy.deepcopy(p); q["outcome_map"]["np_dispute"] = list(om["np_refused"])
        add("wrong_outcome", "wrong outcome code", ["/outcome_map/np_dispute"], q, "R1", ["dispute"])
    if "multi_product" in p:
        q = copy.deepcopy(p); q["multi_product"] = not p["multi_product"]; q["max_products"] = 2 if q["multi_product"] else 1
        add("multi_flip", "multi_product flipped", ["/multi_product", "/max_products"], q, "R1", ["multi_product", "pieces_identity"])
    al = p.get("ask_limits") or {}
    if "id_decline" in al:
        q = copy.deepcopy(p); q["ask_limits"]["id_decline"] = 0 if al["id_decline"] else 3
        add("ask_limit", "ask limit changed", ["/ask_limits/id_decline"], q, "R1", ["identity_declined"])
    if (p.get("identity") or {}).get("max_wrong"):
        q = copy.deepcopy(p); q["identity"]["max_wrong"] = int(p["identity"]["max_wrong"]) + 2
        add("max_wrong", "identity max_wrong changed", ["/identity/max_wrong"], q, "R1", ["identity_failed"])
    cb = p.get("callback") or {}
    if cb.get("start"):
        q = copy.deepcopy(p); h = int(cb["start"][:2]) + 2; q["callback"]["start"] = f"{h:02d}:{cb['start'][3:]}"
        add("callback_window", "callback window shifted", ["/callback/start"], q, "R1", ["third_party"])
    for key in ("condolence", "right_party_question", "goodbye", "ask_again"):
        ln = (p.get("lines") or {}).get(key)
        if ln and ln.get("verbatim"):
            q = copy.deepcopy(p); q["lines"][key]["text"] = ln["text"].replace(".", ",").rstrip(",") + " and have a nice day." if key != "right_party_question" else ln["text"].replace("Am I speaking with", "Is this")
            add("corrupt_line", f"verbatim line {key} corrupted", [f"/lines/{key}/text"], q, "R3", {"condolence": ["deceased"], "right_party_question": ["pieces_identity", "robot_turn1"]}.get(key, ["pieces_identity"]), line_key=key)
            break
    # held-out kinds (different fields from the tuned set)
    if cb.get("end"):
        q = copy.deepcopy(p); h = int(cb["end"][:2]) - 2; q["callback"]["end"] = f"{h:02d}:{cb['end'][3:]}"
        add("callback_end", "callback window end moved", ["/callback/end"], q, "R1", ["third_party"])
    nm = p.get("note_map") or {}
    if nm.get("dispute") and nm.get("refused") and nm["dispute"] != nm["refused"]:
        q = copy.deepcopy(p); q["note_map"]["dispute"] = nm["refused"]
        add("note_value", "note reason for dispute swapped", ["/note_map/dispute"], q, "R1", ["dispute"])
    for tk, t in (p.get("product_types") or {}).items():
        if t.get("floor_field") and t["floor_field"] != p["account_fields"]["past_due"]:
            q = copy.deepcopy(p); q["product_types"][tk]["floor_field"] = p["account_fields"]["past_due"]
            add("floor_field", "floor amount read from the full overdue field", [f"/product_types/{tk}/floor_field"], q, "R1", ["floor_amount"])
            break
    if len(p.get("holidays") or []) > 2:
        q = copy.deepcopy(p); q["holidays"] = [h for i, h in enumerate(p["holidays"]) if i != 1]
        add("holiday_drop", "one holiday missing", ["/holidays"], q, "R1", ["vague_date", "pieces_identity"])
    for key in ("first_ask", "confirm_question", "promise_confirmation", "right_party_question"):
        ln = (p.get("lines") or {}).get(key)
        if ln and ln.get("verbatim"):
            q = copy.deepcopy(p); q["lines"][key]["text"] = ln["text"].rstrip() + " Please hold while I check."
            add("corrupt_line2", f"verbatim line {key} corrupted", [f"/lines/{key}/text"], q, "R3", ["pieces_identity", "vague_date"], line_key=key)
            break
    ex2 = [e for e in p.get("extra_rules") or [] if e.get("brief_quote") and e.get("stage") not in (None, "global")]
    if len(ex2) > 1:
        q = copy.deepcopy(p); q["extra_rules"] = [e for e in q["extra_rules"] if e != ex2[1]]
        add("unknown_rule_removed2", "a second brief rule removed from the policy", ["/extra_rules"], q, "R4", ["unknown_rule"],
            extra_rules=[{"statement": ex2[1]["statement"], "brief_quote": ex2[1]["brief_quote"]}])
    ex = [e for e in p.get("extra_rules") or [] if e.get("brief_quote") and e.get("stage") not in (None, "global")]
    if ex:
        q = copy.deepcopy(p); q["extra_rules"] = [e for e in q["extra_rules"] if e is not ex[0] and e != ex[0]]
        add("unknown_rule_removed", "one brief rule removed from the policy", ["/extra_rules"], q, "R4", ["unknown_rule"],
            extra_rules=[{"statement": ex[0]["statement"], "brief_quote": ex[0]["brief_quote"]}])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("set"); ap.add_argument("brief"); ap.add_argument("--n", type=int, default=0); ap.add_argument("--only", default=""); ap.add_argument("--combo", default="", help="id,id,...: ONE build with all these faults at once (fewer simulations); fault_id = combo")
    a = ap.parse_args()
    src = MAIN / "out" / "template_cc" / a.set / a.brief
    clean = json.loads((src / "policy.json").read_text())
    rep = json.loads((src / "build_report.json").read_text())
    flags = ",".join(rep["features"])
    if a.brief in ("brief-a", "brief-b"):
        briefs, api = [f"briefs/{a.brief}.md"], "tools/openapi.json"
    else:
        briefs, api = [f"testbed/fake_briefs/{a.brief}/brief.md"], f"testbed/fake_briefs/{a.brief}/tools.openapi.json"
    if a.combo:
        sel = [x for x in faults(clean) if x["fault_id"] in a.combo.split(",")]
        comb, cl, sub = copy.deepcopy(clean), copy.deepcopy(clean), []
        from builder.template_cc import extract as _ex
        for x in sel:
            for ptr in x["pointers"]:
                v = _ex._get(x["policy"], ptr)
                _ex._set(comb, ptr, v)
            for ptr, v in (x.get("clean_override") or {}).items():
                k = [y for y in ptr.split("/") if y]; t = cl
                for y in k[:-1]:
                    t = t[y]
                t[k[-1]] = v
            sub.append({k: v for k, v in x.items() if k != "policy"} | {"clean_value": _ex._get(cl, x["pointers"][0]), "faulty_value": _ex._get(x["policy"], x["pointers"][0])})
        d = REPO / "out" / "template_cc" / "faults" / a.brief / "combo"
        d.mkdir(parents=True, exist_ok=True)
        (d / "policy.in.json").write_text(json.dumps(comb, indent=1, ensure_ascii=False))
        (d / "policy.clean.json").write_text(json.dumps(cl, indent=1, ensure_ascii=False))
        r = subprocess.run(["bash", "scripts/final_build.sh", *briefs, api, str(d)], cwd=REPO, capture_output=True, text=True,
                           env={**os.environ, "FEATURES": flags, "POLICY": str(d / "policy.in.json")})
        built = json.loads((d / "policy.json").read_text()) if (d / "workflow.json").exists() else None
        for x in sub:   # a fault whose value policy_check put back is not usable
            x["built_value"] = _ex._get(built, x["pointers"][0]) if built else None
            x["usable"] = bool(built) and (x["expected_action"] == "R4" or x["built_value"] != x["clean_value"])
        (d / "fault.json").write_text(json.dumps({"fault_id": "combo", "brief": a.brief, "set": a.set, "built": built is not None, "faults": sub}, indent=1, ensure_ascii=False))
        print(f"{a.brief}/combo: built={built is not None}", [(x["fault_id"], x["expected_action"], x["usable"]) for x in sub])
        return
    for f in [x for x in faults(clean) if not a.only or x["fault_id"] in a.only.split(",")][: a.n or None]:
        d = REPO / "out" / "template_cc" / "faults" / a.brief / f["fault_id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "policy.in.json").write_text(json.dumps(f.pop("policy"), indent=1, ensure_ascii=False))
        cl = copy.deepcopy(clean)
        for ptr, v in (f.get("clean_override") or {}).items():
            k = [x for x in ptr.split("/") if x]
            t = cl
            for x in k[:-1]:
                t = t[x]
            t[k[-1]] = v
        (d / "policy.clean.json").write_text(json.dumps(cl, indent=1, ensure_ascii=False))
        ptr0 = f["pointers"][0]
        def get(doc):
            from builder.template_cc import extract
            v = extract._get(doc, ptr0)
            return None if v is extract.extract_vote.ABSENT else v
        r = subprocess.run(["bash", "scripts/final_build.sh", *briefs, api, str(d)], cwd=REPO, capture_output=True, text=True,
                           env={**os.environ, "FEATURES": flags, "POLICY": str(d / "policy.in.json")})
        ok = (d / "workflow.json").exists()
        built = json.loads((d / "policy.json").read_text()) if ok else None
        f.update({"brief": a.brief, "set": a.set, "clean_value": get(cl), "faulty_value": get(json.loads((d / "policy.in.json").read_text())),
                  "built_value": get(built) if built else None, "built": ok})
        if ok and f["expected_action"] != "R4" and f["built_value"] == f["clean_value"]:
            f["built"] = False; f["note"] = "policy_check put the clean value back: not a usable fault"
        (d / "fault.json").write_text(json.dumps(f, indent=1, ensure_ascii=False))
        print(f"{a.brief}/{f['fault_id']}: {f['expected_action']} built={f['built']} {f.get('note', '')}")


main()
