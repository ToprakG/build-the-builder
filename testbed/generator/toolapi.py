"""Per-brief tool API files, derived from the kit's openapi.json: same seven tool names, fields and enums from the config."""
import copy
import json

import os, pathlib
KIT_DIR = os.environ.get("KIT_DIR", str(pathlib.Path(__file__).resolve().parents[2] / "build-the-builder"))
KIT_API = os.path.join(KIT_DIR, "tools", "openapi.json")

FIELD_DESC = {
    "date_of_birth": "For date_of_birth: the date as YYYY-MM-DD. An unreadable value is rejected with ok=false and is not counted as a wrong answer.",
    "phone_last4": "For phone_last4: four digits.",
    "postcode": "For postcode: capitals with one space before the last three characters, e.g. LS6 2HQ.",
    "birth_year": "For birth_year: four digits.",
    "company_number": "For company_number: eight digits, no spaces.",
    "founded_year": "For founded_year: four digits.",
}


def _body(api, path):
    return api["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]


def _resp(api, path):
    return api["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]


def build(cfg):
    api = copy.deepcopy(json.load(open(KIT_API)))
    api["info"]["title"] = f"{cfg['bank']} collections tools"
    api["info"]["description"] = (f"Mock of {cfg['bank']}'s systems (synthetic). The service does not enforce the brief: following the brief is the agent's job. "
                                  "The seven tool calls need no key: the customer_id in the body is the secret of one call.")
    api["servers"] = [{"url": f"https://bank-mock.invalid/{cfg['id']}/v1"}]
    t = cfg["tools"]

    # verify_identity
    b = _body(api, "/verify_identity")
    vf, va = t["verify_identity"]["field"], t["verify_identity"]["answer"]
    props = {"customer_id": {"type": "string"},
             vf: {"type": "string", "enum": cfg["identity"]["questions"]},
             va: {"type": "string", "description": " ".join(FIELD_DESC[q] for q in cfg["identity"]["questions"])}}
    b["properties"], b["required"] = props, ["customer_id", vf, va]
    api["paths"]["/verify_identity"]["post"]["summary"] = f"Check one security answer. {cfg['identity']['max_wrong']} wrong answers in total end the check."
    r = _resp(api, "/verify_identity")
    if not cfg["identity"].get("swap_hint"):
        r["properties"].pop("hint", None)

    # get_account
    r = _resp(api, "/get_account")
    af = cfg["account_fields"]
    item = {af["product_id"]: {"type": "string"},
            af["type"]: {"type": "string", "enum": [x["enum"] for x in cfg["product_types"].values()]}}
    for x in cfg["product_types"].values():
        if x.get("last4_field"):
            item[x["last4_field"]] = {"type": "string", "description": f"{x['enum']} only"}
    item[af["past_due"]] = {"type": "number"}
    item[af["days_late"]] = {"type": "integer"}
    floors = {}
    for x in cfg["product_types"].values():
        floors.setdefault(x["floor_field"], []).append(x["enum"])
    for f, enums in floors.items():
        item[f] = {"type": "number", "description": " and ".join(enums) + " only"}
    r["properties"]["products"]["items"]["properties"] = item
    api["paths"]["/get_account"]["post"]["summary"] = "The late products. Refused until every security question is passed." + (" One to three products." if cfg["multi_product"] else "")

    # create_promise
    b = _body(api, "/create_promise")
    cp = t["create_promise"]
    amt = {"type": "integer", "description": "Amount in cents (minor units)."} if cp["amount_unit"] == "cents" else {"type": "number"}
    b["properties"] = {"customer_id": {"type": "string"}, cp["product_id"]: {"type": "string"}, cp["amount"]: amt, cp["date"]: {"type": "string", "format": "date"}}
    b["required"] = ["customer_id", cp["product_id"], cp["amount"], cp["date"]]

    # add_note
    b = _body(api, "/add_note")
    an = t["add_note"]
    b["properties"] = {"customer_id": {"type": "string"}, an["product_id"]: {"type": "string"},
                       an["reason"]: {"type": "string", "enum": list(cfg["note_map"].values())}, "text": {"type": "string"}}
    b["required"] = ["customer_id", an["product_id"], an["reason"]]
    api["paths"]["/add_note"]["post"]["summary"] = "Record why a product ended without a promise." if cfg["multi_product"] else "Not used by this brief."

    # schedule_callback
    b = _body(api, "/schedule_callback")
    sc = t["schedule_callback"]
    if sc["mode"] == "datetime":
        b["properties"] = {"customer_id": {"type": "string"}, sc["datetime"]: {"type": "string", "description": "YYYY-MM-DDTHH:MM"}}
        b["required"] = ["customer_id", sc["datetime"]]

    # transfer_to_agent
    b = _body(api, "/transfer_to_agent")
    b["properties"]["queue"]["enum"] = list(dict.fromkeys(cfg["queue_map"].values()))

    # close_call
    b = _body(api, "/close_call")
    cc = t["close_call"]
    if cc["mode"] == "code":
        b["properties"] = {"customer_id": {"type": "string"}, cc["code"]: {"type": "string", "enum": [v[0] for v in cfg["outcome_map"].values()],
                                                                            "description": "One disposition code from the brief's code table."}}
        b["required"] = ["customer_id", cc["code"]]
    else:
        outs = list(dict.fromkeys(v[0] for v in cfg["outcome_map"].values()))
        reasons = sorted({v[1] for v in cfg["outcome_map"].values() if v[1]})
        b["properties"]["outcome"]["enum"] = outs
        b["properties"]["reason"] = {"type": "string", "enum": reasons, "description": "Required for every outcome that has a reason in the brief's table."}
    return api
