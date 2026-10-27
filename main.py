"""BUXGALTER AI — FastAPI server (Web App + Telegram Mini App + bot), ko'p firmali va login bilan.

Ishga tushirish:  .venv/bin/python main.py   →  http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

import config
import core
import db
import accounts_uz
import services_ai
import services_dashboard
import services_files1c
import services_notion
import services_rules
from auth import COOKIE, FirmCtx, client_ip, current_user, firm_ctx, is_local, require_owner, set_session_cookie
from demo_data import build_sample_xlsx
from services_1c import ODataError
from services_didox import DidoxError
from services_vipiska import StatementError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("buxgalter")
TEMPLATES = config.BASE_DIR / "templates"
# Sahifa har safar serverdan olinsin — yangilanishdan keyin brauzer eski (xatoli) nusxani ko'rsatmasin
NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    if not config.SAMPLE_VIPISKA.exists():
        build_sample_xlsx(config.SAMPLE_VIPISKA)
        log.info("Namuna vipiska yaratildi: %s", config.SAMPLE_VIPISKA)
    if db.user_count() == 0:
        log.warning("Foydalanuvchi yo'q — http://127.0.0.1:%s/login da \"Egasi\" akkauntini yarating", config.PORT)
    bot_task = None
    if config.TELEGRAM_BOT_TOKEN:
        import bot
        bot_task = asyncio.create_task(bot.run_polling())
        log.info("Telegram bot ishga tushdi (polling)")
    yield
    if bot_task:
        bot_task.cancel()


app = FastAPI(title="BUXGALTER AI", version="2.0.0", lifespan=lifespan)
# Begona domen nomi bilan kelgan so'rovlarni rad etish (DNS rebinding hujumidan himoya)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)

STATIC_DIR = config.BASE_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """Boshqa saytdan yuborilgan o'zgartiruvchi so'rovlarni rad etish (CSRF himoyasi, SameSite cookie'ga qo'shimcha)."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host", ""):
            return JSONResponse({"detail": "So'rov boshqa saytdan keldi — rad etildi"}, status_code=403)
    return await call_next(request)


