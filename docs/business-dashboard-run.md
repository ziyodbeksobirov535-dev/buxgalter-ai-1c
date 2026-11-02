# Firma egasi panelini ishga tushirish

## Lokal yangilash

Loyiha `work` branchida. Avval `git status` bilan mahalliy o'zgarishlarni ko'ring; ularni reset/clean bilan o'chirmang.
Yangilangan kodda:

```sh
git pull --ff-only origin work
.venv/bin/python -m pip install -r requirements.txt
NO_OPEN=1 ./start.command
```

`start.command` mavjud virtual muhitda PDF uchun `reportlab` yetishmasa requirements orqali o'rnatadi.
`data/`, `.env` va shifrlash kaliti saqlanadi. Serverning navbatdagi ishga tushishida `users` roli cheklovi
atomar migratsiya qilinadi: foydalanuvchi IDsi, parol xeshi, sessiya va firma biriktirishlari ko'chiriladi.
Eski `owner` qiymati administrator sifatida qoladi; uning huquqi firma egasiga aylantirilmaydi.

## Administrator tayyorlaydigan narsalar

1. Sozlamalar → Foydalanuvchilar: yangi foydalanuvchiga **Firma egasi** rolini bering.
2. Aynan uning firmalarini belgilang. Masalan, A egasiga shartli 1 va 2, B egasiga 3-firma.
3. Buxgalterga **Buxgalter** rolini va o'z firmalarini biriktiring.
4. Ulanishlar orqali haqiqiy firma STIRi va 1C OData ulanishini sozlang. 1C'da tashkilot,
   hujjatlar, hisoblar rejasi va `AccountingRegister_Хозрасчетный` / `_RecordType` o'qish uchun nashr qilingan bo'lsin.
5. Firma sozlamalaridagi qarz mezonlari: dastlab 30 kun va 100 million so'm. Ularni shu firmaga kiruvchi
   buxgalter ham o'zgartira oladi. Umumiy Gemini kalitini faqat administrator AI yordamchi bo'limida saqlaydi.

Firma egasi login qilganda `/business` ochiladi. Mavjud admin/buxgalter paneli o'z manzilida ishlaydi;
ular `/business` orqali biznes ko'rinishini ham tekshirishi mumkin. Egasi profilidan o'z parolini almashtiradi.

## Hisoblash manbalari

- Joriy oy va sana filtri Asia/Tashkent bo'yicha. Qoldiqlar davr oxiriga, daromad/xarajatlar davr ichiga tegishli.
- Har bir jonli so'rovda tashkilot STIR/GUIDi tasdiqlanadi; hujjat va registr so'rovlari
  `Организация_Key` filtri bilan yuboriladi, javobdagi tashkilot ham tekshiriladi.
- Bank/kassa/ombor/qarzlar virtual `Balance`dan. Qoldiq javobida `Account_Key`, `СуммаBalance`,
  subkonto uchun `ExtDimension1..3` va `_Type`, mavjud bo'lsa `КоличествоBalance` ishlatiladi.
- Foyda/zarar faol provodkalardagi `AccountDr_Key`, `AccountCr_Key`, `Сумма` asosida. O'zbekiston hisoblar rejasi:
  90xx savdo daromadi, 93xx/95xx boshqa daromad, 91xx tannarx, 94xx/96xx/98xx xarajatlar.
  99xx bilan yopish provodkalari operatsion natijani qaytadan nollamaydi. Schyot kodlari jonli hisoblar rejasidan olinadi.
- Olinadigan va to'lanadigan qarzlar, berilgan/olingan avanslar alohida. Teskari hisob-kitob qoldig'i
  tasniflanmagan debet/kredit sifatida ko'rinadi; u boshqa qarzdan yashirin ayrilmaydi.
- Qarz yoshi faqat ochiq qoldiqning hujjat subkontosi bo'lsa aniqlanadi. Bog'lanish yo'q yoki hujjat ochilmasa
  noma'lum qoldiq ajratiladi. Fakturaning boshlang'ich summasi qolgan qarz o'rniga ishlatilmaydi.
- 30 kunga yetgan qarz yoki kontragent ochiq qarzi 100 milliondan **katta** bo'lsa belgi chiqadi.
  Har bir tomon/firma alohida. Sana kelishuvdagi to'lov muddati deb talqin qilinmaydi.
