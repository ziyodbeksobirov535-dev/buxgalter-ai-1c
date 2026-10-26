"""Umumiy ish oqimlari: Web App, Telegram bot va AI vositalari (tools) shu funksiyalardan foydalanadi."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import config
import db
import services_1c
import services_files1c as files1c
from services_1c import ACCOUNT_CHOICES, Cond, ODataError, OfflineOData, OneCService, build_service
from services_didox import DidoxError, analyze_invoices, build_purchase_payload, fetch_invoices, recheck_purchase
from services_rules import RuleSet
from services_vipiska import (BankRow, Statement, StatementError, analyze_statement, build_bank_payload, cash_flow, finalize_row, kpis,
                              parse_statement, post_rows, recheck_bank, set_account)

log = logging.getLogger("buxgalter.core")
SESSIONS_DIR = config.DATA_DIR / "sessions"


@dataclass
class Session:
    sid: str
    firm_id: int
    statement: Statement | None = None
    vipiska: list[dict] = field(default_factory=list)
    didox: list[dict] = field(default_factory=list)
    didox_source: str = ""
    chat: list[dict] = field(default_factory=list)
    # Telegram tugmalari uchun: token -> aniq qatorlar to'plami (tugma faqat o'zi ko'rsatgan qatorlarni yozadi)
    batches: dict = field(default_factory=dict)
    analyzed_with: dict = field(default_factory=dict)  # rejim -> qaysi 1C sozlamalari bilan tahlil qilingan
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


SESSIONS: dict[str, Session] = {}
_services: dict[int, tuple[tuple, OneCService]] = {}  # firma -> (sozlamalar izi, 1C servis)
_file_services: dict[int, tuple[tuple, OneCService]] = {}  # firma -> (eksport izi, fayldan tuzilgan 1C nusxasi)
# Barcha sessiyalar (Web App + Telegram) uchun bitta qulf: ikki joydan bir vaqtda bir xil qatorni yozishda
# dublikat tekshiruvi bilan yozish orasidagi "oyna"da ikkalasi ham o'tib ketmasligi uchun.
_post_lock = asyncio.Lock()
_files_lock = asyncio.Lock()


# ---------------------------------------------------------------- sessiyani diskda saqlash
def _session_file(sid: str):
    return SESSIONS_DIR / f"{sid}.json"


def save_session(session: Session) -> None:
    """Jadval holati server qayta ishga tushganda yo'qolmasligi uchun (parol/kalitlar yozilmaydi)."""
    st = session.statement
    data = {
        "statement": None if st is None else {
            "rows": [{**asdict(r), "date": r.date.isoformat(),
                      "posted_date": r.posted_date.isoformat() if r.posted_date else None} for r in st.rows],
            "own_account": st.own_account,
            "source": st.source, "warnings": st.warnings, "is_sample": st.is_sample, "own_inn": st.own_inn,
            "own_name": st.own_name, "opening_balance": st.opening_balance},
        "vipiska": session.vipiska, "didox": session.didox, "didox_source": session.didox_source,
        "analyzed_with": session.analyzed_with,
        "chat": session.chat[-20:],
    }
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _session_file(session.sid).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), "utf-8")
        tmp.replace(_session_file(session.sid))
    except OSError as e:
        log.warning("Sessiya saqlanmadi (%s): %s", session.sid, e)
    try:
        db.save_session_store(session.sid, session.firm_id, data)
    except Exception as e:
        log.debug("DB sessiya saqlanmadi (%s): %s", session.sid, e)


def _load_session(sid: str, firm_id: int) -> Session:
    session = Session(sid, firm_id)
    path = _session_file(sid)
    data = None
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("Sessiya fayli o'qilmadi (%s): %s", sid, e)
    if data is None:
        try:
            data = db.load_session_store(sid)
        except Exception:
            data = None
    if not data:
        return session
    try:
        st = data.get("statement")
        if st:
            rows = [BankRow(**{**r, "date": date.fromisoformat(r["date"]),
                               "posted_date": date.fromisoformat(r["posted_date"]) if r.get("posted_date") else None})
                    for r in st["rows"]]
            session.statement = Statement(rows=rows, own_account=st["own_account"], source=st["source"],
                                          warnings=st["warnings"], is_sample=st.get("is_sample", False),
                                          own_inn=st.get("own_inn", ""), own_name=st.get("own_name", ""),
                                          opening_balance=st.get("opening_balance"))
        session.vipiska = data.get("vipiska", [])
        session.didox = data.get("didox", [])
        session.didox_source = data.get("didox_source", "")
        session.analyzed_with = data.get("analyzed_with", {})
        session.chat = [m for m in data.get("chat", []) if isinstance(m, dict)
                        and m.get("role") in ("user", "model") and isinstance(m.get("text"), str)][-20:]
    except (ValueError, KeyError, TypeError) as e:
        log.warning("Sessiya ma'lumoti tiklanmadi (%s): %s", sid, e)
    return session


def session_id(user_id: int, firm_id: int) -> str:
    """Web App va Telegram bir xil foydalanuvchi + firma uchun bitta umumiy sessiyani ko'radi."""
    return f"u{int(user_id)}_f{int(firm_id)}"


