"""Statement-debt speech and routing. Pure functions. No braces that the workflow engine treats as variables."""

import json
import re

MONTHS = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
ORDINAL = {"1": "birinci", "2": "ikinci", "3": "üçüncü", "4": "dördüncü"}
SERVICE_ERROR = "Şu anda işleminizi gerçekleştiremiyorum, sizi müşteri temsilcimize aktarıyorum."
WEEK_SERVICE_ERROR = "Şu anda hesap kesim tarihinizi değiştiremiyorum, sizi müşteri temsilcimize aktarıyorum."
HANDOFF = "Sizi ana menüye yönlendiriyorum."
HANDOFF_MENU = "Sizi ana menüye aktarıyorum."
OFFER_BOTH = "Hesap kesim tarihinizi değiştirebilirim ya da ekstrenizi kayıtlı mail adresinize gönderebilirim."
OFFER_WEEK = "Hesap kesim tarihinizi değiştirebilirim."
OFFER_EMAIL = "Ekstrenizi kayıtlı mail adresinize gönderebilirim."
OFFER = OFFER_BOTH
ANOTHER = "Başka bir kartınızın borcunu öğrenmek ister misiniz?"
NO_EMAIL = "Bankamızda kayıtlı bir e-posta adresiniz bulunmadığı için ekstrenizi gönderemiyorum."
NO_CARD = "Ekstre borcunu sorgulayabileceğiniz uygun bir kredi kartınız bulunmuyor. Sizi ana menüye aktarıyorum."
WEEK_FAIL = WEEK_SERVICE_ERROR
WEEK_MONTH = "Hesap kesim tarihinizi yılda bir kez değiştirebilirsiniz."
WEEK_YEAR = "Hesap kesim tarihinizi yılda bir kez değiştirebilirsiniz."
SENT_OK = "Ekstreniz kayıtlı e-posta adresinize gönderildi."


def tr_lower(text):
    return str(text or "").replace("İ", "i").replace("I", "ı").lower()


def folded(text):
    return tr_lower(text).translate(str.maketrans("çğıöşü", "cgiosu"))


def gave_up(text):
    """The caller cancelled. A week number in the same sentence is still a choice."""
    if parse_week(text, "") in ("1", "2", "3", "4"):
        return False
    t = folded(text)
    return any(word in t for word in ("kalsin", "bos ver", "vazgec", "hayir", "istemiyorum", "tesekkur", "bu kadar", "baska bir sey"))


def money(raw):
    s = str(raw if raw is not None else "0").strip().replace(" ", "")
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    if not s:
        s = "0"
    if "." in s:
        whole, frac = s.split(".", 1)
        frac = (re.sub(r"\D", "", frac) + "00")[:2]
    else:
        whole, frac = s, "00"
    whole = re.sub(r"\D", "", whole).lstrip("0") or "0"
    groups = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    body = ".".join(reversed(groups))
    if frac != "00":
        body = body + "," + frac
    if neg and body != "0":
        return "eksi " + body
    return body


def speak_date(raw):
    s = str(raw or "").strip()
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", s)
    if not m:
        return s
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if month < 1 or month > 12:
        return s
    return str(day) + " " + MONTHS[month] + " " + str(year)


def digits_of(text):
    words = [
        ("sıfır", "0"), ("sifir", "0"), ("dokuz", "9"), ("sekiz", "8"), ("yedi", "7"),
        ("altı", "6"), ("alti", "6"), ("beş", "5"), ("bes", "5"), ("dört", "4"),
        ("dort", "4"), ("üç", "3"), ("uc", "3"), ("iki", "2"), ("bir", "1"),
    ]
    t = tr_lower(text)
    for word, digit in words:
        t = t.replace(word, digit)
    return re.sub(r"\D", "", t)


def http_status(vars, node):
    for key in (node + "_status_code", node + "_response.status_code"):
        raw = vars.get(key)
        if raw not in (None, ""):
            try:
                return int(float(str(raw)))
            except Exception:
                pass
    return 0


