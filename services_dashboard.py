"""🏠 Bosh sahifa va 📦 Sklad: 1C qoldiqlari, oylik aylanma, 51.10 sverkasi, debitor/kreditor, bugungi ishlar.

Hammasi faqat o'qish: jonli 1C (AccountingRegister_Хозрасчетный/Balance, bank hujjatlari), demo baza yoki 1C eksport
fayllari. 1C dan o'qib bo'lmasa — yuklangan vipiska bo'yicha.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

import db
from services_1c import E_COUNTERPARTY, E_NOMENCLATURE, ODataError, OneCService

log = logging.getLogger("buxgalter.dashboard")

BANK = "51.10"
DEBT_ACCOUNTS = {"40.10": "debitor", "60.10": "kreditor", "43.10": "avans_berilgan", "63.10": "avans_olingan"}
STOCK_ACCOUNTS = [("29.10", "Ombordagi tovarlar"), ("10.10", "Xomashyo va materiallar"), ("10.30", "Yoqilg'i"),
                  ("10.80", "Inventar va xo'jalik jihozlari"), ("28.10", "Tayyor mahsulot")]
UNIT_CATALOG = "Catalog_КлассификаторЕдиницИзмерения"
WEEKS = 8
TOP = 5


def _end_of(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time()) + timedelta(days=1, seconds=-1)


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


async def _safe(coro, default, errors: list[str], what: str):
    try:
        return await coro
    except ODataError as e:
        errors.append(f"{what}: {e}")
        return default


# ---------------------------------------------------------------- 51.10 qoldig'i va sverka
async def bank_balance(onec: OneCService, at: datetime, own_account: str = "") -> float | None:
    rows = await onec.account_balance(BANK, at)
    if not rows:
        return None
    if own_account:  # vipiskadagi aniq hisob raqami bo'yicha (firmada bir nechta bank hisobi bo'lishi mumkin)
        acc = await onec.find_org_bank_account(own_account)
        mine = [r for r in rows if acc and r["dim"] == acc.get("Ref_Key")]
        if mine:
            return round(sum(r["sum"] for r in mine), 2)
    return round(sum(r["sum"] for r in rows), 2)


def statement_info(st, rows: list[dict]) -> dict | None:
    if not st or not st.rows:
        return None
    last = max((r.posted_date or r.date) for r in st.rows)
    pending = [r for r in rows if r.get("kind") == "bank" and r["status"] in ("new", "unchecked") and not r.get("posted")]
    return {"account": st.own_account, "opening": st.opening_balance, "closing": st.closing_balance,
            "first_date": min(r.date for r in st.rows).isoformat(), "last_date": last.isoformat(),
            "kirim": round(sum(r.amount for r in st.rows if r.direction == "kirim"), 2),
            "chiqim": round(sum(r.amount for r in st.rows if r.direction == "chiqim"), 2),
            "pending_count": len(pending),
            "pending_net": round(sum(r["amount"] if r["direction"] == "kirim" else -r["amount"] for r in pending), 2)}


def sverka(statement: dict | None, onec_at_last: float | None) -> dict | None:
    """Vipiska yakuniy qoldig'i vs 1C dagi 51.10 (o'sha kun oxirida). Farq 1C ga hali kiritilmagan to'lovlarga
    teng bo'lsa — tushuntiriladi; bo'lmasa — qo'lda tekshirish kerak."""
    if not statement or statement["closing"] is None or onec_at_last is None:
        return None
    diff = round(statement["closing"] - onec_at_last, 2)
    explained = abs(diff - statement["pending_net"]) < 0.01
    if abs(diff) < 0.01:
        state, text = "ok", "✅ Vipiska va 1C dagi 51.10 qoldig'i bir xil"
    elif explained:
        state, text = "pending", (f"🟡 Farq {statement['pending_count']} ta 1C ga hali kiritilmagan to'lov hisobiga — "
                                  "ularni kiritgach qoldiq tenglashadi")
    else:
        state, text = "mismatch", ("🔴 Farqni yangi to'lovlar tushuntirmaydi — 1C da qo'lda kiritilgan, o'chirilgan "
                                   "yoki boshqa sanaga yozilgan hujjat bo'lishi mumkin")
    return {"statement_closing": statement["closing"], "onec": onec_at_last, "diff": diff,
            "pending_net": statement["pending_net"], "state": state, "text": text, "date": statement["last_date"]}