def get_session(sid: str, firm_id: int) -> Session:
    if sid not in SESSIONS:
        if len(SESSIONS) > 500:  # oddiy xotira himoyasi
            SESSIONS.pop(next(iter(SESSIONS)))
        SESSIONS[sid] = _load_session(sid, firm_id)
    return SESSIONS[sid]


# ---------------------------------------------------------------- 1C servis
def firm_settings(firm_id: int) -> dict:
    s = db.firm_settings(firm_id)
    if not s or not s["active"]:
        raise ODataError("Firma topilmadi yoki o'chirilgan")
    return s


def _sig(s: dict) -> tuple:
    return (s["onec_demo_mode"], s["onec_base_url"], s["onec_user"], s["onec_password"], s["onec_org_inn"])


def file_onec(firm_id: int) -> OneCService | None:
    """1C eksport fayllaridan tuzilgan nusxa (yuklanmagan bo'lsa — None)."""
    store = files1c.load_store(firm_id)
    if not store:
        _file_services.pop(firm_id, None)
        return None
    s = firm_settings(firm_id)
    sig = (store.get("stamp"), s["name"], s["inn"], s["bank_accounts"])
    cached = _file_services.get(firm_id)
    if cached is None or cached[0] != sig:
        _file_services[firm_id] = (sig, files1c.file_service(store, s))
    return _file_services[firm_id][1]


def files_tag(firm_id: int) -> str:
    store = files1c.load_store(firm_id) or {}
    return FILES + onec_fingerprint(firm_id) + ":" + str(store.get("stamp", ""))


def get_onec(firm_id: int) -> OneCService:
    """Har bir firma uchun alohida 1C servis (demo bazadagi qoralamalar qayta ishga tushguncha saqlanadi).
    Ustuvorlik: 1C manzili (jonli OData; ishlamasa — eksport fayllari) > eksport fayllari > demo baza."""
    s = firm_settings(firm_id)
    if not s["onec_base_url"] and (fs := file_onec(firm_id)):
        return fs
    sig = _sig(s)
    cached = _services.get(firm_id)
    if cached is None or cached[0] != sig:
        _services[firm_id] = (sig, build_service(s))
    return _services[firm_id][1]


def onec_for(session: Session) -> OneCService:
    return get_onec(session.firm_id)


def analysis_onec(session: Session, mode: str) -> OneCService:
    """Shu jadval qaysi manba bilan tahlil qilingan bo'lsa — o'sha (jonli 1C / eksport fayllari / ko'rish rejimi)."""
    tag = session.analyzed_with.get(mode) or ""
    if tag.startswith(FILES):
        return file_onec(session.firm_id) or OneCService(OfflineOData("1C eksport fayllari o'chirilgan"))
    if tag.startswith(OFFLINE):
        return OneCService(OfflineOData("1C ga ulanmagan"))
    return onec_for(session)


async def readable_onec(session: Session) -> OneCService:
    """O'qish uchun ishlaydigan manba: jonli 1C javob bermasa (OData nashr qilinmagan va h.k.) — eksport fayllari."""
    onec = onec_for(session)
    if onec.mode != "live":
        return onec
    if (await onec.status())["ok"]:
        return onec
    return file_onec(session.firm_id) or onec


async def onec_status(firm_id: int) -> dict:
    """Holat belgisi: jonli 1C ishlamasa-yu, eksport fayllari yuklangan bo'lsa — dastur shular bilan ishlaydi."""
    onec = get_onec(firm_id)
    st = await onec.status()
    if onec.mode == "live" and not st["ok"] and (fs := file_onec(firm_id)):
        return {**(await fs.status()), "label": "1C eksport fayllari (OData ishlamadi)",
                "warning": "1C OData javob bermadi — tekshiruv yuklangan 1C eksport fayllari bo'yicha. " + st.get("error", "")}
    return st


def analysis_tag(session: Session, onec: OneCService) -> str:
    return files_tag(session.firm_id) if onec.mode == "file" else onec_fingerprint(session.firm_id)


def onec_fingerprint(firm_id: int) -> str:
    """1C ulanishining izi: firma + rejim + manzil + login + tashkilot (parol izga kirmaydi)."""
    demo, url, user, _password, org = _sig(firm_settings(firm_id))
    return hashlib.sha256(repr((firm_id, demo, url, user, org)).encode()).hexdigest()[:16]


def firm_blockers(session: Session, st: Statement) -> list[str]:
    """Ilova bazasidagi firma ma'lumotlari bo'yicha tekshiruv (1C dagi tekshiruvdan tashqari)."""
    firm = firm_settings(session.firm_id)
    accounts = set(filter(None, firm["bank_accounts"].split(",")))
    out = []
    if st.own_account and accounts and st.own_account not in accounts:
        out.append(f"🏦 Hisob raqami {st.own_account} \"{firm['name']}\" firmasining hisoblari ro'yxatida yo'q")
    if st.own_inn and firm["inn"] and st.own_inn != firm["inn"]:
        out.append(f"🏢 Vipiska STIR {st.own_inn} ga tegishli, \"{firm['name']}\" firmasi STIR {firm['inn']}")
    return out


