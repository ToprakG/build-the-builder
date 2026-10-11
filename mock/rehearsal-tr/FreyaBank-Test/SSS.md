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
