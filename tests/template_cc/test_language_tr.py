"""policy.language 'tr': every sentence the code speaks is Turkish; English stays byte-identical. Lines from the brief are untouched."""
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel
from builder.template_cc.compiler import i18n

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
FEATS = "f1,f2,f3,f4,f7,f8,f9,f11,f12,f13,f24_day_once"


def build(lang):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    pol["language"] = lang
    wf, _ = compiler.compile(pol, ApiModel.load(REPO / "tools" / "openapi.json"), features=FEATS)
    return wf, pol


def codes(wf):
    return json.dumps(wf, ensure_ascii=False)


def test_english_unchanged():
    wf, _ = build("en")
    text = codes(wf)
    assert "Which amount would you like to pay?" in text and "Pazartesi" not in text


def test_turkish_code_texts_and_dates():
    wf, pol = build("tr")
    text = codes(wf)
    for en, _ in i18n.TR:
        if en.startswith("'") or en.startswith("money"):
            assert en not in text, en
    assert "Pazartesi" in text and "Ocak" in text and "'Monday'" not in text
    assert "Always speak Turkish" in text


def test_unknown_language_is_unchanged():
    assert i18n.localize("'Goodbye.'", "xx") == "'Goodbye.'"


def test_default_lines_follow_the_language():
    from builder.template_cc import policy_check
    pol = {"language": "tr", "address": "{title} {last_name}", "sentences": {"opening": "Freya Bank'tan arıyorum"}}
    lines = policy_check.default_lines(pol)
    assert lines["right_party_question"]["text"] == "{title} {last_name} ile mi görüşüyorum?"
    assert lines["opening"]["text"].startswith("Merhaba, Freya Bank")
    assert lines["goodbye"]["text"].endswith("İyi günler.")
    en = policy_check.default_lines({"language": "en", "address": "Mr Reyes", "sentences": {}})
    assert en["goodbye"]["text"].endswith("Goodbye.")


def _spoken_amount_code(currency):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    pol["language"] = "tr"
    if currency:
        pol["currency"] = currency
    wf, _ = compiler.compile(pol, ApiModel.load(REPO / "tools" / "openapi.json"), features=FEATS)
    code = wf["nodes"]["promise_check"]["config"]["code"] if "config" in wf["nodes"]["promise_check"] else None
    return wf, code


def test_tr_amounts_comma_dot_and_currency():
    from builder.template_cc.compiler import snippets as S
    for cur, tail in (("TL", " TL"), ("₺", " ₺"), (None, "")):
        src = S.build("vars['x'] = msay(1234.5) + '|' + msay(162) + '|' + msay(1234567.891)", {"lang": "tr", "currency": cur})
        v = {}
        exec(src, {"vars": v})
        assert v["x"] == f"1.234,50{tail}|162,00{tail}|1.234.567,89{tail}", v["x"]


def test_english_keeps_plain_amounts():
    wf, _ = build("en")
    assert "msay(" not in codes(wf)
    wf, pol = build("tr")
    assert "msay(" in codes(wf)
    assert "currency" not in pol


def _run(body, uses, lang="tr"):
    from builder.template_cc.compiler import snippets as S
    v = {}
    exec(S.build(body, {"lang": lang}, uses), {"vars": v})
    return v


