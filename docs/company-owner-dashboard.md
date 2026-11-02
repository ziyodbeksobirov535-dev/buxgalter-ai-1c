# Firma egasi dashboardi — kelishilgan texnik topshiriq

Sana: 2026-11-02, Asia/Tashkent. Holat: 22 ta savol-javob asosida talablar yig'ildi.
Bu hujjat keyingi ishlab chiqish uchun: yangi rol, dashboard va internet sinovi hali amalga oshirilmagan.

## Maqsad

Firma egasi hisobga kiritilgan ma'lumotlar va biznes natijalarini mustaqil ko'radi.
Buxgalter hozirgi to'liq ish panelidan foydalanadi. Administrator tizimning umumiy boshqaruvini saqlaydi.

## Rollar va firmalarga kirish

| Imkoniyat | Tizim administratori | Buxgalter | Firma egasi |
|---|---|---|---|
| Firmalar | Barcha firmalar | Faqat biriktirilgan firmalar | Faqat biriktirilgan firmalar |
| Natija, hujjat, kontragent, tovar va hisob-kitob tafsilotlari | Ko'radi | Ko'radi | Ko'radi |
| Mavjud buxgalter ish amallari va firma sozlamalari | Boshqaradi | Biriktirilgan firmalarda boshqaradi | Ko'rish paneli; kiritish/tahrirlash/sozlash huquqi berilmaydi |
| Umumiy AI kaliti, provayder va tizim sozlamalari | Boshqaradi | Boshqarmaydi | Boshqarmaydi |
| Akkaunt yaratish, rol va firma biriktirish | Boshqaradi | Boshqarmaydi | Boshqarmaydi |
| EHF–1C texnik tafovutlari | Ko'radi | Ko'radi | Egasi dashboardiga chiqarilmaydi |
| Egasi hisoboti | Mavjud ruxsatlariga ko'ra | Mavjud ruxsatlariga ko'ra | Tanlangan firma/davr uchun PDF |

Misol: A egasiga shartli 1 va 2-firma, B egasiga 3-firma biriktirilsa, A faqat 1 va 2 ni,
B faqat 3 ni ko'radi. Boshqa firmaning nomi va ma'lumoti ro'yxat, qidiruv, hisobot yoki AI javobida ochilmaydi.
URL, so'rov parametri yoki `X-Firm-Id`ni almashtirish bu chegarani o'zgartirmaydi.

Bitta egada bir nechta firma bo'lishi mumkin. U firmalarini almashtirib ko'radi;
firmalararo umumiy jamlanma kelishilmagan, har birining natijasi alohida chiqadi.

Texnik e'tibor: hozirgi `owner` roli barcha firmalarga kira oladigan administrator.
Uni cheklangan firma egasiga berish mumkin emas. Alohida rol, masalan `business_owner`, va zarur DB migratsiyasi
rejalashtiriladi; mavjud administrator akkaunti va `viewer` roli ishlashi saqlanadi.
Rol nomi taklif, foydalanuvchiga ko'rinadigan nomlar: “Administrator”, “Buxgalter”, “Firma egasi”.

## Firma egasining bosh sahifasi

Umumiy biznes holati birinchi o'rinda:

- Bank va kassadagi pul qoldig'i.
- Savdo va daromad.
- Tannarx, xarajat, foyda/zarar.
- Olinadigan va to'lanadigan qarzlar.
- Ombor qoldig'i va tovar tafsilotlari.
- Qarz bo'yicha ogohlantirishlar.

Egasi umumiy raqamdan kontragent va hujjatgacha tafsilotni ochishi mumkin.
1C rekvizitlarini kiritish, import, qoralama yaratish va sozlamalarni boshqarish uning ish oqimiga kirmaydi.

Boshlang'ich davr — joriy oy boshidan bugungacha. Sana filtri bilan boshqa davr tanlanadi.
Davr va “bugun” Asia/Tashkent bo'yicha aniqlanadi. Pul/ombor/qarz qoldig'i bilan davrdagi aylanma
bir-biridan aniq ajratiladi; har ko'rsatkichning qaysi sana/davrga tegishliligi ko'rinadi.

Dashboard ochilganda va ochiq turganida har 5 daqiqada yangilanadi. Qo'lda “Yangilash” ham mavjud.
Oxirgi muvaffaqiyatli yangilanish vaqti ko'rinadi. Bu tanlov yopiq brauzerda uzluksiz fon monitoringini anglatmaydi.
Firma/davr almashtirilganda kechikkan eski javob yangi panelga qo'llanmaydi.

Foyda bankdagi pul qoldig'idan alohida, 1C hisobidagi daromad, tannarx va xarajatlarga asoslanadi.
Jonli konfiguratsiyaning schyotlari, registrlari va tashkilot chegarasi tekshiriladi.
Yetishmagan yoki olinmagan ma'lumot nol yoki taxminiy foyda deb ko'rsatilmaydi; to'liq emasligi ochiq belgilanadi.

## Qarz nazorati

Olinadigan va to'lanadigan qarzlarning ikkalasi ham nazoratda.
Foydalanuvchining 1C hisobida to'lov muddati kiritilmaydi, hujjat sanasi va qarz summasi mavjud.
Shuning uchun dastlab “qarz yoshi” qo'llanadi; shartnomaviy to'lov muddati o'ylab topilmaydi.

Boshlang'ich qoida: qarz yoshi 30 kunga yetgan yoki bitta kontragent bo'yicha jami ochiq qarz
100 000 000 so'mdan oshgan bo'lsa ogohlantirish chiqadi. Shartlar **YOKI** orqali ishlaydi.
Ikki mezonga ham tushgan yozuvda ikkala sabab ko'rsatiladi. Masalan: “35 kundan beri yopilmagan qarz”.

