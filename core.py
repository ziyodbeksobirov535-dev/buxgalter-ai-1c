"""Umumiy ish oqimlari: Web App, Telegram bot va AI vositalari (tools) shu funksiyalardan foydalanadi."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from dataclasses import asdict, dataclass, field
from datetime import date

import config
import db
from services_1c import ACCOUNT_CHOICES, ODataError, OfflineOData, OneCService, build_service
from services_didox import analyze_invoices, build_purchase_payload, fetch_invoices, recheck_purchase
from services_rules import RuleSet
from services_vipiska import (BankRow, Statement, analyze_statement, build_bank_payload, cash_flow, kpis,
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
# Barcha sessiyalar (Web App + Telegram) uchun bitta qulf: ikki joydan bir vaqtda bir xil qatorni yozishda
# dublikat tekshiruvi bilan yozish orasidagi "oyna"da ikkalasi ham o'tib ketmasligi uchun.
_post_lock = asyncio.Lock()


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
            "own_name": st.own_name},
        "vipiska": session.vipiska, "didox": session.didox, "didox_source": session.didox_source,
        "analyzed_with": session.analyzed_with,
    }
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _session_file(session.sid).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), "utf-8")
        tmp.replace(_session_file(session.sid))
    except OSError as e:
        log.warning("Sessiya saqlanmadi (%s): %s", session.sid, e)


def _load_session(sid: str, firm_id: int) -> Session:
    session = Session(sid, firm_id)
    path = _session_file(sid)
    if not path.exists():
        return session
    try:
        data = json.loads(path.read_text("utf-8"))
        st = data.get("statement")
        if st:
            rows = [BankRow(**{**r, "date": date.fromisoformat(r["date"]),
                               "posted_date": date.fromisoformat(r["posted_date"]) if r.get("posted_date") else None})
                    for r in st["rows"]]
            session.statement = Statement(rows=rows, own_account=st["own_account"], source=st["source"],
                                          warnings=st["warnings"], is_sample=st.get("is_sample", False),
                                          own_inn=st.get("own_inn", ""), own_name=st.get("own_name", ""))
        session.vipiska = data.get("vipiska", [])
        session.didox = data.get("didox", [])
        session.didox_source = data.get("didox_source", "")
        session.analyzed_with = data.get("analyzed_with", {})
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("Sessiya fayli o'qilmadi (%s): %s", sid, e)
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


def get_onec(firm_id: int) -> OneCService:
    """Har bir firma uchun alohida 1C servis (demo bazadagi qoralamalar qayta ishga tushguncha saqlanadi)."""
    s = firm_settings(firm_id)
    sig = _sig(s)
    cached = _services.get(firm_id)
    if cached is None or cached[0] != sig:
        _services[firm_id] = (sig, build_service(s))
    return _services[firm_id][1]


def onec_for(session: Session) -> OneCService:
    return get_onec(session.firm_id)


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
    stale = bool(rows) and not offline and analyzed not in ("", onec_fingerprint(session.firm_id))
    st = session.statement if mode == "vipiska" else None
    return {"mode": mode, "rows": rows, "kpis": kpis(rows), "source": source, "account_choices": choices,
            "onec_mode": onec_for(session).mode, "stale": stale, "offline": offline, "firm_id": session.firm_id,
            "statement_owner": {"inn": st.own_inn, "account": st.own_account, "name": st.own_name} if st else None,
            "is_sample": bool(mode == "vipiska" and session.statement and session.statement.is_sample),
            "warnings": session.statement.warnings if (mode == "vipiska" and session.statement) else []}


# ---------------------------------------------------------------- vipiska / didox
async def _analyze_vipiska(session: Session, previous: list[dict] | None = None) -> None:
    onec = onec_for(session)
    blockers, rules = firm_blockers(session, session.statement), rules_of(session)
    try:
        session.vipiska = await analyze_statement(session.statement, onec, previous, blockers, rules)
        session.analyzed_with["vipiska"] = onec_fingerprint(session.firm_id)
    except ODataError as e:
        if onec.mode != "live":
            raise
        # Real 1C javob bermadi (masalan, OData'da obyektlar nashr qilinmagan) — ko'rish rejimida tahlil:
        # schyotlar qoidalar/standart bo'yicha, shartnoma to'lov maqsadidan; hech narsa yozilmaydi.
        offline = OneCService(OfflineOData(str(e)))
        session.vipiska = await analyze_statement(session.statement, offline, previous, blockers, rules)
        session.analyzed_with["vipiska"] = OFFLINE + onec_fingerprint(session.firm_id)
    save_session(session)


OFFLINE = "offline:"


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
    if not config.SAMPLE_VIPISKA.exists():
        from demo_data import build_sample_xlsx
        build_sample_xlsx(config.SAMPLE_VIPISKA)
    return await load_statement(session, config.SAMPLE_VIPISKA.name, config.SAMPLE_VIPISKA.read_bytes(),
                                is_sample=True)


async def recheck_vipiska(session: Session) -> dict:
    """1C ni qayta so'rab, dublikat holatlarini yangilaydi (qo'lda tanlangan schyotlar saqlanadi)."""
    if not session.statement:
        return snapshot(session, "vipiska")
    async with session.lock:
        onec_for(session).invalidate()
        await _analyze_vipiska(session, session.vipiska)
    return snapshot(session, "vipiska")


async def load_didox(session: Session) -> dict:
    invoices, source = await fetch_invoices(firm_settings(session.firm_id))
    async with session.lock:
        onec = onec_for(session)
        onec.invalidate()
        session.didox = await analyze_invoices(invoices, onec, session.didox, rules_of(session))
        session.didox_source = "Didox (demo)" if source == "demo" else "Didox API"
        session.analyzed_with["didox"] = onec_fingerprint(session.firm_id)
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


async def post(session: Session, mode: str, ids: list[str], force_ids: list[str] | None = None,
               force_reason: str = "") -> dict:
    """Qoralama yozish. force_ids — buxgalter "baribir yozish"ni alohida tasdiqlagan qatorlar
    (faqat "farq bor" yoki "raqamsiz mos kelgan" qatorlar; to'liq dublikat hech qachon yozilmaydi)."""
    # FAQAT TAHLIL: real (demo bo'lmagan) 1C ga hozircha hech narsa yozilmaydi — egasining talabi
    if onec_for(session).mode == "live":
        raise ODataError("🔒 Faqat tahlil rejimi: real 1C ga yozish hozircha o'chirilgan (hech narsa o'zgartirilmadi)")
    if (session.analyzed_with.get(mode) or "").startswith(OFFLINE):
        raise ODataError("Ko'rish rejimi: vipiska 1C bilan tekshirilmagan — 1C ulangach 🔄 \"1C da qayta tekshirish\" ni bosing")
    if session.analyzed_with.get(mode) != onec_fingerprint(session.firm_id):
        raise ODataError("1C sozlamalari tahlildan keyin o'zgargan — avval 🔄 \"1C da qayta tekshirish\" ni bosing")
    if mode == "vipiska" and session.statement and session.statement.is_sample and onec_for(session).mode == "live":
        raise ODataError("🧪 Test (namuna) vipiska haqiqiy 1C ga yozilmaydi")
    own = session.statement.own_account if session.statement else ""

    async def bank_builder(row, onec):
        return await build_bank_payload(row, onec, own, create_missing=True)  # faqat yozishda yangi shartnoma
    builders = {"bank": (bank_builder, recheck_bank), "ehf": (build_purchase_payload, recheck_purchase)}
    async with _post_lock, session.lock:
        results = await post_rows(rows_of(session, mode), ids, onec_for(session), builders,
                                  force=set(force_ids or []), force_reason=force_reason)
        save_session(session)
    return {"results": results, "ok": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
            **snapshot(session, mode)}


async def preview_payload(session: Session, mode: str, row_id: str) -> dict:
    row = next((r for r in rows_of(session, mode) if r["id"] == row_id), None)
    if not row:
        raise KeyError(row_id)
    onec = onec_for(session)
    payload, error = None, None
    if row["status"] in ("new", "discrepancy", "duplicate") and not row["blockers"] and \
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
