import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { Bank, openapi } from "../src/engine.js";

const spec = JSON.parse(fs.readFileSync(new URL("./fixtures/internal-brief-a.json", import.meta.url)));
function mem() {
  const m = new Map();
  return { get: async (k) => (m.has(k) ? structuredClone(m.get(k)) : undefined), put: async (k, v) => void m.set(k, structuredClone(v)), delete: async (k) => void m.delete(k),
    list: async (p) => [...m.entries()].filter(([k]) => k.startsWith(p)).sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, v]) => [k, structuredClone(v)]) };
}
const cid = spec.customers[0].customer_id, id = spec.customers[0].identity;
const call = (b, p, body, h) => b.call("POST", "/v1/" + p, body, h);
const verified = async (b) => { await call(b, "verify_identity", { customer_id: cid, field: "date_of_birth", answer: id.date_of_birth }); return call(b, "verify_identity", { customer_id: cid, field: "phone_last4", answer: id.phone_last4 }); };

test("account refused until both questions pass, then returns products", async () => {
  const b = new Bank(spec, mem());
  assert.equal((await call(b, "get_account", { customer_id: cid })).json.error, "identity not verified");
  const r1 = await call(b, "verify_identity", { customer_id: cid, field: "date_of_birth", answer: id.date_of_birth });
  assert.deepEqual(r1.json, { ok: true, verified: true, wrong_answers_left: 3, all_verified: false });
  assert.equal((await verified(b)).json.all_verified, true);
  const a = (await call(b, "get_account", { customer_id: cid })).json;
  assert.equal(a.ok, true); assert.equal(a.products[0].product_id, "p1"); assert.equal(a.products[0].card_last4, spec.customers[0].products[0].card_last4);
});

test("three wrong answers in total end the check; unreadable date is not counted; swapped date gives hint", async () => {
  const b = new Bank(spec, mem());
  assert.equal((await call(b, "verify_identity", { customer_id: cid, field: "date_of_birth", answer: "March fourteenth" })).json.ok, false);
  const sw = (await call(b, "verify_identity", { customer_id: cid, field: "date_of_birth", answer: "1988-14-03" })); // invalid month -> unreadable, not counted
  assert.equal(sw.json.ok, false);
  const w1 = await call(b, "verify_identity", { customer_id: cid, field: "date_of_birth", answer: "1990-01-01" });
  assert.equal(w1.json.wrong_answers_left, 2);
  const h2 = await call(b, "verify_identity", { customer_id: cid, field: "phone_last4", answer: "0000" });
  assert.equal(h2.json.wrong_answers_left, 1);
  const h3 = await call(b, "verify_identity", { customer_id: cid, field: "phone_last4", answer: "1111" });
  assert.equal(h3.json.wrong_answers_left, 0);
  assert.equal((await call(b, "verify_identity", { customer_id: cid, field: "phone_last4", answer: id.phone_last4 })).json.error, "identity check is over");
});

test("day/month swap hint", async () => {
  const s = structuredClone(spec); s.customers[0].identity.date_of_birth = "1988-03-12";
  const b = new Bank(s, mem());
  const r = await call(b, "verify_identity", { customer_id: cid, field: "date_of_birth", answer: "1988-12-03" });
  assert.equal(r.json.verified, false); assert.equal(r.json.hint, "day_month_swapped"); assert.equal(r.json.wrong_answers_left, 3);   // not counted as wrong
});

test("promise, one per product, note, callback, transfer, close; nothing after close; ledger shape", async () => {
  const b = new Bank(spec, mem());
  await verified(b);
  const p = await call(b, "create_promise", { customer_id: cid, product_id: "p1", amount: 126, date: "2026-10-20" });
  assert.equal(p.json.ok, true); assert.match(p.json.promise_id, /^pr/);
  assert.equal((await call(b, "create_promise", { customer_id: cid, product_id: "p1", amount: 100, date: "2026-10-21" })).json.ok, false);
  assert.equal((await call(b, "create_promise", { customer_id: cid, product_id: "p9", amount: 100, date: "2026-10-21" })).json.error, "unknown product_id");
  assert.equal((await call(b, "add_note", { customer_id: cid, product_id: "p1", reason: "bogus" })).json.ok, true);
  assert.equal((await call(b, "add_note", { customer_id: cid, product_id: "p1", reason: "hardship", text: "lost job" })).json.ok, true);
    assert.equal((await call(b, "schedule_callback", { customer_id: cid, date: "2026-10-21", time: "10:30" })).json.ok, true);
  assert.equal((await call(b, "transfer_to_agent", { customer_id: cid, queue: "general" })).json.ok, true);
  assert.equal((await call(b, "close_call", { customer_id: cid, outcome: "promise" })).json.ok, true);
  assert.equal((await call(b, "get_account", { customer_id: cid })).json.error, "the call is already closed");
  const l = await b.scorerLedger(cid);
  assert.equal(l.outcome, "promise"); assert.equal(l.promises.length, 1); assert.equal(l.notes[1].reason, "hardship");
  assert.deepEqual(l.callbacks, [{ date: "2026-10-21", time: "10:30" }]); assert.equal(l.transfers[0].queue, "general");
  assert.equal(l.calls.at(-1).tool, "close_call");
});