def _as_obj(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def unflatten(vars, prefix):
    out = {}
    head = prefix + "."
    for key, value in vars.items():
        if not isinstance(key, str) or not key.startswith(head):
            continue
        if value is None or value == "":
            continue
        cur = out
        parts = key[len(head):].split(".")
        for part in parts[:-1]:
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[part] = nxt
            cur = nxt
        cur[parts[-1]] = value
    return out


def _lists(obj):
    if isinstance(obj, dict):
        built = {}
        for key, value in obj.items():
            built[key] = _lists(value)
        if built and all(str(key).isdigit() for key in built):
            return [built[key] for key in sorted(built, key=lambda item: int(item))]
        return built
    if isinstance(obj, list):
        return [_lists(item) for item in obj]
    return obj


def parse_body(vars, node):
    status = http_status(vars, node)
    blob = _as_obj(vars.get(node + "_response.data"))
    if isinstance(blob, dict) and isinstance(blob.get("data"), (dict, list)):
        result = blob.get("result") if isinstance(blob.get("result"), dict) else {}
        return status, blob["data"], result
    flat = _lists(unflatten(vars, node + "_response.data"))
    if isinstance(flat, dict) and isinstance(flat.get("data"), (dict, list)):
        result = flat.get("result") if isinstance(flat.get("result"), dict) else {}
        return status, flat["data"], result
    if isinstance(flat, dict):
        return status, flat, {}
    return status, {}, {}


def _num(raw):
    try:
        return float(str(raw).replace(",", "."))
    except Exception:
        return 0.0


def _has(block):
    if not isinstance(block, dict):
        return False
    return str(block.get("statementPaymentStatus") or "").upper() == "HAS_DEBT"


def _last4_of(number, fallback):
    fallback = re.sub(r"\D", "", str(fallback or ""))
    if len(fallback) >= 4:
        return fallback[-4:]
    digits = re.sub(r"\D", "", str(number or ""))
    if len(digits) >= 4:
        return digits[-4:]
    return fallback


def _bin_of(number):
    text = str(number or "")
    match = re.match(r"^(\d{6})", text)
    if match:
        return match.group(1)
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 6 and "*" not in text[:6]:
        return digits[:6]
    return digits[:6]


def card_from_service(raw):
    number = str(raw.get("cardNumber") or "")
    last4 = _last4_of(number, raw.get("cardNumberLast4"))
    name = str(raw.get("productName") or "").strip()
    return {
        "number": number,
        "last4": last4,
        "name": name,
        "bin": _bin_of(number),
        "full": "*" not in number and len(re.sub(r"\D", "", number)) >= 15,
        "status": str(raw.get("status") or "A"),
    }


def label(card):
    if card.get("name"):
        return "Son dört hanesi " + card["last4"] + " olan " + card["name"] + " kartınız"
    return "Sonu " + card["last4"] + " ile biten kartınız"


def _load_cards(vars):
    raw = vars.get("cards_json")
    if isinstance(raw, str) and raw.strip().startswith("["):
        try:
            data = json.loads(raw)
        except Exception:
            data = []
        if isinstance(data, list):
            return data
    return []


def _save_cards(vars, cards):
    vars["cards_json"] = json.dumps(cards, ensure_ascii=False)
    vars["card_count"] = str(len(cards))


def _select(vars, card):
    vars["selected_number"] = card["number"]
    vars["selected_last4"] = card["last4"]
    vars["selected_name"] = card.get("name") or ""
    vars["selected_bin"] = card.get("bin") or ""
    vars["miss_count"] = "0"


def _bump(vars):
    n = int(float(vars.get("miss_count") or 0)) + 1
    vars["miss_count"] = str(n)
    if n >= 3:
        vars["route"] = "tries"
    else:
        vars["route"] = "retry"


def apply_cards(vars):
    try:
        status, data, result = parse_body(vars, "list_cards")
        cards = []
        if isinstance(data, dict):
            for item in data.get("cards") or []:
                if isinstance(item, dict):
                    card = card_from_service(item)
                    if card["status"] in ("", "A") and card["last4"]:
                        cards.append(card)
        message = str(result.get("messageText") or result.get("message") or "")
        if status >= 500 or (status == 0 and not cards):
            vars["route"] = "error"
            vars["service_error_line"] = SERVICE_ERROR
            return
        if status == 404 or not cards:
            vars["route"] = "none"
            vars["handoff_line"] = NO_CARD
            return
        _save_cards(vars, cards)
        if len(cards) == 1:
            _select(vars, cards[0])
            vars["route"] = "one"
            vars["resume_at"] = "say_debt"
            return
        names = [card["name"] for card in cards if card.get("name")]
        examples = names[:2]
        if len(examples) == 2:
            vars["cards_menu"] = "Hangi kartınızın borcunu öğrenmek istersiniz? Örneğin, " + examples[0] + " veya " + examples[1] + " diyebilirsiniz."
        elif len(examples) == 1:
            vars["cards_menu"] = "Hangi kartınızın borcunu öğrenmek istersiniz? Örneğin, " + examples[0] + " diyebilirsiniz."
        else:
            vars["cards_menu"] = "Hangi kartınızın borcunu öğrenmek istersiniz?"
        vars["question_line"] = vars["cards_menu"]
        vars["replay_line"] = vars["cards_menu"]
        vars["resume_at"] = "choose_card"
        vars["miss_count"] = "0"
        vars["route"] = "many"
    except Exception:
        vars["route"] = "error"
        vars["service_error_line"] = SERVICE_ERROR


def _name_matches(cards, text):
    spoken = tr_lower(text)
    exact = []
    for card in cards:
        name = tr_lower(card.get("name") or "")
        if name and name in spoken:
            exact.append(card)
    return exact


def _by_last4(cards, digits):
    if len(digits) < 4:
        return []
    last4 = digits[-4:]
    return [card for card in cards if card["last4"] == last4]


def _by_last6(cards, digits):
    if len(digits) < 6:
        return []
    last6 = digits[-6:]
    found = []
    for card in cards:
        if card.get("full"):
            number = re.sub(r"\D", "", card["number"])
            if number[-6:] == last6:
                found.append(card)
        elif card["last4"] == last6[-4:]:
            found.append(card)
    return found


def _by_full(cards, digits):
    if len(digits) < 15:
        return []
    found = []
    for card in cards:
        number = re.sub(r"\D", "", card["number"])
        if card.get("full") and number == digits:
            found.append(card)
        elif card.get("bin") and digits.startswith(card["bin"]) and digits.endswith(card["last4"]):
            found.append(card)
    return found


def _ambiguous_name(group):
    names = set(tr_lower(card.get("name") or "") for card in group)
    return len(names) == 1 and len(group) > 1


def apply_pick(vars):
    try:
        cards = _load_cards(vars)
        text = str(vars.get("card_pick") or "")
        digits = digits_of(text)
        found = []
        if len(digits) >= 15:
            found = _by_full(cards, digits)
        elif len(digits) >= 6:
            found = _by_last6(cards, digits)
        elif len(digits) >= 4:
            found = _by_last4(cards, digits)
            named = _name_matches(found or cards, text)
            if named and found:
                both = [card for card in found if card in named]
                if both:
                    found = both
        else:
            found = _name_matches(cards, text)
        if len(found) == 1:
            _select(vars, found[0])
            vars["route"] = "selected"
            vars["resume_at"] = "say_debt"
            return
        if len(found) > 1 and _ambiguous_name(found):
            last4s = set(card["last4"] for card in found)
            vars["pending_json"] = json.dumps(found, ensure_ascii=False)
            if len(last4s) == 1:
                vars["pending_last4"] = found[0]["last4"]
                vars["question_line"] = "Son dört hanesi " + found[0]["last4"] + " olan birden fazla kartınız var. Kartınızın son altı hanesini söyler misiniz?"
                vars["replay_line"] = vars["question_line"]
                vars["resume_at"] = "ask_last6"
                vars["miss_count"] = "0"
                vars["route"] = "ask_last6"
                return
            name = found[0].get("name") or "Kart"
            vars["question_line"] = name + " isimli birden fazla kartınız var. Kartınızın son dört hanesini söyler misiniz?"
            vars["replay_line"] = vars["question_line"]
            vars["resume_at"] = "ask_last4"
            vars["miss_count"] = "0"
            vars["route"] = "ask_last4"
            return
        if len(found) > 1:
            vars["question_line"] = "Birden fazla kart eşleşti. Kartınızın son dört hanesini söyler misiniz?"
            vars["replay_line"] = vars["question_line"]
            vars["pending_json"] = json.dumps(found, ensure_ascii=False)
            vars["resume_at"] = "ask_last4"
            vars["miss_count"] = "0"
            vars["route"] = "ask_last4"
            return
        _bump(vars)
        vars["resume_at"] = "choose_card"
    except Exception:
        vars["route"] = "error"
        vars["service_error_line"] = SERVICE_ERROR


def _pending(vars, cards):
    raw = vars.get("pending_json")
    if isinstance(raw, str) and raw.strip().startswith("["):
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, list) and data:
            return data
    return cards


