"""AI Buxgalter Copilot: Google Gemini (bepul, gemini-3.8-flash) + Function Calling.

Gemini quyidagi vositalarni (tools) chaqira oladi:
  analyze_and_deduplicate_vipiska(), check_didox_vs_1c(), learn_from_1c_history(stir),
  post_confirmed_to_1c(doc_ids), cash_flow_summary(),
  1C dan o'qish (services_insight): firm_overview(), account_balance(account), stock_overview(source),
  counterparty_card(query), list_documents(doc_type, …), onec_read(entity, …)

🔒 1C ga yozish dastur darajasida yopiq (services_1c.WRITES_ENABLED) — agent faqat o'qiydi.

MUHIM: post_confirmed_to_1c hech narsani 1C ga yozmaydi — u faqat chatda tasdiqlash kartasini chiqaradi.
Haqiqiy yozuv faqat buxgalter karta ichidagi tugmani bosganda (/api/post) amalga oshadi.

Gemini kaliti bo'lmasa (yoki limit tugasa) — "Demo AI" (kalit so'zlarga asoslangan) xuddi shu vositalar bilan ishlaydi.
OpenAI / Claude ixtiyoriy zaxira sifatida: vositalar mahalliy ishga tushiriladi, LLM esa javobni shakllantiradi.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

import config
import core
import services_1c
import services_insight as insight
from services_1c import E_BANK_IN, E_BANK_OUT, E_PURCHASE

log = logging.getLogger("buxgalter.ai")

SYSTEM_PROMPT = """Sen — "BUXGALTER AI", O'zbekistondagi korxonalar uchun tajribali bosh buxgalter va 1C:Бухгалтерия
для Узбекистана 3.0 bo'yicha mutaxassis yordamchisan. Faqat O'ZBEK tilida (lotin yozuvida), qisqa va aniq javob ber.

