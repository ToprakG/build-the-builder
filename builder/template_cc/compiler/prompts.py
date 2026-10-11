"""Model-facing texts of the template. Generic wording only: every value comes from the policy or the tool API.

Each function returns the text of one zone. Section titles are fixed; graph.py registers the zones.
"""

from __future__ import annotations

import re

from builder.template_cc.compiler import i18n as I18N

LANGUAGES = {"en": "English", "de": "German", "tr": "Turkish", "fr": "French", "es": "Spanish", "nl": "Dutch",
             "it": "Italian", "pt": "Portuguese", "pl": "Polish", "sv": "Swedish", "da": "Danish", "no": "Norwegian",
             "fi": "Finnish"}

BUDGET = {"rules": 1200, "examples": 600, "global_rules": 1500, "tool": 600, "hook": 300, "intent": 300, "extract": 300}


def line(policy: dict, key: str) -> str:
    return spoken((policy.get("lines") or {}).get(key, {}).get("text", ""), policy)


PLACEHOLDER = re.compile(r"\{\{?\s*([A-Za-z_]\w*)\s*\}?\}")
TRAILING = re.compile(r"(?:[\s,;:\-]+|\b(?:on|in|for|of|about|your|the|a|an|and|with|is|it|to|at)\b)+$", re.I)


def spoken(text: str, policy: dict) -> str:
    """{field} placeholders of call variables -> {{field}}. A placeholder that is not a call variable (an account
    value the extractor wrote into a line) cannot be filled: the line is cut before it, dangling words are dropped."""
    cv = set(policy.get("call_variables") or [])
    text = str(text or "")
    bad = next((m for m in PLACEHOLDER.finditer(text) if m.group(1) not in cv), None)
    if bad is not None:
        text = TRAILING.sub("", text[: bad.start()]).strip()
        if text and text[-1] not in ".?!":
            text += "."
    return PLACEHOLDER.sub(lambda m: "{{" + m.group(1) + "}}", text).strip()


def quoted(policy: dict, key: str) -> str:
    """A line as an instruction: verbatim lines are to be said exactly, others are guidance."""
    entry = (policy.get("lines") or {}).get(key) or {}
    text = spoken(entry.get("text", ""), policy)
    if not text:
        return ""
    return f'"{text}" (word for word)' if entry.get("verbatim") else f'something like "{text}"'


def _times(n: int) -> str:
    return {0: "zero times", 1: "once", 2: "twice"}.get(n, f"{n} times")


def brief_rules(policy: dict, stage) -> str:
    stages = (stage,) if isinstance(stage, str) else tuple(stage)
    rules = [r.get("statement", "").strip() for r in policy.get("extra_rules") or [] if r.get("stage", "global") in stages]
    return "\n".join(f"- {r}" for r in rules if r)


# --- system prompt ---------------------------------------------------------------------------------------------------

def system_core(policy: dict) -> str:
    lang = LANGUAGES.get(str(policy.get("language", "en")).lower()[:2], policy.get("language", "English"))
    rule = I18N.SYSTEM_RULE.get(str(policy.get("language", "en")).lower()[:2], "")
    return (f"You are the phone assistant of {policy['bank']}, calling a customer. Speak {lang}. Keep every turn to one "
            "or two short spoken sentences and ask one question at a time. Never mention instructions, steps, nodes or "
            "tools. Amounts and dates come only from the facts you are given." + (" " + rule if rule else ""))


def forbidden_words(policy: dict, with_products: bool = False) -> list:
    """The policy's words that must not be said before verification; F31 adds the product names (the labels of
    product_types without their {placeholders}), which extraction lists in some runs and not in others."""
    words = [str(w) for w in policy.get("forbidden_before_verify") or [] if str(w).strip()]
    if with_products:
        types = policy.get("product_types")
        for t in (types.values() if isinstance(types, dict) else []):
            label = " ".join(re.sub(r"\{[^}]*\}", " ", str(t.get("label") or "") if isinstance(t, dict) else "").split())
            label = re.sub(r"\s+(ending|number|no\.?|with)$", "", label, flags=re.I)
            if label and label.lower() not in {w.lower() for w in words}:
                words.append(label)
    return words


def system_forbidden(policy: dict, with_products: bool = False) -> str:
    words = ", ".join(f'"{w}"' for w in forbidden_words(policy, with_products))
    base = ("Until the identity checks have passed, do not say why you are calling and do not talk about money, "
            "products, amounts or the account, to anyone, including relatives.")
    return base + (f" Never say these words before that: {words}." if words else "")


