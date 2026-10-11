"""HTTP stand-in for the statement services. Responses come from test-customers.json."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURE = json.loads(Path("/Users/toprakgundogdu/Downloads/test-environment/test-customers.json").read_text())
CUSTOMERS = {c["customerNo"]: c["services"] for c in FIXTURE["customers"]}


def _customer(body):
    number = str(body.get("customerNo") or body.get("customerNumber") or "").strip()
    return CUSTOMERS.get(number)


def _card_bucket(service, card_number):
    if not isinstance(service, dict):
        return None
    if card_number in service:
        return service[card_number]
    digits = "".join(ch for ch in str(card_number or "") if ch.isdigit())
    if len(digits) < 10:
        return None
    for key, value in service.items():
        if not isinstance(value, dict):
            continue
        key_digits = "".join(ch for ch in key if ch.isdigit())
        if key[:6] == digits[:6] and key[-4:] == digits[-4:]:
            return value
        if key_digits and key_digits == digits:
            return value
    return None


def unwrap(body):
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


SERVICES = [
    ("cards.list", "customer"),
    ("cards.balance", "card"),
    ("statement.week.get", "card"),
    ("statement.week.decide", "card"),
    ("statement.week.change", "card"),
    ("contact.email.get", "customer"),
    ("statement.pdf.send", "card"),
]


def openapi():
    paths = {}
    for service, kind in SERVICES:
        required = ["customerNo"] if kind == "customer" else ["cardNumber"]
        if service in ("statement.week.decide", "statement.week.change"):
            required = ["cardNumber", "newStatementWeek"]
        if service == "statement.pdf.send":
            required = ["customerNo", "cardNumber"]
        props = {name: {"type": "string"} for name in required}
        paths[f"/voice/v1/{service}"] = {"post": {
            "operationId": service,
            "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object",
                "required": ["data"],
                "properties": {"data": {"type": "object", "properties": props, "required": required}},
            }}}},
            "responses": {"200": {"description": "Fixture body"}},
        }}
    return {"openapi": "3.0.3", "info": {"title": "Statement services", "version": "trial"}, "paths": paths}


def _owner_by_card(service_name, card_number):
    for services in CUSTOMERS.values():
        if _card_bucket(services.get(service_name), card_number) is not None:
            return services
    return None


def lookup(path, body):
    body = unwrap(body)
    name = path.strip("/").split("/")[-1]
    services = _customer(body)
    if services is None and body.get("cardNumber"):
        services = _owner_by_card(name, body.get("cardNumber"))
    if services is None:
        return 404, {"result": {"messageText": "Müşteri bulunamadı.", "type": "WARNING"}}
    service = services.get(name)
    if service is None:
        return 404, {"result": {"messageText": "Servis yanıtı yok.", "type": "WARNING"}}
    if name in ("cards.list", "contact.email.get", "statement.pdf.send"):
        if not isinstance(service, dict) or "http" not in service:
            return 404, {"result": {"messageText": "Servis yanıtı yok.", "type": "WARNING"}}
        return int(service["http"]), service.get("response") or {"result": {"messageText": "Servis yanıtı yok.", "type": "WARNING"}}
    bucket = _card_bucket(service, body.get("cardNumber"))
    if bucket is None:
        return 404, {"result": {"messageText": "Kart bulunamadı.", "type": "WARNING"}}
    if name in ("statement.week.decide", "statement.week.change"):
        week = str(body.get("newStatementWeek") or "").strip()
        chosen = bucket.get(week) if isinstance(bucket, dict) else None
        if not isinstance(chosen, dict) or "http" not in chosen:
            return 404, {"result": {"messageText": "Hafta bulunamadı.", "type": "WARNING"}}
        return int(chosen["http"]), chosen.get("response") or {"result": {"messageText": "Servis yanıtı yok.", "type": "WARNING"}}
    if "http" not in bucket:
        return 404, {"result": {"messageText": "Servis yanıtı yok.", "type": "WARNING"}}
    return int(bucket["http"]), bucket.get("response") or {"result": {"messageText": "Servis yanıtı yok.", "type": "WARNING"}}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] == "/openapi.json":
            self._send(200, openapi())
            return
        self._send(404, {"result": {"messageText": "Yok."}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except Exception:
            body = {}
        status, payload = lookup(self.path, body)
        self._send(status, payload)
        flat = unwrap(body)
        print(status, self.path, flat.get("customerNo") or flat.get("customerNumber"), flat.get("cardNumber"), flat.get("newStatementWeek"), flush=True)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("mock on", args.port, flush=True)
    server.serve_forever()
