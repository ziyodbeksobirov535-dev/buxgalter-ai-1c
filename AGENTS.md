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
7. Didox'ga yozish faqat `services_didox_draft.ALLOWED_POST`: STIR+parol bilan kirish va 002 hisob-faktura
   QORALAMASI. Imzolash, yuborish, rad etish, o'chirish endpointlarini qo'shmang. Firma sozlamasi
   `didox_drafts_enabled` (egasi yoqadi) + har bir qoralama uchun buxgalter tasdig'i shart.

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


## Codex → Claude: E-DOCS login tayyor bo'lgandagi ish (2026-10-26)
- Ochiq doc.edocs.uz mijozida autentifikatsiya yo'li tasdiqlandi: `POST https://api.edocs.uz/v1.0.1/loginpassword`, JSON `login/password`; javobdagi `token` bilan `GET /profile`, undagi `tin` STIR bilan solishtiriladi. Bu ommaviy hamkorlik API shartnomasi deb hisoblanmasin.
- `services_edocs_api.py` va testlar qo'shildi. Egasi uchun “Ulanishni tekshirish” endi shu API yo'lini ishlatadi; token faqat xotirada, xatolarda server matni va maxfiy qiymatlar qaytarilmaydi. Redirectlar va qayta urinishlar yo'q. Noto'g'ri firma bo'lsa to'xtaydi.
- `python -m services_edocs_api` maxfiy `EDOCS_LOGIN`, `EDOCS_PASSWORD` bindinglari va `EDOCS_INN` orqali mahalliy bazaga yozmasdan tekshiradi. Boshqa kompyuterdagi SQLite sozlamalari bu bulutga avtomatik kelmaydi.
- Hozirgi mashinada ushbu qiymatlar yo'q, mahalliy bazada ham E-DOCS qiymatlari yo'q. Jonli login bajarilmadi, hisob paroli taxmin qilinmadi.
- `api.edocs.uz` tarmoq qo'shimchasi va kerakli binding nomlari draftda saqlandi; runtime so'rovi hozircha proksidan 403 oldi. Muhit sozlamalari qo'llangach qayta tekshirish kerak.
- Brauzerning haqiqiy saytga kirishi `ERR_CERT_AUTHORITY_INVALID` bilan to'xtadi. Doimiy NSS bazasiga CA qo'shish auto-review tomonidan rad etildi va bajarilmadi; TLS tekshiruvi o'chirilmasin, bilvosita chetlab o'tilmasin. Mavjud tizim CA'sidan foydalanuvchi HTTPX autentifikatsiya yo'li tayyorlandi.
- Fakturalar eksporti hali avvalgi brauzer adapterida, haqiqiy eksport formati/sahifalash/jadval asosida sinxronlash uchun jonli hisobdan keyingi moslash talab qilinadi.

- Tekshiruv: yangilangan to‘liq test to‘plami — 256 passed, bitta Starlette TestClient deprecation ogohlantirishi. Jonli E-DOCS hisobiga kirish bajarilmadi.