@app.exception_handler(StatementError)
async def _statement_error(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(db.DBError)
async def _db_error(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(ODataError)
async def _odata_error(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=502)


@app.exception_handler(DidoxError)
async def _didox_error(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=getattr(e, "status", 502))


# ---------------------------------------------------------------- sahifalar
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(TEMPLATES / "index.html", headers=NO_CACHE)


@app.get("/login", include_in_schema=False)
async def login_page():
    return FileResponse(TEMPLATES / "login.html", headers=NO_CACHE)


@app.get("/api/accounts")
async def accounts(_user: dict = Depends(current_user)):
    """Hisoblar rejasi — guruhlar bo'yicha (tanlash ro'yxatlari uchun)."""
    return {"groups": accounts_uz.grouped(), "vat_account": accounts_uz.VAT_ACCOUNT}


@app.get("/api/sample")
async def sample_download():
    if not config.SAMPLE_VIPISKA.exists():
        build_sample_xlsx(config.SAMPLE_VIPISKA)
    return FileResponse(config.SAMPLE_VIPISKA, filename="sample_bank_vipiska.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------- kirish (login)
class LoginIn(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=200)


class SetupIn(LoginIn):
    full_name: str = Field("", max_length=120)


@app.get("/api/auth/state")
async def auth_state(request: Request):
    needs_setup = db.user_count() == 0
    return {"needs_setup": needs_setup, "setup_allowed": needs_setup and is_local(request)}


@app.post("/api/auth/setup")
async def auth_setup(body: SetupIn, request: Request, response: Response):
    if db.user_count() > 0:
        raise HTTPException(409, "Egasi akkaunti allaqachon yaratilgan — tizimga kiring")
    if not is_local(request):
        raise HTTPException(403, "Birinchi akkauntni faqat server o'rnatilgan kompyuterning o'zidan yaratish mumkin")
    user = db.create_user(body.username, body.password, "owner", body.full_name)
    set_session_cookie(response, db.create_web_session(user["id"]), request)
    db.audit(user["id"], None, "setup", username=user["username"])
    return {"user": user}


@app.post("/api/auth/login")
async def auth_login(body: LoginIn, request: Request, response: Response):
    keys = [(f"u:{body.username.strip().lower()}", db.LOGIN_MAX_FAILS), (f"ip:{client_ip(request)}", db.LOGIN_MAX_FAILS_IP)]
    wait = max(db.login_locked_for(k) for k, _ in keys)
    if wait:
        raise HTTPException(429, f"Juda ko'p xato urinish — {wait // 60 + 1} daqiqadan keyin qayta urining")
    user = db.authenticate(body.username, body.password)
    if not user:
        for key, limit in keys:
            db.register_login_failure(key, limit)
        db.audit(None, None, "login_failed", username=body.username.strip()[:64], ip=client_ip(request))
        raise HTTPException(401, "Login yoki parol noto'g'ri")
    db.reset_login_failures(keys[0][0])
    set_session_cookie(response, db.create_web_session(user["id"]), request)
    db.audit(user["id"], None, "login", ip=client_ip(request))
    return {"user": user}


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    db.delete_web_session(request.cookies.get(COOKIE, ""))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(user: dict = Depends(current_user)):
    return {"user": user, "firms": db.list_firms(user), "can_post": user["role"] in ("owner", "accountant"),
            "is_owner": user["role"] == "owner", "roles": db.ROLE_LABELS,
            "telegram_bot": bool(config.TELEGRAM_BOT_TOKEN)}


class PasswordIn(BaseModel):
    old_password: str = Field(..., max_length=200)
    new_password: str = Field(..., max_length=200)


@app.post("/api/auth/password")
async def change_password(body: PasswordIn, request: Request, response: Response, user: dict = Depends(current_user)):
    if not db.authenticate(user["username"], body.old_password):
        raise HTTPException(400, "Joriy parol noto'g'ri")
    db.update_user(user["id"], password=body.new_password)  # barcha qurilmalardan chiqaradi
    set_session_cookie(response, db.create_web_session(user["id"]), request)  # shu qurilmada qoladi
    db.audit(user["id"], None, "password_changed")
    return {"ok": True}


class TgFirmIn(BaseModel):
    firm_id: int


@app.post("/api/auth/tg-firm")
async def telegram_firm(body: TgFirmIn, user: dict = Depends(current_user)):
    """Mini App'da firma almashsa — bot ham shu firma bilan ishlasin (fayl yuborilganda adashmaslik uchun)."""
    if not db.user_can_access(user, body.firm_id):
        raise HTTPException(403, "Bu firmaga kirish huquqingiz yo'q")
    db.set_telegram_firm(user["id"], body.firm_id)
    return {"ok": True}


@app.post("/api/auth/telegram-code")
async def telegram_code(user: dict = Depends(current_user)):
    if not config.TELEGRAM_BOT_TOKEN:
        raise HTTPException(400, "Telegram bot sozlanmagan (.env dagi TELEGRAM_BOT_TOKEN)")
    return {"code": db.create_link_code(user["id"]), "ttl_minutes": int(db.LINK_CODE_TTL.total_seconds() // 60)}


@app.delete("/api/auth/telegram")
async def telegram_unlink(user: dict = Depends(current_user)):
    db.unlink_telegram(user["id"])
    db.audit(user["id"], None, "telegram_unlinked")
    return {"ok": True}


# ---------------------------------------------------------------- firmalar (Egasi)
@app.get("/api/firms")
async def firms_list(user: dict = Depends(current_user)):
    return db.list_firms(user, include_inactive=user["role"] == "owner")


@app.post("/api/firms")
async def firms_create(body: dict, user: dict = Depends(require_owner)):
    firm = db.create_firm(body)
    db.audit(user["id"], firm["id"], "firm_created", name=firm["name"])
    return firm


@app.put("/api/firms/{firm_id}")
async def firms_update(firm_id: int, body: dict, user: dict = Depends(require_owner)):
    firm = db.update_firm(firm_id, body)
    db.audit(user["id"], firm_id, "firm_updated",
             fields=sorted(k for k in body if k in db.FIRM_FIELDS and body[k] != config.MASK))
    return firm


@app.delete("/api/firms/{firm_id}")
async def firms_deactivate(firm_id: int, user: dict = Depends(require_owner)):
    db.deactivate_firm(firm_id)
    db.audit(user["id"], firm_id, "firm_deactivated")
    return {"ok": True}


@app.post("/api/firms/{firm_id}/test")
async def firms_test(firm_id: int, _user: dict = Depends(require_owner)):
    """1C ulanishini tekshirish — bitta urinish (xato login takrorlanmaydi)."""
    onec = core.get_onec(firm_id)
    onec.drop_metadata()  # sozlama o'zgargan bo'lishi mumkin — $metadata qayta o'qiladi
    status = await onec.status()
    meta = await onec.check_metadata() if status["ok"] else None
    return {"onec": status, "metadata": meta}


class WriteAccessIn(BaseModel):
    enabled: bool
    confirm_name: str = Field("", max_length=200)
    limit: int = 1
    create_contracts: bool = False


@app.post("/api/firms/{firm_id}/write-access")
async def firms_write_access(firm_id: int, body: WriteAccessIn, user: dict = Depends(require_owner)):
    """1C ga yozishga ruxsat — faqat "Egasi", yoqishda firma nomini qo'lda yozib tasdiqlaydi."""
    firm = db.get_firm(firm_id)
    if not firm:
        raise HTTPException(404, "Firma topilmadi")
    if body.enabled and body.confirm_name.strip() != firm["name"].strip():
        raise HTTPException(400, f"Tasdiqlash uchun firma nomini aynan yozing: {firm['name']}")
    updated = db.set_write_access(firm_id, body.enabled, body.limit, body.create_contracts)
    db.audit(user["id"], firm_id, "onec_write_access", enabled=body.enabled, limit=updated["onec_write_limit"],
             create_contracts=updated["onec_create_contracts"])
    return updated


@app.post("/api/firms/{firm_id}/edocs/check")
async def edocs_check(firm_id: int, _user: dict = Depends(require_owner)):
    from services_edocs import EdocsError
    from services_edocs_api import check_connection
    settings = db.firm_settings(firm_id)
    if not settings:
        raise HTTPException(404, "Firma topilmadi")
    try:
        result = await check_connection(settings)
    except EdocsError as e:
        raise HTTPException(400, str(e)) from None
    return result


# ---------------------------------------------------------------- foydalanuvchilar (Egasi)
class UserIn(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=200)
    role: str
    full_name: str = Field("", max_length=120)
    firm_ids: list[int] = Field(default_factory=list)


class UserUpdate(BaseModel):
    role: str | None = None
    active: bool | None = None
    full_name: str | None = Field(None, max_length=120)
    firm_ids: list[int] | None = None
    password: str | None = Field(None, max_length=200)


@app.get("/api/users")
async def users_list(_user: dict = Depends(require_owner)):
    return db.list_users()


@app.post("/api/users")
async def users_create(body: UserIn, user: dict = Depends(require_owner)):
    created = db.create_user(body.username, body.password, body.role, body.full_name, body.firm_ids)
    db.audit(user["id"], None, "user_created", username=created["username"], role=created["role"])
    return created


@app.put("/api/users/{user_id}")
async def users_update(user_id: int, body: UserUpdate, user: dict = Depends(require_owner)):
    updated = db.update_user(user_id, **body.model_dump(exclude_none=True))
    changes = sorted(k for k, v in body.model_dump(exclude_none=True).items())
    db.audit(user["id"], None, "user_updated", username=updated["username"], fields=changes)
    return updated


@app.get("/api/audit")
async def audit_list(limit: int = 100, firm_id: int | None = None, _user: dict = Depends(require_owner)):
    return db.list_audit(limit, firm_id)


# ---------------------------------------------------------------- umumiy (AI) sozlamalar (Egasi)
@app.get("/api/settings")
async def get_settings(_user: dict = Depends(require_owner)):
    return config.public_settings()


@app.post("/api/settings")
async def save_settings(body: dict, user: dict = Depends(require_owner)):
    config.save_settings(body)
    db.audit(user["id"], None, "ai_settings_updated", fields=sorted(k for k in body if k in config.DEFAULTS))
    return {"settings": config.public_settings(), "ai": services_ai.engine_info()}


# ---------------------------------------------------------------- firma ichidagi ish
def _mode(mode: str) -> str:
    if mode not in ("vipiska", "didox"):
        raise HTTPException(400, "mode: vipiska | didox")
    return mode


def _with_context(ctx: FirmCtx, snap: dict) -> dict:
    """Vipiska boshqa firmaniki bo'lsa — foydalanuvchi kira oladigan mos firmalarni taklif qilish."""
    owner = snap.get("statement_owner") or {}
    matches = db.find_firms_by_owner_data(owner.get("inn", ""), owner.get("account", ""), owner.get("name", "")) \
        if owner else []
    snap["owner_firms"] = [{"id": f["id"], "name": f["name"], "inn": f["inn"]} for f in matches
                           if f["id"] != ctx.firm_id and db.user_can_access(ctx.user, f["id"])]
    snap["can_post"] = ctx.can_post and not snap.get("read_only")
    return snap


@app.get("/api/status")
async def status(ctx: FirmCtx = Depends(firm_ctx)):
    return {"onec": await core.onec_status(ctx.firm_id), "ai": services_ai.engine_info(), "dedup": True,
            "files": services_files1c.summary(services_files1c.load_store(ctx.firm_id)),
            "firm": db.get_firm(ctx.firm_id), "telegram": bool(config.TELEGRAM_BOT_TOKEN)}


def _source_label(firm: dict) -> str:
    s = db.firm_settings(firm["id"])
    if s["onec_base_url"]:
        return "odata"
    if services_files1c.load_store(firm["id"]):
        return "files"
    return "demo"


@app.get("/api/dashboard")
async def dashboard(ctx: FirmCtx = Depends(firm_ctx)):
    """🏠 Bosh sahifa: 51.10 qoldig'i va sverka, oylik aylanma, debitor/kreditor, bugungi ishlar (faqat o'qish)."""
    session = ctx.session
    status = await core.onec_status(ctx.firm_id)
    onec = await core.readable_onec(session)
    data = await services_dashboard.build(session, onec, status)
    firms = []
    for f in db.list_firms(ctx.user):  # barcha firmalar xulosasi (shu foydalanuvchining jadvallari bo'yicha)
        s = core.get_session(core.session_id(ctx.user["id"], f["id"]), f["id"])
        t = services_dashboard.todo(s.vipiska, s.didox, f["id"])
        firms.append({"id": f["id"], "name": f["name"], "inn": f["inn"], "source": _source_label(f),
                      "rows": len(s.vipiska), "ready": t["bank_ready"] + t["bank_to_export"] if s.vipiska else 0,
                      "discrepancy": t["bank_discrepancy"], "didox_pending": t["didox_pending"],
                      "statement": s.statement.source if s.statement else ""})
    data["firms"] = firms
    data["can_post"] = ctx.can_post
    return data


@app.get("/api/balances")
async def balances(account: str = "40.10", ctx: FirmCtx = Depends(firm_ctx)):
    """Tanlangan hisob-kitob schyoti (40.10, 63.10, 60.10, 43.10 …) qoldig'i kontragentlar bo'yicha — faqat o'qish."""
    from services_1c import ACCOUNT_RE, fmt_account
    if not ACCOUNT_RE.match(account or ""):
        raise HTTPException(400, "Schyot formati: 60.10 ko'rinishida")
    onec = await core.readable_onec(ctx.session)
    data = await services_dashboard.account_details(onec, fmt_account(account))
    data["choices"] = [{"account": c, "title": t, "side": sd} for c, t, sd in services_dashboard.SETTLEMENT_ACCOUNTS]
    return data


@app.get("/api/stock")
async def stock(source: str = "1c", ctx: FirmCtx = Depends(firm_ctx)):
    """📦 Ombor qoldiqlari, faqat o'qish. source: 1c — 1C provodkalaridan (29.10, 10.xx, 28.10);
    notion — firmaning Notion sklad bazasidan; compare — ikkalasini tovar nomi bo'yicha solishtirish."""
    if source not in ("1c", "notion", "compare"):
        raise HTTPException(400, "source: 1c, notion yoki compare")
    notion = None
    if source in ("notion", "compare"):
        notion = await services_notion.stock(core.firm_settings(ctx.firm_id))
        if source == "notion":
            notion.pop("_items", None)
            return {**notion, "source": "notion"}
    onec = await services_dashboard.stock(await core.readable_onec(ctx.session))
    if source == "1c":
        return {**onec, "source": "1c"}
    cmp = services_notion.compare(onec, notion) if notion.get("_items") is not None else {"rows": [], "counts": {}}
    return {"source": "compare", **cmp, "onec_mode": onec.get("onec_mode"),
            "notion": {k: notion.get(k) for k in ("configured", "live", "fetched_at", "warning", "error", "count", "token")}}


@app.get("/api/1c/metadata")
async def onec_metadata(ctx: FirmCtx = Depends(firm_ctx)):
    return await core.onec_for(ctx.session).check_metadata()


@app.get("/api/session")
async def get_state(mode: str = "vipiska", ctx: FirmCtx = Depends(firm_ctx)):
    return _with_context(ctx, core.snapshot(ctx.session, _mode(mode)))


@app.post("/api/vipiska/upload")
async def upload_vipiska(file: UploadFile = File(...), ctx: FirmCtx = Depends(firm_ctx)):
    content = await file.read(10 * 1024 * 1024 + 1)
    snap = await core.load_statement(ctx.session, file.filename or "vipiska.xlsx", content)
    db.audit(ctx.user["id"], ctx.firm_id, "vipiska_uploaded", file=file.filename, rows=len(snap["rows"]))
    return _with_context(ctx, snap)


@app.post("/api/vipiska/sample")
async def analyze_sample(ctx: FirmCtx = Depends(firm_ctx)):
    return _with_context(ctx, await core.load_sample(ctx.session))


@app.delete("/api/vipiska")
async def clear_vipiska(ctx: FirmCtx = Depends(firm_ctx)):
    """Jadvalni tozalash (xato yoki eski vipiska yuklanganda) — 1C ga tegilmaydi."""
    source = ctx.session.statement.source if ctx.session.statement else ""
    snap = await core.clear_statement(ctx.session)
    db.audit(ctx.user["id"], ctx.firm_id, "vipiska_cleared", file=source)
    return _with_context(ctx, snap)


@app.post("/api/vipiska/recheck")
async def recheck(ctx: FirmCtx = Depends(firm_ctx)):
    return _with_context(ctx, await core.recheck_vipiska(ctx.session))


@app.post("/api/didox/load")
async def didox_load(date_from: str = "", date_to: str = "", ctx: FirmCtx = Depends(firm_ctx)):
    snap = await core.load_didox(ctx.session, date_from, date_to)
    db.audit(ctx.user["id"], ctx.firm_id, "ehf_read", rows=len(snap["rows"]))
    return _with_context(ctx, snap)


class AccountIn(BaseModel):
    mode: str = "vipiska"
    account: str


@app.post("/api/rows/{row_id}/account")
async def change_account(row_id: str, body: AccountIn, ctx: FirmCtx = Depends(firm_ctx)):
    ctx.require_post()
    try:
        row = await core.change_account(ctx.session, _mode(body.mode), row_id, body.account)
    except KeyError:
        raise HTTPException(404, "Qator topilmadi") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"row": row, "kpis": core.snapshot(ctx.session, body.mode)["kpis"]}


@app.get("/api/rows/{row_id}/inspect")
async def inspect_row(row_id: str, mode: str = "vipiska", ctx: FirmCtx = Depends(firm_ctx)):
    try:
        return await core.preview_payload(ctx.session, _mode(mode), row_id)
    except KeyError:
        raise HTTPException(404, "Qator topilmadi") from None


class PostIn(BaseModel):
    mode: str = "vipiska"
    # Qatorlar qaysi firmaning jadvalidan olingan. So'rov boshqa firmaga ketsa (firma almashgan, eski oyna,
    # kechikkan javob) — rad etiladi: bir firmaning to'lovlari boshqa firmaning 1C siga tushmasligi uchun.
    expected_firm_id: int | None = None
    ids: list[str] = Field(default_factory=list, max_length=500)
    force_ids: list[str] = Field(default_factory=list, max_length=50)
    force_reason: str = Field("", max_length=300)


@app.post("/api/post")
async def post_to_1c(body: PostIn, ctx: FirmCtx = Depends(firm_ctx)):
    ctx.require_post()
    if body.expected_firm_id is not None and body.expected_firm_id != ctx.firm_id:
        raise HTTPException(409, "Firma almashgan — qatorlar boshqa firmaga tegishli. Sahifani yangilang.")
    if not body.ids and not body.force_ids:
        raise HTTPException(400, "Hech qanday qator tanlanmagan")
    if body.force_ids and len(body.force_reason.strip()) < 5:
        raise HTTPException(400, "Majburan yozish uchun sababini yozing (kamida 5 belgi)")
    res = await core.post(ctx.session, _mode(body.mode), body.ids, body.force_ids, body.force_reason.strip(),
                          by=ctx.user["username"])
    by_id = {r["id"]: r for r in res["rows"]}
    db.audit(ctx.user["id"], ctx.firm_id, "posted_to_1c", mode=body.mode, ok=res["ok"], failed=res["failed"],
             documents=[{"number": by_id.get(r["id"], {}).get("number"), "1c": r.get("number")}
                        for r in res["results"] if r["ok"]],
             forced=body.force_ids or None, reason=body.force_reason.strip() or None)
    return _with_context(ctx, res)


# ---------------------------------------------------------------- 📚 hisob qoidalari (firma bo'yicha)
@app.get("/api/rules")
async def rules_list(ctx: FirmCtx = Depends(firm_ctx)):
    rules = db.list_rules(ctx.firm_id)
    return {"rules": rules, "can_edit": ctx.can_post, "summary": {
        "total": len(rules), "locked": sum(r["locked"] for r in rules), "conflicts": sum(r["conflict"] and not r["locked"] for r in rules),  # hal qilinmaganlari
        "counterparty": sum(r["kind"] == "counterparty" for r in rules),
        "keyword": sum(r["kind"] == "keyword" for r in rules)}}


class LearnIn(BaseModel):
    months: int = Field(12, ge=1, le=36)


@app.post("/api/rules/learn")
async def rules_learn(body: LearnIn, ctx: FirmCtx = Depends(firm_ctx)):
    """1C dagi o'tkazilgan hujjatlarni o'qib, qoidalar jadvalini yangilaydi (1C ga hech narsa yozmaydi)."""
    ctx.require_post()
    onec = await core.readable_onec(ctx.session)  # OData ishlamasa — yuklangan 1C eksport fayllaridan
    learned, summary = await services_rules.learn_from_1c(onec, body.months)
    summary["source"] = onec.mode
    stats = db.upsert_learned_rules(ctx.firm_id, learned)
    db.audit(ctx.user["id"], ctx.firm_id, "rules_learned", **summary, **stats)
    return {"summary": {**summary, **stats}}


class RuleIn(BaseModel):
    kind: str = "counterparty"
    match: str = Field(..., max_length=100)
    name: str = Field("", max_length=200)
    direction: str = ""
    doc_kind: str = "bank"
    account: str = Field(..., max_length=10)
    operation: str = Field("", max_length=100)
    locked: bool = True


@app.post("/api/rules")
async def rules_create(body: RuleIn, ctx: FirmCtx = Depends(firm_ctx)):
    ctx.require_post()
    rule = db.save_rule(ctx.firm_id, body.model_dump(), ctx.user["username"], locked=body.locked, source="manual")
    db.audit(ctx.user["id"], ctx.firm_id, "rule_saved", match=rule["match"], account=rule["account"], locked=rule["locked"])
    return rule


@app.put("/api/rules/{rule_id}")
async def rules_update(rule_id: int, body: dict, ctx: FirmCtx = Depends(firm_ctx)):
    ctx.require_post()
    rule = db.update_rule(rule_id, ctx.firm_id, body, ctx.user["username"])
    db.audit(ctx.user["id"], ctx.firm_id, "rule_updated", match=rule["match"], account=rule["account"],
             locked=rule["locked"])
    return rule


@app.delete("/api/rules/{rule_id}")
async def rules_delete(rule_id: int, ctx: FirmCtx = Depends(firm_ctx)):
    ctx.require_post()
    rule = db.get_rule(rule_id)
    if not rule or rule["firm_id"] != ctx.firm_id:
        raise HTTPException(404, "Qoida topilmadi")
    db.delete_rule(rule_id, ctx.firm_id)
    db.audit(ctx.user["id"], ctx.firm_id, "rule_deleted", match=rule["match"], account=rule["account"])
    return {"ok": True}


class RememberIn(BaseModel):
    mode: str = "vipiska"


@app.post("/api/rows/{row_id}/remember")
async def remember_row(row_id: str, body: RememberIn, ctx: FirmCtx = Depends(firm_ctx)):
    """Vipiskada tanlangan schyotni shu kontragent uchun qat'iy qoida qilib eslab qolish."""
    ctx.require_post()
    try:
        rule = await core.remember_row(ctx.session, _mode(body.mode), row_id, ctx.user["username"])
    except KeyError:
        raise HTTPException(404, "Qator topilmadi") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    db.audit(ctx.user["id"], ctx.firm_id, "rule_remembered", match=rule["match"], name=rule["name"],
             account=rule["account"])
    return {"rule": rule, **_with_context(ctx, core.snapshot(ctx.session, body.mode))}


@app.get("/api/rules/export")
async def rules_export(ctx: FirmCtx = Depends(firm_ctx)):
    firm = db.get_firm(ctx.firm_id)
    content = services_rules.export_xlsx(db.list_rules(ctx.firm_id), firm["name"])
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="hisob_qoidalari_firma{ctx.firm_id}.xlsx"'})


@app.post("/api/rules/import")
async def rules_import(file: UploadFile = File(...), ctx: FirmCtx = Depends(firm_ctx)):
    ctx.require_post()
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "Fayl juda katta (5 MB gacha)")
    try:
        items, errors = services_rules.parse_import_xlsx(content)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    saved = 0
    for item in items:
        try:
            db.save_rule(ctx.firm_id, item, ctx.user["username"], locked=item["locked"], source="import")
            saved += 1
        except db.DBError as e:
            errors.append(f"{item['_row']}-qator: {e}")
    db.audit(ctx.user["id"], ctx.firm_id, "rules_imported", file=file.filename, saved=saved, errors=len(errors))
    return {"saved": saved, "errors": errors}


# ---------------------------------------------------------------- 📁 1C bilan fayl orqali (OData ishlamaganda)
MAX_EXPORT_FILE = 20 * 1024 * 1024


@app.get("/api/1c-files")
async def onec_files(ctx: FirmCtx = Depends(firm_ctx)):
    return services_files1c.summary(services_files1c.load_store(ctx.firm_id))


@app.post("/api/1c-files/upload")
async def onec_files_upload(files: list[UploadFile] = File(...), ctx: FirmCtx = Depends(firm_ctx)):
    ctx.require_post()
    if len(files) > 10:
        raise HTTPException(400, "Bir martada 10 tagacha fayl")
    items = []
    for f in files:
        content = await f.read(MAX_EXPORT_FILE + 1)
        if len(content) > MAX_EXPORT_FILE:
            raise HTTPException(400, f"{f.filename}: fayl juda katta (20 MB gacha)")
        items.append((f.filename or "1c_export.xlsx", content))
    res = await core.load_1c_exports(ctx.session, items, ctx.user["username"])
    db.audit(ctx.user["id"], ctx.firm_id, "onec_files_uploaded",
             files=[{"file": r["file"], "kind": r.get("kind"), "count": r.get("count"), "error": r.get("error")}
                    for r in res["results"]], learned=res["learned"])
    if res["snapshot"]:
        res["snapshot"] = _with_context(ctx, res["snapshot"])
    return res


@app.delete("/api/1c-files")
async def onec_files_clear(ctx: FirmCtx = Depends(firm_ctx)):
    ctx.require_post()
    core.clear_1c_exports(ctx.session)
    db.audit(ctx.user["id"], ctx.firm_id, "onec_files_cleared")
    return {"ok": True}


@app.get("/api/vipiska/checklist")
async def vipiska_checklist(ctx: FirmCtx = Depends(firm_ctx)):
    """📋 Tekshiruv ro'yxati (Excel): har qator qaysi schyot, avans, shartnomaga tushishi kerak — import faylini
    1C ga yuklagandan keyin solishtirish uchun (import faylida schyot yo'q)."""
    session = ctx.session
    if not session.vipiska:
        raise HTTPException(400, "Avval vipiskani yuklang")
    firm = db.get_firm(ctx.firm_id)
    content = services_files1c.checklist_xlsx(session.vipiska, firm["name"],
                                              session.statement.source if session.statement else "")
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="tekshiruv_royxati_firma{ctx.firm_id}.xlsx"'})


@app.get("/api/drafts")
async def drafts_list(ctx: FirmCtx = Depends(firm_ctx)):
    """Dastur 1C da yaratgan qoralamalar (shu firma)."""
    return db.list_drafts(ctx.firm_id)


@app.post("/api/drafts/{draft_id}/undo")
async def drafts_undo(draft_id: int, ctx: FirmCtx = Depends(firm_ctx)):
    """Dastur yaratgan, hali o'tkazilmagan qoralamani 1C da "o'chirishga belgilash"."""
    ctx.require_post()
    try:
        draft = await core.undo_draft(ctx.session, draft_id, ctx.user["username"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    db.audit(ctx.user["id"], ctx.firm_id, "onec_draft_undone", number=draft["number"], ref=draft["ref"],
             amount=draft["amount"])
    return {"draft": draft, "snapshot": _with_context(ctx, core.snapshot(ctx.session, "vipiska"))}


class CounterpartyIn(BaseModel):
    row_id: str = Field(..., max_length=40)
    name: str = Field(..., max_length=150)
    full_name: str = Field("", max_length=250)
    inn: str = Field(..., max_length=20)
    person: str = "ЮридическоеЛицо"
    folder: str = Field("", max_length=40)
    account_no: str = Field("", max_length=30)
    mfo: str = Field("", max_length=10)
    create_contract: bool = True
    contract_number: str = Field("", max_length=30)
    contract_date: str = Field("", max_length=10)
    expected_firm_id: int | None = None


@app.get("/api/counterparties/prefill")
async def counterparty_prefill(row_id: str, ctx: FirmCtx = Depends(firm_ctx)):
    """"➕ 1C da kontragent yaratish" oynasi uchun: vipiskadagi ma'lumot + 1C dagi guruhlar, bank, bor yozuvlar."""
    try:
        return await core.counterparty_prefill(ctx.session, row_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@app.post("/api/counterparties")
async def counterparty_create(body: CounterpartyIn, ctx: FirmCtx = Depends(firm_ctx)):
    """Kontragent (+ bank hisobi, + shartnoma) 1C da — faqat yozish ruxsati bilan, takror yaratilmaydi."""
    ctx.require_post()
    if body.expected_firm_id is not None and body.expected_firm_id != ctx.firm_id:
        raise HTTPException(409, "Firma almashgan — sahifani yangilang.")
    try:
        created = await core.create_counterparty(ctx.session, body.row_id, body.model_dump(), ctx.user["username"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    db.audit(ctx.user["id"], ctx.firm_id, "onec_counterparty_created", name=body.name, inn=body.inn,
             bank_account=created.get("bank_account"), contract=created.get("contract"))
    return {"created": created, "snapshot": _with_context(ctx, core.snapshot(ctx.session, "vipiska"))}


class ExportIn(BaseModel):
    expected_firm_id: int | None = None
    ids: list[str] = Field(default_factory=list, max_length=1000)


@app.post("/api/vipiska/export-1c")
async def export_1c(body: ExportIn, ctx: FirmCtx = Depends(firm_ctx)):
    """1C ga import fayli (1CClientBankExchange). 1C ga o'zimiz hech narsa yozmaymiz — faylni buxgalter yuklaydi."""
    ctx.require_post()
    if body.expected_firm_id is not None and body.expected_firm_id != ctx.firm_id:
        raise HTTPException(409, "Firma almashgan — qatorlar boshqa firmaga tegishli. Sahifani yangilang.")
    try:
        async with ctx.session.lock:
            content, rows = core.export_client_bank(ctx.session, body.ids or None)
    except (ValueError, services_files1c.ExportFileError) as e:
        raise HTTPException(400, str(e)) from None
    db.audit(ctx.user["id"], ctx.firm_id, "onec_import_file", rows=len(rows),
             sum=round(sum(r["amount"] for r in rows), 2), numbers=[r["number"] for r in rows][:200])
    return Response(content, media_type="text/plain; charset=windows-1251",
                    headers={"Content-Disposition": f'attachment; filename="{core.export_filename(ctx.firm_id, rows)}"',
                             "X-Rows": str(len(rows))})


# ---------------------------------------------------------------- AI chat
class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    mode: str = "vipiska"


@app.post("/api/chat")
async def chat(body: ChatIn, ctx: FirmCtx = Depends(firm_ctx)):
    res = await services_ai.chat(ctx.session, body.message, _mode(body.mode))
    core.save_session(ctx.session)
    if not ctx.can_post:
        res["cards"] = [c for c in res["cards"] if c["type"] != "confirm_post"]
    return res


@app.post("/api/chat/stream")
async def chat_stream_endpoint(body: ChatIn, ctx: FirmCtx = Depends(firm_ctx)):
    """AI javobini Server-Sent Events (SSE) oqimi sifatida uzatish."""
    async def sse_gen():
        async for chunk in services_ai.chat_stream(ctx.session, body.message, _mode(body.mode)):
            if not ctx.can_post and '"cards"' in chunk:  # /api/chat dagi kabi: kuzatuvchiga kiritish kartasi yo'q
                event = json.loads(chunk)
                event["cards"] = [c for c in event.get("cards", []) if c.get("type") != "confirm_post"]
                chunk = json.dumps(event, ensure_ascii=False)
            yield f"data: {chunk.strip()}\n\n"
        core.save_session(ctx.session)  # suhbat tarixi qayta ishga tushganda yo'qolmasin
    return StreamingResponse(sse_gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False, proxy_headers=False)
