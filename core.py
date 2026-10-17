"""Umumiy ish oqimlari: Web App, Telegram bot va AI vositalari (tools) shu funksiyalardan foydalanadi."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import config
from services_1c import ACCOUNT_CHOICES, OneCService, build_service
from services_didox import analyze_invoices, build_purchase_payload, fetch_invoices, recheck_purchase
from services_vipiska import (Statement, analyze_statement, build_bank_payload, cash_flow, kpis, parse_statement,
                              post_rows, recheck_bank, set_account)


@dataclass
class Session:
    sid: str
    statement: Statement | None = None
    vipiska: list[dict] = field(default_factory=list)
    didox: list[dict] = field(default_factory=list)
    didox_source: str = ""
    chat: list[dict] = field(default_factory=list)
    pending: dict | None = None  # Telegram: AI tasdiqlash kartasidagi qatorlar
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


SESSIONS: dict[str, Session] = {}
_onec: OneCService | None = None
_onec_sig: tuple | None = None


def get_session(sid: str) -> Session:
    if sid not in SESSIONS:
        if len(SESSIONS) > 500:  # oddiy xotira himoyasi
            SESSIONS.pop(next(iter(SESSIONS)))
        SESSIONS[sid] = Session(sid)
    return SESSIONS[sid]


def get_onec() -> OneCService:
    """1C servis singleton (demo bazadagi qoralamalar qayta ishga tushguncha saqlanadi)."""
    global _onec, _onec_sig
    s = config.get_settings()
    sig = (s["onec_demo_mode"], s["onec_base_url"], s["onec_user"], s["onec_password"], s["onec_org_inn"])
    if _onec is None or sig != _onec_sig:
        _onec, _onec_sig = build_service(s), sig
    return _onec


def rows_of(session: Session, mode: str) -> list[dict]:
    return session.didox if mode == "didox" else session.vipiska


def snapshot(session: Session, mode: str = "vipiska") -> dict:
    rows = rows_of(session, mode)
    source = session.didox_source if mode == "didox" else (session.statement.source if session.statement else "")
    choices = list(ACCOUNT_CHOICES)
    for r in rows:
        if r.get("account") and r["account"] not in choices:
            choices.append(r["account"])
    return {"mode": mode, "rows": rows, "kpis": kpis(rows), "source": source, "account_choices": choices,
            "onec_mode": get_onec().mode,
            "warnings": session.statement.warnings if (mode == "vipiska" and session.statement) else []}


async def load_statement(session: Session, filename: str, content: bytes) -> dict:
    st = parse_statement(filename, content)
    async with session.lock:
        session.statement = st
        session.vipiska = await analyze_statement(st, get_onec())
    return snapshot(session, "vipiska")


async def load_sample(session: Session) -> dict:
    if not config.SAMPLE_VIPISKA.exists():
        from demo_data import build_sample_xlsx
        build_sample_xlsx(config.SAMPLE_VIPISKA)
    return await load_statement(session, config.SAMPLE_VIPISKA.name, config.SAMPLE_VIPISKA.read_bytes())


async def recheck_vipiska(session: Session) -> dict:
    """1C ni qayta so'rab, dublikat holatlarini yangilaydi (qo'lda tanlangan schyotlar saqlanadi)."""
    if not session.statement:
        return snapshot(session, "vipiska")
    async with session.lock:
        onec = get_onec()
        onec.invalidate()
        session.vipiska = await analyze_statement(session.statement, onec, session.vipiska)
    return snapshot(session, "vipiska")


async def load_didox(session: Session) -> dict:
    invoices, source = await fetch_invoices(config.get_settings())
    async with session.lock:
        onec = get_onec()
        onec.invalidate()
        session.didox = await analyze_invoices(invoices, onec, session.didox)
        session.didox_source = "Didox (demo)" if source == "demo" else "Didox API"
    return snapshot(session, "didox")


async def change_account(session: Session, mode: str, row_id: str, account: str) -> dict:
    async with session.lock:
        row = next((r for r in rows_of(session, mode) if r["id"] == row_id), None)
        if not row:
            raise KeyError(row_id)
        if row["posted"]:
            raise ValueError("Yozilgan qatorni o'zgartirib bo'lmaydi")
        set_account(row, account)
        return row


async def post(session: Session, mode: str, ids: list[str]) -> dict:
    own = session.statement.own_account if session.statement else ""

    async def bank_builder(row, onec):
        return await build_bank_payload(row, onec, own)
    builders = {"bank": (bank_builder, recheck_bank), "ehf": (build_purchase_payload, recheck_purchase)}
    async with session.lock:
        results = await post_rows(rows_of(session, mode), ids, get_onec(), builders)
    return {"results": results, "ok": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
            **snapshot(session, mode)}


async def preview_payload(session: Session, mode: str, row_id: str) -> dict:
    row = next((r for r in rows_of(session, mode) if r["id"] == row_id), None)
    if not row:
        raise KeyError(row_id)
    onec = get_onec()
    payload, error = None, None
    if row["status"] in ("new", "discrepancy") and not row["blockers"]:
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
