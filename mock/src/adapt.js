// Accepts the converter's spec (mock/convert.py: {spec_version, id, config, openapi, customers}) and returns the engine's internal spec.
// Anything already in the internal shape (has `endpoints`) is returned unchanged.
const TOOLS = ["verify_identity", "get_account", "create_promise", "add_note", "schedule_callback", "transfer_to_agent", "close_call"];

const kindOf = (name) => (/date|birth|dob|tarih|dogum/i.test(name) && !/year/i.test(name) ? "date" : /last4|last_4|digits|son4|phone|telefon/i.test(name) ? "digits" : "text");

export function adapt(s) {
  if (!s || s.endpoints || !s.openapi) return s;
  const cfg = s.config || {}, tools = cfg.tools || {};
  const server = s.openapi.servers?.[0]?.url || "";
  let base = "";
  try { base = new URL(server, "http://x").pathname.replace(/\/$/, ""); } catch { /* keep "" */ }
  const endpoints = [];
  for (const [path, item] of Object.entries(s.openapi.paths || {})) {
    for (const [method, o] of Object.entries(item)) {
      if (!o || typeof o !== "object") continue;
      const name = path.split("/").filter(Boolean).pop();
      const op = TOOLS.includes(name) ? name : "record";
      const t = tools[op] || {};
      const params = {};
      for (const [canon, actual] of Object.entries(t)) if (typeof actual === "string" && !["mode", "amount_unit"].includes(canon)) params[canon] = actual;
      const e = { op, method: method.toUpperCase(), path, summary: o.summary, params, responses: o.responses,
        request: o.requestBody?.content?.["application/json"]?.schema };
      if (t.mode) e.mode = t.mode;
      if (t.amount_unit) e.amount_unit = t.amount_unit;
      if (op === "record") e.kind = name;
      endpoints.push(e);
    }
  }
  const idq = cfg.identity || {};
  return {
    tenant: s.id, info: s.openapi.info, base_path: base, openapi_raw: s.openapi, endpoint_fields: tools.verify_identity,
    identity: { fields: (idq.questions || []).map((n) => ({ name: n, kind: kindOf(n), ...(kindOf(n) === "digits" && /4/.test(n) ? { len: 4 } : {}) })), max_wrong: idq.max_wrong ?? 3, swap_hint: !!idq.swap_hint },
    products: { id_field: cfg.account_fields?.product_id || "product_id", id_format: cfg.product_id_format || "p{n}" },
    rules: { one_promise_per_product: true, require_verified: ["get_account", "create_promise", "add_note"] },
    endpoints, customers: s.customers || [], faults: s.faults || [],
  };
}