def remember_batch(session: Session, mode: str, ids: list[str]) -> str:
    """Tugmaga bog'lanadigan bir martalik token: qaysi qatorlar (va sessiya orqali — qaysi firma)."""
    token = secrets.token_hex(4)
    session.batches[token] = {"mode": mode, "ids": list(ids)}
    while len(session.batches) > 20:
        session.batches.pop(next(iter(session.batches)))
    return token


def rules_of(session: Session) -> RuleSet:
    return RuleSet(db.list_rules(session.firm_id))


async def remember_row(session: Session, mode: str, row_id: str, by: str) -> dict:
    """Buxgalter tanlagan schyotni shu kontragent uchun qat'iy qoida qilib saqlash."""
    row = next((r for r in rows_of(session, mode) if r["id"] == row_id), None)
    if not row:
        raise KeyError(row_id)
    if not row.get("counterparty_inn"):
        raise ValueError("Bu qatorda kontragent STIR'i yo'q — kalit so'z qoidasini \"📚 Hisob qoidalari\" bo'limida qo'shing")
    learned = row.get("learned") or {}
    same = learned.get("account_code") == row["account"]
    rule = db.save_rule(session.firm_id, {
        "kind": "counterparty", "match": row["counterparty_inn"], "name": row["counterparty_name"],
        "direction": row["direction"] if row["kind"] == "bank" else "", "doc_kind": row["kind"],
        "account": row["account"], "operation": row.get("operation", ""),
        "contract_key": learned.get("contract_key") or "" if same else "",
        "cash_flow_key": learned.get("cash_flow_key") or "" if same else ""}, by, locked=True, source="correction")
    if mode == "didox":
        await load_didox(session)
    else:
        await recheck_vipiska(session)
    return rule


def rows_of(session: Session, mode: str) -> list[dict]:
    return session.didox if mode == "didox" else session.vipiska


def snapshot(session: Session, mode: str = "vipiska") -> dict:
    rows = rows_of(session, mode)
    source = session.didox_source if mode == "didox" else (session.statement.source if session.statement else "")
    choices = list(ACCOUNT_CHOICES)
    for r in [*db.list_rules(session.firm_id), *rows]:  # firmada ishlatiladigan barcha schyotlar
        if r.get("account") and r["account"] not in choices:
            choices.append(r["account"])
    analyzed = session.analyzed_with.get(mode) or ""
    offline = analyzed.startswith(OFFLINE)
    files = analyzed.startswith(FILES)
    expected = files_tag(session.firm_id) if files else onec_fingerprint(session.firm_id)
    stale = bool(rows) and not offline and analyzed not in ("", expected)
    outdated = bool(rows) and session.analyzed_with.get(f"{mode}_ver") != ANALYSIS_VERSION
    st = session.statement if mode == "vipiska" else None
    fs = firm_settings(session.firm_id)
    write = {"enabled": fs["onec_write_enabled"] and services_1c.WRITES_ENABLED, "limit": fs["onec_write_limit"],
             "create_contracts": fs["onec_create_contracts"], "locked": not services_1c.WRITES_ENABLED}
    period = None
    if st and st.rows:
        days = [r.posted_date or r.date for r in st.rows]
        period = {"from": min(r.date for r in st.rows).isoformat(), "to": max(days).isoformat()}
    return {"mode": mode, "rows": rows, "kpis": kpis(rows), "source": source, "account_choices": choices, "write": write,
            "outdated": outdated, "period": period,
            "onec_mode": onec_for(session).mode, "stale": stale, "offline": offline, "firm_id": session.firm_id,
            "files_mode": files, "files": files1c.summary(files1c.load_store(session.firm_id))["counts"] if files else None,
            "exportable": sum(1 for r in rows if files1c.exportable(r)) if mode == "vipiska" else 0,
            "statement_owner": {"inn": st.own_inn, "account": st.own_account, "name": st.own_name} if st else None,
            "is_sample": bool(mode == "vipiska" and session.statement and session.statement.is_sample),
            "warnings": session.statement.warnings if (mode == "vipiska" and session.statement) else []}


# ---------------------------------------------------------------- vipiska / didox
async def _analyze_vipiska(session: Session, previous: list[dict] | None = None) -> None:
    onec = onec_for(session)
    blockers, rules = firm_blockers(session, session.statement), rules_of(session)
    try:
        session.vipiska = await analyze_statement(session.statement, onec, previous, blockers, rules)
        session.analyzed_with["vipiska"] = analysis_tag(session, onec)
    except ODataError as e:
        if onec.mode != "live":
            raise
        # Real 1C javob bermadi (masalan, OData'da obyektlar nashr qilinmagan):
        #  • 1C eksport fayllari yuklangan bo'lsa — ular bo'yicha (dublikat, shartnoma, schyot tarixi);
        #  • bo'lmasa — ko'rish rejimi: schyotlar qoidalar/standart bo'yicha. Ikkalasida ham 1C ga hech narsa yozilmaydi.
        fs = file_onec(session.firm_id)
        if fs:
            session.vipiska = await analyze_statement(session.statement, fs, previous, blockers, rules)
            session.analyzed_with["vipiska"] = files_tag(session.firm_id)
        else:
            offline = OneCService(OfflineOData(str(e)))
            session.vipiska = await analyze_statement(session.statement, offline, previous, blockers, rules)
            session.analyzed_with["vipiska"] = OFFLINE + onec_fingerprint(session.firm_id)
    session.analyzed_with["vipiska_ver"] = ANALYSIS_VERSION
    if (session.analyzed_with.get("vipiska") or "").startswith(FILES):
        files1c.apply_balances(session.vipiska, files1c.load_store(session.firm_id))
        for row in session.vipiska:
            finalize_row(row)
    save_session(session)