test("unknown customer, state persists across Bank instances on the same store", async () => {
  const st = mem();
  assert.equal((await call(new Bank(spec, st), "get_account", { customer_id: "nope" })).json.error, "unknown customer");
  await verified(new Bank(spec, st));
  assert.equal((await call(new Bank(spec, st), "get_account", { customer_id: cid })).json.ok, true);
  assert.ok((await new Bank(spec, st).ledger()).length >= 4);
});

test("faults: 500 uses the contract error, is not in the scorer ledger, and the next call succeeds", async () => {
  const s = { ...spec, faults: [{ op: "schedule_callback", customer_id: null, times: 1 }] };
  const b = new Bank(s, mem());
  const ep = s.endpoints.find((e) => e.op === "schedule_callback");
  const a = await call(b, "schedule_callback", { customer_id: cid, date: "2026-10-21", time: "10:30" });
  assert.equal(a.status, 500);
  assert.equal(a.json.error, ep.responses["500"].description);
  const again = await call(b, "schedule_callback", { customer_id: cid, date: "2026-10-21", time: "10:30" });
  assert.equal(again.status, 200);
  const led = await b.scorerLedger(cid);
  assert.deepEqual(led.callbacks, [{ date: "2026-10-21", time: "10:30" }]);
  assert.equal(led.calls.some((c) => c.result?.error === a.json.error), false);
  assert.deepEqual(led.calls.map((c) => c.tool), ["schedule_callback"]);
  const forced = await call(b, "get_account", { customer_id: cid }, { "x-mock-fail": "1" });
  assert.equal(forced.status, 500);
  assert.equal(forced.json.error, s.endpoints.find((e) => e.op === "get_account").responses["500"].description);
});

test("missing spec parts degrade: no identity/products/rules blocks; param aliases; generic record + dispute", async () => {
  const mini = { tenant: "t", base_path: "", customers: [{ customer_id: "x", products: [{ type: "a" }] }],
    endpoints: [{ op: "dispute", path: "/d", method: "POST", params: { text: "details" }, request: { type: "object", properties: { customer_id: { type: "string" }, details: { type: "string" } }, required: ["customer_id", "details"] } },
      { op: "record", path: "/r", kind: "visit", request: { type: "object", properties: { customer_id: { type: "string" } }, required: ["customer_id"] } },
      { op: "get_account", path: "/a" }] };
  const b = new Bank(mini, mem());
  assert.equal((await b.call("POST", "/d", { customer_id: "x", details: "wrong fee" })).json.ok, true);
  assert.equal((await b.call("POST", "/d", { customer_id: "x" })).json.ok, false);
  assert.equal((await b.call("POST", "/r", { customer_id: "x", extra: 1 })).json.ok, true);
  const l = await b.scorerLedger("x");
  assert.equal(l.disputes[0].text, "wrong fee"); assert.equal(l.records.visit.length, 1);
  assert.equal((await b.call("POST", "/a", { customer_id: "x" })).json.ok, true);   // no identity block: no gate
  assert.equal(b.endpoint("POST", "/zzz"), undefined);
});

test("openapi is generated from the spec", () => {
  const o = openapi(spec, "https://w.example");
  assert.equal(o.servers[0].url, "https://w.example/brief-a/v1");
  assert.deepEqual(Object.keys(o.paths).sort(), spec.endpoints.map((e) => e.path).sort());
  assert.ok(o.paths["/verify_identity"].post.requestBody.content["application/json"].schema.required.includes("answer"));
});

import { adapt } from "../src/adapt.js";
const s3 = JSON.parse(fs.readFileSync(new URL("../specs/spec.brief-a.json", import.meta.url)));