Qoidalar:
- Raqamlarni o'zing to'qib chiqarma — har doim vositalardan (tools) olingan ma'lumotga tayan.
- 1C va Notion doim o'zgarib turadi: har bir savolda ma'lumotni vositadan QAYTA ol, oldingi javoblardagi
  raqamlarni takrorlama (ular eskirgan bo'lishi mumkin).
- 🔒 Sen 1C ga HECH QANDAY o'zgartirish kirita olmaysan va kiritmaysan (egasi qarori) — faqat o'qiysan va tahlil
  qilasan. Kiritish so'ralsa: "1C ga yozish yopiq — 📤 1C import fayli orqali o'zingiz kiritasiz" deb tushuntir.
- 1C dan ma'lumot kerak bo'lsa (o'qish): bank qoldig'i / sverka / oylik aylanma / qarzlar umumiy — firm_overview;
  bitta schyot bo'yicha kontragentlar (40.10, 60.10, 43.10, 63.10, 64.xx, 67.10 …) — account_balance;
  ombor / sklad / tovar qoldig'i — stock_overview (source: 1c, notion yoki compare); bitta kontragent haqida —
  counterparty_card; hujjatlar ro'yxati (bank, xarid/kirim faktura, sotuv/chiqim faktura, kassa) — list_documents; boshqa
  narsa (masalan asosiy vositalar, xodimlar, shartnomalar, registrlar) — onec_read (OData, faqat o'qish).
- Vipiska yoki dublikat haqida so'ralsa — analyze_and_deduplicate_vipiska chaqir.
- Didox / EHF / hisob-faktura haqida — check_didox_vs_1c chaqir.
- Kontragent schyoti / tarix haqida — learn_from_1c_history(stir) chaqir.
- 1C ga kiritish so'ralsa — post_confirmed_to_1c chaqir. U faqat TASDIQLASH KARTASINI chiqaradi; buxgalterga
  karta ichidagi tugmani bosishni ayt. Dublikat va farqli qatorlar hech qachon kiritilmaydi.
- Pul oqimi / tahlil — cash_flow_summary chaqir.
- Hujjatlar 1C ga faqat qoralama (Posted=false) sifatida tushadi — buni eslatib qo'y.
- Hisoblar rejasi (BHMS 21): 40.10 xaridorlar (qarz) / 63.10 xaridorlardan avans; 60.10 yetkazib beruvchilar /
  43.10 berilgan avans; 64.10 budjet (QQS, JShDS — kod 46); 65.20 ijtimoiy soliq (kod 36) va pensiya badali/INPS
  (kod 101); 67.10 ish haqi; 66.10 dividend; 68.10 kredit/qarz; 96.10 kredit foizi; 55.20 korporativ karta (KPK);
  57.10 terminal/inkassatsiya; 94.20 ma'muriy xarajatlar (bank komissiyasi); 44.10 kiruvchi EHF dagi QQS.
  To'lovni 1C o'tkazishda avval qarz (40.10/60.10) yopiladi, ortgani avansga (63.10/43.10) — avtomatik.
  Summalarni "45 000 000 so'm" ko'rinishida yoz.
- Javobni Markdown'da yoz: **qalin**, ro'yxatlar "- " bilan. Jadval ishlatma."""

TOOL_DECLARATIONS = [
    {"name": "analyze_and_deduplicate_vipiska",
     "description": "Yuklangan bank vipiskasini 1C bilan solishtiradi: har bir qator uchun dublikat (1C da bor), "
                    "yangi yoki farq bor holatini va 1C tarixidan o'rganilgan schyotni qaytaradi. Vipiska "
                    "yuklanmagan bo'lsa namunaviy test vipiskasi yuklanadi."},
    {"name": "check_didox_vs_1c",
     "description": "Didox'dagi kiruvchi elektron hisob-fakturalarni (EHF) 1C dagi ПоступлениеТоваровУслуг "
                    "hujjatlari bilan solishtiradi."},
    {"name": "learn_from_1c_history",
     "description": "Kontragentning STIR (INN) bo'yicha 1C dagi oxirgi 5 ta o'tkazilgan hujjatidan ВидОперации, "
                    "hisob schyoti, shartnoma va ДДС moddasini o'rganadi.",
     "parameters": {"type": "OBJECT", "properties": {
         "stir": {"type": "STRING", "description": "Kontragent STIR (9 xonali INN)"}}, "required": ["stir"]}},
    {"name": "post_confirmed_to_1c",
     "description": "Tanlangan yangi (dublikat bo'lmagan) qatorlarni 1C ga qoralama sifatida kiritish uchun "
                    "buxgalterga TASDIQLASH KARTASINI ko'rsatadi. doc_ids bo'sh bo'lsa — barcha tayyor qatorlar.",
     "parameters": {"type": "OBJECT", "properties": {
         "doc_ids": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Qator ID lari (ixtiyoriy)"},
         "mode": {"type": "STRING", "enum": ["vipiska", "didox"], "description": "vipiska yoki didox"}}}},
    {"name": "cash_flow_summary",
     "description": "Vipiska bo'yicha kunlik pul oqimi: kirim, chiqim, sof oqim va eng yirik kontragentlar."},
    {"name": "firm_overview",
     "description": "1C dan firma holati: 51.10 bank qoldig'i (1C va vipiska), sverka, shu oy kirim/chiqim, haftalik "
                    "aylanma, debitor/kreditor (40.10/60.10/43.10/63.10) jami va eng kattalari, bugungi ishlar."},
    {"name": "account_balance",
     "description": "Bitta schyot qoldig'i kontragentlar (subkonto) bo'yicha, 1C provodkalaridan.",
     "parameters": {"type": "OBJECT", "properties": {
         "account": {"type": "STRING", "description": "Schyot kodi, masalan 60.10, 40.10, 4310, 64.10"},
         "search": {"type": "STRING", "description": "Kontragent nomi yoki STIR bo'yicha qidiruv (ixtiyoriy)"}},
         "required": ["account"]}},
    {"name": "stock_overview",
     "description": "Ombor (sklad) qoldiqlari: 1c — 1C provodkalari (29.10, 10.xx, 28.10); notion — Notion sklad "
                    "bazasi (band/zaxira, izohlar bilan); compare — 1C va Notion farqlari.",
     "parameters": {"type": "OBJECT", "properties": {
         "source": {"type": "STRING", "enum": ["1c", "notion", "compare"]},
         "search": {"type": "STRING", "description": "Tovar nomi bo'yicha qidiruv (ixtiyoriy)"}}}},
    {"name": "counterparty_card",
     "description": "Kontragent kartasi: 1C dagi nomi, STIR, hisob-kitob schyotlaridagi qoldig'i (qarz/avans), "
                    "oxirgi 1 yildagi bank, xarid va sotuv hujjatlari.",
     "parameters": {"type": "OBJECT", "properties": {
         "query": {"type": "STRING", "description": "STIR (9 xonali) yoki nomining bir qismi"}}, "required": ["query"]}},
    {"name": "list_documents",
     "description": "1C hujjatlari ro'yxati (yangi → eski): bank kirim/chiqim, xarid (kirim — ПоступлениеТоваровУслуг), "
                    "sotuv (chiqim — РеализацияТоваровУслуг), chiqim_faktura (berilgan hisob-fakturalar), "
                    "kirim_faktura (olingan hisob-fakturalar), kassa.",
     "parameters": {"type": "OBJECT", "properties": {
         "doc_type": {"type": "STRING", "enum": list(insight.DOC_TYPES)},
         "date_from": {"type": "STRING", "description": "YYYY-MM-DD (ixtiyoriy)"},
         "date_to": {"type": "STRING", "description": "YYYY-MM-DD (ixtiyoriy)"},
         "stir": {"type": "STRING", "description": "Kontragent STIR yoki nomi (ixtiyoriy)"},
         "top": {"type": "INTEGER", "description": "Nechta (1–50, standart 20)"}}, "required": ["doc_type"]}},
    {"name": "onec_read",
     "description": "1C dan istalgan obyektni o'qish (OData, faqat GET). Boshqa vositalar yetmaganda ishlat. "
                    "Masalan: Catalog_ОсновныеСредства, Catalog_Сотрудники, Catalog_ДоговорыКонтрагентов, "
                    "Document_СчетНаОплатуПокупателю, InformationRegister_КурсыВалют.",
     "parameters": {"type": "OBJECT", "properties": {
         "entity": {"type": "STRING", "description": "OData obyekt nomi, masalan Catalog_Номенклатура"},
         "filter": {"type": "STRING", "description": "OData $filter, masalan: DeletionMark eq false and "
                                                     "substringof('кабель', Description)"},
         "select": {"type": "STRING", "description": "Maydonlar vergul bilan (ixtiyoriy)"},
         "orderby": {"type": "STRING", "description": "Masalan: Date desc (ixtiyoriy)"},
         "top": {"type": "INTEGER", "description": "1–50, standart 20"}}, "required": ["entity"]}},
]


def money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ") + " so'm"


def _brief(r: dict) -> dict:
    return {"id": r["id"], "sana": r["date"], "raqam": r["number"],
            "turi": "Kirim" if r["direction"] == "kirim" else "Chiqim", "summa": r["amount"],
            "kontragent": r["counterparty_name"], "stir": r["counterparty_inn"], "holat": r["status"],
            "sabab": r.get("status_reason"), "ehf_holati": r.get("ehf_status_label"), "sverka": r.get("reconcile"), "farqlar": r.get("diffs"), "tekshiruv_ogohlantirishlari": r.get('comparison_warnings'), "schyot": r.get("account"),
            "schyot_manbasi": r.get("account_source"), "to'siqlar": r.get("blockers")}


def compact_result(value):
    """JSON tuzilishini buzmasdan katta natijalarni chegaralash."""
    if isinstance(value, dict):
        return {k: compact_result(v) for k, v in value.items()}
    if isinstance(value, list):
        rows = [compact_result(v) for v in value[:50]]
        if len(value) > 50:
            rows.append({"qisqartirilgan": True, "jami_qator": len(value), "qaytarilgan": 50})
        return rows
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "… [qisqartirilgan]"
    return value


class ToolContext:
    def __init__(self, session: core.Session, mode: str):
        self.session = session
        self.mode = mode
        self.cards: list[dict] = []
        self.refresh: set[str] = set()
        self.used: list[str] = []
        self.notes: list[str] = []

    async def run(self, name: str, args: dict) -> dict:
        self.used.append(name)
        fn = getattr(self, f"t_{name}", None)
        if not fn:
            return {"xato": f"Noma'lum vosita: {name}"}
        try:
            return compact_result(await fn(**(args or {})))
        except ValueError as e:  # noto'g'ri argument (schyot, obyekt nomi …) — agentga tushunarli xato
            return {"xato": str(e)}
        except Exception as e:  # noqa: BLE001
            log.exception("tool %s failed", name)
            return {"xato": str(e)}

    async def t_analyze_and_deduplicate_vipiska(self) -> dict:
        note = None
        if not self.session.statement:
            if core.onec_for(self.session).mode != "demo":
                return {"xato": "Vipiska yuklanmagan. 📥 Bank vipiskasini yuklang — test (namuna) fayl faqat "
                                "Demo firmada ishlatiladi."}
            snap = await core.load_sample(self.session)
            note = "Vipiska yuklanmagan edi — namunaviy test vipiskasi (sample_bank_vipiska.xlsx) yuklandi."
        else:
            snap = await core.recheck_vipiska(self.session)
        self.refresh.add("vipiska")
        return {"izoh": note, "manba": snap["source"], "kpi": snap["kpis"], "qatorlar": [_brief(r) for r in snap["rows"]]}

    async def t_check_didox_vs_1c(self) -> dict:
        snap = await core.load_didox(self.session)
        self.refresh.add("didox")
        return {"manba": snap["source"], "kpi": snap["kpis"], "sverka": snap.get("ehf_summary"), "qatorlar": [_brief(r) for r in snap["rows"]]}

    async def t_learn_from_1c_history(self, stir: str = "") -> dict:
        onec = await core.readable_onec(self.session)  # OData ishlamasa — 1C eksport fayllaridan
        kontr = await onec.find_counterparty(str(stir))
        if not kontr:
            return {"xato": f"STIR {stir} bo'yicha kontragent 1C da topilmadi"}
        out = {"kontragent": kontr["Description"], "stir": stir}
        for label, entity in (("bank_chiqim", E_BANK_OUT), ("bank_kirim", E_BANK_IN), ("xarid_EHF", E_PURCHASE)):
            learned = await onec.learn_history(entity, kontr["Ref_Key"])
            if learned:
                out[label] = {k: learned[k] for k in ("operation", "account_code", "confidence", "samples")}
        return out

    async def t_post_confirmed_to_1c(self, doc_ids: list[str] | None = None, mode: str | None = None) -> dict:
        mode = mode if mode in ("vipiska", "didox") else self.mode
        if not services_1c.WRITES_ENABLED and core.onec_for(self.session).mode == "live":
            return {"holat": "yozish_yopiq", "izoh": services_1c.WRITE_LOCK_TEXT}
        if mode == "didox" and core.ehf_read_only(self.session):
            return {"holat": "yozish_yopiq", "izoh": "E-DOCS fakturalari faqat o'qish uchun."}
        rows = core.rows_of(self.session, mode)
        if not rows:
            return {"xato": "Avval vipiska (yoki Didox) ni yuklang va tekshiring"}
        tag = self.session.analyzed_with.get(mode) or ""
        if tag.startswith((core.FILES, core.OFFLINE)):
            ready = [r for r in rows if r.get("kind") == "bank" and r["status"] in ("new", "unchecked")
                     and not r["posted"] and not r.get("hard_blockers")]
            return {"holat": "import_fayli_orqali",
                    "izoh": "1C OData ulanmagan — dastur 1C ga o'zi yozmaydi. Yangi qatorlarni jadval pastidagi "
                            "📤 «1C import fayli» tugmasi bilan yuklab oling va 1C da: Банк и касса → Банковские "
                            "выписки → Загрузить. Schyotlarni 📋 tekshiruv ro'yxati bilan solishtiring.",
                    "import_uchun_tayyor": len(ready), "summa": round(sum(r["amount"] for r in ready), 2)}
        wanted = set(doc_ids or [])
        chosen = [r for r in rows if r["selectable"] and (not wanted or r["id"] in wanted)]
        blocked = [r for r in rows if (not wanted or r["id"] in wanted) and not r["selectable"] and not r["posted"]]
        if chosen:
            self.cards.append({
                "type": "confirm_post", "mode": mode, "firm_id": self.session.firm_id,
                "ids": [r["id"] for r in chosen], "count": len(chosen),
                "total": round(sum(r["amount"] for r in chosen), 2),
                "items": [{"number": r["number"], "counterparty": r["counterparty_name"], "amount": r["amount"],
                           "account": r["account"], "direction": r["direction"]} for r in chosen]})
        return {"holat": "buxgalter_tasdigi_kutilmoqda" if chosen else "kiritiladigan_yangi_qator_yoq",
                "tayyor_soni": len(chosen), "tayyor_summa": round(sum(r["amount"] for r in chosen), 2),
                "bloklangan": [{"raqam": r["number"], "sabab": r["status_label"]} for r in blocked]}

    # ---------------- 1C dan o'qish
    async def _onec(self):
        return await core.readable_onec(self.session)

    async def t_firm_overview(self) -> dict:
        return await insight.overview(self.session, await self._onec(), await core.onec_status(self.session.firm_id))

    async def t_account_balance(self, account: str = "", search: str = "") -> dict:
        return await insight.account(await self._onec(), account, search)

    async def t_stock_overview(self, source: str = "1c", search: str = "") -> dict:
        return await insight.stock(await self._onec(), core.firm_settings(self.session.firm_id),
                                   source if source in ("1c", "notion", "compare") else "1c", search)

    async def t_counterparty_card(self, query: str = "") -> dict:
        return await insight.counterparty_card(await self._onec(), query)

    async def t_list_documents(self, doc_type: str = "", date_from: str = "", date_to: str = "", stir: str = "",
                               top: int = 20) -> dict:
        return await insight.documents(await self._onec(), doc_type, date_from, date_to, stir, top)

    async def t_onec_read(self, entity: str = "", filter: str = "", select: str = "", orderby: str = "",
                          top: int = 20) -> dict:
        return await insight.raw_query(await self._onec(), entity, filter, select, top, orderby)

    async def t_cash_flow_summary(self) -> dict:
        if not self.session.vipiska:
            if core.onec_for(self.session).mode != "demo":
                return {"xato": "Vipiska yuklanmagan — avval bank vipiskasini yuklang."}
            await core.load_sample(self.session)
            self.refresh.add("vipiska")
        cf = core.cash_flow_of(self.session)
        self.cards.append({"type": "cash_flow", **cf})
        return cf


# ---------------------------------------------------------------- Demo AI (kalitsiz)
def route_intent(message: str) -> list[tuple[str, dict]]:
    m = message.lower()
    calls: list[tuple[str, dict]] = []
    import re
    stir = re.search(r"\b(\d{9})\b", m)
    if any(k in m for k in ("didox", "ehf", "faktura", "hisob-faktura")):
        calls.append(("check_didox_vs_1c", {}))
    if stir and any(k in m for k in ("tarix", "schyot", "o'rgan", "stir")):
        calls.append(("learn_from_1c_history", {"stir": stir.group(1)}))
    elif any(k in m for k in ("dublikat", "tekshir", "vipiska", "tarix", "schyot", "mosla")) and not calls \
            and not re.search(r"\b\d{2}[.]?\d{2}\b", m):  # "60.10 schyot" — schyot qoldig'i, vipiska emas
        calls.append(("analyze_and_deduplicate_vipiska", {}))
    if any(k in m for k in ("kirit", "1c ga", "yoz", "qoralama")):
        calls.append(("post_confirmed_to_1c", {"mode": "didox" if "didox" in m or "ehf" in m else "vipiska"}))
    if any(k in m for k in ("pul oqimi", "oqim", "tahlil", "cash")):
        calls.append(("cash_flow_summary", {}))
    acc = re.search(r"\b(\d{2})[.]?(\d{2})\b", m)
    if any(k in m for k in ("sklad", "ombor", "tovar qoldi")):
        calls.append(("stock_overview", {"source": "compare" if "notion" in m and "1c" in m else
                                          "notion" if "notion" in m else "1c"}))
    elif acc and any(k in m for k in ("schyot", "qoldi", "qarz")) and not stir:
        calls.append(("account_balance", {"account": acc.group(1) + "." + acc.group(2)}))
    elif any(k in m for k in ("bank qoldi", "51.10", "5110", "sverka", "holat", "umumiy", "qarzdor", "debitor", "kreditor")):
        calls.append(("firm_overview", {}))
    if stir and any(k in m for k in ("kontragent", "karta", "qarz", "haqida")):
        calls.append(("counterparty_card", {"query": stir.group(1)}))
    return calls


def mock_reply(calls: list[tuple[str, dict]], results: list[dict], mode: str) -> str:
    if not calls:
        return ("Salom! Men **BUXGALTER AI** — 1C va bank vipiskasi bo'yicha yordamchingizman.\n\n"
                "Men quyidagilarni bajara olaman:\n"
                "- 🏦 Vipiskani 1C bilan **dublikatga tekshirish**\n"
                "- 🧠 Oldingi 1C yozuvlaridan **schyotlarni o'rganish** (masalan: \"301234567 tarixi\")\n"
                "- ✅ Yangi qatorlarni 1C ga **qoralama** sifatida kiritish (tasdiqlash bilan)\n"
                "- 📊 Kunlik **pul oqimi** tahlili\n"
                "- 📑 Didox EHF ni 1C bilan solishtirish\n\n"
                "_Gemini API kalitini ⚙️ Sozlamalarda kiritsangiz, erkin savollarga ham javob beraman._")
    parts = []
    for (name, _args), res in zip(calls, results):
        if "xato" in res:
            parts.append(f"⚠️ {res['xato']}")
            continue
        if name == "check_didox_vs_1c" and res.get('sverka'):
            report = res['sverka']; counts = report.get('counts', {})
            parts.append("**EHF ↔ 1C nazorati**\n" + "\n".join(f"- {label}: **{counts.get(code, 0)}**" for code, label in [('matched','Mos'),('missing','1C da topilmadi'),('differences','Farq bor'),('partial','Tovarlar to‘liq tekshirilmadi'),('deleted_in_1c','EHF o‘chirilgan, 1C da bor'),('onec_only','Faqat 1C ro‘yxatida'),('unchecked','Tekshirilmagan')]) + "\n" + "\n".join('⚠️ ' + w for w in report.get('warnings', [])))
        elif name in ("analyze_and_deduplicate_vipiska", "check_didox_vs_1c"):
            k = res["kpi"]
            title = "Bank vipiskasi" if name.startswith("analyze") else "Didox EHF"
            lines = [f"**{title} tahlili** ({res.get('manba') or '—'})"]
            if res.get("izoh"):
                lines.append(f"_{res['izoh']}_")
            lines += [f"- Jami qatorlar: **{k['total']}**",
                      f"- 🛡️ Dublikat (1C da bor, bloklandi): **{k['duplicates']}**",
                      f"- 🟡 Yangi, kiritishga tayyor: **{k['ready']}** ({money(k['ready_sum'])})",
                      f"- 🔴 Farq bor: **{k['discrepancies']}**",
                      f"- 🧠 Tarixdan aniqlangan schyotlar: **{k['learned']}/{k['learned_base']} ({k['learned_pct']}%)**"]
            for r in res["qatorlar"]:
                if r["holat"] == "discrepancy":
                    lines.append(f"- 🔴 №{r['raqam']} {r['kontragent']}: {'; '.join(r['farqlar'] or [])}")
            news = [r for r in res["qatorlar"] if r["holat"] == "new"]
            if news:
                lines.append("\n**Yangi qatorlar va schyotlar:**")
                for r in news:
                    src = "🧠 1C tarixi" if r["schyot_manbasi"] == "history" else "🤖 taxmin"
                    lines.append(f"- №{r['raqam']} {r['kontragent']} — {money(r['summa'])} → **{r['schyot']}** ({src})")
            parts.append("\n".join(lines))
        elif name == "learn_from_1c_history":
            lines = [f"**🧠 {res['kontragent']}** (STIR {res['stir']}) — 1C tarixidan o'rganildi:"]
            labels = {"bank_chiqim": "Bank chiqim", "bank_kirim": "Bank kirim", "xarid_EHF": "Xarid (EHF)"}
            found = False
            for key, label in labels.items():
                if key in res:
                    found = True
                    d = res[key]
                    lines.append(f"- {label}: **{d['account_code']}**, ВидОперации `{d['operation']}` "
                                 f"(ishonch {d['confidence']}, oxirgi {len(d['samples'])} hujjat)")
            if not found:
                lines.append("- Bu kontragent bo'yicha o'tkazilgan hujjat topilmadi")
            parts.append("\n".join(lines))
        elif name == "post_confirmed_to_1c" and res.get("holat") == "yozish_yopiq":
            parts.append(res["izoh"])
        elif name in ("firm_overview", "account_balance", "stock_overview", "counterparty_card", "list_documents",
                      "onec_read"):
            parts.append(_plain_summary(name, res))
        elif name == "post_confirmed_to_1c" and res.get("holat") == "import_fayli_orqali":
            parts.append(f"📤 **{res['import_uchun_tayyor']} ta yangi qator** ({money(res['summa'])}). " + res["izoh"])
        elif name == "post_confirmed_to_1c":
            if res["tayyor_soni"]:
                parts.append(f"✅ **{res['tayyor_soni']} ta yangi hujjat** ({money(res['tayyor_summa'])}) 1C ga "
                             f"**qoralama** sifatida kiritishga tayyor. Quyidagi kartada tasdiqlang 👇")
            else:
                parts.append("Kiritish uchun tayyor yangi qator yo'q.")
            if res["bloklangan"]:
                parts.append("🛡️ Bloklangan: " + ", ".join(f"№{b['raqam']}" for b in res["bloklangan"]))
        elif name == "cash_flow_summary":
            lines = ["**📊 Kunlik pul oqimi**"]
            for d in res["days"]:
                lines.append(f"- {d['date']}: kirim {money(d['kirim'])}, chiqim {money(d['chiqim'])}, "
                             f"sof **{money(d['net'])}**")
            lines.append(f"\nJami kirim **{money(res['total_kirim'])}**, chiqim **{money(res['total_chiqim'])}**, "
                         f"sof oqim **{money(res['net'])}**.")
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _plain_summary(name: str, res: dict) -> str:
    """Demo AI uchun: o'qish vositalari natijasining qisqa ko'rinishi."""
    if name == "firm_overview":
        b = res["bank_51_10"]
        lines = [f"**🏦 51.10:** {money(b['qiymat']) if b['qiymat'] is not None else '—'} ({b['manba']})"]
        m = res.get("shu_oy") or {}
        if m:
            lines.append(f"- Shu oy: kirim {money(m.get('kirim', 0))}, chiqim {money(m.get('chiqim', 0))}")
        for k, v in (res.get("qarzlar") or {}).items():
            lines.append(f"- {k}: {money(v['jami'] or 0)}")
        return "\n".join(lines)
    if name == "account_balance":
        lines = [f"**{res['schyot']} {res['nomi']}** — Dt {money(res['jami_debet'])}, Kt {money(res['jami_kredit'])}"]
        lines += [f"- {r['name']}: {money(r['amount'])}" for r in res["qatorlar"][:10]]
        return "\n".join(lines)
    if name == "stock_overview":
        if "holatlar" in res:
            return f"**⚖️ Sklad 1C ↔ Notion:** {res['holatlar']}"
        lines = [f"**📦 Sklad ({res['manba']}):** {res.get('pozitsiyalar')} pozitsiya, {money(res.get('jami_summa') or 0)}"]
        lines += [f"- {t['name']}: {t['qty']} {t['unit']}" for t in res["tovarlar"][:10]]
        return "\n".join(lines)
    if name == "counterparty_card":
        lines = [f"**{res['kontragent']}** (STIR {res.get('stir') or '—'})"]
        q = res.get("qoldiqlar")
        lines += [f"- {c} {v['nomi']}: {money(v['qoldiq'])}" for c, v in q.items()] if isinstance(q, dict) else [f"- {q}"]
        return "\n".join(lines)
    if name == "list_documents":
        lines = [f"**{res['tur']}:** {res['soni']} ta, {money(res['jami_summa'])}"]
        lines += [f"- {d['sana']} №{d['raqam']} {d['kontragent'] or ''} {money(d['summa'] or 0)}" for d in res["hujjatlar"][:10]]
        return "\n".join(lines)
    return f"**{res.get('obyekt')}:** {res.get('soni')} ta yozuv"


# ---------------------------------------------------------------- dvigatellar
# Sozlamalardagi model o'chirilsa (Google eski modellarni muntazam yopadi — 404 NOT_FOUND), shu alias bilan
# qayta uriniladi: u har doim eng so'nggi Flash modelga ishora qiladi.
GEMINI_FALLBACK_MODEL = "gemini-flash-latest"


async def _gemini(ctx: ToolContext, message: str, settings: dict) -> str:
    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=settings["gemini_api_key"])
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT + f"\nJoriy rejim: {ctx.mode}.",
        tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**d) for d in TOOL_DECLARATIONS])],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    contents = [types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part.from_text(text=m["text"])])
                for m in ctx.session.chat[-8:]]
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=message)]))
    model = settings["gemini_model"]

    async def generate():
        nonlocal model
        try:
            return await client.aio.models.generate_content(model=model, contents=contents, config=cfg)
        except errors.ClientError as e:
            if e.code != 404 or model == GEMINI_FALLBACK_MODEL:
                raise
            log.warning("Gemini modeli %s topilmadi — %s ishlatiladi", model, GEMINI_FALLBACK_MODEL)
            ctx.notes.append(f"ℹ️ `{model}` modeli Google tomonidan yopilgan — `{GEMINI_FALLBACK_MODEL}` ishlatildi. "
                             "⚙️ Sozlamalarda modelni yangilang.")
            model = GEMINI_FALLBACK_MODEL
            return await client.aio.models.generate_content(model=model, contents=contents, config=cfg)

    for _ in range(5):
        resp = await generate()
        calls = resp.function_calls or []
        if not calls:
            return (resp.text or "").strip() or "…"
        contents.append(resp.candidates[0].content)
        parts = []
        for fc in calls:
            result = await ctx.run(fc.name, dict(fc.args or {}))
            parts.append(types.Part.from_function_response(name=fc.name, response={"result": result}))
        # Gemini 3.x "tool" rolini qabul qilmaydi — vosita natijalari "user" roli bilan qaytariladi
        contents.append(types.Content(role="user", parts=parts))
    return "Vositalar ko'p marta chaqirildi — savolni aniqroq yozing."


async def _phrase_with_llm(provider: str, settings: dict, message: str, tool_json: str) -> str:
    prompt = (f"Buxgalter savoli: {message}\n\nVositalar natijasi (JSON):\n{tool_json}\n\n"
              "Shu ma'lumotlar asosida o'zbek tilida qisqa javob yoz. Agar tasdiqlash kartasi yaratilgan bo'lsa, "
              "buxgalterga karta ichidagi tugmani bosishni ayt.")
    async with httpx.AsyncClient(timeout=60) as c:
        if provider == "openai":
            r = await c.post("https://api.openai.com/v1/chat/completions",
                             headers={"Authorization": f"Bearer {settings['openai_api_key']}"},
                             json={"model": settings["openai_model"], "temperature": 0.2, "messages": [
                                 {"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        r = await c.post("https://api.anthropic.com/v1/messages",
                         headers={"x-api-key": settings["anthropic_api_key"], "anthropic-version": "2023-06-01"},
                         json={"model": settings["anthropic_model"], "max_tokens": 1500, "system": SYSTEM_PROMPT,
                               "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"]).strip()


def engine_info(settings: dict | None = None) -> dict:
    s = settings or config.get_settings()
    p = s["ai_provider"]
    if p == "gemini" and s["gemini_api_key"]:
        return {"provider": "gemini", "ok": True, "label": f"Gemini AI (Bepul) · {s['gemini_model']}"}
    if p == "openai" and s["openai_api_key"]:
        return {"provider": "openai", "ok": True, "label": f"OpenAI · {s['openai_model']}"}
    if p == "claude" and s["anthropic_api_key"]:
        return {"provider": "claude", "ok": True, "label": f"Claude · {s['anthropic_model']}"}
    return {"provider": "mock", "ok": False, "label": "Demo AI (Gemini kaliti kiritilmagan)"}


async def chat(session: core.Session, message: str, mode: str = "vipiska") -> dict:
    message = (message or "").strip()[:4000]
    settings = config.get_settings()
    info = engine_info(settings)
    ctx = ToolContext(session, mode)
    engine, note = info["provider"], None
    reply = ""
    try:
        if engine == "gemini":
            reply = await _gemini(ctx, message, settings)
        elif engine in ("openai", "claude"):
            reply = await _provider_tools(ctx, engine, settings, message)
    except Exception as e:  # noqa: BLE001 — AI xatosi tizimni to'xtatmasligi kerak
        log.warning("AI provider %s failed: %s", engine, e)
        note = f"⚠️ {info['label']} javob bermadi ({type(e).__name__}: {str(e)[:160]}). Demo AI javobi:"
        engine, reply = "mock", ""
        ctx = ToolContext(session, mode)
    if ctx.notes and engine != "mock":
        reply = "\n".join(ctx.notes) + "\n\n" + reply
    if engine == "mock":
        calls = route_intent(message)
        results = [await ctx.run(n, a) for n, a in calls]
        reply = mock_reply(calls, results, mode)
        if note:
            reply = note + "\n\n" + reply
    session.chat += [{"role": "user", "text": message}, {"role": "model", "text": reply}]
    session.chat = session.chat[-20:]
    return {"reply": reply, "cards": ctx.cards, "refresh": sorted(ctx.refresh), "tools": ctx.used,
            "engine": engine, "engine_label": info["label"] if engine != "mock" else engine_info({**settings, "ai_provider": "mock"})["label"]}


async def chat_stream(session: core.Session, message: str, mode: str = "vipiska"):
    """Barcha provayderlar uchun bir xil yakuniy javob oqimi."""
    yield json.dumps({"type": "start", **engine_info()}) + "\n"
    result = await chat(session, message, mode)
    yield json.dumps({"type": "token", "content": result["reply"]}) + "\n"
    yield json.dumps({"type": "done", **result}) + "\n"


def _schema(value):
    if isinstance(value, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str) else _schema(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_schema(v) for v in value]
    return value


async def _provider_tools(ctx, provider, settings, message):
    """OpenAI va Claude vositalarni o'zi tanlaydi; chaqiruvlar ketma-ket."""
    messages = [{"role": "user" if m["role"] == "user" else "assistant", "content": m["text"]}
                for m in ctx.session.chat[-8:]]
    messages.append({"role": "user", "content": message})
    declarations = [{"name": d["name"], "description": d["description"],
                     "parameters": _schema(d.get("parameters", {"type": "object", "properties": {}}))}
                    for d in TOOL_DECLARATIONS]
    async with httpx.AsyncClient(timeout=60) as client:
        for _ in range(5):
            if provider == "openai":
                response = await client.post("https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": "Bearer " + settings["openai_api_key"]},
                    json={"model": settings["openai_model"], "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
                          "tools": [{"type": "function", "function": d} for d in declarations]})
                response.raise_for_status()
                answer = response.json()["choices"][0]["message"]
                calls = answer.get("tool_calls") or []
                if not calls:
                    return answer.get("content") or "Javob olinmadi."
                messages.append(answer)
                for call in calls:
                    fn = call["function"]
                    result = await ctx.run(fn["name"], json.loads(fn["arguments"]))
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)})
            else:
                response = await client.post("https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": settings["anthropic_api_key"], "anthropic-version": "2023-06-01"},
                    json={"model": settings["anthropic_model"], "max_tokens": 1500, "system": SYSTEM_PROMPT,
                          "messages": messages, "tools": [{"name": d["name"], "description": d["description"],
                          "input_schema": d["parameters"]} for d in declarations]})
                response.raise_for_status()
                blocks = response.json()["content"]
                calls = [b for b in blocks if b["type"] == "tool_use"]
                if not calls:
                    return "".join(b.get("text", "") for b in blocks) or "Javob olinmadi."
                messages.append({"role": "assistant", "content": blocks})
                results = []
                for call in calls:
                    result = await ctx.run(call["name"], call["input"])
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": json.dumps(result, ensure_ascii=False)})
                messages.append({"role": "user", "content": results})
    return "Vositalar limiti tugadi — savolni aniqroq yozing."
