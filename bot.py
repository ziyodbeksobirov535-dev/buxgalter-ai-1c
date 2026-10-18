"""BUXGALTER AI — Telegram bot (aiogram 3) + Telegram Mini App tugmasi.

Odatda main.py bilan birga ishga tushadi (bitta jarayon — Web App va bot sessiyalari umumiy).
Alohida ishga tushirish:  .venv/bin/python bot.py
"""
from __future__ import annotations

import asyncio
import html
import io
import logging
import re

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           MenuButtonWebApp, Message, WebAppInfo)

import auth
import config
import core
import db
import services_ai
import services_files1c as files1c
from services_1c import ODataError
from services_vipiska import StatementError

log = logging.getLogger("buxgalter.bot")
router = Router()

WEBAPP_TEXT = "📱 Buxgalter AI Web App'ni ochish"


def money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ") + " so'm"


def md_to_html(text: str) -> str:
    """Gemini Markdown → Telegram HTML (xavfsiz)."""
    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", t)
    t = re.sub(r"(?m)^- ", "• ", t)
    return t[:4000]


def webapp_button(text: str = WEBAPP_TEXT) -> InlineKeyboardButton | None:
    if config.WEBAPP_URL.startswith("https://"):
        return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=config.WEBAPP_URL))
    return None


async def deny(m: Message) -> None:
    await m.answer("🔐 Telegram akkauntingiz BUXGALTER AI ga bog'lanmagan.\n\n"
                   "1) Web App'ga login va parol bilan kiring\n"
                   "2) ⚙️ Sozlamalar → <b>Telegram'ni bog'lash</b> → 6 xonali kod oling\n"
                   "3) Kodni shu yerga yuboring: <code>/link 123456</code>")


def user_of(tg_id: int | None) -> dict | None:
    return db.get_user_by_telegram(tg_id) if tg_id else None


def firm_of(user: dict) -> dict | None:
    """Foydalanuvchining Telegram'dagi joriy firmasi (tanlanmagan bo'lsa — birinchi ruxsat etilgani)."""
    firms = db.list_firms(user)
    if not firms:
        return None
    chosen = next((f for f in firms if f["id"] == user.get("tg_firm_id")), firms[0])
    if chosen["id"] != user.get("tg_firm_id"):
        db.set_telegram_firm(user["id"], chosen["id"])
    return chosen


def context_of(tg_id: int | None) -> tuple[dict, dict, core.Session] | None:
    user = user_of(tg_id)
    if not user:
        return None
    firm = firm_of(user)
    if not firm:
        return None
    return user, firm, core.get_session(core.session_id(user["id"], firm["id"]), firm["id"])


async def need_context(m: Message) -> tuple[dict, dict, core.Session] | None:
    ctx = context_of(m.from_user.id if m.from_user else None)
    if ctx is None:
        if user_of(m.from_user.id if m.from_user else None):
            await m.answer("⚠️ Sizga hech qanday firma biriktirilmagan — \"Egasi\"ga murojaat qiling.")
        else:
            await deny(m)
    return ctx


def file_mode(snap: dict) -> bool:
    """1C ga to'g'ridan-to'g'ri ulanmagan: eksport fayllari bo'yicha yoki ko'rish rejimida tahlil qilingan."""
    return bool(snap.get("files_mode") or snap.get("offline"))


def source_label(snap: dict) -> str:
    if snap.get("files_mode"):
        return "eksport fayllari (OData'siz)"
    if snap.get("offline"):
        return "ulanmagan — ko'rish rejimi"
    return "Demo baza" if snap.get("onec_mode") == "demo" else "OData"