OFFLINE = "offline:"
FILES = "files:"
# Tahlil qoidalari o'zgarganda oshiriladi — eski tahlil qilingan jadvalda "qayta tekshiring" ogohlantirishi chiqadi
ANALYSIS_VERSION = "2026-10-18.6"


async def load_statement(session: Session, filename: str, content: bytes, is_sample: bool = False) -> dict:
    st = parse_statement(filename, content)
    st.is_sample = is_sample
    async with session.lock:
        previous = (session.statement, session.vipiska, dict(session.analyzed_with))
        session.statement = st
        try:
            await _analyze_vipiska(session)
        except Exception:
            # Tahlil xato bilan tugasa — oldingi holat saqlanadi (yarim yuklangan vipiska qolib ketmasin)
            session.statement, session.vipiska, session.analyzed_with = previous
            raise
    return snapshot(session, "vipiska")


async def load_sample(session: Session) -> dict:
    if onec_for(session).mode != "demo":
        raise StatementError("🧪 Test (namuna) vipiska faqat Demo firmada ishlatiladi — bu firmaga o'z vipiskangizni "
                             "yuklang")
    if not config.SAMPLE_VIPISKA.exists():
        from demo_data import build_sample_xlsx
        build_sample_xlsx(config.SAMPLE_VIPISKA)
    return await load_statement(session, config.SAMPLE_VIPISKA.name, config.SAMPLE_VIPISKA.read_bytes(),
                                is_sample=True)


async def clear_statement(session: Session) -> dict:
    """Yuklangan vipiskani jadvaldan olib tashlash (faqat dasturdagi ko'rinish; 1C ga tegilmaydi,
    "Dastur yaratganlar" ro'yxati saqlanadi)."""
    async with session.lock:
        session.statement = None
        session.vipiska = []
        session.batches.clear()
        for k in ("vipiska", "vipiska_ver"):
            session.analyzed_with.pop(k, None)
        save_session(session)
    return snapshot(session, "vipiska")


async def recheck_vipiska(session: Session) -> dict:
    """1C ni qayta so'rab, dublikat holatlarini yangilaydi (qo'lda tanlangan schyotlar saqlanadi)."""
    if not session.statement:
        return snapshot(session, "vipiska")
    async with session.lock:
        onec_for(session).invalidate()
        _file_services.pop(session.firm_id, None)
        await _analyze_vipiska(session, session.vipiska)
    return snapshot(session, "vipiska")


async def load_didox(session: Session) -> dict:
    settings = firm_settings(session.firm_id)
    demo_didox = settings["didox_demo_mode"] or not settings.get("didox_user_key")
    if demo_didox and onec_for(session).mode != "demo":
        # Demo hisob-fakturalar o'ylab topilgan — haqiqiy firmaning 1C si bilan solishtirish chalg'itadi
        raise DidoxError("📑 Bu firma uchun Didox API kaliti kiritilmagan (⚙️ Joriy firma → Didox). Demo "
                         "hisob-fakturalar faqat Demo firmada ko'rsatiladi.", status=400)
    invoices, source = await fetch_invoices(settings)
    async with session.lock:
        onec = onec_for(session)
        onec.invalidate()
        try:
            session.didox = await analyze_invoices(invoices, onec, session.didox, rules_of(session))
            session.analyzed_with["didox"] = analysis_tag(session, onec)
        except ODataError as e:
            if onec.mode != "live":
                raise
            fs = file_onec(session.firm_id)  # jonli 1C javob bermadi — fayllar / ko'rish rejimi
            fallback = fs or OneCService(OfflineOData(str(e)))
            session.didox = await analyze_invoices(invoices, fallback, session.didox, rules_of(session))
            session.analyzed_with["didox"] = files_tag(session.firm_id) if fs else OFFLINE + onec_fingerprint(session.firm_id)
        session.didox_source = "Didox (demo)" if source == "demo" else "Didox API"
        session.analyzed_with["didox_ver"] = ANALYSIS_VERSION
        save_session(session)
    return snapshot(session, "didox")


async def change_account(session: Session, mode: str, row_id: str, account: str) -> dict:
    async with session.lock:
        row = next((r for r in rows_of(session, mode) if r["id"] == row_id), None)
        if not row:
            raise KeyError(row_id)
        if row["posted"]:
            raise ValueError("Yozilgan qatorni o'zgartirib bo'lmaydi")
        set_account(row, account)
        save_session(session)
        return row


