=== FILE: BRIEF.md ===

# Brief A: Freya Bank, erken aşama tahsilat, tek ürün

Freya Bank kurgusaldır. Buradaki tüm müşteriler, borçlar ve ödemeler sentetiktir.

Bu brief, gerçek bir banka şartnamesinin yoğunluğundadır. Tamamını okuyun. Başarısız vakaların çoğu, atlanmış tek bir kuraldan kaynaklanır.

## 1. Görev

Freya Bank, tek bir üründe 1 ila 45 gün gecikmiş müşterileri arar. Ajan müşteriyi bulur, kimliği doğrular, borcu bildirir, mümkün olduğunda bir ödeme sözü alır ve görüşmeyi doğru sonuçla kapatır.

## 2. Görüşme değişkenleri

Her görüşme bunlarla başlar. Araçlar çağrılmadan önce başka hiçbir şey bilinmez.

| Değişken | Örnek | Anlamı |
|---|---|---|
| `customer_id` | `c_4f2a...` | Her araç çağrısına iletin. |
| `first_name`, `last_name` | `Daniel`, `Reyes` | Görüşülmesi istenecek kişi. |
| `title` | `Mr` veya `Ms` | Müşteriye unvan artı soyadıyla hitap edin. |
| `now` | `2026-10-14T11:00` | Güncel tarih ve saat. Tüm tarih kuralları bunu kullanır, gerçek saati asla kullanmaz. |

## 3. Açılış ve hatta kimin olduğu

1. İlk cümle: Freya Bank'tan arayan bir dijital asistan olduğunuzu ve görüşmenin kaydedildiğini söyleyin. Kimlik doğrulanmadan önce "tahsilat", "borç", "ödeme" veya ürünü söylemeyin. Bu, akrabalar dahil herkes için geçerlidir.
2. "{title} {last_name} ile mi görüşüyorum?" diye sorun.
3. Yalnızca "evet", "benim", "dinliyorum" veya müşterinin kendi adı onay sayılır. "Alo", "buyurun" ve "kim arıyor" sayılmaz. Bir kez daha sorun. İkinci sorudan sonra da belirsizse görüşmeyi `not_reached` / `unclear` olarak kapatın. Önce müşterinin sorusunu yanıtlayın (kural 4 ve 43). Ardından gelen soru ikinci sorudur.
4. Müşteri robot veya yapay zekâ olup olmadığınızı sorarsa, Freya Bank'ın dijital asistanı olduğunuzu söyleyin ve devam edin.

## 4. Başka biri cevap verirse

5. Neden aradığınızı söylemeyin. Kural 1'deki açılış cümlesinin ötesinde hiçbir şey söylemeyin. Müşteriye ne zaman ulaşılabileceğini sorun.
6. Geri arama, bugün veya sonraki iş günü için, 10:00 ile 19:00 arasında (her ikisi dahil) ve `now` değerinden sonra olmalıdır. Tarih ve saatle `schedule_callback` çağırın, ardından `not_reached` / `callback` olarak kapatın.
7. Kişi bu aralığın dışında bir gün veya saat önerirse, başka bir zaman için bir kez sorun. O da aralığın dışındaysa planlama yapmayın. `not_reached` / `no_callback` olarak kapatın.
8. Kişi numaranın müşteriye ait olmadığını söylerse, `not_reached` / `wrong_number` olarak kapatın.
9. Kişi müşterinin vefat ettiğini söylerse, "Başınız sağ olsun." deyin, başka hiçbir şey sormayın ve `not_reached` / `deceased` olarak kapatın.
10. Bir akraba güvenlik sorularını yanıtlayamaz ve ödeme yapmayı teklif etse bile kendisine hesap hakkında hiçbir şey söylenemez.

## 5. Kimlik

11. İki güvenlik sorusu sorun, birer birer: önce doğum tarihi, sonra kayıtlı telefon numarasının son dört hanesi. Yanıtla `verify_identity` çağırın. Müşteri nasıl söylerse söylesin, doğum tarihini `YYYY-MM-DD` biçiminde gönderin. Servis, tarih olarak okuyamadığı her şeyi reddeder. Yalnızca rakamlarla verilen bir tarihi gün, ay, yıl sırasıyla okuyun.
12. Yanlış bir yanıt tekrar sorulabilir. Hangi soruda olursa olsun toplam üç yanlış yanıt kontrolü sonlandırır.
13. Müşteri gün ve ayı ters söylerse, `verify_identity` `hint: "day_month_swapped"` döndürür. Ayı kelimeyle söylemesini isteyin. Bu yeniden deneme yanlış yanıt sayılmaz.
14. Üç yanlış yanıttan sonra borcu bildirmeyin. Müşteriye mobil uygulamayı veya bir şubeyi kullanmasını söyleyin. `not_verified` / `failed` olarak kapatın.
15. Müşteri yanıt vermek istemezse, soruların hesabını koruduğunu bir kez açıklayın. Yine de reddederse `not_verified` / `declined` olarak kapatın. Yanıt vermek yerine görüşmenin ne hakkında olduğunu sormak reddetmek sayılır. İkinci bir ret veya bu türden ikinci bir soru görüşmeyi kapatır.

