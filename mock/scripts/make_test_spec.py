"""Throwaway spec for the Worker test: tools/openapi.json + public-test-set.json (brief A). The real converter lives in mock/converter (S3)."""
import json, pathlib
R = pathlib.Path(__file__).resolve().parents[2]
oa = json.load(open(R / "tools/openapi.json"))
cases = [c for c in json.load(open(R / "public-test-set.json")) if c["brief"] == "A"]
eps = []
for path, v in oa["paths"].items():
    o = v["post"]
    eps.append({"op": path.strip("/"), "method": "POST", "path": path, "summary": o["summary"],
                "request": o["requestBody"]["content"]["application/json"]["schema"], "responses": o["responses"]})
keys = ["type", "card_last4", "past_due", "days_late", "minimum_payment", "instalment_amount"]
spec = {"tenant": "brief-a", "info": oa["info"], "base_path": "/v1",
        "identity": {"fields": [{"name": "date_of_birth", "kind": "date"}, {"name": "phone_last4", "kind": "digits", "len": 4}], "max_wrong": 3, "swap_hint": True},
        "products": {"id_prefix": "p", "map": {k: k for k in keys}},
        "rules": {"one_promise_per_product": True, "require_verified": ["get_account", "create_promise"]},
        "endpoints": eps,
        "customers": [{"customer_id": c["id"], "identity": c["identity"], "products": c["products"], "meta": {"title": c["title"], "expected": c.get("expected")}} for c in cases]}
json.dump(spec, open(R / "mock/test/fixtures/internal-brief-a.json", "w"), indent=1)
print(len(cases), "customers", len(eps), "endpoints")
