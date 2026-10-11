"""F10 extract_vote: majority merge, quote tie-break, vote labels, quote/value mismatches. Hand-made policies only."""

from builder.template_cc.extract_vote import flatten, merge, norm, quote_mismatches

BRIEF = """# Rules
| Days late | Band | Deadline | Attempts |
| 1 to 14 | **early** | the 10th business day | 1 |
Two wrong answers end the check. A callback is from `10:00` to `20:00`.
"""


def pol(max_wrong=2, start="10:00", attempts=1, src_cb="A callback is from `10:00` to `20:00`.", extra=None):
    p = {"identity": {"max_wrong": max_wrong, "questions": ["a", "b"]},
         "callback": {"start": start, "end": "20:00"},
         "bands": [{"name": "early", "min_days": 1, "max_days": 14, "deadline_business_days": 10, "attempts": attempts}],
         "_source": {"/identity/max_wrong": "Two wrong answers end the check.", "/callback": src_cb,
                     "/bands/0": "| 1 to 14 | **early** | the 10th business day | 1 |"}}
    if extra:
        p.update(extra)
    return p


def test_norm_strips_markdown():
    assert norm("| 1 to 14 | **early**  |\n`x`") == "1 to 14 early x"


def test_flatten_pointers():
    f = flatten(pol())
    assert f["/identity/max_wrong"] == 2 and f["/identity/questions"] == ["a", "b"]
    assert f["/bands/0/attempts"] == 1 and not any(k.startswith("/_source") for k in f)


def test_majority_wins_and_votes():
    merged, votes = merge([pol(), pol(max_wrong=3), pol()], BRIEF)
    assert merged["identity"]["max_wrong"] == 2
    assert votes["/identity/max_wrong"] == "2/3"
    assert votes["/callback/end"] == "3/3"
    assert merged["bands"][0]["attempts"] == 1


def test_three_way_split_takes_the_quoted_value():
    a = pol(start="09:00", src_cb="A callback is from 09:00 to 20:00.")  # quote not in the brief
    b = pol(start="10:00")                                                # quote in the brief (after normalising)
    c = pol(start="11:00", src_cb="callbacks at eleven")
    merged, votes = merge([a, b, c], BRIEF)
    assert votes["/callback/start"] == "split"
    assert merged["callback"]["start"] == "10:00"
    assert merged["_source"]["/callback"] == "A callback is from `10:00` to `20:00`."


def test_two_policies_tie_prefers_quote():
    a = pol(start="09:00", src_cb="not in the brief")
    b = pol(start="10:00")
    merged, votes = merge([a, b], BRIEF)
    assert votes["/callback/start"] == "split" and merged["callback"]["start"] == "10:00"


def test_absent_key_votes():
    merged, votes = merge([pol(extra={"x": 1}), pol(), pol()], BRIEF)
    assert "x" not in merged and votes["/x"] == "2/3"
    merged, _ = merge([pol(extra={"x": 1}), pol(extra={"x": 1}), pol()], BRIEF)
    assert merged["x"] == 1


def test_structure_conflict_is_one_vote():
    a = pol(extra={"close_rule": {"kind": "single"}})
    b = pol(extra={"close_rule": "single"})
    merged, votes = merge([a, b, a], BRIEF)
    assert merged["close_rule"] == {"kind": "single"} and votes["/close_rule"] == "2/3"


def test_quote_mismatches():
    good = pol()
    assert quote_mismatches(good) == []
    bad = pol(start="09:00")  # the quote says 10:00
    assert quote_mismatches(bad) == ["/callback"]
    bad2 = pol(attempts=2)     # the band quote says 1 attempt
    assert quote_mismatches(bad2) == ["/bands/0"]
    iso = {"holidays": ["2026-11-09"], "_source": {"/holidays": "bank holidays: 2026-11-09, 2026-11-23"}}
    assert quote_mismatches(iso) == ["/holidays"]
    text_field = {"attempt1": "propose the full amount", "_source": {"/attempt1": "on the 3rd day"}}
    assert quote_mismatches(text_field) == []  # a text value is not checked
