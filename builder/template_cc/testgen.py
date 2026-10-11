"""TESTGEN: cases generated from OUR extracted policy.json with the testbed's generic case generator.

Only `testbed/generator/{engine,cases,assemble}.py` (code) are imported, by putting that folder on sys.path for the
import. No testbed data file is opened. The generator's brief-specific hooks (case-id prefixes, plan sizes keyed on
kit/fake ids, six known identity field names) are adapted IN MEMORY for the duration of one build; the files are
never edited. Families the generator cannot express for this policy are dropped and logged (DESIGN.md §6).
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import importlib
import random
import sys

from builder.template_cc.io import REPO

GEN = REPO / "testbed" / "generator"
GEN_MODULES = ("engine", "cases", "assemble")
FORBIDDEN_MODULES = ("configs", "briefs", "toolapi", "write", "validate")
FAMILY_KEYS = {
    "Happy path": ("promise",), "Dates": ("promise",), "Amounts": ("promise",), "Scam question": ("promise",),
    "Third party": ("nr_callback", "nr_no_callback"), "Deceased": ("nr_deceased",), "Wrong number": ("nr_wrong_number",),
    "Unclear": ("nr_unclear",), "Identity": ("nv_failed", "promise"), "Declined questions": ("nv_declined",),
    "Hardship": ("np_hardship", "tr_support"), "Restructuring": ("tr_restructuring",), "Human request": ("tr_human",),
    "Dispute": ("np_dispute",), "Already paid": ("paid",), "Tool failure": ("np_service_error",), "Refusal": ("np_refused",),
    "Multi-product": ("promise",),
}
CANON_CFG_KEYS = ("language", "address", "identity", "callback", "outcome_map", "queue_map", "note_map", "close_rule",
                  "notes_on_restructuring", "product_types", "account_fields", "product_id_format", "tools",
                  "forbidden_before_verify", "multi_product", "max_products", "attempt1", "bank", "holidays", "bands",
                  "sentences")


def load_generator():
    """Import engine, cases, assemble by name from testbed/generator. Returns (engine, cases, assemble)."""
    added = str(GEN) not in sys.path
    if added:
        sys.path.insert(0, str(GEN))
    try:
        mods = tuple(importlib.import_module(m) for m in GEN_MODULES)
    finally:
        if added:
            sys.path.remove(str(GEN))
    bad = [m for m in FORBIDDEN_MODULES if m in sys.modules and str(getattr(sys.modules[m], "__file__", "")).startswith(str(GEN))]
    if bad:
        raise RuntimeError(f"testbed generator data modules were imported: {bad}")
    return mods


# --- policy -> generator cfg --------------------------------------------------------------------------------------------

def _business(d: dt.date, hol: set) -> bool:
    return d.weekday() < 5 and d not in hol


def synth_now_dates(policy: dict, seed: int = 7) -> list[str]:
    """5 business days near the policy's holidays: before a holiday, a Friday, a week's last business day,
    a Monday, and one late afternoon just before the callback window closes."""
    rng = random.Random(f"tcc-now-{seed}")
    hol = set()
    for h in policy.get("holidays") or []:
        try:
            hol.add(dt.date.fromisoformat(h))
        except ValueError:
            pass
    anchor = min(hol) if hol else dt.date.today()
    lo = anchor - dt.timedelta(days=14)
    days = [lo + dt.timedelta(days=i) for i in range(42)]
    bds = [d for d in days if _business(d, hol)]
    end = str((policy.get("callback") or {}).get("end") or "17:00")
    try:
        late_t = (dt.datetime.combine(anchor, dt.time.fromisoformat(end)) - dt.timedelta(minutes=40)).strftime("%H:%M")
    except ValueError:
        late_t = "16:20"
    picks: list[tuple[dt.date, str]] = []

    def add(cands, t):
        cands = [c for c in cands if c not in [p[0] for p in picks]]
        if cands:
            picks.append((cands[0] if len(cands) == 1 else rng.choice(cands[:3]), t))

    add([d for d in bds if (d + dt.timedelta(days=1)) in hol or _next_bd(d, hol) - d > dt.timedelta(days=3)], "11:00")
    add([d for d in bds if d.weekday() == 4], "15:30")
    add([d for d in bds if d.weekday() < 4 and not any(_business(d + dt.timedelta(days=k), hol) for k in range(1, 5 - d.weekday()))], "14:00")
    add([d for d in bds if d.weekday() == 0], "09:40")
    add(bds, late_t)
    while len(picks) < 5 and len(picks) < len(bds):
        add(bds, "10:15")
    return sorted(f"{d.isoformat()}T{t}" for d, t in picks)


def _next_bd(d, hol):
    x = d + dt.timedelta(days=1)
    while not _business(x, hol):
        x += dt.timedelta(days=1)
    return x


def policy_to_cfg(policy: dict, seed: int = 7) -> tuple[dict, list[str]]:
    gaps = []
    cfg = {k: copy.deepcopy(policy.get(k)) for k in CANON_CFG_KEYS}
    cfg["id"] = "tcc"
    if "{n}" not in str(cfg.get("product_id_format") or ""):
        cfg["product_id_format"] = "p{n}"  # the same default as the self-test bank (vendor_runner.bank_config)
        gaps.append("product_id_format has no {n}: cases use p{n}")
    cfg["now_dates"] = synth_now_dates(policy, seed)
    rng = random.Random(f"tcc-floor-{seed}")
    for t in (cfg.get("product_types") or {}).values():
        if t.get("last4_field"):
            t["floor_pct"] = round(rng.uniform(0.15, 0.35), 2)
    lines = policy.get("lines") or {}
    s = cfg.setdefault("sentences", {}) or {}
    for k, line_key in (("ai", "ai"), ("scam", "scam"), ("failed_id", "failed_id")):
        if not s.get(k):
            s[k] = (lines.get(line_key) or {}).get("text") or k
    cfg["sentences"] = s
    if not cfg.get("forbidden_before_verify"):
        cfg["forbidden_before_verify"] = ["account"]
        gaps.append("forbidden_before_verify empty: generator scripts use a generic word")
    for key in ("queue_map", "note_map", "outcome_map"):
        cfg[key] = {k: v for k, v in (cfg.get(key) or {}).items()}
    return cfg, gaps


def families(policy: dict) -> tuple[list[str], list[str]]:
    """Families the generator can express for this policy, and the gaps."""
    om = policy.get("outcome_map") or {}
    qm = policy.get("queue_map") or {}
    keep, gaps = [], []
    for fam, keys in FAMILY_KEYS.items():
        if fam == "Multi-product" and not policy.get("multi_product"):
            continue
        missing = [k for k in keys if not om.get(k)]
        missing += [f"queue {k[3:]}" for k in keys if k.startswith("tr_") and not qm.get(k[3:])]
        if missing:
            gaps.append(f"{fam}: dropped (no {', '.join(missing)})")
            continue
        keep.append(fam)
    plan = policy.get("attempt_plan") or []
    if plan and plan[0].get("date") != "end_of_week_roll":
        for fam in ("Dates", "Refusal"):
            if fam in keep:
                keep.remove(fam)
                gaps.append(f"{fam}: dropped (attempt 1 rule {plan[0].get('date')} is not the generator's)")
    ww = policy.get("weekday_words") or {}
    if (ww.get("next", "following_week"), ww.get("this", "current_week")) != ("following_week", "current_week") and "Dates" in keep:
        keep.remove("Dates")
        gaps.append("Dates: dropped (weekday words differ from the generator's)")
    if policy.get("multi_product") and (policy.get("close_rule") or {}).get("kind") not in ("first_non_paid", "priority"):
        if "Multi-product" in keep:
            keep.remove("Multi-product")
            gaps.append("Multi-product: dropped (close rule not expressible)")
    return keep, gaps


def make_plan(fams: list[str], weights: dict, n_cases: int) -> tuple[dict, int]:
    single = [f for f in fams if f != "Multi-product"]
    n_multi = n_cases // 3 if "Multi-product" in fams else 0
    n_single = max(len(single), n_cases - n_multi)
    total = sum(weights.get(f, 2) for f in single) or 1
    plan = {f: max(1, round(n_single * weights.get(f, 2) / total)) for f in single}
    while sum(plan.values()) > n_single:
        f = max(plan, key=lambda k: plan[k])
        plan[f] -= 1
    return plan, n_multi


# --- in-memory adaptation of the generator ----------------------------------------------------------------------------

def _gen_value(kind: str, rng) -> str:
    if kind == "date":
        return dt.date(rng.randint(1950, 2003), rng.randint(1, 12), rng.randint(1, 28)).isoformat()
    if kind == "year":
        return str(rng.randint(1950, 2003))
    if kind == "digits":
        return f"{rng.randint(0, 9999):04d}"
    return "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ23456789") for _ in range(6))


@contextlib.contextmanager
def adapted(C, A, policy: dict, plan: tuple):
    saved = {"plan_for": A.plan_for, "identity_values": C.identity_values, "wrong_value": C.wrong_value,
             "QNAME": dict(C.QNAME), "PREFIX": dict(A.PREFIX)}
    kinds = (policy.get("identity") or {}).get("kinds") or {}
    known = set(saved["QNAME"])
    orig_values, orig_wrong = C.identity_values, C.wrong_value

    def identity_values(ctx):
        out = orig_values(ctx)
        for f in ctx.cfg["identity"]["questions"]:
            if f not in out:
                out[f] = _gen_value(kinds.get(f, "code"), ctx.rng)
        return out

    def wrong_value(ctx, field, right):
        if field in known:
            return orig_wrong(ctx, field, right)
        for _ in range(50):
            cand = _gen_value(kinds.get(field, "code"), ctx.rng)
            if cand != right:
                return cand
        raise C.Infeasible("no wrong value")

    try:
        A.PREFIX["tcc"] = "T"
        A.plan_for = lambda cfg: plan
        C.identity_values, C.wrong_value = identity_values, wrong_value
        for f in (policy.get("identity") or {}).get("questions") or []:
            C.QNAME.setdefault(f, f.replace("_", " "))
        yield
    finally:
        A.plan_for, C.identity_values, C.wrong_value = saved["plan_for"], saved["identity_values"], saved["wrong_value"]
        C.QNAME.clear()
        C.QNAME.update(saved["QNAME"])
        A.PREFIX.clear()
        A.PREFIX.update(saved["PREFIX"])


def generate(policy: dict, n_cases: int = 36, seed: int = 7, trace=None) -> tuple[list[dict], list[str]]:
    """Cases in the testbed format (expected results from OUR policy) and the list of gaps. Never raises."""
    gaps: list[str] = []
    try:
        E, C, A = load_generator()
        cfg, g = policy_to_cfg(policy, seed)
        gaps += g
        fams, g = families(policy)
        gaps += g
        weights = getattr(A, "PLAN_FAKE_SINGLE", {})
        for _ in range(len(fams) + 1):
            plan = make_plan(fams, weights, n_cases)
            try:
                with adapted(C, A, policy, plan):
                    cases = A.build(cfg, seed=seed)
                break
            except Exception as e:  # noqa: BLE001 - a family the generator cannot make: drop it and retry
                fam = next((f for f in fams if f in str(e)), None) or (fams[-1] if fams else None)
                gaps.append(f"{fam}: dropped (generator error: {e})")
                if fam is None:
                    raise
                fams.remove(fam)
        else:
            cases = []
        for c in cases:
            c["source"] = "tcc-testgen"
    except Exception as e:  # noqa: BLE001
        gaps.append(f"testgen failed: {e!r}")
        cases = []
    if trace:
        trace.line(f"testgen: {len(cases)} case(s) from our policy; gaps: {'; '.join(gaps) or 'none'}")
    return cases, gaps


def split(cases: list[dict]) -> tuple[list[dict], list[dict]]:
    """Train / heldout 2:1, stratified by family, deterministic."""
    train, held = [], []
    by: dict[str, list] = {}
    for c in sorted(cases, key=lambda c: c["id"]):
        by.setdefault(c["family"], []).append(c)
    k = 0
    for fam in sorted(by):  # one running counter over the family-sorted list: 2:1 overall, spread over families
        for c in by[fam]:
            (held if k % 3 == 2 else train).append(c)
            k += 1
    return train, held


def persona_tokens(cases: list[dict]) -> set:
    """Names, ids and amounts of the given cases (the judge rejects patches that contain them)."""
    out = set()
    for c in cases:
        out |= {str(v) for v in (c.get("variables") or {}).values() if isinstance(v, str) and not v[:4].isdigit()}
        out |= {str(v) for v in (c.get("identity") or {}).values()}
        for p in c.get("products") or []:
            out |= {str(v) for v in p.values() if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 10}
            out |= {str(v) for k, v in p.items() if isinstance(v, str) and v.isdigit()}
        out.add(c["id"])
    out -= {"Mr", "Ms", "Mx"}
    return out
