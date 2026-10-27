# 🏦 BUXGALTER AI — 1C:Buxgalteriya (O'zbekiston 3.0) uchun AI tizim

Bank vipiskasi va Didox EHF larini **1C OData** bilan solishtiradigan, **dublikatlarni bloklaydigan**,
schyotlarni **buxgalterning oldingi 1C yozuvlaridan o'rganadigan** va yangi hujjatlarni faqat
**qoralama** (`Posted: false`) sifatida kirituvchi tizim.

- 🖥️ Ko'p sahifali Web App (chap menyu / telefonda pastki menyu), yorug'/to'q rejim, 100% o'zbek tilida:
  🏠 Bosh sahifa · 🏦 Bank vipiskalari · 📑 Didox · 📦 Sklad · 📚 Hisob qoidalari · ⚙️ Sozlamalar;
  🤖 AI Copilot — istalgan sahifadan ochiladigan suzuvchi panel
- 🔐 Login va parol bilan kirish, rollar: **Egasi / Buxgalter / Kuzatuvchi**
- 🏢 **Bir akkauntdan bir nechta firma** — har birining o'z 1C bazasi (yoki bitta bazadagi alohida tashkiloti)
- 📜 Audit jurnali: kim, qachon, qaysi firmada nima qilgani
- 📱 Telegram bot + Telegram Mini App (WebApp)
- 🤖 Bepul **Google Gemini** (`gemini-3.8-flash`) + Function Calling; kalitsiz ham Demo AI ishlaydi
- 🛡️ Dublikat himoyasi: Raqam + Sana + Summa + Kontragent_Key
- 🧠 Tarixdan o'rganish: `ВидОперации`, schyot (60.10 / 40.10 / 64.10 / 94.20), shartnoma, ДДС moddasi

---

## ⚡ Tezkor ishga tushirish (2 daqiqa)

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # ixtiyoriy — standart holatda Demo rejim
.venv/bin/python main.py      # → http://127.0.0.1:8000
```

Yoki shunchaki **`start.command`** ni ikki marta bosing (macOS).

Birinchi ochilishda **"Egasi" akkauntini yarating** (login + parol, kamida 8 belgi). Bu faqat dastur o'rnatilgan
kompyuterning o'zidan mumkin. Keyin brauzerda **🧪 Test Vipiska Namunasini Yuklash** tugmasini bosing. Namuna fayl
(`sample_bank_vipiska.xlsx`) server ishga tushganda avtomatik yaratiladi va **📥 Test Vipiska faylini yuklab olish**
tugmasi orqali ham olish mumkin.

**Bank eksport formatlari:** O'zbekiston banklarining "Выписка лицевых счетов" Excel eksporti sinovdan o'tgan —
"№ пп" (tartib raqami) e'tiborsiz qoldiriladi, hujjat raqami "№ док" dan olinadi; "Дата документа" va
"Дата обработки" alohida o'qiladi (1C hujjati bank o'tkazgan sana bilan, kiruvchi sana — hujjat sanasi);
soliq to'lovlari (G'aznachilik, DSI) 64.10, bank xizmat haqi ("За документ №…") 94.20 deb taniladi;
vipiska egasi (sarlavhadagi hisob nomi) bo'yicha to'g'ri firma taklif qilinadi.

**Shartnoma to'lov maqsadidan:** "дог№50 от 10.09.2026", "11.10.2026 yildagi 398 shartnomaga", "Shartnoma N_434",
"№440 шартномага", "17-sonli shartnoma" kabi yozuvlardan raqam va sana olinadi va 1C dan aynan shu kontragentning
o'sha shartnomasi qidiriladi. Raqam 1C da topilmasa — bu yangi shartnoma bo'yicha birinchi to'lov deb hisoblanadi
va shartnoma 1C ga kiritishda yaratiladi; raqam bo'lmasa — 1C tarixidagi shartnoma.

**Ko'rish rejimi:** real 1C javob bermasa (masalan, OData'da obyektlar hali nashr qilinmagan) vipiska baribir
ko'rsatiladi — schyotlar qoidalar/standart bo'yicha, shartnoma to'lov maqsadidan, "⚪ 1C da tekshirilmadi" belgisi
bilan; yozish imkonsiz. 1C ulangach "🔄 1C da qayta tekshirish".

### Namuna vipiska — sinov stsenariysi (14 qator, faqat 🧪 Demo firmada)

| # | Holat | Nima tekshiriladi |
|---|-------|-------------------|
| №112, №45, №118 | 🟢 1C da bor (Dublikat) | Anti-dublikat bloklovchi — checkbox o'chirilgan, serverda ham rad etiladi |
| №52 (yetkazib beruvchi) | 🟡 Yangi → **60.10** | 🧠 1C tarixidan `ОплатаПоставщику` |
| №131 (xaridor) | 🟡 Yangi → **40.10** | 🧠 1C tarixidan `ОплатаОтПокупателя` |
| №53 (QQS to'lovi) | 🟡 Yangi → **64.10** | 🧠 1C tarixidan `ПеречислениеНалога` |
| №54 (bank komissiyasi) | 🟡 Yangi → **94.20** | 🧠 1C tarixidan `ПрочееСписание` |
| №124 | 🔴 Farq bor | Summa tafovuti: faylda 19 500 000, 1C da 19 050 000 |
| №16 / №17 / №18 | 🟡 Yangi → **65.20 / 64.10 / 65.20** | Soliq kodlari: 36 (ijtimoiy soliq), 46 (JShDS), 101 (INPS) |
| №141 | 🟡 Yangi → **57.10** | Terminal (Humo/Uzcard) tushumi |
| №58 | 🟡 Yangi → **55.20** | Korporativ karta (KPK) |
| №57 | ⛔ To'siq | Ish haqi — 1C da vedomost bilan qo'lda |

9 ta yangi qatorni kiritgach, shu faylni **qayta yuklang** — endi ular ham dublikat sifatida bloklanadi.
Bu ikki marta kiritish imkonsizligini isbotlaydi.

---

## 🏠 Sahifalar

Manzil sahifani eslab qoladi (`#/home`, `#/bank`, `#/didox`, `#/stock`, `#/settings`, `#/settings/rules`) — yangilansa ham
joyida qoladi, Telegram'dan to'g'ridan-to'g'ri kerakli sahifani ochish mumkin.