def write_gate(session: Session, count: int) -> dict:
    """Haqiqiy 1C ga yozish faqat "Egasi" shu firma uchun alohida ruxsat bergan bo'lsa va bir martada limit
    doirasida. Demo baza — cheklovsiz. Natija: firma sozlamalari."""
    settings = firm_settings(session.firm_id)
    if onec_for(session).mode == "live":
        if not services_1c.WRITES_ENABLED:
            raise ODataError(services_1c.WRITE_LOCK_TEXT)
        if not settings["onec_write_enabled"]:
            raise ODataError("🔒 Faqat tahlil rejimi: bu firma uchun 1C ga yozishga ruxsat berilmagan (hech narsa "
                             "o'zgartirilmadi). Ruxsatni \"Egasi\" ⚙️ Sozlamalar → Joriy firma → «1C ga yozish» da beradi.")
        if count > settings["onec_write_limit"]:
            raise ODataError(f"🔒 Bir martada ko'pi bilan {settings['onec_write_limit']} ta hujjat yoziladi — "
                             f"{count} ta tanlangan. Bittadan tanlang (limitni \"Egasi\" oshiradi).")
    return settings


async def post(session: Session, mode: str, ids: list[str], force_ids: list[str] | None = None,
               force_reason: str = "", by: str = "") -> dict:
    """Qoralama yozish. force_ids — buxgalter "baribir yozish"ni alohida tasdiqlagan qatorlar
    (faqat "farq bor" yoki "raqamsiz mos kelgan" qatorlar; to'liq dublikat hech qachon yozilmaydi)."""
    if (session.analyzed_with.get(mode) or "").startswith(FILES):
        raise ODataError("📁 Vipiska 1C eksport fayllari bo'yicha tekshirilgan — 1C ga 📤 «1C import fayli» orqali "
                         "kiriting (Банк и касса → Банковские выписки → Загрузить)")
    if onec_for(session).mode == "file":
        raise ODataError("📁 1C ga to'g'ridan-to'g'ri ulanmagan — 📤 «1C import fayli» orqali kiriting")
    # Haqiqiy 1C: faqat alohida ruxsat bilan va bir martada limit doirasida (standart — faqat tahlil)
    settings = write_gate(session, len(set(ids) | set(force_ids or [])))
    if (session.analyzed_with.get(mode) or "").startswith(OFFLINE):
        raise ODataError("Ko'rish rejimi: vipiska 1C bilan tekshirilmagan — 1C ulangach 🔄 \"1C da qayta tekshirish\" ni bosing")
    if session.analyzed_with.get(mode) != onec_fingerprint(session.firm_id):
        raise ODataError("1C sozlamalari tahlildan keyin o'zgargan — avval 🔄 \"1C da qayta tekshirish\" ni bosing")
    if mode == "vipiska" and session.statement and session.statement.is_sample and onec_for(session).mode == "live":
        raise ODataError("🧪 Test (namuna) vipiska haqiqiy 1C ga yozilmaydi")
    own = session.statement.own_account if session.statement else ""
    onec = onec_for(session)
    # yangi shartnoma: demo bazada — har doim; haqiqiy 1C da — faqat alohida ruxsat bilan
    create_contracts = onec.mode != "live" or settings["onec_create_contracts"]

    async def bank_builder(row, onec):
        return await build_bank_payload(row, onec, own, create_missing=create_contracts)
    builders = {"bank": (bank_builder, recheck_bank), "ehf": (build_purchase_payload, recheck_purchase)}
    async with _post_lock, session.lock:
        results = await post_rows(rows_of(session, mode), ids, onec, builders,
                                  force=set(force_ids or []), force_reason=force_reason)
        by_id = {r["id"]: r for r in rows_of(session, mode)}
        for res in results:  # dastur yaratgan qoralamalar ro'yxati ("bekor qilish" faqat shular uchun)
            row = by_id.get(res["id"])
            if res["ok"] and row and row.get("posted_ref"):
                db.record_draft(session.firm_id, row["entity"], row["posted_ref"], by, number=row.get("posted_number"),
                                in_number=row["number"], doc_date=row["date"], amount=row["amount"],
                                counterparty=row.get("counterparty_1c_name") or row["counterparty_name"],
                                account=row.get("account"))
        save_session(session)
    return {"results": results, "ok": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
            **snapshot(session, mode)}