# ---------------------------------------------------------------- aylanma (oy, haftalar)
def _weekly(days: list[dict], today: date) -> list[dict]:
    start = _monday(today) - timedelta(weeks=WEEKS - 1)
    weeks = [{"week": (start + timedelta(weeks=i)).isoformat(), "kirim": 0.0, "chiqim": 0.0} for i in range(WEEKS)]
    for d in days:
        day = date.fromisoformat(d["date"])
        i = (_monday(day) - start).days // 7
        if 0 <= i < WEEKS:
            weeks[i]["kirim"] = round(weeks[i]["kirim"] + d["kirim"], 2)
            weeks[i]["chiqim"] = round(weeks[i]["chiqim"] + d["chiqim"], 2)
    return weeks


def _month(days: list[dict], today: date) -> dict:
    start = today.replace(day=1).isoformat()
    cur = [d for d in days if d["date"] >= start]
    k, c = round(sum(d["kirim"] for d in cur), 2), round(sum(d["chiqim"] for d in cur), 2)
    # o'tgan oyning shu kunigacha bo'lgan qismi — "o'tgan oyga nisbatan" foizi uchun
    prev_start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    prev_end = prev_start + timedelta(days=today.day - 1)
    if prev_end.month != prev_start.month:  # masalan 31-mart → 29-fevral
        prev_end = today.replace(day=1) - timedelta(days=1)
    prev = [d for d in days if prev_start.isoformat() <= d["date"] <= prev_end.isoformat()]
    pk, pc = round(sum(d["kirim"] for d in prev), 2), round(sum(d["chiqim"] for d in prev), 2)
    return {"from": start, "to": today.isoformat(), "kirim": k, "chiqim": c, "net": round(k - c, 2),
            "prev": {"from": prev_start.isoformat(), "to": prev_end.isoformat(), "kirim": pk, "chiqim": pc,
                     "net": round(pk - pc, 2)} if prev else None}


DAILY_DAYS = 30


def _daily(days: list[dict], today: date) -> list[dict]:
    """Oxirgi 30 kun — har kun (harakat bo'lmagan kunlar 0 bilan), grafik uchun."""
    by = {d["date"]: d for d in days}
    out = []
    for i in range(DAILY_DAYS - 1, -1, -1):
        key = (today - timedelta(days=i)).isoformat()
        d = by.get(key) or {}
        out.append({"date": key, "kirim": d.get("kirim", 0.0), "chiqim": d.get("chiqim", 0.0)})
    return out


# Bank chiqimining operatsiya turi → xarajatlar tuzilmasidagi guruh
EXPENSE_GROUPS = {"ОплатаПоставщику": "Yetkazib beruvchilar", "ПеречислениеНалога": "Soliqlar va budjet",
                  "ПеречислениеЗаработнойПлаты": "Ish haqi", "ПеречислениеЗП": "Ish haqi",
                  "КомиссияБанка": "Bank xizmati", "ВозвратПокупателю": "Mijozlarga qaytarish",
                  "ВыдачаПодотчетнику": "Hisobdor shaxslar", "ПереводНаДругойСчет": "O'z hisoblari aro"}


def expense_mix(days: list[dict], today: date) -> list[dict]:
    """Shu oydagi bank chiqimi tuzilmasi (1C dagi ВидОперации bo'yicha)."""
    start = today.replace(day=1).isoformat()
    groups: dict[str, float] = {}
    for d in days:
        if d["date"] < start:
            continue
        for op, amount in (d.get("ops") or {}).items():
            label = EXPENSE_GROUPS.get(op, "Boshqa to'lovlar")
            groups[label] = round(groups.get(label, 0.0) + amount, 2)
    total = sum(groups.values())
    return [{"label": k, "amount": v, "pct": round(100 * v / total, 1) if total else 0}
            for k, v in sorted(groups.items(), key=lambda kv: -kv[1]) if v > 0]