def apply_last4(vars):
    try:
        cards = _pending(vars, _load_cards(vars))
        digits = digits_of(vars.get("last4_said"))
        found = _by_last4(cards, digits)
        if len(found) == 1:
            _select(vars, found[0])
            vars["route"] = "selected"
            vars["resume_at"] = "say_debt"
            return
        if len(found) > 1:
            vars["pending_json"] = json.dumps(found, ensure_ascii=False)
            vars["pending_last4"] = found[0]["last4"]
            vars["question_line"] = "Son dört hanesi " + found[0]["last4"] + " olan birden fazla kartınız var. Kartınızın son altı hanesini söyler misiniz?"
            vars["replay_line"] = vars["question_line"]
            vars["resume_at"] = "ask_last6"
            vars["miss_count"] = "0"
            vars["route"] = "ask_last6"
            return
        _bump(vars)
        vars["resume_at"] = "ask_last4"
    except Exception:
        vars["route"] = "error"
        vars["service_error_line"] = SERVICE_ERROR


def apply_last6(vars):
    try:
        cards = _pending(vars, _load_cards(vars))
        digits = digits_of(vars.get("last6_said"))
        found = _by_last6(cards, digits)
        if len(found) == 1:
            _select(vars, found[0])
            vars["route"] = "selected"
            vars["resume_at"] = "say_debt"
            return
        if len(found) > 1:
            vars["pending_json"] = json.dumps(found, ensure_ascii=False)
            vars["question_line"] = "Son altı hanesi aynı olan birden fazla kartınız var. Kart numaranızı söyler misiniz?"
            vars["replay_line"] = vars["question_line"]
            vars["resume_at"] = "ask_full"
            vars["miss_count"] = "0"
            vars["route"] = "ask_full"
            return
        _bump(vars)
        vars["resume_at"] = "ask_last6"
    except Exception:
        vars["route"] = "error"
        vars["service_error_line"] = SERVICE_ERROR


