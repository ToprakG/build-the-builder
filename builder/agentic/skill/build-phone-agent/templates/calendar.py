# Business-day and money helpers to paste into a code node or hook.
#
# Standard library only (json, re, datetime: the modules verified on the platform). No double braces anywhere:
# the platform may template the code field. Paste the functions you need above your snippet, then call them inside
# try/except. Every date rule must start from vars['now'], never from the real clock.
#
#   import datetime, json, re
#   <helpers>
#   try:
#       now = parse_now(vars.get('now'))
#       hol = parse_holidays(['2031-03-14', '2031-04-02'])        # the brief's own holidays
#       deadline = add_business_days(now.date(), 6, hol)
#       vars['deadline_spoken'] = spoken_date(deadline)
#       vars['deadline_iso'] = deadline.isoformat()
#       vars['calendar_state'] = 'ok'
#   except Exception:
#       vars['calendar_state'] = 'error'
import datetime
import json
import re

WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october',
          'november', 'december']


def parse_now(value):
    """'2031-03-07T11:00', '2031-03-07 11:00:00', '2031-03-07T11:00:00Z' or '2031-03-07' -> datetime."""
    s = str(value or '').strip().replace(' ', 'T').rstrip('Z')
    for fmt, size in (('%Y-%m-%dT%H:%M:%S', 19), ('%Y-%m-%dT%H:%M', 16), ('%Y-%m-%d', 10)):
        try:
            return datetime.datetime.strptime(s[:size], fmt)
        except ValueError:
            pass
    raise ValueError('cannot read now: ' + s)


def parse_holidays(values):
    """ISO strings -> set of dates. Unreadable entries are skipped."""
    out = set()
    for v in values or []:
        try:
            out.add(datetime.date.fromisoformat(str(v)[:10]))
        except ValueError:
            pass
    return out


def is_business_day(d, holidays):
    return d.weekday() < 5 and d not in holidays


def next_business_day(d, holidays, include_today=False):
    """The first business day after d (or d itself when include_today and d is one)."""
    if include_today and is_business_day(d, holidays):
        return d
    d = d + datetime.timedelta(days=1)
    while not is_business_day(d, holidays):
        d = d + datetime.timedelta(days=1)
    return d


def add_business_days(d, n, holidays):
    """The n-th business day after d (d itself not counted). n=0 returns d."""
    for _ in range(int(n)):
        d = next_business_day(d, holidays)
    return d


def nth_business_day_after(d, n, holidays):
    return add_business_days(d, n, holidays)


def business_days_between(a, b, holidays):
    """Business days in (a, b]. 0 when b <= a."""
    count, d = 0, a
    while d < b:
        d = d + datetime.timedelta(days=1)
        if is_business_day(d, holidays):
            count += 1
    return count


def week_monday(d):
    return d - datetime.timedelta(days=d.weekday())


def last_business_day_of_week(d, holidays):
    """The last business day in d's Monday-Sunday week, or None when the week has none."""
    day = week_monday(d) + datetime.timedelta(days=4)
    while day >= week_monday(d):
        if is_business_day(day, holidays):
            return day
        day = day - datetime.timedelta(days=1)
    return None


def weekday_in_week(d, weekday_name, weeks_ahead=0):
    """The named weekday in d's week (weeks_ahead=0) or a later calendar week (1 = the following week)."""
    idx = WEEKDAYS.index(str(weekday_name).strip().lower())
    return week_monday(d) + datetime.timedelta(days=7 * int(weeks_ahead) + idx)


def first_business_day_on_or_after(d, holidays):
    return next_business_day(d, holidays, include_today=True)


def parse_date_words(text, today):
    """'2031-03-07', '07/03/2031' (day first), '7 March', 'March 7th' -> date, or None. The year defaults to the
    next occurrence on or after today. Weekday words are not handled here: use weekday_in_week."""
    s = str(text or '').strip().lower()
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r'\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b', s)
    if m:
        y = int(m.group(3))
        return datetime.date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
    for i, name in enumerate(MONTHS):
        m = re.search(r'\b(\d{1,2})(st|nd|rd|th)?\s+(of\s+)?' + name[:3] + r'[a-z]*\b', s) or \
            re.search(r'\b' + name[:3] + r'[a-z]*\s+(the\s+)?(\d{1,2})(st|nd|rd|th)?\b', s)
        if m:
            day = int(m.group(1) if m.group(1) and m.group(1).isdigit() else m.group(2))
            cand = datetime.date(today.year, i + 1, day)
            return cand if cand >= today else datetime.date(today.year + 1, i + 1, day)
    return None


ORDINAL_WORDS = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth',
                 'eleventh', 'twelfth', 'thirteenth', 'fourteenth', 'fifteenth', 'sixteenth', 'seventeenth',
                 'eighteenth', 'nineteenth', 'twentieth']


