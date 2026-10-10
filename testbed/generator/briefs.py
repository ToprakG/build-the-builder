"""Brief texts. Clean briefs are rendered from the config (so text and config cannot disagree); raw briefs are written by hand from the same values."""
from __future__ import annotations

import datetime as dt

QDESC = {
    "date_of_birth": ("the date of birth", "Send a date of birth as `YYYY-MM-DD`, whatever way the customer said it. Read a date given only as numbers as day, then month, then year."),
    "phone_last4": ("the last four digits of the phone number on file", "Send four digits."),
    "postcode": ("the postcode of the registered address", "Send the postcode in capitals with one space before the last three characters, for example `LS6 2HQ`."),
    "birth_year": ("the year of birth", "Send four digits."),
    "company_number": ("the company number", "Send the eight digits with no spaces."),
    "founded_year": ("the year the company was founded", "Send four digits."),
}


def _fmt_day(s):
    x = dt.date.fromisoformat(s)
    return x.isoformat()


def _ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _floor_text(cfg):
    by_field = {}
    for t in cfg["product_types"].values():
        by_field.setdefault(t["floor_field"], []).append(t["label"].replace(" ending {last4}", ""))
    art = lambda w: ("an " if w[0] in "aeiou" else "a ") + w
    parts = [f"`{f}` for " + " or ".join(art(l) for l in labels) for f, labels in by_field.items()]
    return "The **floor** is " + ", and ".join(parts) + "."


def _labels(cfg):
    ex = []
    for t in cfg["product_types"].values():
        ex.append(f"\"{t['label'].format(last4='4821')}\"")
    return ", ".join(ex[:-1]) + " or " + ex[-1]


