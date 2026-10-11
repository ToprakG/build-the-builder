"""Team tooling: test customers (xlsx/csv/md/docx/pdf/json) + service contract (md/docx/pdf/json openapi) -> mock/spec.<id>.json (see mock/SPEC.md).

    python3 mock/convert.py --id brief-a --name "Freya Bank" --contract briefs/brief-a.md --openapi tools/openapi.json \
        --customers public-test-set.json --config-from seed:A                     # offline, no LLM
    python3 mock/convert.py --id x --contract contract.pdf --customers accounts.xlsx             # LLM-assisted (claude CLI): config from the contract, customers from messy files
    python3 mock/convert.py --check mock/spec.brief-a.json

--config-from: a config.json path, `seed:A` / `seed:B` (Brief A/B canonical config), or omitted (the LLM reads the contract and the openapi).
Customers: .json (public-test-set / cases list / spec customers), .csv/.xlsx with recognisable headers (offline), anything else -> LLM."""
import argparse
import csv
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "vendor/build-the-builder"))

import unicodedata

MONTHS_TR = {"ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6, "temmuz": 7, "agustos": 8, "eylul": 9, "ekim": 10,
             "kasim": 11, "aralik": 12}
MONTHS_EN = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8, "september": 9,
             "october": 10, "november": 11, "december": 12}


def fold(x) -> str:
    """lower-case, Turkish letters to ASCII (ı İ ş ğ ç ö ü), anything else to '_'"""
    t = str(x or "").replace("İ", "i").replace("I", "i").replace("ı", "i").lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


# canonical column meaning by folded header (English + Turkish). kinds: var / id / key (identity answer) / cid / ptype / pfield / fullname
HEAD = {}
for kind, canon, names in [
    ("var", "first_name", "first_name firstname first given_name ad adi isim"),
    ("var", "last_name", "last_name lastname surname family_name soyad soyadi"),
    ("fullname", "full_name", "ad_soyad adi_soyadi ad_soyadi isim_soyisim musteri_adi musteri_adi_soyadi full_name name"),
    ("var", "title", "title salutation hitap unvan"),
    ("var", "now", "now arama_zamani arama_tarihi_saati arama_tarihi"),
    ("cid", "customer_id", "customer_id id customer musteri musteri_no musteri_numarasi musteri_id"),
    ("key", "date_of_birth", "date_of_birth dob birth_date birthdate dogum_tarihi dogum_tarihi_gg_aa_yyyy dogum_t dogum"),
    ("key", "phone_last4", "phone_last4 phone_last_4 last4 phone_last_four telefon_son_4 telefon_son_4_hane telefon_son_dort telefon_son_dort_hane "
                           "tel_son_4 telefonun_son_4_hanesi telefon_numarasinin_son_4_hanesi"),
    ("key", "phone", "phone telefon telefon_no telefon_numarasi cep_telefonu gsm"),
    ("key", "national_id", "tc tckn tc_kimlik tc_kimlik_no tc_kimlik_numarasi kimlik kimlik_no national_id"),
    ("pfield", "card_last4", "card_last4 card_last_4 kart_son_4 kart_son_dort kart_son_4_hane kart_son_dort_hane kartin_son_4_hanesi son_4_hane son_dort_hane"),
    ("ptype", "type", "type product_type product urun urun_tipi urun_turu kart kart_tipi kart_turu"),
    ("pfield", "past_due", "past_due overdue_amount ekstre_borcu ekstre_borc_tutari gecikmis_borc gecikmis_tutar gecmis_borc borc borc_tutari odenmemis_tutar"),
    ("pfield", "days_late", "days_late gecikme_gun gecikme_gunu gecikme_suresi gecikme_gun_sayisi gecikme"),
    ("pfield", "minimum_payment", "minimum_payment min_due asgari_odeme asgari_odeme_tutari asgari_tutar"),
    ("pfield", "instalment_amount", "instalment_amount taksit_tutari taksit"),
    ("pfield", "due_date", "due_date son_odeme_tarihi son_odeme"),
    ("pfield", "statement_date", "statement_date ekstre_tarihi hesap_kesim_tarihi"),
]:
    for n in names.split():
        HEAD[n] = (kind, canon)
CANON = sorted({c for _, c in HEAD.values()})
PRODUCT_NUM = {"past_due", "minimum_payment", "instalment_amount"}
PRODUCT_INT = {"days_late"}
PRODUCT_DATE = {"due_date", "statement_date"}
DIGITS4 = {"phone_last4", "card_last4"}
REQUIRED = [("name", lambda m: {"first_name", "last_name"} <= m or "full_name" in m), ("identity", lambda m: bool(m & {"date_of_birth", "phone_last4", "phone", "national_id"})),
            ("past_due", lambda m: "past_due" in m)]