def summary_text(snap: dict, firm: dict, title: str = "🏦 Vipiska tahlili") -> str:
    k = snap["kpis"]
    lines = [f"🏢 <b>{html.escape(firm['name'])}</b>",
             f"<b>{title}</b>: {html.escape(snap.get('source') or '')}",
             f"1C: {source_label(snap)}", "",
             f"Jami qatorlar: <b>{k['total']}</b>",
             f"🛡️ Dublikat (1C da bor, bloklandi): <b>{k['duplicates']}</b>",
             f"🟡 Yangi (kiritishga tayyor): <b>{k['ready']}</b> — {money(k['ready_sum'])}",
             f"🔴 Farq bor: <b>{k['discrepancies']}</b>",
             f"🧠 Tarixdan aniqlangan schyotlar: <b>{k['learned']}/{k['learned_base']} ({k['learned_pct']}%)</b>"]
    if k["posted"]:
        lines.append(f"✅ Qoralama yozilgan: <b>{k['posted']}</b>")
    for w in snap.get("warnings", [])[:5]:
        lines.append(f"⚠️ {html.escape(w)}")
    news = [r for r in snap["rows"] if (files1c.exportable(r) if file_mode(snap) else r["selectable"])]
    if news:
        lines += ["", "<b>Yangi qatorlar:</b>"]
        for r in news[:15]:
            icon = "🧠" if r["account_source"] == "history" else "🤖"
            arrow = "⬇️" if r["direction"] == "kirim" else "⬆️"
            lines.append(f"{arrow} №{html.escape(r['number'])} {html.escape(r['counterparty_name'][:32])} — "
                         f"{money(r['amount'])} → <b>{r['account']}</b> {icon}")
    shown = set()
    for r in snap["rows"]:
        if r["status"] == "discrepancy":
            lines.append(f"🔴 №{html.escape(r['number'])} {html.escape(r['counterparty_name'][:32])}: "
                         f"{html.escape('; '.join(r['diffs']))}")
        elif r["blockers"] and r["blockers"][0] not in shown:
            shown.add(r["blockers"][0])
            lines.append(f"⚠️ №{html.escape(r['number'])}: {html.escape(r['blockers'][0])}")
    return "\n".join(lines)[:4000]


def post_button(session: core.Session, mode: str, ids: list[str], text: str) -> InlineKeyboardButton:
    """Tugma aniq firma + aniq qatorlarga bog'lanadi. Firma almashsa yoki yangi vipiska yuklansa ham,
    eski tugma faqat o'zi ko'rsatgan qatorlarni o'z firmasiga yozadi (yoki eskirgan deb rad etiladi)."""
    token = core.remember_batch(session, mode, ids)
    return InlineKeyboardButton(text=text, callback_data=f"post:{session.firm_id}:{token}")


