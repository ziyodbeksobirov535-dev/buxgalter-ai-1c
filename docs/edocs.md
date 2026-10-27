# E-DOCS ulanishi

Login/parol autentifikatsiyasi, firma STIRi, kirim/chiqim EHF ro'yxati va har bir fakturaning tovar qatorlari jonli hisobda tekshirildi. Brauzer, reklama oynasi, faylni qo'lda yuklash va `data/edocs-profile.json` bu API yo'li uchun kerak emas.

Bu saytning o'z ochiq mijozida ishlatiladigan API; ommaviy hamkorlik API shartnomasi deb hisoblanmasin. Sayt formati o'zgarsa parserni moslash talab qilinadi.

## Sozlash

Egasi → Sozlamalar → Joriy firma: firma STIRini kiriting va saqlang. Keyin Sozlamalar → Ulanishlar bo‘limida E-DOCS manbasini yoqing, login/parolni kiriting va saqlang. Parol bazada shifrlanadi, API javoblarida maskalanadi. Login o'zgarsa eski parol o'chiriladi. “Ulanishni tekshirish” faqat login va STIRni tekshiradi. EHF sahifasida yuklash fakturalarni API orqali olib mavjud 1C bilan solishtirish mantiqiga beradi.

Bulutda `EDOCS_LOGIN` va `EDOCS_PASSWORD` maxfiy bindinglari faqat `api.edocs.uz` manzili uchun; `EDOCS_INN` — kutiladigan firma STIR/JShShIRi. Mahalliy firma login/paroli ikkalasi ham yo'q bo'lsa, ushbu bindinglar faqat firma STIRi `EDOCS_INN` bilan aynan mos kelganda ishlatiladi. Mahalliy login va bulut paroli aralashtirilmaydi.

Boshqa kompyuterdagi SQLite bazasi bu bulut bazasidan alohida. Maxfiy qiymatlarni chatga, kodga, testlarga yoki Gitga yozmang. Proksi bindinglarini mavjud HTTPS proksi yo'li orqali ishlating, qiymatlarini chiqarmang.

## Buyruq orqali tekshirish

Oddiy API yo'li uchun requirements.txt yetarli; Playwright kerak emas.

```sh
.venv/bin/python -m services_edocs_api
.venv/bin/python -m services_edocs_api --fetch
.venv/bin/python -m services_edocs_api --fetch --from-date 2026-01-01 --to-date 2026-01-31
```

Birinchi buyruq login/profilni, ikkinchisi barcha kirim/chiqim fakturalarni, uchinchisi berilgan davrni tekshiradi. Natijada faqat holat yoki fakturalar soni chiqadi; login, parol, STIR, kontragentlar, summalar va token chiqmaydi. Token faqat bitta ish davomida xotirada turadi. Login avtomatik qayta urinilmaydi va redirect kuzatilmaydi.

## O'qish va to'liqlik tekshiruvlari

- `POST /v1.0.1/loginpassword` — faqat autentifikatsiya.
- `GET /v1.0.1/profile` — tanlangan firma STIRi mosligini tekshirish.
- `GET /v1.0.1/documents` — `type=factura`, kirim va chiqim, saytda mavjud holatlar bo'yicha sahifalab o'qish.
- `GET /v1.0.1/documents/factura/{id}` — tovar qatorlari, MXIK, miqdor, narx, QQS va summalar.

So'rovlar ketma-ket yuboriladi. Kirimda xaridor, chiqimda sotuvchi STIRi tekshiriladi. Takroriy ID, noto'g'ri sahifalash, yuklash vaqtida o'zgargan umumiy son yoki sarlavha xatosida to'liq operatsiya to'xtaydi; qisman natija qaytarilmaydi. Tovar qatorlari yoki ularning summasi mos kelmasa faktura sarlavhasi saqlanadi, tafsilot tekshirilmagani ko'rsatiladi. Hujjat chegarasi 10000; undan ko'p bo'lsa kichikroq sana davrini tanlang.

