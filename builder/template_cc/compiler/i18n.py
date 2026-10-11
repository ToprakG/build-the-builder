"""Spoken texts built in code, per language. The workflow is written in English; for another language the generated
code and the fixed spoken lines go through `localize` (exact-substring pairs), so nothing is spoken in the wrong language.
Lines taken from the brief are never touched. Values (names, amounts, dates) are not part of any pair."""

from __future__ import annotations

SUPPORTED = ("tr",)

TR_WEEKDAYS = "['Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar']"
TR_MONTHS = ("['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', "
             "'Aralık']")

# (english, turkish): longest first at apply time. Placeholders in the English text are kept as they are.
TR = [
    # identity step
    ("'Thank you. ' + pr", "'Teşekkür ederim. ' + pr"),
    ("'Could you say that again? ' + pr", "'Rica etsem tekrar eder misiniz? ' + pr"),
    ("'Sorry, I could not read that. ' + pr", "'Pardon, bunu anlayamadım. ' + pr"),
    ("'The check did not go through. ' + pr", "'Doğrulama tamamlanamadı. ' + pr"),
    ("'That answer does not match. ' + pr", "'Bu yanıt eşleşmedi. ' + pr"),
    ("'Sorry, I did not catch that. '", "'Pardon, anlayamadım. '"),
    ("'Thank you. Could you give me the rest of that, please?'", "'Teşekkür ederim. Devamını da söyler misiniz?'"),
    ("'Let us try that once more. '", "'Bir kez daha deneyelim. '"),
    # closing and fallbacks
    ("'Goodbye.'", "'İyi günler.'"),
    ("I am sorry, I could not complete that because of a system problem.", "Özür dilerim, bir sistem sorunu nedeniyle bunu tamamlayamadım."),
    ("Thank you, we will call back then.", "Teşekkür ederim, o zaman sizi tekrar arayacağız."),
    ("I am connecting you with a colleague now.", "Şimdi sizi bir meslektaşımıza bağlıyorum."),
    ("I am connecting you with our support team now.", "Şimdi sizi destek ekibimize bağlıyorum."),
    ("Sorry for the trouble.", "Rahatsızlık için özür dilerim."),
    ("Could you tell me when {first_name} {last_name} can be reached?", "{first_name} {last_name} hangi zaman ulaşılabilir, söyler misiniz?"),
    ("Could you tell me when they can be reached?", "Hangi zaman ulaşılabilir olduklarını söyler misiniz?"),
    ("' Could you give me another time?'", "' Başka bir zaman söyler misiniz?'"),
    ("'Which day and time would suit?'", "'Hangi gün ve saat uygun olur?'"),
    ("'We can call back '", "'Şu zamanlarda tekrar arayabiliriz: '"),
    ("' or '.join(parts)", "' veya '.join(parts)"),
    ("rel = 'today' if d == today else ('tomorrow' if", "rel = 'bugün' if d == today else ('yarın' if"),
    ("', from ' + a['start'] + ' to ' + a['end']", "', ' + a['start'] + ' ile ' + a['end'] + ' arası'"),
    # promise check
    ("'On which day exactly would you like to pay it?'", "'Tam olarak hangi gün ödemek istersiniz?'"),
    ("'The date I can record is ' + spoken(datetime.date.fromisoformat(r['att1'])) + '. Could you pay on that date?'",
     "'Kaydedebileceğim tarih ' + spoken(datetime.date.fromisoformat(r['att1'])) + '. O tarihte ödeyebilir misiniz?'"),
    ("'The latest date I can record is ' + spoken(dl) + '. Could you pay on that date?'",
     "'Kaydedebileceğim en geç tarih ' + spoken(dl) + '. O tarihte ödeyebilir misiniz?'"),
    ("'Which amount would you like to pay?'", "'Hangi tutarı ödemek istersiniz?'"),
    ("'I cannot record less than ' + money(floor) + '. Could you pay that amount?'",
     "money(floor) + ' tutarından azını kaydedemiyorum. Bu tutarı ödeyebilir misiniz?'"),
    ("'The amount owed is ' + money(past) + ', so I cannot record more than that. Could you pay ' + money(past) + '?'",
     "'Borç tutarı ' + money(past) + ', bundan fazlasını kaydedemiyorum. ' + money(past) + ' ödeyebilir misiniz?'"),
    ("'I need a date from today on. The next business day is ' + spoken(bd_on_or_after(today, hol)) + '. Would that work?'",
     "'Bugünden itibaren bir tarih gerekiyor. Bir sonraki iş günü ' + spoken(bd_on_or_after(today, hol)) + '. Bu uygun olur mu?'"),
    ("'That day is not a business day. The next business day is ' + spoken(nb) + '. Would that work?'",
     "'O gün iş günü değil. Bir sonraki iş günü ' + spoken(nb) + '. Bu uygun olur mu?'"),
    ("vars['rb_text'] = money(amt) + ' on ' + spoken(d)", "vars['rb_text'] = spoken(d) + ' günü ' + money(amt)"),
    ("'So that is ' + money(amt) + ' on ' + spoken(d) + ' for ' + r['label'] + '. ' + str(CFG['lines'].get('confirm_question') or 'Shall I record that?')",
     "'Yani ' + r['label'] + ' için ' + spoken(d) + ' günü ' + money(amt) + '. ' + str(CFG['lines'].get('confirm_question') or 'Bunu kaydedeyim mi?')"),
    ("'I could not save that because of a technical problem.'", "'Teknik bir sorun nedeniyle bunu kaydedemedim.'"),
    # attempt ladder
    ("'Could you pay the minimum amount of ' + money(p['floor']) + ' on ' + pr['spoken'] + '? The rest would stay overdue.'",
     "pr['spoken'] + ' günü en az ' + money(p['floor']) + ' ödeyebilir misiniz? Kalanı gecikmiş olarak kalır.'"),
    ("'Could you pay the full amount of ' + money(p['past_due']) + ' on ' + pr['spoken'] + '?'",
     "pr['spoken'] + ' günü tutarın tamamını, ' + money(p['past_due']) + ', ödeyebilir misiniz?'"),
    # spoken debt statement
    ("'Your ' + r['label'] + ': ' + money(r['past_due']) + ' past due, ' + str(r['days']) + ' days late.'",
     "r['label'] + ': ' + money(r['past_due']) + ' gecikmiş, ' + str(r['days']) + ' gün gecikme.'"),
]


