"""Local checks for statement-debt speech. No network. Also acts as speech_guard."""

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
    raw = body["response"]["data"]
    vars["selected_last4"] = raw["cardNumberLast4"]
    vars["selected_name"] = raw.get("productName") or ""
    vars["selected_number"] = number
    vars["now"] = "2026-10-12T10:30:00+03:00"
    speech.apply_debt(vars)
    return vars


def speech_guard(spoken):
    if re.search(r"\d{5,}", spoken):
        return "long digit run"
    if re.search(r"\b(USD|EUR|GBP|TL)\b", spoken):
        return "currency code"
    if re.search(r"\d{6,}\*{2,}\d{4}", spoken):
        return "masked pan"
    return ""


def fail(msg):
    raise SystemExit(msg)


def main():
    # Example 1 customer
    melis = debt_of("95004361", "540000******2000")
    text = melis["debt_body"]
    for bit in ("2000", "Yıldız Premium", "12.000", "4.800", "9.500", "2.000", "85,20", "34,10", "5 Ekim 2026", "15 Ekim 2026"):
        if bit not in text:
            fail("melis missing " + bit + " :: " + text)
    if "Dolar ve İngiliz Sterlini borcunuz bulunmamaktadır." not in text:
        fail("melis empty fx " + text)
    if melis["route"] != "need_email":
        fail("melis route " + melis["route"])
    hit = speech_guard(text)
    if hit:
        fail("melis guard " + hit)

    # Compose offer with email
    melis["email_mode"] = "offer"
    email = customer("95004361")["services"]["contact.email.get"]
    melis.update(envelope("load_email", email["http"], email.get("response")))
    speech.apply_email(melis)
    if melis["route"] != "offer" or speech.OFFER_BOTH not in melis["debt_speech"]:
        fail("melis offer " + str(melis.get("debt_speech")))

    # Example 6: no email -> week-only offer
    bare = debt_of("96125950", "540000******7200")
    bare["email_mode"] = "offer"
    email = customer("96125950")["services"]["contact.email.get"]
    bare.update(envelope("load_email", email["http"], email.get("response")))
    speech.apply_email(bare)
    if bare["route"] != "offer" or speech.OFFER_WEEK not in bare["debt_speech"]:
        fail("bare offer " + str(bare.get("debt_speech")))
    if "mail" in bare["debt_speech"] and "kayıtlı mail" in bare["debt_speech"]:
        fail("bare mentioned email " + bare["debt_speech"])

    # Multi card asks another first
    multi = debt_of("97526380", "540000******1101")
    if multi["route"] != "ask_another" or speech.ANOTHER not in multi["debt_speech"]:
        fail("multi " + multi["route"] + " " + multi.get("debt_speech", ""))

    # Min paid / below
    paid = debt_of("92030487", "540000******6184")["debt_body"]
    if "Minimum ödemenizi yaptığınız için teşekkür ederiz." not in paid:
        fail("min paid " + paid)
    below = debt_of("92030487", "545000******4253")["debt_body"]
    if "kalan minimum ödeme tutarınız" not in below:
        fail("below " + below)

    # Disambiguation sentences
    ilgaz = cards_of("92030487")
    ilgaz["card_pick"] = "Yıldız Pratik"
    speech.apply_pick(ilgaz)
    if ilgaz["route"] != "ask_last4":
        fail("ilgaz name " + ilgaz["route"])
    if "isimli birden fazla kartınız var" not in ilgaz["question_line"]:
        fail("last4 prompt " + ilgaz["question_line"])
    ilgaz["last4_said"] = "4253"
    speech.apply_last4(ilgaz)
    if "son altı" not in ilgaz["question_line"] or "4253" not in ilgaz["question_line"]:
        fail("last6 " + ilgaz["question_line"])

    # Week change happy path amounts for 96158684
    week = cards_of("96158684")
    week["selected_last4"] = "1110"
    week["selected_name"] = "Yıldız Miles"
    week["selected_number"] = "540000******1110"
    body = customer("96158684")["services"]["statement.week.get"]["540000******1110"]
    week.update(envelope("load_week", body["http"], body.get("response")))
    speech.apply_week(week)
    if "hesap kesim tarihi her ayın" not in week["week_speech"]:
        fail("week speech " + week.get("week_speech", ""))
    if "2" in week.get("other_weeks", "").split():
        fail("current week offered " + week["other_weeks"])

    decide = customer("96158684")["services"]["statement.week.decide"]["540000******1110"]["3"]
    week["chosen_week"] = "3"
    week["chosen_ordinal"] = "üçüncü"
    week.update(envelope("load_decide", decide["http"], decide["response"]))
    speech.apply_decide(week)
    if "Onaylıyor musunuz?" not in week["preview_speech"]:
        fail("preview " + week.get("preview_speech", ""))
    if "16 Kasım 2026" not in week["preview_speech"] or "26 Kasım 2026" not in week["preview_speech"]:
        fail("preview dates " + week["preview_speech"])

    change = customer("96158684")["services"]["statement.week.change"]["540000******1110"]["3"]
    week.update(envelope("load_change", change["http"], change["response"]))
    speech.apply_change(week)
    if week["route"] != "done":
        fail("change route " + week["route"])

    # Guards across customers
    for person in DATA["customers"]:
        listing = person["services"]["cards.list"]
        if listing.get("http") != 200:
            continue
        for card in listing["response"]["data"]["cards"]:
            number = card["cardNumber"]
            spoken = debt_of(person["customerNo"], number)["debt_body"]
            hit = speech_guard(spoken)
            if hit:
                fail(hit + " " + person["customerNo"] + " " + spoken)
            if number in spoken or (card["cardNumber"][:6].isdigit() and card["cardNumber"][:6] in spoken):
                fail("pan " + person["customerNo"])

    # Anger / silence
    vars = {"anger_count": "0"}
    routes = []
    for _ in range(4):
        speech.apply_anger(vars)
        routes.append(vars["route"])
    if routes != ["calm", "calm", "calm", "end"]:
        fail("anger " + str(routes))
    quiet = {"silence_count": "0"}
    speech.apply_silence(quiet)
    speech.apply_silence(quiet)
    speech.apply_silence(quiet)
    if quiet.get("route") != "end":
        fail("silence")

    print("speech checks passed")


if __name__ == "__main__":
    main()
