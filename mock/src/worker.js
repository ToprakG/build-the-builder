import { DurableObject } from "cloudflare:workers";
import { Bank, openapi } from "./engine.js";
import { adapt } from "./adapt.js";
import bundled from "./bundled_specs.json";

const J = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json", "access-control-allow-origin": "*" } });
const bearer = (req, url) => (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "") || url.searchParams.get("token") || "";

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const parts = url.pathname.split("/").filter(Boolean);
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" } });
    if (!parts.length) return J({ service: "bank-mock", tenants: Object.keys(bundled), read: "/<tenant>/openapi.json, /<tenant>/records, /<tenant>/ledger/<customer_id>, /<tenant>/state" });
    const tenant = parts[0];
    if (!/^[a-z0-9][a-z0-9-]*$/.test(tenant)) return J({ ok: false, error: "bad tenant" }, 404);
    const id = env.BANK.idFromName(tenant);
    return env.BANK.get(id).fetch(req);
  },
};

export class BankDO extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    const s = ctx.storage;
    this.store = {
      get: (k) => s.get(k),
      put: (k, v) => s.put(k, v),
      delete: (k) => s.delete(k),
      list: async (p) => [...(await s.list({ prefix: p })).entries()],
    };
  }
  async spec(tenant) {
    const s = (await this.ctx.storage.get("spec")) || bundled[tenant] || null;
    return s && adapt(s);
  }
  async fetch(req) {
    const url = new URL(req.url);
    const parts = url.pathname.split("/").filter(Boolean);
    const tenant = parts[0];
    const rest = "/" + parts.slice(1).join("/");
    const spec = await this.spec(tenant);
    if (!spec) {   // a new tenant is created by uploading its spec
      if (rest === "/admin/spec" && req.method === "PUT" && this.env.ADMIN_TOKEN && bearer(req, url) === this.env.ADMIN_TOKEN) {
        const ns = await req.json(); const a = adapt(ns);
        if (!a?.endpoints) return J({ ok: false, error: "not a spec" }, 400);
        await this.ctx.storage.put("spec", ns); return J({ ok: true, tenant, customers: (ns.customers || []).length });
      }
      return J({ ok: false, error: "unknown tenant" }, 404);
    }
    spec.tenant = tenant;   // the URL decides the tenant name
    const bank = new Bank(spec, this.store);
    const token = bearer(req, url);

    if (rest.startsWith("/admin/")) {
      if (!this.env.ADMIN_TOKEN || token !== this.env.ADMIN_TOKEN) return J({ ok: false, error: "forbidden" }, 403);
      if (rest === "/admin/spec" && req.method === "GET") return J((await this.ctx.storage.get("spec")) || bundled[tenant]);
      if (rest === "/admin/spec" && req.method === "PUT") { const ns = await req.json(); if (!adapt(ns)?.endpoints) return J({ ok: false, error: "not a spec" }, 400); await this.ctx.storage.put("spec", ns); return J({ ok: true, tenant: adapt(ns).tenant, customers: (ns.customers || []).length }); }
      if (rest === "/admin/customers" && req.method === "PUT") {
        const incoming = await req.json(); const list = Array.isArray(incoming) ? incoming : incoming.customers;
        const raw = (await this.ctx.storage.get("spec")) || bundled[tenant];
        const cur = { ...raw, customers: [...(raw.customers || [])] };
        for (const c of list) { const i = cur.customers.findIndex((x) => x.customer_id === c.customer_id); i >= 0 ? (cur.customers[i] = c) : cur.customers.push(c); }
        await this.ctx.storage.put("spec", cur); return J({ ok: true, customers: cur.customers.length });
      }
      if (rest === "/admin/reset" && req.method === "POST") { await bank.reset(url.searchParams.get("customer_id")); return J({ ok: true }); }
      return J({ ok: false, error: "not found" }, 404);
    }

    if (req.method === "GET") {
      if (rest === "/openapi.json") return J(openapi(spec, url.origin));
      if (rest === "/health") return J({ ok: true, tenant, customers: (spec.customers || []).length });
      const guarded = ["/records", "/state"].includes(rest) || /\/ledger\//.test(rest);
      if (guarded && this.env.RECORDS_TOKEN && token !== this.env.RECORDS_TOKEN) return J({ ok: false, error: "forbidden" }, 403);
      if (rest === "/records") return J({ tenant, records: await bank.ledger({ customer_id: url.searchParams.get("customer_id"), limit: +url.searchParams.get("limit") || 0 }) });
      if (rest === "/state") return J({ tenant, customers: await bank.publicState() });
      const lm = rest.match(/^(?:\/v1)?(?:\/practice)?\/ledger\/(.+)$/);   // /ledger/<id> and the organisers' /v1/practice/ledger/<id>
      if (lm) return J(await bank.scorerLedger(decodeURIComponent(lm[1])));
    }

    let body = {};
    if (req.method !== "GET") { try { body = await req.json(); } catch { return J({ ok: false, error: "invalid JSON body" }, 400); } }
    else body = Object.fromEntries(url.searchParams);
    const headers = { "x-mock-fail": req.headers.get("x-mock-fail") || "" };
    const r = await bank.call(req.method, rest, body, headers);
    return r ? J(r.json, r.status) : J({ ok: false, error: "not found" }, 404);
  }
}