def apply_full(vars):
    try:
        cards = _pending(vars, _load_cards(vars))
        digits = digits_of(vars.get("full_said"))
        found = _by_full(cards, digits)
        if len(found) == 1:
            _select(vars, found[0])
            vars["route"] = "selected"
            vars["resume_at"] = "say_debt"
            return
        _bump(vars)
        vars["resume_at"] = "ask_full"
    except Exception:
        vars["route"] = "error"
        vars["service_error_line"] = SERVICE_ERROR



def _bucket(block):
    remaining = _num(block.get("remainingDebt"))
    min_pay = _num(block.get("minPayment"))
    left_min = _num(block.get("remainingMinPayment"))
    if remaining <= 0.001:
        return "clear"
    if left_min <= 0.001:
        return "min_or_more"
    if left_min + 0.001 >= min_pay:
        return "none_paid"
    return "below"


def _fx_line(block, unit):
    return (
        unit + " borcunuz " + money(block.get("statementDebt")) + " " + unit
        + ", kalan toplam borcunuz " + money(block.get("remainingDebt")) + " " + unit
        + ", ödenmesi gereken minimum tutar " + money(block.get("minPayment")) + " " + unit + "'dir."
    )


def _card_intro(card_name, last4):
    if card_name:
        return "Sonu " + last4 + " ile biten " + card_name
    return "Sonu " + last4 + " ile biten kartınız"


def _empty_debt_sentence(empty):
    names = {"tl": "Türk Lirası", "usd": "Dolar", "eur": "Euro", "gbp": "İngiliz Sterlini"}
    labels = [names[key] for key in empty]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0] + " borcunuz bulunmamaktadır."
    if len(labels) == 2:
        return labels[0] + " ve " + labels[1] + " borcunuz bulunmamaktadır."
    return ", ".join(labels[:-1]) + " ve " + labels[-1] + " borcunuz bulunmamaktadır."


def _due_sentence(raw, now_raw=""):
    due = speak_date(raw)
    if not due:
        return ""
    now = str(now_raw or "")
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", now)
    d = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", str(raw or "").strip())
    if m and d:
        if int(m.group(1)) == int(d.group(3)) and int(m.group(2)) == int(d.group(2)) and int(m.group(3)) == int(d.group(1)):
            return "Borçlarınız için son ödeme tarihiniz bugündür."
    return "Borçlarınız için son ödeme tarihiniz " + due + "'dir."


