"""Assemble cases per brief: pick family/variant/now/style, run the generator, resolve expected outcomes, map to the brief's own values."""
from __future__ import annotations

import random

import cases as C
from engine import Policy, parse_now

PLAN_A = {"Happy path": 7, "Third party": 7, "Identity": 7, "Dates": 11, "Hardship": 6, "Amounts": 8, "Already paid": 5, "Restructuring": 5,
          "Tool failure": 6, "Dispute": 5, "Human request": 5, "Scam question": 5, "Refusal": 8, "Declined questions": 5, "Deceased": 3,
          "Wrong number": 3, "Unclear": 4}
PLAN_B_SINGLE = {"Happy path": 2, "Third party": 2, "Identity": 2, "Dates": 2, "Hardship": 2, "Amounts": 2, "Already paid": 2, "Restructuring": 2,
                 "Tool failure": 2, "Dispute": 2, "Human request": 2, "Scam question": 1, "Refusal": 2, "Declined questions": 2, "Deceased": 1,
                 "Wrong number": 1, "Unclear": 1}
PLAN_FAKE_SINGLE = {"Happy path": 3, "Third party": 4, "Identity": 4, "Dates": 6, "Hardship": 3, "Amounts": 4, "Already paid": 2, "Restructuring": 2,
                    "Tool failure": 3, "Dispute": 2, "Human request": 3, "Scam question": 2, "Refusal": 4, "Declined questions": 3, "Deceased": 2,
                    "Wrong number": 1, "Unclear": 2}
PLAN_F6 = {**PLAN_FAKE_SINGLE, "Third party": 6, "Dates": 8, "Happy path": 2, "Already paid": 1, "Restructuring": 1, "Scam question": 1}
PLAN_FAKE_MULTI_SINGLE = {"Happy path": 2, "Third party": 2, "Identity": 2, "Dates": 3, "Hardship": 2, "Amounts": 2, "Already paid": 2, "Restructuring": 2,
                          "Tool failure": 2, "Dispute": 2, "Human request": 2, "Scam question": 1, "Refusal": 2, "Declined questions": 2, "Deceased": 1,
                          "Wrong number": 1, "Unclear": 2}

PREFIX = {"brief-a": "A", "brief-b": "B", "f1-nordhavn": "F1", "f2-ostral": "F2", "f3-kestrel": "F3", "f4-brightwell": "F4",
          "f5-halden-email": "F5", "f6-corvid-memo": "F6", "f7-lumen-codes": "F7"}

SINGLE_OUTCOME = {"refused": "np_refused", "hardship": "np_hardship", "dispute": "np_dispute", "paid": "paid", "service_error": "np_service_error"}


def plan_for(cfg):
    if cfg["id"] == "brief-a":
        return PLAN_A, 0
    if cfg["id"] == "brief-b":
        return PLAN_B_SINGLE, 30
    if cfg["id"] == "f6-corvid-memo":
        return PLAN_F6, 0
    if cfg["multi_product"]:
        return PLAN_FAKE_MULTI_SINGLE, 18
    return PLAN_FAKE_SINGLE, 0


def resolve(cfg, prods, res):
    """Canonical expected outcome from the generator's per-product results."""
    term, results = res["terminal"], res["results"]
    promises, notes, transfers = [], [], []
    if isinstance(term, str):  # ended before any product was discussed
        return {"outcome": term, "promises": [], "notes": [], "callbacks": res["callbacks"], "transfers": []}
    if isinstance(term, tuple):
        q = term[1]
        transfers = [q]
        for i, _ in enumerate(prods):
            if res.get("pre_verify"):
                break
            if cfg["multi_product"] and cfg.get("notes_on_restructuring") and q == "restructuring":
                notes.append((i, cfg["notes_on_restructuring"]))
        return {"outcome": f"tr_{q}", "promises": [], "notes": notes, "callbacks": [], "transfers": transfers}
    for i, r in enumerate(results):
        if "promise" in r:
            promises.append((i, r["promise"][0], r["promise"][1]))
        else:
            notes.append((i, r["note"]))
    if promises:
        outcome = "promise"
    elif not cfg["multi_product"]:
        outcome = SINGLE_OUTCOME[notes[0][1]]
    elif all(n[1] == "paid" for n in notes):
        outcome = "paid"
    elif cfg["close_rule"]["kind"] == "first_non_paid":
        outcome = SINGLE_OUTCOME[next(n[1] for n in notes if n[1] != "paid")]
    else:
        present = {n[1] for n in notes}
        outcome = SINGLE_OUTCOME[next(k for k in cfg["close_rule"]["order"] if k in present)]
    if not cfg["multi_product"]:
        notes = []
    return {"outcome": outcome, "promises": promises, "notes": notes, "callbacks": [], "transfers": transfers}