async def preview_payload(session: Session, mode: str, row_id: str) -> dict:
    row = next((r for r in rows_of(session, mode) if r["id"] == row_id), None)
    if not row:
        raise KeyError(row_id)
    onec = analysis_onec(session, mode)
    payload, error = None, None
    if onec.mode in ("file", "offline"):
        error = ("📁 1C ga to'g'ridan-to'g'ri yozilmaydi: qator 📤 «1C import fayli» orqali kiritiladi, schyotni 1C "
                 "shartnoma/kontragent sozlamasidan qo'yadi — yuklagandan keyin shu jadval bo'yicha tekshiring")
    elif row["status"] in ("new", "discrepancy", "duplicate") and not row["blockers"] and \
            (row["status"] == "new" or row.get("overridable")):
        try:
            if row["kind"] == "bank":
                payload = await build_bank_payload(row, onec, session.statement.own_account if session.statement else "")
            else:
                payload = await build_purchase_payload(row, onec)
            payload, dropped = await onec.filter_payload(row["entity"], {k: v for k, v in payload.items() if v is not None})
            payload["Posted"] = False
            if dropped:
                error = "ℹ️ Sizning 1C konfiguratsiyangizda yo'q, shuning uchun yuborilmaydi: " + ", ".join(dropped)
        except Exception as e:  # noqa: BLE001
            error = str(e)
    learned = row.get("learned") or {}
    names = {}
    if learned:
        from services_1c import E_CASH_FLOW, E_CONTRACT
        names["contract"] = await onec.ref_description(E_CONTRACT, learned.get("contract_key"))
        names["cash_flow"] = await onec.ref_description(E_CASH_FLOW, learned.get("cash_flow_key"))
    return {"row": row, "payload": payload, "payload_error": error, "learned_names": names, "entity": row["entity"]}


def cash_flow_of(session: Session) -> dict:
    return cash_flow(session.vipiska)


# ---------------------------------------------------------------- 1C bilan fayl orqali (eksport / import)
async def load_1c_exports(session: Session, files: list[tuple[str, bytes]], by: str = "") -> dict:
    """1C dan chiqarilgan Excel fayllarni firmaning 1C nusxasiga qo'shadi, qoidalarni o'rganadi, vipiskani qayta
    tekshiradi. Xato fayl boshqalarini to'xtatmaydi."""
    from services_rules import learn_from_1c
    learned_stats = None
    async with _files_lock:  # bir vaqtda ikki joydan yuklanganda bir-birining ustiga yozilmasin
        store = files1c.load_store(session.firm_id) or files1c.empty_store()
        results = []
        for name, content in files:
            try:
                parsed = files1c.parse_export(name, content)
                results.append({"file": name, "ok": True, **files1c.merge(store, parsed, name, by)})
            except files1c.ExportFileError as e:
                results.append({"file": name, "ok": False, "error": str(e)})
        if any(r["ok"] for r in results):
            files1c.save_store(session.firm_id, store)
            learned, info = await learn_from_1c(file_onec(session.firm_id), months=24)
            learned_stats = {**info, **db.upsert_learned_rules(session.firm_id, learned)}
    snap = await recheck_vipiska(session) if session.statement else None
    return {"results": results, "learned": learned_stats, "summary": files1c.summary(files1c.load_store(session.firm_id)),
            "snapshot": snap}


def clear_1c_exports(session: Session) -> None:
    files1c.clear_store(session.firm_id)
    _file_services.pop(session.firm_id, None)


def export_client_bank(session: Session, ids: list[str] | None = None) -> tuple[bytes, list[dict]]:
    """Yangi qatorlardan 1CClientBankExchange fayli. ids bo'lmasa — hali eksport qilinmagan barcha yangi qatorlar."""
    if not session.statement:
        raise ValueError("Avval vipiskani yuklang")
    rows = session.vipiska
    if ids:
        by_id = {r["id"]: r for r in rows}
        bad = [i for i in ids if i not in by_id or not files1c.exportable(by_id[i])]
        if bad:
            raise ValueError(f"{len(bad)} ta qator eksport qilinmaydi (1C da bor, to'siqli yoki yozilgan)")
        chosen = [by_id[i] for i in ids]
    else:
        chosen = [r for r in rows if files1c.exportable(r) and not r.get("exported_at")]
    if not chosen:
        raise ValueError("Eksport uchun yangi qator yo'q (hammasi 1C da bor yoki allaqachon faylga chiqarilgan)")
    st, firm = session.statement, firm_settings(session.firm_id)
    content = files1c.build_client_bank(chosen, st.own_account, st.own_inn or firm["inn"], st.own_name or firm["name"])
    stamp = datetime.now().isoformat(timespec="seconds")
    for r in chosen:
        r["exported_at"] = stamp
    save_session(session)
    return content, chosen


def export_filename(firm_id: int, rows: list[dict]) -> str:
    day = max(r.get("posted_date") or r["date"] for r in rows).replace("-", "")
    return f"kl_to_1c_{firm_id}_{day}.txt"


# ---------------------------------------------------------------- yangi kontragent (vipiskadan)
CREATED_MARK = "BUXGALTER AI: vipiskadan yaratildi"


def _find_row(session: Session, row_id: str) -> dict:
    row = next((r for r in session.vipiska if r["id"] == row_id), None)
    if not row:
        raise ValueError("Qator topilmadi")
    return row