Summa nazorati har bir firma va qarz tomoni bo'yicha alohida. Olinadigan/to'lanadigan qarzlar va avanslar
o'zaro avtomatik yopib yuborilmaydi. Buxgalter o'z firmasining boshlang'ich mezonlarini keyin o'zgartira oladi.

Qarz yoshi hujjat kesimidagi **qolgan ochiq qoldiq** asosida hisoblanadi: qisman/to'liq to'lov va
qaytarishlar hisobga olinadi. Faqat kontragentning jami qoldig'i olinib, hujjatga bog'lanish tasdiqlanmasa,
yosh “aniqlanmadi” bo'ladi; eski faktura borligi o'zi eski ochiq qarzning isboti deb olinmaydi.

Birinchi bosqichda ogohlantirishlar dashboardda. Telegram orqali qarz ogohlantirishlari keyingi bosqichga
qoldirildi. Kunlik Telegram jamlanmasi tanlanmagan. EHF–1C farqlarini egasiga xabar qilish talab qilinmagan.

## Gemini

Firma egasi erkin savol-javob qiladi: masalan, “Kimdan qancha qarzimiz bor?”, “Qaysi xarajat oshgan?”.
Gemini faqat egasiga ruxsat etilgan **tanlangan firma** ma'lumotlarini o'qiydi va tushuntiradi.
Firma egasi AI orqali yozish, import yoki sozlamani o'zgartirish huquqiga ega bo'lmaydi.

Firma chegarasi server va AI vositalarida ham qo'llanadi; savolda boshqa firma nomini yozish yoki
vosita argumentini almashtirish ruxsatni kengaytirmaydi. Suhbat, kesh va javoblar foydalanuvchi/firma bo'yicha
ajratiladi. AI javobi manba/davr va ma'lumotning to'liq emasligini hisobga oladi.
Umumiy Gemini kaliti va provayderini faqat administrator boshqaradi.

## PDF hisoboti

Firma egasi hisobotni faqat PDF ko'rinishida yuklab oladi; Excel eksporti uning paneliga kirmaydi.
PDF tanlangan firma va tanlangan davrga tegishli bo'ladi. Firma nomi, davr, ma'lumot yangilangan vaqt,
biznes natijalari va to'liq bo'lmagan ma'lumotlar haqidagi izohlar ko'rinadi.
PDF so'rovida ham firma/rol tekshiriladi; boshqa firmaning hisoboti olinmaydi.

## Internetda foydalanish va bosqichlar

Maqsad — egalar telefon/kompyuter brauzeridan login-parol bilan istalgan joydan kirishi.
Foydalanuvchida hozir server ham, domen ham yo'q. Tanlov: avval **bepul sinov**,
doimiy pullik xizmat va domen keyin tanlanadi. Pullik budjet miqdori belgilanmagan.

1. Mahalliy nusxada rol/firma chegaralari, egasi dashboardi, qarzlar, Gemini va PDFni tayyorlash va tekshirish.
2. Mos bepul sinov xizmatini tanlash va internet sinovini tayyorlash. Xizmat hali tanlanmagan va sayt
   joylashtirilmagan. HTTPS, DB/sessiya fayllari va shifrlash kalitining saqlanishi, 1C ga ulanish,
   qayta ishga tushishda tiklanish hamda bepul tarifning uyqu/limit shartlari tanlashda tekshiriladi.
   Bepul sinovga 24/7 doimiy ishlash va'dasi berilmagan.
3. Alohida keyingi bosqich: doimiy server/domen va Telegram qarz ogohlantirishlari.

Hozirgi `start.command` bilan lokal ishga tushirish sinov uchun saqlanadi.
Internetga ochish xizmat/sozlamalar aniq tayyorlangach alohida ko'rib chiqiladi.

## Ishlab chiqishda tekshiriladigan qabul mezonlari

- Administrator barcha firmalarni, buxgalter va firma egasi faqat biriktirilganlarini ko'radi.
- Ikki egasi va uch shartli firma misoli UI, to'g'ridan-to'g'ri API, AI, PDF va keshda bir xil ishlaydi.
- Egasi hisob tafsilotlarini o'qiydi; firma/tizim sozlamasi, huquq yoki hujjatni o'zgartira olmaydi.
- Buxgalter biriktirilgan firma sozlamalarini boshqaradi; umumiy AI, akkaunt/rol/firma biriktirish
  amallari administratorga tegishli. Mavjud administrator boshqaruvi saqlanadi.
- Foyda/zarar jonli 1C releasidagi tashkilot va hisob manbalariga solishtiriladi; xato/yetishmagan
  natija “nol” bo'lmaydi. Haqiqiy 1C so'rovlari ketma-ket va faqat o'qish orqali bajariladi.
- Joriy oy, boshqa davr, avtomatik 5 daqiqalik yangilash va firma almashgandagi kechikkan javob tekshiriladi.
- 30 kun, 100 million chegaralari, har bir mezon alohida va birga, qisman/to'liq to'lov,
  qaytarish, avans va hujjati noma'lum qarz holatlari sun'iy ma'lumotda tekshiriladi.
- Egasi ko'rinishida EHF–1C texnik tafovutlari yo'q; buxgalterning mavjud nazorati ishlaydi.
- Gemini va PDF aynan tanlangan firma/davrga bog'lanadi; telefon va kompyuter kengliklarida ishlaydi.

Ushbu reja haqiqiy 1C ga yozish qulfini ochmaydi va mavjud EDO yozish/imzolash chegaralarini kengaytirmaydi.