## 6. Borcun bildirilmesi

16. Her iki soru da geçildikten sonra `get_account` çağırın.
17. Gecikmiş bir ödeme nedeniyle tahsilat ekibinden aradığınızı söyleyin. Ürünü sözcüklerle belirtin: "4821 ile biten kredi kartı", "ihtiyaç kredisi" veya "taşıt kredisi". Vadesi geçmiş tutarı ve kaç gün geciktiğini belirtin.

## 7. Tarihler

İş günleri, Freya Bank tatilleri hariç Pazartesi'den Cuma'ya kadardır: 2026-10-26, 2026-11-11, 2026-11-26, 2026-12-25, 2027-01-01.

18. **Son tarih**, müşterinin ne kadar geciktiğine bağlıdır.

| `days_late` | Bant | Son tarih | İkna denemeleri |
|---|---|---|---|
| 1 ila 7 | early | bugünden sonraki 12. iş günü | 1 |
| 8 ila 45 | late | bugünden sonraki 7. iş günü | 2 |

19. Söz tarihi bugün veya daha sonrası olmalı, bir iş günü olmalı ve son tarihten sonra olmamalıdır.
20. Müşteri bir hafta sonu gününü veya son tarihten sonra olmayan bir tatil gününü söylerse, sonraki iş gününü önerin. Söylenen gün son tarihten sonraysa kural 23 geçerlidir.
21. Müşteri belirsiz bir dönem söylerse ("gelecek hafta", "ay sonu", "maaşım yattığında"), kesin günü bir kez sorun. Yine de vermezse son tarihi önerin.
22. "Gelecek <haftanın günü>", takip eden takvim haftasındaki o günü ifade eder. "Bu <haftanın günü>", mevcut haftadaki günü ifade eder. Yani Cuma günü söylenen "gelecek Cuma", takip eden haftanın Cuması'dır.
23. Müşteri son tarihten sonraki bir tarih isterse, kaydedebileceğiniz en geç tarihin son tarih olduğunu söyleyin ve bunu önerin. Müşteri son tarihi reddederse, bunu kesin ret sayın (kural 30).

## 8. Tutarlar

24. Önce vadesi geçmiş tutarın tamamını isteyin. Asla "ne kadar ödeyebilirsiniz?" diye sormayın. Her tutarı ajan önerir.
25. **Taban tutar**, kredi kartı için `minimum_payment`, ihtiyaç kredisi veya taşıt kredisi için `instalment_amount` değeridir.
26. Müşteri tutarın tamamının çok fazla olduğunu söylerse, taban tutarı bir kez önerin. Kalanın vadesi geçmiş olarak kalacağını söyleyin. Müşteri taban tutarı reddederse, bunu kesin ret sayın (kural 30). 2. deneme taban tutarı son tarihte yeniden önerebilir.
27. Müşteri taban tutardan tutarın tamamına kadar (her ikisi dahil) bir tutar önerirse, kabul edin. Taban tutarın altındaysa, bunun kaydedilemeyeceğini söyleyin ve taban tutarı bir kez önerin. Müşteri taban tutarı kabul etmezse, bunu kesin ret sayın (kural 30).

## 9. Sözün alınması

28. İlk soru: "Bu ödemeyi bugün yapabilir misiniz?".
29. Müşterinin önerdiği bir tarih veya tutar asla ret sayılmaz. Bunu bölüm 7 ve 8'e göre ele alın.
30. Kesin bir ret veya yanıtsızlık bir ikna denemesi kullanır. Tarih ve tutar belirtmeden bugün ödemeye hayır demek kesin retir. Deneme 1, vadesi geçmiş tutarın tamamı içindir; müşteri tutarın tamamının çok fazla olduğunu zaten söylemişse taban tutar içindir.
    - Deneme 1: bu haftanın son iş gününü önerin. Bugün o gün ise, gelecek haftanın son iş gününü önerin.
    - Deneme 2, yalnızca late bandı: son tarihte taban tutarı önerin.
