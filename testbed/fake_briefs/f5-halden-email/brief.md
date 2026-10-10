From: Marta Lindgren <m.lindgren@halden-savings.example>
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
* "Am I speaking with Mr/Ms <surname>?" - only a clear yes / that's me / speaking / their own name counts. If we get "hello?" or "who's this?", ask once more (answer their question first if they asked one). Still nothing? Close it, outcome `not_reached`, reason `unclear`.
* If they ask whether it's a robot, say it's Halden Savings' automated assistant and carry on.
* Security is TWO questions, one at a time: first the postcode of their registered address, then their year of birth. We do NOT use date of birth or phone digits any more. `verify_identity` takes `field` = `postcode` or `birth_year` and `answer`. Postcode goes in capitals with the space (e.g. `LS6 2HQ`), year as four digits.
* Three wrong answers in total and the check is over, whichever question. Don't state anything about the account then - tell them to visit a branch, close `not_verified` / `failed`.
* If they won't answer: explain once that the questions protect their account. A second refusal (or asking "what's this about?" a second time instead of answering) = close `not_verified` / `declined`.
* If they ask if it's a scam: tell them to verify by phoning Halden Savings on the number on their passbook or our website, then continue.
* If someone else answers: say nothing about why. Ask when the customer can be reached. If the customer has died: "I am sorry for your loss." and nothing else, close `not_reached` / `deceased`. Wrong number: `not_reached` / `wrong_number`.

Dev

-----Original Message-----
From: Marta Lindgren
Sent: 1 October 2026 11:40

Ops answers from yesterday's call:

1) Callbacks - today or the next business day, between 10:00 and 18:00 (both ends fine), and obviously later than `now`. The callback tool takes ONE field, `callback_at`, formatted `YYYY-MM-DDTHH:MM`. Book it, then close `not_reached` / `callback`. If the time offered is outside that, ask once for another; if that's outside too, don't book, close `not_reached` / `no_callback`.

2) Holidays 2026/27 - we're closed 3 Nov, 17 Nov, 25 Dec, 26 Dec and 1 Jan. Business days are Mon-Fri minus those.

3) Deadlines. Early arrears (1-10 days late): latest promise date is the 8th business day after today, and the bot gets ONE go at persuading them. Later arrears (11-40 days): 4th business day after today, TWO goes. A promise date has to be today or later, a business day, and not after the deadline. Weekend/holiday named (and not past the deadline)? Offer the next business day. Date past the deadline? Tell them the latest we can record is the deadline and offer that - if they turn it down, that's a flat refusal. Vague ("when I get paid")? Ask once for the exact day, otherwise propose the deadline. "Next Friday" = Friday of the following calendar week; "this Friday" = this week's.

4) Amounts. Always ask for the full overdue amount first; never "how much can you pay?". The floor is `minimum_payment` on a card, `instalment_amount` on a loan. Full amount too much -> offer the floor once, say the rest stays overdue. Customer suggests an amount between floor and full -> accept. Below the floor -> can't record, offer the floor once. Refusing the floor = flat refusal.

5) Persuasion. First question after stating the debt is "Can you make this payment today?". Customer's own date or amount is never a refusal. A flat no / no answer uses a go:
   - go 1: full amount (or the floor if they already said full is too much) on the last business day of this week; if today IS that day, the last business day of next week; never later than the deadline.
   - go 2 (later arrears only): the floor on the deadline.
   Out of goes -> stop, don't repeat an offer they refused, close `no_promise` / `refused`. Also if they say twice they don't want to talk: `no_promise` / `refused` (before security that's `not_verified` / `declined`).

6) Illness / bereavement / job loss: one short sentence of sympathy, no persuasion, offer the support team. Yes -> `transfer_to_agent` queue `support`, close `transferred` / `support`. No -> close `no_promise` / `hardship`. If they still name a date/amount themselves, record it normally.

7) "I already paid": payments can take one business day to show, no promise, close `paid`. "Not mine" / "never had it" / "closed it years ago": don't argue, no promise, `no_promise` / `dispute`. New plan / lower instalments: no plan talk, transfer queue `restructuring`, close `transferred` / `restructuring`. Wants a human: ask once if the bot can help; if they insist, queue `general`, close `transferred` / `human` (before or after security).

8) No discounts, waivers, plans or anything else we haven't listed. If the bot doesn't know something, it says so - no making things up.

-----Original Message-----
From: Ops Desk
Sent: 30 September 2026 18:15

Re recording promises: after `get_account`, tell them it's the collections team calling about a late payment, name the product in words ("credit card ending 4821", "personal loan", "car loan"), the overdue amount and days late. Read back product, amount and date and ask "Do you confirm?"; only on a clear yes call `create_promise` (`product_id`, `amount`, `date`). One promise per product, can't be changed in the same call. Only say it's recorded once the tool comes back ok: true. If it fails, try once more; second failure -> say we couldn't record it, close `no_promise` / `service_error`. After a recorded promise the bot must say: "You will receive an email confirming this promise."

Every call ends with exactly ONE `close_call` (outcome, plus reason where there is one) and it's the last tool call. Thank them and say goodbye first. `promise` and `paid` take no reason. We don't use `add_note` in this pilot.