def localize(code: str, lang: str) -> str:
    """Code or data text -> the language's spoken texts. Unknown language: unchanged."""
    if lang not in SUPPORTED:
        return code
    for en, tr in sorted(TR, key=lambda p: -len(p[0])):
        code = code.replace(en, tr.replace("money(", "msay("))
    code = code.replace("WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']",
                        "WEEKDAYS = " + TR_WEEKDAYS)
    a = code.find("MONTHS = ['January'")
    if a >= 0:
        b = code.index("]", a) + 1
        code = code[:a] + "MONTHS = " + TR_MONTHS + code[b:]
    return code


SYSTEM_RULE = {"tr": "Always speak Turkish, in a natural and polite way, addressing the customer with the formal 'siz' form. "
                     "Say dates and amounts the Turkish way, for example '22 Ekim Perşembe'. Never answer in English."}


# Fallback lines, used only when the brief does not state the line (a stated line is always kept as it is).
TR_LINES = {
    "condolence": "Başınız sağ olsun.",
    "debt_intro": "Geciken bir ödeme nedeniyle arıyorum.",
    "first_ask": "Bu tutarı bugün ödeyebilir misiniz?",
    "first_ask_multi": "Bunların hepsini bugün ödeyebilir misiniz?",
    "confirm_question": "Onaylıyor musunuz?",
    "ai": "Ben dijital bir asistanım.",
    "scam": "Bu aramayı doğrulamak için bankayı arayabilirsiniz.",
    "failed_id": "Doğrulama olmadan devam edemiyorum.",
    "paid_reply": "Teşekkür ederim. Bir ödemenin hesapta görünmesi biraz zaman alabilir.",
    "change_promise": "Kaydedilmiş bir söz bu görüşmede değiştirilemez.",
    "cannot_record": "Üzgünüm, bunu kaydedemedim.",
    "no_info": "Bu bilgiye sahip değilim.",
    "id_reason": "Bu sorular hesabınızı korumak içindir.",
    "hardship_sympathy": "Bunu duyduğuma üzüldüm.",
    "hint_retry": "Rica etsem bir seferde bir parça olacak şekilde tekrar eder misiniz?",
    "goodbye": "Zaman ayırdığınız için teşekkür ederim. İyi günler.",
    "promise_confirmation": "Sözünüz kaydedildi.",
}
TR_LINES_ADDR = {"right_party_question": "{addr} ile mi görüşüyorum?", "right_party_question_none": "Hesap sahibi ile mi görüşüyorum?",
                 "opening": "Merhaba, {opening}", "opening_none": "Merhaba."}