def build_debt_speech(data, last4, name, now_raw=""):
    overall = str(data.get("statementPaymentStatus") or "").upper()
    intro = _card_intro(name, last4)
    tl = data.get("debtTL") if isinstance(data.get("debtTL"), dict) else {}
    usd = data.get("debtUSD") if isinstance(data.get("debtUSD"), dict) else {}
    eur = data.get("debtEUR") if isinstance(data.get("debtEUR"), dict) else {}
    gbp = data.get("debtGBP") if isinstance(data.get("debtGBP"), dict) else {}
    tl_has, usd_has, eur_has, gbp_has = _has(tl), _has(usd), _has(eur), _has(gbp)
    if overall == "NO_DEBT" or (overall != "HAS_DEBT" and not (tl_has or usd_has or eur_has or gbp_has)):
        cut = speak_date(data.get("statementDate"))
        due = speak_date(data.get("lastPayDate") or data.get("nextLastPayDate"))
        total = money(tl.get("totalDebt") if tl else 0)
        line = (
            intro + " kartınızın " + cut + " tarihli ekstresinden borcunuz bulunmamaktadır. "
            + "Gelecek dönemler dahil toplam borcunuz " + total + " Türk Lirası'dır. "
            + "Son ödeme tarihiniz " + due + " olacaktır."
        )
        return "none", line
    parts = []
    cut = speak_date(data.get("statementDate"))
    first = True
    if tl_has:
        kind = _bucket(tl)
        head = intro + " kartınıza ait " + cut + " tarihinde kesilen ekstre " if first and cut else ""
        first = False
        if kind == "min_or_more":
            parts.append(
                head + "Türk Lirası borcunuz " + money(tl.get("statementDebt"))
                + " Türk Lirası, kalan toplam borcunuz " + money(tl.get("remainingDebt"))
                + " Türk Lirası'dır. Minimum ödemenizi yaptığınız için teşekkür ederiz."
            )
        elif kind == "below":
            parts.append(
                head + "Türk Lirası borcunuz " + money(tl.get("statementDebt"))
                + " Türk Lirası, ödenmesi gereken minimum tutar " + money(tl.get("minPayment"))
                + " Türk Lirası, kalan toplam borcunuz " + money(tl.get("remainingDebt"))
                + " Türk Lirası, kalan minimum ödeme tutarınız " + money(tl.get("remainingMinPayment"))
                + " Türk Lirası'dır."
            )
        else:
            parts.append(
                head + "Türk Lirası borcunuz " + money(tl.get("statementDebt"))
                + " Türk Lirası, ödenmesi gereken minimum tutar " + money(tl.get("minPayment"))
                + " Türk Lirası'dır."
            )
    for unit, block, flag in (("Dolar", usd, usd_has), ("Euro", eur, eur_has), ("İngiliz Sterlini", gbp, gbp_has)):
        if not flag:
            continue
        line = _fx_line(block, unit)
        if first and cut:
            line = intro + " kartınıza ait " + cut + " tarihinde kesilen ekstre " + line
            first = False
        parts.append(line)
    empty = []
    if tl and not tl_has:
        empty.append("tl")
    if usd and not usd_has:
        empty.append("usd")
    if eur and not eur_has:
        empty.append("eur")
    if gbp and not gbp_has:
        empty.append("gbp")
    if empty and (tl_has or usd_has or eur_has or gbp_has):
        parts.append(_empty_debt_sentence(empty))
    flag = str(data.get("automaticPaymentFlag") or "").upper()
    if flag == "Y" and _num(data.get("remainAutomaticPaymentAmount")) > 0:
        parts.append(
            "Otomatik ödeme talimatınızdan kalan tutar "
            + money(data.get("remainAutomaticPaymentAmount")) + " Türk Lirası'dır."
        )
    due_line = _due_sentence(data.get("lastPayDate"), now_raw)
    if due_line:
        parts.append(due_line)
    return "debt", " ".join(parts)


def apply_debt(vars):
    try:
        status, data, result = parse_body(vars, "load_balance")
        if status >= 400 or status == 0 or not isinstance(data, dict) or not data:
            vars["route"] = "error"
            message = str(result.get("messageText") or "")
            vars["service_error_line"] = (message + " " + SERVICE_ERROR).strip() if message else SERVICE_ERROR
            return
        last4 = str(vars.get("selected_last4") or data.get("cardNumberLast4") or "")
        name = str(vars.get("selected_name") or "").strip()
        if not name and str(data.get("productName") or "").strip():
            name = str(data.get("productName") or "").strip()
        path, speech = build_debt_speech(data, last4, name, vars.get("now"))
        if re.search(r"\d{5,}", speech):
            vars["route"] = "error"
            vars["service_error_line"] = SERVICE_ERROR
            return
        vars["statement_date"] = str(data.get("statementDate") or "")
        vars["currency_type"] = "TL"
        vars["debt_path"] = path
        vars["debt_body"] = speech
        count = int(float(vars.get("card_count") or 0))
        if path == "none":
            if count > 1:
                speech = speech + " " + ANOTHER
                vars["question_line"] = ANOTHER
            else:
                vars["question_line"] = HANDOFF
            vars["debt_speech"] = speech
            vars["replay_line"] = speech
            vars["resume_at"] = "say_nodebt"
            vars["route"] = "none"
            vars["miss_count"] = "0"
            return
        if count > 1:
            speech = speech + " " + ANOTHER
            vars["debt_speech"] = speech
            vars["replay_line"] = speech
            vars["question_line"] = ANOTHER
            vars["resume_at"] = "say_debt"
            vars["route"] = "ask_another"
        else:
            vars["debt_speech"] = speech
            vars["replay_line"] = speech
            vars["question_line"] = speech
            vars["resume_at"] = "say_debt"
            vars["route"] = "need_email"
        vars["miss_count"] = "0"
    except Exception:
        vars["route"] = "error"
        vars["service_error_line"] = SERVICE_ERROR