def result_keyboard(snap: dict, user: dict, session: core.Session, mode: str = "vipiska") -> InlineKeyboardMarkup:
    rows = []
    if mode == "vipiska" and file_mode(snap):  # 1C ga ulanmagan — import fayli (1C da "Загрузить")
        n = sum(1 for r in snap["rows"] if files1c.exportable(r) and not r.get("exported_at"))
        if n and user["role"] in auth.CAN_POST:
            rows.append([InlineKeyboardButton(text=f"📤 1C import fayli ({n} ta yangi)",
                                              callback_data=f"export:{session.firm_id}")])
    else:
        ready_ids = [r["id"] for r in snap["rows"] if r["selectable"]]
        write = snap.get("write") or {}
        if snap.get("onec_mode") == "live":  # haqiqiy 1C: faqat ruxsat bilan va limit doirasida
            ready_ids = ready_ids[:write.get("limit", 1)] if write.get("enabled") else []
        if ready_ids and user["role"] in auth.CAN_POST:
            rows.append([post_button(session, mode, ready_ids, f"✅ Yangilarini 1C ga kiritish ({len(ready_ids)})")])
    btn = webapp_button("📱 Web App'da ko'rish")
    if btn:
        rows.append([btn])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def cmd_start(m: Message) -> None:
    ctx = await need_context(m)
    if not ctx:
        return
    user, firm, _session = ctx
    kb = []
    btn = webapp_button()
    if btn:
        kb.append([btn])
    if core.get_onec(firm["id"]).mode == "demo":  # namuna faqat Demo firmada
        kb.append([InlineKeyboardButton(text="🧪 Test vipiskani tahlil qilish", callback_data="sample")])
    kb.append([InlineKeyboardButton(text="📑 Didox EHF vs 1C", callback_data="didox")])
    kb.append([InlineKeyboardButton(text=f"🏢 Firma: {firm['name'][:40]}", callback_data="firms")])
    note = "" if btn else ("\n\n<i>Web App tugmasi uchun .env da WEBAPP_URL=https://… (HTTPS tunnel) ni sozlang.</i>")
    await m.answer(
        f"🏦 <b>BUXGALTER AI</b> — xush kelibsiz, {html.escape(user['full_name'] or user['username'])} "
        f"({user['role_label']})!\n\n"
        f"Joriy firma: 🏢 <b>{html.escape(firm['name'])}</b> (/firma — almashtirish)\n\n"
        "• Bank <b>vipiska</b> faylini (.xlsx / .csv / 1CClientBankExchange .txt) shu chatga yuboring\n"
        "• Har bir qatorni 1C da <b>dublikatga</b> tekshiraman 🛡️\n"
        "• Schyotlarni <b>oldingi 1C yozuvlaridan</b> o'rganaman 🧠\n"
        "• Yangilarini faqat <b>qoralama</b> sifatida kiritaman ✅\n\n"
        "/namuna — test vipiska fayli · /firma — firmani tanlash\n"
        "Yoki savol yozing: <i>\"Kunlik pul oqimi tahlili\"</i>" + note,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


async def _link(m: Message, code: str) -> None:
    tg_id = m.from_user.id
    key = f"tg:{tg_id}"
    if db.login_locked_for(key):
        await m.answer("⛔ Juda ko'p xato urinish. 15 daqiqadan keyin qayta urining.")
        return
    user = db.consume_link_code(code, tg_id)
    if not user:
        db.register_login_failure(key, db.LOGIN_MAX_FAILS)
        await m.answer("⚠️ Kod noto'g'ri yoki muddati o'tgan. Web App'dan yangi kod oling.")
        return
    db.reset_login_failures(key)
    db.audit(user["id"], None, "telegram_linked", telegram_id=tg_id)
    await m.answer(f"✅ Bog'landi: <b>{html.escape(user['username'])}</b> ({user['role_label']}). /start ni bosing.")


@router.message(Command("link"))
async def cmd_link(m: Message, command: CommandObject) -> None:
    await _link(m, command.args or "")


@router.message(F.text.regexp(r"^\s*\d{6}\s*$"))
async def plain_code(m: Message) -> None:
    if user_of(m.from_user.id):
        return await on_text(m)
    await _link(m, m.text)


def firms_keyboard(user: dict, current_id: int | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("✅ " if f["id"] == current_id else "🏢 ") + f"{f['name'][:45]}"
                              + (f" · {f['inn']}" if f["inn"] else ""), callback_data=f"firm:{f['id']}")]
        for f in db.list_firms(user)])


@router.message(Command("firma"))
async def cmd_firma(m: Message) -> None:
    ctx = await need_context(m)
    if ctx:
        user, firm, _ = ctx
        await m.answer("🏢 Qaysi firma bilan ishlaymiz?", reply_markup=firms_keyboard(user, firm["id"]))


@router.callback_query(F.data == "firms")
async def cb_firms(c: CallbackQuery) -> None:
    ctx = context_of(c.from_user.id)
    if not ctx:
        return await c.answer("Ruxsat yo'q", show_alert=True)
    await c.answer()
    await c.message.answer("🏢 Qaysi firma bilan ishlaymiz?", reply_markup=firms_keyboard(ctx[0], ctx[1]["id"]))


@router.callback_query(F.data.startswith("firm:"))
async def cb_firm_select(c: CallbackQuery) -> None:
    user = user_of(c.from_user.id)
    firm_id = int(c.data.split(":", 1)[1])
    if not user or not db.user_can_access(user, firm_id):
        return await c.answer("Bu firmaga kirish huquqingiz yo'q", show_alert=True)
    db.set_telegram_firm(user["id"], firm_id)
    firm = db.get_firm(firm_id)
    await c.answer(f"Tanlandi: {firm['name']}")
    await c.message.edit_text(f"✅ Joriy firma: 🏢 <b>{html.escape(firm['name'])}</b>")


@router.message(Command("namuna"))
async def cmd_sample(m: Message) -> None:
    if not await need_context(m):
        return
    if not config.SAMPLE_VIPISKA.exists():
        from demo_data import build_sample_xlsx
        build_sample_xlsx(config.SAMPLE_VIPISKA)
    await m.answer_document(BufferedInputFile(config.SAMPLE_VIPISKA.read_bytes(), "sample_bank_vipiska.xlsx"),
                            caption="🧪 Test vipiska (Demo firma uchun): 14 qator — 3 ta dublikat, 1 ta farqli, "
                                    "1 ta ish haqi (vedomost bilan) va 9 ta yangi.\n"
                                    "Demo firmada shu faylni menga qayta yuboring — tahlil qilaman.")


