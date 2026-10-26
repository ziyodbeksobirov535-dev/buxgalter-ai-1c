"""🔎 AI agent uchun 1C dan O'QISH vositalari (yozish yo'q).

Agent (Gemini) shu funksiyalar orqali firma holatini ko'radi: 51.10 va sverka, istalgan schyot qoldig'i,
sklad (1C / Notion / solishtirish), kontragent kartasi, hujjatlar ro'yxati va umumiy OData o'qish so'rovi.
Hammasi GET; 1C ga yozish services_1c.write_guard bilan dastur darajasida yopiq.

Natijalar ixcham (agent kontekstiga sig'sin): qatorlar soni cheklangan, uzun matnlar qisqartirilgan,
maxfiy obyektlar (foydalanuvchilar, parollar, sertifikatlar) o'qilmaydi.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import services_dashboard as dash
import services_notion
from services_1c import (DT, E_BANK_IN, E_BANK_OUT, E_COUNTERPARTY, E_PURCHASE, Cond, ODataError, OneCService,
                         fmt_account, norm_account, render_filter, Guid)

MAX_ROWS = 50
MAX_TEXT = 300
DOC_TYPES = {  # agent tanlaydigan hujjat turlari
    "bank_kirim": E_BANK_IN,
    "bank_chiqim": E_BANK_OUT,
    "xarid": E_PURCHASE,                                  # kirim fakturasi (tovar/xizmat)
    "sotuv": "Document_РеализацияТоваровУслуг",          # chiqim (sotuv) — tovar/xizmat realizatsiyasi
    "chiqim_faktura": "Document_СчетФактураВыданный",     # berilgan (chiqim) hisob-fakturalar
    "kirim_faktura": "Document_СчетФактураПолученный",    # olingan hisob-fakturalar
    "kassa_kirim": "Document_ПриходныйКассовыйОрдер",
    "kassa_chiqim": "Document_РасходныйКассовыйОрдер",
}
ENTITY_RE = re.compile(r"^(Catalog|Document|DocumentJournal|AccumulationRegister|InformationRegister|AccountingRegister|"
                       r"ChartOfAccounts|ChartOfCharacteristicTypes|ChartOfCalculationTypes|CalculationRegister|Constant)"
                       r"_[0-9A-Za-zА-Яа-яЁё_]+$")
# maxfiy ma'lumotlar — agent o'qimaydi
SECRET_RE = re.compile(r"Пользовател|Парол|УчетныеЗаписи|Сертификат|ЭлектроннаяПодпись|Токен|Ключ|Сеанс|Доступ", re.I)


def _short(v):
    if isinstance(v, str):
        return v if len(v) <= MAX_TEXT else v[:MAX_TEXT] + "…"
    if isinstance(v, list):
        return [_compact(x) if isinstance(x, dict) else _short(x) for x in v[:20]]
    if isinstance(v, dict):
        return _compact(v)
    return v


def _compact(rec: dict) -> dict:
    """Bo'sh, texnik va maxfiy maydonlarsiz yozuv."""
    out = {}
    for k, v in rec.items():
        if k.startswith(("odata.", "@")) or k.endswith(("@navigationLinkUrl", "_Type")) or SECRET_RE.search(k):
            continue
        if v in (None, "", [], {}) or v == "00000000-0000-0000-0000-000000000000" or v == "0001-01-01T00:00:00":
            continue
        out[k] = _short(v)
    return out


