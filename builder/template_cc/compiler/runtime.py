"""Helpers that run INSIDE the Freya workflow (code nodes and hooks). Embedded as source by compiler/snippets.py.

Rules (docs/tools-and-code.md "Rules for snippets"): standard modules json, re, datetime only; no double braces
anywhere in this file; never raise to the platform (callers wrap in try/except); routing flags are strings; every
date rule starts from vars['now']. The parameter that carries the variable table is named `vars` on purpose, so the
workflow linter sees every literal key written.
"""
import datetime
import json
import re

WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December']
FALLBACK_KEY = {'nr_no_callback': 'nr_unclear', 'nr_callback': 'nr_unclear', 'nr_wrong_number': 'nr_unclear',
                'nr_deceased': 'nr_unclear', 'nv_declined': 'nv_failed', 'nv_failed': 'nr_unclear',
                'np_hardship': 'np_refused', 'np_dispute': 'np_refused', 'np_service_error': 'np_refused',
                'paid': 'np_refused', 'tr_support': 'np_hardship', 'tr_human': 'np_refused',
                'tr_restructuring': 'np_refused', 'np_refused': 'nr_unclear', 'promise': 'np_refused'}
STATUS_KEY = {'refused': 'np_refused', 'hardship': 'np_hardship', 'dispute': 'np_dispute', 'paid': 'paid',
              'service_error': 'np_service_error', 'open': 'np_refused'}


def parse_now(value):
    s = str(value or '').strip().replace(' ', 'T').rstrip('Z')
    for fmt, size in (('%Y-%m-%dT%H:%M:%S', 19), ('%Y-%m-%dT%H:%M', 16), ('%Y-%m-%d', 10)):
        try:
            return datetime.datetime.strptime(s[:size], fmt)
        except ValueError:
            pass
    raise ValueError('cannot read now: ' + s)


def hol_set(cfg):
    out = set()
    for h in cfg.get('holidays') or []:
        try:
            out.add(datetime.date.fromisoformat(str(h)[:10]))
        except ValueError:
            pass
    return out


def is_bd(d, hol):
    return d.weekday() < 5 and d not in hol


def next_bd_after(d, hol):
    x = d + datetime.timedelta(days=1)
    while not is_bd(x, hol):
        x = x + datetime.timedelta(days=1)
    return x


def bd_on_or_after(d, hol):
    return d if is_bd(d, hol) else next_bd_after(d, hol)


def add_bd(d, n, hol):
    x, k = d, 0
    while k < n:
        x = x + datetime.timedelta(days=1)
        if is_bd(x, hol):
            k += 1
    return x


def last_bd_of_week(d, hol):
    x = d + datetime.timedelta(days=4 - d.weekday())
    monday = d - datetime.timedelta(days=d.weekday())
    while not is_bd(x, hol) and x > monday:
        x = x - datetime.timedelta(days=1)
    return x


def attempt_date(rule, today, deadline, hol, cap):
    if rule in ('end_of_week_roll', 'end_of_week'):
        x = last_bd_of_week(today, hol)
        if rule == 'end_of_week_roll' and x <= today:
            x = last_bd_of_week(today + datetime.timedelta(days=7), hol)
        elif x < today or not is_bd(x, hol):
            x = bd_on_or_after(today, hol)
    elif rule == 'today':
        x = bd_on_or_after(today, hol)
    elif rule == 'next_business_day':
        x = next_bd_after(today, hol)
    else:
        x = deadline
    if cap and x > deadline:
        x = deadline
    return x


def spoken(d):
    return WEEKDAYS[d.weekday()] + ' ' + str(d.day) + ' ' + MONTHS[d.month - 1]


def spoken_full(d):
    return spoken(d) + ' (' + d.isoformat() + ')'


def clean_line(line, banned, fallback):
    """F22: a line spoken before identity is verified never holds a banned word; else the fixed fallback line."""
    low = ' ' + re.sub(r'[^a-z0-9]+', ' ', str(line).lower()) + ' '
    for w in banned or []:
        k = re.sub(r'[^a-z0-9]+', ' ', str(w).lower()).strip()
        if k and ' ' + k + ' ' in low:
            return fallback
    return line


def cb_window_line(allowed, today):
    """F22: the spoken form of the allowed callback times (cb_allowed is computed in setup, later than now)."""
    parts = []
    for a in allowed:
        d = datetime.date.fromisoformat(a['date'])
        rel = 'today' if d == today else ('tomorrow' if d == today + datetime.timedelta(days=1) else '')
        parts.append((rel + ', ' if rel else '') + spoken(d) + ', from ' + a['start'] + ' to ' + a['end'])
    return ('We can call back ' + ' or '.join(parts) + '.') if parts else ''


def cb_pick(day_text, time_text, allowed, today):
    """F22: (date, time, inside) from what the person said; inside = a whole allowed day/time pair, bounds included."""
    low = str(day_text or '').lower()
    d = to_iso(day_text, today) if str(day_text or '').strip() else ''
    if not d:
        if 'tomorrow' in low:
            d = (today + datetime.timedelta(days=1)).isoformat()
        elif 'today' in low or 'tonight' in low:
            d = today.isoformat()
    t = to_hhmm(time_text) if str(time_text or '').strip() else ''
    inside = bool(d and t) and any(a['date'] == d and a['start'] <= t <= a['end'] for a in allowed)
    return d, t, inside


def money(x):
    s = '%.2f' % float(x)
    return s[:-3] if s.endswith('.00') else s


def num(v):
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r'[^0-9.,\-]', '', str(v or ''))
    if s.count(',') and s.count('.'):
        s = s.replace(',', '')
    elif s.count(',') == 1 and len(s.split(',')[1]) == 2:
        s = s.replace(',', '.')
    else:
        s = s.replace(',', '')
    return float(s) if s not in ('', '.', '-') else 0.0


def truthy(v):
    return str(v).strip().lower() in ('true', '1', 'yes')


