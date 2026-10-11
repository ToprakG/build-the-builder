"""PATCH JUDGE: deterministic checks first (all critical), then one yes/no decision per question.

    judge = make_judge(models, trace, call_llm)        # ToprakDecisionJudge, LocalDecisionJudge, or None
    verdict = judge.judge(patch, ctx)                  # prompt patch
    verdict = judge.judge_policy(patch, ctx)           # policy patch (audit)

A different decision model plugs in with one DecisionBackend class: `ask(text, questions) -> {name: Answer}`.
Every verdict is appended to ctx.log_path (patch_log.jsonl) with its inputs and answers, for calibration.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from builder.template_cc import compiler, patches, policy_check, zones
from builder.template_cc.io import REPO

VENDOR = REPO / "vendor" / "build-the-builder"

# name -> (question, passing answer, critical by default)
PROMPT_QUESTIONS = {
    "necessary": ("In the evidence turns, does the agent's behaviour break the rule that this patch adds or sharpens?", "yes", True),
    "supported": ("Does the quoted brief text require the behaviour this patch asks for?", "yes", True),
    "contradicts_brief": ("Does the patch ask for anything that the brief forbids or that conflicts with another brief rule?", "no", True),
    "case_specific": ("Does the patch only fit one particular customer or conversation instead of a general situation?", "no", True),
    "redundant": ("Does the zone's current text already state this rule just as concretely? (A replace that makes an "
                  "existing rule more concrete is not redundant.)", "no", True),
    "right_place": ("Is this zone the step of the call where the behaviour happens?", "yes", True),
}
POLICY_QUESTIONS = {
    "value_supported": ("Does the quote state exactly this value?", "yes", True),
    "override_respected": ("If later brief material changes this rule, does the new value follow the latest version? "
                           "(Answer yes when nothing later changes it.)", "yes", True),
    "contradicts_brief": ("Does the new value conflict with anything in the brief?", "no", True),
}
PRE_VERIFY_STAGES = ("opening", "opening_ask2", "third_party", "identity", "identity_explain")
NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")

FRAME = ("You judge a proposed edit to the instructions of a bank's automated phone agent (a collections call). "
         "The edit was written to fix a behaviour seen in test calls. Answer each question about the edit using only "
         "the material below: the brief (the bank's specification), the edit, the current text of the place it edits, "
         "and the bug with its evidence turns.")


@dataclass
class Answer:
    answer: str | None  # "yes" | "no" | None (no answer)
    p: float | None = None
    evidence: str = ""


@dataclass
class Verdict:
    accepted: bool
    checks: list = field(default_factory=list)
    reasons: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


@dataclass
class JudgeContext:
    brief_text: str
    policy: dict
    workflow: dict | None = None
    book: zones.ZoneBook | None = None
    api: object = None
    bugs: dict = field(default_factory=dict)  # bug_id -> bug dict (behaviour, expected_per_brief, evidence[...])
    evidence_text: dict = field(default_factory=dict)  # bug_id -> rendered evidence turns
    facts_text: str = ""  # computed facts seen in the runs
    case_tokens: set = field(default_factory=set)  # names, ids, amounts of the failing cases' personas
    base_size: int = 0  # model-facing text size at the start of the iteration
    log_path: Path | None = None
    iteration: int = 0
    critical: dict = field(default_factory=dict)  # question -> bool override


# --- decision backends -------------------------------------------------------------------------------------------------

class DecisionBackend:
    name = "abstract"
    model = ""

    def ask(self, text: str, questions: list[dict]) -> dict:  # [{name, question}] -> {name: Answer}
        raise NotImplementedError


def load_vendor(path: Path = VENDOR):
    """Load vendor tools/decide.py under private module names. Returns the module or None."""
    tools = Path(path) / "tools"
    if not (tools / "decide.py").exists() or not (tools / "envfile.py").exists():
        return None
    saved = sys.modules.get("envfile")
    try:
        spec = importlib.util.spec_from_file_location("_tcc_vendor_envfile", tools / "envfile.py")
        env = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(env)
        sys.modules["envfile"] = env  # decide.py does `from envfile import load_env`
        spec = importlib.util.spec_from_file_location("_tcc_vendor_decide", tools / "decide.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None
    finally:
        if saved is None:
            sys.modules.pop("envfile", None)
        else:
            sys.modules["envfile"] = saved


class VendorDecisionBackend(DecisionBackend):
    """build-the-builder's decision model: decide(text, [{name, type: predicate, instructions}]) -> P(yes)."""
    name = "vendor"

    def __init__(self, module, threshold: float = 0.5, model: str = "", trace=None):
        self.mod, self.threshold, self.trace = module, threshold, trace
        self.model = model or getattr(module, "MODEL", "")

    def ask(self, text, questions):
        qs = [{"name": q["name"], "type": "predicate", "instructions": q["question"]} for q in questions]
        kwargs = {"model": self.model} if self.model else {}
        body = self.mod.decide(text, qs, **kwargs)
        if self.trace:
            self.trace.model_call(body.get("model") or self.model, "judge (vendor decision model): "
                                  + ", ".join(q["name"] for q in questions))
        by = {a.get("name"): a for a in body.get("answers") or []}
        out = {}
        for q in questions:
            a = by.get(q["name"]) or {}
            p = a.get("probability")
            if p is None or a.get("type") == "refusal":
                out[q["name"]] = Answer(None, None, "no answer")
            else:
                out[q["name"]] = Answer("yes" if float(p) >= self.threshold else "no", float(p), f"p={float(p):.2f}")
        return out