31. Denemeler tükendiğinde durun. Müşterinin zaten reddettiği bir teklifi tekrarlamayın. `no_promise` / `refused` olarak kapatın.
32. Müşteri hastalıktan, ailede bir ölümden veya işini kaybetmekten bahsederse, kısa bir başsağlığı/geçmiş olsun cümlesi söyleyin ve ikna denemesi yapmayın. Müşteriyi müşteri destek ekibine bağlamayı teklif edin. Kabul ederse queue `support` ile `transfer_to_agent` çağırın ve `transferred` / `support` olarak kapatın. Reddederse `no_promise` / `hardship` olarak kapatın. Bu kural, kural 30 ve 41'den önce gelir. Müşteri yine de kendisi bir tarih veya tutar belirtirse, sözü bölüm 7 ve 8'e göre kaydedin.
33. Bu brief'te olmayan hiçbir indirim, vazgeçme, taksit planı veya şart önermeyin.

## 10. Onaylama ve kaydetme

34. Ürünü, tutarı ve tarihi geri okuyun ve "Onaylıyor musunuz?" diye sorun. `create_promise` yalnızca net bir evetten sonra çağrılır.
35. Müşteri bu adımda tarihi veya tutarı değiştirirse, yeni değeri bölüm 7 ve 8'e göre kontrol edin ve tekrar geri okuyun.
36. Ürün başına bir söz. Kaydedilmiş bir söz aynı görüşmede değiştirilemez veya iptal edilemez. Müşteri isterse, mobil uygulamadan değiştirebileceğini söyleyin.
37. Sözün kaydedildiğini yalnızca `create_promise` `ok: true` döndürdükten sonra söyleyin. Başarısız olursa bir kez daha deneyin. Yine başarısız olursa kaydedemediğinizi söyleyin ve `no_promise` / `service_error` olarak kapatın.
38. Kaydedilmiş bir sözden sonra şu cümleyi söyleyin: "Bu sözü onaylayan bir kısa mesaj alacaksınız."

## 11. Müşterilerin söylediği diğer şeyler

39. "Zaten ödedim": ödemelerin görünmesinin bir iş günü sürebileceğini söyleyin, söz almayın ve `paid` olarak kapatın.
40. "Bu borç benim değil" veya "Bu ürünüm hiç olmadı": tartışmayın ve söz almayın. `no_promise` / `dispute` olarak kapatın. Ürünün geçmişte kapatıldığı veya borcunun ödendiği iddiası bir itirazdır. Kural 39 yalnızca bu vadesi geçmiş tutarın ödenmesini kapsar.
41. Yeni bir ödeme planı veya daha düşük taksit talebi: bir plan anlatmayın. Queue `restructuring` ile `transfer_to_agent` çağırın ve `transferred` / `restructuring` olarak kapatın.
42. Bir insanla görüşme talebi: önce yardımcı olup olamayacağınızı bir kez sorun. Ayrıca ısrar ederse, queue `general` ile `transfer_to_agent` çağırın ve `transferred` / `human` olarak kapatın.
43. "Bu bir dolandırıcılık mı?": görüşmeyi Freya Bank'ın müşteri hizmetleri numarasını arayarak doğrulayabileceklerini söyleyin ve devam edin.
44. Araçlardan yanıtlayamayacağınız bir soru: bu bilgiye sahip olmadığınızı söyleyin. Rakam, tarih veya gerekçe uydurmayın.
45. Müşteri görüşmek istemediğini iki kez söylerse, `no_promise` / `refused` olarak kapatın. Kimlik doğrulanmadan önce aynı müşteri `not_verified` / `declined` olarak kapatılır. Kural 42, doğrulamadan önce ve sonra geçerlidir.

## 12. Kapanış

46. Her görüşme, bir sonuçla ve tabloda listelenmişse bir nedenle, tam olarak bir `close_call` ile biter. Bu, son araç çağrısıdır.

| Sonuç | Neden | Ne zaman |
|---|---|---|
| `promise` | yok | en az bir söz kaydedildi |
| `paid` | yok | kural 39 |
| `transferred` | `restructuring`, `human`, `support` | kural 41, 42, 32 |
| `no_promise` | `refused`, `hardship`, `dispute`, `service_error` | kural 31 ve 45, 32, 40, 37 |
| `not_verified` | `failed`, `declined` | kural 14, 15 |
| `not_reached` | `callback`, `no_callback`, `wrong_number`, `deceased`, `unclear` | kural 6, 7, 8, 9, 3 |

Kaydedilmiş bir sözün ardından bir aktarma yapılırsa, o aktarmanın nedeniyle `transferred` olarak kapatın.

Brief A hiçbir zaman `add_note` kullanmaz.

47. `close_call` öncesinde müşteriye teşekkür edin ve veda edin.

=== FILE: README.md ===

# Freya Bank – Erken Aşama Tahsilat (Brief A) Prova Dosyaları

Freya Bank kurgusaldır. Tüm müşteriler, borçlar ve ödemeler sentetiktir.

## Dosyalar

