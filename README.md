# 🏦 BUXGALTER AI — 1C:Buxgalteriya (O'zbekiston 3.0) uchun AI tizim

Bank vipiskasi va Didox EHF larini **1C OData** bilan solishtiradigan, **dublikatlarni bloklaydigan**,
schyotlarni **buxgalterning oldingi 1C yozuvlaridan o'rganadigan** va yangi hujjatlarni faqat
**qoralama** (`Posted: false`) sifatida kirituvchi tizim.

- 🖥️ Split-screen Web App (70% jadval / 30% AI Copilot), yorug'/to'q rejim, 100% o'zbek tilida
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

Brauzerda **🧪 Test Vipiska Namunasini Yuklash** tugmasini bosing. Namuna fayl
(`sample_bank_vipiska.xlsx`) server ishga tushganda avtomatik yaratiladi va **📥 Test Vipiska faylini yuklab olish**
tugmasi orqali ham olish mumkin.

### Namuna vipiska — sinov stsenariysi (8 qator)

| # | Holat | Nima tekshiriladi |
|---|-------|-------------------|
| №112, №45, №118 | 🟢 1C da bor (Dublikat) | Anti-dublikat bloklovchi — checkbox o'chirilgan, serverda ham rad etiladi |
| №52 (yetkazib beruvchi) | 🟡 Yangi → **60.10** | 🧠 1C tarixidan `ОплатаПоставщику` |
| №131 (xaridor) | 🟡 Yangi → **40.10** | 🧠 1C tarixidan `ОплатаОтПокупателя` |
| №53 (QQS to'lovi) | 🟡 Yangi → **64.10** | 🧠 1C tarixidan `ПеречислениеНалога` |
| №54 (bank komissiyasi) | 🟡 Yangi → **94.20** | 🧠 1C tarixidan `ПрочееСписание` |
| №124 | 🔴 Farq bor | Summa tafovuti: faylda 19 500 000, 1C da 19 050 000 |

4 ta yangi qatorni kiritgach, shu faylni **qayta yuklang** — endi 7 ta qator dublikat sifatida bloklanadi.
Bu ikki marta kiritish imkonsizligini isbotlaydi.

---

## 🧠 Gemini API kaliti (bepul, 30 soniya)

1. <https://aistudio.google.com/app/apikey> → **Create API key**
2. Web App → **⚙️ Sozlamalar** → kalitni qo'ying (yoki `.env` da `GEMINI_API_KEY=`)

Gemini quyidagi vositalarni (tools) chaqiradi: `analyze_and_deduplicate_vipiska()`, `check_didox_vs_1c()`,
`learn_from_1c_history(stir)`, `post_confirmed_to_1c(doc_ids)`, `cash_flow_summary()`.

> `post_confirmed_to_1c` hech narsani o'zi yozmaydi — chatda **tasdiqlash kartasini** chiqaradi. 1C ga yozuv
> faqat buxgalter tugmani bosgandan keyin bo'ladi. Gemini xato bersa yoki limit tugasa, Demo AI avtomatik ishlaydi.
> OpenAI / Claude ham zaxira sifatida sozlanadi.

---

## 🗄️ Real 1C ga ulanish (OData)

1. 1C Konfigurator: **Администрирование → Публикация на веб-сервере** (OData yoqilgan holda).
2. 1C:Предприятие: **Все функции → Обработки → Настройка автоматического REST-сервиса**
   (`Настройка стандартного интерфейса OData`) — quyidagilarni yoqing:
   `Catalog_Контрагенты`, `Catalog_ДоговорыКонтрагентов`, `Catalog_БанковскиеСчета`, `Catalog_Номенклатура`,
   `Catalog_Организации`, `Catalog_СтатьиДвиженияДенежныхСредств`, `ChartOfAccounts_Хозрасчетный`,
   `Document_ПоступлениеНаРасчетныйСчет`, `Document_СписаниеСРасчетногоСчета`, `Document_ПоступлениеТоваровУслуг`.
3. ⚙️ Sozlamalar → "Demo 1C bazasi" belgisini olib tashlang → URL (`http://server/baza`), login, parol →
   **🔌 Saqlash va 1C ulanishini tekshirish**. Tizim `$metadata` ni o'qib, nashr qilinmagan obyektlarni ko'rsatadi.

**Muhim:** konfiguratsiya relizlariga qarab rekvizit nomlari farq qilishi mumkin (masalan, `Контрагент_Key` o'rniga
kompozit `Контрагент` + `Контрагент_Type`). Ular `services_1c.py` dagi `FIELDS` lug'atida bir joyda jamlangan.
Birinchi real ulanishda `$metadata` ni tekshirib, kerak bo'lsa shu lug'atni moslang. Avval test bazada sinang.

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
2. O'z Telegram ID ingizni `ALLOWED_TELEGRAM_IDS=` ga yozing (bilmasangiz — botga `/start` yuboring, u ID ni aytadi).
   Ro'yxat bo'sh bo'lsa, **hech kim** foydalana olmaydi.
3. Mini App HTTPS talab qiladi. Masalan:
   ```bash
   cloudflared tunnel --url http://127.0.0.1:8000
   ```
   Chiqqan `https://...trycloudflare.com` manzilini `WEBAPP_URL=` ga yozing.
4. Tashqi manzil orqali brauzerdan kirish uchun `APP_ACCESS_TOKEN=` (uzun tasodifiy satr) qo'ying va uni
   ⚙️ Sozlamalar → "Masofadan kirish tokeni" ga kiriting. Telegram Mini App esa `initData` imzosi orqali tekshiriladi.
5. `.venv/bin/python main.py` — bot server bilan birga ishga tushadi (sessiyalar umumiy).

Bot imkoniyatlari: `/start` → **📱 Buxgalter AI Web App'ni ochish**; vipiska faylini chatga yuborish →
tahlil + **✅ Yangilarini 1C ga kiritish** / **📱 Web App'da ko'rish**; `/namuna` — test fayl; erkin savollar → AI.

---

## 📂 Tuzilma

```
main.py              FastAPI: API + Web App, kirish nazorati, bot'ni ishga tushirish
bot.py               aiogram 3 Telegram bot
core.py              Umumiy ish oqimlari (Web, bot va AI shu yerdan foydalanadi)
services_1c.py       1C OData: Ref_Key, dublikat, tarixdan o'rganish, qoralama; Http + Demo transport
services_vipiska.py  Excel / CSV / 1CClientBankExchange parser, tahlil, payload
services_didox.py    Didox EHF ↔ ПоступлениеТоваровУслуг
services_ai.py       Gemini Function Calling + Demo AI + OpenAI/Claude zaxira
demo_data.py         Demo 1C bazasi (OData shaklida) va namuna vipiska generatori
config.py            .env + UI sozlamalari (data/settings.json, git'ga kirmaydi)
templates/index.html Tailwind + Alpine.js + Lucide + telegram-web-app.js
```

### API
| Metod | Yo'l | Vazifa |
|---|---|---|
| GET | `/api/status` | 1C / AI holati |
| POST | `/api/vipiska/upload` | Vipiska yuklash (multipart `file`) |
| POST | `/api/vipiska/sample` | Namuna vipiskani tahlil qilish |
| POST | `/api/vipiska/recheck` | 1C da qayta tekshirish |
| POST | `/api/didox/load` | Didox EHF ↔ 1C |
| POST | `/api/rows/{id}/account` | Schyotni o'zgartirish |
| GET | `/api/rows/{id}/inspect` | Tafsilot + OData payload ko'rinishi |
| POST | `/api/post` | Tanlanganlarni 1C ga qoralama sifatida yozish |
| POST | `/api/chat` | AI Copilot |
| GET/POST | `/api/settings` | Sozlamalar (maxfiy kalitlar maskalanadi) |
| GET | `/api/1c/metadata` | `$metadata` tekshiruvi |

---

## 🧪 Avtomatik testlar

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

47 ta test: parser (summa/yo'nalish formatlari, o'tkazib yuborilgan qatorlar), dublikat va majburan yozish qoidalari,
test vipiskaning jonli 1C ga tushmasligi, parallel yozishda ikki marta kiritilmaslik, uzcloud 401/404 javoblari,
bloklangan login'ga qayta urinmaslik, Host/parol himoyasi, Telegram imzosi, Gemini model zaxirasi.
Testlar internetga chiqmaydi va `data/` papkasiga tegmaydi.

## ⚠️ Cheklovlar
- Demo 1C bazasi xotirada saqlanadi — server qayta ishga tushganda qoralamalar o'chadi (jadval holati esa
  `data/sessions/` da saqlanadi).
- O'z hisoblar orasidagi o'tkazma, ish haqi va valyuta hisoblari aniqlanadi, lekin avtomatik yozilmaydi —
  1C da qo'lda kiritiladi.
- "🔴 Farq bor" yoki "raqamsiz mos kelgan" qatorni 👁 Tekshirish oynasida sabab yozib, baribir yozish mumkin;
  to'liq dublikat (raqam + sana + summa + kontragent) hech qachon yozilmaydi.
- Didox partner API endpoint/maydon nomlari shartnomaga qarab farq qiladi — `services_didox.py`
  (`DIDOX_LIST_PATH`, `_map_invoice`) ni o'z hujjatlaringizga moslang.
- Sessiyalar server xotirasida (bitta jarayon uchun mo'ljallangan).
- Eski `.xls` format qo'llab-quvvatlanmaydi — `.xlsx` ga saqlang.