LOCAL_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string", "enum": ["yes", "no"]},
                                                  "evidence": {"type": "string"}}, "required": ["answer", "evidence"]}


class LocalDecisionBackend(DecisionBackend):
    """agentforge/run/decision.py pattern: one call per question, temperature 0, JSON {answer, evidence}."""
    name = "local"

    def __init__(self, call_llm, model: str, votes: int = 1, trace=None, deadline=None):
        self.call_llm, self.model, self.votes, self.trace, self.deadline = call_llm, model, max(1, votes), trace, deadline

    def ask(self, text, questions):
        out = {}
        for q in questions:
            yes = no = 0
            ev = ""
            for v in range(self.votes):
                r = self.call_llm(FRAME + " Answer one yes/no question. evidence: the words that decide it.",
                                  text + f"\n\n# Question\n{q['question']}", self.model, json_schema=LOCAL_SCHEMA,
                                  temperature=0, max_tokens=800, effort="low", trace=self.trace,
                                  purpose=f"judge question {q['name']} on {q.get('patch_id', '?')}"
                                  + (f" (vote {v + 1})" if self.votes > 1 else ""), deadline=self.deadline)
                a = str(r.get("answer", "")).lower()
                yes += a == "yes"
                no += a == "no"
                ev = ev or str(r.get("evidence", ""))[:300]
            ans = "yes" if yes > no else "no" if no > yes else None
            out[q["name"]] = Answer(ans, None, ev)
        return out


# --- the judge ---------------------------------------------------------------------------------------------------------

def _check(name, passed, detail="", kind="deterministic", critical=True, answer=None):
    return {"name": name, "kind": kind, "passed": bool(passed), "answer": answer, "evidence": detail, "critical": critical}


def _norm_num(s: str) -> str:
    try:
        return ("%f" % float(s.replace(",", "."))).rstrip("0").rstrip(".")
    except ValueError:
        return s


def known_numbers(*texts) -> set:
    out = set()
    for t in texts:
        for m in NUMBER.findall(t if isinstance(t, str) else json.dumps(t, ensure_ascii=False)):
            out.add(m)
            out.add(_norm_num(m))
    return out


