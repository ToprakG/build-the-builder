"""Local checks for the statement-debt speech. No network."""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import speech

ROOT = Path("/Users/toprakgundogdu/Downloads/test-environment/test-customers.json")
DATA = json.loads(ROOT.read_text())
BY_NO = {c["customerNo"]: c for c in DATA["customers"]}


def flatten(obj, prefix, out):
    if isinstance(obj, dict):
        for key, value in obj.items():
            flatten(value, prefix + "." + str(key), out)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            flatten(value, prefix + "." + str(index), out)
    elif obj is None:
        return
    elif isinstance(obj, bool):
        out[prefix] = "true" if obj else "false"
    else:
        out[prefix] = str(obj)


def envelope(node, http, body):
    out = {node + "_status_code": str(http), node + "_response.status_code": str(http)}
    if body is not None:
        flatten(body, node + "_response.data", out)
    return out


def customer(no):
    return BY_NO[no]


def cards_of(no):
    body = customer(no)["services"]["cards.list"]
    vars = envelope("list_cards", body["http"], body.get("response"))
    speech.apply_cards(vars)
    return vars


def debt_of(no, number):
    picked = cards_of(no)
    body = customer(no)["services"]["cards.balance"][number]
    vars = dict(picked)
    vars.update(envelope("load_balance", body["http"], body.get("response")))
    # pretend this card was selected
    raw = body["response"]["data"]
    vars["selected_last4"] = raw["cardNumberLast4"]
    vars["selected_name"] = raw.get("productName") or ""
    vars["selected_number"] = number
    speech.apply_debt(vars)
    return vars


def fail(msg):
    raise SystemExit(msg)