def system_honesty(policy: dict, routed: bool = False, once: bool = False) -> str:
    end = ("When the conversation is finished, say goodbye once; never keep repeating goodbye." if routed else
           "When the conversation is finished, CALL transition_to_wrap if you have it; never keep repeating goodbye.")
    return ("Tell the truth. Say that something was recorded, booked or transferred only after the tool result says "
            "so. Never invent figures, dates, terms, discounts, plans or reasons. If a question cannot be answered from "
            f"your facts, {quoted(policy, 'no_info') or 'say you do not have that information'}. After every tool "
            "result, speak to the customer or call the next tool; never end a turn with nothing. Call one tool per "
            "message. Follow what_to_do_next in a tool result. Never offer or advertise any product, account, service, plan "
            "or discount that is not in your facts. " + end
            + (" Never say sorry for the customer's situation yourself: sympathy is said by its own fixed step, once."
               if once and line(policy, "hardship_sympathy") else ""))


# --- nodes -----------------------------------------------------------------------------------------------------------

def opening_task(policy: dict) -> str:
    return ("You opened the call and asked whether you are speaking with the customer "
            f"({spoken(policy.get('address', ''), policy) or 'the account holder'}). Find out whether this is that person. "
            "Only a clear confirmation counts, for example a yes or the person saying their own name; a greeting, "
            "'go ahead' or a question back does not. Never mention the reason for the call. "
            "No tool call is allowed in this turn.")


def opening_ask2_task(policy: dict) -> str:
    return ("The person has not clearly confirmed who they are yet. If they asked a question, answer it in one short "
            f"sentence first: who you are ({quoted(policy, 'ai')}); if they doubt the call ({quoted(policy, 'scam')}). "
            f"Never mention the reason for the call. Then ask once more: {quoted(policy, 'ask_again')}. "
            "No tool call is allowed in this turn.")


def third_party_task(policy: dict, api_cb_field_hint: str) -> str:
    n = int((policy.get("ask_limits") or {}).get("callback_reask", 1))
    return ("You are not speaking with the customer. Do not say why you are calling and give no account details, even "
            "if the person offers to pay or to answer questions for the customer. Never ask them security questions. "
            "Find out when the customer is available.\n"
            "{{cb_window_text}}\n"
            f"When the person names a day and a time inside these times, CALL schedule_callback ({api_cb_field_hint}), "
            "then thank them and say goodbye. If the time is outside these times, say when you can call back and ask "
            f"for another time (at most {_times(n)}). If that is also outside, or they cannot "
            "say, do not schedule anything: thank them and say goodbye.")


def identity_task(policy: dict) -> str:
    ident = policy["identity"]
    qs = "\n".join(f"{i}. {ident['prompts'].get(q, q)} (field: {q})" for i, q in enumerate(ident["questions"], 1))
    return ("You are speaking with the customer. Check identity first, with these questions in this order, one at a "
            f"time:\n{qs}\n"
            "After each answer CALL verify_identity with that question's field and the customer's answer as they said "
            "it. Follow what_to_do_next. Do not say whether an answer was right beyond what what_to_do_next says. "
            "Ask a question again only when what_to_do_next says so. Say nothing about the account or the reason for "
            "the call until the checks have passed.")


def identity_explain_task(policy: dict) -> str:
    return (f"The customer did not want to answer or asked what this is about. Explain once: {quoted(policy, 'id_reason')}. "
            "Never mention the reason for the call. Then ask the pending security question again. After the answer CALL "
            "verify_identity as before and follow what_to_do_next.")


def identity_task_f1(policy: dict, f2: bool) -> str:
    """F1: the system speaks the security questions itself (tts of id_say) and checks the answers in code."""
    ai = line(policy, "ai")
    ai_rule = (f" If the customer asked whether you are a robot, a computer or an AI and you have not answered yet, say "
               f"first: {ai}") if ai else ""
    return ("You are speaking with the customer. The system asks the security questions itself; the current line is: "
            "{{id_say}}\n"
            "You do not check answers yourself. If the customer says something that is not an answer, reply in one short "
            "sentence and then say that line again. Never say whether an answer was right. Say nothing about the account or "
            "the reason for the call until the checks have passed." + ai_rule)


def identity_explain_task_f1(policy: dict) -> str:
    return ("The customer did not want to answer or asked what this is about. The system has just explained and asked the "
            "question again; the line was: {{id_say}}\nIf the customer says something that is not an answer, reply in one short "
            "sentence without the reason for the call and say that line again.")