def parse_amount(v) -> float:
    """1.234,56 TL / 1234,56 / 1.234 TL / 1,234.56 / 1234.5 -> float"""
    if isinstance(v, (int, float)):
        return float(v)
    t = re.sub(r"(?i)(tl|try|₺|lira|\s)", "", str(v)).strip()
    if not t:
        raise ValueError("empty amount")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if re.fullmatch(r"-?\d+,\d{1,2}", t) else t.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")          # Turkish thousands: 1.234 = 1234
    return float(t)


def parse_date(v) -> str:
    """14.03.1988 / 14/03/1988 / 14 Mart 1988 / 1988-03-14 / datetime -> YYYY-MM-DD"""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    t = str(v).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?", t)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?:[ T].*)?", t)
        if m:
            d, mo, y = map(int, m.groups())
        else:
            m = re.fullmatch(r"(\d{1,2})\s+([^\d\s]+)\s+(\d{4})", t)
            mon = fold(m.group(2)) if m else ""
            mo = MONTHS_TR.get(mon) or MONTHS_EN.get(mon) if m else None
            if not mo:
                raise ValueError(f"not a date: {v!r}")
            d, y = int(m.group(1)), int(m.group(3))
    import datetime
    return datetime.date(y, mo, d).isoformat()


def parse_now(v) -> str:
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%dT%H:%M")
    t = str(v).strip()
    m = re.search(r"(\d{1,2}):(\d{2})", t)
    return parse_date(re.sub(r"\s*\d{1,2}:\d{2}.*$", "", t).replace("T", " ").split(" ")[0] if re.match(r"\d{4}-", t) else re.sub(r"\s*\d{1,2}:\d{2}.*$", "", t)) + \
        (f"T{int(m.group(1)):02d}:{m.group(2)}" if m else "T11:00")


def read_text(path: Path) -> str:
    s = path.suffix.lower()
    if s in (".md", ".txt", ".json", ".csv", ".yaml", ".yml"):
        return path.read_text(errors="replace")
    if s == ".docx":
        import docx
        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            parts += [" | ".join(c.text for c in r.cells) for r in t.rows]
        return "\n".join(parts)
    if s == ".pdf":
        import pypdf
        return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(str(path)).pages)
    if s in (".xlsx", ".xlsm"):
        return "\n".join(" | ".join("" if c is None else str(c) for c in row) for row in _xlsx_rows(path))
    raise SystemExit(f"unsupported file type {path}")


def _xlsx_rows(path: Path):
    import openpyxl
    ws = openpyxl.load_workbook(str(path), data_only=True).active
    return [list(r) for r in ws.iter_rows(values_only=True) if any(c not in (None, "") for c in r)]


def llm(system: str, user: str, max_tokens=8000) -> str:
    from builder.template_cc import llm as L
    L.load_env()
    return L._claude_cli(system, user, "sonnet", max_tokens, 600)[0]