def stage_of(zone_id: str) -> str:
    m = re.match(r"^(?:node|extract|tts|transition|global):([^/]+)", zone_id)
    return m.group(1) if m else ""


class PatchJudge:
    def __init__(self, backend: DecisionBackend | None, critical: dict | None = None, trace=None):
        self.backend, self.trace = backend, trace
        self.critical = critical or {}
        self.name = f"{type(self).__name__}({backend.name if backend else 'none'})"

    # deterministic -----------------------------------------------------------------------------------------------
    def deterministic(self, p: dict, ctx: JudgeContext) -> list[dict]:
        out = []
        shape = patches.shape_errors(p)
        out.append(_check("shape", not shape, "; ".join(shape)))
        if shape:
            return out
        z = ctx.book.zones.get(p["zone"])
        out.append(_check("zone_unlocked", z is not None and not z["locked"],
                          "unknown zone" if z is None else ("locked" if z["locked"] else "")))
        if z is None or z["locked"]:
            return out
        if p["op"] == "replace":
            n = z["text"].count(p["before"])
            out.append(_check("before_once", n == 1, f"found {n} time(s)"))
            if n != 1:
                return out
        text = patches.new_text(ctx.book, p)
        out.append(_check("zone_budget", not z.get("max_chars") or len(text) <= z["max_chars"],
                          f"{len(text)}/{z.get('max_chars')} chars"))
        try:
            wf2, book2 = zones.apply(ctx.workflow, ctx.book, p["zone"], text)
        except zones.ZoneError as e:
            out.append(_check("zone_apply", False, str(e)))
            return out
        size = zones.model_text_size(book2)
        base = ctx.base_size or zones.model_text_size(ctx.book)
        out.append(_check("growth_budget", size <= base * (1 + zones.MAX_GROWTH), f"{size} vs base {base} (+{zones.MAX_GROWTH:.0%})"))
        out.append(_check("quote_in_brief", policy_check.quote_in(p.get("brief_quote", ""), ctx.brief_text), p.get("brief_quote", "")[:120]))
        report = compiler.lint(wf2, ctx.policy, ctx.api) if ctx.api is not None else {"errors": []}
        out.append(_check("lint_clean", not report["errors"], "; ".join(e["msg"] for e in report["errors"][:3])))
        from builder.agentic.tools import lint as alint
        names, prefixes = alint.set_variables(wf2, ctx.policy.get("call_variables"))
        unknown = [v for v in alint.PLACEHOLDER.findall(p["after"]) if not alint._is_set(v, names, prefixes)]
        out.append(_check("no_unknown_vars", not unknown, ", ".join(unknown)))
        allowed = known_numbers(ctx.brief_text, ctx.policy, ctx.facts_text, z["text"])
        nums = [m for m in NUMBER.findall(p["after"]) if m not in allowed and _norm_num(m) not in allowed]
        out.append(_check("numbers_known", not nums, ", ".join(nums)))
        forbidden = []
        if stage_of(p["zone"]) in PRE_VERIFY_STAGES or p["zone"].startswith("system/"):
            low = p["after"].lower()
            forbidden = [w for w in ctx.policy.get("forbidden_before_verify") or []
                         if re.search(r"\b" + re.escape(w.lower()) + r"\b", low) and not re.search(r"\b" + re.escape(w.lower()) + r"\b", z["text"].lower())]
        out.append(_check("no_forbidden_pre_verification", not forbidden, ", ".join(forbidden)))
        leaked = sorted(t for t in ctx.case_tokens if t and len(str(t)) >= 3 and re.search(
            r"(?<![\w])" + re.escape(str(t)) + r"(?![\w])", p["after"], re.I))
        out.append(_check("no_case_tokens", not leaked, ", ".join(leaked)))
        return out

    def deterministic_policy(self, p: dict, ctx: JudgeContext) -> list[dict]:
        out = []
        shape = patches.policy_shape_errors(p)
        out.append(_check("shape", not shape, "; ".join(shape)))
        if shape:
            return out
        exists = policy_check.pointer_exists(ctx.policy, p["path"])
        out.append(_check("pointer_exists", exists, p["path"]))
        if not exists:
            return out
        new = patches.apply_policy(ctx.policy, p)
        schema = policy_check.validate_schema(new)
        out.append(_check("schema_valid", not schema, "; ".join(f"{e['path']}: {e['msg']}" for e in schema[:3])))
        if ctx.api is not None:
            before = {(v["path"], v["msg"]) for v in policy_check.check(ctx.policy, ctx.api, ctx.brief_text)}
            after = [v for v in policy_check.check(new, ctx.api, ctx.brief_text) if (v["path"], v["msg"]) not in before]
            out.append(_check("policy_check", not after, "; ".join(f"{v['path']}: {v['msg']}" for v in after[:3])))
        out.append(_check("quote_in_brief", policy_check.quote_in(p.get("brief_quote", ""), ctx.brief_text), p.get("brief_quote", "")[:120]))
        return out

    # decision ------------------------------------------------------------------------------------------------------
    def _context_text(self, p: dict, ctx: JudgeContext) -> str:
        z = ctx.book.zones.get(p["zone"], {}) if ctx.book else {}
        bugs = [ctx.bugs.get(b, {"bug_id": b}) for b in p.get("bug_ids") or []]
        ev = "\n".join(ctx.evidence_text.get(b, "") for b in p.get("bug_ids") or [])
        return (f"{FRAME}\n\n# Brief\n{ctx.brief_text}\n\n# The edit\nplace: {p['zone']} ({z.get('purpose', '')})\n"
                f"operation: {p['op']}\nbefore: {p.get('before', '')!r}\nafter: {p.get('after', '')!r}\n"
                f"brief quote given: {p.get('brief_quote', '')!r}\nrationale: {p.get('rationale', '')}\n\n"
                f"# Current text of that place\n{z.get('text', '') or '(empty)'}\n\n"
                f"# Bug\n{json.dumps(bugs, ensure_ascii=False, indent=1)}\n\n# Evidence turns\n{ev or '(none given)'}")

    def _policy_text(self, p: dict, ctx: JudgeContext) -> str:
        try:
            cur = policy_check.get_pointer(ctx.policy, p["path"])
        except Exception:
            cur = None
        return (f"{FRAME.replace('instructions of', 'extracted policy of')}\n\n# Brief\n{ctx.brief_text}\n\n"
                f"# The edit to policy.json\npath: {p['path']}\ncurrent value: {json.dumps(cur, ensure_ascii=False)}\n"
                f"new value: {json.dumps(p.get('new_value'), ensure_ascii=False)}\nbrief quote: {p.get('brief_quote', '')!r}\n"
                f"rationale: {p.get('rationale', '')}")

    def _decide(self, text: str, table: dict, pid: str, ctx: JudgeContext) -> list[dict]:
        qs = [{"name": k, "question": v[0], "patch_id": pid} for k, v in table.items()]
        answers = self.backend.ask(text, qs)
        out = []
        for k, (q, passing, crit) in table.items():
            a = answers.get(k) or Answer(None)
            critical = ctx.critical.get(k, self.critical.get(k, crit))
            out.append(_check(k, a.answer == passing, a.evidence, kind="decision", critical=critical, answer=a.answer))
        return out

    def _finish(self, kind: str, p: dict, checks: list, ctx: JudgeContext, inputs: dict) -> Verdict:
        bad = [c for c in checks if c["critical"] and not c["passed"]]
        v = Verdict(accepted=not bad, checks=checks, reasons=[f"{c['name']}: {c['evidence'] or c['answer']}" for c in bad])
        if self.trace:
            self.trace.line(f"judge {kind} patch {p.get('id')}: {'ACCEPT' if v.accepted else 'REJECT'} "
                            + ("; ".join(v.reasons)[:300] if v.reasons else ""))
            for c in checks:
                if c["kind"] == "deterministic":
                    self.trace.event("check", name=f"judge:{p.get('id')}:{c['name']}", ok=c["passed"], detail=c["evidence"])
        if ctx.log_path:
            rec = {"iteration": ctx.iteration, "kind": kind, "judge": self.name, "patch": p, "inputs": inputs,
                   "checks": checks, "accepted": v.accepted, "reasons": v.reasons}
            with Path(ctx.log_path).open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return v

    def judge(self, p: dict, ctx: JudgeContext) -> Verdict:
        checks = self.deterministic(p, ctx)
        inputs = {"zone_text": (ctx.book.zones.get(p.get("zone"), {}) or {}).get("text") if ctx.book else None,
                  "bugs": [ctx.bugs.get(b) for b in p.get("bug_ids") or []]}
        if all(c["passed"] for c in checks if c["critical"]):
            if self.backend is None:
                checks.append(_check("decision_model", False, "no decision model available", kind="decision"))
            else:
                checks += self._decide(self._context_text(p, ctx), PROMPT_QUESTIONS, p.get("id", "?"), ctx)
        return self._finish("prompt", p, checks, ctx, inputs)

    def judge_policy(self, p: dict, ctx: JudgeContext) -> Verdict:
        checks = self.deterministic_policy(p, ctx)
        inputs = {}
        try:
            inputs["current_value"] = policy_check.get_pointer(ctx.policy, p.get("path", ""))
        except Exception:
            inputs["current_value"] = None
        if all(c["passed"] for c in checks if c["critical"]):
            if self.backend is None:
                checks.append(_check("decision_model", False, "no decision model available", kind="decision"))
            else:
                checks += self._decide(self._policy_text(p, ctx), POLICY_QUESTIONS, p.get("id", "?"), ctx)
        return self._finish("policy", p, checks, ctx, inputs)