def to_brief(cfg, prods, canon):
    pid = lambda i: cfg["product_id_format"].format(n=i + 1)
    outcome, reason = cfg["outcome_map"][canon["outcome"]]
    return {
        "outcome": outcome, "reason": reason,
        "promises": [{"product_id": pid(i), "amount": round(a, 2), "date": dte.isoformat()} for i, a, dte in canon["promises"]],
        "notes": [{"product_id": pid(i), "reason": cfg["note_map"][r]} for i, r in canon["notes"]],
        "callbacks": [{"date": dd.isoformat(), "time": t} for dd, t in canon["callbacks"]],
        "transfers": [{"queue": cfg["queue_map"][q]} for q in canon["transfers"]],
    }


def product_json(cfg, p):
    t, f = cfg["product_types"][p["ctype"]], cfg["account_fields"]
    out = {f["type"]: t["enum"]}
    if t.get("last4_field"):
        out[t["last4_field"]] = p["last4"]
    out[f["past_due"]] = p["past_due"]
    out[f["days_late"]] = p["days_late"]
    out[t["floor_field"]] = p["floor"]
    return out


def build(cfg, seed=7):
    rng = random.Random(f"{cfg['id']}-{seed}")
    single, n_multi = plan_for(cfg)
    slots = [(fam, None) for fam, n in single.items() for _ in range(n)]
    slots += [("Multi-product", None)] * n_multi
    rng.shuffle(slots)
    variant_cycle = {}
    styles = (C.STYLES * 200)[: len(slots)]
    rng.shuffle(styles)
    nows = cfg["now_dates"]
    out = []
    for k, (fam, _) in enumerate(slots):
        if fam == "Multi-product":
            fn, nv = C.fam_multi, C.MULTI_VARIANTS
        else:
            fn, nv = C.SINGLE_FAMILIES[fam]
        cyc = variant_cycle.setdefault(fam, rng.sample(range(nv), nv))
        made = None
        for attempt in range(60):
            v = cyc[(len([c for c in out if c["family"] == fam]) + attempt) % nv]
            now = nows[(k + attempt) % len(nows)]
            ctx = C.Ctx(cfg, random.Random(f"{cfg['id']}-{k}-{attempt}-{seed}"), now)
            pp = C.person(ctx)
            ctx.ident = C.identity_values(ctx)
            try:
                prods, res = fn(ctx, pp, v)
            except C.Infeasible:
                continue
            made = (ctx, pp, prods, res, v)
            break
        if made is None:
            raise RuntimeError(f"could not make {fam} for {cfg['id']}")
        ctx, pp, prods, res, v = made
        canon = resolve(cfg, prods, res)
        variables = {"first_name": pp["first_name"], "last_name": pp["last_name"], "title": pp["title"], "now": ctx.now}
        if "company_name" in pp:
            variables["company_name"] = pp["company_name"]
        case = {
            "id": f"{PREFIX[cfg['id']]}-{k + 1:03d}", "brief": PREFIX[cfg["id"]], "family": fam if fam != "Multi-product" else "Multi-product",
            "variant": v, "level": res["level"], "title": res["title"], "style": styles[k],
            "variables": variables, "identity": ctx.ident,
            "products": [product_json(cfg, p) for p in prods],
            "script": res["script"], "expected": to_brief(cfg, prods, canon), "checks": res["checks"],
            "oracle": {
                "outcome_key": canon["outcome"],
                "products": [{"product_id": cfg["product_id_format"].format(n=i + 1), "band": ctx.pol.band(p["days_late"])["name"],
                              "deadline": ctx.pol.deadline(ctx.today, p["days_late"]).isoformat(),
                              "attempts": ctx.pol.attempts(p["days_late"]), "attempt1_date": ctx.pol.attempt1(ctx.today, p["days_late"]).isoformat(),
                              "floor": p["floor"], "past_due": p["past_due"]} for i, p in enumerate(prods)],
            },
        }
        if res["faults"]:
            case["faults"] = res["faults"]
        if res["third_party"]:
            case["third_party"] = True
        out.append(case)
    return out