def _day(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------- umumiy holat
async def overview(session, onec: OneCService, status: dict) -> dict:
    d = await dash.build(session, onec, status)
    b = d.get("balance") or {}
    debts = d.get("debts") or {}
    return {"bank_51_10": {"qiymat": b.get("value"), "manba": b.get("source"), "1c": b.get("onec"),
                           "vipiska": b.get("statement"), "sverka": b.get("sverka")},
            "shu_oy": d.get("month"), "haftalik": d.get("weeks"),
            "qarzlar": {k: {"jami": v.get("total"), "eng_kattalari": v.get("top")} for k, v in debts.items()
                        if isinstance(v, dict)},
            "bugungi_ishlar": d.get("todo"), "ogohlantirishlar": d.get("errors") or None}


# ---------------------------------------------------------------- schyot qoldig'i
async def account(onec: OneCService, code: str, search: str = "") -> dict:
    code = fmt_account(norm_account(str(code or ""))) or ""
    if not re.fullmatch(r"\d{2}\.\d{2}(\.\d+)?", code):
        raise ValueError("Schyot kodini kiriting, masalan 60.10")
    d = await dash.account_details(onec, code)
    rows = d["rows"]
    if search:
        q = search.lower()
        rows = [r for r in rows if q in r["name"].lower() or q in (r["inn"] or "")]
    return {"schyot": code, "nomi": d["title"], "kontragentlar_soni": d["count"], "jami_debet": d["total_dt"],
            "jami_kredit": d["total_kt"], "qatorlar": rows[:MAX_ROWS],
            "izoh": "amount > 0 — debet qoldiq, < 0 — kredit qoldiq"}


# ---------------------------------------------------------------- sklad
async def stock(onec: OneCService, firm: dict, source: str = "1c", search: str = "") -> dict:
    q = (search or "").lower()
    if source == "notion":
        n = await services_notion.stock(firm)
        items = [i for g in n.get("groups", []) for i in g["items"]]
        items = [i for i in items if not q or q in i["name"].lower() or q in (i["note"] or "").lower()]
        return {"manba": "notion", "jonli": n.get("live"), "holat_vaqti": n.get("fetched_at"), "xato": n.get("error"),
                "jami_summa": n.get("total"), "pozitsiyalar": n.get("count"), "topilgan": len(items),
                "tovarlar": [{k: i[k] for k in ("name", "qty", "unit", "price", "sum", "kind", "reserved_for", "note")}
                             for i in items[:MAX_ROWS]]}
    o = await dash.stock(onec)
    if source == "compare":
        c = services_notion.compare(o, await services_notion.stock(firm))
        rows = [r for r in c["rows"] if r["state"] != "ok" or q]
        rows = [r for r in rows if not q or q in r["name"].lower() or q in r["notion_name"].lower()]
        return {"manba": "1C va Notion solishtirish", "holatlar": c["counts"], "farqli_qatorlar": [
            {k: r.get(k) for k in ("name", "notion_name", "unit", "qty_1c", "qty_notion", "diff", "state")}
            for r in rows[:MAX_ROWS]]}
    items = [dict(i, schyot=g["account"]) for g in o["groups"] for i in g["items"]]
    items = [i for i in items if not q or q in i["name"].lower() or q in (i.get("ikpu") or "")]
    return {"manba": "1c", "jami_summa": o["total"], "pozitsiyalar": o["count"], "topilgan": len(items),
            "tovarlar": [{k: i.get(k) for k in ("name", "qty", "unit", "price", "sum", "ikpu", "schyot")}
                         for i in items[:MAX_ROWS]]}


# ---------------------------------------------------------------- kontragent
async def find_counterparty(onec: OneCService, query: str) -> dict | None:
    query = (query or "").strip()
    digits = re.sub(r"\D", "", query)
    if len(digits) in (9, 14):
        found = await onec.find_counterparty(digits)
        if found:
            return found
    if query and not digits:
        rows = await onec.t.query(E_COUNTERPARTY, [Cond("DeletionMark", "eq", False),
                                                   Cond("Description", "substringof", query)], top=5)
        return rows[0] if rows else None
    return None


async def _org_scope(onec, entity):
    """Jonli hujjatlar faqat tanlangan tashkilot chegarasida o'qiladi."""
    if onec.mode != "live":
        return []
    props = await onec.entity_props(entity)
    if not props or "Организация_Key" not in props["header"]:
        raise ValueError("Obyekt uchun tashkilot chegarasi tasdiqlanmadi — o'qish to'xtatildi")
    org = await onec.organization()
    if not onec.org_inn or not org:
        raise ValueError("Firma STIRi va 1C tashkilotini aniqlash kerak")
    return [Cond("Организация_Key", "eq", Guid(org["Ref_Key"]))]


async def _docs(onec: OneCService, entity: str, conds: list[Cond], top: int) -> list[dict]:
    conds = list(conds) + await _org_scope(onec, entity)
    rows = await onec.t.query(entity, conds, top=top, orderby="Date desc")
    keys = []
    for r in rows:
        k = r.get("Контрагент_Key") or (r.get("Контрагент") if r.get("Контрагент_Type", "").endswith("Контрагенты") else None)
        if k:
            keys.append(k)
    names = await onec.describe(E_COUNTERPARTY, keys, ["ИНН"]) if keys else {}
    out = []
    for r in rows:
        k = r.get("Контрагент_Key") or r.get("Контрагент")
        n = names.get(k) or {}
        out.append({"sana": str(r.get("Date", ""))[:10], "raqam": r.get("Number"), "otkazilgan": r.get("Posted"),
                    "summa": r.get("СуммаДокумента"), "kontragent": n.get("Description"), "stir": n.get("ИНН"),
                    "kiruvchi_raqam": r.get("НомерВходящегоДокумента") or None,
                    "maqsad": _short(r.get("НазначениеПлатежа") or "") or None,
                    "izoh": _short(r.get("Комментарий") or "") or None})
    return out


async def documents(onec: OneCService, doc_type: str, date_from: str = "", date_to: str = "", stir: str = "",
                    top: int = 20) -> dict:
    entity = DOC_TYPES.get(doc_type)
    if not entity:
        raise ValueError("Hujjat turi: " + ", ".join(DOC_TYPES))
    top = max(1, min(int(top or 20), MAX_ROWS))
    conds = [Cond("DeletionMark", "eq", False)]
    d1, d2 = _day(date_from), _day(date_to)
    if d1:
        conds.append(Cond("Date", "ge", DT(datetime.combine(d1, datetime.min.time()))))
    if d2:
        conds.append(Cond("Date", "le", DT(datetime.combine(d2, datetime.max.time()).replace(microsecond=0))))
    kontr = None
    if stir:
        kontr = await find_counterparty(onec, stir)
        if not kontr:
            return {"xato": f"«{stir}» bo'yicha kontragent 1C da topilmadi"}
        conds.append(await onec.counterparty_cond(entity, kontr["Ref_Key"]))
    rows = await _docs(onec, entity, conds, top)
    return {"tur": doc_type, "hujjat": entity, "kontragent": kontr.get("Description") if kontr else None,
            "soni": len(rows), "jami_summa": round(sum(r["summa"] or 0 for r in rows), 2), "hujjatlar": rows,
            "izoh": f"Eng oxirgi {top} tagacha hujjat (yangi → eski)."}


async def counterparty_card(onec: OneCService, query: str) -> dict:
    k = await find_counterparty(onec, query)
    if not k:
        return {"xato": f"«{query}» bo'yicha kontragent 1C da topilmadi"}
    ref = k["Ref_Key"]
    balances = {}
    errors = []
    for code, title, _side in dash.SETTLEMENT_ACCOUNTS:
        try:
            total = round(sum(r["sum"] for r in await onec.account_balance(code) if r["dim"] == ref), 2)
        except ODataError as e:
            errors.append(str(e))
            continue
        if abs(total) >= 0.01:
            balances[code] = {"nomi": title, "qoldiq": total}
    recent = {}
    since = datetime.combine(date.today() - timedelta(days=365), datetime.min.time())
    for label in ("bank_kirim", "bank_chiqim", "xarid", "sotuv"):
        entity = DOC_TYPES[label]
        try:
            conds = [Cond("DeletionMark", "eq", False), Cond("Date", "ge", DT(since)),
                     await onec.counterparty_cond(entity, ref)]
            docs = await _docs(onec, entity, conds, 5)
        except ODataError as e:
            errors.append(str(e))
            continue
        if docs:
            recent[label] = docs
    return {"kontragent": k.get("Description"), "toliq_nomi": k.get("НаименованиеПолное") or None, "stir": k.get("ИНН"),
            "ochirishga_belgilangan": bool(k.get("DeletionMark")) or None,
            "qoldiqlar": balances or ("ma’lumot olinmadi" if errors else "qarz/avans yo'q"),
            "ogohlantirishlar": errors, "toliq": not errors,
            "izoh": "qoldiq > 0 — debet (bizga qarz yoki biz bergan avans), < 0 — kredit (bizning qarz yoki olingan avans)",
            "oxirgi_hujjatlar": recent}


# ---------------------------------------------------------------- umumiy o'qish
async def raw_query(onec: OneCService, entity: str, filter: str = "", select: str = "", top: int = 20,
                    orderby: str = "") -> dict:
    entity = str(entity or "").strip()
    if not ENTITY_RE.match(entity):
        raise ValueError("Obyekt nomi noto'g'ri. Masalan: Catalog_Номенклатура, Document_РеализацияТоваровУслуг, "
                         "AccumulationRegister_…, InformationRegister_…")
    if SECRET_RE.search(entity):
        raise ValueError("Bu obyekt maxfiy (foydalanuvchilar, parollar, sertifikatlar) — o'qilmaydi")
    top = max(1, min(int(top or 20), MAX_ROWS))
    reader = getattr(onec.t, "raw", None)
    if reader is None:  # demo / fayl nusxasi: filtrsiz
        rows = await onec.t.query(entity, top=top)
        note = "Demo/fayl rejimi — filtr qo'llanmadi"
    else:
        params = {"$format": "json", "$top": str(top)}
        scope = await _org_scope(onec, entity)
        scoped = render_filter(scope)
        if filter or scoped:
            params["$filter"] = f"({filter}) and ({scoped})" if filter and scoped else filter or scoped
        if select:
            params["$select"] = ",".join(s.strip() for s in select.split(",") if s.strip() and not SECRET_RE.search(s))
        if orderby:
            params["$orderby"] = orderby
        rows = await reader(entity, params)
        note = None
    return {"obyekt": entity, "soni": len(rows), "yozuvlar": [_compact(r) for r in rows], "izoh": note}