def id_intent_desc(f2: bool, f3: bool) -> str:
    """The id_intent extraction description for the active flags (it must name exactly the enum values in use)."""
    human = " wants_human = asks for a person, an agent or a human." if f3 else ""
    if f2:
        return ("answer = gives an answer or part of one, even after a question; asks_question = asks something instead "
                "of answering (why, which account, who is calling); refuses = says they will not answer or do not want to "
                "talk;" + human + " other = anything else.")
    return ("answer = gives an answer or part of one; declines = refuses to answer a security question; asks_purpose = "
            "asks what the call is about instead of answering;" + human + " other = anything else.")


def negotiate_task(policy: dict, multi: bool, typed: bool = False, routed: bool = False) -> str:
    parts = [
        "You stated the debt and asked the first question. Get a payment promise where you can, within these rules:",
        "- Start by asking for the whole past-due amount. Never ask how much the customer can pay: you propose every amount.",
        "- A date or an amount the customer proposes is never a refusal: check it against the facts.",
        "- A promise date must be today or later, a business day, and not after the product's latest date (see the "
        "date table). A weekend day or holiday that is not after the latest date: offer the next business day. A date "
        "after the latest date: say the latest date you can record and offer it.",
        ("- A vague time (next week, end of the month, payday): the system asks for the exact day and, if there is still "
         "none, offers a date; say the line you are given.") if typed else
        f"- A vague time (next week, end of the month, payday): ask for the exact day "
        f"{_times(int((policy.get('ask_limits') or {}).get('vague_date', 1)))}; if there is still none, propose the "
        "latest date.",
        "- Amounts from the minimum amount up to the full amount, both included, are accepted. If the full amount is "
        "too much, offer the minimum amount once and say the rest stays overdue. Below the minimum amount: say it "
        "cannot be recorded and offer the minimum amount once.",
        "- Each flat refusal or non-answer counts as one persuasion attempt. Use the attempt proposals from the facts, in "
        "order. Refusing the latest date or the minimum amount is a refusal. When the attempts of a product are used up "
        + ("and the customer still refuses, stop: never repeat an offer the customer refused." if routed else
           "and the customer still refuses, stop: never repeat an offer the customer refused, and CALL the refusal "
           "transition for that product."),
        ("- The system checks every amount and date the customer names and reads it back by itself. Never read back, "
         "record or confirm anything yourself; when a line tells you what to say, say it. You never take payments or card "
         "details: you only agree an amount and a date.") if typed else
        f"- Before recording, read back the product, the amount and the date and ask: {quoted(policy, 'confirm_question')}. "
        "CALL create_promise only after a clear yes. If the customer changes the date or the amount, check it again "
        "and read it back again.",
        "- One promise per product. A recorded promise cannot be changed or cancelled on this call: "
        f"{quoted(policy, 'change_promise')}.",
        "- Never offer a discount, a waiver, a payment plan or any term that is not in your facts.",
        "- Offering the latest date (because the customer's date was too late) is not a persuasion attempt and does "
        "not change the amount: the amount stays the full past-due amount, unless the customer already said the full "
        "amount is too much. Only flat refusals move to the next attempt proposal.",
        ("- Never say that anything is recorded or saved unless a line you are given says so.") if typed else
        "- Until create_promise succeeds, never say that anything is recorded or saved. After it succeeds, follow "
        "what_to_do_next.",
    ]
    if multi:
        parts += [
            "Several products:",
            "- If the customer cannot pay everything today, go through the products one by one, in the order of the "
            "facts. Ask about paying today only once, not per product. Persuasion attempts count per product and start "
            "again at attempt 1 for each product.",
            "- If the customer names one date for everything and it is after one product's latest date, that product "
            "gets its own latest date: say so in the read-back. The other products keep the named date.",
            ("- Each product is checked and confirmed on its own.") if typed else
            "- Read all agreed promises back together and ask once for confirmation. Then CALL create_promise once per "
            "product, one call per message.",
        ]
    return "\n".join(parts)


def negotiate_tool_hint(policy: dict, unit_cents: bool) -> str:
    unit = "in currency units exactly as written in the facts (the system converts it)" if unit_cents else \
        "in currency units exactly as written in the facts"
    return f"CALL create_promise with the product_id from the facts, the amount {unit}, and the date as YYYY-MM-DD from the date table."