## Codex → Claude: jonli E-DOCS o'qishi (2026-10-27)
- Foydalanuvchi maxfiy bindinglarni saqlab muhitni publish qildi. Login/parol va firma STIRi jonli hisobda muvaffaqiyatli tekshirildi. Hech qanday maxfiy qiymat repozitoriyga yoki logga chiqarilmadi.
- `services_edocs_api.fetch_invoices`: faqat kiruvchi imzolangan `factura`, to'liq pagination va har birining detail/tovar qatorlarini ketma-ket GET bilan oladi. Jarayon jonli hisobda to'liq bajarildi; haqiqiy firma ma'lumotlari test/commit matniga kiritilmadi.
- Native format: `data.facturadoc`, `data.buyertin/sellertin/seller`, `data.productlist.products`; miqdor `count`, narx `summa`, summa `deliverysumwithvat`, QQS `vatrate/withoutvat`, MXIK `catalogcode`. Xaridor STIRi, qator summalari, to'liq son, pagination va takroriy ID tekshiriladi; qisman natija qaytarilmaydi.
- `services_didox` E-DOCS yoqilganda endi native API fetch ishlatadi. Playwright, reklama va mahalliy brauzer profili talab qilinmaydi. Eski brauzer adapteri eksperimental holda qoldi.
- Bulut login/parol bindinglari mahalliy firmada ikkala credential ham yo'q va STIR `EDOCS_INN` bilan mos bo'lgandagina fallback sifatida olinadi. Mahalliy login/bulut paroli aralashmaydi. Token faqat xotirada; faqat autentifikatsiya POST, keyin GET.
- CLI `--fetch`, `--from-date`, `--to-date` qo'shildi. 1000 hujjat chegarasi bor. Hozir faqat kiruvchi imzolangan EHF; fon jadvali bo'yicha sinxronlash va boshqa hujjat turlari qo'shilmagan. Haqiqiy 1C ga ulanish/yozish ushbu sinovda bajarilmadi.
- Foydalanuvchi E-DOCS'da faktura/shartnoma qoralamasini yaratish imkonini so'radi. Sayt mijozida save va save+sign alohida, generic POST `/documents/{type}/` mavjud; ikkala turning qoralama ro'yxati GET orqali ochildi. Lekin login/parol rejimida ko'rish huquqi cheklovi ko'rsatilgan. Qoralama yaratish ruxsati tasdiqlanmagan, haqiqiy hujjat yaratilmadi, imzo/yuborish/saqlash endpointlari chaqirilmadi.
- Yakuniy tekshiruv: 270 passed, bitta Starlette TestClient deprecation ogohlantirishi. Native EHF → mavjud tahlil pipeline'i regressiyasi ham bor. Jonli 1C solishtirish va qoralama yaratish tekshirilmagan. Yangi startup ko'rsatmasi muhit draftiga saqlandi; bu yangi snapshot publish qilinganini anglatmaydi.

## Codex → Claude: BUXGALTER AI EHF sahifasi (2026-10-27)
- Foydalanuvchi hozircha faqat E-DOCS o'qishini dasturga qo'shishni so'radi. U lokal kompyuterda `start.command` ishlatadi. Har yakunlangan o'zgarish GitHub'ga push qilinsin degan ko'rsatma berdi; joriy branch `work`.
- EHF sahifasida E-DOCS yuklash tugmasi, sana oralig'i, oxirgi muvaffaqiyatli yuklash vaqti va faqat o'qish xabari qo'shildi. `/api/didox/load` date_from/date_to parametrlarini native adapterga uzatadi. Sana bo'yicha yuklash E-DOCS uchun; eski Didox oqimi saqlangan.
- E-DOCS sahifasiga kirganda avtomatik barcha davrni olish o'rniga foydalanuvchi sana tanlab yuklaydi. Bu fon jadvali emas.
- E-DOCS qatorlaridan 1C ga yozish UI, core.post (Telegram ham) va AI tasdiqlash kartasida bloklanadi. Manba sessiyada saqlanadi: E-DOCS sozlamasi o'chirilganda ham avval olingan E-DOCS qatorlari yozilmaydi.
- Haqiqiy E-DOCS fakturalari demo 1C ga solishtirilmaydi; jonli ulanish bo'lmasa offline/unchecked ko'rsatiladi. Xatoda avvalgi jadval saqlanadi. `tests/test_edocs_app.py` ilova endpointlari, read-only, davr va qayta yuklash regressiyalarini tekshiradi.
- Lokal ishga tushirish bo'yicha docs/edocs.md yangilandi. Bulut bindinglari lokal kompyuterga avtomatik ko'chmaydi. data/ va .env saqlanadi; lokal o'zgarishlarni reset/clean bilan yo'qotmang.
- Tekshiruv: 273 test o‘tdi. Haqiqiy Chromium brauzerida mahalliy sun’iy hisob bilan login → EHF sahifasi → E-DOCS yuklash → faktura qatori → yozish tugmalari yo‘qligi ham tekshirildi. Sahifadagi suratlar faqat sun’iy sinov ma’lumotlaridan; haqiqiy fakturalar screenshotga olinmadi.