**Hisob qoidalari** endi **Sozlamalar → Hisob qoidalari** ichida. Eski `#/rules` havolasi ham shu joyga yo'naltiriladi.
Sozlamalar **Tizim holati**, **Joriy firma**, **Ulanishlar**, **Hisob qoidalari**, **AI yordamchi**, **Firmalar**, **Foydalanuvchilar**, **Faoliyat jurnali** va **Akkaunt** bo'limlariga ajratilgan.
Firma rekvizitlari «Joriy firma»da, 1C/E-DOCS/Didox/Notion esa «Ulanishlar»da sozlanadi.
Buxgalter va kuzatuvchi hisob qoidalariga avvalgi huquqlari bilan kira oladi.

- **🏠 Bosh sahifa** — **51.10 qoldig'i** (1C provodkalaridan; 1C yo'q bo'lsa — vipiskaning yakuniy qoldig'i).
  1C dagi qoldiq bugungi emas — **1C ga oxirgi kiritilgan bank hujjati sanasidagi** holat: shu sana va
  "⏳ 1C N kun orqada" belgisi ko'rsatiladi, yonida bankdagi haqiqiy qoldiq (oxirgi vipiska bo'yicha);
  **⚖️ sverka**: vipiska yakuniy qoldig'i ↔ 1C dagi 51.10 (o'sha kun oxirida) — farq 1C ga hali kiritilmagan
  to'lovlar bilan tushuntirilsa 🟡, aks holda 🔴; shu oydagi kirim / chiqim / sof aylanma; oxirgi 8 haftalik grafik;
  **📋 bugungi ishlar** (tayyor to'lovlar, farqlar, to'siqlar, Didox, ziddiyatli qoidalar — bosilsa o'sha sahifa);
  **🤝 hisob-kitoblar**: 40.10 / 63.10 / 60.10 / 43.10 jami summalari va tanlangan schyot (shular yoki 48.90, 69.90,
  42.20, 68.10, 66.10) bo'yicha barcha kontragentlar ro'yxati — debet/kredit qoldiq, qidiruv, tanlov firma bo'yicha
  eslab qolinadi; bir nechta firma bo'lsa — barchasining xulosasi.
- **📦 Sklad va qoldiqlar** — 29.10, 10.10, 10.30, 10.80, 28.10 qoldig'i nomenklatura bo'yicha: MXIK (IKPU), birlik,
  miqdor, o'rtacha narx, summa, qidiruv. Manba — `AccountingRegister_Хозрасчетный/Balance` (faqat o'qish).
- **⚙️ Sozlamalar** — tizim holati (1C, AI, Didox, Telegram, eksport fayllari), joriy firma (1C OData manzili, login,
  Didox), firmalar, foydalanuvchilar, AI kaliti, jurnal, akkaunt.

