# EHF ↔ 1C nazorati

EHF sahifasida kirim/chiqim alohida yo'nalishlarda ko'rsatiladi. Tovar kirimi pul chiqimi bilan aralashtirilmaydi. Sana oralig'ini tanlab “E-DOCS’dan yuklash” orqali saytda mavjud qabul qilingan, yuborilgan, tasdiqlangan, rad etilgan, qoralama va o'chirilgan holatlar olinadi.

## Sverkaga kiradiganlar

- `signed` — tasdiqlangan.
- `sended` / kiruvchi `received` — imzo kutilmoqda.
- `deleted` — oddiy sverka emas, 1C da faol hujjat mavjudligi nazorati.
- Qoralama va rad etilganlar yuklanadi, ammo odatdagi 1C sverkadan chiqariladi.

## 1C manbalari va moslik

Kirim: `Document_ПоступлениеТоваровУслуг`, `Document_СчетФактураПолученный`.
Chiqim: `Document_РеализацияТоваровУслуг`, `Document_СчетФактураВыданный`.
Metadata orqali nashr qilingan tegishli obyektlar o'qiladi. So'rovlar tashkilot GUIDiga chegaralangan va ketma-ket. Ro'yxatlar to'liq sahifalanadi (har obyekt uchun 20000 gacha); yil chegaralari va oldingi/keyingi 45 kun olinib, EHF sanasi bilan kiritish sanasi o'rtasidagi tafovut ham tekshiriladi. Xatolar va to'liq bo'lmagan rekvizitlar “tekshirilmagan” sifatida qaytadi; ularni “1C da yo'q” deb talqin qilmang.

Raqamdagi bosh nollar, bo'shliq va № prefiksi normallanadi. Tashqi raqam, 1C raqami, sana, summa va kontragent STIRi tekshiriladi. Mavjud bo'lsa, EDO/roaming identifikatori rekvizitlari o'zgargan hujjatni ham topadi. Raqam o'zgargan, lekin sana/summa/STIR mos hujjat to'liq mos deb yashirilmaydi — farq sifatida ko'rsatiladi. Bir turdagi bir nechta mos hujjat ham ogohlantirishdir.

1C dagi tovar/xizmat qatorlari nomenklatura nomi/MXIK bo'yicha moslanadi. Miqdor, MXIK, qatorning QQS bilan summasi va QQS summasi solishtiriladi. Tasdiqlangan farqlar qizil, yetishmagan/noaniq tafsilotlar sariq ko'rsatiladi. Rekvizitlar mos, tovarlar hali moslashtirilmagan bo'lsa `partial` — “Rekvizitlar mos; tovarlar to'liq tekshirilmadi”. Moslashtirilmagan qator avtomatik “ortiqcha tovar” deb hisoblanmaydi. Tasdiqlangan EHFning 1C qoralamasi va o'chirishga belgilangan 1C hujjati nazoratga tushadi. Bank vipiskasi dublikat algoritmi o'zgartirilmagan.

Teskari tekshiruv tanlangan EHF davrida 1C da bor, ammo yuklangan EHF ro'yxatida topilmagan hujjatlarni chiqaradi. Bu “EHF tizimida umuman yo'q” degani emas: qamrov va holat cheklovlari borligini hisobga oling. Holati bo'yicha sverkaga kirmaydigan, ammo yuklangan EHF raqami ma'lum bo'lganlar teskari nazoratda noto'g'ri yo'q deb belgilanmaydi.

## Filtrlar va natijalar

Sana, kirim/chiqim, EHF holati, sverka natijasi, kontragent/STIR/raqam qidiruvi mavjud. EHF holati va 1C natijasi alohida ko'rsatiladi. Natijalar: mos, topilmadi, rekvizit farqi, o'chirilgan EHF 1C da bor, faqat 1C ro'yxatida, tekshirilmagan va holati bo'yicha tekshirilmaydi. “Tozalash” barcha jadval filtrlarini tiklaydi. Yuklash davri va oxirgi vaqt sahifada ko'rinadi.

## Faktura ichida Gemini bilan savol-javob

