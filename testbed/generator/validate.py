"""Independent checks. 1) The engine reproduces the kit's public cases. 2) Every generated case obeys its brief's rules."""
import datetime as dt
import json
import sys

import assemble
import configs
from engine import Policy, parse_now

import os, pathlib
KIT_DIR = os.environ.get("KIT_DIR", str(pathlib.Path(__file__).resolve().parents[2] / "build-the-builder"))
KIT = os.path.join(KIT_DIR, "public-test-set.json")


def check_public():
    pol = Policy(configs.BRIEF_A)
    errs = []
    for c in json.load(open(KIT)):
        today, _ = parse_now(c["variables"]["now"])
        for i, p in enumerate(c["products"]):
            for pr in c["expected"]["promises"]:
                if pr["product_id"] != f"p{i + 1}":
                    continue
                dl = pol.deadline(today, p["days_late"])
                d = dt.date.fromisoformat(pr["date"])
                if not (pol.cal.is_bd(d) and today <= d <= dl):
                    errs.append(f"{c['id']}: promise {d} outside [today, {dl}] or not a business day")
                floor = p.get("minimum_payment", p.get("instalment_amount"))
                if not (floor <= pr["amount"] <= p["past_due"]):
                    errs.append(f"{c['id']}: amount {pr['amount']} outside [{floor}, {p['past_due']}]")
        for cb in c["expected"]["callbacks"]:
            if not pol.callback_ok(c["variables"]["now"], dt.date.fromisoformat(cb["date"]), cb["time"]):
                errs.append(f"{c['id']}: callback {cb} invalid")
    # targeted facts from the public set
    t = dt.date(2026, 10, 14)
    assert pol.deadline(t, 14) == dt.date(2026, 10, 23), pol.deadline(t, 14)          # PUB-04
    assert pol.cal.next_bd_after(dt.date(2026, 10, 26)) == dt.date(2026, 10, 27)      # PUB-07
    assert pol.deadline(t, 4) >= dt.date(2026, 10, 30)                                 # PUB-13 card
    assert pol.attempt1(t, 16) == dt.date(2026, 10, 16)                                # last business day of this week
    return errs


def check_cases(cfg, cases):
    pol, errs = Policy(cfg), []
    outcome_pairs = {tuple(v) for v in cfg["outcome_map"].values()}
    note_vals, queue_vals = set(cfg["note_map"].values()), set(cfg["queue_map"].values())
    pre = {"nv_failed", "nv_declined", "nr_callback", "nr_no_callback", "nr_wrong_number", "nr_deceased", "nr_unclear"}
    for c in cases:
        e, o = c["expected"], c["oracle"]
        today, _ = parse_now(c["variables"]["now"])
        tag = c["id"]
        if not pol.cal.is_bd(today):
            errs.append(f"{tag}: now is not a business day")
        if (e["outcome"], e["reason"]) not in outcome_pairs:
            errs.append(f"{tag}: outcome {(e['outcome'], e['reason'])} not in the outcome table")
        pids = [p["product_id"] for p in o["products"]]
        if len(c["products"]) > cfg["max_products"]:
            errs.append(f"{tag}: too many products")
        seen = set()
        for pr in e["promises"]:
            i = pids.index(pr["product_id"])
            po = o["products"][i]
            d = dt.date.fromisoformat(pr["date"])
            if pr["product_id"] in seen:
                errs.append(f"{tag}: two promises on one product")
            seen.add(pr["product_id"])
            if not (pol.cal.is_bd(d) and today <= d <= dt.date.fromisoformat(po["deadline"])):
                errs.append(f"{tag}: promise {d} outside [today, {po['deadline']}] or not a business day")
            if not (po["floor"] - 1e-9 <= pr["amount"] <= po["past_due"] + 1e-9):
                errs.append(f"{tag}: amount {pr['amount']} outside [{po['floor']}, {po['past_due']}]")
            if pol.deadline(today, next(p for p in [c["products"][i]] for k, v in p.items() if k == cfg["account_fields"]["days_late"]) [cfg["account_fields"]["days_late"]]).isoformat() != po["deadline"]:
                errs.append(f"{tag}: deadline mismatch")
        for cb in e["callbacks"]:
            if not pol.callback_ok(c["variables"]["now"], dt.date.fromisoformat(cb["date"]), cb["time"]):
                errs.append(f"{tag}: callback {cb} invalid")
        if e["notes"] and not cfg["multi_product"]:
            errs.append(f"{tag}: notes in a single-product brief")
        for n in e["notes"]:
            if n["reason"] not in note_vals:
                errs.append(f"{tag}: note reason {n['reason']}")
            if n["product_id"] in seen:
                errs.append(f"{tag}: note on a product that has a promise")
        for t in e["transfers"]:
            if t["queue"] not in queue_vals:
                errs.append(f"{tag}: queue {t['queue']}")
        if o["outcome_key"] in pre and (e["promises"] or e["notes"] or e["transfers"]):
            errs.append(f"{tag}: records on a pre-verification outcome")
        if o["outcome_key"] == "promise" and not e["promises"]:
            errs.append(f"{tag}: promise outcome without a promise")
        if e["promises"] and o["outcome_key"] != "promise" and not o["outcome_key"].startswith("tr_"):
            errs.append(f"{tag}: promises but outcome {o['outcome_key']}")
        if cfg["multi_product"] and not o["outcome_key"].startswith("tr_") and o["outcome_key"] not in pre:
            covered = seen | {n["product_id"] for n in e["notes"]}
            if covered != set(pids):
                errs.append(f"{tag}: products without a promise or a note: {set(pids) - covered}")
        if c["family"] in ("Third party", "Deceased", "Wrong number") and not c.get("third_party"):
            errs.append(f"{tag}: third-party flag missing")
        if o["outcome_key"] == "nr_callback" and len(e["callbacks"]) != 1:
            errs.append(f"{tag}: callback outcome without exactly one callback")
    return errs


if __name__ == "__main__":
    bad = check_public()
    print("public-set check:", "OK" if not bad else bad)
    total = 0
    for cfg in configs.ALL:
        cs = assemble.build(cfg)
        e = check_cases(cfg, cs)
        total += len(e)
        print(f"{cfg['id']:<16} {len(cs):>3} cases  {'OK' if not e else str(len(e)) + ' problems'}")
        for x in e[:10]:
            print("    ", x)
    sys.exit(1 if total or bad else 0)