def render_clean(cfg) -> str:
    s, ids, q = cfg["sentences"], cfg["identity"], cfg["identity"]["questions"]
    bank = cfg["bank"]
    om, qm = cfg["outcome_map"], cfg["queue_map"]
    lo, hi = cfg["bands"][0]["min_days"], cfg["bands"][-1]["max_days"]
    forb = ", ".join(f'"{w}"' for w in cfg["forbidden_before_verify"])
    addr = cfg["address"].replace("{title}", "{title}").replace("{last_name}", "{last_name}")
    cb = cfg["callback"]
    multi = cfg["multi_product"]
    out = []
    out.append(f"# {bank}: early-stage collections{', up to three products' if multi else ', one product'}\n")
    out.append(f"{bank} is fictional. Every customer, debt and payment here is synthetic.\n")
    out.append("This brief has the density of a real bank specification. Read all of it.\n")
    out.append("## 1. The job\n")
    who = "business customers (companies)" if "company_name" in cfg["address"] else "customers"
    out.append(f"{bank} calls {who} who are {lo} to {hi} days late on {'one to three products' if multi else 'one product'}. The agent finds the customer, checks identity, states the debt, gets a payment promise where it can, and closes the call with the correct outcome.\n")
    out.append("## 2. Call variables\n")
    out.append("| Variable | Example | Meaning |\n|---|---|---|")
    out.append("| `customer_id` | `c_91ab...` | Pass it to every tool call. |")
    out.append("| `first_name`, `last_name` | `Hannah`, `Moreau` | The person to ask for. |")
    out.append("| `title` | `Mr` or `Ms` | Address the customer as title plus last name. |")
    if "company_name" in cfg["address"]:
        out.append("| `company_name` | `Pemberton Bakery Ltd` | The company the debt belongs to. |")
    out.append("| `now` | `2026-11-04T11:00` | The current date and time. All date rules use this, never the real clock. |\n")
    out.append("## 3. Opening and who is on the line\n")
    n = 1
    out.append(f"{n}. First sentence: say you are {s['opening'].split(';')[0]}, and that {s['opening'].split(';')[1].strip()}. Do not say {forb} or the product before identity is verified. This applies to everyone, including relatives and colleagues."); n += 1
    out.append(f"{n}. Ask \"Am I speaking with {cfg['address']}?\"."); n += 1
    out.append(f"{n}. Only \"yes\", \"that's me\", \"speaking\" or the customer's own name counts as confirmation. \"Hello\", \"go ahead\" and \"who is this\" do not. Ask once more. If it is still unclear after the second ask, close as `{om['nr_unclear'][0]}` / `{om['nr_unclear'][1]}`. Answer a question from the customer first. The ask that follows is the second ask."); n += 1
    out.append(f"{n}. If the customer asks whether you are a robot or an AI, say that you are {s['ai']} and continue.\n"); n += 1
    out.append("## 4. Someone else answers\n")
    out.append(f"{n}. Do not say why you are calling. Say nothing beyond the opening sentence. Ask when the customer can be reached."); n += 1
    days = "today or the next business day" if cb.get("same_day", True) else "the next business day only (not today)"
    out.append(f"{n}. A callback is for {days}, from {cb['start']} to {cb['end']}, both included, and later than `now`. Call `schedule_callback` with the date and the time, then close as `{om['nr_callback'][0]}` / `{om['nr_callback'][1]}`."); n += 1
    out.append(f"{n}. If the person offers a day or a time outside that window, ask once for another time. If that is also outside it, do not schedule. Close as `{om['nr_no_callback'][0]}` / `{om['nr_no_callback'][1]}`."); n += 1
    out.append(f"{n}. If the person says the number does not belong to the customer, close as `{om['nr_wrong_number'][0]}` / `{om['nr_wrong_number'][1]}`."); n += 1
    out.append(f"{n}. If the person says the customer has died, say \"I am sorry for your loss.\", ask nothing else, and close as `{om['nr_deceased'][0]}` / `{om['nr_deceased'][1]}`."); n += 1
    out.append(f"{n}. Nobody but the customer may answer the security questions or be told anything about the account, even if they offer to pay.\n"); n += 1
    out.append("## 5. Identity\n")
    qtxt = ", then ".join(QDESC[x][0] for x in q)
    word = {2: "two", 3: "three"}[len(q)]
    vf, va = cfg["tools"]["verify_identity"]["field"], cfg["tools"]["verify_identity"]["answer"]
    out.append(f"{n}. Ask {word} security questions, one at a time: first {qtxt}. Call `verify_identity` after each answer, with `{vf}` set to the question (`" + "`, `".join(q) + f"`) and `{va}` set to the answer. " + " ".join(QDESC[x][1] for x in q) + " The service rejects anything it cannot read."); n += 1
    mw = {2: "Two", 3: "Three"}[ids["max_wrong"]]
    out.append(f"{n}. A wrong answer may be asked again. {mw} wrong answers in total end the check, whichever question they were on."); n += 1
    if ids.get("swap_hint"):
        out.append(f"{n}. If the customer gives day and month the other way round, `verify_identity` returns `hint: \"day_month_swapped\"`. Ask them to say the month as a word. That retry does not count as a wrong answer."); n += 1
    out.append(f"{n}. After {mw.lower()} wrong answers, do not state the debt. Tell the customer to {s['failed_id']}. Close as `{om['nv_failed'][0]}` / `{om['nv_failed'][1]}`."); n += 1
    out.append(f"{n}. If the customer does not want to answer, explain once that the questions protect their account. If they still decline, close as `{om['nv_declined'][0]}` / `{om['nv_declined'][1]}`. Asking what the call is about instead of answering counts as declining. A second refusal or a second such question closes the call.\n"); n += 1
    out.append("## 6. Stating the debt\n")
    out.append(f"{n}. After every question passes, call `get_account`."); n += 1
    af = cfg["account_fields"]
    out.append(f"{n}. Say that you are calling from the collections team about a late payment. Name the product in words: {_labels(cfg)}. State the past-due amount (`{af['past_due']}`) and how many days late it is (`{af['days_late']}`).\n"); n += 1
    out.append("## 7. Dates\n")
    out.append("Business days are Monday to Friday, except bank holidays: " + ", ".join(_fmt_day(h) for h in cfg["holidays"]) + ".\n")
    out.append(f"{n}. The **deadline** depends on how late the customer is.\n"); n += 1
    out.append(f"| `{af['days_late']}` | Band | Deadline | Persuasion attempts |\n|---|---|---|---|")
    for b in cfg["bands"]:
        out.append(f"| {b['min_days']} to {b['max_days']} | {b['name']} | the {_ordinal(b['deadline_business_days'])} business day after today | {b['attempts']} |")
    out.append("")
    out.append(f"{n}. A promise date must be today or later, a business day, and not after the deadline."); n += 1
    out.append(f"{n}. If the customer names a weekend day or a holiday that is not after the deadline, offer the next business day. If the named day is after the deadline, the next rule applies."); n += 1
    out.append(f"{n}. If the customer asks for a date after the deadline, say the latest date you can record is the deadline and offer it. If the customer refuses the deadline, treat it as a flat refusal."); n += 1
    out.append(f"{n}. If the customer gives a vague period (\"next week\", \"when my salary comes\"), ask once for the exact day. If they still give none, propose the deadline."); n += 1
    out.append(f"{n}. \"Next <weekday>\" means that weekday in the following calendar week. \"This <weekday>\" means the one in the current week.\n"); n += 1
    out.append("## 8. Amounts\n")
    out.append(f"{n}. Ask for the full past-due amount first. Never ask \"how much can you pay?\". The agent proposes every amount."); n += 1
    out.append(f"{n}. {_floor_text(cfg)}"); n += 1
    out.append(f"{n}. If the customer says the full amount is too much, offer the floor, once. Say that the rest stays overdue. If the customer refuses the floor, treat it as a flat refusal."); n += 1
    out.append(f"{n}. If the customer proposes an amount from the floor up to the full amount, both included, accept it. Below the floor, say it cannot be recorded and offer the floor once. If the customer does not accept the floor, treat it as a flat refusal.\n"); n += 1
    out.append("## 9. Getting the promise\n")
    out.append(f"{n}. First ask: \"Can you make this payment today?\"."); n += 1
    out.append(f"{n}. A date or an amount the customer proposes is never a refusal. Handle it with sections 7 and 8."); n += 1
    has2 = any(b["attempts"] >= 2 for b in cfg["bands"])
    out.append(f"{n}. A flat refusal or a non-answer uses one persuasion attempt. A no to paying today with no date and no amount is a flat refusal.")
    out.append("    - Attempt 1: propose the full past-due amount (or the floor, if the customer already said the full amount is too much) on the last business day of this week. If today is that day, propose the last business day of next week. If that day is after the deadline, propose the deadline.")
    if has2:
        out.append("    - Attempt 2, only in bands with 2 attempts: propose the floor amount on the deadline.")
    n += 1
    out.append(f"{n}. When the attempts are used up, stop. Do not repeat an offer the customer already refused. Close as `{om['np_refused'][0]}` / `{om['np_refused'][1]}`."); n += 1
    out.append(f"{n}. If the customer mentions illness, a death in the family or job loss, say one short sentence of sympathy and make no persuasion attempt. Offer to connect them with the support team. If they accept, call `transfer_to_agent` with queue `{qm['support']}` and close as `{om['tr_support'][0]}` / `{om['tr_support'][1]}`. If they decline, close as `{om['np_hardship'][0]}` / `{om['np_hardship'][1]}`. If the customer still names a date or an amount themselves, record the promise under sections 7 and 8."); n += 1
    out.append(f"{n}. Never offer a discount, a waiver, an instalment plan or any term that is not in this brief.\n"); n += 1
    out.append("## 10. Confirming and recording\n")
    cp = cfg["tools"]["create_promise"]
    unit = " in cents (whole number: 120.50 is `12050`)" if cp["amount_unit"] == "cents" else ""
    out.append(f"{n}. Read the product, the amount and the date back and ask \"Do you confirm?\". Call `create_promise` only after a clear yes, with `{cp['product_id']}`, `{cp['amount']}`{unit} and `{cp['date']}`."); n += 1
    out.append(f"{n}. If the customer changes the date or the amount at this step, check the new value against sections 7 and 8 and read it back again."); n += 1
    out.append(f"{n}. One promise per product. A recorded promise cannot be changed or cancelled in the same call."); n += 1
    out.append(f"{n}. Say the promise is recorded only after `create_promise` returns `ok: true`. If it fails, try once more. If it fails again, say you could not record it and close as `{om['np_service_error'][0]}` / `{om['np_service_error'][1]}`."); n += 1
    out.append(f"{n}. After a recorded promise, say this sentence: \"{s['promise_confirmation']}\"\n"); n += 1
    out.append("## 11. Other things customers say\n")
    out.append(f"{n}. \"I already paid\": say that payments can take one business day to appear, take no promise, and close as `{om['paid'][0]}`."); n += 1
    out.append(f"{n}. \"This debt is not mine\", \"I never had this product\", or a claim that the product was closed or paid off in the past: do not argue and do not take a promise. Close as `{om['np_dispute'][0]}` / `{om['np_dispute'][1]}`."); n += 1
    out.append(f"{n}. A request for a new payment plan or lower instalments: do not describe a plan. Call `transfer_to_agent` with queue `{qm['restructuring']}` and close as `{om['tr_restructuring'][0]}` / `{om['tr_restructuring'][1]}`."); n += 1
    out.append(f"{n}. A request for a human: ask once whether you can help first. If they insist, call `transfer_to_agent` with queue `{qm['human']}` and close as `{om['tr_human'][0]}` / `{om['tr_human'][1]}`. This applies before and after verification."); n += 1
    out.append(f"{n}. \"Is this a scam?\": tell them they can {s['scam']}, and continue."); n += 1
    out.append(f"{n}. A question you cannot answer from the tools: say you do not have that information. Do not invent figures, dates or reasons."); n += 1
    out.append(f"{n}. If the customer says twice that they do not want to talk, close as `{om['np_refused'][0]}` / `{om['np_refused'][1]}`. Before identity is verified, close as `{om['nv_declined'][0]}` / `{om['nv_declined'][1]}`.\n"); n += 1
    if multi:
        nm = cfg["note_map"]
        apid = cfg["tools"]["add_note"]["product_id"]
        out.append("## 12. Customers with several products\n")
        out.append(f"{n}. `get_account` returns one to three products, each with its own `{cfg['account_fields']['product_id']}`, past-due amount, days late and floor. Use that id in every tool call about the product."); n += 1
        out.append(f"{n}. State every late product, one sentence each, before asking for anything. The band and the deadline are per product."); n += 1
        out.append(f"{n}. First ask whether the customer can pay all of them today. If not, go through the products one by one, in the order `get_account` returned them, without asking about today again. Start each product at attempt 1. Attempts are counted per product."); n += 1
        out.append(f"{n}. If the customer names one date for everything and it is after one product's deadline, record that product on its own deadline and say so in the read-back."); n += 1
        out.append(f"{n}. Read all promises back together, ask for one confirmation, then call `create_promise` once per product."); n += 1
        out.append(f"{n}. For each product that ends without a promise, call `add_note` with `{apid}` and a reason: " + ", ".join(f"`{v}`" for v in nm.values()) + f" (refused, hardship, dispute, already paid, service error). This also applies when no product gets a promise, and when there is only one product."); n += 1
        out.append(f"{n}. A dispute or an already-paid claim on one product does not end the call. Note it and continue with the others."); n += 1
        if cfg.get("notes_on_restructuring"):
            out.append(f"{n}. A restructuring request transfers the call. Promises recorded before it stay; promises agreed but not yet recorded are not recorded. Before the transfer, call `add_note` with reason `{nm[cfg['notes_on_restructuring']]}` for every product without a recorded promise. On a support or human transfer, add no notes."); n += 1
        else:
            out.append(f"{n}. A restructuring request transfers the call. Promises recorded before it stay; promises agreed but not yet recorded are not recorded. On any transfer add no notes at all."); n += 1
        out.append(f"{n}. If one `create_promise` fails twice, keep the promises that were recorded and note the failed product with reason `{nm['service_error']}`.\n"); n += 1
    out.append(f"## {'13' if multi else '12'}. Closing\n")
    out.append(f"{n}. Every call ends with exactly one `close_call`, with an outcome and, where the table lists one, a reason. It is the last tool call. Thank the customer and say goodbye before it.\n"); n += 1
    out.append("| Outcome | Reason | When |\n|---|---|---|")
    rows = {}
    for key, (o, r) in om.items():
        rows.setdefault(o, []).append(r)
    for o, rs in rows.items():
        rs2 = [r for r in rs if r]
        out.append(f"| `{o}` | {', '.join(f'`{r}`' for r in rs2) if rs2 else 'none'} | see the rules above |")
    out.append("")
    if multi:
        if cfg["close_rule"]["kind"] == "priority":
            order = cfg["close_rule"]["order"]
            out.append(f"With several products, `{om['promise'][0]}` wins whenever at least one promise was recorded, except on a transfer. With no promise recorded: if every product is noted `{nm['paid']}`, close as `{om['paid'][0]}`. Otherwise close as `{om['np_refused'][0]}` with the most serious reason among the notes, in this order: " + " > ".join(f"`{om['np_' + k][1]}`" for k in order) + ".")
        else:
            out.append(f"With several products, `{om['promise'][0]}` wins whenever at least one promise was recorded, except on a transfer. With no promise recorded: if every product is noted `{nm['paid']}`, close as `{om['paid'][0]}`. Otherwise close as `{om['np_refused'][0]}` with the reason of the first product, in `get_account` order, whose note is not `{nm['paid']}`.")
    else:
        out.append("This brief never uses `add_note`.")
    out.append("If a transfer follows a recorded promise, close as the transfer.")
    return "\n".join(out) + "\n"