def apply_email(vars):
    try:
        status, data, result = parse_body(vars, "load_email")
        if status >= 500 or status == 0:
            vars["route"] = "error"
            vars["service_error_line"] = SERVICE_ERROR
            return
        address = ""
        comm = ""
        if isinstance(data, dict):
            address = str(data.get("ttsEmail") or data.get("ibanEmail") or "").strip()
            comm = str(data.get("communicationType") or "").strip().lower()
        has_email = bool(address) and comm not in ("phone", "p", "tel", "telephone")
        if status == 404 or not has_email:
            vars["email_address"] = ""
            vars["has_email"] = "0"
            if str(vars.get("email_mode") or "") == "offer":
                body = str(vars.get("debt_body") or "").strip()
                offer = OFFER_WEEK
                speech = (body + " " + offer).strip()
                vars["debt_speech"] = speech
                vars["offer_line"] = offer
                vars["question_line"] = offer
                vars["replay_line"] = speech
                vars["resume_at"] = "say_debt"
                vars["route"] = "offer"
                vars["miss_count"] = "0"
                return
            vars["route"] = "no_email"
            vars["handoff_line"] = NO_EMAIL + " " + HANDOFF
            return
        vars["email_address"] = address
        vars["has_email"] = "1"
        if str(vars.get("email_mode") or "") == "offer":
            body = str(vars.get("debt_body") or "").strip()
            offer = OFFER_BOTH
            speech = (body + " " + offer).strip()
            vars["debt_speech"] = speech
            vars["offer_line"] = offer
            vars["question_line"] = offer
            vars["replay_line"] = speech
            vars["resume_at"] = "say_debt"
            vars["route"] = "offer"
            vars["miss_count"] = "0"
            return
        spoken = address
        if len(address) > 40 or address.count(".") > 3:
            local = address.split("@")[0] if "@" in address else address
            domain = address.split("@")[-1] if "@" in address else ""
            if "gmail" in domain.lower():
                spoken = (local[:1] or "e") + " ile başlayan Gmail adresiniz"
            else:
                spoken = "kayıtlı e-posta adresiniz"
        line = "Ekstrenizi " + spoken + " adresine göndermemi ister misiniz?"
        vars["question_line"] = line
        vars["replay_line"] = line
        vars["resume_at"] = "ask_send"
        vars["miss_count"] = "0"
        vars["route"] = "ask"
    except Exception:
        vars["route"] = "error"
        vars["service_error_line"] = SERVICE_ERROR

def apply_send(vars):
    try:
        status, data, result = parse_body(vars, "send_pdf")
        exit_state = str((data or {}).get("exitState") or "").upper() if isinstance(data, dict) else ""
        if status == 200 and exit_state == "DONE":
            vars["route"] = "sent"
            return
        message = str(result.get("messageText") or "")
        vars["route"] = "error"
        vars["service_error_line"] = (message + " " + SERVICE_ERROR).strip() if message else SERVICE_ERROR
    except Exception:
        vars["route"] = "error"
        vars["service_error_line"] = SERVICE_ERROR


def _week_ordinal(raw):
    text = str(raw or "").strip()
    if text in ORDINAL:
        return text, ORDINAL[text]
    try:
        n = str(int(float(text)))
    except Exception:
        return "", ""
    if n in ORDINAL:
        return n, ORDINAL[n]
    return "", ""


def _related_name(data):
    name = str(data.get("relatedCardProductName") or "").strip()
    if name:
        return name
    last4 = _last4_of(data.get("relatedCardNumber"), "")
    if last4:
        return "Sonu " + last4 + " ile biten kart"
    return ""


def _card_head(last4, name, suffix):
    if name:
        return "Son dört hanesi " + last4 + " olan " + name + " kartınız" + suffix
    return "Sonu " + last4 + " ile biten kartınız" + suffix



def build_week_speech(data, last4, name):
    week, ordinal = _week_ordinal(data.get("statementWeek"))
    relation = str(data.get("relationType") or "").strip().upper()
    related = _related_name(data)
    cut = speak_date(data.get("nextStatementDate"))
    due = speak_date(data.get("nextLastPayDate"))
    card = (name + " kartınız") if name else ("Sonu " + last4 + " ile biten kartınız")
    if relation == "M" and related:
        line = card + " ile buna bağlı " + related + " kartınızın hesap kesim tarihi her ayın " + week + ". haftası olarak tanımlı."
    elif relation == "C" and related:
        line = card + " ile bağlı olduğu " + related + " kartınızın hesap kesim tarihi her ayın " + week + ". haftası olarak tanımlı."
    else:
        line = card + "ın hesap kesim tarihi her ayın " + week + ". haftası olarak tanımlı."
    line = (
        line + " Bir sonraki hesap kesim tarihiniz " + cut + ", son ödeme tarihiniz " + due
        + ". Hesap kesim tarihinizi değiştirmek ister misiniz?"
    )
    others = [item for item in ("1", "2", "3", "4") if item != week]
    return week, relation, line, others

