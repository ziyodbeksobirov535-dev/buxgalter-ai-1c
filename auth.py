"""Kirish nazorati: login cookie, Telegram Mini App imzosi, rollar va firmaga kirish huquqi.

Rollar:
  owner      (Egasi)      — hamma narsa: firmalar, foydalanuvchilar, AI sozlamalari, barcha firmalar
  accountant (Buxgalter)  — biriktirilgan firmalarda vipiska yuklash, tekshirish va 1C ga qoralama yozish
  viewer     (Kuzatuvchi) — biriktirilgan firmalarda faqat ko'rish va tahlil (1C ga yozolmaydi)
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

import config
import core
import db

COOKIE = "bx_session"
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "cf-connecting-ip")

CAN_POST = {"owner", "accountant"}


def is_local(request: Request) -> bool:
    """So'rov shu kompyuterning o'zidan kelganmi (tunnel/proxy orqali emas)."""
    if any(h in request.headers for h in _PROXY_HEADERS):
        return False
    return (request.client.host if request.client else "") in _LOOPBACK


def client_ip(request: Request) -> str:
    fwd = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "?")


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


def set_session_cookie(response, token: str, request: Request) -> None:
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=is_https(request),
                        max_age=int(db.SESSION_MAX.total_seconds()), path="/")


def telegram_id_from_init_data(init_data: str) -> int:
    from aiogram.utils.web_app import safe_parse_webapp_init_data
    if not config.TELEGRAM_BOT_TOKEN:
        raise HTTPException(401, "Server'da TELEGRAM_BOT_TOKEN sozlanmagan")
    try:
        data = safe_parse_webapp_init_data(config.TELEGRAM_BOT_TOKEN, init_data)
    except ValueError:
        raise HTTPException(401, "Telegram initData imzosi noto'g'ri") from None
    if data.auth_date and time.time() - data.auth_date.timestamp() > 86400:
        raise HTTPException(401, "Telegram sessiyasi eskirgan — Web App'ni qayta oching")
    if not data.user:
        raise HTTPException(401, "Telegram foydalanuvchisi aniqlanmadi")
    return data.user.id


async def current_user(request: Request) -> dict:
    init = request.headers.get("x-telegram-init-data")
    if init:
        tg_id = telegram_id_from_init_data(init)
        user = db.get_user_by_telegram(tg_id)
        if not user:
            raise HTTPException(403, f"Telegram akkauntingiz (ID {tg_id}) bog'lanmagan. Web'da kiring → "
                                     "⚙️ Sozlamalar → \"Telegram'ni bog'lash\" kodini botga yuboring.")
        request.state.via = "telegram"
        return user
    user = db.user_from_web_session(request.cookies.get(COOKIE, ""))
    if not user:
        raise HTTPException(401, "Tizimga kiring")
    request.state.via = "web"
    return user


def require_owner(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "owner":
        raise HTTPException(403, "Bu amal faqat \"Egasi\" uchun")
    return user


@dataclass
class FirmCtx:
    user: dict
    firm_id: int
    session: core.Session

    @property
    def can_post(self) -> bool:
        return self.user["role"] in CAN_POST

    def require_post(self) -> None:
        if not self.can_post:
            raise HTTPException(403, "\"Kuzatuvchi\" roli 1C ga yoza olmaydi")


async def firm_ctx(request: Request, user: dict = Depends(current_user)) -> FirmCtx:
    raw = request.headers.get("x-firm-id") or request.query_params.get("firm_id") or ""
    if not raw and getattr(request.state, "via", "") == "telegram":
        raw = str(user.get("tg_firm_id") or "")
    if not raw.isdigit():
        firms = db.list_firms(user)
        if not firms:
            raise HTTPException(403, "Sizga hech qanday firma biriktirilmagan — \"Egasi\"ga murojaat qiling")
        raw = str(next((f["id"] for f in firms if "demo" not in f["name"].lower() and not f.get("onec_demo_mode")), firms[0]["id"]))
    firm_id = int(raw)
    if not db.user_can_access(user, firm_id):
        raise HTTPException(403, "Bu firmaga kirish huquqingiz yo'q")
    session = core.get_session(core.session_id(user["id"], firm_id), firm_id)
    return FirmCtx(user, firm_id, session)
