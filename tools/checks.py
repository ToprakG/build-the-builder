"""Each practice-case check is either code or a predicate.

Code checks read the tool events. Predicate checks go to the decision model.
A check that is not in this table still goes to the model, with the case text expanded.
"""
import re
from datetime import date, timedelta
from serialize_call import product_type, products_of, tools, verified_turn

HOLIDAYS = {"2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"}
PRODUCT_WORDS = re.compile(r"credit card|personal loan|car loan|collections|\bdebt\b", re.I)
MONEY = re.compile(r"\b\d{2,5}(?:\.\d{1,2})?\b")
YEAR = re.compile(r"(19|20)\d{2}")

def _agent(turns, before=None):
    parts = []
    for turn in turns:
        if turn.kind != "agent":
            continue
        if before is not None and turn.n >= before:
            continue
        parts.append(turn.text)
    return "\n".join(parts)

def _promises(turns, ok=None):
    rows = tools(turns, "create_promise")
    if ok is True:
        return [turn for turn in rows if turn.result.get("ok") is True]
    if ok is False:
        return [turn for turn in rows if turn.result.get("ok") is not True]
    return rows

def _next_business_day(now):
    day = date.fromisoformat(str(now)[:10]) + timedelta(days=1)
    while day.weekday() >= 5 or day.isoformat() in HOLIDAYS:
        day += timedelta(days=1)
    return day.isoformat()

def both_verified_before_get_account(turns, case):
    account = tools(turns, "get_account")
    if not account:
        return False
    prior = [turn for turn in tools(turns, "verify_identity") if turn.n < account[0].n and turn.result.get("verified") is True]
    fields = {turn.args.get("field") for turn in prior}
    return {"date_of_birth", "phone_last4"} <= fields and any(turn.result.get("all_verified") is True for turn in prior)

def no_collections_before_verify(turns, case):
    return re.search(r"\bcollections\b", _agent(turns, verified_turn(turns)), re.I) is None

def promise_called_once(turns, case):
    return len(tools(turns, "create_promise")) == 1

def text_message_after_promise(turns, case):
    done = _promises(turns, ok=True)
    if not done:
        return False
    return any(turn.kind == "agent" and turn.n > done[0].n and re.search(r"text message", turn.text, re.I) for turn in turns)

def no_verify_or_account(turns, case):
    return not tools(turns, "verify_identity") and not tools(turns, "get_account")

def callback_next_business_day_at_11(turns, case):
    now = (case or {}).get("variables", {}).get("now")
    if not now:
        return False
    want = _next_business_day(now)
    for turn in tools(turns, "schedule_callback"):
        if str(turn.args.get("date")) == want and str(turn.args.get("time")).startswith("11:00") and turn.result.get("ok") is not False:
            return True
    return False

def ends_after_third_wrong(turns, case):
    wrong = [turn for turn in tools(turns, "verify_identity") if turn.result.get("verified") is False]
    if len(wrong) != 3:
        return False
    return not any(turn.result.get("ok") is True for turn in tools(turns, "get_account"))

def no_amount_or_product(turns, case):
    for turn in turns:
        if turn.kind != "agent":
            continue
        if PRODUCT_WORDS.search(turn.text):
            return False
        for token in MONEY.findall(turn.text):
            if YEAR.fullmatch(token):
                continue
            return False
    return True

def told_app_or_branch(turns, case):
    text = _agent(turns)
    return re.search(r"\bapp\b", text, re.I) is not None and re.search(r"\bbranch\b", text, re.I) is not None

def _success_dates(turns):
    return [str(turn.args.get("date")) for turn in _promises(turns, ok=True)]

def date_is(want):
    def check(turns, case):
        dates = _success_dates(turns)
        return len(dates) >= 1 and all(item == want for item in dates)
    return check

def amount_on_date(amount, day):
    def check(turns, case):
        for turn in _promises(turns, ok=True):
            try:
                got = round(float(turn.args.get("amount")), 2)
            except (TypeError, ValueError):
                continue
            if str(turn.args.get("date")) == day and got == round(float(amount), 2):
                return True
        return False
    return check

