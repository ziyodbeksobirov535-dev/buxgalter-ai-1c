# E-DOCS ulanishi

Login/parol autentifikatsiyasi, firma STIRi, kiruvchi imzolangan EHF ro'yxati va har bir fakturaning tovar qatorlari jonli hisobda tekshirildi. Brauzer, reklama oynasi, faylni qo'lda yuklash va `data/edocs-profile.json` bu API yo'li uchun kerak emas.

Bu saytning o'z ochiq mijozida ishlatiladigan API; ommaviy hamkorlik API shartnomasi deb hisoblanmasin. Sayt formati o'zgarsa parserni moslash talab qilinadi.

## Sozlash

Egasi → Sozlamalar → Joriy firma: firma STIRini kiriting, E-DOCS manbasini yoqing, login/parolni kiriting va saqlang. Parol bazada shifrlanadi, API javoblarida maskalanadi. Login o'zgarsa eski parol o'chiriladi. “Ulanishni tekshirish” faqat login va STIRni tekshiradi. EHF sahifasida yuklash fakturalarni API orqali olib mavjud 1C bilan solishtirish mantiqiga beradi.

Bulutda `EDOCS_LOGIN` va `EDOCS_PASSWORD` maxfiy bindinglari faqat `api.edocs.uz` manzili uchun; `EDOCS_INN` — kutiladigan firma STIR/JShShIRi. Mahalliy firma login/paroli ikkalasi ham yo'q bo'lsa, ushbu bindinglar faqat firma STIRi `EDOCS_INN` bilan aynan mos kelganda ishlatiladi. Mahalliy login va bulut paroli aralashtirilmaydi.

Boshqa kompyuterdagi SQLite bazasi bu bulut bazasidan alohida. Maxfiy qiymatlarni chatga, kodga, testlarga yoki Gitga yozmang. Proksi bindinglarini mavjud HTTPS proksi yo'li orqali ishlating, qiymatlarini chiqarmang.

## Buyruq orqali tekshirish

Oddiy API yo'li uchun requirements.txt yetarli; Playwright kerak emas.

```sh
.venv/bin/python -m services_edocs_api
.venv/bin/python -m services_edocs_api --fetch
.venv/bin/python -m services_edocs_api --fetch --from-date 2026-01-01 --to-date 2026-01-31
```

Birinchi buyruq login/profilni, ikkinchisi barcha kiruvchi imzolangan fakturalarni, uchinchisi berilgan davrni tekshiradi. Natijada faqat holat yoki fakturalar soni chiqadi; login, parol, STIR, kontragentlar, summalar va token chiqmaydi. Token faqat bitta ish davomida xotirada turadi. Login avtomatik qayta urinilmaydi va redirect kuzatilmaydi.

## O'qish va to'liqlik tekshiruvlari

- `POST /v1.0.1/loginpassword` — faqat autentifikatsiya.
- `GET /v1.0.1/profile` — tanlangan firma STIRi mosligini tekshirish.
- `GET /v1.0.1/documents` — `type=factura`, `io=in`, `status=signed`; sahifalab o'qish.
- `GET /v1.0.1/documents/factura/{id}` — tovar qatorlari, MXIK, miqdor, narx, QQS va summalar.

So'rovlar ketma-ket yuboriladi. Xaridor STIRi har bir fakturada tekshiriladi. Takroriy ID, noto'g'ri sahifalash, yuklash vaqtida o'zgargan umumiy son, yetishmagan qatorlar yoki qator summalari mos kelmasa to'liq operatsiya xato bilan tugaydi; qisman natija qaytarilmaydi. Hujjat chegarasi 1000; undan ko'p bo'lsa CLI/API funksiyasida kichikroq sana davrini tanlash kerak.

Hozir faqat kiruvchi imzolangan `factura` hujjatlari olinadi; imzolanmagan, rad etilgan, hybridInvoice va shartnomalar ushbu importga kirmaydi. Tovar/xizmat schyotlari mavjud 1C mantiqi bilan tekshirilishi kerak. Jadval bo'yicha fon sinxronlash hali qo'shilmagan; dastur yuklash so'rovi yoki CLI orqali avtomatik API o'qishini bajaradi.

## Qoralama yaratish bo'yicha tekshiruv

Sayt mijozida “Saqlash” va “Saqlash va imzolash” alohida. Umumiy saqlash yo'li `POST /documents/{type}/`, imzo esa alohida `/sign` chaqiruvida. `factura` va `contract` qoralamalarini ro'yxatdan o'qish jonli hisobda ishladi.

Biroq sayt login/parol rejimida “faqat ko'rish, ma'lumotlarni o'zgartirish cheklangan” deb ogohlantiradi. Qoralamalar ro'yxatining HTTP 200 javobi ularni yaratish huquqini tasdiqlamaydi. Haqiqiy qoralama yaratilmadi, hujjat saqlash/imzolash/yuborish endpointlari chaqirilmadi. Shu hisobda yaratish ruxsati tasdiqlanmaguncha yozish funksiyasini ochmang. 1C yozish qulfi ham o'zgarmaydi.

## Eski brauzer adapteri

`services_edocs.py` va `requirements-edocs.txt` oldingi eksperimental adapter sifatida qoldirilgan; asosiy EHF yuklash undan foydalanmaydi. Uning Playwright selektorlari va namunaviy JSON eksport shartnomasi haqiqiy saytga moslangan deb hisoblanmasin.

Avval Chromium TLS xatosi berdi va doimiy NSS ishonch bazasiga CA qo'shish avtomatik ko'rikda rad etildi. Bu o'zgarish bajarilmadi, TLS tekshiruvi o'chirilmadi. Ishlayotgan API yo'li HTTPX va muhitning mavjud CA ishonch sozlamalarini ishlatadi.