“Tekshirish” oynasidagi “Shu fakturani AI bilan tushunish” bo'limida tayyor savolni bosing yoki o'z savolingizni yozing. Shu fakturaning rekvizitlari, mos 1C hujjatlari, EHF/1C tovar qatorlari, farqlar va qamrov ogohlantirishlari Gemini'ga yuboriladi. Umumiy chat tarixi, boshqa fakturalar va ulanish credentiallari yuborilmaydi. “EHF va 1C tovar qatorlarini ko'rish” ostida dastlabki qiymatlar ham ko'rinadi.

Egasi Sozlamalar → AI yordamchi bo'limida Gemini kaliti/modelini saqlaydi. Maxsus faktura izohi Gemini kalitini ishlatadi; umumiy chatda boshqa provayder tanlangan bo'lsa ham boshqa AIga yashirin o'tmaydi. Kalit yo'q yoki Gemini xatosida “Mahalliy izoh” deb aniq belgilangan diagnostika chiqadi; bu erkin savolga Gemini javobi emas. Bulutda Gemini kaliti mavjud emasligi lokal kompyuterda ham yo'q degani emas.

Suhbat har faktura va foydalanuvchi/firma sessiyasi uchun alohida, restartda tiklanadi. Oxirgi 20 faktura suhbati, har birida 20 xabargacha saqlanadi. Yangi yuklash yoki solishtirish o'zgarsa oldingi suhbat qayta ishlatilmaydi. Javob kutish paytida ma'lumot yangilansa 409 qaytadi: oynani qayta oching. Gemini yangi 1C/E-DOCS so'rovini bajarmaydi; izoh oxirgi yuklangan natijaga tayanadi, to'liq qayta nazorat uchun E-DOCS'dan qayta yuklang.

AI kontekstida EHFdan 100 va mos 1C hujjatlaridan jami 100 tagacha tovar qatori, ko'pi bilan 10 mos hujjat uzatiladi; jami/berilgan sonlari orqali qisqartirish ko'rsatiladi. AI moslikni o'zgartirmaydi va hujjat yozish/imzolash vositalariga ega emas.

## E-DOCS o'chirilgan kirim bo'yicha cheklov

Saytning o'z mijozida kiruvchi o'chirilganlar ro'yxati mavjud emas; `io=in,status=deleted` haqiqiy API'da `422 ERROR_WITH_FILTER` qaytardi. Chiquvchi o'chirilganlar ro'yxati ishlaydi. Dastur bu cheklovni ogohlantirishda ko'rsatadi.

Avval yuklangan EHF IDlari sessiya tarixida saqlanadi; davr almashtirilishi va qayta ishga tushirish bu tarixni o'chirmaydi. Ular ro'yxatdan yo'qolsa, qo'llanayotgan sana oralig'i ichida alohida ID bo'yicha qayta o'qiladi: statusi o'chirilgan bo'lsa 1C nazorati bajariladi. Tafsilot 404 bo'lsa ogohlantirish chiqadi va teskari 1C nazorati ko'rsatiladi. Kuzatuv boshlanishidan oldin o'chirilgan, IDsi noma'lum kirimlarning to'liq qamrovi tasdiqlanmagan; buning uchun E-DOCS'dan qo'llab-quvvatlanadigan ro'yxat/eksport yo'li kerak. Tarix joriy foydalanuvchi/firma sessiyasi uchun; umumiy fon monitoringi hali mavjud emas.

## Ishga tushirish va tekshirish

Lokal dasturda Sozlamalar → Joriy firma (STIR), Ulanishlar (E-DOCS va jonli 1C) sozlanadi. Bu bulutda faqat E-DOCS maxfiy bindinglari bor; jonli 1C sozlanmagan. E-DOCS yuklash jonli tekshirildi, 1C sverka esa sun'iy testlar bilan tasdiqlandi. Lokal 1C releasining metadata maydonlari bilan jonli tekshiruv hali talab qilinadi.

Barcha amallar faqat o'qish; 1C/EDO hujjatlari yaratilmaydi, o'zgartirilmaydi, imzolanmaydi va jo'natilmaydi.