Bosh sahifa va Sklad 1C dan **faqat o'qiydi**. Demo firmada namuna qoldiqlar bilan darhol ko'rinadi.

## 🧠 Gemini API kaliti (bepul, 30 soniya)

1. <https://aistudio.google.com/app/apikey> → **Create API key**
2. Web App → **⚙️ Sozlamalar** → kalitni qo'ying (yoki `.env` da `GEMINI_API_KEY=`)

Gemini quyidagi vositalarni (tools) chaqiradi: `analyze_and_deduplicate_vipiska()`, `check_didox_vs_1c()`,
`learn_from_1c_history(stir)`, `post_confirmed_to_1c(doc_ids)`, `cash_flow_summary()`.

> `post_confirmed_to_1c` hech narsani o'zi yozmaydi — chatda **tasdiqlash kartasini** chiqaradi. 1C ga yozuv
> faqat buxgalter tugmani bosgandan keyin bo'ladi. Gemini xato bersa yoki limit tugasa, Demo AI avtomatik ishlaydi.
> OpenAI / Claude ham zaxira sifatida sozlanadi.

---

## 🔐 Foydalanuvchilar va rollar

| Rol | Nima qila oladi |
|---|---|
| **Egasi** | Hamma narsa: barcha firmalar, firmalarning 1C/Didox sozlamalari, foydalanuvchilar, AI sozlamalari, jurnal |
| **Buxgalter** | Biriktirilgan firmalarda: vipiska yuklash, tekshirish, schyot tanlash, 1C ga qoralama yozish |
| **Kuzatuvchi** | Biriktirilgan firmalarda: faqat ko'rish va tahlil — 1C ga yoza olmaydi |

- Foydalanuvchi qo'shish: 👤 menyu → **👥 Foydalanuvchilar** (faqat Egasi). Har biriga qaysi firmalar ko'rinishini belgilang.
- Parollar faqat **scrypt xeshi** ko'rinishida saqlanadi. 5 ta xato urinishdan keyin login 15 daqiqaga to'xtatiladi.
- Sessiya 8 soat harakatsizlikdan keyin tugaydi; parol o'zgarganda boshqa barcha qurilmalardan chiqariladi.
- Oxirgi faol "Egasi"ni o'chirib yoki rolini tushirib bo'lmaydi.

## 🏢 Bir nechta firma

- 👤 menyu → **🗂 Firmalar** → yangi firma (nomi + STIR). Yuqori paneldagi tanlagich orqali firmalar orasida o'tiladi.
- Har firmaning **o'z 1C ulanishi** (manzil, login, parol — shifrlangan), Didox kalitlari va jadvali bor.
- Firmalar alohida 1C bazalarda bo'lishi ham, **bitta bazadagi bir nechta tashkilot** bo'lishi ham mumkin —
  ikkinchi holatda manzil bir xil, STIR har xil bo'ladi (STIR bo'yicha 1C dagi tashkilot tanlanadi).