EHF barcha mavjud holatlari bilan kirim/chiqim yo'nalishlarida olinadi. Imzo kutilmoqda va tasdiqlanganlar 1C bilan solishtiriladi. O'chirilgan EHF uchun 1C da faol hujjat borligi tekshiriladi. Qoralama va rad etilganlar odatdagi sverkaga kirmaydi. Filtrlar, teskari nazorat va qamrov cheklovlari [EHF sverka yo'riqnomasida](ehf-reconciliation.md) yozilgan. Jadval bo'yicha fon sinxronlash hali yoqilmagan.

## Qoralama yaratish bo'yicha tekshiruv

Sayt mijozida “Saqlash” va “Saqlash va imzolash” alohida. Umumiy saqlash yo'li `POST /documents/{type}/`, imzo esa alohida `/sign` chaqiruvida. `factura` va `contract` qoralamalarini ro'yxatdan o'qish jonli hisobda ishladi.

Biroq sayt login/parol rejimida “faqat ko'rish, ma'lumotlarni o'zgartirish cheklangan” deb ogohlantiradi. Qoralamalar ro'yxatining HTTP 200 javobi ularni yaratish huquqini tasdiqlamaydi. Haqiqiy qoralama yaratilmadi, hujjat saqlash/imzolash/yuborish endpointlari chaqirilmadi. Shu hisobda yaratish ruxsati tasdiqlanmaguncha yozish funksiyasini ochmang. 1C yozish qulfi ham o'zgarmaydi.

## Eski brauzer adapteri

`services_edocs.py` va `requirements-edocs.txt` oldingi eksperimental adapter sifatida qoldirilgan; asosiy EHF yuklash undan foydalanmaydi. Uning Playwright selektorlari va namunaviy JSON eksport shartnomasi haqiqiy saytga moslangan deb hisoblanmasin.

Avval Chromium TLS xatosi berdi va doimiy NSS ishonch bazasiga CA qo'shish avtomatik ko'rikda rad etildi. Bu o'zgarish bajarilmadi, TLS tekshiruvi o'chirilmadi. Ishlayotgan API yo'li HTTPX va muhitning mavjud CA ishonch sozlamalarini ishlatadi.

## BUXGALTER AI ichida foydalanish

1. Sozlamalar → Joriy firma bo‘limida STIR/JShShIRni saqlang. Sozlamalar → Ulanishlar bo‘limida “E-DOCS orqali o‘qish”ni yoqing, login/parolni kiriting va saqlang.
2. “Ulanishni tekshirish” tugmasini bosing.
3. “EHF” sahifasini oching. Kerak bo'lsa boshlanish/tugash sanasini tanlang; bo'sh davr API ro'yxatlaridagi barcha mavjud kirim/chiqim fakturalarni holatlari bilan oladi (10000 gacha). Kiruvchi o'chirilganlar bo'yicha qamrov cheklovini ko'ring.
4. “E-DOCS’dan yuklash” tugmasini bosing. Manba, oxirgi yuklash vaqti va natijalar ko'rinadi.

E-DOCS natijalari ushbu integratsiyada faqat o'qiladi: UI yozish tugmalarini yashiradi va server ham yozishni rad etadi. AI tasdiqlash kartasi chiqarmaydi. Haqiqiy fakturalar demo 1C bilan solishtirilmaydi; jonli 1C ulanmagan bo'lsa “tekshirilmagan” holatida ko'rsatiladi. Muvaffaqiyatsiz qayta yuklash oldingi jadvalni o'chirmaydi.

Lokal kompyuterda `start.command` bilan ishlatayotgan foydalanuvchi loyiha nusxasini GitHub'dagi `work` branchidan yangilashi kerak. Avval mahalliy o'zgarishlarni `git status` bilan ko'ring; ularni reset/clean bilan o'chirmang. Git orqali yangilash mumkin bo'lsa:

```sh
git fetch origin
git switch work
git pull --ff-only origin work
./start.command
```

Buyruqlar loyiha papkasida bajariladi. Branch almashish yoki pull mahalliy o'zgarish sabab to'xtasa, foydalanuvchi ishini saqlab masalani alohida hal qiling. `.env` va `data/` o'chirilmaydi. Bulutga kiritilgan maxfiy qiymatlar lokal kompyuterga avtomatik kelmaydi; lokal dastur sozlamalarida kiritiladi.
