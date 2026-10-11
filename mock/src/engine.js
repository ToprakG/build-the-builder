// Pure bank-mock logic. `store` is {get(k), put(k,v), list(prefix)->[[k,v]]} (async); the Worker backs it with Durable Object storage.
const pad = (n) => String(n).padStart(8, "0");
const MONTHS = ["january","february","march","april","may","june","july","august","september","october","november","december"];

export function normDate(s) {
  if (typeof s !== "string") return null;
  s = s.trim();
  let m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;                       // the contract: YYYY-MM-DD only; words are converted by the agent
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  if (d.getUTCFullYear() !== +m[1] || d.getUTCMonth() !== +m[2] - 1 || d.getUTCDate() !== +m[3]) return null;
  return s;
}
const swapDate = (iso) => { const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/); return m && +m[3] <= 12 ? `${m[1]}-${m[3]}-${m[2]}` : null; };
export const isTime = (s) => typeof s === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(s);

const KIT_FAULT = "The service failed. Nothing was recorded. The call may be repeated.";

function faultText(ep) {
  const text = ep?.responses?.["500"]?.description;
  return typeof text === "string" && text.trim() ? text.trim() : KIT_FAULT;
}

// lenient schema check: required fields only (types/formats are the agent's business; the service does not enforce the brief). Returns an error string or null.
export function validate(body, schema) {
  if (!schema || !schema.properties) return null;
  for (const k of schema.required || []) if (body[k] === undefined || body[k] === null || body[k] === "") return `missing ${k}`;
  for (const [k, p] of Object.entries(schema.properties)) {
    const v = body[k]; if (v === undefined || v === null) continue;
    void p;   // enums are not enforced: reasons/outcomes/queues are recorded verbatim
  }
  return null;
}

export class Bank {
  constructor(spec, store, now = () => new Date()) { this.spec = spec; this.store = store; this.now = now; }

  customers() { return this.spec.customers || []; }
  customer(id) { return this.customers().find((c) => c.customer_id === id); }
  endpoint(method, path) {
    const base = this.spec.base_path || "";
    return (this.spec.endpoints || []).find((e) => (e.method || "POST").toUpperCase() === method && base + e.path === path);
  }
  async state(cid) {
    return (await this.store.get("C:" + cid)) || { verified: {}, wrong: 0, closed: false, outcome: null, reason: null, promises: [], notes: [], callbacks: [], transfers: [], disputes: [], records: {}, faults_used: {} };
  }
  async log(entry) {
    const seq = ((await this.store.get("seq")) || 0) + 1;
    await this.store.put("seq", seq);
    const e = { seq, ts: this.now().toISOString(), ...entry };
    await this.store.put("L:" + pad(seq), e);
    return e;
  }
  async ledger({ customer_id, limit } = {}) {
    let rows = (await this.store.list("L:")).map(([, v]) => v);
    if (customer_id) rows = rows.filter((r) => r.customer_id === customer_id);
    return limit ? rows.slice(-limit) : rows;
  }
  async scorerLedger(cid) {
    const st = await this.state(cid);
    const calls = (await this.ledger({ customer_id: cid })).filter((r) => !r.fault).map((r) => ({ seq: r.seq, ts: r.ts, tool: r.tool, args: r.args, result: r.result }));
    return { customer_id: cid, outcome: st.outcome, reason: st.reason, closed: st.closed, promises: st.promises, notes: st.notes, callbacks: st.callbacks, transfers: st.transfers, disputes: st.disputes, records: st.records, calls };
  }
  async reset(cid) {
    for (const [k] of await this.store.list("C:")) if (!cid || k === "C:" + cid) await this.store.delete(k);
    for (const [k, v] of await this.store.list("L:")) if (!cid || v.customer_id === cid) await this.store.delete(k);
    for (const [k] of await this.store.list("F:")) if (!cid || k.startsWith("F:" + cid + ":")) await this.store.delete(k);
  }