async def counterparty_prefill(session: Session, row_id: str) -> dict:
    """Oyna uchun: vipiskadagi ma'lumot + 1C dagi guruhlar, bank (MFO bo'yicha), shu STIR bilan bor yozuvlar."""
    row = _find_row(session, row_id)
    onec = onec_for(session)
    inn = row.get("counterparty_inn") or ""
    folders = await onec.counterparty_folders()
    want = "покуп" if row["direction"] == "kirim" else "постав"
    folder = next((f["key"] for f in folders if want in f["name"].lower()), "")
    bank = await onec.find_bank(row.get("mfo") or "")
    ref = row.get("contract_ref") or {}
    return {
        "row_id": row_id, "name": row["counterparty_name"], "full_name": row["counterparty_name"], "inn": inn,
        "person": "ФизическоеЛицо" if len(inn) == 14 else "ЮридическоеЛицо", "folders": folders, "folder": folder,
        "account_no": row.get("account_no") or "", "mfo": row.get("mfo") or "",
        "bank": {"key": bank["Ref_Key"], "name": bank.get("Description")} if bank else None,
        "create_contract": True, "contract_number": ref.get("number") or "", "contract_date": ref.get("date") or "",
        "contract_kind": "СПоставщиком" if row["direction"] == "chiqim" else "СПокупателем",
        "existing": [{"name": r.get("Description"), "deleted": bool(r.get("DeletionMark"))}
                     for r in await onec.find_counterparty_any(inn)],
        "write": {"enabled": firm_settings(session.firm_id)["onec_write_enabled"] and services_1c.WRITES_ENABLED,
                  "locked": not services_1c.WRITES_ENABLED, "mode": onec.mode},
    }


async def create_counterparty(session: Session, row_id: str, data: dict, by: str = "") -> dict:
    """Kontragent (+ bank hisobi, + shartnoma) 1C da yaratish. Faqat yozish ruxsati bilan; avval shu STIR bilan
    (o'chirishga belgilanganlari ham) bor-yo'qligi tekshiriladi — takror yaratilmaydi."""
    import re as _re
    from services_1c import E_BANK_ACCOUNT, E_COUNTERPARTY, E_CONTRACT, _is_empty_key
    row = _find_row(session, row_id)
    name = " ".join(str(data.get("name") or "").split())[:150]
    inn = _re.sub(r"\D", "", str(data.get("inn") or ""))
    account_no = _re.sub(r"\D", "", str(data.get("account_no") or ""))
    mfo = _re.sub(r"\D", "", str(data.get("mfo") or ""))
    if not name:
        raise ValueError("Kontragent nomini kiriting")
    if len(inn) not in (9, 14):
        raise ValueError("STIR 9 xonali (yuridik shaxs) yoki JShShIR 14 xonali (jismoniy shaxs) bo'lsin")
    if account_no and len(account_no) != 20:
        raise ValueError("Bank hisob raqami 20 xonali bo'lsin")
    write_gate(session, 1)
    onec = onec_for(session)
    if onec.mode == "live":  # egasi qarori: haqiqiy 1C ga faqat bank vipiskasi va kirim fakturalari
        services_1c.write_guard(E_COUNTERPARTY)
    if onec.mode not in ("live", "demo"):
        raise ODataError("1C ga ulanmagan — kontragentni 1C ning o'zida qo'shing (yoki import faylida 1C o'zi yaratadi)")
    async with _post_lock, session.lock:
        existing = await onec.find_counterparty_any(inn)
        if existing:
            e = existing[0]
            raise ValueError(f"1C da shu STIR bilan kontragent bor: {e.get('Description')}"
                             + (" (o'chirishga belgilangan — 1C da belgini olib tashlang)" if e.get("DeletionMark") else "")
                             + " — yangisi yaratilmadi")
        person = "ФизическоеЛицо" if data.get("person") == "ФизическоеЛицо" or len(inn) == 14 else "ЮридическоеЛицо"
        template = await onec.latest(E_COUNTERPARTY, [Cond("IsFolder", "eq", False)])
        body = {"Description": name, "НаименованиеПолное": " ".join(str(data.get("full_name") or name).split())[:250],
                "ЮридическоеФизическоеЛицо": person, "ИННВведенКорректно": True, "Комментарий": CREATED_MARK,
                "Parent_Key": data.get("folder") or None, "СтранаРегистрации_Key": template.get("СтранаРегистрации_Key"),
                "IsFolder": False, "DeletionMark": False,
                ("ИНН" if len(inn) == 9 else "ПИНФЛ"): inn}
        body, _ = await onec.filter_payload(E_COUNTERPARTY, {k: v for k, v in body.items() if not _is_empty_key(v) or k in ("IsFolder", "DeletionMark")})
        cp = await onec.t.create(E_COUNTERPARTY, body)
        ref = cp["Ref_Key"]
        db.record_draft(session.firm_id, E_COUNTERPARTY, ref, by, number=cp.get("Code"), counterparty=name)
        created = {"counterparty": {"ref": ref, "name": name, "code": cp.get("Code")}}
        patch = {"ГоловнойКонтрагент_Key": ref}
        bank = await onec.find_bank(mfo) if account_no and mfo else None
        if account_no and not bank:
            created["bank_note"] = f"MFO {mfo or '—'} bo'yicha bank 1C da topilmadi — bank hisobini 1C da qo'shing"
        if bank:
            acc_tmpl = await onec.latest(E_BANK_ACCOUNT, [Cond("Owner_Type", "eq", f"StandardODATA.{E_COUNTERPARTY}")])
            acc_body = {"Owner": ref, "Owner_Type": f"StandardODATA.{E_COUNTERPARTY}", "НомерСчета": account_no,
                        "Банк_Key": bank["Ref_Key"], "Description": f"{account_no} в {mfo} {bank.get('Description') or ''}"[:100],
                        "ВалютаДенежныхСредств_Key": acc_tmpl.get("ВалютаДенежныхСредств_Key"),
                        "ВидСчета": acc_tmpl.get("ВидСчета") or "Расчетный", "DeletionMark": False}
            acc_body, _ = await onec.filter_payload(E_BANK_ACCOUNT, {k: v for k, v in acc_body.items() if v is not None})
            acc = await onec.t.create(E_BANK_ACCOUNT, acc_body)
            db.record_draft(session.firm_id, E_BANK_ACCOUNT, acc["Ref_Key"], by, in_number=ref, counterparty=name,
                            number=account_no)
            patch["ОсновнойБанковскийСчет_Key"] = acc["Ref_Key"]
            created["bank_account"] = account_no
        props = await onec.entity_props(E_COUNTERPARTY)
        patch = {k: v for k, v in patch.items() if props is None or k in props["header"]}
        if patch:
            await onec.t.patch(E_COUNTERPARTY, ref, patch)
        if data.get("create_contract"):
            ctr = await onec.create_contract(ref, str(data.get("contract_number") or "").strip()[:30],
                                             str(data.get("contract_date") or "")[:10] or None, row["direction"])
            db.record_draft(session.firm_id, E_CONTRACT, ctr["Ref_Key"], by, in_number=ref, counterparty=name,
                            number=ctr.get("Description") or "")
            created["contract"] = ctr.get("Description")
        onec.invalidate()
    await recheck_vipiska(session)
    return created