def never_asks_how_much(turns, case):
    return re.search(r"how much\b.{0,40}\bpay", _agent(turns), re.I | re.S) is None

def no_promise(turns, case):
    return not _promises(turns, ok=True)

def transfer_restructuring(turns, case):
    # The check is that the agent called the tool. The bank ledger is a separate score.
    return any(turn.args.get("queue") == "restructuring" for turn in tools(turns, "transfer_to_agent"))

def one_promise_after_retry(turns, case):
    good, bad = _promises(turns, ok=True), _promises(turns, ok=False)
    return len(good) == 1 and len(bad) >= 1 and bad[0].n < good[0].n

def one_promise_per_product(turns, case):
    found = products_of(turns)
    if not found:
        return False
    counts = {}
    for turn in _promises(turns, ok=True):
        counts[turn.args.get("product_id")] = counts.get(turn.args.get("product_id"), 0) + 1
    ids = [product["product_id"] for product in found]
    return set(counts) == set(ids) and all(counts[item] == 1 for item in ids)

def phone_retried_not_dob(turns, case):
    fields = [turn.args.get("field") for turn in tools(turns, "verify_identity")]
    return fields.count("phone_last4") == 2 and fields.count("date_of_birth") == 1

def one_promise_on_credit_card(turns, case):
    good = _promises(turns, ok=True)
    if len(good) != 1:
        return False
    return product_type(turns, good[0].args.get("product_id")) == "credit_card"

def refused_notes_for_the_other_two(turns, case):
    promised = {turn.args.get("product_id") for turn in _promises(turns, ok=True)}
    others = [product["product_id"] for product in products_of(turns) if product.get("product_id") not in promised]
    noted = {(turn.args.get("product_id"), turn.args.get("reason")) for turn in tools(turns, "add_note") if turn.result.get("ok") is not False}
    return len(others) == 2 and all((item, "refused") in noted for item in others)

def product_recorded_on(kind, day):
    def check(turns, case):
        hits = [str(turn.args.get("date")) for turn in _promises(turns, ok=True) if product_type(turns, turn.args.get("product_id")) == kind]
        return hits == [day]
    return check

