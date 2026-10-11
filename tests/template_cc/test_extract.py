"""extract(): two readings + merge + verifier + deterministic rules, with a mocked call_llm (no network)."""

import copy

from builder.template_cc import extract
from builder.template_cc.trace import NullTrace

QUOTE_OK = "Two wrong replies end the checks"


class Mock:
    def __init__(self, a, b=None, verifier=None, pass2=None):
        self.a, self.b, self.verifier, self.pass2 = a, b if b is not None else copy.deepcopy(a), verifier, pass2
        self.purposes = []
        self.users = {}

    def __call__(self, system, user, model, **kw):
        purpose = kw["purpose"]
        self.purposes.append(purpose)
        self.users[purpose] = user
        if "reading A" in purpose:
            return copy.deepcopy(self.a)
        if "reading B" in purpose:
            return copy.deepcopy(self.b)
        if "verifier" in purpose:
            return self.verifier(user)
        if "pass 2" in purpose:
            return copy.deepcopy(self.pass2 or self.a)
        return copy.deepcopy(self.pass2 or self.a)  # fix round

    def verifier_calls(self):
        return [p for p in self.purposes if "verifier" in p]


def run(mock, fx_api, fx_brief):
    t = NullTrace()
    r = extract.extract(fx_brief, fx_api, mock, "m", t)
    return r["policy"], t, mock


def base(fx_policy):
    p = copy.deepcopy(fx_policy)
    p["multi_product"], p["max_products"] = False, 1
    return p


def test_agreeing_readings_need_no_verifier(fx_policy, fx_api, fx_brief):
    _, t, mock = run(Mock(base(fx_policy)), fx_api, fx_brief)
    assert mock.verifier_calls() == []
    assert any("disagree (0 critical)" in l for l in t.lines)


def test_disagreement_takes_the_verifiers_value_when_its_quote_is_found(fx_policy, fx_api, fx_brief):
    a, b = base(fx_policy), base(fx_policy)
    a["identity"]["max_wrong"], b["identity"]["max_wrong"] = 3, 2
    mock = Mock(a, b, verifier=lambda u: {"answers": [{"pointer": "/identity/max_wrong", "value": 2, "quote": QUOTE_OK}]})
    policy, t, _ = run(mock, fx_api, fx_brief)
    assert policy["identity"]["max_wrong"] == 2
    assert any("/identity/max_wrong: verified" in l for l in t.lines)


def test_unfound_verifier_quote_falls_back_to_reading_a(fx_policy, fx_api, fx_brief):
    a, b = base(fx_policy), base(fx_policy)
    a["identity"]["max_wrong"], b["identity"]["max_wrong"] = 3, 2
    mock = Mock(a, b, verifier=lambda u: {"answers": [{"pointer": "/identity/max_wrong", "value": 2, "quote": "words that are not in the brief at all"}]},
                pass2=a)
    policy, t, _ = run(mock, fx_api, fx_brief)
    assert policy["identity"]["max_wrong"] == 3
    assert any("/identity/max_wrong: fallback to reading A" in l for l in t.lines)


def test_multi_product_claim_without_statement_is_overridden(fx_policy, fx_api, fx_brief):
    a = copy.deepcopy(fx_policy)
    a["multi_product"], a["max_products"] = True, 3
    policy, t, _ = run(Mock(a), fx_api, "A plain brief. Customers are late on one loan.\n" + fx_brief)
    assert policy["multi_product"] is False and policy["max_products"] == 1
    assert any("multi_product forced" in l for l in t.lines)


def test_multi_product_claim_with_a_statement_is_kept(fx_policy, fx_api, fx_brief):
    a = copy.deepcopy(fx_policy)
    a["multi_product"], a["max_products"] = True, 3
    brief = "A customer can be late on up to three products.\n" + fx_brief
    policy, _, _ = run(Mock(a), fx_api, brief)
    assert policy["multi_product"] is True and policy["max_products"] == 3


def test_band_number_not_in_its_quote_goes_to_the_fix_round(fx_policy, fx_api, fx_brief):
    a = base(fx_policy)
    a["bands"][0]["attempts"] = 7
    a.setdefault("_source", {})["/bands/0"] = "| 1 to 20 | first | 5 | 1 |"
    mock = Mock(a)
    _, _, mock = run(mock, fx_api, fx_brief)
    fixes = [p for p in mock.purposes if "fix round" in p]
    assert fixes and "/bands/0" in mock.users[fixes[0]]


def test_null_max_products_becomes_one_when_the_brief_states_one_product(fx_policy, fx_api, fx_brief):
    a = copy.deepcopy(fx_policy)
    a["multi_product"], a["max_products"] = False, None
    policy, _, _ = run(Mock(a), fx_api, "A plain brief. Customers are late on one loan.\n" + fx_brief)
    assert policy["multi_product"] is False and policy["max_products"] == 1