@router.callback_query(F.data == "sample")
async def cb_sample(c: CallbackQuery) -> None:
    ctx = context_of(c.from_user.id)
    if not ctx:
        return await c.answer("Ruxsat yo'q", show_alert=True)
    user, firm, session = ctx
    await c.answer("Tahlil qilinmoqda…")
    try:
        snap = await core.load_sample(session)
    except (StatementError, ODataError) as e:
        return await c.message.answer(f"⚠️ {html.escape(str(e))}")
    await c.message.answer(summary_text(snap, firm), reply_markup=result_keyboard(snap, user, session))


@router.callback_query(F.data == "didox")
async def cb_didox(c: CallbackQuery) -> None:
    ctx = context_of(c.from_user.id)
    if not ctx:
        return await c.answer("Ruxsat yo'q", show_alert=True)
    user, firm, session = ctx
    await c.answer("Didox tekshirilmoqda…")
    try:
        snap = await core.load_didox(session)
    except Exception as e:  # noqa: BLE001
        return await c.message.answer(f"⚠️ {html.escape(str(e))}")
    await c.message.answer(summary_text(snap, firm, "📑 Didox EHF vs 1C"),
                           reply_markup=result_keyboard(snap, user, session, "didox"))


@router.message(F.document)
async def on_document(m: Message, bot: Bot) -> None:
    ctx = await need_context(m)
    if not ctx:
        return
    user, firm, session = ctx
    doc = m.document
    if doc.file_size and doc.file_size > 10 * 1024 * 1024:
        return await m.answer("⚠️ Fayl juda katta (10 MB gacha).")
    wait = await m.answer(f"⏳ 🏢 {html.escape(firm['name'])}: vipiska o'qilmoqda, 1C bilan solishtirilmoqda…")
    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    try:
        snap = await core.load_statement(session, doc.file_name or "vipiska.xlsx", buf.getvalue())
    except (StatementError, ODataError) as e:
        return await wait.edit_text(f"⚠️ {html.escape(str(e))}")
    db.audit(user["id"], firm["id"], "vipiska_uploaded", file=doc.file_name, rows=len(snap["rows"]), via="telegram")
    await wait.edit_text(summary_text(snap, firm), reply_markup=result_keyboard(snap, user, session))


