# E-DOCS sinov adapteri

Bu adapter hali doc.edocs.uz bilan jonli tasdiqlanmagan. Login tayyor bo'lgach haqiqiy DOM va eksportni moslash zarur. Avtomatik jadval bo'yicha sinxronlash hali yoqilmagan.

## O'rnatish

Loyiha papkasida:

```sh
.venv/bin/python -m pip install -r requirements-edocs.txt
PLAYWRIGHT_BROWSERS_PATH="$PWD/data/playwright-browsers" .venv/bin/python -m playwright install chromium
```

Muhitda `chromium` mavjud bo‘lsa, adapter undan foydalanadi va brauzerni qayta yuklash shart emas.

Linuxda Chromium tizim kutubxonalari ham kerak bo'lishi mumkin; Playwright xatosidagi paketlar yoki `playwright install-deps chromium` orqali o'rnatiladi.

## Sozlash

Egasi → Sozlamalar → Joriy firma: E-DOCS login/parolini kiriting, E-DOCS manbasini yoqing va saqlang. Parol bazada shifrlanadi, API javoblarida maskalanadi. Login o'zgarsa eski parol o'chiriladi. Login faqat bir marta uriniladi; cookie, trace va screenshot saqlanmaydi.

`data/edocs-profile.json` sayt tekshirilgach mahalliy yaratiladi (gitga kirmaydi). Quyidagilar **namunaviy kalitlar, haqiqiy selektorlar emas**:

```json
{
  "login_url": "https://doc.edocs.uz/HAQIQIY_LOGIN_YOLI",
  "incoming_url": "https://doc.edocs.uz/HAQIQIY_KIRUVCHI_YOLI",
  "username": "HAQIQIY_LOGIN_SELEKTORI",
  "password": "HAQIQIY_PAROL_SELEKTORI",
  "submit": "HAQIQIY_KIRISH_TUGMASI",
  "authenticated": "KIRILGANINI_TASDIQLOVCHI_ELEMENT",
  "ad_close": "REKLAMA_YOPISH_TUGMASI",
  "firm_inn": "FAQAT_STIR_MATNI_BOR_ELEMENT",
  "export_json": "KIRUVCHI_EHF_JSON_EKSPORT_TUGMASI"
}
```

Reklama ixtiyoriy: tugma 8 soniyagacha kutiladi, bosilgach yo'qolgani tekshiriladi. Barcha eksportdan oldin tanlangan firmaning STIRi solishtiriladi. Noto'g'ri firma yoki noma'lum formatda import rad etiladi. Olingan fayl vaqtinchalik brauzer kontekstida turadi va kontekst yopilgach o'chadi.

“Ulanishni tekshirish” endi saytning ochiq mijozida ishlatiladigan `https://api.edocs.uz/v1.0.1/loginpassword` (POST) va `/profile` (GET) orqali login va firma STIRini tekshiradi. Login javobidagi `token` faqat xotirada turadi. Redirect va qayta login urinishlari bajarilmaydi. Bu endpointlar ochiq sayt JavaScriptidan tasdiqlandi, lekin jonli hisob bilan hali tekshirilmagan. Brauzer profili ulanish tekshiruvi uchun kerak emas; eksport jarayoni uchun hali kerak. EHF sahifasidagi yuklash esa eksportni olib mavjud 1C solishtirishiga beradi. Imzolash/yuborish tugmalari bosilmaydi, 1C yozish qulfi o'zgarmaydi.

## Eksport shartnomasi va qolgan ish

Joriy parser `{"buyer_inn":"...","invoices":[...]}` JSON kutadi. Fakturada `id, number, date, seller_inn, seller_name, total, items`; har tovar qatorida `name, qty, price, total`, ixtiyoriy `vat_rate, ikpu` bor. Sana YYYY-MM-DD. Bu E-DOCS haqiqiy formati deb da'vo qilinmaydi: eksport namunasi bilan moslashtirish zarur. Agar sayt faqat XLSX/XML bersa tegishli parser, hujjat sahifalari bo'lsa pagination, davr filtri va to'liq yuklanganlik tekshiruvi qo'shiladi. Hozir faqat eksport tugmasi bergan fayl olinadi.

Jonli sinovdan keyingi bosqich: tasdiqlangan yo'llar va selektorlar, kiruvchi EHF formatini moslash, takror yuklashda dublikatlar, davr bo'yicha jadval asosida sinxronlash, sessiya eskirishi va oxirgi muvaffaqiyat vaqtini ko'rsatish. Parolni chat, repozitoriy yoki hujjatlarga yozmang.


## Bulutda maxfiy kirish qiymatlari

Bu bulutdagi SQLite bazasi foydalanuvchining boshqa kompyuteridagi dastur bazasidan alohida. Boshqa kompyuterda saqlangan parol avtomatik bu muhitga kelmaydi.

Shu muhitdagi jonli tekshiruv uchun muhit sozlamalarida `EDOCS_LOGIN` va `EDOCS_PASSWORD` maxfiy bindinglarini `api.edocs.uz` HTTPS manzili uchun kiriting; `EDOCS_INN` muhit o'zgaruvchisiga kutiladigan firma STIR/JShShIRini kiriting. Qiymatlar chatga yoki Gitga yozilmaydi. Proksi bindinglari mavjud HTTPS proksi yo'li orqali yuboriladi; qiymatlarni chiqarish yoki placeholderni raw parol deb tekshirish kerak emas.

```sh
.venv/bin/python -m services_edocs_api
```

Buyruq faqat muvaffaqiyat/xato matnini chiqaradi; login, parol, STIR va tokenni chiqarmaydi. Muhit sozlamalari qo'llangach bir marta sinash kerak. Bu tekshiruv hujjatlarni olish yoki o'zgartirishni bajarmaydi.

Bu muhitda Chromium `ERR_CERT_AUTHORITY_INVALID` berdi. Chromium doimiy NSS bazasiga proksi CA qo'shish avtomatik tasdiqlash tizimi tomonidan keng HTTPS ishonch o'zgarishi sifatida rad etildi. O'zgarish bajarilmadi, TLS tekshiruvi o'chirilmadi. HTTPX bilan tizimning mavjud CA sozlamalari orqali `doc.edocs.uz` ochildi; API yo'li muqobil sifatida tayyorlandi. Brauzerning haqiqiy saytdagi TLS ishlashi hozircha tasdiqlanmagan.