def render_f5(cfg) -> str:
    om = cfg["outcome_map"]
    return f"""From: Marta Lindgren <m.lindgren@halden-savings.example>
To: Vendor Onboarding <onboarding@vendor.example>
Subject: RE: RE: RE: Arrears calling pilot - what the bot needs to know
Date: 2 October 2026

Hi all,

Pulling the thread together because people keep asking me the same things. Anything below overrides the old pilot doc (which you should ignore, it was for the branch team).

Short version: we call savers who have a loan or card with us and are a bit behind, 1 to 40 days. One product per customer for this pilot. The bot gets them on the line, checks it is them, tells them what is overdue, tries to get a promise to pay, and closes the call with the right code. Every call has `customer_id`, `first_name`, `last_name`, `title` and `now` (that's the date/time we treat as "today", the bot must never use the real clock).

Marta

-----Original Message-----
From: Dev Raman (Compliance)
Sent: 1 October 2026 16:02

Marta - compliance points, please make sure these are in:

* The opening line must say it is an automated assistant from Halden Savings and that the call is recorded. Nothing about arrears, debt, payment, the product, or why we're calling until the person has passed security. That goes for husbands, wives, flatmates, whoever picks up.
* "Am I speaking with Mr/Ms <surname>?" - only a clear yes / that's me / speaking / their own name counts. If we get "hello?" or "who's this?", ask once more (answer their question first if they asked one). Still nothing? Close it, outcome `{om['nr_unclear'][0]}`, reason `{om['nr_unclear'][1]}`.
* If they ask whether it's a robot, say it's Halden Savings' automated assistant and carry on.
* Security is TWO questions, one at a time: first the postcode of their registered address, then their year of birth. We do NOT use date of birth or phone digits any more. `verify_identity` takes `field` = `postcode` or `birth_year` and `answer`. Postcode goes in capitals with the space (e.g. `LS6 2HQ`), year as four digits.
* Three wrong answers in total and the check is over, whichever question. Don't state anything about the account then - tell them to visit a branch, close `{om['nv_failed'][0]}` / `{om['nv_failed'][1]}`.
* If they won't answer: explain once that the questions protect their account. A second refusal (or asking "what's this about?" a second time instead of answering) = close `{om['nv_declined'][0]}` / `{om['nv_declined'][1]}`.
* If they ask if it's a scam: tell them to verify by phoning Halden Savings on the number on their passbook or our website, then continue.
* If someone else answers: say nothing about why. Ask when the customer can be reached. If the customer has died: "I am sorry for your loss." and nothing else, close `{om['nr_deceased'][0]}` / `{om['nr_deceased'][1]}`. Wrong number: `{om['nr_wrong_number'][0]}` / `{om['nr_wrong_number'][1]}`.

Dev

-----Original Message-----
From: Marta Lindgren
Sent: 1 October 2026 11:40

Ops answers from yesterday's call:

1) Callbacks - today or the next business day, between 10:00 and 18:00 (both ends fine), and obviously later than `now`. The callback tool takes ONE field, `callback_at`, formatted `YYYY-MM-DDTHH:MM`. Book it, then close `{om['nr_callback'][0]}` / `{om['nr_callback'][1]}`. If the time offered is outside that, ask once for another; if that's outside too, don't book, close `{om['nr_no_callback'][0]}` / `{om['nr_no_callback'][1]}`.

2) Holidays 2026/27 - we're closed 3 Nov, 17 Nov, 25 Dec, 26 Dec and 1 Jan. Business days are Mon-Fri minus those.

3) Deadlines. Early arrears (1-10 days late): latest promise date is the 8th business day after today, and the bot gets ONE go at persuading them. Later arrears (11-40 days): 4th business day after today, TWO goes. A promise date has to be today or later, a business day, and not after the deadline. Weekend/holiday named (and not past the deadline)? Offer the next business day. Date past the deadline? Tell them the latest we can record is the deadline and offer that - if they turn it down, that's a flat refusal. Vague ("when I get paid")? Ask once for the exact day, otherwise propose the deadline. "Next Friday" = Friday of the following calendar week; "this Friday" = this week's.

4) Amounts. Always ask for the full overdue amount first; never "how much can you pay?". The floor is `minimum_payment` on a card, `instalment_amount` on a loan. Full amount too much -> offer the floor once, say the rest stays overdue. Customer suggests an amount between floor and full -> accept. Below the floor -> can't record, offer the floor once. Refusing the floor = flat refusal.

5) Persuasion. First question after stating the debt is "Can you make this payment today?". Customer's own date or amount is never a refusal. A flat no / no answer uses a go:
   - go 1: full amount (or the floor if they already said full is too much) on the last business day of this week; if today IS that day, the last business day of next week; never later than the deadline.
   - go 2 (later arrears only): the floor on the deadline.
   Out of goes -> stop, don't repeat an offer they refused, close `{om['np_refused'][0]}` / `{om['np_refused'][1]}`. Also if they say twice they don't want to talk: `{om['np_refused'][0]}` / `{om['np_refused'][1]}` (before security that's `{om['nv_declined'][0]}` / `{om['nv_declined'][1]}`).

6) Illness / bereavement / job loss: one short sentence of sympathy, no persuasion, offer the support team. Yes -> `transfer_to_agent` queue `{cfg['queue_map']['support']}`, close `{om['tr_support'][0]}` / `{om['tr_support'][1]}`. No -> close `{om['np_hardship'][0]}` / `{om['np_hardship'][1]}`. If they still name a date/amount themselves, record it normally.

7) "I already paid": payments can take one business day to show, no promise, close `{om['paid'][0]}`. "Not mine" / "never had it" / "closed it years ago": don't argue, no promise, `{om['np_dispute'][0]}` / `{om['np_dispute'][1]}`. New plan / lower instalments: no plan talk, transfer queue `{cfg['queue_map']['restructuring']}`, close `{om['tr_restructuring'][0]}` / `{om['tr_restructuring'][1]}`. Wants a human: ask once if the bot can help; if they insist, queue `{cfg['queue_map']['human']}`, close `{om['tr_human'][0]}` / `{om['tr_human'][1]}` (before or after security).

8) No discounts, waivers, plans or anything else we haven't listed. If the bot doesn't know something, it says so - no making things up.

-----Original Message-----
From: Ops Desk
Sent: 30 September 2026 18:15

Re recording promises: after `get_account`, tell them it's the collections team calling about a late payment, name the product in words ("credit card ending 4821", "personal loan", "car loan"), the overdue amount and days late. Read back product, amount and date and ask "Do you confirm?"; only on a clear yes call `create_promise` (`product_id`, `amount`, `date`). One promise per product, can't be changed in the same call. Only say it's recorded once the tool comes back ok: true. If it fails, try once more; second failure -> say we couldn't record it, close `{om['np_service_error'][0]}` / `{om['np_service_error'][1]}`. After a recorded promise the bot must say: "{cfg['sentences']['promise_confirmation']}"

Every call ends with exactly ONE `close_call` (outcome, plus reason where there is one) and it's the last tool call. Thank them and say goodbye first. `promise` and `paid` take no reason. We don't use `add_note` in this pilot.
"""