- 🛡️ **Vipiska boshqa firmaga tushmasligi:** fayl sarlavhasidagi STIR va hisob raqami tanlangan firmaning 1C dagi
  tashkiloti va bank hisoblari bilan solishtiriladi. Mos kelmasa — barcha qatorlar bloklanadi va (ruxsatingiz bo'lsa)
  to'g'ri firmaga o'tish taklif qilinadi.
- Web App va Telegram bir foydalanuvchi + firma uchun bitta umumiy jadvalni ko'radi.
- 🔗 **Firma almashganda adashmaslik:** Telegram'dagi "✅ Kiritish" tugmalari o'zi ko'rsatgan firma va qatorlarga
  bog'langan va bir martalik; Web App'da firma almashgandan keyin kelgan eski javoblar ko'rsatilmaydi, server esa
  boshqa firmaning qatorlarini yozishni rad etadi. Firmaga ruxsat olib tashlansa — ruxsat etilgan firmaga o'tiladi.

## 📚 Hisob qoidalari — dastur bazani qanday yuritganingizni o'rganadi

Yuqoridagi **📚 Hisob qoidalari** bo'limi (har bir firma uchun alohida):

- **🧠 1C tarixidan o'rganish** — oxirgi 3/6/12/24 oydagi barcha o'tkazilgan bank va xarid hujjatlarini o'qib,
  jadval tuzadi: qaysi kontragent qaysi schyotga, qaysi ВидОперации, shartnoma va ДДС moddasi bilan olingan,
  necha marta (ishonch: masalan 18/18). 1C ga hech narsa yozilmaydi.
- **⚠️ Ziddiyatli** — bir kontragent turli schyotlarga olingan bo'lsa (masalan 60.10×3, 69.90×1) — bir marta ko'rib
  chiqib, to'g'ri schyotni tanlab 🔒 belgilang.
- **🔤 Kalit so'z qoidalari** — to'lov maqsadidagi so'z bo'yicha ("komissiya" → 94.20, "ijara" → 94.20 …);
  tarixdan topiladi yoki qo'lda qo'shiladi.
- **🔒 Qat'iy qoida** — 1C tarixidan ustun turadi. Qayta o'rganishda qat'iy qoidaning schyoti o'zgartirilmaydi.
- **💾 Eslab qolish** — vipiskada schyotni o'zgartirsangiz, "Shu kontragent uchun eslab qol" tugmasi chiqadi.
- **📥/📤 Excel** — qoidalarni Excel'ga yuklab olib tekshirish/to'ldirish va qayta yuklash.
- Har bir vipiska qatorida **"nega bu schyot?"** ko'rinadi: 🔒 Qat'iy qoida / 🧠 1C tarixi (3/3) / 🔤 Kalit so'z / 🤖 Standart.

**Ustuvorlik:** 🔒 qat'iy qoida → 🧠 kontragentning 1C tarixi → 🔤 kalit so'z → 🤖 standart taxmin.

**Qarz yoki avans (40.10 / 63.10, 60.10 / 43.10).** Har bir qoida ikki schyotni saqlaydi: qarz bo'yicha
hisob-kitob schyoti va qarz bo'lmasa ishlatiladigan avans schyoti. Masalan, xaridordan pul tushsa: faktura oldin
berilgan bo'lsa 40.10 dagi qarz yopiladi, oldindan to'lov bo'lsa 63.10 (olingan avans). Qoralamada
"Способ погашения задолженности: Автоматически" turadi — taqsimotni 1C hujjatni o'tkazganda kontragentning shu
paytdagi qarziga qarab o'zi qiladi. Ikkala schyot ham kontragentning 1C tarixidan o'rganiladi.

**Real 1C uchun:** qoralama yuborishdan oldin rekvizitlar 1C ning `$metadata` iga solishtiriladi — konfiguratsiyada
yo'q maydonlar yuborilmaydi (👁 Tekshirish oynasida ro'yxati ko'rinadi). Hozircha real firmalar uchun **faqat tahlil
rejimi** yoqilgan — 1C ga hech narsa yozilmaydi.
Qoidalarni Egasi va Buxgalter tahrirlaydi, Kuzatuvchi faqat ko'radi; har o'zgarish jurnalga yoziladi.

## 🧾 Hisoblar rejasi (BHMS 21) va aniq belgilar

`accounts_uz.py` — 33 ta asosiy schyot 6 guruhda (pul mablag'lari, hisob-kitoblar, soliqlar, ish haqi/kreditlar,
TMZ, daromad/xarajatlar); tanlash ro'yxatlari guruhlangan va o'zbekcha nomlar bilan.

To'lov maqsadidagi **aniq belgilar 1C tarixidan ustun** (G'aznachilik bitta kontragent, lekin har soliq o'z schyotiga):

| Belgi | Schyot |
|---|---|
| Soliq kodi 46 (JShDS), QQS soliq organiga | 64.10 |
| Soliq kodi 36 (ijtimoiy soliq), 101 (jamg'arib boriladigan pensiya / INPS) | 65.20 |
| ish haqi / зарплата | 67.10 (vedomost bilan qo'lda) |
| dividend | 66.10 |
| kredit foizi / проценты по кредиту | 96.10 |
| kredit asosiy qarzi, moliyaviy yordam, заём | 68.10 |
| korporativ karta (KPK) | 55.20 |
| terminal, Humo/Uzcard, эквайринг, inkassatsiya (kirim) | 57.10 |

"QQS 12% bilan" yozilgan oddiy to'lovlar soliq deb olinmaydi. Kiruvchi EHF: **Dt 29.10 (tovar) / 94.20 (xizmat) +
Dt 44.10 (QQS 12%) — Kt 60.10**.

**Yangi shartnoma:** to'lov maqsadidagi shartnoma 1C da topilmasa — "➕ Yangi shartnoma №… — kiritishda yaratiladi";
1C ga yozishdan oldin yana qidiriladi, bo'lmasa kontragentga raqami va sanasi bilan yaratiladi va to'lov unga bog'lanadi.

## 🗄️ Real 1C ga ulanish (OData)

1. 1C Konfigurator: **Администрирование → Публикация на веб-сервере** (OData yoqilgan holda).
2. 1C:Предприятие: **Все функции → Обработки → Настройка автоматического REST-сервиса**
   (`Настройка стандартного интерфейса OData`) — quyidagilarni yoqing:
   `Catalog_Контрагенты`, `Catalog_ДоговорыКонтрагентов`, `Catalog_БанковскиеСчета`, `Catalog_Номенклатура`,
   `Catalog_Организации`, `ChartOfAccounts_Хозрасчетный` (ixtiyoriy: `Catalog_СтатьиДвиженияДенежныхСредств`, `AccountingRegister_Хозрасчетный`),
   `Document_ПоступлениеНаРасчетныйСчет`, `Document_СписаниеСРасчетногоСчета`, `Document_ПоступлениеТоваровУслуг`.
3. ⚙️ Sozlamalar → **🏢 Joriy firma** → "Demo 1C bazasi" belgisini olib tashlang → URL
   (masalan `https://1c.uzcloud.uz/a/bpuz/1234`), 1C bazasidagi login, parol → **🔌 Saqlash va 1C ni tekshirish**. Tizim `$metadata` ni o'qib, nashr qilinmagan obyektlarni ko'rsatadi.

**Bulutli 1C (uzcloud, 1C:Fresh):** OData obyektlari bazaning o'zida yoqiladi — qidiruvga "REST" yozing →
**"Настройка автоматического REST-сервиса"** → «Состав» → «Загрузить метаданные» → kerakli obyektlar
(1C bog'liqlarini o'zi qo'shadi — "Зависимости объекта" so'ralsa **«Нет»**) → «Авторизация» da alohida `odata.user`
→ «Сохранить и закрыть». Ixtiyoriy, lekin tavsiya: **Регистры бухгалтерии → Хозрасчетный** ham — schyotlar 1C
provodkalaridan aniq o'rganiladi. Dastur bu bazalarga moslangan:
- `$filter` da bo'sh joy `%20` bilan yuboriladi (uzcloud kirillcha filtrda `+` ni tushunmaydi);
- bank hujjatlarida kontragent kompozit (`Контрагент` + `Контрагент_Type`) — avtomatik aniqlanadi;
- "Расшифровка платежа" qatori bor hujjatda schyot qatordan (sarlavhadagi standart 42.20 emas), qatorsiz hujjatda
  (komissiya, soliq) — 1C provodkasidan; uch bo'g'inli schyotlar (`45.30.1`) qo'llab-quvvatlanadi;
- YaTT uchun 14 xonali JShShIR (`ПИНФЛ`) bo'yicha ham qidiriladi; sahifalab o'qishda `$orderby` majburiy;
- G'aznachilik to'lovida schyot **shu firmada shu soliq kodi bilan oldin qaysi schyotga olingan** bo'lsa — o'sha
  (masalan, avans soliq schyotlari 44.10.x / 45.10.x); tarix bo'lmasa — standart kod→schyot jadvali.

**Muhim:** konfiguratsiya relizlariga qarab rekvizit nomlari farq qilishi mumkin (masalan, `Контрагент_Key` o'rniga
kompozit `Контрагент` + `Контрагент_Type`). Ular `services_1c.py` dagi `FIELDS` lug'atida bir joyda jamlangan.
Birinchi real ulanishda `$metadata` ni tekshirib, kerak bo'lsa shu lug'atni moslang. Avval test bazada sinang.

## 📁 1C bilan fayl orqali (OData ishlamasa — masalan, bulutli 1C:Fresh)

OData nashr qilinmagan bo'lsa ham dastur to'liq tekshiruv qiladi: 1C dan Excel eksportlarini o'qiydi, yangi
qatorlarni esa 1C ning o'z "Загрузить" oynasi orqali kiritiladigan fayl qilib beradi. **1C ga dastur o'zi hech narsa
yozmaydi.**

**1. O'qish (📚 Hisob qoidalari → 📁 1C eksport fayllari, yoki vipiska ustidagi banner):** 1C da har birini
**Еще → Вывести список** (hisobotda **Сохранить как → Лист Excel 2007 (.xlsx)**) qilib yuklang — bir nechtasini birga:

| 1C dagi joy | Nima uchun |
|---|---|
| Справочники → **Контрагенты** (Наименование, ИНН) | kontragentni STIR bo'yicha topish |
| Справочники → **Договоры** (Наименование, Контрагент, Номер, Дата, Вид договора) | to'lov maqsadidagi shartnoma 1C da bormi |
| Отчеты → **Карточка счета 51.10** (12 oy) | qaysi to'lov qaysi schyotga olingan (qarz 40.10 + avans 63.10 bo'linishi ham) |
| *(ixtiyoriy)* Банк и касса → **Банковские выписки** ro'yxati | «Вх. номер» bo'lsa — dublikat raqam bo'yicha aniq |
| *(ixtiyoriy)* **ОСВ по счету 40.10 / 60.10** (kontragentlar bo'yicha) | to'lovning qancha qismi qarzni yopadi, qanchasi avans |

Fayl turi sarlavhalardan avtomatik aniqlanadi; qayta yuklash dublikat yaratmaydi. Yuklangach dastur qoidalar
jadvalini (📚) o'rganadi va vipiskani qayta tekshiradi: dublikat (raqam/sana/summa/kontragent), shartnoma, schyot.
1C manzili kiritilgan, lekin OData javob bermasa — avtomatik shu fayllar bo'yicha ishlaydi.

**📋 Tekshiruv ro'yxati (Excel)** — jadval ustidagi tugma: har qator qaysi schyot, avans schyoti va shartnomaga
tushishi kerakligi, holati va qarz/avans izohi. Import faylini 1C ga yuklagach solishtirish uchun.

Telegram botda ham fayl rejimida «📤 1C import fayli» tugmasi chiqadi — fayl chatga yuboriladi.

**2. Yozish (📤 1C import fayli):** jadval pastidagi tugma faqat yangi (1C da yo'q, to'siqsiz) qatorlardan
`kl_to_1c_….txt` (1CClientBankExchange 1.03, Windows-1251) tayyorlaydi. 1C da: **Банк и касса → Банковские выписки →
Загрузить** (Обмен с банком) → faylni tanlang → ro'yxatni ko'rib **Загрузить**. Bu formatda schyot maydoni yo'q —
1C schyotni shartnoma/kontragent sozlamasidan qo'yadi; yuklagach jadvaldagi schyot bilan solishtiring.
Chiqarilgan qatorlar "📤 Import fayliga chiqarildi" deb belgilanadi va keyingi faylga qayta tushmaydi.

## ✍️ 1C ga yozish — faqat ruxsat bilan, bittadan

Haqiqiy 1C li firma standart holatda **faqat tahlil**: hech narsa yozilmaydi. Yozishni sinash tartibi:

1. uzcloud "Менеджер сервиса" → firma → **«Резервное копирование»** (zaxira nusxa).
2. ⚙️ Sozlamalar → Joriy firma → **✍️ 1C ga yozish** (faqat "Egasi"): firma nomini qo'lda yozib tasdiqlang,
   limit — **1 ta hujjat**; "yangi shartnoma yaratish" o'chiq qoldiring.
3. Bank sahifasida vipiskadagi **haqiqatan kiritilishi kerak bo'lgan bitta** to'lovni belgilang → qoralama.
   Haqiqiy 1C da qatorlar avtomatik tanlanmaydi; limitdan ko'p tanlansa tugma ishlamaydi.
4. 1C da qoralamani tekshiring (kontragent, shartnoma, schyot, avans). To'g'ri bo'lsa — 1C ning o'zida o'tkazing;
   noto'g'ri bo'lsa — **📝 Dastur yaratgan qoralamalar → ↩️ Bekor qilish**: dastur faqat o'zi yaratgan, izohi
   "BUXGALTER AI" bilan boshlanadigan va **o'tkazilmagan** hujjatni "o'chirishga belgilaydi" (DeletionMark).
5. Ruxsatni yoqish/yopish, har bir yozuv va bekor qilish audit jurnaliga yoziladi. Telegram bot ham shu limitga bo'ysunadi.

**➕ Yangi kontragent:** vipiskadagi kontragent 1C da yo'q bo'lsa (⛔ to'siq), qatorda **"➕ Kontragent yaratish"**:
nomi, STIR/JShShIR, yuridik/jismoniy, 1C dagi guruh (kirim — покупатели, chiqim — поставщики), bank hisobi (bank
1C katalogidan MFO bo'yicha) va shartnoma (to'lov maqsadidagi raqam yoki "Основной договор"; valyuta, tashkilot,
rahbar oxirgi shartnomadan) oldindan to'ldiriladi. Shu STIR bilan kontragent 1C da bo'lsa — **o'chirishga
belgilangan bo'lsa ham** — yangisi yaratilmaydi. Faqat yozish ruxsati bilan; yaratilganlar "📝 Dastur yaratganlar"
ro'yxatida, o'tkazilgan hujjatlarda ishlatilmaguncha "↩️ Bekor qilish" (bank hisobi va shartnoma bilan birga).

**Qoralama 1C dagi haqiqiy hujjat namunasida:** provodkani 1C o'zi tuzadi ("Провести"), shuning uchun qoralama shu
operatsiyaning 1C dagi oxirgi o'tkazilgan hujjatidek to'ldiriladi — qatorli yoki qatorsiz (komissiya, soliq), shartnoma,
holat maydonlari, kurs/karralik. Soliq to'lovida schyotni **soliq turi** (`Налог_Key`) belgilaydi: u, kontragent (1C da —
tuman soliq inspeksiyasi), soliq davri va tahliliy maydonlar shu soliq kodli oxirgi hujjatdan olinadi; tarixda bir kod
uchun turli soliq turi bo'lsa — ⚠️ ogohlantirish.

Yozishdan oldin `ВидОперации` shu bazada haqiqatan ishlatilgan nomga keltiriladi (O'zbekiston BP:
`ОплатаПокупателя`, `КомиссияБанка`, `ВозвратПокупателю` …), so'm hisoblari uchun kurs/karralik 1 qo'yiladi.

### Xavfsizlik tamoyillari
- Bog'langan obyektlar hech qachon matn bilan yuborilmaydi — har doim `Ref_Key` (`$filter=ИНН eq '...'`).
- Har bir yozuvdan **oldin** 1C qayta so'raladi (jadval ochilgandan keyin boshqa buxgalter kiritgan bo'lsa ham bloklanadi).
- Hujjatlar faqat `"Posted": false` (qoralama). O'tkazishni 1C da inson qiladi.
- Kontragent STIR bo'yicha topilmasa, qator kiritilmaydi.
- Raqamlar yil boshida qayta boshlangani uchun "farq" faqat ±31 kun ichidagi hujjatlar bilan solishtiriladi.
- 1C uchun alohida, minimal huquqli foydalanuvchi yarating.

---

## 📱 Telegram bot + Mini App

1. [@BotFather](https://t.me/BotFather) → `/newbot` → tokenni `.env` ga: `TELEGRAM_BOT_TOKEN=...`
2. Mini App HTTPS talab qiladi. Masalan: `cloudflared tunnel --url http://127.0.0.1:8000` →
   chiqqan `https://...trycloudflare.com` manzilini `WEBAPP_URL=` ga yozing.
3. Serverni qayta ishga tushiring (`start.command`) — bot server bilan birga ishlaydi.
4. **Akkauntni bog'lash:** Web App → 👤 → **🔑 Parol va Telegram** → **Kod olish** → botga `/link 123456` yuboring.
   Bog'lanmagan Telegram foydalanuvchilari hech narsa ko'ra olmaydi.
5. Botda `/firma` — firmani tanlash. Vipiska faylini chatga yuborsangiz, joriy firma bo'yicha tekshiriladi.

## 📂 Tuzilma

```
main.py              FastAPI: API + Web App, firmalar/foydalanuvchilar API, bot'ni ishga tushirish
auth.py              Kirish: login cookie, Telegram imzosi, rollar, firmaga kirish huquqi
db.py                SQLite (data/app.db): foydalanuvchilar, firmalar (shifrlangan sirlar), sessiyalar, jurnal
bot.py               aiogram 3 Telegram bot
core.py              Umumiy ish oqimlari (Web, bot va AI shu yerdan foydalanadi)
services_1c.py       1C OData: Ref_Key, dublikat, tarixdan o'rganish, qoralama; Http + Demo transport
services_vipiska.py  Excel / CSV / 1CClientBankExchange parser, tahlil, payload
services_didox.py    Didox EHF ↔ ПоступлениеТоваровУслуг
services_rules.py    Hisob qoidalari: 1C tarixidan o'rganish, ustuvorlik, Excel
services_dashboard.py Bosh sahifa va Sklad: 1C qoldiqlari, sverka, aylanma, qarzlar, bugungi ishlar
services_files1c.py  1C bilan fayl orqali: Excel eksportlari → 1C nusxasi, OSV qoldiqlari, 1CClientBankExchange
services_ai.py       Gemini Function Calling + Demo AI + OpenAI/Claude zaxira
demo_data.py         Demo 1C bazasi (OData shaklida) va namuna vipiska generatori
config.py            .env + umumiy AI sozlamalari (data/settings.json, git'ga kirmaydi)
templates/index.html Asosiy ekran: Tailwind + Alpine.js + Lucide + telegram-web-app.js
templates/login.html Kirish va birinchi "Egasi" akkauntini yaratish
```

### API (asosiylari)
Firma ichidagi so'rovlar `X-Firm-Id` sarlavhasi bilan yuboriladi; hammasi login talab qiladi.

| Metod | Yo'l | Vazifa |
|---|---|---|
| POST | `/api/auth/login`, `/api/auth/logout`, `/api/auth/setup` | Kirish / chiqish / birinchi Egasi |
| GET | `/api/auth/me` | Joriy foydalanuvchi va uning firmalari |
| POST | `/api/vipiska/upload`, `/api/vipiska/sample`, `/api/vipiska/recheck` | Vipiska tahlili |
| DELETE | `/api/vipiska` | Jadvalni tozalash (xato/eski vipiska; 1C ga tegilmaydi) |
| POST | `/api/post` | Qoralama yozish (Buxgalter/Egasi) |
| GET/POST/PUT/DELETE | `/api/firms…` | Firmalar (Egasi) |
| GET/POST/PUT | `/api/users…` | Foydalanuvchilar (Egasi) |
| GET | `/api/audit` | Jurnal (Egasi) |
| GET/POST/PUT/DELETE | `/api/rules…` | Hisob qoidalari; `/api/rules/learn` — 1C tarixidan o'rganish |
| GET/POST/DELETE | `/api/1c-files…` | 1C Excel eksportlari (o'qish, o'rganish, o'chirish) |
| POST | `/api/vipiska/export-1c` | 1C ga import fayli (1CClientBankExchange) |
| GET | `/api/vipiska/checklist` | 📋 Tekshiruv ro'yxati (Excel): schyot, avans, shartnoma |
| GET | `/api/dashboard` | Bosh sahifa: qoldiq, sverka, aylanma, qarzlar, bugungi ishlar, firmalar |
| GET | `/api/balances?account=63.10` | Tanlangan schyot qoldig'i kontragentlar bo'yicha |
| GET | `/api/stock` | Ombor qoldiqlari nomenklatura bo'yicha |
| POST | `/api/chat` | AI Copilot |

## 🧪 Avtomatik testlar

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

203 ta test: parser (summa/yo'nalish formatlari, o'tkazib yuborilgan qatorlar), dublikat va majburan yozish qoidalari,
test vipiskaning jonli 1C ga tushmasligi, parallel yozishda ikki marta kiritilmaslik, uzcloud 401/404 javoblari,
bloklangan login'ga qayta urinmaslik, Host/parol himoyasi, Telegram imzosi, Gemini model zaxirasi, login/blok, rollar, firmalar izolyatsiyasi, boshqa firmaning vipiskasi, hisob qoidalari (o'rganish, ustuvorlik, Excel), 1C Excel eksportlari (turini aniqlash, kartochka, OSV, qarz/avans), 1C import fayli, Bosh sahifa (qoldiq, sverka, aylanma, qarzlar) va Sklad, sahifadagi JS satrlari.
Testlar internetga chiqmaydi va `data/` papkasiga tegmaydi.

**Zaxira nusxa:** `data/` papkasini (ayniqsa `app.db` va `secret.key`) muntazam nusxalang.

## ⚠️ Cheklovlar
- Demo 1C bazasi xotirada saqlanadi — server qayta ishga tushganda qoralamalar o'chadi (jadval holati esa
  `data/sessions/` da saqlanadi).
- O'z hisoblar orasidagi o'tkazma, ish haqi va valyuta hisoblari aniqlanadi, lekin avtomatik yozilmaydi —
  1C da qo'lda kiritiladi.
- "🔴 Farq bor" yoki "raqamsiz mos kelgan" qatorni 👁 Tekshirish oynasida sabab yozib, baribir yozish mumkin;
  to'liq dublikat (raqam + sana + summa + kontragent) hech qachon yozilmaydi.
- 🧪 Namuna vipiska va Didox demo hisob-fakturalari faqat Demo firmada ishlaydi — haqiqiy firmada o'ylab topilgan
  ma'lumot 1C bilan solishtirilmaydi. Didox uchun firma sozlamasida API kalitini kiriting.
- Didox EHF da QQS stavkasi qator bo'yicha olinadi (12% / 0% / QQSsiz); stavka ko'rsatilmasa — 12%.
- Didox partner API endpoint/maydon nomlari shartnomaga qarab farq qiladi — `services_didox.py`
  (`DIDOX_LIST_PATH`, `_map_invoice`) ni o'z hujjatlaringizga moslang.
- Sessiyalar server xotirasida (bitta jarayon uchun mo'ljallangan).
- Eski `.xls` format qo'llab-quvvatlanmaydi — `.xlsx` ga saqlang.