## Codex → Claude: Sozlamalar va Hisob qoidalari (2026-10-27)
- Foydalanuvchi Qoidalar tugmasini yuqori menyudan Sozlamalar ichiga ko'chirishni va Sozlamalarni tartiblashni so'radi.
- Qoidalar kontenti yagona nusxada `Sozlamalar → Hisob qoidalari` ichida. `#/settings/rules` yangilashda saqlanadi; eski `#/rules` shu joyga yo'naltiriladi. Bank/EHF ish joyi endi alohida qoidalar sahifasini ko'rsatmaydi; qaytganda rulesDirty orqali qayta tekshirish mantiqi saqlangan.
- Sozlamalar guruhlangan ichki menyuga ega: Umumiy; Firma va ulanishlar; Boshqaruv; Shaxsiy. Tizim holati alohida bo'limda; firma rekvizitlari va integratsiyalar alohida «Joriy firma» va «Ulanishlar»da. 1C yozish va Notion ichki havolalari yangi Ulanishlarga moslangan.
- Buxgalter/kuzatuvchida qoidalar kirishi saqlangan, tahrirlash huquqi mavjud backend can_edit bo'yicha. Admin bo'limlar frontendda ham rolga qarab yashiriladi. Yangi API yoki ruxsat o'zgarishi kiritilmadi.
- Firma va Ulanishlar o'rtasida saqlanmagan forma qiymatlari saqlanadi; firma almashtirilsa to'g'ri firmaga qayta yuklanadi.
- `tests/test_settings_navigation.py` haqiqiy Chromium va sun'iy mahalliy hisoblar bilan eski/yangi marshrutlar, qayta ochish, qoida qo'shish, uch rol, forma saqlanishi, firma almashish va telefon kengligini tekshiradi. README va docs/edocs.md navigatsiya yo'llari yangilangan. Haqiqiy 1C/E-DOCS ga test so'rovlari yuborilmagan.
- Yakuniy tekshiruv: 276 passed, bitta mavjud Starlette TestClient deprecation ogohlantirishi. Uch rolning brauzer sinovi ham to'liq o'tdi. Skrinshotlar sun'iy ma'lumotli sinov muhitidan olingan.

## Codex → Claude: EHF ikki tomonlama nazorat (2026-10-27)
- Foydalanuvchi kirim/chiqimni barcha holatlari bilan yuklash, vipiskadagidek filtrlar, faqat imzo kutilmoqda/tasdiqlanganlarni normal sverka, o'chirilgan EHF 1Cda bo'lsa ogohlantirish va EHF/1C bir xil yurishini nazorat qilishni so'radi.
- `services_edocs_api`: in/received,signed,rejected va out/drafts,sended,signed,rejected,deleted bucketlari sahifalanadi. Yo'nalish tovar kirimi/chiqimi bo'yicha; kontragent kirimda sotuvchi, chiqimda xaridor. EHF statusi alohida saqlanadi. Tovar qatori xatosi sarlavhani yashirmaydi: parse_error qaytadi.
- Kiruvchi deleted ro'yxatining haqiqiy API so'rovi 422 ERROR_WITH_FILTER beradi; frontend ham bu ro'yxatni ko'rsatmaydi. Dastur buni qamrov ogohlantirishida ko'rsatadi. Avval yuklangan source_id lar ro'yxatdan yo'qolsa GET detail orqali qayta tekshiriladi. Boshlang'ich tarixdagi IDsi noma'lum kiruvchi o'chirilganlar qamrovi to'liq emas. Teskari nazorat “yuklangan EHF ro'yxatida topilmadi” deydi, mutlaq yo'qlik deb talqin qilinmasin.
- `services_ehf.py`: alohida faqat o'qish sverka. Kirimda xarid + olingan faktura, chiqimda realizatsiya + berilgan faktura metadatasida mavjud obyektlar olinadi. Tashkilot GUIDi so'rovda va javobda tekshiriladi; to'liq pagination, ketma-ket so'rovlar. Metadata/1C xatosi “yo'q” emas, “tekshirilmagan”.
- Raqamlar normallanadi; raqam/sana/summa/STIR, mavjud roaming ID hamda tovar miqdori/MXIK/qator summasi/QQS tekshiriladi. Ikki mos hujjat, noto'g'ri kontragent, 1C qoralama/o'chirish belgisi yashirilmaydi. Normal sverka faqat signed/sended/received uchun. Deleted uchun faol 1C mosligi alohida qizil nazorat. Qoralama/rad etilganlar oddiy sverkadan chiqarilgan.
- Teskari 1C-only nazorat faqat tanlangan davrda. 1C-only qatorining source_id si yo'q, uni E-DOCS detailga yubormang. EHF ID/yo'nalish/sana tarixi davr almashtirilganda va restartda saqlanadi; tarix joriy foydalanuvchi/firma sessiyasi uchun. ehf_summary ham tiklanadi. Yangi EHF analysis versiyasi eski sessiyaga qayta tekshirishni tavsiya qiladi. 1C yozish qulfi va E-DOCS read-only cheklovi saqlangan.
- UI: EHF holati, sverka natijasi, kirim/chiqim, sana va matn qidiruvi; alohida KPI va qamrov ogohlantirishlari. AI vositasi ham shu sverka xulosasini oladi. `docs/ehf-reconciliation.md` cheklovlar va qo'llanmani beradi.
- E-DOCS kirim/chiqim va turli holatlari haqiqiy hisobda to'liq o'qildi; maxfiy ma'lumot repo/test/commitga kiritilmadi. Bu bulutda jonli 1C credential yo'q: 1C natijalari sun'iy testlar bilan tekshiriladi, haqiqiy 1C release bilan tasdiqlash hali kerak. Sinovda hujjat yozish/imzolash/o'chirish bajarilmagan.
- Yakuniy tekshiruv: 307 passed, bitta mavjud Starlette TestClient deprecation ogohlantirishi. Native API pagination/holatlar, ikki tomonlama sverka, rekvizit/tovar/QQS farqlari, noaniq natijalar, ID tarixining restart/davrdan keyin tiklanishi hamda Chromium'da filtrlar/telefon kengligi tekshirildi. UI tekshirish oynasi barcha mos 1C hujjatlarini ko'rsatadi; EHFdan foydalanilmaydigan schyot/tanlash boshqaruvlari yashirilgan. Fon jadvali va hujjat yozish qo'shilmagan.