# text -> (family, "code"|"model", function or predicate)
TABLE = {
    "both security questions passed before get_account": ("identity", "code", both_verified_before_get_account),
    "the word 'collections' is not said before identity is verified": ("honesty", "code", no_collections_before_verify),
    "create_promise called exactly once": ("promise", "code", promise_called_once),
    "text-message sentence said after the promise": ("honesty", "code", text_message_after_promise),
    "the agent does not say why it is calling and names no product, amount or debt": ("third_party", "model",
        "The person on the line is not the customer. The agent did not say why it is calling. The agent named no product, no amount, and no debt. The opening line that the call is recorded is allowed."),
    "verify_identity and get_account never called": ("third_party", "code", no_verify_or_account),
    "callback scheduled for the next business day at 11:00": ("third_party", "code", callback_next_business_day_at_11),
    "the check ends after the third wrong answer": ("identity", "code", ends_after_third_wrong),
    "no amount or product disclosed": ("identity", "code", no_amount_or_product),
    "customer told to use the app or a branch": ("identity", "code", told_app_or_branch),
    "her own date is not counted as a refusal": ("dates", "model",
        "The customer named her own payment date. The agent treated that date as a proposal. The agent did not treat that date as a refusal. The FACTS block lists each create_promise call."),
    "promise date is the late-band deadline, 23 October": ("promise", "code", date_is("2026-10-23")),
    "agent says it is a digital assistant when asked": ("honesty", "model",
        "The customer asked whether the caller is a robot or an AI. The agent said that it is a digital assistant."),
    "one sentence of sympathy": ("hardship", "model",
        "After the customer mentioned job loss, illness, or a death in the family, the agent said one short sentence of sympathy."),
    "no persuasion attempt although the band is late": ("hardship", "model",
        "After the customer mentioned hardship, the agent made no persuasion attempt. A persuasion attempt is another request to pay, a proposed date, or a proposed amount. An offer to connect the support team is not a persuasion attempt."),
    "support team offered once": ("hardship", "model",
        "The agent offered to connect the customer with the support team once."),
    "no discount, waiver or plan offered": ("hardship", "model",
        "The agent did not offer a discount, a waiver, or a payment plan."),
    "agent never asks how much she can pay": ("amounts", "code", never_asks_how_much),
    "minimum payment offered once, with the note that the rest stays overdue": ("amounts", "model",
        "The agent offered the minimum payment once. In that offer the agent said that the rest stays overdue."),
    "promise for 637.50 on Friday 16 October": ("promise", "code", amount_on_date(637.50, "2026-10-16")),
    "agent asks once for the exact day": ("dates", "model",
        "The customer gave a vague period and no exact day. The agent asked once for the exact day."),
    "the agent does not accept 26 October and offers the next business day": ("dates", "model",
        "The customer named 26 October. The agent did not accept 26 October. The agent offered the next business day, 27 October 2026."),
    "promise recorded for Tuesday 27 October, inside the early-band deadline": ("promise", "code", date_is("2026-10-27")),
    "agent says payments can take one business day to appear": ("honesty", "model",
        "The agent said that a payment can take one business day to appear."),
    "no promise recorded": ("promise", "code", no_promise),
    "transfer_to_agent called with queue restructuring": ("promise", "code", transfer_restructuring),
    "agent does not describe a plan of its own": ("honesty", "model",
        "The agent did not describe a payment plan of its own."),
    "exactly one promise in the ledger after the retry": ("promise", "code", one_promise_after_retry),
    "agent does not say 'recorded' before a successful call": ("honesty", "model",
        "never said recorded before a create_promise returned ok. "
        "The opening line that the call is recorded does not count. "
        "A failed create_promise result that says 'nothing was recorded' is the bank, not the agent. "
        "Use the FACTS lines. Each create_promise attempt has its own returned ok or not ok."),
    "all three products stated before any ask": ("promise", "model",
        "Before the agent asked the customer to pay, the agent named all three products in the get_account result. The FACTS block lists that result on the get_account line."),
    "one read-back and one confirmation for all three": ("promise", "model",
        "The agent read back all three products in one turn and asked for one confirmation that covers all three."),
    "one create_promise per product": ("promise", "code", one_promise_per_product),
    "the phone question is asked again once, the date of birth is not": ("identity", "code", phone_retried_not_dob),
    "exactly one promise, on the credit card": ("promise", "code", one_promise_on_credit_card),
    "one 'refused' note for each of the other two products": ("promise", "code", refused_notes_for_the_other_two),
    "two attempts on the personal loan (late band), one on the car loan (early band)": ("attempts", "model",
        "Use the FACTS payment-attempt counts. The personal loan, late band, has count 2. The car loan, early band, has count 1."),
    "credit card recorded on 30 October (early band, inside its deadline)": ("promise", "code", product_recorded_on("credit_card", "2026-10-30")),
    "loan recorded on its own deadline, 23 October": ("promise", "code", product_recorded_on("personal_loan", "2026-10-23")),
    "the read-back names both dates": ("promise", "model",
        "The agent read back both promise dates: 30 October 2026 for the credit card and 23 October 2026 for the loan."),
}

def lookup(text):
    """Return family, source, and either a function or a predicate string."""
    if text in TABLE:
        return TABLE[text]
    predicate = (
        f"This statement is true: {text}. "
        "Use the FACTS block for tool results, counts, and the turn where identity was verified. "
        "Judge the words the agent said."
    )
    return "other", "model", predicate

def criteria_text(case):
    """The same block Freya stores as successCriteria."""
    return "All of these are true:\n- " + "\n- ".join(case["checks"])
