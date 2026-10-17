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
from aiogram.filters import Command, CommandStart
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           MenuButtonWebApp, Message, WebAppInfo)

import config
import core
import services_ai
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


def allowed(user_id: int | None) -> bool:
    return bool(user_id) and user_id in config.ALLOWED_TELEGRAM_IDS


async def deny(m: Message) -> None:
    uid = m.from_user.id if m.from_user else "?"
    await m.answer(f"⛔ Ruxsat yo'q.\n\nSizning Telegram ID: <code>{uid}</code>\n"
                   f"Server .env fayliga <code>ALLOWED_TELEGRAM_IDS={uid}</code> qo'shib, qayta ishga tushiring.")


def session_of(user_id: int) -> core.Session:
    return core.get_session(f"tg_{user_id}")


def summary_text(snap: dict, title: str = "🏦 Vipiska tahlili") -> str:
    k = snap["kpis"]
    lines = [f"<b>{title}</b>: {html.escape(snap.get('source') or '')}",
             f"1C: {'Demo baza' if snap.get('onec_mode') == 'demo' else 'OData'}", "",
             f"Jami qatorlar: <b>{k['total']}</b>",
             f"🛡️ Dublikat (1C da bor, bloklandi): <b>{k['duplicates']}</b>",
             f"🟡 Yangi (kiritishga tayyor): <b>{k['ready']}</b> — {money(k['ready_sum'])}",
             f"🔴 Farq bor: <b>{k['discrepancies']}</b>",
             f"🧠 Tarixdan aniqlangan schyotlar: <b>{k['learned']}/{k['learned_base']} ({k['learned_pct']}%)</b>"]
    if k["posted"]:
        lines.append(f"✅ Qoralama yozilgan: <b>{k['posted']}</b>")
    news = [r for r in snap["rows"] if r["selectable"]]
    if news:
        lines += ["", "<b>Yangi qatorlar:</b>"]
        for r in news[:15]:
            icon = "🧠" if r["account_source"] == "history" else "🤖"
            arrow = "⬇️" if r["direction"] == "kirim" else "⬆️"
            lines.append(f"{arrow} №{html.escape(r['number'])} {html.escape(r['counterparty_name'][:32])} — "
                         f"{money(r['amount'])} → <b>{r['account']}</b> {icon}")
    for r in snap["rows"]:
        if r["status"] == "discrepancy":
            lines.append(f"🔴 №{html.escape(r['number'])} {html.escape(r['counterparty_name'][:32])}: "
                         f"{html.escape('; '.join(r['diffs']))}")
        elif r["status"] == "new" and r["blockers"]:
            lines.append(f"⚠️ №{html.escape(r['number'])}: {html.escape(r['blockers'][0])}")
    return "\n".join(lines)