def main():
    ada = debt_of("90000001", "540000******1234")
    text = ada["debt_speech"]
    for bit in ("5.000,13", "2.000,05", "1234", "Yıldız Pratik Classic", speech.OFFER):
        if bit not in text:
            fail("ada missing " + bit + " :: " + text)
    if ada["debt_path"] != "debt":
        fail("ada path")
    if "USD" in text or "TL" in text.split():
        fail("ada code " + text)

    tulay = debt_of("92122831", "540000******0411")["debt_speech"]
    for bit in ("Türk Lirası ve İngiliz Sterlini cinsinden bir borcunuz yok.", "120 Dolar", "240,50 Euro", "96,20 Euro"):
        if bit not in tulay:
            fail("tulay missing " + bit + " :: " + tulay)
    if "USD" in tulay or "EUR" in tulay:
        fail("tulay code")

    asli = debt_of("92311921", "540000******1896")
    if asli["debt_path"] != "none":
        fail("asli path " + asli.get("debt_speech", ""))
    if "eksi 1.896" not in asli["debt_speech"]:
        fail("asli total " + asli["debt_speech"])
    if "ödenmemiş bir borç görünmüyor" not in asli["debt_speech"]:
        fail("asli none line")

    onur = debt_of("96026000", "540000******1250")
    if onur["debt_path"] != "none" or "1.250" not in onur["debt_speech"]:
        fail("onur " + onur.get("debt_speech", ""))

    paid = debt_of("92030487", "540000******6184")["debt_speech"]
    if "Asgari ödemenizi yaptınız, teşekkür ederiz." not in paid or "1.900" not in paid:
        fail("min paid " + paid)
    below = debt_of("92030487", "545000******4253")["debt_speech"]
    if "4.000" not in below or "kalan asgari" not in below:
        fail("below " + below)

    nil = debt_of("94896459", "540000******7007")["debt_speech"]
    if "İngiliz Sterlini" not in nil or "GBP" in nil:
        fail("gbp " + nil)

    empty = debt_of("96451507", "540000******1303")["debt_speech"]
    if "Sonu 1303 ile biten" not in empty:
        fail("empty name " + empty)
    if "Yıldız" in empty:
        fail("invented name " + empty)

    leak = debt_of("93075404", "540000******1402")["debt_speech"]
    if "1401" in leak or "1403" in leak or re.search(r"\d{5,}", leak):
        fail("leak " + leak)
    if "3.100" not in leak:
        fail("injection debt " + leak)

    # disambiguation
    ilgaz = cards_of("92030487")
    if ilgaz["route"] != "many":
        fail("ilgaz menu")
    ilgaz["card_pick"] = "Yıldız Pratik"
    speech.apply_pick(ilgaz)
    if ilgaz["route"] != "ask_last4":
        fail("ilgaz name " + ilgaz["route"])
    ilgaz["last4_said"] = "4253"
    speech.apply_last4(ilgaz)
    if ilgaz["route"] != "ask_last6":
        fail("ilgaz last4 " + ilgaz["route"])
    if "4253" not in ilgaz["question_line"] or "son altı" not in ilgaz["question_line"]:
        fail("last6 sentence " + ilgaz["question_line"])
    ilgaz["last6_said"] = "004253"
    speech.apply_last6(ilgaz)
    if ilgaz["route"] != "ask_full":
        fail("ilgaz last6 " + ilgaz["route"])
    ilgaz["full_said"] = "5450009988774253"
    speech.apply_full(ilgaz)
    if ilgaz.get("selected_number") != "545000******4253":
        fail("full match " + str(ilgaz.get("selected_number")))

    # unique last4 inside a shared name
    ilgaz2 = cards_of("92030487")
    ilgaz2["card_pick"] = "6184"
    speech.apply_pick(ilgaz2)
    if ilgaz2.get("selected_last4") != "6184":
        fail("unique last4")

    # week linked
    sevda = cards_of("92220795")
    sevda["selected_last4"] = "3201"
    sevda["selected_name"] = "Yıldız Pratik"
    body = customer("92220795")["services"]["statement.week.get"]["540000******3201"]
    sevda.update(envelope("load_week", body["http"], body["response"]))
    speech.apply_week(sevda)
    if "buna bağlı Yıldız Premium" not in sevda["week_speech"] or "üçüncü" not in sevda["week_speech"]:
        fail("week M " + sevda.get("week_speech", ""))
    child = dict(sevda)
    child["selected_last4"] = "3202"
    child["selected_name"] = "Yıldız Premium"
    body = customer("92220795")["services"]["statement.week.get"]["540000******3202"]
    child.update(envelope("load_week", body["http"], body["response"]))
    speech.apply_week(child)
    if "bağlı olduğu Yıldız Pratik" not in child["week_speech"]:
        fail("week C " + child.get("week_speech", ""))

    # decide preview then change
    decide = customer("92220795")["services"]["statement.week.decide"]["540000******3201"]["1"]
    sevda["chosen_week"] = "1"
    sevda["chosen_ordinal"] = "birinci"
    sevda["relation_type"] = "M"
    sevda["related_name"] = "Yıldız Premium"
    sevda.update(envelope("load_decide", decide["http"], decide["response"]))
    speech.apply_decide(sevda)
    if "alınacak" not in sevda["preview_speech"] or "onaylar mısınız" not in sevda["preview_speech"]:
        fail("preview " + sevda.get("preview_speech", ""))
    change = customer("92220795")["services"]["statement.week.change"]["540000******3201"]["1"]
    sevda.update(envelope("load_change", change["http"], change["response"]))
    speech.apply_change(sevda)
    if sevda["route"] != "done" or "alındı" not in sevda["week_done_line"]:
        fail("change " + str(sevda.get("week_done_line")))

    # no cards / list error
    ozan = customer("93821776")["services"]["cards.list"]
    vars = envelope("list_cards", ozan["http"], ozan.get("response"))
    speech.apply_cards(vars)
    if vars["route"] != "none":
        fail("ozan " + vars["route"])
    alp = customer("97073972")["services"]["cards.list"]
    vars = envelope("list_cards", alp["http"], alp.get("response"))
    speech.apply_cards(vars)
    if vars["route"] != "error" or "müşteri temsilcimize" not in vars["service_error_line"]:
        fail("alp")

    # every card: no long digit run, no masked pan, no currency code
    for person in DATA["customers"]:
        listing = person["services"]["cards.list"]
        if listing.get("http") != 200:
            continue
        for card in listing["response"]["data"]["cards"]:
            number = card["cardNumber"]
            spoken = debt_of(person["customerNo"], number)["debt_speech"]
            if number in spoken or card["cardNumber"][:6] in spoken:
                fail("pan in speech " + person["customerNo"] + " " + spoken)
            if re.search(r"\d{5,}", spoken):
                fail("long digits " + person["customerNo"] + " " + spoken)
            if re.search(r"\b(USD|EUR|GBP|TL)\b", spoken):
                fail("code " + person["customerNo"] + " " + spoken)

    # three calms then transfer
    vars = {"anger_count": "0"}
    routes = []
    for _ in range(4):
        speech.apply_anger(vars)
        routes.append(vars["route"])
    if routes != ["calm", "calm", "calm", "end"]:
        fail("anger " + str(routes))

    # email empty
    z = cards_of("96125950")
    email = customer("96125950")["services"]["contact.email.get"]
    z.update(envelope("load_email", email["http"], email.get("response")))
    speech.apply_email(z)
    if z["route"] != "no_email":
        fail("empty email " + z["route"])

    week = {"week_said": "boş ver, kalsın", "current_week": "2", "other_weeks": "1 3 4", "week_choices": "birinci, üçüncü ve dördüncü"}
    speech.apply_week_pick(week)
    if week.get("route") != "cancel":
        fail("cancel week " + str(week.get("route")))
    kept = {"week_said": "üçüncü", "current_week": "2", "other_weeks": "1 3 4"}
    speech.apply_week_pick(kept)
    if kept.get("route") != "decide" or kept.get("chosen_week") != "3":
        fail("pick week " + str(kept.get("route")))

    quiet = {"silence_count": "0"}
    speech.apply_silence(quiet)
    speech.apply_silence(quiet)
    if quiet.get("route") != "again":
        fail("silence early " + str(quiet.get("route")))
    speech.apply_silence(quiet)
    if quiet.get("route") != "end" or "Sesinizi alamadığım için" not in quiet.get("handoff_line", ""):
        fail("silence end " + str(quiet))

    print("speech checks passed")


if __name__ == "__main__":
    main()