## Codex → Claude: faktura ichidagi Gemini izohi (2026-10-27)
- Foydalanuvchi EHF tekshirish oynasida aniq farqni AIga so'rashni so'radi. `services_ehf_ai.py`, `POST /api/ehf/{row_id}/chat` va modal ichidagi chat qo'shildi. Tayyor savollar, erkin savol, davomiy savol va EHF/1C tovar jadvallari mavjud. Mavjud Gemini kaliti/modeli ishlatiladi; umumiy chat provayderi o'zgartirilmaydi. Kalit yo'q yoki provider xatosida mahalliy izoh deb ochiq belgilanadi.
- Gemini faqat tanlangan faktura, mos 1C hujjatlari, tovarlar, farqlar va qamrov holatini oladi; credential, boshqa faktura va umumiy chat tarixi berilmaydi. Google chaqiruvida tools yo'q: EDO/1C qayta o'qish, yozish, imzolash yoki moslikni o'zgartirish bajarilmaydi. `onec_only` qatorida EHF qiymatlari to'qilmaydi (`ehf=null`).
- `Session.ehf_chats` foydalanuvchi/firma va qatorga bog'langan, restartda tiklanadi (20 suhbat, har birida 20 xabar). Kontekst hashiga oxirgi yuklash vaqti ham kiradi: qayta yuklashdan keyin eski suhbat ishlatilmaydi, javob kutishda natija o'zgarsa 409. Mijozda kechikkan javob boshqa oynaga tatbiq qilinmaydi. Kontekst budjeti va jami/berilgan sonlari hujjatda yozilgan.
- Sverkada haqiqiy farq bilan noaniq moslashtirish ajratildi: `comparison_warnings` sariq, `partial` alohida filtr/KPI. Raqam/sana/summa/STIR mos, tovar/MXIK mosligi yetarli bo'lmasa “Rekvizitlar mos; tovarlar to'liq tekshirilmadi”. Moslashtirilmagan 1C qatori avtomatik ortiqcha deb tasdiqlanmaydi; tasdiqlangan miqdor/summa/QQS/MXIK farqlari qizil qoladi. EHF analysis versiyasi oshirildi: eski natijani qayta yuklang.
- Mahalliy brauzerda modal yopilganda `inspect=null` bilan Alpine ichki effektlari orasidagi xato ham tuzatildi; modal endi barqaror `detail` kontekstidan foydalanadi. `docs/ehf-reconciliation.md` ishga tushirish va cheklovlarni tushuntiradi. Barcha test/suratlarda faqat sun'iy firma ma'lumotlari ishlatildi. Bulutda Gemini kaliti yo'q: haqiqiy Google javobi va jonli 1C bu ishda sinalmadi, adapter soxta Google mijozida tekshirildi.
- Yakuniy tekshiruv: 316 passed, bitta mavjud Starlette TestClient deprecation ogohlantirishi. Gemini kontekstidagi tovar/QQS tafsiloti, allowlist/qisqartirish, follow-up va restart, firma/login huquqi, eskirgan kontekst 409, kalit/provider xatosi, 404 model fallback, noaniq/tasdiqlangan farqlar, desktop/telefon Chromium suhbat oqimi tekshirildi. Lokal nusxa `work`dan yangilanib `start.command` qayta ishga tushirilgach EHF qayta yuklansin.