async def _used_in_posted(onec, entity: str, ref: str) -> bool:
    """Kontragent / shartnoma o'tkazilgan bank hujjatlarida ishlatilganmi (bo'lsa — bekor qilinmaydi)."""
    from services_1c import E_BANK_IN, E_BANK_OUT, E_CONTRACT, FIELDS, Guid
    for doc_entity in (E_BANK_IN, E_BANK_OUT):
        cond = (Cond(FIELDS["contract"], "eq", Guid(ref)) if entity == E_CONTRACT
                else await onec.counterparty_cond(doc_entity, ref))
        if await onec.t.query(doc_entity, [cond, Cond("Posted", "eq", True)], top=1, select=["Ref_Key"]):
            return True
    return False


async def undo_draft(session: Session, draft_id: int, by: str = "") -> dict:
    """Dastur yaratgan, hali O'TKAZILMAGAN qoralamani 1C da "o'chirishga belgilash" (DeletionMark). Boshqa
    hujjatlarga tegilmaydi: faqat ro'yxatimizdagi Ref_Key, izohi "BUXGALTER AI" bilan boshlangan va Posted=false."""
    draft = db.get_draft(session.firm_id, draft_id)
    if not draft:
        raise ValueError("Qoralama topilmadi")
    if draft["undone_at"]:
        raise ValueError("Bu qoralama allaqachon bekor qilingan")
    write_gate(session, 1)
    onec = onec_for(session)
    async with _post_lock, session.lock:
        doc = await onec.t.get(draft["entity"], draft["ref"])
        if not doc:
            raise ValueError("1C da bu hujjat topilmadi (o'chirilgan bo'lishi mumkin)")
        if doc.get("Posted"):
            raise ValueError("Hujjat 1C da allaqachon O'TKAZILGAN — dastur unga tegmaydi. Kerak bo'lsa 1C ning o'zida "
                             "o'tkazishni bekor qiling")
        if not str(doc.get("Комментарий") or "").startswith("BUXGALTER AI"):
            raise ValueError("Hujjat izohi o'zgartirilgan — dastur yaratgan qoralama ekaniga ishonch yo'q, tegilmadi")
        if draft["entity"].startswith("Catalog_") and await _used_in_posted(onec, draft["entity"], draft["ref"]):
            raise ValueError("Bu kontragent/shartnoma 1C da o'tkazilgan hujjatlarda ishlatilgan — dastur unga tegmaydi")
        if not doc.get("DeletionMark"):
            await onec.t.patch(draft["entity"], draft["ref"], {"DeletionMark": True})
        db.mark_draft_undone(draft_id, by)
        for child in db.list_drafts(session.firm_id, 500):  # kontragent bilan birga yaratilgan bank hisobi va shartnoma
            if child["in_number"] == draft["ref"] and not child["undone_at"] and child["entity"].startswith("Catalog_"):
                if not (await onec.t.get(child["entity"], child["ref"]) or {}).get("DeletionMark"):
                    await onec.t.patch(child["entity"], child["ref"], {"DeletionMark": True})
                db.mark_draft_undone(child["id"], by)
        onec.invalidate()
        for row in session.vipiska + session.didox:  # jadvalda qator yana "yangi" bo'ladi
            if row.get("posted_ref") == draft["ref"]:
                row.update(posted=False, posted_ref=None, posted_number=None)
        save_session(session)
    if session.statement:
        await recheck_vipiska(session)
    return db.get_draft(session.firm_id, draft_id)