def hardship_task(policy: dict, has_support: bool, said: str = "") -> str:
    if said:  # F8: the sympathy line is spoken by the step itself, word for word
        ask = " Do not offer a transfer." if not has_support else ""  # with support, the fixed line already makes the offer
        return ("You have just said your sympathy line. Do not say it again. Make no persuasion attempt." + ask +
                " If the customer still names a date or an amount themselves, say you can record it. No tool call is "
                "allowed in this turn.")
    offer = ("Offer to connect them with the support team, and ask whether they want that." if has_support
             else "Do not offer a transfer.")
    return (f"The customer mentioned a hardship (illness, a death in the family, job loss). Say one short sentence of "
            f"sympathy ({quoted(policy, 'hardship_sympathy')}). Make no persuasion attempt. {offer} If the customer "
            "still names a date or an amount themselves, say you can record it. No tool call is allowed in this turn.")


def human_task(policy: dict, asked: bool = False) -> str:
    if asked:  # F20: the offer is made once, when this step starts; later turns never repeat it
        return ("The customer asked to speak to a person. In your first turn here ask once whether you can help them "
                "yourself first. Never ask it a second time: if the customer says something else, answer it in one short "
                "sentence and wait. If they insist, say you will connect them. No tool call is allowed in this turn.")
    return ("The customer asked to speak to a person. Ask once whether you can help them yourself first. If they "
            "insist, say you will connect them. No tool call is allowed in this turn.")


NO_DISCOUNT = "I am not able to reduce the amount."


# policy["intents"] name -> the value of the `side` extraction it defines
INTENT_SIDE = {"hardship": "hardship", "dispute": "dispute", "paid_already": "paid_already", "restructuring": "restructuring",
               "discount": "discount", "human": "human", "stop_talking": "stop_talking"}


def intents_text(policy: dict, values) -> str:
    """F16: the brief's own definitions, examples and near misses (policy["intents"]) for the side values in use."""
    out = []
    for name, val in INTENT_SIDE.items():
        it = (policy.get("intents") or {}).get(name)
        if val not in values or not isinstance(it, dict):
            continue
        parts = []
        d = str(it.get("definition") or "").strip()
        if d:
            parts.append(f"{val} (the brief's definition): {d.rstrip('.')}.")
        ex = [str(x).strip() for x in (it.get("examples") or []) if str(x).strip()][:3]
        if ex:
            parts.append("Examples: " + "; ".join(f'"{x}"' for x in ex) + ".")
        nm = [str(x).strip() for x in (it.get("near_misses") or []) if str(x).strip()][:2]
        if nm:
            parts.append(f"NOT {val}: " + "; ".join(f'"{x}"' for x in nm) + ".")
        if parts:
            out.append(" ".join(parts))
    return " ".join(out)


def side_desc(f4: bool = True, refused: int = 0, intents: str = "", dispute_first: bool = False) -> str:
    """The side description with the discount value (F4: asking to knock something off is not restructuring) and, for the
    router (F7), the refused_N values that replace the refusal tool transitions."""
    d = EXTRACT["side"]
    key = "human = asks to speak"
    if f4:
        d = d.replace(key, "discount = asks to reduce, waive or knock something off the amount (this is NOT restructuring; if the message also "
                      "mentions illness, a death in the family or job loss, it is hardship); " + key, 1)
    if refused:
        d = d.replace("stop_talking = does not want to talk;", f"refused_1 .. refused_{refused} = the product at that position in the facts has "
                      "its persuasion attempts used up and the customer still refuses, or refused its last offer (a first flat "
                      "refusal is none); stop_talking = does not want to talk;", 1)
    if dispute_first:  # a claim that the debt is not theirs / closed / paid in the past wins over a refusal to talk or pay
        d += (" When the message says the debt or product is not theirs, or was closed or paid off in the past, the value is "
              "dispute even if the customer also refuses to talk or to pay.")
    return d + (" " + intents if intents else "")


def resume_entry(policy: dict, f4: bool = False, routed: bool = False, scoped: bool = False) -> str:
    extra = (" If this line is not empty and you have not said it yet in this conversation, say it first, word for word, "
             "once: {{say_line}}") if f4 else ""
    scope = (" Your facts list exactly {{product_count}} product(s): never mention a product, card or account that is not "
             "in that list.") if scoped else ""
    return ("Continue the conversation from where it is, in one or two short sentences: briefly acknowledge what the "
            "customer just said and go on with the next open product from your facts." + scope + " If the customer said they already "
            f"paid, {quoted(policy, 'paid_reply')}. Never offer anything that is not in your facts: no other product, "
            "account, service, plan or discount. If nothing is left to discuss, "
            + ("say goodbye." if routed else "CALL transition_to_wrap.") + extra)