@router.callback_query(F.data.startswith("post:") | (F.data == "confirm"))
async def cb_post(c: CallbackQuery) -> None:
    user = user_of(c.from_user.id)
    if not user:
        return await c.answer("Ruxsat yo'q", show_alert=True)
    parts = (c.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit():  # eski formatdagi (firmasiz) tugma
        return await c.answer("Bu tugma eskirgan — vipiskani qayta yuboring", show_alert=True)
    firm_id, token = int(parts[1]), parts[2]
    if not db.user_can_access(user, firm_id):
        return await c.answer("Bu firmaga kirish huquqingiz yo'q", show_alert=True)
    if user["role"] not in auth.CAN_POST:
        return await c.answer("\"Kuzatuvchi\" roli 1C ga yoza olmaydi", show_alert=True)
    firm = db.get_firm(firm_id)
    session = core.get_session(core.session_id(user["id"], firm_id), firm_id)
    batch = session.batches.pop(token, None)
    if not batch:
        return await c.answer("Bu tugma eskirgan yoki allaqachon ishlatilgan — vipiskani qayta yuboring",
                              show_alert=True)
    mode, ids = batch["mode"], batch["ids"]
    if not ids:
        return await c.answer("Kiritish uchun yangi qator yo'q", show_alert=True)
    await c.answer("1C ga yozilmoqda…")
    await c.message.edit_reply_markup(reply_markup=None)  # ikki marta bosishdan himoya
    try:
        res = await core.post(session, mode, ids, by=user["username"])
    except ODataError as e:
        await c.message.answer(f"⚠️ {html.escape(str(e))}")
        return
    db.audit(user["id"], firm["id"], "posted_to_1c", mode=mode, ok=res["ok"], failed=res["failed"], via="telegram")
    lines = [f"🏢 <b>{html.escape(firm['name'])}</b>",
             f"<b>1C natijasi</b>: ✅ {res['ok']} ta qoralama yaratildi, ⛔ {res['failed']} ta o'tkazib yuborildi", ""]
    by_id = {r["id"]: r for r in res["rows"]}
    for r in res["results"]:
        row = by_id.get(r["id"], {})
        lines.append(f"{'✅' if r['ok'] else '⛔'} №{html.escape(str(row.get('number', '')))} — {html.escape(r['message'])}")
    lines.append("\n<i>Hujjatlar 1C da qoralama (Posted=false). Tekshirib, 1C ning o'zida o'tkazing.</i>")
    kb = [[b]] if (b := webapp_button("📱 Web App'da ko'rish")) else []
    await c.message.answer("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data.startswith("export:"))
async def cb_export(c: CallbackQuery) -> None:
    """1C import fayli (1CClientBankExchange) — chatga fayl sifatida. 1C ga dastur o'zi hech narsa yozmaydi."""
    user = user_of(c.from_user.id)
    parts = (c.data or "").split(":")
    if not user or len(parts) != 2 or not parts[1].isdigit():
        return await c.answer("Ruxsat yo'q", show_alert=True)
    firm_id = int(parts[1])
    if not db.user_can_access(user, firm_id):
        return await c.answer("Bu firmaga kirish huquqingiz yo'q", show_alert=True)
    if user["role"] not in auth.CAN_POST:
        return await c.answer("\"Kuzatuvchi\" roli import faylini tayyorlay olmaydi", show_alert=True)
    session = core.get_session(core.session_id(user["id"], firm_id), firm_id)
    try:
        async with session.lock:
            content, rows = core.export_client_bank(session)
    except (ValueError, files1c.ExportFileError) as e:
        return await c.answer(str(e)[:190], show_alert=True)
    await c.answer("Fayl tayyor")
    await c.message.edit_reply_markup(reply_markup=None)
    db.audit(user["id"], firm_id, "onec_import_file", rows=len(rows), sum=round(sum(r["amount"] for r in rows), 2),
             numbers=[r["number"] for r in rows][:200], via="telegram")
    firm = db.get_firm(firm_id)
    await c.message.answer_document(
        BufferedInputFile(content, core.export_filename(firm_id, rows)),
        caption=(f"🏢 <b>{html.escape(firm['name'])}</b>: 📤 {len(rows)} ta yangi qator — "
                 f"{money(sum(r['amount'] for r in rows))}\n\n"
                 "1C da: <b>Банк и касса → Банковские выписки → Загрузить</b> → shu faylni tanlang → ro'yxatni "
                 "tekshirib <b>Загрузить</b>.\n<i>Faylda schyot yo'q — 1C schyotni shartnoma sozlamasidan qo'yadi; "
                 "Web App'dagi 📋 tekshiruv ro'yxati bilan solishtiring.</i>"))


@router.message(F.text)
async def on_text(m: Message) -> None:
    ctx = await need_context(m)
    if not ctx:
        return
    user, _firm, session = ctx
    await m.bot.send_chat_action(m.chat.id, "typing")
    res = await services_ai.chat(session, m.text, "didox" if "didox" in m.text.lower() else "vipiska")
    kb = []
    for card in res["cards"]:
        if card["type"] == "confirm_post" and user["role"] in auth.CAN_POST:
            kb.append([post_button(session, card["mode"], card["ids"],
                                   f"✅ Tasdiqlash: {card['count']} ta hujjat ({money(card['total'])})")])
    await m.answer(md_to_html(res["reply"]), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb) if kb else None)


async def run_polling() -> None:
    bot = Bot(config.TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await bot.set_my_commands([BotCommand(command="start", description="Boshlash"),
                                   BotCommand(command="firma", description="Firmani tanlash"),
                                   BotCommand(command="link", description="Akkauntni bog'lash: /link 123456"),
                                   BotCommand(command="namuna", description="Test vipiska fayli")])
        if config.WEBAPP_URL.startswith("https://"):
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Buxgalter AI",
                                                                        web_app=WebAppInfo(url=config.WEBAPP_URL)))
    except Exception as e:  # noqa: BLE001
        log.warning("Bot sozlamalari o'rnatilmadi: %s", e)
    await dp.start_polling(bot, handle_signals=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN .env faylida ko'rsatilmagan")
    db.init()
    asyncio.run(run_polling())
