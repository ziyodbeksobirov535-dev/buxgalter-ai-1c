"""Umumiy ish oqimlari: Web App, Telegram bot va AI vositalari (tools) shu funksiyalardan foydalanadi."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date

import config
from services_1c import ACCOUNT_CHOICES, ODataError, OneCService, build_service
from services_didox import analyze_invoices, build_purchase_payload, fetch_invoices, recheck_purchase
from services_vipiska import (BankRow, Statement, analyze_statement, build_bank_payload, cash_flow, kpis,
                              parse_statement, post_rows, recheck_bank, set_account)

log = logging.getLogger("buxgalter.core")
SESSIONS_DIR = config.DATA_DIR / "sessions"


@dataclass
class Session:
    sid: str
    statement: Statement | None = None
    vipiska: list[dict] = field(default_factory=list)
    didox: list[dict] = field(default_factory=list)
    didox_source: str = ""
    chat: list[dict] = field(default_factory=list)
    pending: dict | None = None  # Telegram: AI tasdiqlash kartasidagi qatorlar
    analyzed_with: dict = field(default_factory=dict)  # rejim -> qaysi 1C sozlamalari bilan tahlil qilingan
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


SESSIONS: dict[str, Session] = {}
_onec: OneCService | None = None
_onec_sig: tuple | None = None
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
            "rows": [{**asdict(r), "date": r.date.isoformat()} for r in st.rows], "own_account": st.own_account,
            "source": st.source, "warnings": st.warnings, "is_sample": st.is_sample},
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


def _load_session(sid: str) -> Session:
    session = Session(sid)
    path = _session_file(sid)
    if not path.exists():
        return session
    try:
        data = json.loads(path.read_text("utf-8"))
        st = data.get("statement")
        if st:
            rows = [BankRow(**{**r, "date": date.fromisoformat(r["date"])}) for r in st["rows"]]
            session.statement = Statement(rows=rows, own_account=st["own_account"], source=st["source"],
                                          warnings=st["warnings"], is_sample=st.get("is_sample", False))
        session.vipiska = data.get("vipiska", [])
        session.didox = data.get("didox", [])
        session.didox_source = data.get("didox_source", "")
        session.analyzed_with = data.get("analyzed_with", {})
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("Sessiya fayli o'qilmadi (%s): %s", sid, e)
    return session


def get_session(sid: str) -> Session:
    if sid not in SESSIONS:
        if len(SESSIONS) > 500:  # oddiy xotira himoyasi
            SESSIONS.pop(next(iter(SESSIONS)))
        SESSIONS[sid] = _load_session(sid)
    return SESSIONS[sid]


# ---------------------------------------------------------------- 1C servis
def get_onec() -> OneCService:
    """1C servis singleton (demo bazadagi qoralamalar qayta ishga tushguncha saqlanadi)."""
    global _onec, _onec_sig
    s = config.get_settings()
    sig = (s["onec_demo_mode"], s["onec_base_url"], s["onec_user"], s["onec_password"], s["onec_org_inn"])
    if _onec is None or sig != _onec_sig:
        _onec, _onec_sig = build_service(s), sig
    return _onec


def onec_fingerprint() -> str:
    """Joriy 1C ulanishining izi: rejim + manzil + login + tashkilot (parol izga kirmaydi)."""
    get_onec()
    demo, url, user, _password, org = _onec_sig
    return hashlib.sha256(repr((demo, url, user, org)).encode()).hexdigest()[:16]


def rows_of(session: Session, mode: str) -> list[dict]:
    return session.didox if mode == "didox" else session.vipiska


def snapshot(session: Session, mode: str = "vipiska") -> dict:
    rows = rows_of(session, mode)
    source = session.didox_source if mode == "didox" else (session.statement.source if session.statement else "")
    choices = list(ACCOUNT_CHOICES)
    for r in rows:
        if r.get("account") and r["account"] not in choices:
            choices.append(r["account"])
    stale = bool(rows) and session.analyzed_with.get(mode) not in (None, onec_fingerprint())
    return {"mode": mode, "rows": rows, "kpis": kpis(rows), "source": source, "account_choices": choices,
            "onec_mode": get_onec().mode, "stale": stale,
            "is_sample": bool(mode == "vipiska" and session.statement and session.statement.is_sample),
            "warnings": session.statement.warnings if (mode == "vipiska" and session.statement) else []}


# ---------------------------------------------------------------- vipiska / didox
async def _analyze_vipiska(session: Session, previous: list[dict] | None = None) -> None:
    onec = get_onec()
    session.vipiska = await analyze_statement(session.statement, onec, previous)
    session.analyzed_with["vipiska"] = onec_fingerprint()
    save_session(session)


async def load_statement(session: Session, filename: str, content: bytes, is_sample: bool = False) -> dict:
    st = parse_statement(filename, content)
    st.is_sample = is_sample
    async with session.lock:
        session.statement = st
        await _analyze_vipiska(session)
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
        get_onec().invalidate()
        await _analyze_vipiska(session, session.vipiska)
    return snapshot(session, "vipiska")


async def load_didox(session: Session) -> dict:
    invoices, source = await fetch_invoices(config.get_settings())
    async with session.lock:
        onec = get_onec()
        onec.invalidate()
        session.didox = await analyze_invoices(invoices, onec, session.didox)
        session.didox_source = "Didox (demo)" if source == "demo" else "Didox API"
        session.analyzed_with["didox"] = onec_fingerprint()
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
    if session.analyzed_with.get(mode) != onec_fingerprint():
        raise ODataError("1C sozlamalari tahlildan keyin o'zgargan — avval 🔄 \"1C da qayta tekshirish\" ni bosing")
    if mode == "vipiska" and session.statement and session.statement.is_sample and get_onec().mode == "live":
        raise ODataError("🧪 Test (namuna) vipiska haqiqiy 1C ga yozilmaydi")
    own = session.statement.own_account if session.statement else ""

    async def bank_builder(row, onec):
        return await build_bank_payload(row, onec, own)
    builders = {"bank": (bank_builder, recheck_bank), "ehf": (build_purchase_payload, recheck_purchase)}
    async with _post_lock, session.lock:
        results = await post_rows(rows_of(session, mode), ids, get_onec(), builders,
                                  force=set(force_ids or []), force_reason=force_reason)
        save_session(session)
    return {"results": results, "ok": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
            **snapshot(session, mode)}


async def preview_payload(session: Session, mode: str, row_id: str) -> dict:
    row = next((r for r in rows_of(session, mode) if r["id"] == row_id), None)
    if not row:
        raise KeyError(row_id)
    onec = get_onec()
    payload, error = None, None
    if row["status"] in ("new", "discrepancy", "duplicate") and not row["blockers"] and \
            (row["status"] == "new" or row.get("overridable")):
        try:
            if row["kind"] == "bank":
                payload = await build_bank_payload(row, onec, session.statement.own_account if session.statement else "")
            else:
                payload = await build_purchase_payload(row, onec)
            payload["Posted"] = False
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