def json_out(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    s = m.group(1) if m else text
    i = min([x for x in (s.find("{"), s.find("[")) if x >= 0] or [0])
    return json.loads(s[i:])


def cid(v: dict) -> str:
    key = "|".join(str(v.get(k, "")) for k in ("first_name", "last_name")) + str(v.get("_k", ""))
    return "c_" + hashlib.sha1(key.encode()).hexdigest()[:8]


def norm_customer(c: dict, i: int) -> dict:
    vars_ = dict(c.get("variables") or {})
    for k in ("first_name", "last_name", "title", "now"):
        if k in c and k not in vars_:
            vars_[k] = c[k]
    out = {"customer_id": c.get("customer_id") or cid({**vars_, "_k": i}), "variables": vars_,
           "identity": dict(c.get("identity") or {}), "products": [dict(p) for p in c.get("products") or []]}
    return out


LLM_MAP = """The first row of a table of a bank's test customers (header + 3 sample rows) is below. Map EVERY column to one canonical name from this list, or null
if it is not needed: %s. (Turkish headers are common: ad, soyad, doğum tarihi, TC kimlik, telefon son 4, ürün, ekstre borcu, asgari ödeme, gecikme gün.)
Output ONLY a JSON object {"<header exactly as given>": "<canonical or null>"}."""


def map_headers(rows: list) -> dict:
    """header -> (kind, canon). Offline synonyms first; if a required field is missing ask the LLM; still missing -> fail loudly."""
    heads = [str(h) for h in rows[0]]
    m = {h: HEAD[fold(h)] for h in heads if fold(h) in HEAD}

    def missing():
        have = {c for _, c in m.values()}
        return [n for n, ok in REQUIRED if not ok(have)]
    if missing():
        sample = "\n".join(" | ".join("" if c is None else str(c) for c in r) for r in rows[:4])
        got = json_out(llm(LLM_MAP % ", ".join(CANON), sample, 2000))
        for h, c in got.items():
            if h in heads and c in CANON and h not in m:
                m[h] = next(v for v in HEAD.values() if v[1] == c)
    if missing():
        raise SystemExit(f"FAILED: required customer fields not found in the columns {heads}: missing {missing()}; map them with a --columns file")
    return m


def customers_from_table(rows: list) -> list | None:
    if not rows or len(rows) < 2:
        return None
    heads = [str(h) for h in rows[0]]
    if not any(fold(h) in HEAD for h in heads):
        return None            # not a customer table: the LLM reads the whole file
    mp = map_headers(rows)
    groups, order = {}, []
    for r in rows[1:]:
        rec = dict(zip(heads, r))
        if all(v in (None, "") for v in rec.values()):
            continue
        cidh = next((h for h in heads if mp.get(h, ("",))[0] == "cid" and rec.get(h) not in (None, "")), None)
        fnh = next((h for h in heads if mp.get(h, ("", ""))[1] == "first_name"), None)
        lnh = next((h for h in heads if mp.get(h, ("", ""))[1] == "last_name"), None)
        fuh = next((h for h in heads if mp.get(h, ("", ""))[1] == "full_name"), None)
        key = str(rec[cidh]) if cidh else f"{rec.get(fnh) or rec.get(fuh)} {rec.get(lnh) or ''}"
        if key not in groups:
            groups[key] = {"variables": {}, "identity": {}, "products": []}
            order.append(key)
        g = groups[key]
        prod = {}
        for h in heads:
            v = rec.get(h)
            if v in (None, ""):
                continue
            if isinstance(v, str):
                v = v.strip()
            kind, canon = mp.get(h, ("pfield", fold(h)))
            if kind == "var":
                g["variables"][canon] = parse_now(v) if canon == "now" else str(v)
            elif kind == "fullname":
                parts = str(v).split()
                g["variables"].setdefault("first_name", " ".join(parts[:-1]) or parts[0])
                g["variables"].setdefault("last_name", parts[-1] if len(parts) > 1 else "")
            elif kind == "key":
                if canon == "date_of_birth":
                    g["identity"][canon] = parse_date(v)
                elif canon == "phone":
                    d = re.sub(r"\D", "", str(v))
                    g["identity"].setdefault("phone_last4", d[-4:])
                elif canon in DIGITS4:
                    g["identity"][canon] = re.sub(r"\D", "", str(v).split(".")[0]).zfill(4)
                else:
                    g["identity"][canon] = str(v).split(".")[0] if isinstance(v, float) else str(v)
            elif kind == "cid":
                g["customer_id"] = str(v)
            elif kind == "ptype":
                prod["type"] = str(v)
            else:
                if canon in DIGITS4:
                    prod[canon] = re.sub(r"\D", "", str(v).split(".")[0]).zfill(4)
                elif canon in PRODUCT_DATE:
                    prod[canon] = parse_date(v)
                elif canon in PRODUCT_INT:
                    prod[canon] = int(parse_amount(v))
                elif canon in PRODUCT_NUM:
                    prod[canon] = parse_amount(v)
                else:
                    try:
                        prod[canon] = parse_amount(v) if re.search(r"\d", str(v)) and not isinstance(v, str) or re.fullmatch(r"[\d.,\s]+(TL|₺)?", str(v)) else str(v)
                    except ValueError:
                        prod[canon] = str(v)
        if prod:
            g["products"].append(prod)
    return [norm_customer(groups[k], i) for i, k in enumerate(order)]


LLM_CUSTOMERS = """Convert the test-customer data into JSON: a list of customers, each
{"variables": {"first_name","last_name","title","now"}, "identity": {<one key per security question, e.g. "date_of_birth" as YYYY-MM-DD, "phone_last4">},
 "products": [{"type": <as in the data>, "past_due": number, "days_late": number, <every other numeric field of the product with the data's own name>}]}.
Keep the data's own field names and values; do not invent customers or fields. Output ONLY the JSON list."""


def load_customers(path: Path, only_brief: str = "") -> list:
    s = path.suffix.lower()
    if s == ".json":
        d = json.loads(path.read_text())
        d = d.get("customers", d) if isinstance(d, dict) else d
        seen, out = set(), []
        for i, c in enumerate(x for x in d if not only_brief or x.get("brief") == only_brief):
            nc = norm_customer(c, i)
            sig = json.dumps([nc["variables"].get("first_name"), nc["variables"].get("last_name"), nc["identity"]], sort_keys=True)
            if sig in seen:
                continue   # a case list repeats customers: one account each
            seen.add(sig)
            out.append(nc)
        return out
    rows = None
    if s == ".csv":
        rows = list(csv.reader(path.open(newline="")))
    elif s in (".xlsx", ".xlsm"):
        rows = _xlsx_rows(path)
    got = customers_from_table(rows) if rows else None
    if got:
        return got
    return [norm_customer(c, i) for i, c in enumerate(json_out(llm(LLM_CUSTOMERS, read_text(path))))]


LLM_CONFIG = """You read a bank's service contract (the call-handling brief and API description) and output the CANONICAL mock-bank config as JSON.
Follow EXACTLY the shape of this example (keys, nesting, value types); take every value from the contract, never invent. Use the API's own field names in "tools".
Example:
%s
Output ONLY the JSON object."""


def load_config(arg: str | None, contract: str, openapi: dict) -> dict:
    if arg and arg.startswith("seed:"):
        from seed import policy_ab
        return dict({"A": policy_ab.POLICY_A, "B": policy_ab.POLICY_B}[arg[5:]])
    if arg:
        return json.loads(Path(arg).read_text())
    ex = json.loads((REPO / "testbed/fake_briefs/f5-halden-email/config.json").read_text())
    return json_out(llm(LLM_CONFIG % json.dumps(ex, indent=1), "CONTRACT:\n" + contract + "\n\nOPENAPI:\n" + json.dumps(openapi)[:30000]))


def check(spec: dict) -> list:
    bad = []
    cfg = spec.get("config") or {}
    for k in ("tools", "identity", "outcome_map", "account_fields"):
        if k not in cfg:
            bad.append(f"config.{k} missing")
    qs = (cfg.get("identity") or {}).get("questions") or []
    for c in spec.get("customers") or []:
        for q in qs:
            if q not in c.get("identity", {}):
                bad.append(f"{c['customer_id']}: identity.{q} missing")
        if not c.get("products"):
            bad.append(f"{c['customer_id']}: no products")
        enums = {t.get("enum") for t in (cfg.get("product_types") or {}).values()}
        for p in c.get("products") or []:
            if enums and p.get("type") not in enums:
                bad.append(f"{c['customer_id']}: product type {p.get('type')!r} is not one of {sorted(enums)} (map it in the spec)")
    paths = set((spec.get("openapi") or {}).get("paths") or {})
    for t in (cfg.get("tools") or {}):
        if paths and not any(p.rstrip("/").endswith("/" + t) or p.strip("/") == t for p in paths):
            bad.append(f"tool {t}: no matching openapi path")
    ids = [c["customer_id"] for c in spec.get("customers") or []]
    if len(ids) != len(set(ids)):
        bad.append("duplicate customer_id")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id"); ap.add_argument("--name", default="")
    ap.add_argument("--contract", nargs="+", help="one or more files (brief, contract pdf/docx/md...); read and concatenated"); ap.add_argument("--openapi"); ap.add_argument("--customers")
    ap.add_argument("--only-brief", default="", help="json case lists: keep the cases of this brief key (A/B)"); ap.add_argument("--config-from"); ap.add_argument("--out"); ap.add_argument("--check")
    a = ap.parse_args()
    if a.check:
        bad = check(json.loads(Path(a.check).read_text()))
        print("OK" if not bad else "\n".join(bad)); return 1 if bad else 0
    openapi = json.loads(Path(a.openapi).read_text()) if a.openapi else {}
    contract = "\n\n".join(f"## {Path(f).name}\n" + read_text(Path(f)) for f in a.contract or [])
    if not openapi:   # no openapi file in the case folder: the LLM writes it from the contract (paths and field names exactly as the contract states them)
        openapi = json_out(llm("You write OpenAPI 3.0 documents.", "Write the OpenAPI 3.0 JSON of this service contract: every endpoint (servers/paths/methods), request and response "
                               "schemas with the contract's own field names and enums, descriptions in the contract's language. If the contract already contains an OpenAPI "
                               "document or JSON schemas, reproduce them exactly. Output ONLY the JSON.\n\n" + contract[:60000], 16000))
        if not openapi.get("paths"):
            raise SystemExit("FAILED: no openapi given and none could be derived from the contract")
    spec = {"spec_version": 1, "id": a.id, "name": a.name or a.id, "config": load_config(a.config_from, contract, openapi),
            "openapi": openapi, "customers": load_customers(Path(a.customers), a.only_brief)}
    out = Path(a.out or REPO / "mock" / f"spec.{a.id}.json")
    out.write_text(json.dumps(spec, indent=1, ensure_ascii=False))
    bad = check(spec)
    print(f"wrote {out}: {len(spec['customers'])} customers; " + ("OK" if not bad else "PROBLEMS:\n  " + "\n  ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
