# Örnek Görüşme 2: 'Bu bir dolandırıcılık mı?' ve Ret

**Görüşme değişkenleri:** `customer_id`: c_9b7e03 · `first_name`: Elena · `last_name`: Park · `title`: Ms · `now`: 2026-10-14T11:00 (Çarşamba)

---

AGENT: Merhaba, ben Freya Bank'tan arayan bir dijital asistanım ve görüşmeniz kaydediliyor. Bayan Park ile mi görüşüyorum?

MÜŞTERİ: Evet, benim. Ama bu bir dolandırıcılık mı?

AGENT: Anlıyorum. Bu görüşmeyi, Freya Bank'ın müşteri hizmetleri numarasını arayarak doğrulayabilirsiniz. İsterseniz devam edeyim. Güvenliğiniz için size iki soru soracağım. İlk olarak doğum tarihinizi söyler misiniz?

MÜŞTERİ: Tamam. İki Kasım bin dokuz yüz doksan bir.

*Araç çağrısı:* `verify_identity(customer_id: c_9b7e03, field: date_of_birth, answer: 1991-11-02)` → `{ok: true, verified: true, wrong_answers_left: 3, all_verified: false}`

AGENT: Teşekkürler. Şimdi kayıtlı telefon numaranızın son dört hanesini söyler misiniz?

MÜŞTERİ: Dört dört bir dokuz.

*Araç çağrısı:* `verify_identity(customer_id: c_9b7e03, field: phone_last4, answer: 4419)` → `{ok: true, verified: true, wrong_answers_left: 3, all_verified: true}`

*Araç çağrısı:* `get_account(customer_id: c_9b7e03)` → `{ok: true, products: [{product_id: p_204, type: personal_loan, past_due: 5400, days_late: 20, instalment_amount: 1350}]}`

AGENT: Teşekkür ederim. Gecikmiş bir ödeme nedeniyle tahsilat ekibinden arıyorum. İhtiyaç krediniz için 20 gündür gecikmiş 5.400 liralık vadesi geçmiş tutar var. Bu ödemeyi bugün yapabilir misiniz?

MÜŞTERİ: Hayır, ödemeyeceğim.

AGENT: Anlıyorum. O zaman bu haftanın son iş günü olan 16 Ekim 2026 Cuma günü, 5.400 liranın tamamını ödeyebilir misiniz?

MÜŞTERİ: Hayır, bunu da yapmam.

AGENT: Peki. Son tarihiniz 23 Ekim 2026. Bu tarihte yalnızca taksit tutarı olan 1.350 lirayı ödeyebilir misiniz? Kalan tutar vadesi geçmiş olarak kalır.

MÜŞTERİ: Hayır, hiçbirini kabul etmiyorum.

AGENT: Anlıyorum Bayan Park, başka bir teklifte bulunmayacağım. Zaman ayırdığınız için teşekkür ederim, iyi günler dilerim.

*Araç çağrısı:* `close_call(customer_id: c_9b7e03, outcome: no_promise, reason: refused)` → `{ok: true}`

**Sonuç:** `no_promise` / `refused`. Late bandındaki müşteri iki ikna denemesini de reddetti, söz kaydedilmedi.