def as_list(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip().startswith('['):
        try:
            x = json.loads(v)
            return x if isinstance(x, list) else []
        except ValueError:
            return []
    return []


def jget(vars, key, default):
    v = vars.get(key)
    if isinstance(v, (dict, list)):
        return v
    try:
        x = json.loads(v) if isinstance(v, str) and v.strip() else None
    except ValueError:
        x = None
    return x if isinstance(x, type(default)) else default


def jput(vars, key, value):
    vars[key] = json.dumps(value)


def _flattened_items(vars, list_field):
    """Products as flattened keys: <prefix>.data.<list>.<i>.<field>."""
    rows = dict()
    pat = re.compile(r'^(?:get_account|fetch_account|fetch_account_retry)_response\.data\.' + re.escape(list_field) + r'\.(\d+)\.(\w+)$')
    for k in list(vars.keys()):
        m = pat.match(str(k))
        if m:
            rows.setdefault(int(m.group(1)), dict())[m.group(2)] = vars.get(k)
    return [rows[i] for i in sorted(rows)]


def raw_products(vars, cfg):
    lf = cfg.get('list_field') or 'products'
    for key in ('products_raw', 'get_account_response.data.' + lf, 'fetch_account_response.data.' + lf,
                'fetch_account_retry_response.data.' + lf):
        items = as_list(vars.get(key))
        if items:
            return [x for x in items if isinstance(x, dict)]
    return _flattened_items(vars, lf)


def band_for(cfg, days):
    bands = cfg.get('bands') or []
    for b in bands:
        if int(b['min_days']) <= days <= int(b['max_days']):
            return b
    if bands and days < int(bands[0]['min_days']):
        return bands[0]
    return bands[-1] if bands else dict(name='default', deadline_business_days=5, attempts=1)


def type_key(cfg, value):
    for k, t in (cfg.get('product_types') or dict()).items():
        if str(t.get('enum')) == str(value):
            return k
    return ''


def product_rows(vars, cfg):
    """Every product, in API order, with its computed facts."""
    now = parse_now(vars.get('now'))
    today = now.date()
    hol = hol_set(cfg)
    af = cfg.get('account_fields') or dict()
    plan = cfg.get('attempt_plan') or []
    out = []
    for i, p in enumerate(raw_products(vars, cfg)[: int(cfg.get('max_products') or 3)]):
        tk = type_key(cfg, p.get(af.get('type', 'type')))
        t = (cfg.get('product_types') or dict()).get(tk) or dict()
        pid = str(p.get(af.get('product_id', 'product_id')) or str(cfg.get('product_id_format') or 'p{n}').replace('{n}', str(i + 1)))
        last4 = str(p.get(t.get('last4_field') or '') or '')
        label = str(t.get('label') or 'account').replace('{last4}', last4)
        past_due = num(p.get(af.get('past_due', 'past_due')))
        days = int(num(p.get(af.get('days_late', 'days_late'))))
        floor = num(p.get(t.get('floor_field') or '')) if t.get('floor_field') in p else past_due
        if floor <= 0 or floor > past_due:
            floor = past_due
        b = band_for(cfg, days)
        deadline = add_bd(today, int(b.get('deadline_business_days', 5)), hol)
        attempts = int(b.get('attempts', 1))
        props = []
        for k in range(attempts):
            step = plan[k] if k < len(plan) else (plan[-1] if plan else dict(date='deadline', amount='floor'))
            d = attempt_date(step.get('date', 'deadline'), today, deadline, hol, step.get('cap_at_deadline', True))
            props.append(dict(date=d.isoformat(), spoken=spoken(d), amount=step.get('amount', 'full_or_floor')))
        out.append(dict(index=i + 1, pid=pid, label=label, past_due=past_due, days=days, floor=floor,
                        band=str(b.get('name', '')), deadline=deadline.isoformat(), deadline_spoken=spoken(deadline),
                        attempts=attempts, proposals=props))
    return out


def outcome_fields(cfg, key):
    om = cfg.get('outcome_map') or dict()
    seen = set()
    k = key
    while k and k not in seen:
        seen.add(k)
        v = om.get(k)
        if v:
            return k, v[0], v[1]
        k = FALLBACK_KEY.get(k)
    return key, '', None


def final_key(vars, cfg):
    tr = str(vars.get('transfer_key') or '')
    if tr and tr != 'none':
        return 'tr_' + tr
    if str(vars.get('end_key') or '') == 'np_service_error_hard':
        return 'np_service_error'
    if str(vars.get('phase') or '') != 'products':
        return str(vars.get('end_key') or 'nr_unclear')
    pst = jget(vars, 'pstate', dict())
    order = jget(vars, 'pids', [])
    statuses = [pst.get(p, 'open') for p in order]
    if not statuses:
        return str(vars.get('end_key') or 'np_refused')
    if 'promised' in statuses:
        return 'promise'
    statuses = ['refused' if s == 'open' else s for s in statuses]
    if not cfg.get('multi') or len(statuses) == 1:
        return STATUS_KEY.get(statuses[0], 'np_refused')
    if all(s == 'paid' for s in statuses):
        return 'paid'
    rule = cfg.get('close_rule') or dict()
    if rule.get('kind') == 'priority' and rule.get('order'):
        for k in rule['order']:
            if k in statuses:
                return STATUS_KEY.get(k, 'np_refused')
    for s in statuses:
        if s != 'paid':
            return STATUS_KEY.get(s, 'np_refused')
    return 'np_refused'


def refresh_close(vars, cfg):
    key, outcome, reason = outcome_fields(cfg, final_key(vars, cfg))
    vars['close_key'] = key
    vars['close_outcome'] = outcome or ''
    vars['close_reason'] = reason or ''
    vars['close_code'] = outcome or ''
    return key


def said_once(vars, line_id):
    """Once-rules in code ("once", "only once", "exactly once", "one sentence"): True the first time the line is due,
    False every time after. The flag is vars['said_<line_id>'] (the verifier reads the same key)."""
    key = 'said_' + str(line_id)
    if str(vars.get(key)) == '1':
        return False
    vars[key] = '1'
    return True


def with_aside(vars, text):
    """F18: the answer to a side question (vars['aside_line']) is said once, in front of the step's own line."""
    a = str(vars.get('aside_line') or '').strip()
    vars['aside_line'] = ''
    return (a + ' ' + str(text or '')).strip()


def open_pids(vars):
    pst = jget(vars, 'pstate', dict())
    return [p for p in jget(vars, 'pids', []) if pst.get(p, 'open') == 'open']


def set_status(vars, pid, status):
    pst = jget(vars, 'pstate', dict())
    if pid in jget(vars, 'pids', []) and (pst.get(pid) != 'promised' or status == 'promised'):
        pst[pid] = status  # a recorded promise is never downgraded
        jput(vars, 'pstate', pst)


def pick_products(vars, which):
    """Product ids an event concerns: '1'..'3' (order), 'all', or '' (the first open product)."""
    pids = jget(vars, 'pids', [])
    w = str(which or '').strip().lower()
    if w.isdigit() and 1 <= int(w) <= len(pids):
        return [pids[int(w) - 1]]
    if w == 'all':
        return open_pids(vars) or list(pids)
    op = open_pids(vars)
    return op[:1] if op else pids[:1]


def neg_flag(vars):
    vars['neg_state'] = 'done' if not open_pids(vars) else 'open'
    return vars['neg_state']


NUM_WORDS = {'zero': 0, 'oh': 0, 'one': 1, 'first': 1, 'two': 2, 'second': 2, 'three': 3, 'third': 3, 'four': 4,
             'fourth': 4, 'five': 5, 'fifth': 5, 'six': 6, 'sixth': 6, 'seven': 7, 'seventh': 7, 'eight': 8,
             'eighth': 8, 'nine': 9, 'ninth': 9, 'ten': 10, 'tenth': 10, 'eleven': 11, 'eleventh': 11, 'twelve': 12,
             'twelfth': 12, 'thirteen': 13, 'thirteenth': 13, 'fourteen': 14, 'fourteenth': 14, 'fifteen': 15,
             'fifteenth': 15, 'sixteen': 16, 'sixteenth': 16, 'seventeen': 17, 'seventeenth': 17, 'eighteen': 18,
             'eighteenth': 18, 'nineteen': 19, 'nineteenth': 19, 'twenty': 20, 'twentieth': 20, 'thirty': 30,
             'thirtieth': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}


def _number(vals):
    """A run of number-word values -> its digits: cardinals with hundred/thousand, or a spoken year (19 89)."""
    if 100 in vals or 1000 in vals:
        total, cur = 0, 0
        for v in vals:
            if v == 100:
                cur = (cur or 1) * 100
            elif v == 1000:
                total += (cur or 1) * 1000
                cur = 0
            else:
                cur += v
        return str(total + cur)
    groups, cur = [], 0
    for v in vals:
        if cur and not (cur >= 20 and cur % 10 == 0 and v < 10):
            groups.append(cur)
            cur = 0
        cur += v
    groups.append(cur)
    if len(groups) == 2 and 10 <= groups[0] <= 20 and groups[1] < 100:
        return str(groups[0] * 100 + groups[1])
    return ' '.join(str(g) for g in groups)


def words_to_digits(text):
    """'twenty-third of February nineteen eighty-nine' -> '23 of february 1989'. Digits stay as they are."""
    words = str(text or '').lower().replace('-', ' ').replace(',', ' , ').split()
    out, run = [], []
    for i, w in enumerate(words):
        if w in NUM_WORDS or w in ('hundred', 'thousand'):
            run.append(NUM_WORDS.get(w, 100 if w == 'hundred' else 1000))
        elif w == 'and' and run and i + 1 < len(words) and words[i + 1] in NUM_WORDS:
            continue
        else:
            if run:
                out.append(_number(run))
                run = []
            out.append(w)
    if run:
        out.append(_number(run))
    return ' '.join(out)


def to_iso(text, today):
    """A date said or written in many ways -> YYYY-MM-DD (day-first for numeric dates). '' when unreadable."""
    s = str(text or '').strip().lower()
    if not s:
        return ''
    if re.search(r'[a-z]', s) and not re.search(r'\d', s):
        s = words_to_digits(s)
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return ''
    m = re.search(r'\b(\d+)[./\-](\d+)(?:[./\-](\d+))?\b', s)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        try:
            x = datetime.date(y, mo, d)
        except ValueError:
            return ''
        if not m.group(3) and x < today:
            x = datetime.date(y + 1, mo, d)
        return x.isoformat()
    mi = -1
    for i, name in enumerate(MONTHS):
        if name.lower() in s or re.search(r'\b' + name.lower()[:3] + r'\b', s):
            mi = i
            break
    if mi >= 0:
        nums = [int(x) for x in re.findall(r'\d+', s)]
        day = next((n for n in nums if 1 <= n <= 31), 0)
        year = next((n for n in nums if n > 31), 0)
        if day:
            y = year or today.year
            try:
                x = datetime.date(y, mi + 1, day)
            except ValueError:
                return ''
            if not year and x < today:
                x = datetime.date(y + 1, mi + 1, day)
            return x.isoformat()
    return ''


def to_hhmm(text):
    s = str(text or '').strip().lower().replace('.', ':')
    m = re.search(r'(\d+)(?::(\d+))?\s*(am|pm|a\.m|p\.m)?', s)
    if not m:
        return ''
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    if m.group(2) is None and h >= 100:
        h, mi = h // 100, h % 100
    ap = m.group(3) or ''
    if ap.startswith('p') and h < 12:
        h += 12
    if ap.startswith('a') and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return ''
    return '%02d:%02d' % (h, mi)


def norm_answer(kind, text, today):
    s = str(text or '').strip()
    if kind == 'date':
        iso = to_iso(s, datetime.date(today.year, 1, 1))
        if iso:
            y = int(iso[:4])
            if y > today.year:  # a birth date has no future year; to_iso rolled it
                iso = str(y - 1) + iso[4:]
            return iso
        return s
    if kind == 'year':
        m = re.search(r'(1[89]|20)\d\d', s)
        return m.group(0) if m else re.sub(r'\D', '', s)
    if kind == 'digits':
        return re.sub(r'\D', '', s)
    if kind == 'code':
        return re.sub(r'\s+', ' ', s).upper()
    return re.sub(r'\s+', ' ', s)


def date_table(cfg, rows, today, days=21):
    hol = hol_set(cfg)
    lines = []
    for k in range(days + 1):
        d = today + datetime.timedelta(days=k)
        if is_bd(d, hol):
            ok = [r['pid'] for r in rows if d.isoformat() <= r['deadline']]
            tail = 'business day; allowed for ' + (', '.join(ok) if ok else 'no product (after every latest date)')
        else:
            tail = 'NOT a business day (next business day: ' + spoken_full(next_bd_after(d, hol)) + ')'
        lines.append('- ' + spoken_full(d) + ': ' + tail)
    return '\n'.join(lines)


def weekday_words(cfg, today):
    ww = cfg.get('weekday_words') or dict()
    monday = today - datetime.timedelta(days=today.weekday())
    out = []
    for i, name in enumerate(WEEKDAYS[:5]):
        this_d = monday + datetime.timedelta(days=i)
        if ww.get('this') == 'next_occurrence' and this_d < today:
            this_d = this_d + datetime.timedelta(days=7)
        nxt = monday + datetime.timedelta(days=7 + i)
        if ww.get('next') == 'next_occurrence':
            nxt = today + datetime.timedelta(days=(i - today.weekday()) % 7 or 7)
        out.append('"this ' + name + '" = ' + spoken_full(this_d) + '; "next ' + name + '" = ' + spoken_full(nxt))
    return '\n'.join(out)


# --- F1: complete security answers from speech (generic: the field's KIND comes from the tool API) -------------
ID_UNITS = {"zero": 0, "oh": 0, "o": 0, "nought": 0, "nil": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
         "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
ID_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
ID_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
       "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
       "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20, "thirtieth": 30}
ID_MONTHS = {'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12, 'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12, 'sept': 9}


def kind_from_description(text):
    t = (text or "").lower()
    if "yyyy-mm-dd" in t or "date" in t and "birth" in t:
        return "date"
    if re.search(r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b", text or ""):  # an example such as AB1 2CD: letters and digits, spaced
        return "alnum_code"
    m = re.search(r"(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+digits", t)
    if "year" in t and ("four digits" in t or "yyyy" in t):
        return "year"
    if m:
        w = m.group(1)
        n = int(w) if w.isdigit() else ID_UNITS.get(w, 0) or {"ten": 10}.get(w, 0)
        return "digits:%d" % n
    if "year" in t:
        return "year"
    return "text"


def _id_tokens(text):
    t = (text or "").lower().replace("-", " ")
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)
    return re.findall(r"[a-z]+|\d+", t)


def digit_string(text):
    """Spoken digits -> '0077'. Handles 'double', 'triple', 'oh', and spoken pairs like 'fifty twenty-three'."""
    out, toks, i = [], _id_tokens(text), 0
    while i < len(toks):
        w = toks[i]
        rep = {"double": 2, "triple": 3}.get(w)
        if rep and i + 1 < len(toks) and (toks[i + 1] in ID_UNITS or toks[i + 1].isdigit()):
            nxt = toks[i + 1]
            d = nxt if nxt.isdigit() else str(ID_UNITS[nxt])
            out.append(d * rep)
            i += 2
            continue
        if w.isdigit():
            out.append(w)
        elif w in ID_TENS:
            v = ID_TENS[w]
            if i + 1 < len(toks) and toks[i + 1] in ID_UNITS and 0 < ID_UNITS[toks[i + 1]] < 10:
                v += ID_UNITS[toks[i + 1]]
                i += 1
            out.append(str(v))
        elif w in ID_UNITS:
            out.append(str(ID_UNITS[w]))
        elif w == "hundred" and out:
            out[-1] = out[-1] + "00"
        i += 1
    return "".join(out)


def spoken_year(toks):
    """['nineteen','sixty','eight'] -> 1968; ['two','thousand','and','three'] -> 2003; ['1968'] -> 1968."""
    s = " ".join(toks)
    m = re.search(r"\b(1[89]\d\d|20\d\d)\b", s)
    if m:
        return int(m.group(1))
    m = re.search(r"two thousand(?: and)?(?: (\w+))?(?: (\w+))?", s)
    if m:
        rest = [x for x in (m.group(1), m.group(2)) if x]
        v = 0
        for x in rest:
            v += ID_TENS.get(x, 0) + ID_UNITS.get(x, 0)
        return 2000 + v
    # "nineteen sixty eight", "twenty twelve", "nineteen oh five"
    vals = []
    j = 0
    while j < len(toks):
        x = toks[j]
        if x in ID_TENS:
            v = ID_TENS[x]
            if j + 1 < len(toks) and toks[j + 1] in ID_UNITS and 0 < ID_UNITS[toks[j + 1]] < 10:
                v += ID_UNITS[toks[j + 1]]
                j += 1
            vals.append(v)
        elif x in ID_UNITS:
            vals.append(ID_UNITS[x])
        j += 1
    for k in range(len(vals) - 1):
        hi, lo = vals[k], vals[k + 1]
        if hi in (19, 20) and 0 <= lo <= 99:
            if lo < 10 and k + 2 < len(vals) and lo == 0:
                lo = vals[k + 2]
            return hi * 100 + lo
    return None


def parse_date(text):
    """Day-month-year in words or digits -> 'YYYY-MM-DD', or '' if any part is missing. Numeric = day, month, year."""
    raw = (text or "").lower()
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _id_iso(y, mo, d)
    m = re.search(r"\b(\d{1,2})[./ -](\d{1,2})[./ -](\d{2,4})\b", raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 1900 if y > 30 else 2000
        return _id_iso(y, mo, d)
    toks = _id_tokens(raw)
    month = next((ID_MONTHS[t] for t in toks if t in ID_MONTHS), None)
    if month is None:  # digits said one by one: day, month, year ("zero four zero one nineteen seventy six")
        ds = digit_string(raw)
        if len(ds) == 8:
            return _id_iso(int(ds[4:]), int(ds[2:4]), int(ds[:2]))
        return ""
    mi = next(i for i, t in enumerate(toks) if t in ID_MONTHS)
    day = None
    day_end = 0  # tokens of `after` taken by a day said after the month
    before, after = toks[:mi], toks[mi + 1:]
    for seq in (before, after[:3]):
        for i, t in enumerate(seq):
            if t.isdigit() and 1 <= int(t) <= 31 and len(t) <= 2:
                day = int(t)
                day_end = i + 1
            elif t in ID_ORD:
                v = ID_ORD[t]
                if t in ("twentieth", "thirtieth") and i + 1 < len(seq) and seq[i + 1] in ID_ORD and ID_ORD[seq[i + 1]] < 10:
                    v += ID_ORD[seq[i + 1]]
                day = v if day is None else day
                day_end = i + 1
            elif t in ID_TENS and i + 1 < len(seq) and seq[i + 1] in ID_ORD:
                day = ID_TENS[t] + ID_ORD[seq[i + 1]]
                day_end = i + 2
                break
            elif seq is before and i == len(seq) - 1 and (t in ID_UNITS or t in ID_TENS):
                v = ID_UNITS.get(t, 0) or ID_TENS.get(t, 0)
                if i > 0 and seq[i - 1] in ID_TENS and t in ID_UNITS:
                    v += ID_TENS[seq[i - 1]]
                if 1 <= v <= 31:
                    day = v
        if day:
            break
    year_toks = after
    if day is not None and day_end and not before_has_day(before, day):
        year_toks = after[day_end:]
    elif day is not None and after and (after[0].isdigit() and len(after[0]) <= 2 or after[0] in ID_ORD):
        year_toks = after[1:]
    year = spoken_year(year_toks)
    if day is None or year is None:
        return ""
    return _id_iso(year, month, day)


def before_has_day(before, day):
    """True when the day was said before the month ("the tenth of April"), so nothing after the month is a day word."""
    return any(t.isdigit() and int(t) == day and len(t) <= 2 for t in before) or any(
        t in ID_ORD or t in ID_UNITS or t in ID_TENS for t in before)


def _id_iso(y, mo, d):
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return ""


def parse_postcode(text):
    """Letters and digits said one by one or written -> 'AB12 3CD' when it has a full outward + inward part."""
    toks = _id_tokens(text)
    chars = []
    for t in toks:
        if t.isdigit():
            chars.append(t)
        elif t in ID_UNITS:
            chars.append(str(ID_UNITS[t]))
        elif t in ID_TENS:
            chars.append(str(ID_TENS[t]))
        elif t in ("double", "triple"):
            chars.append("*" + t)
        elif len(t) == 1:
            chars.append(t.upper())
        elif len(t) <= 4 and re.fullmatch(r"[a-z]{1,2}\d*[a-z]{0,2}", t):
            chars.append(t.upper())
        elif re.fullmatch(r"[a-z]+\d+[a-z]*|\d+[a-z]+", t):
            chars.append(t.upper())
    s, i = "", 0
    while i < len(chars):
        c = chars[i]
        if c.startswith("*") and i + 1 < len(chars):
            s += chars[i + 1] * (2 if c == "*double" else 3)
            i += 2
            continue
        s += c
        i += 1
    s = re.sub(r"[^A-Z0-9]", "", s)
    m = re.fullmatch(r"([A-Z]{1,2}\d[A-Z\d]?)(\d[A-Z]{2})", s)
    return (m.group(1) + " " + m.group(2)) if m else ""


ID_CORRECTION = r"\b(?:sorry|i mean|no wait|wait|actually|correction|rather|no,)\b"


def after_correction(text):
    """'1636, sorry, I mean 5023' -> ' 5023': the caller's last version counts."""
    parts = [p for p in re.split(ID_CORRECTION, (text or "").lower()) if re.search(r"\d|[a-z]", p or "")]
    return parts[-1] if parts else (text or "")


def answer_from_speech(kind, text):
    """Return (value, complete). Never send an incomplete value to the bank: it may count as a wrong answer."""
    v, ok = _id_answer(kind, after_correction(text))
    if not ok:
        v2, ok2 = _id_answer(kind, text)
        if ok2:
            return v2, ok2
    return v, ok


SPELLING = {'alpha': 'A', 'alfa': 'A', 'bravo': 'B', 'charlie': 'C', 'delta': 'D', 'echo': 'E', 'foxtrot': 'F', 'golf': 'G',
            'hotel': 'H', 'india': 'I', 'juliet': 'J', 'juliett': 'J', 'kilo': 'K', 'lima': 'L', 'mike': 'M', 'november': 'N',
            'oscar': 'O', 'papa': 'P', 'quebec': 'Q', 'romeo': 'R', 'sierra': 'S', 'tango': 'T', 'uniform': 'U', 'victor': 'V',
            'whiskey': 'W', 'whisky': 'W', 'xray': 'X', 'yankee': 'Y', 'zulu': 'Z'}
DIGIT_RUN = re.compile(r"\b(?:(?:zero|oh|nought|one|two|three|four|five|six|seven|eight|nine|\d)[ ,.-]+){3}"
                       r"(?:zero|oh|nought|one|two|three|four|five|six|seven|eight|nine|\d)\b", re.I)


def spoken_forms(kind, text):
    """F21: a spoken identity answer in the shapes the plain parsers miss. Four single digits in a row are a year
    ("one nine five nine" -> 1959) for dates and years; a spelling alphabet in a code is its letters ("mike golf" -> MG")."""
    t = str(text or '')
    if kind in ('date', 'year'):
        def year(m):
            ds = digit_string(m.group(0))
            return ds if len(ds) == 4 and 1880 <= int(ds) <= 2100 else m.group(0)
        t = DIGIT_RUN.sub(year, t)
    elif kind == 'alnum_code':
        t = ' '.join(SPELLING.get(w.lower(), w) for w in re.findall(r"[A-Za-z]+|\d+|[^\sA-Za-z\d]+", t))
    return t


MONTH_CARDINAL = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)[ ,]+"
                            r"((?:twenty|thirty)(?:[ -](?:one|two|three|four|five|six|seven|eight|nine))?|one|two|three|four|"
                            r"five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
                            r"eighteen|nineteen)\b", re.I)


def _day_digits(m):
    v = 0
    for w in re.split(r"[ -]", m.group(2).lower()):
        v += ID_TENS.get(w, 0) + (ID_UNITS.get(w, 0) if w in ID_UNITS else 0)
    return m.group(1) + ' ' + str(v) if 1 <= v <= 31 else m.group(0)


def answer_from_spoken(kind, text):
    """answer_from_speech after spoken_forms (F21). A date said as 'July sixteen' (a cardinal day after the month) is read
    when the plain reading is incomplete."""
    t = spoken_forms(kind, text)
    v, ok = answer_from_speech(kind, t)
    if not ok and kind == 'date':
        v2, ok2 = answer_from_speech(kind, MONTH_CARDINAL.sub(_day_digits, t))
        if ok2:
            return v2, ok2
    return v, ok


def _id_answer(kind, text):
    if kind == "date":
        v = parse_date(text)
        return v, bool(v)
    if kind == "alnum_code":
        v = parse_postcode(text)
        return v, bool(v)
    if kind == "year":
        y = spoken_year(_id_tokens(text))
        if y is None:
            ds = digit_string(text)
            y = int(ds) if len(ds) == 4 else None
        return (str(y), True) if y and 1880 <= y <= 2100 else ("", False)
    if kind.startswith("digits:"):
        n = int(kind.split(":")[1])
        ds = digit_string(text)
        if len(ds) == n:
            return ds, True
        # chatter with other numbers: take the last sentence or clause that holds exactly n digits
        for seg in reversed(re.split(r"[.!?;,]| it is | it's | its | is ", " " + (text or "").lower() + " ")):
            d2 = digit_string(seg)
            if len(d2) == n:
                return d2, True
        return ds, False
    return (text or "").strip(), bool((text or "").strip())


# --- F9: amounts and dates said in words (copied from research/number_input/amount_date.py; MONTHS/WEEKDAYS renamed AD_*) ---
UNITS = {"zero": 0, "nought": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
DIGIT_WORDS = {'zero': 0, 'nought': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'oh': 0, 'o': 0}
TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}
ORD = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5, 'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9, 'tenth': 10, 'eleventh': 11, 'twelfth': 12, 'thirteenth': 13, 'fourteenth': 14, 'fifteenth': 15, 'sixteenth': 16, 'seventeenth': 17, 'eighteenth': 18, 'nineteenth': 19, 'twentieth': 20, 'thirtieth': 30}
AD_MONTHS = {'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6, 'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12, 'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12, 'sept': 9}
AD_WEEKDAYS = {'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6, 'mon': 0, 'tue': 1, 'tues': 1, 'wed': 2, 'weds': 2, 'thu': 3, 'thur': 3, 'thurs': 3, 'fri': 4, 'sat': 5, 'sun': 6}
CURRENCY_WORDS = ("pounds", "pound", "quid", "dollars", "dollar", "bucks", "euros", "euro", "sterling")
CENT_WORDS = ("pence", "p", "cents", "cent", "penny", "pennies")
SYMBOLS = ("£", "$", "€")
NOT_MONEY_AFTER = ("day", "days", "week", "weeks", "month", "months", "year", "years", "kid", "kids", "child",
                   "children", "percent", "am", "pm", "oclock", "o", "hours", "hour", "minutes", "times", "st", "nd",
                   "rd", "th", "of")
FILLERS = ("uh", "um", "er", "erm", "like", "hmm")
FULL = re.compile(r"\b(?:full amount|full balance|full sum|the full|in full|all of it|the whole|whole amount|whole lot|"
                  r"the lot|everything|entire amount|entire balance|all of the|the total|total amount|clear it all|"
                  r"pay it all|all at once)\b")
CORRECTION = re.compile(r"\b(?:sorry|i mean|no wait|wait|actually|correction|rather|make that|scratch that|"
                        r"or rather)\b|\bno\s*,")
VAGUE_DATE = re.compile(r"\b(?:end of (?:the |this |next )?month|end of the week|next month|next week|sometime|some time|"
                        r"soon|later|when i (?:get|am) paid|payday|pay day|a few days|few weeks|beginning of|start of|"
                        r"middle of|mid month|not sure|no idea|whenever|asap|as soon as)\b")


def segments(text):
    """'300, sorry, I mean 350' -> ['300, ', ', ', ' 350']: the caller's last version is the last segment."""
    parts = CORRECTION.split((text or "").lower())
    return [p for p in parts if p and re.search(r"[a-z0-9]", p)] or [(text or "").lower()]


# --- amounts ----------------------------------------------------------------------------------------------------------

def _amount_tokens(text):
    t = (text or "").lower()
    for _ in range(3):
        t = re.sub(r"(\d),(\d{3})", r"\1\2", t)
    t = t.replace("-", " ").replace("o'clock", "oclock")
    return re.findall(r"\d+(?:\.\d+)?(?:st|nd|rd|th|k)?|[a-z]+|[£$€]", t)


def _num(tok):
    if re.fullmatch(r"\d+(?:\.\d+)?", tok or ""):
        return float(tok)
    return None


def _starts_number(toks, i):
    w = toks[i]
    if _num(w) is not None or re.fullmatch(r"\d+(?:\.\d+)?k", w):
        return True
    if w in UNITS or w in TENS:
        return True
    if w == "a" and i + 1 < len(toks) and toks[i + 1] in ("hundred", "thousand", "grand"):
        return True
    if w in SYMBOLS and i + 1 < len(toks) and (_starts_number(toks, i + 1)):
        return True
    return False


def _small(toks, k):
    """A number from 1 to 99 at toks[k] (words or digits) -> (value, next index) or (None, k)."""
    if k >= len(toks):
        return None, k
    w = toks[k]
    v = _num(w)
    if v is not None and v == int(v) and 0 < v < 100:
        return int(v), k + 1
    if w in TENS:
        v = TENS[w]
        if k + 1 < len(toks) and toks[k + 1] in UNITS and 0 < UNITS[toks[k + 1]] < 10:
            return v + UNITS[toks[k + 1]], k + 2
        return v, k + 1
    if w in UNITS and UNITS[w] > 0:
        return UNITS[w], k + 1
    return None, k


def _decimals(toks, k):
    """Digits after 'point': 'five' -> '5', 'five oh' -> '50', 'fifty' -> '50', '5' -> '5'."""
    ds = ""
    while k < len(toks):
        w = toks[k]
        if re.fullmatch(r"\d+", w):
            ds += w
        elif w in DIGIT_WORDS and DIGIT_WORDS[w] < 10:
            ds += str(DIGIT_WORDS[w])
        elif w in TENS and not ds:
            v = TENS[w]
            if k + 1 < len(toks) and toks[k + 1] in UNITS and 0 < UNITS[toks[k + 1]] < 10:
                v += UNITS[toks[k + 1]]
                k += 1
            ds += str(v)
        else:
            break
        k += 1
    return ds, k


def _parse_amount(toks, i):
    """One number phrase starting at toks[i] -> (value, end, complete, currency) or (None, end, False, False)."""
    n = len(toks)
    total, cur, dec = 0.0, 0.0, None
    last, currency, complete = None, False, True
    j = i
    if toks[j] in SYMBOLS:
        currency = True
        j += 1
    while j < n:
        w = toks[j]
        v = _num(w)
        if w.endswith("k") and _num(w[:-1]) is not None:
            if last is not None and last not in ("and",):
                break
            total += _num(w[:-1]) * 1000
            last = "thousand"
        elif v is not None:
            if last in ("num", "unit", "teen", "tens"):
                break
            cur += v
            last = "num"
        elif w in UNITS:
            u = UNITS[w]
            if last == "tens" and u < 10 and cur % 10 == 0:
                cur += u
                last = "unit2"
            elif last in ("unit", "teen") and u >= 10 and 0 < cur < 20 and total == 0:
                cur = cur * 100 + u  # 'two fifteen' = 215
                last = "unit2"
            elif last in ("num", "unit", "teen", "tens", "unit2"):
                break
            else:
                cur += u
                last = "teen" if u >= 10 else "unit"
        elif w in TENS:
            t = TENS[w]
            if last in ("unit", "teen") and 0 < cur < 20 and total == 0:
                cur = cur * 100 + t  # 'four fifty' = 450, 'twelve fifty' = 1250
                last = "tens"
            elif last in ("num", "tens", "unit2", "unit", "teen"):
                break
            else:
                cur += t
                last = "tens"
        elif w == "hundred":
            cur = (cur or 1) * 100
            last = "hundred"
        elif w in ("thousand", "grand"):
            total += (cur or 1) * 1000
            cur = 0
            last = "thousand"
        elif w == "a" and j + 1 < n and toks[j + 1] in ("hundred", "thousand", "grand"):
            pass
        elif w == "and":
            if j + 2 < n and toks[j + 1] == "a" and toks[j + 2] == "half" and last is not None:
                dec = 0.5
                j += 3
                break
            if j + 1 >= n or all(x in FILLERS or x == "and" for x in toks[j + 1:]):
                complete = False
                j += 1
                break
            if last in ("hundred", "thousand") and j + 1 < n and _starts_number(toks, j + 1):
                last = "and"
            else:
                break
        elif w == "point":
            ds, k = _decimals(toks, j + 1)
            if not ds:
                complete = False
                j += 1
                break
            dec = float("0." + ds)
            j = k
            break
        elif w in FILLERS and last is not None:
            pass
        else:
            break
        j += 1
    if last is None:
        return None, j, False, False
    value = total + cur
    if j < n and toks[j] in ORD:
        return None, j + 1, False, False  # 'twenty second' is a date
    if j < n and toks[j] in NOT_MONEY_AFTER:
        return None, j + 1, False, False
    if dec is not None:
        value += dec
    if j < n and toks[j] in CURRENCY_WORDS:
        currency = True
        j += 1
        k = j + 1 if j < n and toks[j] == "and" else j
        c, k2 = _small(toks, k)
        if c is not None and dec is None:
            value += c / 100.0
            j = k2
            if j < n and toks[j] in CENT_WORDS:
                j += 1
        elif k < n and toks[k] == "and" and k + 1 >= n:
            complete = False
    elif j < n and toks[j] in CENT_WORDS and dec is None and total == 0 and cur < 100:
        value = cur / 100.0
        currency = True
        j += 1
    return value, j, complete, currency


def _amount_in(seg):
    toks = _amount_tokens(seg)
    found, i = [], 0
    while i < len(toks):
        if _starts_number(toks, i):
            v, j, ok, cur = _parse_amount(toks, i)
            if v is not None:
                found.append((v, ok, cur))
            i = max(j, i + 1)
        else:
            i += 1
    if not found:
        return None
    with_cur = [f for f in found if f[2]]
    v, ok, _ = (with_cur or found)[-1]
    return round(v, 2), ok


def amount_from_speech(text):
    """Return (amount or None, complete). complete is 'full' when the caller means the whole amount."""
    try:
        for seg in reversed(segments(text)):
            r = _amount_in(seg)
            if r is not None:
                return r
            if FULL.search(seg):
                return None, "full"
        if FULL.search((text or "").lower()):
            return None, "full"
    except Exception:
        pass
    return None, False


# --- dates ------------------------------------------------------------------------------------------------------------

def _today(now_iso):
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", str(now_iso or ""))
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return datetime.date.today()


def _date_tokens(text):
    t = (text or "").lower().replace("-", " ")
    t = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1 #ord", t)
    return re.findall(r"#ord|[a-z]+|\d+", t)


def _day_at(toks, k, need_marker):
    """A day of month at toks[k] -> (day, next index) or (None, k). need_marker: only clear ordinals count."""
    if k >= len(toks):
        return None, k
    w = toks[k]
    nxt = toks[k + 1] if k + 1 < len(toks) else ""
    if re.fullmatch(r"\d{1,2}", w):
        d = int(w)
        if nxt == "#ord":
            return (d, k + 2) if 1 <= d <= 31 else (None, k)
        if need_marker:
            return None, k
        return (d, k + 1) if 1 <= d <= 31 else (None, k)
    if w in TENS and nxt in ORD and ORD[nxt] < 10:
        return TENS[w] + ORD[nxt], k + 2
    if w in ORD:
        return ORD[w], k + 1
    if need_marker:
        return None, k
    if w in TENS:
        v = TENS[w]
        if nxt in UNITS and 0 < UNITS[nxt] < 10:
            v += UNITS[nxt]
            return (v, k + 2) if v <= 31 else (None, k)
        return (v, k + 1) if v <= 31 else (None, k)
    if w in UNITS and UNITS[w] > 0:
        return UNITS[w], k + 1
    return None, k


def _year_at(toks, k):
    seq = [t for t in toks[k:k + 5] if t not in ("of", "the", "in", "and", "#ord")]
    if not seq:
        return None
    if re.fullmatch(r"(19|20)\d\d", seq[0]):
        return int(seq[0])
    if seq[0] == "two" and len(seq) > 1 and seq[1] == "thousand":
        rest = seq[2:]
        v, _ = _small(rest, 0)
        return 2000 + (v or 0)
    if seq[0] in ("twenty", "nineteen") and len(seq) > 1:
        hi = 20 if seq[0] == "twenty" else 19
        v, _ = _small(seq, 1)
        if v is not None and v >= 10:
            return hi * 100 + v
    return None


def _resolve(y, m, d, today):
    if y is not None:
        try:
            dt = datetime.date(y, m, d)
        except ValueError:
            return None, False
        return (dt.isoformat(), True) if dt >= today else (None, False)
    for yy in (today.year, today.year + 1):
        try:
            dt = datetime.date(yy, m, d)
        except ValueError:
            continue
        if dt >= today:
            return dt.isoformat(), True
    return None, False


def _next_day_of_month(d, today):
    y, m = today.year, today.month
    for _ in range(13):
        try:
            dt = datetime.date(y, m, d)
            if dt >= today:
                return dt.isoformat(), True
        except ValueError:
            pass
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return None, False


def _count_word(w):
    if re.fullmatch(r"\d+", w or ""):
        return int(w)
    if w in ("a", "an", "one"):
        return 1
    if w == "couple":
        return 2
    return UNITS.get(w) if w in UNITS else None


def _date(seg, today):
    t = seg.lower()
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return _resolve(int(m.group(1)), int(m.group(2)), int(m.group(3)), today)
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else None
        if y is not None and y < 100:
            y += 2000
        if mo > 12 and d <= 12:
            d, mo = mo, d
        if 1 <= mo <= 12:
            return _resolve(y, mo, d, today)
    toks = _date_tokens(t)
    words = set(toks)
    if "day" in words and "after" in words and "tomorrow" in words:
        return (today + datetime.timedelta(days=2)).isoformat(), True
    if "tomorrow" in words:
        return (today + datetime.timedelta(days=1)).isoformat(), True
    if "today" in words or "tonight" in words:
        return today.isoformat(), True
    # 'in two weeks', 'in 3 days', 'a week from today', 'in a fortnight'
    for i, w in enumerate(toks):
        if w not in ("day", "days", "week", "weeks", "fortnight") or i == 0:
            continue
        if not ("in" in toks[max(0, i - 4):i] or toks[i + 1:i + 2] in (["from"], ["time"])):
            continue
        if w == "fortnight":
            days = 14
        else:
            c = _count_word(toks[i - 1])
            if toks[i - 1] == "of" and i > 1 and toks[i - 2] == "couple":
                c = 2
            if not c:
                continue
            days = c * (7 if w.startswith("week") else 1)
        return (today + datetime.timedelta(days=days)).isoformat(), True
    # month name with a day next to it
    for i, w in enumerate(toks):
        if w not in AD_MONTHS:
            continue
        mo = AD_MONTHS[w]
        d, k = None, i + 1
        # '22nd of October', 'the twenty second of October', 'twenty two October'
        pre = toks[max(0, i - 5):i]
        while pre and pre[-1] in ("of", "the"):
            pre.pop()
        for start in range(max(0, len(pre) - 3), len(pre)):
            dd, end = _day_at(pre, start, False)
            if dd is not None and end == len(pre):
                d = dd
                break
        if d is None:
            k2 = i + 1
            while k2 < len(toks) and toks[k2] in ("the", "of"):
                k2 += 1
            dd, end = _day_at(toks, k2, False)
            if dd is not None:
                d, k = dd, end
        if d is None:
            if w == "may" or len(w) == 3:
                continue
            return None, False  # 'sometime in October': ask for the day
        return _resolve(_year_at(toks, k), mo, d, today)
    # a day of month alone: 'the twenty-second', 'on the 22nd'
    for i, w in enumerate(toks):
        marker = i > 0 and toks[i - 1] == "the"
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if marker and not (re.fullmatch(r"\d{1,2}", w) or w in ORD or (w in TENS and nxt in ORD)):
            continue  # 'the one I said' is not a day
        dd, _ = _day_at(toks, i, not marker)
        if dd is not None and (marker or (i + 1 < len(toks) and toks[i + 1] == "#ord")):
            return _next_day_of_month(dd, today)
    for w in toks:
        if w in AD_WEEKDAYS:
            delta = (AD_WEEKDAYS[w] - today.weekday()) % 7 or 7
            return (today + datetime.timedelta(days=delta)).isoformat(), True
    if VAGUE_DATE.search(t):
        return None, False
    return None


def date_from_speech(text, now_iso):
    """Return (ISO date or None, complete). None + False means: ask the caller for a specific day."""
    try:
        today = _today(now_iso)
        for seg in reversed(segments(text)):
            r = _date(seg, today)
            if r is not None:
                return r
    except Exception:
        pass
    return None, False


def slot_summary(vars, cfg):
    """F12: what is known and what is missing, in at most four short lines (written by every state-changing step)."""
    try:
        rows = jget(vars, 'products', [])
        pst = jget(vars, 'pstate', dict())
        agreed = jget(vars, 'agreed', dict())
        passed = jget(vars, 'id_passed', [])
        verified = str(vars.get('id_state') or '') == 'passed'
        known = 'Known: identity ' + ('verified' if verified else 'not verified yet')
        if rows:
            known += '; ' + '; '.join([str(r['label']) + ' ' + money(r['past_due']) + ' owed, ' + str(pst.get(r['pid'], 'open')) for r in rows])
        said = 'Customer said: amount ' + (str(vars.get('said_amount') or '') or '-') + ', date ' + (str(vars.get('said_date') or '') or '-')
        done = 'Agreed so far: ' + ('; '.join([str(k) + ' ' + str(v) for k, v in agreed.items()]) if agreed else 'nothing')
        if not verified:
            left = [q for q in cfg['identity']['questions'] if q not in passed]
            need = 'Still needed: the answer to ' + (left[0] if left else 'the security questions')
        else:
            op = [r for r in rows if pst.get(r['pid'], 'open') == 'open']
            need = 'Still needed: ' + (('an agreed amount and date for ' + str(op[0]['label'])) if op else 'nothing, close the call')
        return '\n'.join([known, said, done, need])
    except Exception:
        return ''