  // Handle one tool call. Returns {status, json}.
  async call(method, path, body, headers = {}) {
    const ep = this.endpoint(method, path);
    if (!ep) return null;
    const op = ep.op || "record";
    const pm = ep.params || {};
    const arg = (n) => body[pm[n] || n];
    const cid = arg("customer_id");
    if (typeof body !== "object" || body === null || Array.isArray(body)) return { status: 400, json: { ok: false, error: "body must be a JSON object" } };

    // failure injection: nothing is recorded in the ledger/state. The next call of the same tool succeeds.
    if (headers["x-mock-fail"] === "1" || await this.consumeFault(op, cid)) {
      const error = faultText(ep);
      await this.log({ customer_id: cid ?? null, tool: op, args: body, result: { ok: false, error }, fault: true });
      return { status: 500, json: { ok: false, error } };
    }
    const done = async (result, st) => {
      if (st) await this.store.put("C:" + cid, st);
      await this.log({ customer_id: cid ?? null, tool: op, args: body, result });
      return { status: 200, json: result };
    };
    const bad = (error) => done({ ok: false, error });

    const err = validate(this.canon(body, pm), ep.request ? this.canonSchema(ep.request, pm) : null);
    if (err) return bad("invalid request: " + err);
    const c = this.customer(cid);
    if (!c) return bad("unknown customer");
    const st = await this.state(cid);
    if (st.closed) { await this.log({ customer_id: cid, tool: op, args: body, result: { ok: false, error: "the call is already closed" }, fault: true, late: true }); return { status: 200, json: { ok: false, error: "the call is already closed" } }; }   // kept out of the call list: nothing is accepted after close_call
    const need = (this.spec.rules?.require_verified ?? ["get_account", "create_promise"]).includes(op);
    const allOk = () => (this.spec.identity?.fields || []).every((f) => st.verified[f.name]);
    if (need && !allOk()) return bad("identity not verified");

    switch (op) {
      case "verify_identity": return this.verify(c, st, arg("field"), arg("answer"), done, bad);
      case "get_account": {
        const pmap = this.spec.products?.map, idf = this.idField();
        const products = this.products(c).map((p) => { if (!pmap) return p; const o = { [idf]: p[idf] }; for (const [k, src] of Object.entries(pmap)) if (p[src] !== undefined) o[k] = p[src]; return o; });
        return done({ ok: true, products }, st);
      }
      case "create_promise": {
        const pid = arg("product_id"), amount = arg("amount"), date = arg("date");
        if (!this.products(c).some((p) => this.pid(p) === pid)) return bad("unknown " + (pm.product_id || "product_id"));
        if (this.spec.rules?.one_promise_per_product !== false && st.promises.some((p) => p.product_id === pid)) return bad("this product already has a promise");
        const promise_id = "pr" + (st.promises.length + 1);
        const prod = this.products(c).find((p) => this.pid(p) === pid);
        const num = typeof amount === "string" && amount.trim() !== "" && Number.isFinite(Number(amount)) ? Number(amount) : amount;   // "126.5" -> 126.5; anything else is kept as sent
        const amt = ep.amount_unit === "cents" && typeof num === "number" ? num / 100 : num;
        st.promises.push({ promise_id, product_id: pid, type: prod.type ?? null, amount: amt, date });
        return done({ ok: true, promise_id }, st);
      }
      case "add_note": {
        const pid = arg("product_id");
        if (pid && !this.products(c).some((p) => this.pid(p) === pid)) return bad("unknown " + (pm.product_id || "product_id"));
        st.notes.push({ product_id: pid ?? null, reason: arg("reason"), text: arg("text") ?? null });
        return done({ ok: true }, st);
      }
      case "schedule_callback": {
        if (ep.mode === "datetime") { const [d, t = ""] = String(arg("datetime") ?? "").split("T"); st.callbacks.push({ date: d, time: t.slice(0, 5) }); }
        else st.callbacks.push({ date: arg("date"), time: arg("time") });
        return done({ ok: true }, st);
      }
      case "transfer_to_agent": st.transfers.push({ queue: arg("queue") }); return done({ ok: true }, st);
      case "dispute": {
        const pid = arg("product_id");
        if (pid && !this.products(c).some((p) => this.pid(p) === pid)) return bad("unknown " + (pm.product_id || "product_id"));
        st.disputes.push({ product_id: pid ?? null, reason: arg("reason") ?? null, text: arg("text") ?? null });
        return done({ ok: true, dispute_id: "dp_" + st.disputes.length + "_" + cid }, st);
      }
      case "close_call": {
        st.closed = true;
        if (ep.mode === "code") { st.outcome = arg("code"); st.reason = null; } else { st.outcome = arg("outcome"); st.reason = arg("reason") || null; }
        return done({ ok: true }, st);
      }
      default: { // generic record
        const kind = ep.kind || ep.path.replace(/^\//, "");
        (st.records[kind] ||= []).push(this.canon(body, pm));
        return done({ ok: true }, st);
      }
    }
  }

  products(c) {
    const fmt = this.spec.products?.id_format || (this.spec.products?.id_prefix || "p") + "{n}", idf = this.idField();
    return (c.products || []).map((p, i) => ({ [idf]: fmt.replace("{n}", i + 1), ...p }));
  }
  idField() { return this.spec.products?.id_field || "product_id"; }
  pid(p) { return p[this.idField()]; }
  canon(body, pm) { const o = { ...body }; for (const [canon, actual] of Object.entries(pm)) if (actual !== canon && body[actual] !== undefined) o[canon] = body[actual]; return o; }
  canonSchema(schema, pm) {
    if (!Object.keys(pm).length || !schema.properties) return schema;
    const rev = Object.fromEntries(Object.entries(pm).map(([c, a]) => [a, c]));
    return { ...schema, required: (schema.required || []).map((k) => rev[k] || k), properties: Object.fromEntries(Object.entries(schema.properties).map(([k, v]) => [rev[k] || k, v])) };
  }

  async verify(c, st, field, answer, done, bad) {
    const id = this.spec.identity || { fields: [], max_wrong: 3 };
    const f = id.fields.find((x) => x.name === field);
    if (!f) return bad(`${this.spec.endpoint_fields?.field || "field"} must be one of: ` + id.fields.map((x) => x.name).join(", "));
    const max = id.max_wrong ?? 3;
    const left = () => Math.max(0, max - st.wrong);
    const allv = () => id.fields.every((x) => st.verified[x.name]);
    if (st.wrong >= max) return done({ ok: false, verified: false, wrong_answers_left: 0, error: "identity check is over" }, st);
    if (st.verified[field]) return done({ ok: true, verified: true, wrong_answers_left: left(), all_verified: allv() }, st);
    const truth = c.identity?.[field];
    let given = answer, want = truth, hint;
    if (f.kind === "date") {
      given = normDate(answer);
      if (given === null) return bad("answer is not a date. Send it as YYYY-MM-DD.");   // not counted
      if (want !== undefined && given !== want && swapDate(given) === want) { if (id.swap_hint) return done({ ok: true, verified: false, wrong_answers_left: left(), hint: "day_month_swapped" }, st); }   // a swap is not a wrong answer
    } else if (f.kind === "digits") {
      given = String(answer ?? "").replace(/\D/g, ""); want = String(truth ?? "").replace(/\D/g, "");
      if (f.len && given.length !== f.len) return bad(`expected ${f.len} digits`);
    } else { given = String(answer ?? "").trim().toLowerCase(); want = String(truth ?? "").trim().toLowerCase(); }
    const ok = truth !== undefined && given === want;
    if (ok) st.verified[field] = true; else st.wrong += 1;
    const r = { ok: true, verified: ok, wrong_answers_left: left(), all_verified: allv() };
    if (!ok && hint && id.swap_hint) r.hint = hint;
    return done(r, st);
  }

  async consumeFault(op, cid) {
    for (const [i, f] of (this.spec.faults || []).entries()) {
      if (f.op !== op || (f.customer_id && f.customer_id !== cid)) continue;
      const key = `F:${cid}:${i}`, used = (await this.store.get(key)) || 0;
      if (used < (f.times ?? 1)) { await this.store.put(key, used + 1); return true; }
    }
    return false;
  }

  publicState() {
    return Promise.all(this.customers().map(async (c) => {
      const st = await this.state(c.customer_id);
      return { customer_id: c.customer_id, meta: c.meta ?? null, verified: st.verified, wrong: st.wrong, closed: st.closed, outcome: st.outcome, reason: st.reason, promises: st.promises.length, notes: st.notes.length, callbacks: st.callbacks.length, transfers: st.transfers.length };
    }));
  }
}

export function openapi(spec, origin) {
  if (spec.openapi_raw) return { ...spec.openapi_raw, servers: [{ url: `${origin}/${spec.tenant}${spec.base_path || ""}` }] };   // the bank's own contract, only the server is ours
  const paths = {};
  for (const e of spec.endpoints || []) {
    const m = (e.method || "POST").toLowerCase();
    const op = { summary: e.summary || e.op, operationId: e.op || e.path.replace(/\W+/g, "_") };
    if (e.request) op.requestBody = { required: true, content: { "application/json": { schema: e.request } } };
    op.responses = e.responses || { 200: { description: "ok" } };
    (paths[e.path] ||= {})[m] = op;
  }
  return { openapi: "3.0.3", info: spec.info || { title: spec.tenant, version: "1.0" }, servers: [{ url: `${origin}/${spec.tenant}${spec.base_path || ""}` }], components: {}, paths };
}