- Nashr/ulanish/rekvizit yoki to'liqlik xatosi ogohlantirish va olinmagan qiymat sifatida qaytadi.
  Haqiqiy firma yashirin demo natijasini olmaydi. Demo faqat demo rejimi aniq yoqilganida ishlaydi.

Jonli 1C konfiguratsiyasi ushbu bulutda ulanmagan: yuqoridagi maydonlar va ko'rsatkichlarni lokal bazaning
hisoboti bilan birinchi ishga tushirishda tasdiqlang. O'qish chegarasi 20000 yozuv; oshsa qisman natija
jami deb qaytarilmaydi. 1C ga yozish qulfi saqlangan.

## Gemini va PDF

Biznes chatining faqat uchta vositasi bor: umumiy natija, tanlangan firmada hujjat qidirish va bitta hujjat tafsiloti.
Model firma identifikatorini almashtira olmaydi, umumiy buxgalter vositalari yoki yozish kartasiga o'tmaydi.
Huquq so'rov boshida, vositalar orasida va javob berishdan oldin qayta tekshiriladi.
Suhbat foydalanuvchi/firma/davr/ulanishga bog'langan; firma va davr almashtirilsa aralashmaydi.
Kalit yoki Gemini javobi bo'lmasa mahalliy izoh deb ochiq yoziladi. Bulutda kalit mavjud emas,
Google chaqiruvi soxta SDK mijozida tekshirilgan.

PDF faqat tanlangan firma va davr uchun serverda hosil qilinadi. Unicode shrift va litsenziyasi `static/fonts/`da.
Fayl diskka yozilmaydi, HTTP keshga qo'yilmaydi. Uzoq hisobotda har ro'yxatdan dastlabki 2000 qator olinadi
va qisqartirish yoziladi. 1C rekvizitlari va maxfiy ulanishlar hisobotga kirmaydi.

## Sinov

```sh
.venv/bin/python -m pytest -q
```

Rol/API chegaralari, eski baza migratsiyasi, tashkilot filtri/javobi, to'liq sahifalash, foyda va yopish provodkalari,
qisman to'lov va noma'lum yosh, chegara sonlari, kesh, Gemini vosita sikli, PDF va haqiqiy Chromium brauzeri tekshiriladi.
Brauzer sinovi avtomatik yangilanish, filtr, hujjat, chat, PDF, kechikkan javob va telefon kengligini qamraydi.
Barcha yozuvlar sun'iy; haqiqiy 1C yoki EDO'ga hujjat yozilmaydi.

Ushbu versiya: 348 test o'tdi. Desktop/telefon Chromium sinovi ham bor. Konteynerda yangi sun'iy DB,
login, yangi rol, biznes hisoboti, Unicode PDF va API rad etilishi tekshirildi. Docker builderning DNSi
paketlarni yuklamagani uchun sinov image'i oldindan /tmp ga yuklangan ochiq wheel paketlari bilan,
tarmoqsiz yig'ildi; odatiy Docker build uchun ishlaydigan paket tarmog'i kerak.

## Bepul internet sinoviga tayyorgarlik

Dastur Docker bilan tayyorlanadi (`Dockerfile`, `docker-compose.yml`); reportlab va Unicode shrift obrazga kiradi.
Compose porti hozir faqat `127.0.0.1`ga bog'langan. Internet sinovi uchun quyidagilar xizmat tanlashda bajariladi:

- HTTPS manzili, `WEBAPP_URL` va kerak bo'lsa aniq `ALLOWED_HOSTS`; umumiy `*` ruxsati qo'llanmaydi.
- `data/` doimiy maxfiy volume sifatida, mos `APP_SECRET_KEY` yoki saqlangan `data/secret.key` bilan.
- Boshlang'ich administrator lokal muhitda yaratiladi; uning bazasi/kaliti xavfsiz saqlanadi.
- Qayta ishga tushishda DB va kalit tiklanishi, 1Cga tarmoq kirishi, bepul tarif uyqu/limitlari tekshiriladi.

Xizmat akkaunti/domen tanlanmagan va ommaviy sayt joylashtirilmagan. Bu lokal dashboard tayyorligini
internetdan allaqachon ochilgan deb talqin qilmang. Telegram ogohlantirishlari kelishuvga ko'ra keyingi bosqichda.