def result_keyboard(snap: dict, mode: str = "vipiska") -> InlineKeyboardMarkup:
    rows = []
    ready = snap["kpis"]["ready"]
    if ready:
        rows.append([InlineKeyboardButton(text=f"✅ Yangilarini 1C ga kiritish ({ready})", callback_data=f"post:{mode}")])
    btn = webapp_button("📱 Web App'da ko'rish")
    if btn:
        rows.append([btn])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def cmd_start(m: Message) -> None:
    if not allowed(m.from_user.id if m.from_user else None):
        return await deny(m)
    kb = []
    btn = webapp_button()
    if btn:
        kb.append([btn])
    kb.append([InlineKeyboardButton(text="🧪 Test vipiskani tahlil qilish", callback_data="sample")])
    kb.append([InlineKeyboardButton(text="📑 Didox EHF vs 1C", callback_data="didox")])
    note = "" if btn else ("\n\n<i>Web App tugmasi uchun .env da WEBAPP_URL=https://… (HTTPS tunnel) ni sozlang.</i>")
    await m.answer(
        "🏦 <b>BUXGALTER AI</b> ga xush kelibsiz!\n\n"
        "Men 1C:Buxgalteriya (O'zbekiston 3.0) bilan ishlaydigan AI buxgalterman:\n"
        "• Bank <b>vipiska</b> faylini (.xlsx / .csv / 1CClientBankExchange .txt) shu chatga yuboring\n"
        "• Har bir qatorni 1C da <b>dublikatga</b> tekshiraman 🛡️\n"
        "• Schyotlarni <b>oldingi 1C yozuvlaridan</b> o'rganaman 🧠\n"
        "• Yangilarini faqat <b>qoralama</b> sifatida kiritaman ✅\n\n"
        "/namuna — test vipiska faylini olish\n"
        "Yoki shunchaki savol yozing: <i>\"Kunlik pul oqimi tahlili\"</i>" + note,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.message(Command("namuna"))
async def cmd_sample(m: Message) -> None:
    if not allowed(m.from_user.id if m.from_user else None):
        return await deny(m)
    if not config.SAMPLE_VIPISKA.exists():
        from demo_data import build_sample_xlsx
        build_sample_xlsx(config.SAMPLE_VIPISKA)
    await m.answer_document(BufferedInputFile(config.SAMPLE_VIPISKA.read_bytes(), "sample_bank_vipiska.xlsx"),
                            caption="🧪 Test vipiska: 3 ta dublikat, 4 ta yangi, 1 ta farqli qator.\n"
                                    "Shu faylni menga qayta yuboring — tahlil qilaman.")


@router.callback_query(F.data == "sample")
async def cb_sample(c: CallbackQuery) -> None:
    if not allowed(c.from_user.id):
        return await c.answer("Ruxsat yo'q", show_alert=True)
    await c.answer("Tahlil qilinmoqda…")
    snap = await core.load_sample(session_of(c.from_user.id))
    await c.message.answer(summary_text(snap), reply_markup=result_keyboard(snap))


@router.callback_query(F.data == "didox")
async def cb_didox(c: CallbackQuery) -> None:
    if not allowed(c.from_user.id):
        return await c.answer("Ruxsat yo'q", show_alert=True)
    await c.answer("Didox tekshirilmoqda…")
    try:
        snap = await core.load_didox(session_of(c.from_user.id))
    except Exception as e:  # noqa: BLE001
        return await c.message.answer(f"⚠️ {html.escape(str(e))}")
    await c.message.answer(summary_text(snap, "📑 Didox EHF vs 1C"), reply_markup=result_keyboard(snap, "didox"))


@router.message(F.document)
async def on_document(m: Message, bot: Bot) -> None:
    if not allowed(m.from_user.id if m.from_user else None):
        return await deny(m)
    doc = m.document
    if doc.file_size and doc.file_size > 10 * 1024 * 1024:
        return await m.answer("⚠️ Fayl juda katta (10 MB gacha).")
    wait = await m.answer("⏳ Vipiska o'qilmoqda, 1C bilan solishtirilmoqda…")
    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    try:
        snap = await core.load_statement(session_of(m.from_user.id), doc.file_name or "vipiska.xlsx", buf.getvalue())
    except (StatementError, ODataError) as e:
        return await wait.edit_text(f"⚠️ {html.escape(str(e))}")
    await wait.edit_text(summary_text(snap), reply_markup=result_keyboard(snap))


@router.callback_query(F.data.startswith("post:") | (F.data == "confirm"))
async def cb_post(c: CallbackQuery) -> None:
    if not allowed(c.from_user.id):
        return await c.answer("Ruxsat yo'q", show_alert=True)
    session = session_of(c.from_user.id)
    if c.data == "confirm" and session.pending:
        mode, ids = session.pending["mode"], session.pending["ids"]
        session.pending = None
    else:
        mode = c.data.split(":", 1)[1] if ":" in c.data else "vipiska"
        ids = [r["id"] for r in core.rows_of(session, mode) if r["selectable"]]
    if not ids:
        return await c.answer("Kiritish uchun yangi qator yo'q", show_alert=True)
    await c.answer("1C ga yozilmoqda…")
    await c.message.edit_reply_markup(reply_markup=None)  # ikki marta bosishdan himoya
    res = await core.post(session, mode, ids)
    lines = [f"<b>1C natijasi</b>: ✅ {res['ok']} ta qoralama yaratildi, ⛔ {res['failed']} ta o'tkazib yuborildi", ""]
    by_id = {r["id"]: r for r in res["rows"]}
    for r in res["results"]:
        row = by_id.get(r["id"], {})
        lines.append(f"{'✅' if r['ok'] else '⛔'} №{html.escape(str(row.get('number', '')))} — {html.escape(r['message'])}")
    lines.append("\n<i>Hujjatlar 1C da qoralama (Posted=false). Tekshirib, 1C ning o'zida o'tkazing.</i>")
    kb = [[b]] if (b := webapp_button("📱 Web App'da ko'rish")) else []
    await c.message.answer("\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.message(F.text)
async def on_text(m: Message) -> None:
    if not allowed(m.from_user.id if m.from_user else None):
        return await deny(m)
    session = session_of(m.from_user.id)
    await m.bot.send_chat_action(m.chat.id, "typing")
    res = await services_ai.chat(session, m.text, "didox" if "didox" in m.text.lower() else "vipiska")
    kb = []
    for card in res["cards"]:
        if card["type"] == "confirm_post":
            session.pending = {"mode": card["mode"], "ids": card["ids"]}
            kb.append([InlineKeyboardButton(text=f"✅ Tasdiqlash: {card['count']} ta hujjat ({money(card['total'])})",
                                            callback_data="confirm")])
    await m.answer(md_to_html(res["reply"]), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb) if kb else None)


async def run_polling() -> None:
    bot = Bot(config.TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await bot.set_my_commands([BotCommand(command="start", description="Boshlash"),
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
    asyncio.run(run_polling())