TR_NO_DISCOUNT = "Tutarı düşüremem."
TR_HARDSHIP_OFFER = "Sizi destek ekibine bağlamamı ister misiniz?"

# Spoken amounts: decimal comma, thousands dot, the policy's own currency word after it (none when the brief names none).
# The facts the model reads and sends keep plain digits with a dot, so no tool argument is ever written in this form.
MSAY_SRC = """def msay(x):
    s = '%.2f' % float(x)
    whole, frac = s.split('.')
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    cur = str(CFG.get('currency') or '').strip()
    return '.'.join(groups) + ',' + frac + (' ' + cur if cur else '')
"""


# Customer speech in Turkish -> the words and digits the (English) spoken-answer parsers read. Applied only for language tr, at the entry of
# digit_string / answer_from_speech / answer_from_spoken / amount_from_speech / date_from_speech. Text answers are never changed.
TRNORM_SRC = r'''def tr_norm(text):
    t = str(text or '').replace('İ', 'i').replace('I', 'ı').lower()
    t = re.sub(r"(?<=\d)\.(?=\d{3}(?!\d))", '', t)
    t = re.sub(r"(?<=\d),(?=\d{1,2}(?!\d))", '.', t)
    t = re.sub(r"(?<=\w)['’][^\W\d_]+", '', t)
    units = {'sıfır': 0, 'bir': 1, 'iki': 2, 'üç': 3, 'dört': 4, 'beş': 5, 'altı': 6, 'yedi': 7, 'sekiz': 8, 'dokuz': 9}
    tens = {'on': 10, 'yirmi': 20, 'otuz': 30, 'kırk': 40, 'elli': 50, 'altmış': 60, 'yetmiş': 70, 'seksen': 80, 'doksan': 90}
    words = {'ocak': 'january', 'şubat': 'february', 'subat': 'february', 'mart': 'march', 'nisan': 'april', 'mayıs': 'may',
             'mayis': 'may', 'haziran': 'june', 'temmuz': 'july', 'ağustos': 'august', 'agustos': 'august', 'eylül': 'september',
             'eylul': 'september', 'ekim': 'october', 'kasım': 'november', 'kasim': 'november', 'aralık': 'december',
             'aralik': 'december', 'pazartesi': 'monday', 'salı': 'tuesday', 'çarşamba': 'wednesday',
             'perşembe': 'thursday', 'cuma': 'friday', 'cumartesi': 'saturday', 'pazar': 'sunday', 'bugün': 'today',
             'yarın': 'tomorrow', 'lira': 'pounds', 'tl': 'pounds', '₺': '£', 'kuruş': 'pence',
             'tamamı': 'the full amount', 'tamamını': 'the full amount', 'hepsini': 'the full amount',
             'haftaya': 'next week'}
    known = set(units) | set(tens) | set(words) | {'yüz', 'bin', 'milyon'}
    ords = ('inci', 'ıncı', 'uncu', 'üncü', 'nci', 'ncı', 'ncu', 'ncü')
    sufs = ('sinde', 'sında', 'sunda', 'sünde', 'inde', 'ında', 'unda', 'ünde', 'nde', 'nda', 'ini', 'ını', 'unu', 'ünü', 'ine',
            'ına', 'una', 'üne', 'nin', 'nın', 'nun', 'nün', 'den', 'dan', 'ten', 'tan', 'si', 'sı', 'su', 'sü', 'ye', 'ya',
            'yi', 'yı', 'yu', 'yü', 'de', 'da', 'te', 'ta', 'in', 'ın', 'un', 'ün', 'le', 'la', 'e', 'a', 'i', 'ı', 'u', 'ü')

    def stem(w):
        if w in known:
            return w, False
        # a suffixed number is a day ("yirmisinde")
        for suf, ordinal in [(o, True) for o in ords] + [(x, True) for x in sufs]:
            if w.endswith(suf) and len(w) > len(suf):
                st = w[:-len(suf)]
                for cand in (st, st[:-1] + 't' if st.endswith('d') else '', st[:-1] + 'k' if st.endswith('ğ') else ''):
                    if cand in known:
                        return cand, ordinal
        return w, False

    t = re.sub(r"(?<=\d)(?:inci|ıncı|uncu|üncü|nci|ncı|ncu|ncü|sinde|sında|sunda|sünde|inde|ında|unda|ünde|nde|nda|si|sı|su|sü|de|da|e|a|i|ı|u|ü)\b", '', t)
    out, total, grp, last, have, ordf, after_ay = [], 0, 0, '', False, False, False

    def osuf(n):
        return 'th' if 10 <= n % 100 <= 20 or n % 10 not in (1, 2, 3) else {1: 'st', 2: 'nd', 3: 'rd'}[n % 10]

    for w0 in re.findall(r"\d+(?:\.\d+)?|[^\W\d_]+|[^\w\s]", t):
        w, isord = stem(w0)
        kind = 'u' if w in units else 't' if w in tens else 'h' if w == 'yüz' else 'b' if w in ('bin', 'milyon') else ''
        if kind:
            if have and ((kind == 'u' and last == 'u') or (kind == 't' and last in ('u', 't'))):
                n = total + grp
                out.append(str(n) + (osuf(n) if ordf and n <= 31 else ''))
                total, grp, ordf = 0, 0, False
            if kind in ('u', 't'):
                grp += units.get(w, tens.get(w, 0))
            elif kind == 'h':
                grp = (grp or 1) * 100
            else:
                total += (grp or 1) * (1000 if w == 'bin' else 1000000)
                grp = 0
            have, last = True, kind
            ordf = ordf or isord or after_ay
            continue
        if have:
            n = total + grp
            out.append(str(n) + (osuf(n) if ordf and n <= 31 else ''))
            total, grp, have, last, ordf = 0, 0, False, '', False
        if w0 == 'buçuk' and out and re.fullmatch(r"\d+", out[-1]):
            out[-1] += '.5'
            continue
        if w0 in ('yarım', 'buçuk'):
            out.append('0.5')
            continue
        if after_ay and w0.isdigit() and int(w0) <= 31:
            out.append(w0 + osuf(int(w0)))
            after_ay = False
            continue
        after_ay = w0 in ('ayın', 'ayının')
        if after_ay:
            continue  # "ayın X" = day X of the month: the number carries the day mark, the word is dropped
        out.append(words.get(w, w0))
    if have:
        n = total + grp
        out.append(str(n) + (osuf(n) if ordf and n <= 31 else ''))
    return ' '.join(out)
'''


_ENTRY = [("def digit_string(text):\n", "text"), ("def amount_from_speech(text):\n", "text"),
          ("def date_from_speech(text, now_iso):\n", "text")]
_ENTRY_KIND = ["def answer_from_speech(kind, text):\n", "def answer_from_spoken(kind, text):\n"]


def inject_parsers(h: str) -> str:
    """tr: the parser entries read Turkish through tr_norm; the helper itself is added once."""
    used = False
    for head, _ in _ENTRY:
        if head in h:
            h, used = h.replace(head, head + "    text = tr_norm(text)\n"), True
    for head in _ENTRY_KIND:
        if head in h:
            h, used = h.replace(head, head + "    text = tr_norm(text) if kind != 'text' else text\n"), True
    return (h + "\n\n\n" + TRNORM_SRC) if used else h