def ordinals_to_digits(text):
    """'the thirtieth', 'twenty-first', 'twenty first' -> '30th', '21th' (digits + th), so the date rules can read them."""
    s = str(text or '').lower()
    s = re.sub(r'\bthirty[\s-]+first\b', '31th', s)
    s = re.sub(r'\bthirtieth\b', '30th', s)
    for i, w in enumerate(ORDINAL_WORDS[:9], 1):
        s = re.sub(r'\btwenty[\s-]+' + w + r'\b', str(20 + i) + 'th', s)
    for i, w in enumerate(ORDINAL_WORDS, 1):
        s = re.sub(r'\b' + w + r'\b', str(i) + 'th', s)
    return s


def resolve_date_words(text, today, next_weekday_weeks=1):
    """The customer's date words -> date, or None when vague. Handles ISO and numeric dates, '7 March',
    'the thirtieth of October', 'Monday the 26th', 'the 26th', 'today', 'tomorrow', 'day after tomorrow', '<weekday>',
    'this <weekday>', 'next <weekday>'. An explicit day of the month always wins over a weekday name. A bare weekday
    is the next one on or after today. next_weekday_weeks sets what 'next <weekday>' means: 1 = that weekday in the
    following calendar week. Set it from the brief; if the brief defines it otherwise, change it."""
    s = ordinals_to_digits(str(text or '').strip().lower())
    if not s:
        return None
    explicit = parse_date_words(s, today)
    if explicit is not None:
        return explicit
    m = re.search(r'\b(\d{1,2})(st|nd|rd|th)\b', s)
    if m:  # a day of the month without a month: this month if still ahead, else next month
        day = int(m.group(1))
        for k in range(0, 3):
            y, mo = today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1
            try:
                cand = datetime.date(y, mo, day)
            except ValueError:
                continue
            if cand >= today:
                return cand
        return None
    if 'day after tomorrow' in s:
        return today + datetime.timedelta(days=2)
    if 'tomorrow' in s:
        return today + datetime.timedelta(days=1)
    if re.search(r'\btoday\b|\bnow\b', s):
        return today
    for i, name in enumerate(WEEKDAYS):
        if re.search(r'\b' + name + r'\b', s):
            if re.search(r'\bnext\s+' + name, s):
                return weekday_in_week(today, name, next_weekday_weeks)
            if re.search(r'\bthis\s+' + name, s):
                return weekday_in_week(today, name, 0)
            d = weekday_in_week(today, name, 0)
            return d if d >= today else d + datetime.timedelta(days=7)
    return parse_date_words(s, today)


def spoken_date(d):
    """'Friday 7 March'. Adapt the words to the brief's language."""
    return WEEKDAYS[d.weekday()].title() + ' ' + str(d.day) + ' ' + MONTHS[d.month - 1].title()


def parse_hhmm(text):
    m = re.match(r'^\s*(\d{1,2})[:.h]?(\d{2})?\s*$', str(text or ''))
    if not m:
        raise ValueError('cannot read time: ' + str(text))
    return datetime.time(int(m.group(1)), int(m.group(2) or 0))


def in_window(t, start, end):
    """start <= t <= end, both ends included. All datetime.time."""
    return start <= t <= end


def to_cents(amount):
    """Major units -> integer minor units. round(), never int(): 19.99 * 100 is 1998.999..."""
    return int(round(float(str(amount).replace(',', '')) * 100))


def from_cents(cents):
    return round(int(cents) / 100.0, 2)


def money(amount, symbol=''):
    """1234.5 -> '1,234.50' (with an optional symbol in front). 1234.0 -> '1,234'."""
    v = round(float(amount), 2)
    text = '{:,.0f}'.format(v) if v == int(v) else '{:,.2f}'.format(v)
    return symbol + text


def list_from_vars(vars, name, prefixes):
    """A list from an API result, wherever the platform put it. Seen in a practice run on 10 October: a node-level
    response_mapping of a list left the variable unset; the list existed only as flattened keys such as
    '<node id>_response.data.<field>.0.<key>'. Tries vars[name] first, then each flattened prefix
    (e.g. 'load_account_response.data.items' or 'get_account_response.data.items'), in index order."""
    found = as_list(vars.get(name)) if vars.get(name) not in (None, '') else []
    if found:
        return found
    for prefix in prefixes:
        rows = {}
        start = prefix + '.'
        for key, value in vars.items():
            if not str(key).startswith(start):
                continue
            rest = str(key)[len(start):]
            idx, _, field = rest.partition('.')
            if idx.isdigit():
                if field:
                    rows.setdefault(int(idx), {})[field] = value
                else:
                    rows[int(idx)] = value
        if rows:
            return [rows[i] for i in sorted(rows)]
        whole = vars.get(prefix)
        if whole not in (None, ''):
            got = as_list(whole)
            if got:
                return got
    return []


def as_list(value):
    """A mapped list may arrive as a list, a JSON string or a dict (tools-and-code.md): always return a list."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else []