## Claude: Didox chiqim faktura qoralamasi (2026-10-29)
- Egasi so'radi: 1C sotuvlaridan (Реализация) Didox'da chiqim faktura qoralamasi, imzoni o'zi Didox'da qo'yadi.
- `services_didox_draft.py`: `https://api.didox.uz/`, `POST v1/auth/{STIR}/password/ru` (bir urinish; rad etilgan parol
  o'zgarmaguncha qayta yuborilmaydi), GET profil/xaridor/`v1/measures/all`, `POST v1/documents/002/create`. Token 20 daqiqa
  xotirada. Yo'llar va maydonlar ochiq SDK/integratsiyalardan olingan — Didox'ning rasmiy hujjati topilmadi, JONLI TASDIQLANMAGAN.
- Qoralama faqat EHF sverkasida `onec_only` bo'lgan, o'tkazilgan `РеализацияТоваровУслуг` uchun. 1C dan faqat GET: birlik
  (`Catalog_КлассификаторЕдиницИзмерения`) va shartnoma. MXIK, birlik, QQS, xaridor STIRi yetishmasa yuborilmaydi.
- `didox_drafts` jadvali: yuborishdan oldin band qilinadi; aniq 4xx rad — o'chiriladi, noma'lum natija — `unknown`, qayta
  yuborilmaydi. Audit logga yoziladi. UI: EHF tekshirish oynasi → «Didox'da chiqim faktura qoralamasi»; sozlama: Ulanishlar → Didox.
- Tekshiruv: 321 test; sun'iy nusxada UI ko'rindi, parolsiz holatda Didox'ga so'rov ketmadi. Jonli Didox va haqiqiy 1C
  birligi/shartnoma maydon nomlari sinalmagan. Partner kaliti kerak bo'lishi mumkin (`didox_partner_token`).
- EHF manbasi bitta: `edocs_enabled` — E-DOCS, aks holda Didox (UI'da radio). Ikkalasidan bir vaqtda tortilmaydi; haqiqiy
  firmada demo EHF'ga yashirin o'tish yo'q. Didox API har so'rovda Didox beradigan `Partner-Authorization` talab qiladi
  (sayt JS'ida ko'rindi): `didox_partner_token` bo'lmasa login/so'rov umuman yuborilmaydi. Sayt ichidagi kalitni
  ishlatib, Didox mijozini taqlid qilmang — faqat Didox'dan rasmiy olingan kalit. Jonli Didox o'qishi hali sinalmagan.

## Codex → Claude: firma egasi dashboardi talablari (2026-11-02)
- Foydalanuvchi avval savol-javob orqali yangi dashboardni rejalashtirishni so'radi. 22 javobning yakuni [docs/company-owner-dashboard.md](docs/company-owner-dashboard.md)da. Bu o'zgarish talablar hujjati; yangi rol/UI/hosting hali tayyorlangan deb hisoblanmasin.
- Firma egasi faqat biriktirilgan firmalarda barcha biznes tafsilotlarini o'qiydi. Bir necha firmasini alohida almashtiradi, jamlanma tanlanmagan. EHF–1C texnik tafovutlari buxgalter panelida qoladi. Hozirgi `owner` barcha firmaga ruxsatli administrator ekanini unutmang: yangi cheklangan egaga shu rolni bermang.
- Buxgalter biriktirilgan firmalarda hozirgi to'liq ish paneli va firma sozlamalariga ega bo'ladi. Umumiy AI/tizim sozlamalari, akkaunt yaratish va firma biriktirish faqat administratorda. Mavjud 1C/EDO yozish va imzo cheklovlari saqlanadi.
- Egasi: bank/kassa, savdo/daromad, tannarx/xarajat/foyda-zarar, ikki tomonlama qarz va ombor; boshlang'ich davr joriy oy, ochiq sahifada har 5 daqiqa yangilash. Erkin Gemini savol-javobi va faqat PDF hisobot. AI/API/kesh/PDFda firma va rol chegarasi kerak.
- To'lov muddati 1Cda kiritilmasligi aytildi. Qarz yoshi 30 kun yoki bitta kontragentning ochiq qarzi 100 million so'mdan oshishi bo'yicha ogohlantirish. Hujjat kesimida qoldiq tasdiqlanmasa yoshni to'qimang. Dashboard ogohlantirishlari birinchi bosqichda, Telegram keyin.
- Internetdan telefon/kompyuterda kirish maqsad. Server va domen yo'q; avval bepul sinov tanlandi. Xizmat, domen yoki pullik budjet belgilanmagan, joylashtirish bajarilmadi. Birinchi versiya va keyingi hosting/Telegram bosqichlari hujjatda.
- GitHub'dagi yangi `work` commitlari fast-forward bilan olindi va saqlandi. Faqat shu texnik topshiriq hamda handoff qaydi qo'shildi. Mavjud to'liq testlar: 328 passed, bitta Starlette deprecation ogohlantirishi; bu yangi dashboard amalga oshirilgan yoki sinalganini anglatmaydi.

## Codex → Claude: firma egasi dashboardi amalga oshirildi (2026-11-02)
- Foydalanuvchi kelishilgan dashboardni yakunlashni buyurdi. `business_owner` alohida rol, `/business` sahifasi va `/api/business/*` API qo'shildi. Hozirgi `owner` qiymati administrator bo'lib qoldi; UI yorlig'i “Administrator”. SQLite CHECK migratsiyasi mavjud ID/parol/sessiya/FKlarni saqlaydi va atomar bajariladi. Eski administratorni firma egasiga avtomatik aylantirmang.
- `auth.firm_ctx` firma egasiga eski ish/sverka/import/chat APIlarini bermaydi. Biznes API, firma ro'yxati, PDF va AI faqat biriktirilgan firmalarga tegishli; public firma javobidan credential/ulanish maydonlari chiqarildi. Javobdan oldin huquq qayta tekshiriladi, biznes JSON/PDF `no-store`. Telegram konteksti business_owner uchun hozir bloklangan — keyingi bosqich.
- Buxgalter biriktirilgan firmaning rekvizit, ulanish, hisob qoidalari va qarz mezonlarini boshqaradi. Umumiy AI, foydalanuvchilar, firma yaratish/biriktirish administratorda. Mavjud alohida 1C write-access va Didox qoralama yaratishni yoqish darvozalari administratorga tegishli; 1C WRITES_ENABLED o'zgarmadi.
- `services_business.Reader` STIR/GUID, so'rovdagi va javobdagi tashkilotni tekshiradi; Balance va RecordType, hujjat/nomlarni ketma-ket GET bilan o'qiydi. 500 qatorlik pagination, 20000 chegarasi, takroriy yozuv va davr tekshiruvlari. Standart P&L: 90 savdo, 93/95 boshqa daromad, 91 tannarx, 94/96/98 xarajat; 99 bilan yopish chiqarilgan. Jonli releasdagi rekvizitlar hali lokal haqiqiy 1C bilan tasdiqlanishi kerak.
- Qarzlar ochiq Balance qoldig'idan: 30 kun yoki kontragentga 100 milliondan katta qoldiq, avans va tomonlar alohida. Yosh faqat hujjat subkontosi bo'lsa; noma'lum qoldiq ochiq ko'rsatiladi. Teskari qoldiq yashirilmaydi yoki boshqa qarzga net qilinmaydi — tasniflanmagan debet/kredit. Real firmada 1C sozlanmagan bo'lsa demo raqamiga o'tish bloklangan; noma'lum natija nol emas.
- `templates/business.html`, `static/business.js/css`: umumiy natija, qarz/ombor qidiruvi, sahifalangan hujjat va tovar tafsiloti, alohida Gemini chat, PDF. Boshlang'ich davr Asia/Tashkent joriy oy; ochiq panel 5 daqiqada va tugmadan yangilanadi. Firma/davr almashganda eski so'rov, chat va PDF javobi yangi ko'rinishga qo'llanmaydi.
- `services_business_ai` faqat uch o'qish vositasini beradi; firma IDsi model argumenti emas, huquq vositalar orasida ham qayta tekshiriladi. Suhbat firma/davr/ulanishga bog'langan; yangi JSON tarixdan keyin uzatiladi. Gemini kaliti/xizmati yo'q bo'lsa mahalliy izoh deb yoziladi. Jonli Google bu ishda chaqirilmadi, soxta SDK bilan vosita sikli va fallback sinaldi.
- `services_business_pdf`: reportlab, kiritilgan litsenziyali DejaVu Unicode shrift, diskka yozilmagan PDF. Har ro'yxatda 2000 qator chegarasi va ochiq qisqartirish xabari bor. `start.command` reportlabni mavjud venvni saqlab o'rnatadi. Docker HOST/stdlib healthcheck moslandi. [Ishga tushirish va manbalar](docs/business-dashboard-run.md), README va talablar holati yangilandi.
- To'liq testlar: 348 passed, bitta mavjud Starlette deprecation ogohlantirishi. Migratsiya, ikki egasi/uch firma, P&L/yopish, hujjat qoldig'i/yosh/chegara, noma'lum natija, Gemini/read-only/revocation, PDF va haqiqiy Chromium desktop/telefon sinovlari bor. Skrinshotlar faqat sun'iy ma'lumotlarda. Haqiqiy 1C credentiallari bu bulutda yo'q, real firma hisoboti bilan solishtirish bajarilmadi.
- Bepul internet xizmatiga akkaunt/domen tanlanmagan, ommaviy sayt joylashtirilmadi. Telegram xabarlari kelishuvga ko'ra keyingi bosqich. Lokal foydalanuvchi `work`ni yangilab, `start.command`ni qayta ishga tushiradi; admin egasi akkauntini yaratib kerakli firmalarni biriktiradi.
- Yakuniy qayta tekshiruv: 348 passed. JS va start.command sintaksisi ham o'tdi. Docker oddiy yuklashida builder DNS xatosi berdi; muhitning mavjud pip tarmog'i orqali ochiq wheel paketlari vaqtinchalik /tmp ga olindi, tarmoqsiz yig'ish varianti bilan image tayyorlandi. Tarmoqsiz, yangi sun'iy DBli konteynerda login → yangi business_owner → dashboard → Unicode PDF → eski API 403 oqimi muvaffaqiyatli o'tdi; konteyner to'xtatib o'chirildi. TLS ishonchi o'zgartirilmadi, real data yoki credential image'ga kiritilmadi.

## Claude: dashboard tekshiruvi (2026-11-03)
- Jonli 1C'da tekshirildi: bank, debitorlik, oktyabr foyda-zarari, PDF, hujjatlar ishlaydi. 60.10 dagi debet qoldiqlar (ortiqcha to'lov) kreditorlikdan alohida — bosh sahifadagi sof qiymatdan farqi shu.
- Biznes chati endi 429/404/5xx da barcha zaxira modellarga o'tadi. Kontragent/nomenklatura/schyot/birlik nomlari firma bo'yicha 30 daqiqa keshlanadi.
- Real firmada hisob-kitoblar hujjat kesimida emas, shuning uchun qarz yoshi `RecordsWithExtDimensions` (faqat GET, tashkilot filtri) bo'yicha oxirgi 180 kun provodkalaridan FIFO bilan hisoblanadi (`fifo_split`); undan eski qism `age_beyond` (180+). Bu taxmin, UI/PDF'da “FIFO” deb belgilanadi.