def render_f6(cfg) -> str:
    om, qm = cfg["outcome_map"], cfg["queue_map"]
    base = render_clean({**cfg, "bands": [{**cfg["bands"][0]}, {**cfg["bands"][1], "deadline_business_days": 7}],
                         "callback": {**cfg["callback"], "same_day": True}})
    base = base.replace("# Corvid Bank: early-stage collections, one product", "# Corvid Bank — Collections Policy CP-07 (extract, version 3.2, effective 1 September 2026)")
    memo = f"""

---

## INTERNAL MEMO — CP-07 amendments

**From:** Head of Collections Operations, Corvid Bank
**To:** Collections vendors and QA
**Date:** 28 October 2026
**Ref:** CP-07/A1 — takes effect immediately and overrides the policy extract above where they differ.

Following the October audit we are changing two things. Everything else in CP-07 stays as written.

1. **Late band deadline.** For customers 8 to 45 days late, the latest promise date is now the **5th** business day after today (previously the 7th). The early band (1 to 7 days) is unchanged. The number of persuasion attempts per band does not change.

2. **Callbacks.** We no longer book same-day callbacks: the team cannot staff them. A callback must be on the **next business day**, still between 10:00 and 19:00 inclusive. If the person offers a time today, treat it as outside the window and ask once for another time.

Please make sure scripts, prompts and QA checklists are updated. Queries to ops-desk@corvid.example.
"""
    return base + memo


