"""Firma va foydalanuvchi almashganda eski tugmalar / javoblar boshqa firmaga yozib yubormasligi."""
import asyncio
import types
from unittest.mock import AsyncMock

import pytest

import bot
import core
import db
from tests.conftest import DEMO_FIRM

TG = 4242


class FakeMessage:
    def __init__(self):
        self.sent = []

    async def answer(self, text, **kw):
        self.sent.append(text)

    edit_reply_markup = AsyncMock()
    edit_text = AsyncMock()


def callback(data):
    return types.SimpleNamespace(data=data, from_user=types.SimpleNamespace(id=TG), message=FakeMessage(),
                                 answer=AsyncMock())


@pytest.fixture
def two_firms():
    """Bir xil vipiska ikki firmada yuklangan (qator ID lari bir xil) — eng xavfli holat."""
    alfa = db.create_firm({"name": "Alfa", "inn": "305123456", "onec_demo_mode": True})["id"]
    user = db.create_user("buh", "parol12345", "accountant", firm_ids=[DEMO_FIRM, alfa])
    db.consume_link_code(db.create_link_code(user["id"]), TG)
    return user, alfa


def drafts(firm_id):
    onec = core.get_onec(firm_id)
    return [d for e in ("Document_ПоступлениеНаРасчетныйСчет", "Document_СписаниеСРасчетногоСчета")
            for d in onec.t.db[e] if d.get("Комментарий", "").startswith("BUXGALTER AI")]


def keyboard_data(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def test_old_post_button_never_writes_into_the_newly_selected_firm(two_firms):
    user, alfa = two_firms

    async def scenario():
        db.set_telegram_firm(user["id"], DEMO_FIRM)
        _, firm, s_demo = bot.context_of(TG)
        snap = await core.load_sample(s_demo)
        button = keyboard_data(bot.result_keyboard(snap, user, s_demo))[0]  # DEMO uchun chiqqan tugma
        await bot.cb_firm_select(callback(f"firm:{alfa}"))                              # foydalanuvchi Alfa'ga o'tdi
        _, _, s_alfa = bot.context_of(TG)
        await core.load_sample(s_alfa)
        await bot.cb_post(callback(button))                                              # eski tugma bosildi
    asyncio.run(scenario())
    assert len(drafts(alfa)) == 0, "eski tugma yangi tanlangan firmaga yozib yubordi"
    assert len(drafts(DEMO_FIRM)) == 4  # tugma qaysi firma uchun chiqqan bo'lsa — o'sha firmaga


def test_old_ai_confirm_button_is_bound_to_its_firm(two_firms):
    user, alfa = two_firms
    sent = {}

    async def fake_answer(text, reply_markup=None, **kw):
        sent["markup"] = reply_markup

    async def scenario():
        db.set_telegram_firm(user["id"], DEMO_FIRM)
        _, _, s_demo = bot.context_of(TG)
        await core.load_sample(s_demo)
        msg = types.SimpleNamespace(text="Yangi vipiskalarni 1C ga kirit", from_user=types.SimpleNamespace(id=TG),
                                    chat=types.SimpleNamespace(id=1), answer=fake_answer,
                                    bot=types.SimpleNamespace(send_chat_action=AsyncMock()))
        await bot.on_text(msg)
        button = keyboard_data(sent["markup"])[0]
        await bot.cb_firm_select(callback(f"firm:{alfa}"))
        _, _, s_alfa = bot.context_of(TG)
        await core.load_sample(s_alfa)
        await bot.cb_post(callback(button))
    asyncio.run(scenario())
    assert len(drafts(alfa)) == 0
    assert len(drafts(DEMO_FIRM)) == 4


def test_button_for_firm_without_access_is_refused(two_firms):
    user, alfa = two_firms

    async def scenario():
        db.set_telegram_firm(user["id"], alfa)
        _, firm, s_alfa = bot.context_of(TG)
        snap = await core.load_sample(s_alfa)
        button = keyboard_data(bot.result_keyboard(snap, user, s_alfa))[0]
        db.update_user(user["id"], firm_ids=[DEMO_FIRM])  # Egasi Alfa'ga ruxsatni olib tashladi
        cb = callback(button)
        await bot.cb_post(cb)
        return cb
    cb = asyncio.run(scenario())
    assert len(drafts(alfa)) == 0
    assert "huquq" in str(cb.answer.call_args)


def test_button_is_single_use(two_firms):
    user, _ = two_firms

    async def scenario():
        db.set_telegram_firm(user["id"], DEMO_FIRM)
        _, _, s = bot.context_of(TG)
        snap = await core.load_sample(s)
        button = keyboard_data(bot.result_keyboard(snap, user, s))[0]
        await bot.cb_post(callback(button))
        second = callback(button)
        await bot.cb_post(second)
        return second
    second = asyncio.run(scenario())
    assert len(drafts(DEMO_FIRM)) == 4
    assert "ishlatilgan" in str(second.answer.call_args)


def test_old_button_does_not_post_a_newer_statement(two_firms):
    user, _ = two_firms
    other = ("Sana;Hujjat №;Debet;Kredit;Kontragent;STIR;To'lov maqsadi\n"
             "16.10.2026;900;5 000,00;;TOSHKENT QURILISH MATERIALLARI MChJ;304567890;Boshqa to'lov\n").encode()

    async def scenario():
        db.set_telegram_firm(user["id"], DEMO_FIRM)
        _, _, s = bot.context_of(TG)
        snap = await core.load_sample(s)
        button = keyboard_data(bot.result_keyboard(snap, user, s))[0]   # namuna vipiska uchun tugma
        await core.load_statement(s, "boshqa.csv", other)               # keyin boshqa vipiska yuklandi
        await bot.cb_post(callback(button))
    asyncio.run(scenario())
    assert len(drafts(DEMO_FIRM)) == 0  # yangi vipiskaning qatori eski tugma bilan yozilmadi


def test_web_post_with_rows_from_another_firm_is_refused(two_firms):
    from fastapi.testclient import TestClient

    import main
    user, alfa = two_firms
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    client.post("/api/auth/login", json={"username": "buh", "password": "parol12345"})
    demo = client.post("/api/vipiska/sample", headers={"X-Firm-Id": str(DEMO_FIRM)}).json()
    client.post("/api/vipiska/sample", headers={"X-Firm-Id": str(alfa)})
    ids = [r["id"] for r in demo["rows"] if r["selectable"]]
    # UI DEMO jadvalidagi qatorlarni yubormoqda, lekin sarlavhada allaqachon Alfa (firma almashgan)
    r = client.post("/api/post", headers={"X-Firm-Id": str(alfa)},
                    json={"ids": ids, "expected_firm_id": DEMO_FIRM})
    assert r.status_code == 409
    assert len(drafts(alfa)) == 0 and len(drafts(DEMO_FIRM)) == 0
    chat = client.post("/api/chat", headers={"X-Firm-Id": str(DEMO_FIRM)},
                       json={"message": "Yangi vipiskalarni 1C ga kirit"}).json()
    assert chat["cards"][0]["firm_id"] == DEMO_FIRM  # tasdiqlash kartasi o'z firmasini biladi
