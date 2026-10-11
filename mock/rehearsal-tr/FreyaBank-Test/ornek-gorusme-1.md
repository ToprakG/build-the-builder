# Örnek Görüşme 1: Normal Ödeme Sözü

**Görüşme değişkenleri:** `customer_id`: c_4f2a91 · `first_name`: Daniel · `last_name`: Reyes · `title`: Mr · `now`: 2026-10-14T11:00 (Çarşamba)

---

AGENT: Merhaba, ben Freya Bank'tan arayan bir dijital asistanım ve görüşmeniz kaydediliyor. Bay Reyes ile mi görüşüyorum?

MÜŞTERİ: Evet, benim.

AGENT: Teşekkürler Bay Reyes. Güvenliğiniz için size iki soru soracağım. İlk olarak doğum tarihinizi söyler misiniz?

MÜŞTERİ: On yedi Mart bin dokuz yüz seksen sekiz.

*Araç çağrısı:* `verify_identity(customer_id: c_4f2a91, field: date_of_birth, answer: 1988-03-17)` → `{ok: true, verified: true, wrong_answers_left: 3, all_verified: false}`

AGENT: Teşekkürler. Şimdi kayıtlı telefon numaranızın son dört hanesini söyler misiniz?

MÜŞTERİ: Yedi üç sıfır beş.

*Araç çağrısı:* `verify_identity(customer_id: c_4f2a91, field: phone_last4, answer: 7305)` → `{ok: true, verified: true, wrong_answers_left: 3, all_verified: true}`

*Araç çağrısı:* `get_account(customer_id: c_4f2a91)` → `{ok: true, products: [{product_id: p_001, type: credit_card, card_last4: 4821, past_due: 3200, days_late: 5, minimum_payment: 640}]}`

AGENT: Doğrulama için teşekkür ederim. Gecikmiş bir ödeme nedeniyle tahsilat ekibinden arıyorum. 4821 ile biten kredi kartınızda 5 gündür gecikmiş 3.200 liralık vadesi geçmiş tutar var. Bu ödemeyi bugün yapabilir misiniz?

MÜŞTERİ: Bugün olmaz ama cuma günü yapabilirim.

AGENT: Cuma günü, yani 16 Ekim 2026 uygundur. Son tarihiniz 2 Kasım 2026 olduğu için bu tarih kabul edilebilir. Tutarın tamamını, 3.200 lirayı mı ödeyeceksiniz?

MÜŞTERİ: Evet, hepsini ödeyeceğim.

AGENT: Özetliyorum: 4821 ile biten kredi kartı için 16 Ekim 2026 tarihinde 3.200 lira ödeme sözü kaydedeceğim. Onaylıyor musunuz?

MÜŞTERİ: Onaylıyorum.

*Araç çağrısı:* `create_promise(customer_id: c_4f2a91, product_id: p_001, amount: 3200, date: 2026-10-16)` → `{ok: true, promise_id: pr_001}`

AGENT: Sözünüz kaydedildi. Bu sözü onaylayan bir kısa mesaj alacaksınız. Teşekkür ederim Bay Reyes, iyi günler dilerim.

*Araç çağrısı:* `close_call(customer_id: c_4f2a91, outcome: promise)` → `{ok: true}`

**Sonuç:** `promise`. 4821 ile biten kredi kartı için 3.200 lira, 2026-10-16 tarihli söz kaydedildi.