def funnel(vipiska: list[dict]) -> dict | None:
    """Yuklangan vipiska qatorlari: jami → 1C da bor (dublikat) → yangi → 1C ga kiritilgan/eksport qilingan."""
    bank = [r for r in vipiska if r.get("kind") == "bank"]
    if not bank:
        return None
    dup = sum(1 for r in bank if r["status"] == "duplicate")
    new = sum(1 for r in bank if r["status"] in ("new", "unchecked", "posted") or r.get("posted"))
    done = sum(1 for r in bank if r.get("posted") or r.get("exported_at"))
    disc = sum(1 for r in bank if r["status"] == "discrepancy")
    total = len(bank)
    return {"total": total, "steps": [
        {"label": "Vipiskada", "count": total}, {"label": "1C da bor", "count": dup},
        {"label": "Yangi", "count": new}, {"label": "Kiritilgan", "count": done}],
        "discrepancy": disc, "coverage_pct": round(100 * (dup + done) / total, 1) if total else 0}


def statement_days(st) -> list[dict]:
    days: dict[str, dict] = {}
    for r in (st.rows if st else []):
        key = (r.posted_date or r.date).isoformat()
        d = days.setdefault(key, {"date": key, "kirim": 0.0, "chiqim": 0.0})
        d[r.direction] = round(d[r.direction] + r.amount, 2)
    return sorted(days.values(), key=lambda x: x["date"])


# ---------------------------------------------------------------- debitor / kreditor
async def debts(onec: OneCService) -> dict:
    out: dict = {}
    keys: list[str] = []
    for code, label in DEBT_ACCOUNTS.items():
        rows = [r for r in await onec.account_balance(code) if r["dim_type"] == E_COUNTERPARTY and abs(r["sum"]) >= 0.01]
        total = round(sum(r["sum"] for r in rows), 2)
        # debitor/berilgan avans — debet qoldiq (+); kreditor/olingan avans — kredit qoldiq (−)
        sign = 1 if code in ("40.10", "43.10") else -1
        top = sorted((r for r in rows if r["sum"] * sign > 0), key=lambda r: -abs(r["sum"]))[:TOP]
        keys += [r["dim"] for r in top]
        out[label] = {"account": code, "total": round(total * sign, 2), "count": len(rows),
                      "top": [{"key": r["dim"], "amount": round(abs(r["sum"]), 2)} for r in top]}
    names = await onec.describe(E_COUNTERPARTY, keys, ["ИНН"])
    for section in out.values():
        for t in section["top"]:
            info = names.get(t.pop("key"), {})
            t["name"], t["inn"] = info.get("Description") or "—", info.get("ИНН") or ""
    return out


# ---------------------------------------------------------------- tanlangan schyot bo'yicha to'liq ro'yxat
SETTLEMENT_ACCOUNTS = [  # Bosh sahifadagi tanlov ro'yxati: (schyot, nomi, qoldiq tomoni: dt — bizga qarz, kt — bizning qarz)
    ("40.10", "Xaridorlar qarzi (debitorlar)", "dt"), ("63.10", "Xaridorlardan olingan avanslar", "kt"),
    ("60.10", "Yetkazib beruvchilarga qarz (kreditorlar)", "kt"), ("43.10", "Yetkazib beruvchilarga berilgan avanslar", "dt"),
    ("48.90", "Turli debitorlar", "dt"), ("69.90", "Turli kreditorlar", "kt"), ("42.20", "Hisobdor shaxslar", "dt"),
    ("68.10", "Qisqa muddatli kreditlar va qarzlar", "kt"), ("66.10", "Dividendlar", "kt"),
]
MAX_ROWS = 1000