test("converter spec is accepted as is: tenant, base path, field names, openapi served with our server url", async () => {
  const a = adapt(s3);
  assert.equal(a.tenant, "brief-a"); assert.equal(a.base_path, "/v1"); assert.equal(a.endpoints.length, 7);
  assert.deepEqual(a.identity.fields.map((f) => [f.name, f.kind]), [["date_of_birth", "date"], ["phone_last4", "digits"]]);
  assert.equal(adapt(a), a);   // internal shape passes through
  const o = openapi(a, "https://w.example");
  assert.equal(o.servers[0].url, "https://w.example/brief-a/v1"); assert.deepEqual(Object.keys(o.paths), Object.keys(s3.openapi.paths));
  const b = new Bank(a, mem()), c = s3.customers[0];
  const call2 = (p, body) => b.call("POST", "/v1/" + p, body);
  assert.equal((await call2("get_account", { customer_id: c.customer_id })).json.error, "identity not verified");
  await call2("verify_identity", { customer_id: c.customer_id, field: "date_of_birth", answer: c.identity.date_of_birth });
  assert.equal((await call2("verify_identity", { customer_id: c.customer_id, field: "phone_last4", answer: c.identity.phone_last4 })).json.all_verified, true);
  const acc = (await call2("get_account", { customer_id: c.customer_id })).json;
  assert.deepEqual(acc.products[0], { product_id: "p1", ...c.products[0] });   // the bank's own field names pass through
  assert.equal((await call2("create_promise", { customer_id: c.customer_id, product_id: "p1", amount: 126, date: "2026-10-20" })).json.promise_id, "pr1");
  await call2("schedule_callback", { customer_id: c.customer_id, date: "2026-10-21", time: "10:00" });
  await call2("close_call", { customer_id: c.customer_id, outcome: "promise" });
  const l = await b.scorerLedger(c.customer_id);
  assert.deepEqual([l.outcome, l.promises[0].type, l.callbacks[0].time], ["promise", "credit_card", "10:00"]);
});

test("converter spec variants: datetime callback, code close, cents, renamed fields", async () => {
  const v = structuredClone(s3);
  for (const it of Object.values(v.openapi.paths)) delete it.post.requestBody;   // the variant renames fields, so the copied schemas no longer apply
  v.config.tools.schedule_callback = { mode: "datetime", datetime: "when" };
  v.config.tools.close_call = { mode: "code", code: "disposition" };
  v.config.tools.create_promise = { product_id: "pid", amount: "cents", date: "on", amount_unit: "cents" };
  const b = new Bank(adapt(v), mem()), c = v.customers[0].customer_id, id = v.customers[0].identity;
  const k = (p, body) => b.call("POST", "/v1/" + p, body);
  await k("verify_identity", { customer_id: c, field: "date_of_birth", answer: id.date_of_birth }); await k("verify_identity", { customer_id: c, field: "phone_last4", answer: id.phone_last4 });
  await k("create_promise", { customer_id: c, pid: "p1", cents: 12600, on: "2026-10-20" });
  await k("schedule_callback", { customer_id: c, when: "2026-10-21T10:30:00" });
  await k("close_call", { customer_id: c, disposition: "10-PTP" });
  const l = await b.scorerLedger(c);
  assert.deepEqual([l.promises[0].amount, l.callbacks[0], l.outcome, l.reason], [126, { date: "2026-10-21", time: "10:30" }, "10-PTP", null]);
});

test("converter spec without optional config blocks still works", () => {
  const a = adapt({ id: "x", openapi: { info: {}, servers: [{ url: "https://h/x" }], paths: { "/get_account": { post: {} } } }, customers: [] });
  assert.equal(a.base_path, "/x"); assert.equal(a.identity.max_wrong, 3); assert.equal(a.endpoints[0].op, "get_account");
});

test("rehearsal-style spec: empty note_map and null amount_unit; any note reason is recorded verbatim; amounts work", async () => {
  const r = JSON.parse(fs.readFileSync(new URL("../specs/spec.rehearsal-tr.json", import.meta.url)));
  assert.deepEqual(r.config.note_map, {}); assert.equal(r.config.tools.create_promise.amount_unit, null);
  const a = adapt(r), b = new Bank(a, mem()), c = r.customers[0], k = (p, body) => b.call("POST", "/v1/" + p, body);
  await k("verify_identity", { customer_id: c.customer_id, field: "date_of_birth", answer: c.identity.date_of_birth });
  await k("verify_identity", { customer_id: c.customer_id, field: "phone_last4", answer: c.identity.phone_last4 });
  assert.equal((await k("add_note", { customer_id: c.customer_id, product_id: "p1", reason: "ödeme_sözü_yok", text: "x" })).json.ok, true);
  assert.equal((await k("create_promise", { customer_id: c.customer_id, product_id: "p1", amount: 126.5, date: "2026-10-20" })).json.ok, true);
  const l = await b.scorerLedger(c.customer_id);
  assert.equal(l.notes[0].reason, "ödeme_sözü_yok"); assert.equal(l.promises[0].amount, 126.5);
  const b2 = new Bank(a, mem()); const k2 = (p, body) => b2.call("POST", "/v1/" + p, body);
  await k2("verify_identity", { customer_id: c.customer_id, field: "date_of_birth", answer: c.identity.date_of_birth }); await k2("verify_identity", { customer_id: c.customer_id, field: "phone_last4", answer: c.identity.phone_last4 });
  await k2("create_promise", { customer_id: c.customer_id, product_id: "p1", amount: "400", date: "2026-10-20" });
  assert.equal((await b2.scorerLedger(c.customer_id)).promises[0].amount, 400);
});