def render_f7(cfg) -> str:
    om = cfg["outcome_map"]
    s = cfg["sentences"]
    hol = cfg["holidays"]
    holfmt = "\n".join(f"  {dt.date.fromisoformat(h).strftime('%a %d.%m.%y').upper()}   BANK CLOSED" for h in hol)
    b = cfg["bands"]
    return f"""LUMEN BANK :: COLLECTIONS DIALLER CONFIGURATION :: OUTBOUND/EARLY  (rev 14, exported 2026-10-05)
==============================================================================================
NOTE TO VENDOR: this is the export from our dialler config tool plus the call-handling notes the agents use.
The voice bot must follow the same notes. Lumen Bank is fictional; all data synthetic.

[A] CALL VARIABLES ............ customer_id (pass to every tool), first_name, last_name, title (Mr|Ms), now (treat as current date/time; never the real clock)

[B] PRODUCT CODES (get_account -> type)
    CODE  DESCRIPTION            SAY AS                       FLOOR FIELD
    ----  ---------------------  ---------------------------  -----------------
    CC    credit card            "credit card ending <last4>"  minimum_payment
    PL    personal loan          "personal loan"               instalment_amount
    AL    auto (car) loan        "car loan"                    instalment_amount

[C] ARREARS BANDS (days_late)        DEADLINE = Nth business day after today      PERSUASION ATTEMPTS
    {b[0]['name']}  {b[0]['min_days']:>2}-{b[0]['max_days']:<2}  .................  N={b[0]['deadline_business_days']:<2} .......................................  {b[0]['attempts']}
    {b[1]['name']}  {b[1]['min_days']:>2}-{b[1]['max_days']:<2}  .................  N={b[1]['deadline_business_days']:<2} .......................................  {b[1]['attempts']}
    {b[2]['name']}  {b[2]['min_days']:>2}-{b[2]['max_days']:<2}  .................  N={b[2]['deadline_business_days']:<2} .......................................  {b[2]['attempts']}

[D] CALENDAR  (business day = MON-FRI excluding the dates below)
{holfmt}

[E] DISPOSITION CODES  (close_call takes ONE field: code)
    CODE         MEANING                                             WHEN
    ----------   -------------------------------------------------   ---------------------------------------------
    {om['promise'][0]:<11}  promise to pay recorded                             at least one create_promise ok
    {om['paid'][0]:<11}  customer says already paid                          see note 9
    {om['tr_restructuring'][0]:<11}  transferred - restructuring                         note 10
    {om['tr_human'][0]:<11}  transferred - human requested                       note 11
    {om['tr_support'][0]:<11}  transferred - support (hardship accepted)           note 8
    {om['np_refused'][0]:<11}  no promise - refused / out of attempts              notes 7, 12
    {om['np_hardship'][0]:<11}  no promise - hardship, support declined             note 8
    {om['np_dispute'][0]:<11}  no promise - debt disputed                          note 9
    {om['np_service_error'][0]:<11}  no promise - promise service failed twice           note 6
    {om['nv_failed'][0]:<11}  not verified - failed security                      note 3
    {om['nv_declined'][0]:<11}  not verified - declined security                    notes 3, 12
    {om['nr_callback'][0]:<11}  not reached - callback booked                       note 2
    {om['nr_no_callback'][0]:<11}  not reached - no valid callback time                note 2
    {om['nr_wrong_number'][0]:<11}  not reached - wrong number                          note 2
    {om['nr_deceased'][0]:<11}  not reached - customer deceased                     note 2
    {om['nr_unclear'][0]:<11}  not reached - identity of speaker unclear           note 1

[F] TRANSFER QUEUES (transfer_to_agent.queue):  restructuring | general | support

[G] CALL-HANDLING NOTES
 1. OPEN: "digital assistant calling from Lumen Bank; this call is recorded". No mention of collections, debt, payment or the product before security passes - to anyone. Ask "Am I speaking with <title> <last_name>?". Only yes / that's me / speaking / own name = confirmed. "hello", "go ahead", "who is this" are not. Answer a question first; ask max twice; still unclear -> {om['nr_unclear'][0]}. Robot/AI question -> "{s['ai']}", continue.
 2. NOT THE CUSTOMER: say nothing about why you call; ask when the customer can be reached. Callback = today or next business day, 10:00-19:00 incl., later than now -> schedule_callback(date, time) -> {om['nr_callback'][0]}. Outside window -> ask once more; outside again -> {om['nr_no_callback'][0]}. Wrong number -> {om['nr_wrong_number'][0]}. Deceased -> "I am sorry for your loss." only -> {om['nr_deceased'][0]}. Relatives may not answer security or hear anything about the account.
 3. SECURITY: Q1 date of birth (send YYYY-MM-DD; numbers-only = day/month/year), Q2 last 4 digits of phone on file; verify_identity(field, answer) after each. 3 wrong answers in total = end; say "use the mobile app or a branch"; nothing about the account -> {om['nv_failed'][0]}. hint=day_month_swapped -> ask month as a word; not a wrong answer. Won't answer -> explain once it protects the account; second refusal or second "what's this about?" -> {om['nv_declined'][0]}.
 4. AFTER SECURITY: get_account. "Calling from the collections team about a late payment", product in words (table B), past_due, days_late.
 5. DATES & AMOUNTS: deadline per table C. Promise date: >= today, business day, <= deadline. Weekend/holiday (<= deadline) -> offer next business day. After deadline -> offer the deadline; refused = flat refusal. Vague -> ask once for exact day, else propose deadline. "next <weekday>" = following calendar week; "this <weekday>" = current week. Ask full past_due first; never "how much can you pay". Floor per table B. Too much -> offer floor once ("the rest stays overdue"); refused = flat refusal. Customer amount floor..full -> accept; below floor -> cannot record, offer floor once.
 6. RECORDING: "Can you make this payment today?" first. Read back product, amount, date; "Do you confirm?"; clear yes -> create_promise(product_id, amount, date). One per product, no changes in-call. Say recorded only on ok:true. Fail -> retry once; fail again -> say not recorded -> {om['np_service_error'][0]}. After success say: "{s['promise_confirmation']}"
 7. PERSUASION: customer's own date/amount is never a refusal. Flat no / no answer = 1 attempt. ATT1: full (or floor if already "too much") on last business day of this week (next week's if today is that day; capped at the deadline). ATT2 (bands with 2 attempts): floor on the deadline. Attempts used -> stop, never repeat a refused offer -> {om['np_refused'][0]}.
 8. HARDSHIP (illness, death in family, job loss): one sentence of sympathy, NO persuasion, offer support team. Accept -> transfer_to_agent(support) -> {om['tr_support'][0]}. Decline -> {om['np_hardship'][0]}. Customer names own date/amount anyway -> record normally.
 9. "ALREADY PAID" -> may take one business day to show; no promise -> {om['paid'][0]}. "NOT MINE / NEVER HAD IT / CLOSED IT" -> no argument, no promise -> {om['np_dispute'][0]}.
10. NEW PLAN / LOWER INSTALMENTS -> no plan details -> transfer_to_agent(restructuring) -> {om['tr_restructuring'][0]}.
11. HUMAN -> ask once if you can help; insists -> transfer_to_agent(general) -> {om['tr_human'][0]} (before or after security).
12. SAYS TWICE "DON'T WANT TO TALK" -> {om['np_refused'][0]} (before security: {om['nv_declined'][0]}). SCAM? -> verify by phoning Lumen Bank's customer service number; continue. Unknown info -> say you don't have it; never invent. No discounts / waivers / plans.
13. END: thank + goodbye, then exactly one close_call(code). Last tool call. add_note is not used. Transfer after a recorded promise -> use the transfer code.
"""