FINISH = ("The conversation is finished: everything is settled or cannot go on (for example a technical problem), and "
          "the customer is saying goodbye or has nothing more to discuss. Never use it while an agreed promise is "
          "still waiting to be recorded.")

GOODBYE_INTENT = ("The person says goodbye or thanks you to end the call, after you have already said goodbye or said "
                  "that the call is over.")


# --- extraction descriptions -----------------------------------------------------------------------------------------

VAGUE_SIGNAL = (" A refusal for today that gives any time hint without an exact day (later, once something happens, when money arrives) "
                "is vague, not none; none is only a refusal with no hint of a later payment.")
AMOUNT_TAIL = (" Copy it with any self-correction exactly as said, also when the message stops in the middle of it "
               "(for example 'twelve hundred, sorry, I'): never drop the words after the number.")

EXTRACT = {
    "who1": "Who answered: customer = the person confirms they are the customer; someone_else = another person "
            "(a relative, a colleague); not_their_number = the number is not the customer's; customer_died = the customer has "
            "died; unclear = no clear confirmation yet (a greeting, a question, 'go ahead').",
    "tp_state": "gave_up = the person gave a second time outside the allowed times, or cannot or will not say when the "
                "customer can be reached; not_their_number = not the customer's number; customer_died = the customer has died; "
                "none = anything else.",
    "id_refusal": "declines = the customer refuses to answer a security question; asks_purpose = asks what the call is "
                  "about instead of answering; answering = anything else.",
    "turn_intent": "asks_robot = asks whether you are a robot, a computer or an AI; asks_scam = asks whether the call is a scam or "
                   "doubts that it is really from the bank; wants_human = asks to speak to a human, a person, an agent or a "
                   "manager; goodbye = says goodbye or thanks you to end the call, after you have said goodbye or said the call "
                   "is over; finished = everything is settled or cannot go on and the customer has nothing more to discuss "
                   "(never while an agreed promise is still waiting to be recorded); none = anything else.",
    "ai_asked": "yes = the customer asks whether they are talking to a robot, a computer or an AI; no = anything else.",
    "scam_asked": "yes = the customer asks whether this call is a scam or fraud, or doubts that it is really from the bank; no = anything else.",
    "who_wants_human": "yes = the person asks for a human, a person or an agent (asking whether you are a robot or an AI "
                       "is NOT that); no = anything else.",
    "offer_intent": "proposes = the customer names an amount or a date for paying; accepts_offer = the customer agrees to your "
                    "last offer; vague = the customer says they will pay but names no exact day (soon, later, next week, payday); "
                    "none = anything else (a refusal, a question).",
    "offer_amount_text": "The payment amount the customer names in their last message, or the amount of your last offer when "
                         "they just agree to it, copied as said (words or digits). Empty if there is none.",
    "offer_date_text": "The payment date the customer names in their last message, or the date of your last offer when they "
                       "just agree to it, copied as said. Empty if there is none.",
    "rb_answer": "yes = a clear yes to what you just read back; no = no, they do not want that; change = they name a "
                 "different amount or date; other = anything else.",
    "id_answer_text": "The customer's answer to the security question just asked, copied exactly as said (words or digits). "
                      "ALWAYS fill it when the customer gives an answer, even a partial one. If they gave it over several "
                      "messages, join the parts. Empty only if there is no answer.",
    "side": "What the customer's last message is about: hardship = illness, a death in the family or job loss; "
            "dispute = the debt or product is not theirs, or was closed or paid off in the past; paid_already = they "
            "already paid this past-due amount; restructuring = explicitly asks for a new payment plan, a restructuring or lower instalments (saying the amount "
            "is too much or that they cannot pay is NOT restructuring: that is none); "
            "human = asks to speak to a person or an agent (asking whether you are a robot or an AI is NOT human: that is none); stop_talking = does not want to talk; none = anything else, including dates, "
            "amounts and refusals to pay.",
    "side_product": "Which product the customer's last message is about, by its position in the facts (1, 2, 3), or "
                    "all; empty when unclear.",
    "hs_choice": "accepts = wants to be connected to the support team; declines = does not want that; names_terms = "
                 "names a payment date or amount themselves; none = anything else.",
    "human_choice": "insists = still wants a person; continues = accepts your help; none = anything else.",
}