def apply_week(vars):
    try:
        status, data, result = parse_body(vars, "load_week")
        if status >= 400 or status == 0 or not isinstance(data, dict) or not data.get("statementWeek"):
            vars["route"] = "error"
            vars["service_error_line"] = WEEK_SERVICE_ERROR
            return
        last4 = str(vars.get("selected_last4") or data.get("cardNumberLast4") or "")
        name = str(vars.get("selected_name") or data.get("productName") or "").strip()
        week, relation, speech, others = build_week_speech(data, last4, name)
        if re.search(r"\d{5,}", speech):
            vars["route"] = "error"
            vars["service_error_line"] = WEEK_SERVICE_ERROR
            return
        vars["current_week"] = week
        vars["relation_type"] = relation
        vars["related_name"] = _related_name(data)
        vars["week_speech"] = speech
        vars["replay_line"] = speech
        vars["question_line"] = "Bunu değiştirmek ister misiniz?"
        dots = [n + "." for n in others]
        if len(dots) == 1:
            listed = dots[0]
        elif len(dots) == 2:
            listed = dots[0] + " ya da " + dots[1]
        else:
            listed = ", ".join(dots[:-1]) + " ya da " + dots[-1]
        vars["week_choices"] = listed
        vars["other_weeks"] = " ".join(others)
        vars["resume_at"] = "say_week"
        vars["miss_count"] = "0"
        vars["route"] = "ask"
    except Exception:
        vars["route"] = "error"
        vars["service_error_line"] = WEEK_SERVICE_ERROR


def parse_week(text, current):
    t = tr_lower(text)
    for word, number in (("dördüncü", "4"), ("dorduncu", "4"), ("üçüncü", "3"), ("ucuncu", "3"), ("ikinci", "2"), ("birinci", "1")):
        if word in t:
            return number
    digits = digits_of(t)
    if digits in ("1", "2", "3", "4"):
        return digits
    return ""


def apply_week_pick(vars):
    try:
        if gave_up(vars.get("week_said")):
            vars["handoff_line"] = "Sizi ana menüye aktarıyorum."
            vars["route"] = "cancel"
            return
        chosen = parse_week(vars.get("week_said"), vars.get("current_week"))
        current = str(vars.get("current_week") or "")
        allowed = set(str(vars.get("other_weeks") or "").split())
        if not chosen or chosen == current or (allowed and chosen not in allowed):
            _bump(vars)
            vars["resume_at"] = "ask_week"
            vars["question_line"] = "Hesap kesim tarihinizi ayın " + str(vars.get("week_choices") or "") + " haftasına alabilirim. Hangisini tercih edersiniz?"
            return
        vars["chosen_week"] = chosen
        vars["chosen_ordinal"] = ORDINAL[chosen]
        vars["miss_count"] = "0"
        vars["route"] = "decide"
    except Exception:
        vars["route"] = "error"
        vars["service_error_line"] = SERVICE_ERROR



def build_preview(vars, data):
    last4 = str(vars.get("selected_last4") or "")
    name = str(vars.get("selected_name") or "").strip()
    related = str(vars.get("related_name") or "").strip()
    relation = str(vars.get("relation_type") or "")
    ordinal_num = str(vars.get("chosen_week") or "")
    cut = speak_date(data.get("statementDate"))
    due = speak_date(data.get("statementDueDate"))
    vars["cut_after"] = cut
    vars["due_after"] = due
    warn = ""
    if relation == "M" and related:
        warn = "Bu değişiklik kartınıza bağlı " + related + " kartınızı da etkileyecek. "
    elif relation == "C" and related:
        warn = "Bu değişiklik kartınızın bağlı olduğu " + related + " kartınızı da etkileyecek. "
    line = (
        warn + "Yeni hesap kesim haftanız her ayın " + ordinal_num + ". haftası olacak. "
        + "Yeni hesap kesim tarihiniz " + cut + ", son ödeme tarihiniz " + due
        + " olacak. Onaylıyor musunuz?"
    )
    return line