def test_turkish_speech_is_read_by_the_parsers():
    v = _run("vars['d'] = answer_from_spoken('date', 'on dört Mart bin dokuz yüz seksen sekiz')\n"
             "vars['y'] = answer_from_speech('year', 'bin dokuz yüz seksen sekiz')\n"
             "vars['n'] = digit_string('dört dört yedi bir')\n"
             "vars['a'] = amount_from_speech('yüz elli lira')\n"
             "vars['b'] = amount_from_speech('1.234,50 TL')\n"
             "vars['f'] = amount_from_speech('hepsini')\n"
             "vars['t'] = answer_from_speech('text', 'Bir İstanbul')\n"
             "vars['dd'] = date_from_speech('22 Ekim', '2026-10-11T10:00')\n"
             "vars['wd'] = date_from_speech('perşembe', '2026-10-11T10:00')",
             ("answer_from_spoken", "answer_from_speech", "digit_string", "amount_from_speech", "date_from_speech"))
    assert v["d"][1] and "1988-03-14" in str(v["d"][0]), v["d"]
    assert v["y"][0] == "1988", v["y"]
    assert v["n"] == "4471", v["n"]
    assert v["a"][0] == 150 and v["b"][0] == 1234.5, (v["a"], v["b"])
    assert v["f"][1] == "full"
    assert "1" not in v["t"][0] and v["t"][0].startswith("bir"), v["t"]  # text answers are never rewritten (no number words)
    assert v["dd"][0] == "2026-10-22" and v["wd"][0] == "2026-10-15", (v["dd"], v["wd"])


def test_english_parsers_are_untouched():
    from builder.template_cc.compiler import snippets as S
    assert "tr_norm" not in S.build("vars['a'] = amount_from_speech('fifty')", {}, ("amount_from_speech",))


def _read(lines):
    body = "".join(f"vars['r{i}'] = {l}\n" for i, l in enumerate(lines))
    return _run(body, ("answer_from_spoken", "answer_from_speech", "digit_string", "amount_from_speech", "date_from_speech"))


NOW = "'2026-10-11T10:00'"


def test_tr_suffixes_without_apostrophe():
    v = _read([f"date_from_speech('ayın on beşinde', {NOW})",
               f"date_from_speech('yirmisinde', {NOW})",
               f"date_from_speech('ayın otuzunda', {NOW})",
               f"date_from_speech('Kasımda ayın yirmisi', {NOW})",
               f"date_from_speech('Cumaya', {NOW})",
               f"date_from_speech('Perşembeye', {NOW})",
               f"date_from_speech('on dört Mart', {NOW})",
               f"date_from_speech('14 Martta', {NOW})",
               f"date_from_speech('Aralığında 3', {NOW})"])
    assert v["r0"][0] == "2026-10-15", v["r0"]
    assert v["r1"][0] == "2026-10-20", v["r1"]
    assert v["r2"][0] == "2026-10-30", v["r2"]
    assert v["r3"][0] == "2026-11-20", v["r3"]
    assert v["r4"][0] == "2026-10-16", v["r4"]
    assert v["r5"][0] == "2026-10-15", v["r5"]
    assert v["r6"][0] == "2027-03-14" or v["r6"][0] == "2026-03-14" or v["r6"][0].endswith("-03-14"), v["r6"]
    assert v["r7"][0].endswith("-03-14"), v["r7"]


def test_tr_ordinals():
    v = _read([f"date_from_speech('on dördüncü Mart', {NOW})",
               f"date_from_speech('ayın on beşi', {NOW})",
               f"date_from_speech('ayın 15\\'i', {NOW})",
               f"date_from_speech('ayın 22\\'sinde', {NOW})",
               f"date_from_speech('yirmi üçüncü Ekim', {NOW})",
               f"date_from_speech('birinci Aralık', {NOW})"])
    assert v["r0"][0].endswith("-03-14"), v["r0"]
    assert v["r1"][0] == "2026-10-15", v["r1"]
    assert v["r2"][0] == "2026-10-15", v["r2"]
    assert v["r3"][0] == "2026-10-22", v["r3"]
    assert v["r4"][0] == "2026-10-23", v["r4"]
    assert v["r5"][0].endswith("-12-01"), v["r5"]


def test_tr_half():
    v = _read(["amount_from_speech('yüz elli buçuk lira')", "amount_from_speech('yarım lira')", "amount_from_speech('iki bin buçuk')",
               "amount_from_speech('bir buçuk')"])
    assert v["r0"][0] == 150.5, v["r0"]
    assert v["r1"][0] == 0.5, v["r1"]
    assert v["r2"][0] == 2000.5, v["r2"]
    assert v["r3"][0] == 1.5, v["r3"]