class ToprakDecisionJudge(PatchJudge):
    pass


class LocalDecisionJudge(PatchJudge):
    pass


def make_judge(models, trace, call_llm, deadline=None, vendor_path: Path = VENDOR) -> PatchJudge | None:
    """The vendor decision model when it loads and has a key; else LocalDecisionJudge (loud); else None."""
    # The vendor decision model talks to OpenAI / OpenRouter: it is used only when asked for (TCC_VENDOR_JUDGE=1) or when a vendor_path is passed
    # explicitly. The final path judges with the local decision model through claude_cli (no key, no OpenRouter credit, reproducible).
    mod = load_vendor(vendor_path) if (vendor_path != VENDOR or os.environ.get("TCC_VENDOR_JUDGE")) else None
    if mod is not None:
        try:
            ok = bool(mod.has_decision_key())
        except Exception:
            ok = False
        if ok:
            trace.line(f"judge: vendor decision model ({vendor_path.name}, threshold {models.judge_threshold})")
            return ToprakDecisionJudge(VendorDecisionBackend(mod, models.judge_threshold, models.judge_vendor_model, trace), trace=trace)
        trace.warn("judge: vendor decision model found but OPENAI_API_KEY/OPENROUTER_API_KEY is empty; using LocalDecisionJudge")
    else:
        trace.warn(f"judge: vendor decision model NOT available at {vendor_path}; using LocalDecisionJudge ({models.judge})")
    from builder.template_cc.llm import load_env, split_model
    load_env()
    provider, _ = split_model(models.judge)
    var = {"anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY", "claude_cli": None}.get(provider, "OPENAI_API_KEY")
    if var and not os.environ.get(var):   # claude_cli (the local Claude Code login) needs no key
        trace.warn("judge: no key for the local decision model either; patches will be logged, not applied")
        return None
    return LocalDecisionJudge(LocalDecisionBackend(call_llm, models.judge, models.judge_votes, trace, deadline), trace=trace)
