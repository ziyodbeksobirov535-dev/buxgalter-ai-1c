# BUXGALTER AI — AI yordamchilar uchun qoidalar

Bu fayl loyihada ishlaydigan har qanday AI yordamchi (Claude Code, ChatGPT / Codex, Cursor va boshqalar) uchun.
Kod yozishdan oldin to'liq o'qing. Foydalanuvchi bilan O'ZBEK tilida (lotin) gaplashing.

## Loyiha
FastAPI + Alpine.js veb-ilova va Telegram bot: bank vipiskasi va Didox fakturalarini 1C:Бухгалтерия для Узбекистана
3.0 (uzcloud, 1C:Fresh, OData) bilan solishtiradi, dublikatlarni ajratadi, schyotlarni 1C tarixidan o'rganadi,
1C ga import fayli (1CClientBankExchange) tayyorlaydi. AI agent (Gemini) 1C va Notion'dan faqat o'qiydi.

Asosiy fayllar: `main.py` (API), `core.py` (sessiya, yozish darvozasi), `services_1c.py` (OData, yozish qulfi),
`services_vipiska.py` (vipiska tahlili), `services_ai.py` (agent va vositalari), `services_insight.py` (agentning
1C o'qish vositalari), `services_notion.py` (Notion sklad), `services_dashboard.py`, `db.py`, `templates/index.html`.

## 🔒 Qat'iy qoidalar (buzish mumkin emas)
1. **Haqiqiy 1C ga hech qanday yozuv yo'q.** `services_1c.WRITES_ENABLED = False` va `write_guard()` —
   egasining qarori. GET dan boshqa har qanday so'rov (POST, PATCH, PUT, DELETE, MERGE, **OData `$batch` ham**)
   faqat `HttpOData._send` orqali yuboriladi va u yerda bloklanadi. `self.client.post(...)` kabi to'g'ridan-to'g'ri
   chaqiruv bilan qulfni chetlab o'tmang. Qulfni ochmang — buni faqat egasi alohida aytadi.
2. Kelajakda yozish ochilsa ham faqat `WRITE_ALLOWLIST`: bank vipiskasi (`ПоступлениеНаРасчетныйСчет`,
   `СписаниеСРасчетногоСчета`) va kirim fakturalari (`ПоступлениеТоваровУслуг`). Kontragent, shartnoma va boshqa
   hujjatlar yaratilmaydi.
3. Haqiqiy firma bilan ishlaganda: faqat o'qish, bir vaqtda bitta so'rov; xato login bilan qayta
   urinmang (1C foydalanuvchini bloklaydi). 1C web-interfeysida hech narsa bosmang/saqlamang.
4. Haqiqiy ma'lumot (STIR, summalar, kontragent nomlari, parol, token) repozitoriyga, testlarga va commit
   matniga tushmasin. `data/` va `.env` — maxfiy, git va Docker obraziga kirmaydi.
5. GitHub'ga `push` — faqat foydalanuvchi aniq "push qil" deganda. Mahalliy commit — mumkin.
6. Demo ma'lumotlarni (`demo_data.py`) haqiqiy mantiqqa (masalan `TAX_INNS`) qo'shmang.

## Ish tartibi
- Testlar: `.venv/bin/python -m pytest -q` — har o'zgarishdan keyin hammasi o'tishi shart; yangi funksiyaga test.
- Serverni qayta ishga tushirish: `NO_OPEN=1 ./start.command` (http://127.0.0.1:8000, log: `data/server.log`).
- Kod uslubi: mavjud kodga o'xshash, izohlar o'zbekcha, qisqa. UI matnlari o'zbekcha.
- Katta o'zgarishdan oldin foydalanuvchiga reja ayting; tugagach nima o'zgarganini va nima tekshirilmaganini ayting.
- Bir vaqtda ikki AI bir faylni o'zgartirmasin: ishni tugatib commit qiling, keyingisi `git log` / `git diff` dan
  boshlasin.

## Codex → Claude: agent tuzatishlari (2026-10-26)
- `services_insight.py`: kontragent qoldig'ini o'qish xatolari `ogohlantirishlar` va `toliq=false` bilan qaytadi; xato endi “qarz yo'q” emas.
- Jonli agent hujjatlari va raw so'rovlari tashkilot GUID filtri bilan chegaralanadi. STIR, tashkilot yoki metadata tasdiqlanmasa o'qish rad etiladi. Tashkilot maydoni yo'q umumiy kataloglar ham hozir rad etiladi; ularni keyin alohida ruxsat ro'yxati bilan ochish mumkin. Dashboard va boshqa eski o'qish yo'llarining tashkilot chegarasi alohida audit talab qiladi.
- `core.py` chatning oxirgi 20 xabarini saqlaydi/tiklaydi; oddiy chat endpointi ham sessiyani saqlaydi.
- OpenAI va Claude vositalarni API orqali tanlaydi, natijani qayta oladi; 5 bosqich limiti bor. Gemini mexanizmi saqlangan.
- Katta vosita natijalari JSONni buzmasdan 50 qatorgacha qisqaradi, qisqartirish belgisi qo'shiladi. To'liq pagination va umumiy token budjeti keyingi ish.
- Streaming barcha provayderlar uchun umumiy chat mexanizmidan foydalanadi va yakuniy provider/engine holatini qaytaradi. Bu haqiqiy tokenma-token streaming emas; UI ham hali `/api/chat` ishlatadi.
- `tests/test_agent_upgrade.py` regressiyalarni tekshiradi. Haqiqiy 1C va tashqi AI API chaqirilmadi; yozish qulfi o'zgartirilmadi.

## Codex → Claude: E-DOCS tayyorgarligi (2026-10-26)
- `services_edocs.py`: Playwright adapteri, bir login urinishi, reklama yopilishini kutish, firma STIRini tekshirish, JSON eksportni olish. Sayt profili `data/edocs-profile.json` (mahalliy, gitga kirmaydi). Cookie, parol, trace yoki screenshot diskka yozilmaydi; vaqtinchalik download kontekst bilan o'chadi.
- Firma sozlamalariga `edocs_enabled`, `edocs_login`, shifrlangan `edocs_password` qo'shildi; SQLite migratsiyasi mavjud. Egasi uchun `/api/firms/{firm_id}/edocs/check` va UI tugmasi bor. E-DOCS tanlanganda Didox demo/API ga yashirin o'tish yo'q.
- `requirements-edocs.txt`, `docs/edocs.md`, `tests/test_edocs.py` qo'shildi. Muhitdagi `/usr/bin/chromium` ishlatiladi; bo'lmasa Playwright brauzerini o'rnatish kerak. Chromium CDN yuklashga hozirgi tarmoq siyosati 403 berdi; tekshiruvlar mavjud tizim Chromium bilan qilinadi.
- MUHIM: haqiqiy E-DOCS login/selectors/eksport formati hali tasdiqlanmagan. Namuna JSON shartnomasi E-DOCS formati deb hisoblanmasin. Foydalanuvchi login yaratmoqda; tayyor bo'lgach maxfiy sozlamada kiritadi, jonli DOM va eksport moslanadi. Hozirgi tayyorgarlik “avtomatik integratsiya to'liq ishlaydi” degani emas.
- Avtomatik jadval/sahifalash/davr filtri keyingi bosqich; faqat eksport tugmasi bergan fayl olinadi. 1C yozish qulfi o'zgartirilmadi, EDO hujjatlariga imzo yoki jo'natish amalga oshirilmaydi.

- Tekshiruv: to‘liq to‘plamda 242 test o‘tdi; keyin qo‘shilgan endpoint ruxsat testi alohida o‘tdi. Brauzer testi mahalliy HTTP sahifalar va sun’iy ma’lumotlarda bajarildi, jonli E-DOCS sinalmadi. Muhit draftida doc.edocs.uz va cdn.playwright.dev domenlari hamda Playwright o‘rnatish ko‘rsatmalari saqlandi; bu runtime ruxsati qo‘llandi degani emas.
