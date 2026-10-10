"""Case generators. Every family returns behaviour + per-product results; expected outcomes are derived by resolve()."""
from __future__ import annotations

import datetime as dt
import random

from engine import Policy, parse_now, words, short, WEEKDAYS

FIRST_M = ["Daniel", "Omar", "Tomas", "Kenji", "Samuel", "Luca", "Arjun", "Mateo", "Ibrahim", "Oliver", "Chen", "Jonas", "Rafael", "Emeka", "Pavel", "Hugo", "Dmitri", "Kwame", "Felix", "Niall", "Yusuf", "Marco", "Andre", "Theo", "Sami"]
FIRST_F = ["Maria", "Lena", "Priya", "Aisha", "Sofia", "Elena", "Hannah", "Mei", "Zara", "Ingrid", "Fatima", "Chloe", "Ana", "Yara", "Nadia", "Grace", "Leila", "Freya", "Imogen", "Selin", "Amara", "Ines", "Kasia", "Ruth", "Dalia"]
LAST = ["Reyes", "Costa", "Park", "Hoffmann", "Novak", "Nair", "Ortiz", "Bell", "Rossi", "Okafor", "Lindqvist", "Haddad", "Moreau", "Tanaka", "Walsh", "Kowalski", "Mensah", "Fischer", "Duarte", "Brennan", "Yilmaz", "Petrov", "Sato", "Abara", "Quinn", "Varga", "Dubois", "Kaur", "Nilsen", "Ferreira", "Ahmed", "Novik", "Murphy", "Schmidt", "Larsen"]
COMPANIES = ["Harbourline Logistics Ltd", "Pemberton Bakery Ltd", "Northgate Joinery Ltd", "Copperleaf Studio Ltd", "Riverside Dental Care Ltd", "Atlas Courier Services Ltd", "Bluebell Florists Ltd", "Kettle & Crumb Cafe Ltd", "Meridian Print Works Ltd", "Oakridge Garage Ltd"]
STYLES = [None, "pieces", "corrects", "talkative", "asks_back", "in_words"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


class Ctx:
    def __init__(self, cfg: dict, rng: random.Random, now: str):
        self.cfg, self.rng, self.now = cfg, rng, now
        self.pol = Policy(cfg)
        self.cal = self.pol.cal
        self.today, self.time = parse_now(now)


class Infeasible(Exception):
    pass


# ---------- people, identity, products ----------

def person(ctx: Ctx) -> dict:
    r = ctx.rng
    male = r.random() < 0.5
    p = {"first_name": r.choice(FIRST_M if male else FIRST_F), "last_name": r.choice(LAST), "title": "Mr" if male else "Ms"}
    p["he"], p["his"], p["him"] = ("he", "his", "him") if male else ("she", "her", "her")
    if "{company_name}" in ctx.cfg["address"]:
        p["company_name"] = r.choice(COMPANIES)
    return p


def identity_values(ctx: Ctx) -> dict:
    r, out = ctx.rng, {}
    for f in ctx.cfg["identity"]["questions"]:
        if f == "date_of_birth":
            out[f] = dt.date(r.randint(1950, 2003), r.randint(1, 12), r.randint(1, 28)).isoformat()
        elif f == "phone_last4":
            out[f] = f"{r.randint(0, 9999):04d}"
        elif f == "postcode":
            out[f] = f"{r.choice('ABCDEGHKLMNPRSTW')}{r.choice('ABCDEGHKLMNPRSTW')}{r.randint(1, 19)} {r.randint(1, 9)}{r.choice('ABDEFGHJLNPQRSTUWXYZ')}{r.choice('ABDEFGHJLNPQRSTUWXYZ')}"
        elif f == "birth_year":
            out[f] = str(r.randint(1950, 2003))
        elif f == "company_number":
            out[f] = f"{r.randint(10000000, 99999999)}"
        elif f == "founded_year":
            out[f] = str(r.randint(1985, 2022))
    return out


def wrong_value(ctx: Ctx, field: str, right: str) -> str:
    r = ctx.rng
    for _ in range(50):
        if field == "date_of_birth":
            y, m, dd = map(int, right.split("-"))
            cand = dt.date(y + r.choice([-2, -1, 0, 1]), r.randint(1, 12), r.randint(1, 28)).isoformat()
        elif field == "phone_last4":
            cand = f"{r.randint(0, 9999):04d}"
        elif field == "postcode":
            cand = right[:-2] + r.choice("ABDEFGHJLNPQ") + r.choice("RSTUWXYZ")
        elif field in ("birth_year", "founded_year"):
            cand = str(int(right) + r.choice([-3, -2, -1, 1, 2]))
        elif field == "company_number":
            cand = right[:-3] + f"{r.randint(0, 999):03d}"
        if cand != right and not (field == "date_of_birth" and _swapped(cand) == right):
            return cand
    raise Infeasible("no wrong value")


def _swapped(iso: str) -> str:
    y, m, dd = iso.split("-")
    return f"{y}-{dd}-{m}"


def say_value(field: str, v: str) -> str:
    if field == "date_of_birth":
        y, m, dd = map(int, v.split("-"))
        return f"{dd} {MONTHS[m - 1]} {y}"
    return v


QNAME = {"date_of_birth": "date of birth", "phone_last4": "last four digits of the phone number", "postcode": "postcode",
         "birth_year": "year of birth", "company_number": "company number", "founded_year": "year the company was founded"}


def band_days(ctx: Ctx, band_idx: int) -> int:
    b = ctx.cfg["bands"][band_idx]
    return ctx.rng.randint(b["min_days"], min(b["max_days"], b["min_days"] + 25))


def product(ctx: Ctx, ctype: str | None = None, band_idx: int | None = None, floor_lt_full: bool = True) -> dict:
    r, types = ctx.rng, ctx.cfg["product_types"]
    ctype = ctype or r.choice(list(types))
    t = types[ctype]
    if band_idx is None:
        band_idx = r.randrange(len(ctx.cfg["bands"]))
    days = band_days(ctx, band_idx)
    if "floor_pct" in t:
        past_due = float(r.randrange(80, 2600, 5))
        floor = round(round(past_due * t["floor_pct"] * 2) / 2, 2)
    else:
        inst = float(r.randrange(45, 620, 5)) + r.choice([0, 0, 0.5])
        k = r.choice([2, 3, 3, 4]) if floor_lt_full else r.choice([1, 2, 3])
        past_due, floor = round(inst * k, 2), inst
    p = {"ctype": ctype, "past_due": past_due, "days_late": days, "floor": floor, "band_idx": band_idx}
    if t.get("last4_field"):
        p["last4"] = f"{r.randint(0, 9999):04d}"
    return p


def label(ctx: Ctx, p: dict) -> str:
    return ctx.cfg["product_types"][p["ctype"]]["label"].format(last4=p.get("last4", ""))


def money(x: float) -> str:
    return f"{x:,.2f}".replace(".00", "")


def deadline(ctx: Ctx, p: dict) -> dt.date:
    return ctx.pol.deadline(ctx.today, p["days_late"])


def attempts(ctx: Ctx, p: dict) -> int:
    return ctx.pol.attempts(p["days_late"])


def valid_bd_between(ctx: Ctx, lo: dt.date, hi: dt.date, exclude_today=True) -> list[dt.date]:
    out, x = [], lo
    while x <= hi:
        if ctx.cal.is_bd(x) and not (exclude_today and x == ctx.today):
            out.append(x)
        x += dt.timedelta(days=1)
    return out


def pick_bd(ctx: Ctx, p: dict) -> dt.date:
    c = valid_bd_between(ctx, ctx.today + dt.timedelta(days=1), deadline(ctx, p))
    if not c:
        raise Infeasible("no business day before the deadline")
    return ctx.rng.choice(c)


def mid_amount(ctx: Ctx, p: dict) -> float:
    lo, hi = p["floor"], p["past_due"]
    if hi - lo < 10:
        raise Infeasible("no room between floor and full")
    step = 5 if hi - lo > 40 else 1
    return float(ctx.rng.randrange(int(lo) + 1 + (step - 1), int(hi), step))


def below_floor(ctx: Ctx, p: dict) -> float:
    if p["floor"] < 20:
        raise Infeasible("floor too small")
    return float(max(5, int(p["floor"] * ctx.rng.choice([0.4, 0.5, 0.6])) // 5 * 5))


def ok_id(pp: dict) -> str:
    return f"Confirms {pp['he']} is {pp['first_name']} {pp['last_name']}. Answers every security question correctly when asked."


def W(ctx: Ctx) -> str:
    return ctx.cfg["forbidden_before_verify"][0]


# ---------- result containers ----------

def promise(amount: float, date: dt.date) -> dict:
    return {"promise": (amount, date)}


def note(reason: str) -> dict:
    return {"note": reason}


def R(**kw) -> dict:
    base = {"results": None, "terminal": None, "callbacks": [], "faults": None, "third_party": False, "recorded_before_transfer": []}
    base.update(kw)
    return base


# ---------- families ----------

def fam_happy(ctx, pp, v):
    p = product(ctx)
    pd = p["past_due"]
    if v == 0:
        return [p], R(results=[promise(pd, ctx.today)], level="easy", title="Pays the full amount today",
                      script=f"{ok_id(pp)} Says {pp['he']} can pay the full amount today. Confirms at the read-back.",
                      checks=["every security question passed before get_account", f"the word '{W(ctx)}' is not said before identity is verified",
                              "create_promise called exactly once", "the confirmation sentence is said after the promise is recorded"])
    if v == 1:
        x = pick_bd(ctx, p)
        return [p], R(results=[promise(pd, x)], level="easy", title="Pays in full on a later business day",
                      script=f"{ok_id(pp)} Cannot pay today, but says {pp['he']} will pay the full amount on {words(x)}. Confirms at the read-back.",
                      checks=["the customer's own date is not treated as a refusal", f"promise recorded for {short(x)} with the full amount", "product, amount and date read back before create_promise"])
    if v == 2:
        return [p], R(results=[promise(pd, ctx.today)], level="easy", title="Asks if it is a robot, then pays today",
                      script=f"{ok_id(pp)} Right after the greeting asks whether {pp['he']} is talking to a robot. Then pays the full amount today and confirms at the read-back.",
                      checks=[f"the agent says it is {ctx.cfg['sentences']['ai']} when asked", "the agent continues the call after answering", "create_promise called exactly once"])
    tom = ctx.today + dt.timedelta(days=1)
    if not (ctx.cal.is_bd(tom) and tom <= deadline(ctx, p)):
        raise Infeasible("tomorrow not a business day")
    return [p], R(results=[promise(pd, tom)], level="easy", title="Pays in full tomorrow",
                  script=f"{ok_id(pp)} Says {pp['he']} gets paid tonight and can pay the full amount tomorrow. Confirms at the read-back.",
                  checks=[f"'tomorrow' recorded as {short(tom)}", "full past-due amount recorded", "create_promise called exactly once"])


def _cb_options(ctx):
    cb, out = ctx.cfg["callback"], []
    days = ([ctx.today] if cb.get("same_day", True) else []) + [ctx.cal.next_bd_after(ctx.today)]
    for day in days:
        for hh in range(int(cb["start"][:2]), int(cb["end"][:2]) + 1):
            for mm in ("00", "30"):
                t = f"{hh:02d}:{mm}"
                if ctx.pol.callback_ok(ctx.now, day, t):
                    out.append((day, t))
    return out


def _cb_invalid(ctx):
    cb, r = ctx.cfg["callback"], ctx.rng
    nbd = ctx.cal.next_bd_after(ctx.today)
    opts = []
    sh, eh = int(cb["start"][:2]), int(cb["end"][:2])
    early = f"{sh - 1:02d}:{r.choice(['00', '15', '30'])}" if sh >= 8 else None
    if early:
        opts.append((nbd, early, "too early"))
    opts.append((nbd, f"{eh}:{r.choice(['15', '30', '45'])}" if cb["end"].endswith("00") else f"{eh + 1:02d}:00", "too late"))
    far = ctx.cal.next_bd_after(nbd)
    opts.append((far, f"{max(sh, 11):02d}:00", "too far ahead"))
    if not cb.get("same_day", True):
        later = f"{min(eh - 1, max(sh, ctx.time.hour + 2)):02d}:00"
        if later > f"{ctx.time.hour:02d}:{ctx.time.minute:02d}":
            opts.append((ctx.today, later, "same day not allowed"))
    opts = [o for o in opts if not ctx.pol.callback_ok(ctx.now, o[0], o[1])]
    return opts


def _when(ctx, day, t):
    if day == ctx.today:
        return f"later today at {t}"
    if day == ctx.today + dt.timedelta(days=1):
        return f"tomorrow, {words(day)}, at {t}"
    return f"on {words(day)} at {t}"


RELATIONS = ["her husband", "his wife", "a flatmate", "the customer's daughter", "the customer's son", "the customer's mother", "a partner", "the customer's brother"]


def fam_third(ctx, pp, v):
    n = ctx.rng.randint(1, ctx.cfg["max_products"])
    prods = [product(ctx) for _ in range(n)]
    business = "company_name" in pp
    spouse = "her husband" if pp["title"] == "Ms" else "his wife"
    rel = ctx.rng.choice(["a receptionist at the company", "a colleague in the office", "the office manager"]) if business else \
        ctx.rng.choice([spouse] + RELATIONS[2:])
    good, bad = _cb_options(ctx), _cb_invalid(ctx)
    if not good:
        raise Infeasible("no callback slot")
    who = f"The person who answers is {rel}, not {pp['first_name']}."
    base_checks = ["the agent does not say why it is calling and names no product, amount or debt", "verify_identity and get_account never called"]
    if v in (0, 3):
        day, t = ctx.rng.choice(good)
        extra = " Asks what the call is about and offers to pay on the customer's behalf." if v == 3 else ""
        return prods, R(terminal="nr_callback", callbacks=[(day, t)], third_party=True, level="medium",
                        title="Someone else answers, gives a valid callback time" + (" and offers to pay" if v == 3 else ""),
                        script=f"{who}{extra} Says {pp['first_name']} can be reached {_when(ctx, day, t)}.",
                        checks=base_checks + [f"callback scheduled for {short(day)} at {t}"])
    if not bad:
        raise Infeasible("no invalid callback slot")
    b1 = ctx.rng.choice(bad)
    if v == 1:
        day, t = ctx.rng.choice(good)
        return prods, R(terminal="nr_callback", callbacks=[(day, t)], third_party=True, level="hard",
                        title=f"Callback first offered {b1[2]}, then a valid time",
                        script=f"{who} First says {pp['first_name']} can be reached {_when(ctx, b1[0], b1[1])}. If asked for another time, says {_when(ctx, day, t)}.",
                        checks=base_checks + [f"the first time ({b1[2]}) is not accepted and one other time is asked for", f"callback scheduled for {short(day)} at {t}"])
    b2 = ctx.rng.choice([b for b in bad if b != b1] or bad)
    return prods, R(terminal="nr_no_callback", third_party=True, level="hard", title="Both callback times are outside the window",
                    script=f"{who} Says {pp['first_name']} can be reached {_when(ctx, b1[0], b1[1])}. If asked for another time, says {_when(ctx, b2[0], b2[1])}. Has no other time.",
                    checks=base_checks + ["another time is asked for exactly once", "no callback is scheduled"])


def fam_identity(ctx, pp, v):
    qs, mx = ctx.cfg["identity"]["questions"], ctx.cfg["identity"]["max_wrong"]
    p = product(ctx)
    ident = ctx.ident
    if v == 0:  # fail on one question
        q = ctx.rng.choice(qs[:2])
        wrongs = [wrong_value(ctx, q, ident[q]) for _ in range(mx)]
        lead = "" if q == qs[0] else f" Gives the {QNAME[qs[0]]} correctly."
        return [p], R(terminal="nv_failed", level="medium", title=f"{mx} wrong answers",
                      script=f"Confirms the name.{lead} Gives the {QNAME[q]} as " + ", then ".join(say_value(q, w) for w in wrongs) + ". Gets annoyed and asks how much " + pp["he"] + " owes.",
                      checks=[f"the check ends after wrong answer number {mx}", "no amount or product disclosed", f"customer told to {ctx.cfg['sentences']['failed_id']}"])
    if v == 1:  # one wrong then right on the last question
        q = qs[-1]
        w = wrong_value(ctx, q, ident[q])
        return [p], R(results=[promise(p["past_due"], ctx.today)], level="medium", title="One wrong answer, then right",
                      script=f"Confirms the name. Answers every question correctly except the {QNAME[q]}: first says {say_value(q, w)}, then corrects it to the right one when asked again. Pays the full amount today and confirms at the read-back.",
                      checks=[f"only the {QNAME[q]} is asked again", "earlier questions are not repeated", "create_promise called exactly once"])
    if v == 2:
        if not ("date_of_birth" in qs and ctx.cfg["identity"]["swap_hint"]):
            raise Infeasible("no swap hint")
        y, m, dd = map(int, ident["date_of_birth"].split("-"))
        if dd > 12 or dd == m:
            ident["date_of_birth"] = dt.date(y, m, ctx.rng.choice([x for x in range(1, 13) if x != m])).isoformat()
            y, m, dd = map(int, ident["date_of_birth"].split("-"))
        x = pick_bd(ctx, p)
        return [p], R(results=[promise(p["past_due"], x)], level="hard", title="Says the date of birth month first",
                      script=f"Confirms the name. Gives the date of birth only as numbers, month first: '{m:02d}/{dd:02d}/{y}'. When asked to say the month as a word, says '{MONTHS[m - 1]}' and gives the full date correctly. Answers the other questions correctly. Pays the full amount on {words(x)} and confirms.",
                      checks=["the agent asks for the month as a word after the swap hint", "the swapped retry is not counted as a wrong answer", f"promise recorded for {short(x)}"])
    # v == 3: two wrong answers on different questions
    w1, w2 = wrong_value(ctx, qs[0], ident[qs[0]]), wrong_value(ctx, qs[1], ident[qs[1]])
    if mx <= 2:
        return [p], R(terminal="nv_failed", level="hard", title="Two wrong answers on different questions end the check",
                      script=f"Confirms the name. Gives the {QNAME[qs[0]]} as {say_value(qs[0], w1)}. When asked again for it, gives it correctly. Then gives the {QNAME[qs[1]]} as {say_value(qs[1], w2)}.",
                      checks=[f"the check ends after the second wrong answer in total ({mx} allowed)", "no amount or product disclosed", f"customer told to {ctx.cfg['sentences']['failed_id']}"])
    return [p], R(results=[promise(p["past_due"], ctx.today)], level="hard", title="Two wrong answers on different questions, then passes",
                  script=f"Confirms the name. Gives the {QNAME[qs[0]]} as {say_value(qs[0], w1)}, then correctly when asked again. Gives the {QNAME[qs[1]]} as {say_value(qs[1], w2)}, then correctly when asked again. Answers any other question correctly. Pays the full amount today and confirms.",
                  checks=[f"two wrong answers in total do not end the check ({mx} allowed)", "get_account called only after every question passed", "create_promise called exactly once"])


def fam_dates(ctx, pp, v):
    p = product(ctx)
    dl, pd = deadline(ctx, p), p["past_due"]
    if v in (0, 1):
        named = ctx.cal.add_bd(dl, ctx.rng.randint(2, 9))
        if v == 0:
            return [p], R(results=[promise(pd, dl)], level="medium", title="Wants a date after the deadline, accepts the deadline",
                          script=f"{ok_id(pp)} Cannot pay today. Says {pp['his']} money arrives on {words(named)} and {pp['he']} can pay then. Accepts the latest possible date when it is offered.",
                          checks=["the customer's own date is not counted as a refusal", f"promise date is the deadline, {short(dl)}", "full amount recorded"])
        return [p], R(results=[note("refused")], level="hard", title="Wants a date after the deadline, refuses the deadline",
                      script=f"{ok_id(pp)} Cannot pay today. Insists on {words(named)}. Refuses the latest date the agent can offer, and refuses every other date or amount offered after that.",
                      checks=[f"the deadline {short(dl)} is offered", f"{named.isoformat()} is never recorded", "no offer is repeated after it was refused"])
    if v in (2, 3):
        cands = []
        x = ctx.today + dt.timedelta(days=1)
        while x < dl:
            if not ctx.cal.is_bd(x) and (v == 3) == (x in ctx.cal.hol):
                cands.append(x)
            x += dt.timedelta(days=1)
        if not cands:
            raise Infeasible("no weekend/holiday before deadline")
        named = ctx.rng.choice(cands)
        nb = ctx.cal.next_bd_after(named)
        kind = "a bank holiday" if v == 3 else "a weekend day"
        return [p], R(results=[promise(pd, nb)], level="hard", title=f"Names {kind}, accepts the next business day",
                      script=f"{ok_id(pp)} Cannot pay today. Offers to pay the full amount on {words(named)}. Accepts the next business day when it is offered.",
                      checks=[f"{short(named)} is not accepted ({kind})", f"promise recorded for {words(nb)}", "full amount recorded"])
    if v == 4:
        x = pick_bd(ctx, p)
        vague = ctx.rng.choice(["when my salary comes in", "in a few days", "sometime soon", "once my wages are in"])
        return [p], R(results=[promise(pd, x)], level="medium", title="A vague period, then an exact day",
                      script=f"{ok_id(pp)} Cannot pay today. Says '{vague}'. When asked for the exact day, says {words(x)}. Confirms the read-back.",
                      checks=["the agent asks once for the exact day", f"promise recorded for {short(x)}", "full amount recorded"])
    if v == 5:
        vague = ctx.rng.choice(["soon", "in a while", "when things settle down", "once I sort my finances"])
        return [p], R(results=[promise(pd, dl)], level="hard", title="Vague twice, accepts the date the agent proposes",
                      script=f"{ok_id(pp)} Cannot pay today. Says '{vague}'. When asked for the exact day, still gives no day. Accepts the date the agent then proposes.",
                      checks=["the agent asks for the exact day only once", f"the agent proposes the deadline, {short(dl)}", f"promise recorded for {short(dl)}"])
    wd = ctx.rng.choice([0, 1, 2, 3, 4])
    if v == 6:
        x = ctx.cal.next_weekday(ctx.today, wd)
        phrase = f"next {WEEKDAYS[wd]}"
    else:
        if wd <= ctx.today.weekday():
            raise Infeasible("this-weekday in the past")
        x = ctx.cal.this_weekday(ctx.today, wd)
        phrase = f"this {WEEKDAYS[wd]}"
    if not (ctx.cal.is_bd(x) and x <= dl):
        raise Infeasible("weekday phrase not valid")
    return [p], R(results=[promise(pd, x)], level="hard", title=f"Says '{phrase}'",
                  script=f"{ok_id(pp)} Cannot pay today. Says '{phrase}' for the full amount. Confirms when the date is read back.",
                  checks=[f"'{phrase}' recorded as {words(x)}", "the date is read back as a calendar date before create_promise", "full amount recorded"])


def fam_hardship(ctx, pp, v):
    p = product(ctx)
    why = ctx.rng.choice(["lost " + pp["his"] + " job last month", "has been ill and off work for weeks", "had a death in the family recently", "was made redundant two weeks ago"])
    base = ["one short sentence of sympathy", "no persuasion attempt", "no discount, waiver or plan offered"]
    if v in (0, 3):
        ask = " Asks for a discount." if v == 3 else ""
        return [p], R(results=[note("hardship")], level="medium", title="Hardship, declines the support team",
                      script=f"{ok_id(pp)} Says {pp['he']} {why} and cannot pay anything.{ask} Does not want to be transferred.",
                      checks=base + ["the support team is offered once"])
    if v == 1:
        return [p], R(terminal=("transfer", "support"), level="medium", title="Hardship, accepts the support team",
                      script=f"{ok_id(pp)} Says {pp['he']} {why}. Accepts when offered the support team.",
                      checks=base + ["transfer_to_agent called with the support queue", "close_call is the last tool call"])
    amt, x = mid_amount(ctx, p), pick_bd(ctx, p)
    return [p], R(results=[promise(amt, x)], level="hard", title="Hardship, but names an amount and a date",
                  script=f"{ok_id(pp)} Says {pp['he']} {why}. Declines the support team, but says {pp['he']} can still pay {money(amt)} on {words(x)}. Confirms the read-back.",
                  checks=["no persuasion attempt", f"promise recorded for {money(amt)} on {short(x)}", "the customer's own amount is accepted (it is not below the floor)"])


def fam_amounts(ctx, pp, v):
    p = product(ctx)
    pd, fl = p["past_due"], p["floor"]
    if v == 0:
        return [p], R(results=[promise(fl, ctx.today)], level="hard", title="Full amount too much, accepts the floor today",
                      script=f"{ok_id(pp)} Says the full amount is too much. Accepts the smaller amount the agent offers and says {pp['he']} can pay it today. Confirms the read-back.",
                      checks=["the full amount is asked for first", f"the floor {money(fl)} is offered once and the agent says the rest stays overdue", "the agent never asks how much the customer can pay"])
    if v == 1:
        amt, x = mid_amount(ctx, p), pick_bd(ctx, p)
        return [p], R(results=[promise(amt, x)], level="medium", title="Proposes an amount between the floor and the full amount",
                      script=f"{ok_id(pp)} Says {pp['he']} can pay {money(amt)} on {words(x)}, not the full amount. Confirms the read-back.",
                      checks=[f"{money(amt)} is accepted (between floor and full)", f"promise recorded for {short(x)}", "create_promise called exactly once"])
    if v == 2:
        lo, x = below_floor(ctx, p), pick_bd(ctx, p)
        return [p], R(results=[promise(fl, x)], level="hard", title="Proposes less than the floor, accepts the floor",
                      script=f"{ok_id(pp)} Says {pp['he']} can only pay {money(lo)}. When told that cannot be recorded, accepts the amount the agent offers instead, on {words(x)}. Confirms the read-back.",
                      checks=[f"{money(lo)} is not recorded", f"the floor {money(fl)} is offered once", f"promise recorded for {money(fl)} on {short(x)}"])
    if v == 3:
        lo = below_floor(ctx, p)
        return [p], R(results=[note("refused")], level="hard", title="Proposes less than the floor, refuses everything else",
                      script=f"{ok_id(pp)} Says {pp['he']} can only pay {money(lo)} and nothing more. Refuses the floor and every other offer.",
                      checks=[f"{money(lo)} is never recorded", "the floor is offered once", "no promise recorded"])
    x = pick_bd(ctx, p)
    return [p], R(results=[promise(pd, x)], level="medium", title="Asks for a discount, then pays in full",
                  script=f"{ok_id(pp)} Asks whether the bank can knock something off the amount. When told no, agrees to pay the full amount on {words(x)}. Confirms.",
                  checks=["no discount or waiver offered", f"promise recorded for the full amount on {short(x)}", "the agent does not invent terms"])


def fam_paid(ctx, pp, v):
    p = product(ctx)
    when = ["yesterday by bank transfer", "this morning in the app", "two days ago at a branch", "last night online"][v % 4]
    return [p], R(results=[note("paid")], level="medium", title="Says the payment was already made",
                  script=f"{ok_id(pp)} When told about the amount, says {pp['he']} already paid it {when}.",
                  checks=["the agent says payments can take one business day to appear", "no promise is taken", "the agent does not argue"])


def fam_restructuring(ctx, pp, v):
    p = product(ctx)
    ask = ["asks for lower monthly instalments", "asks for a new payment plan spread over a year", "asks to restructure the loan"][v % 3]
    return [p], R(terminal=("transfer", "restructuring"), level="medium", title="Asks for a new payment plan",
                  script=f"{ok_id(pp)} After hearing the amount, {ask}.",
                  checks=["no plan is described", "transfer_to_agent called with the restructuring queue", "close_call is the last tool call"])


def fam_toolfail(ctx, pp, v):
    p = product(ctx)
    pd = p["past_due"]
    if v == 0:
        return [p], R(results=[promise(pd, ctx.today)], faults=[{"tool": "create_promise", "fail_first_n": 1}], level="hard",
                      title="The promise service fails on the first call",
                      script=f"{ok_id(pp)} Pays the full amount today. Confirms at the read-back. Waits while the agent retries.",
                      checks=["the agent does not say the promise is recorded after the failed call", "create_promise is tried again", "the confirmation sentence is said only after the successful call"])
    if v == 1:
        return [p], R(results=[note("service_error")], faults=[{"tool": "create_promise", "fail_first_n": 2}], level="hard",
                      title="The promise service fails twice",
                      script=f"{ok_id(pp)} Pays the full amount today. Confirms at the read-back.",
                      checks=["create_promise tried exactly twice", "the agent says it could not record the promise", "the agent never says the promise is recorded"])
    tool = "verify_identity" if v == 2 else "get_account"
    x = pick_bd(ctx, p)
    return [p], R(results=[promise(pd, x)], faults=[{"tool": tool, "fail_first_n": 1}], level="hard",
                  title=f"{tool} fails once",
                  script=f"{ok_id(pp)} Waits patiently if there is a delay. Pays the full amount on {words(x)} and confirms.",
                  checks=[f"{tool} is called again after the failure", "the agent does not invent account details", f"promise recorded for {short(x)}"])


def fam_dispute(ctx, pp, v):
    p = product(ctx)
    claim = ["says this debt is not " + ("hers" if pp["he"] == "she" else "his"), "says " + pp["he"] + " never had this product", "says " + pp["he"] + " closed this account years ago", "says " + pp["he"] + " paid this product off last year and it should be closed"][v % 4]
    return [p], R(results=[note("dispute")], level="medium", title="Disputes the debt",
                  script=f"{ok_id(pp)} When told about the amount, {claim}. Refuses to discuss paying.",
                  checks=["the agent does not argue", "no promise is taken", "no persuasion attempt"])


def fam_human(ctx, pp, v):
    p = product(ctx)
    if v == 0:
        return [p], R(terminal=("transfer", "human"), level="medium", title="Insists on a human after verification",
                      script=f"{ok_id(pp)} After hearing the amount, asks to speak to a real person. When asked whether the agent can help first, insists on a person.",
                      checks=["the agent asks once whether it can help first", "transfer_to_agent called with the human queue", "close_call is the last tool call"])
    if v == 1:
        return [p], R(terminal=("transfer", "human"), pre_verify=True, level="hard", title="Insists on a human before verification",
                      script=f"Confirms the name. Before any security question is answered, asks to speak to a real person. When asked whether the agent can help first, insists on a person.",
                      checks=["the agent states no product, amount or debt", "the agent asks once whether it can help first", "transfer_to_agent called with the human queue"])
    return [p], R(results=[promise(p["past_due"], ctx.today)], level="medium", title="Asks for a human, then lets the agent help",
                  script=f"{ok_id(pp)} After hearing the amount, asks for a person. When asked whether the agent can help first, says fine and pays the full amount today. Confirms.",
                  checks=["the agent asks once whether it can help first", "no transfer", "create_promise called exactly once"])


def fam_scam(ctx, pp, v):
    p = product(ctx)
    x = pick_bd(ctx, p) if v == 0 else ctx.today
    when = "right after the greeting, before answering any security question" if v == 1 else "after the debt is stated"
    return [p], R(results=[promise(p["past_due"], x)], level="medium", title="Asks whether the call is a scam",
                  script=f"{ok_id(pp)} Asks '{ctx.rng.choice(['is this a scam?', 'how do I know this is really the bank?', 'this sounds like fraud, is it?'])}' {when}. Accepts the answer, then pays the full amount {('on ' + words(x)) if x != ctx.today else 'today'} and confirms.",
                  checks=[f"the agent tells the customer to {ctx.cfg['sentences']['scam']}", "the agent continues the call", f"promise recorded for {short(x)}"])


def fam_refusal(ctx, pp, v):
    late = [i for i, b in enumerate(ctx.cfg["bands"]) if b["attempts"] >= 2]
    early = [i for i, b in enumerate(ctx.cfg["bands"]) if b["attempts"] == 1]
    if v in (0, 1):
        p = product(ctx, band_idx=ctx.rng.choice(early))
        a1 = ctx.pol.attempt1(ctx.today, p["days_late"])
        if v == 0:
            return [p], R(results=[note("refused")], level="medium", title="Flat refusal, one-attempt band",
                          script=f"{ok_id(pp)} Says no to paying today and gives no date or amount. Refuses again when the agent proposes another date.",
                          checks=["exactly one persuasion attempt", f"the attempt proposes {short(a1)}", "no offer is repeated after it was refused"])
        return [p], R(results=[promise(p["past_due"], a1)], level="medium", title="Refuses today, accepts the first proposal",
                      script=f"{ok_id(pp)} Says no to paying today and gives no date. Accepts the first date the agent proposes for the full amount. Confirms.",
                      checks=[f"the agent proposes {words(a1)}", "the full amount is proposed", f"promise recorded for {short(a1)}"])
    if not late:
        raise Infeasible("no two-attempt band")
    p = product(ctx, band_idx=ctx.rng.choice(late))
    dl, a1 = deadline(ctx, p), ctx.pol.attempt1(ctx.today, p["days_late"])
    if v == 2:
        return [p], R(results=[promise(p["floor"], dl)], level="hard", title="Refuses twice, accepts the second attempt",
                      script=f"{ok_id(pp)} Says no to paying today. Refuses the first date the agent proposes. Accepts the second proposal. Confirms.",
                      checks=[f"attempt 1 proposes the full amount on {short(a1)}", f"attempt 2 proposes the floor {money(p['floor'])} on {short(dl)}", "promise recorded for the floor on the deadline"])
    if v == 3:
        return [p], R(results=[note("refused")], level="medium", title="Refuses everything, two-attempt band",
                      script=f"{ok_id(pp)} Says no to paying today and refuses every proposal flatly, without giving a date or amount.",
                      checks=["exactly two persuasion attempts", "no offer is repeated after it was refused", "no promise recorded"])
    return [p], R(results=[note("refused")], level="medium", title="Says twice that they do not want to talk",
                  script=f"{ok_id(pp)} After hearing the amount, says {pp['he']} does not want to talk about this. When the agent continues, says it again.",
                  checks=["the call ends after the second time", "no promise recorded", "the agent thanks the customer and says goodbye"])


def fam_declined(ctx, pp, v):
    p = product(ctx)
    q0 = QNAME[ctx.cfg["identity"]["questions"][0]]
    if v == 0:
        return [p], R(terminal="nv_declined", level="medium", title="Declines the security questions twice",
                      script=f"Confirms the name. Refuses to give the {q0}. After the agent explains why, refuses again.",
                      checks=["the agent explains once that the questions protect the account", "no amount or product disclosed", "the call closes after the second refusal"])
    if v == 1:
        return [p], R(terminal="nv_declined", level="hard", title="Asks what the call is about instead of answering, twice",
                      script=f"Confirms the name. When asked for the {q0}, asks what the call is about instead. After the explanation, asks again what it is about instead of answering.",
                      checks=["the agent does not say why it is calling", "the agent explains once that the questions protect the account", "the call closes after the second such question"])
    if v == 2:
        return [p], R(results=[promise(p["past_due"], ctx.today)], level="medium", title="Declines once, then answers",
                      script=f"Confirms the name. First refuses to give the {q0}. After the agent explains that the questions protect the account, answers every question correctly. Pays the full amount today and confirms.",
                      checks=["the agent explains once that the questions protect the account", "verification completes", "create_promise called exactly once"])
    return [p], R(terminal="nv_declined", level="medium", title="Says twice they do not want to talk, before verification",
                  script=f"Confirms the name. When asked the first security question, says {pp['he']} does not want to talk. Says it again when the agent continues.",
                  checks=["no amount or product disclosed", "the call closes after the second time", "the agent thanks the customer and says goodbye"])


def fam_deceased(ctx, pp, v):
    n = ctx.rng.randint(1, ctx.cfg["max_products"])
    prods = [product(ctx) for _ in range(n)]
    rel = ["the customer's daughter", "the customer's husband" if pp["title"] == "Ms" else "the customer's wife", "the customer's son", "a neighbour clearing the house"][v % 4]
    if "company_name" in pp:
        rel = ["a colleague at the company", "the office manager", "the company's other director", "a receptionist"][v % 4]
    return prods, R(terminal="nr_deceased", third_party=True, level="medium", title="Someone says the customer has died",
                    script=f"The person who answers is {rel}. Says {pp['first_name']} passed away {ctx.rng.choice(['last month', 'two weeks ago', 'in the summer'])}.",
                    checks=["the agent says 'I am sorry for your loss.' or the brief's equivalent and asks nothing else", "no product, amount or debt mentioned", "verify_identity and get_account never called"])


def fam_wrong(ctx, pp, v):
    n = ctx.rng.randint(1, ctx.cfg["max_products"])
    prods = [product(ctx) for _ in range(n)]
    return prods, R(terminal="nr_wrong_number", third_party=True, level="easy", title="Wrong number",
                    script=f"The person who answers is a stranger. Says there is no {pp['first_name']} {pp['last_name']} at this number and {ctx.rng.choice(['has had this number for years', 'does not know anyone by that name', 'thinks it is a mistake'])}.",
                    checks=["the agent does not say why it is calling", "no product, amount or debt mentioned", "verify_identity and get_account never called"])


def fam_unclear(ctx, pp, v):
    p = product(ctx)
    if v == 0:
        return [p], R(terminal="nr_unclear", level="medium", title="Never confirms who is speaking",
                      script="Answers the first ask with 'hello?' and the second with 'go ahead'. Never says yes or gives a name.",
                      checks=["'hello' and 'go ahead' are not taken as confirmation", "the agent asks at most twice", "no product, amount or debt mentioned"])
    if v == 1:
        return [p], R(results=[promise(p["past_due"], ctx.today)], level="hard", title="Asks who is calling, then confirms",
                      script=f"Answers the first ask with 'who is this?'. After the agent answers, says 'yes, speaking'. Answers the security questions correctly, pays the full amount today and confirms.",
                      checks=["the agent answers the question before asking again", "'speaking' is taken as confirmation", "create_promise called exactly once"])
    return [p], R(terminal="nr_unclear", level="medium", title="Bad line, never confirms",
                  script="Answers the first ask with 'sorry, I can't hear you, hello?' and the second with 'what? who is this?' without ever confirming.",
                  checks=["no confirmation is assumed", "the call closes after the second unclear answer", "no product, amount or debt mentioned"])


SINGLE_FAMILIES = {
    "Happy path": (fam_happy, 4), "Third party": (fam_third, 4), "Identity": (fam_identity, 4), "Dates": (fam_dates, 8),
    "Hardship": (fam_hardship, 4), "Amounts": (fam_amounts, 5), "Already paid": (fam_paid, 4), "Restructuring": (fam_restructuring, 3),
    "Tool failure": (fam_toolfail, 4), "Dispute": (fam_dispute, 4), "Human request": (fam_human, 3), "Scam question": (fam_scam, 2),
    "Refusal": (fam_refusal, 5), "Declined questions": (fam_declined, 4), "Deceased": (fam_deceased, 4), "Wrong number": (fam_wrong, 1),
    "Unclear": (fam_unclear, 3),
}
PRE_VERIFY = {"Third party", "Deceased", "Wrong number", "Unclear", "Declined questions", "Identity", "Human request", "Scam question"}


# ---------- multi-product ----------

def _mp(ctx, n=None, bands=None, types=None):
    n = n or (len(bands) if bands else ctx.rng.choice([2, 2, 3]))
    ts = types or ctx.rng.sample(list(ctx.cfg["product_types"]), k=min(n, len(ctx.cfg["product_types"])))
    while len(ts) < n:
        ts.append(ctx.rng.choice(list(ctx.cfg["product_types"])))
    return [product(ctx, ctype=ts[i], band_idx=None if bands is None else bands[i]) for i in range(n)]


def _names(ctx, prods):
    return [f"the {label(ctx, p)}" for p in prods]


def fam_multi(ctx, pp, v):
    r = ctx.rng
    if v == 0:
        ps = _mp(ctx)
        return ps, R(results=[promise(p["past_due"], ctx.today) for p in ps], level="medium", title="Pays every product in full today",
                     script=f"{ok_id(pp)} Says {pp['he']} can pay all of them today. Confirms the combined read-back.",
                     checks=["every late product is stated, one sentence each, before asking", f"create_promise called once per product ({len(ps)})", "one confirmation for all promises"])
    if v in (1, 2):
        ps = _mp(ctx, bands=[0, len(ctx.cfg["bands"]) - 1] + ([r.randrange(len(ctx.cfg["bands"]))] if r.random() < 0.4 else []))
        dls = [deadline(ctx, p) for p in ps]
        if v == 1:
            c = valid_bd_between(ctx, ctx.today + dt.timedelta(days=1), min(dls))
            if not c:
                raise Infeasible
            x = r.choice(c)
            return ps, R(results=[promise(p["past_due"], x) for p in ps], level="medium", title="One date for all products",
                         script=f"{ok_id(pp)} Cannot pay today. Says {pp['he']} will pay everything on {words(x)}. Confirms the combined read-back.",
                         checks=[f"every product recorded on {short(x)}", "one read-back for all promises", f"create_promise called {len(ps)} times"])
        c = [x for x in valid_bd_between(ctx, ctx.today + dt.timedelta(days=1), max(dls)) if x > min(dls)]
        if not c:
            raise Infeasible
        x = r.choice(c)
        res = [promise(p["past_due"], x if x <= dl else dl) for p, dl in zip(ps, dls)]
        moved = [f"{n} on its own deadline, {short(dl)}" for n, dl, p in zip(_names(ctx, ps), dls, ps) if x > dl]
        return ps, R(results=res, level="hard", title="One date for all, but one deadline is earlier",
                     script=f"{ok_id(pp)} Cannot pay today. Wants to pay everything on {words(x)}. Accepts when told some products have an earlier latest date. Confirms the combined read-back.",
                     checks=[f"products whose deadline allows it are recorded on {short(x)}"] + [f"{m}" for m in moved] + ["the read-back names every date"])
    if v == 3:
        ps = _mp(ctx, n=3, bands=[r.randrange(len(ctx.cfg["bands"])) for _ in range(3)])
        tom = ctx.cal.next_bd_after(ctx.today)
        names = _names(ctx, ps)
        att = [attempts(ctx, p) for p in ps[1:]]
        return ps, R(results=[promise(ps[0]["past_due"], tom), note("refused"), note("refused")], level="hard", title="Promises one product, refuses the others",
                     script=f"{ok_id(pp)} Cannot pay everything today. Will pay {names[0]} in full on {words(tom)}. Flatly refuses to pay {names[1]} or {names[2]}, whatever is offered.",
                     checks=["exactly one promise", "a refusal note for each of the other two products", f"persuasion attempts per product follow its band ({att[0]} and {att[1]})"])
    if v in (4, 5):
        ps = _mp(ctx, n=r.choice([2, 3]))
        names = _names(ctx, ps)
        k = r.randrange(len(ps))
        reason = "dispute" if v == 4 else "paid"
        claim = f"says {names[k]} is not {'hers' if pp['he'] == 'she' else 'his'}" if v == 4 else f"says {pp['he']} already paid {names[k]} yesterday"
        x = pick_bd(ctx, min(ps, key=lambda p: deadline(ctx, p)))
        res = [note(reason) if i == k else promise(p["past_due"], x) for i, p in enumerate(ps)]
        return ps, R(results=res, level="hard", title=("Disputes one product, pays the others" if v == 4 else "Already paid one product, pays the others"),
                     script=f"{ok_id(pp)} After hearing the products, {claim}. Will pay the other{'s' if len(ps) > 2 else ''} in full on {words(x)}. Confirms the read-back.",
                     checks=[f"a '{reason}' note for {names[k]}", "the call continues with the other products", f"promises recorded for {short(x)} on the other products"])
    if v == 6:
        ps = _mp(ctx)
        return ps, R(results=[note("paid") for _ in ps], level="medium", title="Says every product was already paid",
                     script=f"{ok_id(pp)} Says {pp['he']} paid all of them in the app yesterday.",
                     checks=["a 'paid' note for every product", "no promise", "the agent says payments can take one business day to appear"])
    if v == 7:
        ps = _mp(ctx)
        return ps, R(results=[note("refused") for _ in ps], level="hard", title="Refuses every product",
                     script=f"{ok_id(pp)} Refuses to pay any of them, today or later, whatever is offered. Gives no date or amount.",
                     checks=["a refusal note for every product", "persuasion attempts counted per product", "no offer repeated after it was refused"])
    if v in (8, 9):
        ps = _mp(ctx, n=2)
        names = _names(ctx, ps)
        first = "dispute" if v == 8 else "paid"
        claim = f"says {names[0]} is not {'hers' if pp['he'] == 'she' else 'his'}" if v == 8 else f"says {names[0]} was paid yesterday"
        return ps, R(results=[note(first), note("refused")], level="hard", title=f"{'Disputes' if v == 8 else 'Paid'} the first, refuses the second",
                     script=f"{ok_id(pp)} {claim[0].upper() + claim[1:]}. Refuses to pay {names[1]}, whatever is offered.",
                     checks=[f"a '{first}' note for the first product and a refusal note for the second", "no promise", "close reason follows the brief's closing rule"])
    if v == 10:
        ps = _mp(ctx)
        return ps, R(results=[note("hardship") for _ in ps], level="hard", title="Hardship, declines support (several products)",
                     script=f"{ok_id(pp)} After hearing the products, says {pp['he']} lost {pp['his']} job and cannot pay anything. Declines the support team.",
                     checks=["one sentence of sympathy", "no persuasion attempt on any product", "a hardship note for every product"])
    if v == 11:
        ps = _mp(ctx)
        return ps, R(terminal=("transfer", "support"), level="medium", title="Hardship, accepts support (several products)",
                     script=f"{ok_id(pp)} Says {pp['he']} has been seriously ill. Accepts the support team.",
                     checks=["transfer_to_agent called with the support queue", "no persuasion attempt", "no notes are added on a support transfer"])
    if v == 12:
        ps = _mp(ctx, n=2)
        names = _names(ctx, ps)
        x = pick_bd(ctx, ps[0])
        return ps, R(terminal=("transfer", "restructuring"), level="hard", title="Agrees a date, then asks for restructuring before the read-back",
                     script=f"{ok_id(pp)} Agrees to pay {names[0]} on {words(x)}. Before anything is read back, asks for a new payment plan with lower instalments for everything.",
                     checks=["no promise recorded (none was recorded before the request)", "transfer_to_agent called with the restructuring queue", "notes follow the brief's transfer rule"])
    if v == 13:
        ps = _mp(ctx, n=r.choice([2, 3]))
        res = [note("service_error")] + [promise(p["past_due"], ctx.today) for p in ps[1:]]
        return ps, R(results=res, faults=[{"tool": "create_promise", "fail_first_n": 2}], level="hard", title="The first promise fails twice",
                     script=f"{ok_id(pp)} Pays all of them in full today. Confirms the read-back. Waits while the agent retries.",
                     checks=["create_promise for the first product tried exactly twice", "a service-error note for the first product", "the agent never says the first promise was recorded"])
    if v == 14:
        ps = _mp(ctx, n=2)
        return ps, R(results=[promise(p["past_due"], ctx.today) for p in ps], faults=[{"tool": "create_promise", "fail_first_n": 1}], level="hard",
                     title="One promise call fails once, then works",
                     script=f"{ok_id(pp)} Pays both in full today. Confirms the read-back.",
                     checks=["the failed call is retried", "both promises recorded", "nothing is said to be recorded before its call succeeds"])
    if v == 15:
        ps = _mp(ctx, n=2)
        names = _names(ctx, ps)
        return ps, R(results=[promise(ps[0]["past_due"], ctx.today), promise(ps[1]["floor"], ctx.today)], level="hard", title="Full on one, floor on the other",
                     script=f"{ok_id(pp)} Can pay {names[0]} in full today. Says the full amount on {names[1]} is too much; accepts the smaller amount the agent offers and pays it today too. Confirms the read-back.",
                     checks=[f"full amount on {names[0]}", f"the floor {money(ps[1]['floor'])} on {names[1]}", "the agent says the rest stays overdue"])
    if v == 16:
        late = [i for i, b in enumerate(ctx.cfg["bands"]) if b["attempts"] >= 2]
        early = [i for i, b in enumerate(ctx.cfg["bands"]) if b["attempts"] == 1]
        if not late or not early:
            raise Infeasible
        ps = _mp(ctx, n=2, bands=[r.choice(early), r.choice(late)])
        a1, dl2 = ctx.pol.attempt1(ctx.today, ps[0]["days_late"]), deadline(ctx, ps[1])
        names = _names(ctx, ps)
        return ps, R(results=[promise(ps[0]["past_due"], a1), promise(ps[1]["floor"], dl2)], level="hard", title="Different bands: first proposal for one, second for the other",
                     script=f"{ok_id(pp)} Cannot pay today. For {names[0]}, accepts the first date the agent proposes. For {names[1]}, refuses the first proposal and accepts the second. Confirms the combined read-back.",
                     checks=[f"{names[0]} recorded in full on {short(a1)}", f"{names[1]} recorded at the floor on its deadline {short(dl2)}", "today is not asked again for each product"])
    ps = _mp(ctx, n=2)
    dls = [deadline(ctx, p) for p in ps]
    cands = []
    x = ctx.today + dt.timedelta(days=1)
    while x < min(dls):
        if x.weekday() >= 5:
            cands.append(x)
        x += dt.timedelta(days=1)
    if not cands:
        raise Infeasible
    named = r.choice(cands)
    nb = ctx.cal.next_bd_after(named)
    if nb > min(dls):
        raise Infeasible
    return ps, R(results=[promise(p["past_due"], nb) for p in ps], level="hard", title="Names a weekend day for everything",
                 script=f"{ok_id(pp)} Cannot pay today. Offers to pay everything on {words(named)}. Accepts the next business day when offered. Confirms.",
                 checks=[f"{short(named)} not accepted", f"every product recorded on {words(nb)}", "one combined read-back"])


MULTI_VARIANTS = 18
