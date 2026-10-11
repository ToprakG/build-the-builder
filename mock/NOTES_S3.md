# S3 -> S2 (converter status, 19:4x)
- `python3 mock/convert.py --id <id> --customers <xlsx|csv|json|md|docx|pdf> --openapi <openapi.json> --config-from <config.json|seed:A|omit=LLM from contract> [--contract <md|docx|pdf>]` -> `mock/spec.<id>.json`; `--check <spec>` validates.
- Turkish: headers (ad, soyad, ad soyad, müşteri no, doğum tarihi, TC kimlik, telefon (son 4), kart son dört, ürün, ekstre borcu, asgari ödeme, taksit, son ödeme tarihi, gecikme gün...) are mapped offline; any required field not mapped -> LLM column mapping; still missing -> hard failure (no silent drop).
- Formats parsed: dates 14.03.1988 / 14 Mart 1988 / ISO / Excel dates; amounts `1.234,56 TL`, `₺ 12.000`, `1,234.56`; last-4 digits zero-padded; `Arama Zamanı` -> variables.now.
- Product `type` is kept as written in the file; `--check` warns if it is not in config.product_types enums (e.g. "Kredi Kartı" vs credit_card): fix in the spec or the config for the final.
- The Worker reads only the spec JSON (see SPEC.md). Customers are `{customer_id, variables, identity{...}, products[...]}`; identity keys can include `national_id` if the final contract asks for it.

# REHEARSAL CASE FOLDER (Turkish, mirrors the final format) — for S0 (build.py ingestion), S1 (Turkish build), S2 (mock deploy)
Folder: `/Users/alinebidal/yc-s3/mock/rehearsal-tr/FreyaBank-Test/`  (copy in main: `/Users/alinebidal/yc-hackathon-vapi/mock/rehearsal-tr/FreyaBank-Test/`)
Files: BRIEF.md (brief-a in Turkish), README.md, duyuru.docx (an announcement that CHANGES one rule: callback window 10:00-19:00 -> 10:00-18:00), servis-sozlesmesi.pdf (contract prose + openapi JSON appendix),
musteriler.xlsx (10 test customers, Turkish headers/formats), SSS.md (Q&A), ornek-gorusme-1.md / -2.md (two example conversations).
Rehearsal only; nothing from it goes into the builder. Regenerate: `.venv310/bin/python mock/make_rehearsal.py` (PDF step needs fpdf2 under system python: `python3 -m pip install --target /tmp/pdflib fpdf2`).
Spec made from it: `mock/spec.rehearsal-tr.json` (`python3 mock/convert.py --id rehearsal-tr --contract $F/BRIEF.md $F/servis-sozlesmesi.pdf $F/duyuru.docx $F/SSS.md --openapi tools/openapi.json --customers $F/musteriler.xlsx`) -> `--check` OK.
Result: 10 customers identical to spec.brief-a.json; config read by the LLM from the Turkish documents agrees with seed:A except language/Turkish labels, the announced callback end 18:00 (the changed rule was picked up), and `note_map` empty (check this key in the real run: the Worker needs the note reasons).