- `BRIEF.md`: Görevin ana şartnamesi (Brief A). Tüm kurallar burada yer alır.
- `duyuru.docx`: Brief'teki tek bir sayısal kuralı değiştiren iç duyuru. Duyuru ile brief çelişirse duyuru geçerlidir.
- `servis-sozlesmesi.pdf`: Araç servisinin (OpenAPI) düzyazı olarak anlatımı: uç noktalar, istek ve yanıt alanları, hata davranışı. Servis brief'i uygulamaz.
- `musteriler.xlsx`: Prova için sentetik müşteri listesi.
- `SSS.md`: Ajan geliştiricisinin sorabileceği 6 soru ve yanıtı.
- `ornek-gorusme-1.md`: Normal bir ödeme sözü alınan örnek görüşme.
- `ornek-gorusme-2.md`: Müşterinin 'bu bir dolandırıcılık mı?' diye sorduğu ve sonunda reddettiği örnek görüşme.

## Okuma sırası

1. `BRIEF.md`
2. `duyuru.docx` (brief'in hangi değerinin değiştiğini not edin)
3. `servis-sozlesmesi.pdf`
4. `SSS.md` ve örnek görüşmeler

=== FILE: duyuru.docx ===

# Duyuru

**İç Duyuru: Geri Arama Saat Aralığında Değişiklik**

Brief A, kural 6'da geri arama saatini şöyle tanımlıyordu: 'Geri arama, bugün veya sonraki iş günü için, 10:00 ile 19:00 arasında (her ikisi dahil) ve `now` değerinden sonra olmalıdır.' Bu duyuruyla söz konusu saat aralığı değişmiştir. Eski değer olan 10:00 ile 19:00 arasında (her ikisi dahil) ifadesi geçersizdir ve yerine 10:00 ile 18:00 arasında (her ikisi dahil) değeri gelmiştir. Yeni kural şöyledir: geri arama bugün veya sonraki iş günü için, 10:00 ile 18:00 arasında (her ikisi dahil) ve `now` değerinden sonra olmalıdır. Kural 7'deki 'aralığın dışında' ifadesi de bu yeni aralığa göre okunur.

Brief'in diğer tüm kuralları aynen geçerlidir: geri aramanın bugün veya sonraki iş günü için olması, `now` değerinden sonra olması, bir kez başka zaman sorulması ve sonuç eşlemeleri (`not_reached` / `callback` ve `not_reached` / `no_callback`) değişmemiştir. Servis saat aralığını denetlemez. Yeni aralığa uymak ajanın sorumluluğundadır.

=== FILE: musteriler.xlsx ===

### Sheet: Sheet

| Müşteri No | Ad | Soyad | Hitap | Doğum Tarihi | Telefon Son 4 Hane | Arama Zamanı | Ürün | Kart Son Dört | Ekstre Borcu | Gecikme Gün | Asgari Ödeme | Taksit Tutarı |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c_69871a7a | Daniel | Reyes | Mr | 14.03.1988 | 4471 | 14.10.2026 11:00 | credit_card | 4821 | 420,00 TL | 6 | 126,00 TL |  |
| c_b80abe0b | Maria | Costa | Ms | 2 Kasım 1979 | 358 | 14.10.2026 11:00 | personal_loan |  | 310,50 TL | 11 |  | 103,50 TL |
| c_d14324ff | Owen | Park | Mr | 1992-07-21 00:00:00 | 6630 | 14.10.2026 11:00 | credit_card | 377 | 95,00 TL | 4 | 28,50 TL |  |
| c_bf05b744 | Lena | Hoffmann | Ms | 30.01.1984 | 2219 | 14.10.2026 11:00 | car_loan |  | 1.250,00 TL | 14 |  | 400,00 TL |
| c_24f26acf | Tomas | Novak | Mr | 9 Eylül 1990 | 8846 | 14.10.2026 11:00 | personal_loan |  | 780,00 TL | 21 |  | 260,00 TL |
| c_631d86dc | Priya | Nair | Ms | 1986-05-17 00:00:00 | 1502 | 14.10.2026 11:00 | credit_card | 1903 | 2.125,10 TL | 12 | 637,50 TL |  |
| c_6a27a57c | Samuel | Ortiz | Mr | 05.12.1975 | 7093 | 14.10.2026 11:00 | personal_loan |  | 540,00 TL | 5 |  | 180,00 TL |
| c_eea1e2d0 | Hannah | Schmidt | Ms | 28 Nisan 1993 | 3317 | 14.10.2026 11:00 | credit_card | 5512 | 180,00 TL | 5 | 54,00 TL |  |
| c_17649d38 | Victor | Ionescu | Mr | 1981-08-11 00:00:00 | 5560 | 14.10.2026 11:00 | personal_loan |  | 2.100,00 TL | 28 |  | 700,00 TL |
| c_3368cc24 | Grace | Lin | Ms | 23.02.1989 | 9904 | 14.10.2026 11:00 | credit_card | 7740 | 365,00 TL | 8 | 109,50 TL |  |

=== FILE: ornek-gorusme-1.md ===

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

=== FILE: ornek-gorusme-2.md ===

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

=== FILE: servis-sozlesmesi.pdf ===

Servis Sözleşmesi
Freya Bank Tahsilat Araçları – Servis Sözleşmesi (sürüm 0.3-draft)

Genel kurallar

Bu servis, bankanın sistemlerinin bir taklididir ve Build the Builder hackathonu için barındırılır. Her çağrı, puanlayıcının
okuduğu deftere yazılır. Temel adres: `https://freya-bank-mock.freyavoice.workers.dev/v1`. Yedi aracın tamamı `POST`
yöntemiyle ve JSON gövdeyle çağrılır. Anahtar gerekmez. Gövdedeki `customer_id`, o görüşmenin sırrıdır.

**Hata davranışı (tüm uç noktalar için ortaktır):** İşlem gerçekleştirilmediğinde servis HTTP 200 ile `ok: false` döndürür
ve `error` alanında bir açıklama verir. Servis başarısız olursa HTTP 500 döner. Bu durumda hiçbir şey kaydedilmez ve
çağrı tekrarlanabilir.

**Kimlik önce geçmelidir:** Müşterinin kimliği iki güvenlik sorusuyla doğrulanmadan hesap bilgisi alınmamalı ve söz
kaydedilmemelidir. Servis bunu yalnızca `get_account` için zorlar. Diğer uç noktalarda sırayı korumak ajanın
sorumluluğundadır.

**Servis brief'i uygulamaz:** Servis, tatil gününe veya son tarihten sonrasına verilen bir sözü şikâyet etmeden kaydeder.
Tarih, tutar, saat aralığı ve sonuç-neden eşlemelerinin brief'e uygunluğu tamamen ajanın işidir.

POST /verify_identity

Bir güvenlik yanıtını denetler. Toplam üç yanlış yanıt kontrolü sonlandırır.

**İstek alanları:**
- `customer_id` (metin, zorunlu)
- `field` (zorunlu): `date_of_birth` veya `phone_last4`
- `answer` (metin, zorunlu): `date_of_birth` için tarih `YYYY-MM-DD` biçiminde olmalıdır. Arayanlar tarihi sözcüklerle
söyler, dönüştürmek ajanın işidir. `phone_last4` için dört rakam gönderilir.

**Yanıt alanları:** `ok` (mantıksal), `error` (metin), `verified` (mantıksal), `wrong_answers_left` (tam sayı), `all_verified`
(mantıksal), `hint` (yalnızca `day_month_swapped` olabilir).

**Hata davranışı:** Okunamayan bir tarih `ok: false` ile reddedilir ve yanlış yanıt olarak sayılmaz. `hint:
day_month_swapped`, gün ile ayın ters söylendiğini gösterir. HTTP 500'de hiçbir şey kaydedilmez ve çağrı
tekrarlanabilir.

POST /get_account

Gecikmiş ürünleri döndürür. İki soru da geçilene kadar reddedilir.

**İstek alanları:** `customer_id` (metin, zorunlu).

**Yanıt alanları:** `ok`, `error` ve `products` dizisi. Her ürün şu alanlara sahiptir:
- `product_id` (metin)
- `type`: `credit_card`, `personal_loan` veya `car_loan`
- `card_last4` (yalnızca kredi kartı)
- `past_due` (sayı)
- `days_late` (tam sayı)
- `minimum_payment` (sayı, yalnızca kredi kartı)
- `instalment_amount` (sayı, yalnızca krediler)
**Hata davranışı:** Kimlik doğrulaması tamamlanmadan çağrılırsa `ok: false` ve `error` döner. HTTP 500'de hiçbir şey
kaydedilmez ve çağrı tekrarlanabilir.

POST /create_promise

Bir ödeme sözü kaydeder. Ürün başına bir söz kaydedilir.

**İstek alanları (hepsi zorunlu):** `customer_id` (metin), `product_id` (metin), `amount` (sayı), `date` (tarih,
`YYYY-MM-DD`).

**Yanıt alanları:** `ok`, `error`, `promise_id` (metin).

**Hata davranışı:** İşlem yapılamazsa `ok: false` döner. HTTP 500'de hiçbir şey kaydedilmez ve çağrı tekrarlanabilir.
Servis, iş günü, son tarih veya taban tutar denetimi yapmaz. Bunlar ajanın sorumluluğundadır. Sözün kaydedildiği
müşteriye yalnızca `ok: true` yanıtından sonra söylenir.

POST /add_note

Bir ürünün neden sözsüz bittiğini kaydeder. Bu uç nokta Brief B içindir. Brief A hiçbir zaman `add_note` kullanmaz.

**İstek alanları:** `customer_id` (zorunlu), `product_id` (zorunlu), `reason` (zorunlu): `refused`, `hardship`, `dispute`,
`paid` veya `service_error`. `text` isteğe bağlı bir metindir.

**Yanıt alanları:** `ok`, `error`.

**Hata davranışı:** İşlem yapılamazsa `ok: false` döner. HTTP 500'de hiçbir şey kaydedilmez ve çağrı tekrarlanabilir.

POST /schedule_callback

Geri arama planlar.

**İstek alanları (hepsi zorunlu):** `customer_id` (metin), `date` (tarih, `YYYY-MM-DD`), `time` (metin, `HH:MM`).

**Yanıt alanları:** `ok`, `error`.

**Hata davranışı:** İşlem yapılamazsa `ok: false` döner. HTTP 500'de hiçbir şey kaydedilmez ve çağrı tekrarlanabilir.
Servis saat aralığını veya iş günü şartını denetlemez. Bunu ajan denetler.

POST /transfer_to_agent

Görüşmeyi bir insana devreder.

**İstek alanları (hepsi zorunlu):** `customer_id` (metin), `queue` (`restructuring`, `general` veya `support`).

**Yanıt alanları:** `ok`, `error`.

**Hata davranışı:** İşlem yapılamazsa `ok: false` döner. HTTP 500'de hiçbir şey kaydedilmez ve çağrı tekrarlanabilir.

POST /close_call

Görüşmeyi bitirir. Her zaman son araç çağrısıdır ve sonrasında hiçbir çağrı kabul edilmez.

**İstek alanları:** `customer_id` (zorunlu), `outcome` (zorunlu): `promise`, `paid`, `transferred`, `no_promise`,
`not_verified` veya `not_reached`. `reason` metindir. `promise` ve `paid` dışındaki her sonuç için zorunludur ve geçerli
değerler brief'teki tablodadır.

**Yanıt alanları:** `ok`, `error`.

**Hata davranışı:** İşlem yapılamazsa `ok: false` döner. HTTP 500'de hiçbir şey kaydedilmez ve çağrı tekrarlanabilir.
Servis sonuç ile nedenin brief'teki tabloya uyup uymadığını denetlemez.
Ek: OpenAPI (JSON)
{
 "openapi": "3.0.3",
 "info": {
  "title": "Freya Bank collections tools",
  "version": "0.3-draft",
  "description": "Mock of the bank's systems for the Build the Builder hackathon. We host it. Every call is written to the ledger the scorer reads. The service does not enforce
the brief: it records a promise on a holiday or past the deadline without complaint. Following the brief is the agent's job. The seven tool calls need no key: the customer_id in
the body is the secret of one call."
 },
 "servers": [
  {
    "url": "https://freya-bank-mock.freyavoice.workers.dev/v1"
  }
 ],
 "components": {},
 "paths": {
  "/verify_identity": {
    "post": {
     "summary": "Check one security answer. Three wrong answers in total end the check.",
     "requestBody": {
      "required": true,
      "content": {
       "application/json": {
        "schema": {
         "type": "object",
         "properties": {
           "customer_id": {
            "type": "string"
           },
           "field": {
            "type": "string",
            "enum": [
              "date_of_birth",
              "phone_last4"
            ]
           },
           "answer": {
            "type": "string",
             "description": "For date_of_birth: the date as YYYY-MM-DD. Callers say dates in words, the agent converts. An unreadable value is rejected with ok=false and is not
counted as a wrong answer. For phone_last4: four digits."
           }
         },
         "required": [
           "customer_id",
           "field",
           "answer"
         ]
        }
       }
      }
     },
     "responses": {
      "200": {
       "description": "`ok` is false when the action was not carried out.",
       "content": {
        "application/json": {
         "schema": {
           "type": "object",
           "properties": {
            "ok": {
              "type": "boolean"
            },
            "error": {
              "type": "string"
            },
            "verified": {
              "type": "boolean"
            },
            "wrong_answers_left": {
              "type": "integer"
            },
            "all_verified": {
              "type": "boolean"
            },
            "hint": {
              "type": "string",
              "enum": [
                "day_month_swapped"
              ]
            }
        }
      }
     }
    }
   },
   "500": {
    "description": "The service failed. Nothing was recorded. The call may be repeated."
   }
  }
 }
},
"/get_account": {
 "post": {
  "summary": "The late products. Refused until both questions are passed.",
  "requestBody": {
   "required": true,
   "content": {
    "application/json": {
     "schema": {
      "type": "object",
      "properties": {
        "customer_id": {
         "type": "string"
        }
      },
      "required": [
        "customer_id"
      ]
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "`ok` is false when the action was not carried out.",
    "content": {
     "application/json": {
      "schema": {
        "type": "object",
        "properties": {
         "ok": {
          "type": "boolean"
         },
         "error": {
          "type": "string"
         },
         "products": {
          "type": "array",
          "items": {
           "type": "object",
           "properties": {
            "product_id": {
             "type": "string"
            },
            "type": {
             "type": "string",
             "enum": [
               "credit_card",
               "personal_loan",
               "car_loan"
             ]
            },
            "card_last4": {
             "type": "string",
             "description": "credit cards only"
            },
            "past_due": {
             "type": "number"
            },
            "days_late": {
             "type": "integer"
            },
            "minimum_payment": {
             "type": "number",
             "description": "credit cards only"
            },
            "instalment_amount": {
             "type": "number",
             "description": "loans only"
            }
           }
          }
         }
        }
      }
     }
    }
   },
   "500": {
    "description": "The service failed. Nothing was recorded. The call may be repeated."
   }
  }
 }
},
"/create_promise": {
 "post": {
  "summary": "Record a payment promise. One per product.",
  "requestBody": {
   "required": true,
   "content": {
    "application/json": {
     "schema": {
      "type": "object",
      "properties": {
        "customer_id": {
         "type": "string"
        },
        "product_id": {
         "type": "string"
        },
        "amount": {
         "type": "number"
        },
        "date": {
         "type": "string",
         "format": "date"
        }
      },
      "required": [
        "customer_id",
        "product_id",
        "amount",
        "date"
      ]
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "`ok` is false when the action was not carried out.",
    "content": {
     "application/json": {
      "schema": {
        "type": "object",
        "properties": {
         "ok": {
          "type": "boolean"
         },
         "error": {
          "type": "string"
         },
         "promise_id": {
          "type": "string"
         }
        }
      }
     }
    }
   },
   "500": {
    "description": "The service failed. Nothing was recorded. The call may be repeated."
   }
  }
 }
},
"/add_note": {
 "post": {
  "summary": "Brief B: record why a product ended without a promise.",
  "requestBody": {
   "required": true,
   "content": {
    "application/json": {
     "schema": {
      "type": "object",
      "properties": {
        "customer_id": {
         "type": "string"
        },
        "product_id": {
         "type": "string"
        },
        "reason": {
         "type": "string",
         "enum": [
           "refused",
           "hardship",
           "dispute",
           "paid",
           "service_error"
         ]
        },
        "text": {
         "type": "string"
        }
      },
      "required": [
        "customer_id",
        "product_id",
        "reason"
      ]
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "`ok` is false when the action was not carried out.",
    "content": {
     "application/json": {
      "schema": {
        "type": "object",
        "properties": {
         "ok": {
           "type": "boolean"
         },
         "error": {
           "type": "string"
         }
        }
      }
     }
    }
   },
   "500": {
    "description": "The service failed. Nothing was recorded. The call may be repeated."
   }
  }
 }
},
"/schedule_callback": {
 "post": {
  "summary": "Book a callback.",
  "requestBody": {
   "required": true,
   "content": {
    "application/json": {
     "schema": {
      "type": "object",
      "properties": {
        "customer_id": {
         "type": "string"
        },
        "date": {
         "type": "string",
         "format": "date"
        },
        "time": {
         "type": "string",
         "description": "HH:MM"
        }
      },
      "required": [
        "customer_id",
        "date",
        "time"
      ]
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "`ok` is false when the action was not carried out.",
    "content": {
     "application/json": {
      "schema": {
        "type": "object",
        "properties": {
         "ok": {
           "type": "boolean"
         },
         "error": {
           "type": "string"
         }
        }
      }
     }
    }
   },
   "500": {
    "description": "The service failed. Nothing was recorded. The call may be repeated."
   }
  }
 }
},
"/transfer_to_agent": {
 "post": {
  "summary": "Hand the call to a person.",
  "requestBody": {
   "required": true,
   "content": {
    "application/json": {
     "schema": {
      "type": "object",
      "properties": {
        "customer_id": {
         "type": "string"
        },
        "queue": {
         "type": "string",
         "enum": [
           "restructuring",
           "general",
           "support"
         ]
        }
      },
      "required": [
        "customer_id",
        "queue"
      ]
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "`ok` is false when the action was not carried out.",
    "content": {
     "application/json": {
      "schema": {
        "type": "object",
        "properties": {
         "ok": {
           "type": "boolean"
         },
         "error": {
           "type": "string"
         }
        }
      }
     }
    }
   },
   "500": {
    "description": "The service failed. Nothing was recorded. The call may be repeated."
   }
  }
 }
},
"/close_call": {
   "post": {
    "summary": "End the call. Always the last tool call. Nothing is accepted after it.",
    "requestBody": {
     "required": true,
     "content": {
      "application/json": {
       "schema": {
        "type": "object",
        "properties": {
          "customer_id": {
           "type": "string"
          },
          "outcome": {
           "type": "string",
           "enum": [
             "promise",
             "paid",
             "transferred",
             "no_promise",
             "not_verified",
             "not_reached"
           ]
          },
          "reason": {
           "type": "string",
           "description": "Required for every outcome except promise and paid. See the table in the brief."
          }
        },
        "required": [
          "customer_id",
          "outcome"
        ]
       }
      }
     }
    },
    "responses": {
     "200": {
      "description": "`ok` is false when the action was not carried out.",
      "content": {
       "application/json": {
        "schema": {
          "type": "object",
          "properties": {
           "ok": {
             "type": "boolean"
           },
           "error": {
             "type": "string"
           }
          }
        }
       }
      }
     },
     "500": {
      "description": "The service failed. Nothing was recorded. The call may be repeated."
     }
    }
   }
  }
 }
}

=== FILE: SSS.md ===

# SSS – Brief A Hakkında Sık Sorulan Sorular

**1. Müşteri telefonu açıp sadece 'Alo' dese bunu kimlik onayı sayabilir miyim?**

Hayır. Yalnızca 'evet', 'benim', 'dinliyorum' veya müşterinin kendi adı onay sayılır. 'Alo', 'buyurun' ve 'kim arıyor' sayılmaz. Bir kez daha sorun. İkinci sorudan sonra da belirsizse görüşmeyi `not_reached` / `unclear` olarak kapatın. Müşterinin sorusu varsa önce onu yanıtlayın. Ardından gelen soru ikinci sorudur.

**2. Kimlik doğrulamada kaç yanlış yanıta izin var ve gün ile ay ters söylenirse ne olur?**

Hangi soruda olursa olsun toplam üç yanlış yanıt kontrolü sonlandırır. Bu durumda borcu bildirmeyin, mobil uygulamayı veya bir şubeyi önerin ve `not_verified` / `failed` olarak kapatın. Gün ve ay ters söylenirse `verify_identity` `hint: day_month_swapped` döndürür. Müşteriden ayı kelimeyle söylemesini isteyin. Bu yeniden deneme yanlış yanıt sayılmaz.

**3. Son tarih nasıl hesaplanır?**

`days_late` 1 ile 7 arasındaysa bant early'dir: son tarih bugünden sonraki 12. iş günüdür ve 1 ikna denemesi vardır. `days_late` 8 ile 45 arasındaysa bant late'tir: son tarih bugünden sonraki 7. iş günüdür ve 2 ikna denemesi vardır. İş günleri Pazartesi'den Cuma'ya kadardır. Freya Bank tatilleri hariçtir: 2026-10-26, 2026-11-11, 2026-11-26, 2026-12-25 ve 2027-01-01.

**4. Müşteriye 'ne kadar ödeyebilirsiniz?' diye sorabilir miyim?**

Hayır. Önce vadesi geçmiş tutarın tamamı istenir. Her tutarı ajan önerir. Müşteri tutarın tamamının çok fazla olduğunu söylerse taban tutar bir kez önerilir. Taban tutar kredi kartında `minimum_payment`, ihtiyaç ve taşıt kredisinde `instalment_amount` değeridir. Kalanın vadesi geçmiş olarak kalacağı da söylenir.

**5. Başka biri telefona cevap verirse geri arama için hangi saat aralığı geçerlidir?**

Bu yanıt duyuruyla değişmiştir. Geri arama bugün veya sonraki iş günü için, 10:00 ile 18:00 arasında (her ikisi dahil) ve `now` değerinden sonra olmalıdır. Brief'teki eski değer olan 19:00 geçersizdir. Tarih ve saatle `schedule_callback` çağırın, ardından `not_reached` / `callback` olarak kapatın. Kişi aralığın dışında bir zaman önerirse bir kez başka zaman sorun. O da aralığın dışındaysa planlama yapmayın ve `not_reached` / `no_callback` olarak kapatın.

**6. Ne zaman `create_promise` çağrılır ve söz kaydedildikten sonra ne söylenir?**

Ürün, tutar ve tarih geri okunur, 'Onaylıyor musunuz?' diye sorulur. `create_promise` yalnızca net bir evetten sonra çağrılır. Sözün kaydedildiği yalnızca `ok: true` yanıtından sonra söylenir. Ardından 'Bu sözü onaylayan bir kısa mesaj alacaksınız.' cümlesi söylenir. Çağrı başarısız olursa bir kez daha denenir. Yine başarısız olursa kaydedilemediği söylenir ve `no_promise` / `service_error` ile kapatılır. Müşteriye teşekkür edilip veda edildikten sonra tek bir `close_call` yapılır. Bu, son araç çağrısıdır.
