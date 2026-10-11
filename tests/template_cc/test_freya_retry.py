"""freya_retry: 5xx retry on GET/PUT only (3 tries), and the outage signature. No network."""

import types

import pytest

from builder.template_cc import freya_retry


def fake_module(fails, status="503"):
    calls = []

    def api(method, path, body=None, **params):
        calls.append(method)
        if len(calls) <= fails:
            raise RuntimeError(f"{method} {path} -> {status}: error code: {status}")
        return {"ok": True}
    m = types.SimpleNamespace(api=api)
    return m, calls


def test_get_is_retried_up_to_three_tries():
    m, calls = fake_module(2)
    pauses = []
    freya_retry.install(m, sleep=pauses.append)
    assert m.api("GET", "/x") == {"ok": True} and len(calls) == 3 and pauses == [10, 10]


def test_gives_up_after_three_tries_and_never_retries_post_or_4xx():
    m, calls = fake_module(5)
    freya_retry.install(m, sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        m.api("PUT", "/x", {})
    assert len(calls) == 3
    m, calls = fake_module(5)
    freya_retry.install(m, sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        m.api("POST", "/api/v2/simulations", {})
    assert len(calls) == 1
    m, calls = fake_module(5, status="404")
    freya_retry.install(m, sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        m.api("GET", "/x")
    assert len(calls) == 1


def test_install_twice_wraps_once():
    m, calls = fake_module(0)
    freya_retry.install(m)
    first = m.api
    freya_retry.install(m)
    assert m.api is first


def test_outage_signature():
    assert freya_retry.is_outage([{"tools": []}, {"tools": []}])
    assert not freya_retry.is_outage([{"tools": []}, {"tools": ["verify_identity"]}])
    assert not freya_retry.is_outage([])


def publish_module(first_fails, published_after_failure, second_fails=False):
    calls = []

    def api(method, path, body=None, **params):
        calls.append((method, path))
        if method == "POST" and path.endswith("/publish"):
            n = sum(1 for c in calls if c[0] == "POST")
            if (n == 1 and first_fails) or (n == 2 and second_fails):
                raise RuntimeError(f"POST {path} -> 504: error code: 504")
            return {"success": True}
        if method == "GET" and "/versions/workflow/" in path:
            return {"versions": [{"id": "v2", "versionNumber": 2, "isPublished": published_after_failure}]}
        raise AssertionError(path)
    return types.SimpleNamespace(api=api, workspace_id=lambda: "ws"), calls


def test_publish_5xx_but_already_published_is_not_repeated():
    m, calls = publish_module(True, True)
    freya_retry.install(m, sleep=lambda s: None)
    out = m.api("POST", "/api/v2/versions/workflow/w1/publish", {"versionId": "v2"})
    assert out["success"] and sum(1 for c in calls if c[0] == "POST") == 1 and any(c[0] == "GET" for c in calls)


def test_publish_5xx_and_not_published_is_repeated_once():
    m, calls = publish_module(True, False)
    freya_retry.install(m, sleep=lambda s: None)
    assert m.api("POST", "/api/v2/versions/workflow/w1/publish", {"versionId": "v2"}) == {"success": True}
    assert sum(1 for c in calls if c[0] == "POST") == 2
    m, calls = publish_module(True, False, second_fails=True)
    freya_retry.install(m, sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        m.api("POST", "/api/v2/versions/workflow/w1/publish", {"versionId": "v2"})
    assert sum(1 for c in calls if c[0] == "POST") == 2         # never a third


def test_other_posts_are_still_never_retried():
    m, calls = fake_module(5)
    freya_retry.install(m, sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        m.api("POST", "/api/v2/workflow", {})
    assert len(calls) == 1
