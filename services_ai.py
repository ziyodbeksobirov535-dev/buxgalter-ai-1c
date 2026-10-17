"""AI Buxgalter Copilot: Google Gemini (bepul, gemini-3.8-flash) + Function Calling.

Gemini quyidagi vositalarni (tools) chaqira oladi:
  analyze_and_deduplicate_vipiska(), check_didox_vs_1c(), learn_from_1c_history(stir),
  post_confirmed_to_1c(doc_ids), cash_flow_summary()

MUHIM: post_confirmed_to_1c hech narsani 1C ga yozmaydi — u faqat chatda tasdiqlash kartasini chiqaradi.
Haqiqiy yozuv faqat buxgalter karta ichidagi tugmani bosganda (/api/post) amalga oshadi.

Gemini kaliti bo'lmasa (yoki limit tugasa) — "Demo AI" (kalit so'zlarga asoslangan) xuddi shu vositalar bilan ishlaydi.
OpenAI / Claude ixtiyoriy zaxira sifatida: vositalar mahalliy ishga tushiriladi, LLM esa javobni shakllantiradi.
"""
from __future__ import annotations

import json
import logging

import httpx

import config
import core
from services_1c import E_BANK_IN, E_BANK_OUT, E_PURCHASE

log = logging.getLogger("buxgalter.ai")

SYSTEM_PROMPT = """Sen — "BUXGALTER AI", O'zbekistondagi korxonalar uchun tajribali bosh buxgalter va 1C:Бухгалтерия
для Узбекистана 3.0 bo'yicha mutaxassis yordamchisan. Faqat O'ZBEK tilida (lotin yozuvida), qisqa va aniq javob ber.

Qoidalar:
- Raqamlarni o'zing to'qib chiqarma — har doim vositalardan (tools) olingan ma'lumotga tayan.
- Vipiska yoki dublikat haqida so'ralsa — analyze_and_deduplicate_vipiska chaqir.
- Didox / EHF / hisob-faktura haqida — check_didox_vs_1c chaqir.
- Kontragent schyoti / tarix haqida — learn_from_1c_history(stir) chaqir.
- 1C ga kiritish so'ralsa — post_confirmed_to_1c chaqir. U faqat TASDIQLASH KARTASINI chiqaradi; buxgalterga
  karta ichidagi tugmani bosishni ayt. Dublikat va farqli qatorlar hech qachon kiritilmaydi.
- Pul oqimi / tahlil — cash_flow_summary chaqir.
- Hujjatlar 1C ga faqat qoralama (Posted=false) sifatida tushadi — buni eslatib qo'y.
- Schyotlar: 60.10 — mol yetkazib beruvchilar, 40.10 — xaridorlar, 64.10 — byudjetga soliqlar, 94.20 — ma'muriy
  xarajatlar (bank komissiyasi). Summalarni "45 000 000 so'm" ko'rinishida yoz.
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
]


def money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ") + " so'm"


def _brief(r: dict) -> dict:
    return {"id": r["id"], "sana": r["date"], "raqam": r["number"],
            "turi": "Kirim" if r["direction"] == "kirim" else "Chiqim", "summa": r["amount"],
            "kontragent": r["counterparty_name"], "stir": r["counterparty_inn"], "holat": r["status"],
            "sabab": r.get("status_reason"), "farqlar": r.get("diffs"), "schyot": r.get("account"),
            "schyot_manbasi": r.get("account_source"), "to'siqlar": r.get("blockers")}


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
            return await fn(**(args or {}))
        except Exception as e:  # noqa: BLE001
            log.exception("tool %s failed", name)
            return {"xato": str(e)}

    async def t_analyze_and_deduplicate_vipiska(self) -> dict:
        note = None
        if not self.session.statement:
            if core.get_onec().mode == "live":
                return {"xato": "Vipiska yuklanmagan. 📥 Bank vipiskasini yuklang — jonli 1C rejimida test (namuna) "
                                "fayl avtomatik ishlatilmaydi."}
            snap = await core.load_sample(self.session)
            note = "Vipiska yuklanmagan edi — namunaviy test vipiskasi (sample_bank_vipiska.xlsx) yuklandi."
        else:
            snap = await core.recheck_vipiska(self.session)
        self.refresh.add("vipiska")
        return {"izoh": note, "manba": snap["source"], "kpi": snap["kpis"], "qatorlar": [_brief(r) for r in snap["rows"]]}

    async def t_check_didox_vs_1c(self) -> dict:
        snap = await core.load_didox(self.session)
        self.refresh.add("didox")
        return {"manba": snap["source"], "kpi": snap["kpis"], "qatorlar": [_brief(r) for r in snap["rows"]]}

    async def t_learn_from_1c_history(self, stir: str = "") -> dict:
        onec = core.get_onec()
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
        rows = core.rows_of(self.session, mode)
        if not rows:
            return {"xato": "Avval vipiska (yoki Didox) ni yuklang va tekshiring"}
        wanted = set(doc_ids or [])
        chosen = [r for r in rows if r["selectable"] and (not wanted or r["id"] in wanted)]
        blocked = [r for r in rows if (not wanted or r["id"] in wanted) and not r["selectable"] and not r["posted"]]
        if chosen:
            self.cards.append({
                "type": "confirm_post", "mode": mode, "ids": [r["id"] for r in chosen], "count": len(chosen),
                "total": round(sum(r["amount"] for r in chosen), 2),
                "items": [{"number": r["number"], "counterparty": r["counterparty_name"], "amount": r["amount"],
                           "account": r["account"], "direction": r["direction"]} for r in chosen]})
        return {"holat": "buxgalter_tasdigi_kutilmoqda" if chosen else "kiritiladigan_yangi_qator_yoq",
                "tayyor_soni": len(chosen), "tayyor_summa": round(sum(r["amount"] for r in chosen), 2),
                "bloklangan": [{"raqam": r["number"], "sabab": r["status_label"]} for r in blocked]}

    async def t_cash_flow_summary(self) -> dict:
        if not self.session.vipiska:
            if core.get_onec().mode == "live":
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
    elif any(k in m for k in ("dublikat", "tekshir", "vipiska", "tarix", "schyot", "mosla")) and not calls:
        calls.append(("analyze_and_deduplicate_vipiska", {}))
    if any(k in m for k in ("kirit", "1c ga", "yoz", "qoralama")):
        calls.append(("post_confirmed_to_1c", {"mode": "didox" if "didox" in m or "ehf" in m else "vipiska"}))
    if any(k in m for k in ("pul oqimi", "oqim", "tahlil", "cash")):
        calls.append(("cash_flow_summary", {}))
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
        if name in ("analyze_and_deduplicate_vipiska", "check_didox_vs_1c"):
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
            calls = route_intent(message)
            results = [await ctx.run(n, a) for n, a in calls]
            payload = json.dumps([{"tool": n, "result": r} for (n, _), r in zip(calls, results)],
                                 ensure_ascii=False, default=str)[:12000]
            reply = await _phrase_with_llm(engine, settings, message, payload)
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