async def account_details(onec: OneCService, code: str) -> dict:
    """Bitta schyot qoldig'i subkonto (odatda kontragent) bo'yicha — hammasi, summa bo'yicha kamayish tartibida.
    amount > 0 — debet qoldiq, < 0 — kredit qoldiq."""
    rows = [r for r in await onec.account_balance(code) if abs(r["sum"]) >= 0.01]
    grouped: dict[tuple, float] = {}
    for r in rows:  # shartnoma/hujjat bo'yicha bo'lingan qatorlar — birinchi subkonto bo'yicha jamlanadi
        key = (r["dim"], r["dim_type"])
        grouped[key] = round(grouped.get(key, 0.0) + r["sum"], 2)
    items = sorted(grouped.items(), key=lambda kv: -abs(kv[1]))[:MAX_ROWS]
    names: dict[str, dict] = {}
    for dim_type in {t for (_k, t), _v in items if t}:
        keys = [k for (k, t), _v in items if t == dim_type]
        names.update(await onec.describe(dim_type, keys, ["ИНН"] if dim_type == E_COUNTERPARTY else None))
    out = [{"name": (names.get(k) or {}).get("Description") or "—", "inn": (names.get(k) or {}).get("ИНН") or "",
            "amount": v} for (k, _t), v in items]
    title, side = next(((t, sd) for c, t, sd in SETTLEMENT_ACCOUNTS if c == code), ("", "dt"))
    return {"account": code, "title": title, "side": side, "count": len(grouped),
            "total_dt": round(sum(v for v in grouped.values() if v > 0), 2),
            "total_kt": round(-sum(v for v in grouped.values() if v < 0), 2),
            "rows": out, "onec_mode": onec.mode}


# ---------------------------------------------------------------- bugungi ishlar
def todo(vipiska: list[dict], didox: list[dict], firm_id: int) -> dict:
    bank = [r for r in vipiska if r.get("kind") == "bank"]
    rules = db.list_rules(firm_id)
    return {
        "bank_ready": sum(1 for r in bank if r.get("selectable")),
        "bank_to_export": sum(1 for r in bank if r["status"] in ("new", "unchecked") and not r.get("posted")
                              and not r.get("hard_blockers") and not r.get("exported_at")),
        "bank_discrepancy": sum(1 for r in bank if r["status"] == "discrepancy"),
        "bank_blocked": sum(1 for r in bank if r["status"] in ("new", "unchecked") and r.get("blockers")),
        "bank_duplicates": sum(1 for r in bank if r["status"] == "duplicate"),
        "bank_posted": sum(1 for r in bank if r.get("posted")),
        "didox_pending": sum(1 for r in didox if r["status"] in ("new", "unchecked") and not r.get("posted")),
        "didox_discrepancy": sum(1 for r in didox if r["status"] == "discrepancy"),
        "rules_conflicts": sum(1 for r in rules if r.get("conflict") and not r.get("locked")),
        "rules_total": len(rules),
    }


