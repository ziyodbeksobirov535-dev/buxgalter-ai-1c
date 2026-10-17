"""BUXGALTER AI — FastAPI server (Web App + Telegram Mini App + bot).

Ishga tushirish:  .venv/bin/python main.py   →  http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

import config
import core
import services_ai
from demo_data import build_sample_xlsx
from services_1c import ODataError
from services_didox import DidoxError
from services_vipiska import StatementError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("buxgalter")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not config.SAMPLE_VIPISKA.exists():
        build_sample_xlsx(config.SAMPLE_VIPISKA)
        log.info("Namuna vipiska yaratildi: %s", config.SAMPLE_VIPISKA)
    bot_task = None
    if config.TELEGRAM_BOT_TOKEN:
        import bot
        bot_task = asyncio.create_task(bot.run_polling())
        log.info("Telegram bot ishga tushdi (polling)")
    yield
    if bot_task:
        bot_task.cancel()


app = FastAPI(title="BUXGALTER AI", version="1.0.0", lifespan=lifespan)
# Begona domen nomi bilan kelgan so'rovlarni rad etish: zararli sayt DNS rebinding orqali 127.0.0.1 dagi
# serverga murojaat qilib, sozlamalarni o'zgartira olmasligi uchun.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)

# ---------------------------------------------------------------- kirish nazorati
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "cf-connecting-ip")


def _is_remote(request: Request) -> bool:
    # Tunnel (ngrok / cloudflared) orqali kelgan so'rovlar ham localhost'dan keladi — proxy sarlavhalarini tekshiramiz
    if any(h in request.headers for h in _PROXY_HEADERS):
        return True
    return (request.client.host if request.client else "") not in _LOOPBACK


def telegram_user_from_init_data(init_data: str) -> int:
    from aiogram.utils.web_app import safe_parse_webapp_init_data
    if not config.TELEGRAM_BOT_TOKEN:
        raise HTTPException(401, "Server'da TELEGRAM_BOT_TOKEN sozlanmagan")
    try:
        data = safe_parse_webapp_init_data(config.TELEGRAM_BOT_TOKEN, init_data)
    except ValueError:
        raise HTTPException(401, "Telegram initData imzosi noto'g'ri") from None
    if data.auth_date and time.time() - data.auth_date.timestamp() > 86400:
        raise HTTPException(401, "Telegram sessiyasi eskirgan — Web App'ni qayta oching")
    uid = data.user.id if data.user else 0
    if uid not in config.ALLOWED_TELEGRAM_IDS:
        raise HTTPException(403, f"Telegram ID {uid} ruxsat etilmagan. .env dagi ALLOWED_TELEGRAM_IDS ga qo'shing.")
    return uid


async def access(request: Request) -> core.Session:
    init = request.headers.get("x-telegram-init-data")
    if init:
        return core.get_session(f"tg_{telegram_user_from_init_data(init)}")
    if _is_remote(request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not config.APP_ACCESS_TOKEN or not hmac.compare_digest(token, config.APP_ACCESS_TOKEN):
            raise HTTPException(401, "Kirish tokeni kerak (APP_ACCESS_TOKEN) yoki Telegram orqali oching")
    sid = re.sub(r"[^A-Za-z0-9_-]", "", request.headers.get("x-session-id", ""))[:64] or "default"
    return core.get_session(f"web_{sid}")


@app.exception_handler(StatementError)
async def _statement_error(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(ODataError)
async def _odata_error(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=502)


@app.exception_handler(DidoxError)
async def _didox_error(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=502)


# ---------------------------------------------------------------- sahifalar
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(config.BASE_DIR / "templates" / "index.html")


@app.get("/api/sample")
async def sample_download():
    if not config.SAMPLE_VIPISKA.exists():
        build_sample_xlsx(config.SAMPLE_VIPISKA)
    return FileResponse(config.SAMPLE_VIPISKA, filename="sample_bank_vipiska.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/status")
async def status(session: core.Session = Depends(access)):
    return {"onec": await core.get_onec().status(), "ai": services_ai.engine_info(), "dedup": True,
            "telegram": bool(config.TELEGRAM_BOT_TOKEN), "session": session.sid.split("_")[0]}


@app.get("/api/1c/metadata")
async def onec_metadata(_s: core.Session = Depends(access)):
    return await core.get_onec().check_metadata()


# ---------------------------------------------------------------- vipiska / didox
def _mode(mode: str) -> str:
    if mode not in ("vipiska", "didox"):
        raise HTTPException(400, "mode: vipiska | didox")
    return mode


@app.get("/api/session")
async def get_state(mode: str = "vipiska", session: core.Session = Depends(access)):
    return core.snapshot(session, _mode(mode))


@app.post("/api/vipiska/upload")
async def upload_vipiska(file: UploadFile = File(...), session: core.Session = Depends(access)):
    content = await file.read(10 * 1024 * 1024 + 1)
    return await core.load_statement(session, file.filename or "vipiska.xlsx", content)


@app.post("/api/vipiska/sample")
async def analyze_sample(session: core.Session = Depends(access)):
    return await core.load_sample(session)


@app.post("/api/vipiska/recheck")
async def recheck(session: core.Session = Depends(access)):
    return await core.recheck_vipiska(session)


@app.post("/api/didox/load")
async def didox_load(session: core.Session = Depends(access)):
    return await core.load_didox(session)


class AccountIn(BaseModel):
    mode: str = "vipiska"
    account: str


@app.post("/api/rows/{row_id}/account")
async def change_account(row_id: str, body: AccountIn, session: core.Session = Depends(access)):
    try:
        row = await core.change_account(session, _mode(body.mode), row_id, body.account)
    except KeyError:
        raise HTTPException(404, "Qator topilmadi") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"row": row, "kpis": core.snapshot(session, body.mode)["kpis"]}


@app.get("/api/rows/{row_id}/inspect")
async def inspect_row(row_id: str, mode: str = "vipiska", session: core.Session = Depends(access)):
    try:
        return await core.preview_payload(session, _mode(mode), row_id)
    except KeyError:
        raise HTTPException(404, "Qator topilmadi") from None


class PostIn(BaseModel):
    mode: str = "vipiska"
    ids: list[str] = Field(default_factory=list, max_length=500)
    force_ids: list[str] = Field(default_factory=list, max_length=50)
    force_reason: str = Field("", max_length=300)


@app.post("/api/post")
async def post_to_1c(body: PostIn, session: core.Session = Depends(access)):
    if not body.ids and not body.force_ids:
        raise HTTPException(400, "Hech qanday qator tanlanmagan")
    if body.force_ids and len(body.force_reason.strip()) < 5:
        raise HTTPException(400, "Majburan yozish uchun sababini yozing (kamida 5 belgi)")
    return await core.post(session, _mode(body.mode), body.ids, body.force_ids, body.force_reason.strip())


# ---------------------------------------------------------------- AI chat
class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    mode: str = "vipiska"


@app.post("/api/chat")
async def chat(body: ChatIn, session: core.Session = Depends(access)):
    return await services_ai.chat(session, body.message, _mode(body.mode))


# ---------------------------------------------------------------- sozlamalar
@app.get("/api/settings")
async def get_settings(_s: core.Session = Depends(access)):
    return config.public_settings()


@app.post("/api/settings")
async def save_settings(body: dict, _s: core.Session = Depends(access)):
    config.save_settings(body)
    return {"settings": config.public_settings(), "onec": await core.get_onec().status(),
            "ai": services_ai.engine_info()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False, proxy_headers=False)