def apply_decide(vars):
    try:
        status, data, result = parse_body(vars, "load_decide")
        code = str((data or {}).get("responseCode") if isinstance(data, dict) else "")
        if status >= 400 or status == 0 or not isinstance(data, dict) or code not in ("0", "0.0"):
            vars["route"] = "error"
            vars["service_error_line"] = WEEK_SERVICE_ERROR
            return
        speech = build_preview(vars, data)
        if re.search(r"\d{5,}", speech):
            vars["route"] = "error"
            vars["service_error_line"] = WEEK_SERVICE_ERROR
            return
        vars["preview_speech"] = speech
        vars["replay_line"] = speech
        vars["question_line"] = "Bu değişikliği onaylar mısınız?"
        vars["resume_at"] = "confirm_week"
        vars["miss_count"] = "0"
        vars["route"] = "confirm"
    except Exception:
        vars["route"] = "error"
        vars["service_error_line"] = WEEK_SERVICE_ERROR



def apply_change(vars):
    try:
        status, data, result = parse_body(vars, "load_change")
        exit_state = str((data or {}).get("exitState") or "") if isinstance(data, dict) else ""
        message = str((data or {}).get("responseMessage") or "") if isinstance(data, dict) else ""
        code = str((data or {}).get("responseCode") or "") if isinstance(data, dict) else ""
        blob = (exit_state + " " + message + " " + str(result.get("messageText") or "")).lower()
        ok = status == 200 and (
            exit_state.upper() in ("OK", "DONE", "SUCCESS")
            or message in ("00", "Success", "SUCCESS")
            or code in ("0", "0.0", "1", "1.0")
        )
        if ok:
            last4 = str(vars.get("selected_last4") or "")
            name = str(vars.get("selected_name") or "").strip()
            week = str(vars.get("chosen_week") or "")
            cut = str(vars.get("cut_after") or "")
            due = str(vars.get("due_after") or "")
            head = ("Sonu " + last4 + " ile biten " + name) if name else ("Sonu " + last4 + " ile biten")
            vars["week_done_line"] = (
                head + " kartınızın hesap kesim haftası her ayın " + week
                + ". haftası olarak değiştirildi. Yeni hesap kesim tarihiniz " + cut
                + ", son ödeme tarihiniz " + due + ". " + HANDOFF
            )
            vars["route"] = "done"
            return
        if "yıl" in blob or "yil" in blob or "yearly" in blob:
            vars["week_fail_line"] = WEEK_YEAR
        elif "ayda" in blob or "aylık" in blob or "monthly" in blob:
            vars["week_fail_line"] = WEEK_MONTH
        else:
            vars["week_fail_line"] = WEEK_FAIL
        vars["route"] = "fail"
    except Exception:
        vars["week_fail_line"] = WEEK_FAIL
        vars["route"] = "fail"

def apply_which(vars):
    try:
        line = OFFER_BOTH
        vars["question_line"] = line
        vars["replay_line"] = line
        vars["resume_at"] = "offer_which"
        vars["miss_count"] = "0"
        vars["route"] = "ask"
    except Exception:
        vars["route"] = "error"
        vars["service_error_line"] = SERVICE_ERROR


def apply_silence(vars):
    try:
        n = int(float(vars.get("silence_count") or 0)) + 1
        vars["silence_count"] = str(n)
        if n >= 3:
            vars["handoff_line"] = "Sesinizi alamadığım için görüşmeyi sonlandırıyorum."
            vars["route"] = "end"
            return
        vars["route"] = "again"
    except Exception:
        vars["route"] = "end"
        vars["handoff_line"] = "Sesinizi alamadığım için görüşmeyi sonlandırıyorum."


def apply_anger(vars):
    try:
        n = int(float(vars.get("anger_count") or 0)) + 1
        vars["anger_count"] = str(n)
        lines = {
            1: "Sizi dinliyorum.",
            2: "Size yardımcı olmak istiyorum.",
            3: "Sakin bir şekilde devam edebiliriz.",
        }
        if n >= 4:
            vars["route"] = "end"
            return
        vars["calm_line"] = lines.get(n, "Sizi dinliyorum.")
        vars["route"] = "calm"
    except Exception:
        vars["route"] = "end"


def apply_another(vars):
    try:
        count = int(float(vars.get("card_count") or 0))
        if count <= 1:
            vars["handoff_line"] = "Başka kartınız görünmüyor. " + HANDOFF
            vars["route"] = "only"
            return
        vars["question_line"] = str(vars.get("cards_menu") or "Hangi kartınız için devam edeyim?")
        vars["replay_line"] = vars["question_line"]
        vars["resume_at"] = "choose_card"
        vars["miss_count"] = "0"
        vars["route"] = "choose"
    except Exception:
        vars["route"] = "only"
        vars["handoff_line"] = HANDOFF