# ---------------------------------------------------------------- bosh sahifa
async def build(session, onec: OneCService, status: dict, today: date | None = None) -> dict:
    today = today or date.today()
    errors: list[str] = []
    st = session.statement
    statement = statement_info(st, session.vipiska)
    connected = onec.mode in ("live", "demo", "file") and status.get("ok")

    balance_now = balance_at_last = last_entry = balance_month_start = None
    days: list[dict] = []
    debt = None
    if connected:
        own = st.own_account if st else ""
        at_last = _end_of(date.fromisoformat(statement["last_date"])) if statement else None
        prev_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        since = min(_monday(today) - timedelta(weeks=WEEKS - 1), prev_month, today - timedelta(days=DAILY_DAYS))
        month_start_eod = _end_of(today.replace(day=1) - timedelta(days=1))
        last_entry, balance_now, balance_at_last, days, debt, balance_month_start = await asyncio.gather(
            _safe(onec.last_bank_date(), None, errors, "oxirgi bank hujjati"),
            _safe(bank_balance(onec, _end_of(today), own), None, errors, "51.10 qoldig'i"),
            _safe(bank_balance(onec, at_last, own), None, errors, "sverka") if at_last else asyncio.sleep(0, None),
            _safe(onec.bank_flows(since, today), [], errors, "aylanma"),
            _safe(debts(onec), None, errors, "debitor/kreditor"),
            _safe(bank_balance(onec, month_start_eod, own), None, errors, "oy boshidagi qoldiq"))
    flow_source = "1c" if days else ("statement" if st and st.rows else "none")
    if not days:
        days = statement_days(st)
    hero_value = balance_now if balance_now is not None else (statement or {}).get("closing")
    onec_info = None
    if balance_now is not None:
        # 1C dagi 51.10 — bugungi holat emas: 1C ga oxirgi kiritilgan bank hujjati kunigacha bo'lgan holat
        behind = (today - last_entry).days if last_entry else None
        newer = bool(statement and last_entry and statement["last_date"] > last_entry.isoformat())
        onec_info = {"value": balance_now, "last_entry": last_entry.isoformat() if last_entry else None,
                     "days_behind": behind, "statement_newer": newer}
    return {
        "today": today.isoformat(), "onec_mode": onec.mode, "connected": bool(connected), "status": status,
        "balance": {"value": hero_value, "source": "1c" if balance_now is not None else
                    ("statement" if hero_value is not None else "none"),
                    "onec": onec_info, "statement": statement, "sverka": sverka(statement, balance_at_last)},
        "month": _month(days, today), "weeks": _weekly(days, today), "flow_source": flow_source,
        "daily": _daily(days, today), "expense_mix": expense_mix(days, today), "funnel": funnel(session.vipiska),
        "balance_month_start": balance_month_start,
        "debts": debt, "todo": todo(session.vipiska, session.didox, session.firm_id),
        "errors": errors[:5],
    }


# ---------------------------------------------------------------- sklad
async def stock(onec: OneCService) -> dict:
    groups, keys = [], []
    for code, title in STOCK_ACCOUNTS:
        rows = [r for r in await onec.account_balance(code) if r["dim_type"] == E_NOMENCLATURE
                and (abs(r["sum"]) >= 0.01 or abs(r["qty"]) >= 0.001)]
        items: dict[str, dict] = {}
        for r in rows:  # partiya / ombor bo'yicha bo'lingan qatorlar — nomenklatura bo'yicha jamlanadi
            it = items.setdefault(r["dim"], {"key": r["dim"], "qty": 0.0, "sum": 0.0})
            it["qty"] = round(it["qty"] + r["qty"], 3)
            it["sum"] = round(it["sum"] + r["sum"], 2)
        if items:
            groups.append({"account": code, "title": title, "items": sorted(items.values(), key=lambda x: -x["sum"]),
                           "total": round(sum(i["sum"] for i in items.values()), 2)})
            keys += list(items)
    info = await onec.describe(E_NOMENCLATURE, keys, ["КодИзКлассификатора", "КодИКПУ", "ЕдиницаИзмерения_Key",
                                                      "ЕдиницаИзмерения"])
    units = await onec.describe(UNIT_CATALOG, [i.get("ЕдиницаИзмерения_Key") for i in info.values()
                                               if i.get("ЕдиницаИзмерения_Key")])
    for g in groups:
        for it in g["items"]:
            d = info.get(it.pop("key"), {})
            unit_key = d.get("ЕдиницаИзмерения_Key")
            it.update(name=d.get("Description") or "—", ikpu=d.get("КодИзКлассификатора") or d.get("КодИКПУ") or "",
                      unit=(units.get(unit_key) or {}).get("Description") or d.get("ЕдиницаИзмерения") or "",
                      price=round(it["sum"] / it["qty"], 2) if it["qty"] else None)
    return {"groups": groups, "total": round(sum(g["total"] for g in groups), 2),
            "count": sum(len(g["items"]) for g in groups), "onec_mode": onec.mode}

