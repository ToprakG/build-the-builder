"""Is the phone agent's SPOKEN text Turkish? Heuristic check of a compiled workflow.json (no model, no Freya).

    python3 scripts/check_turkish.py <workflow.json> [--policy policy.json] [--show 40]       exit 0 = no English found in spoken places, 1 = English found
Spoken places: on_enter tts texts, the policy's fixed lines (when --policy is given), and string literals inside code nodes that look like sentences
(the code nodes assemble re-asks, read-backs, date/amount offers). A text is English when it has no Turkish letter and at least two English function words.
Prompt texts (task_message, system prompt) are not checked: the model speaks the language the system prompt asks for. Date words (weekday/month names)
and the currency in code-built sentences are reported separately as 'locale': they are English in the code helpers (runtime.spoken/money).
"""
import json
import re
import sys
from pathlib import Path

EN = set("the you your please could can and is are to for would with that this not we our will have has do did what which when where how from on at be it i me my a of in or if so no yes thank sorry hello goodbye".split())
TR_CHARS = set("çğıöşüÇĞİÖŞÜ")
TR = set("bir bu ve için lütfen sayın merhaba teşekkür ederim size sizin ile mi mı mu mü değil evet hayır tarih tutar ödeme borç hesap kimlik doğrulama".split())
LOCALE = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|January|February|March|April|May|June|July|August|September|October|November|December)\b")
LIT = re.compile(r"""(?<![\w])(?:'((?:[^'\\\n]|\\.){10,})'|"((?:[^"\\\n]|\\.){10,})")""")


def words(s):
    return re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]+", s)


def english(s):
    if any(c in TR_CHARS for c in s):
        return False
    w = [x.lower() for x in words(s)]
    return sum(x in EN for x in w) >= 2 and sum(x in TR for x in w) == 0


def code_of(node):
    for a in node.get("pre_actions") or []:
        p = ((a.get("function") or {}).get("parameters") or {}).get("properties", {}).get("code", {})
        if p.get("default"):
            yield p["default"]
    c = node.get("code")
    if isinstance(c, str):
        yield c


def main(argv):
    a = [x for x in argv if not x.startswith("--")]
    show = int(argv[argv.index("--show") + 1]) if "--show" in argv else 40
    pol = argv[argv.index("--policy") + 1] if "--policy" in argv else None
    a = [x for x in a if x != pol and not x.isdigit()]
    wf = json.loads(Path(a[0]).read_text())
    wf = wf.get("workflow", wf)
    found, locale = [], []
    for nid, n in wf["nodes"].items():
        t = (n.get("on_enter") or {}).get("text")
        if t and english(t):
            found.append((nid, "tts", t))
        for code in code_of(n):
            for m in LIT.finditer(code):
                s = (m.group(1) or m.group(2)).encode().decode("unicode_escape", errors="ignore") if "\\" in (m.group(1) or m.group(2)) else (m.group(1) or m.group(2))
                if "CFG" in s or s.startswith("{") or "import " in s:
                    continue
                if english(s):
                    found.append((nid, "code", s))
                elif LOCALE.search(s):
                    locale.append((nid, s))
    if pol:
        p = json.loads(Path(pol).read_text())
        for k, v in (p.get("lines") or {}).items():
            t = v.get("text") if isinstance(v, dict) else v
            if isinstance(t, str) and english(t):
                found.append(("policy.lines", k, t))
        for k, t in (p.get("sentences") or {}).items():
            if isinstance(t, str) and english(t):
                found.append(("policy.sentences", k, t))
        print(f"policy language: {p.get('language')!r}")
    uniq = {}
    for nid, kind, s in found:
        uniq.setdefault(s, []).append(nid)
    print(f"English spoken texts: {len(uniq)} distinct ({len(found)} places); locale words (English weekday/month) in code: {len(locale)}")
    for s, nids in list(uniq.items())[:show]:
        print(f"  EN  {sorted(set(nids))[:3]}  {s[:110]!r}")
    for nid, s in locale[:5]:
        print(f"  LOC {nid}  {s[:110]!r}")
    return 1 if uniq else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
