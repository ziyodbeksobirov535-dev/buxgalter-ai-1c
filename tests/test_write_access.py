"""1C ga yozish ruxsati: standart — faqat tahlil; Egasi firma nomi bilan tasdiqlab yoqadi; bir martada limit;
dastur yaratgan qoralamalar ro'yxati va "bekor qilish" (faqat o'tkazilmagan, o'zimiz yaratgan qoralama)."""
import asyncio

import pytest
from fastapi.testclient import TestClient

import bot
import config
import core
import db
import main
from demo_data import build_sample_xlsx
from services_1c import DemoOData, ODataError, OneCService
from tests.conftest import DEMO_FIRM

PASSWORD = "parol12345"


class LiveDemo(DemoOData):
    mode = "live"


@pytest.fixture
def live(monkeypatch):
    """Demo firma "haqiqiy 1C" sifatida (xotiradagi baza, lekin mode=live — barcha himoyalar ishlaydi)."""
    service = OneCService(LiveDemo())
    monkeypatch.setattr(core, "get_onec", lambda *a: service)
    monkeypatch.setattr(core, "onec_fingerprint", lambda *a: "live-fp")
    return service


def run(coro):
    return asyncio.run(coro)


def analyzed(sid="w1"):
    s = core.get_session(sid, DEMO_FIRM)
    if not config.SAMPLE_VIPISKA.exists():
        build_sample_xlsx(config.SAMPLE_VIPISKA)
    snap = run(core.load_statement(s, "vipiska.xlsx", config.SAMPLE_VIPISKA.read_bytes()))
    return s, [r for r in snap["rows"] if r["selectable"]]


def test_live_firm_is_analysis_only_by_default(live):
    s, ready = analyzed()
    assert db.firm_settings(DEMO_FIRM)["onec_write_enabled"] is False
    with pytest.raises(ODataError, match="ruxsat berilmagan"):
        run(core.post(s, "vipiska", [ready[0]["id"]]))
    assert not db.list_drafts(DEMO_FIRM)


def test_limit_one_document_at_a_time_and_draft_recorded(live):
    db.set_write_access(DEMO_FIRM, True, limit=1)
    s, ready = analyzed()
    with pytest.raises(ODataError, match="ko'pi bilan 1 ta"):
        run(core.post(s, "vipiska", [ready[0]["id"], ready[1]["id"]]))
    res = run(core.post(s, "vipiska", [ready[0]["id"]], by="buh"))
    assert res["ok"] == 1
    drafts = db.list_drafts(DEMO_FIRM)
    assert len(drafts) == 1 and drafts[0]["created_by"] == "buh" and drafts[0]["in_number"] == ready[0]["number"]
    doc = run(live.t.get(drafts[0]["entity"], drafts[0]["ref"]))
    assert doc["Posted"] is False and doc["Комментарий"].startswith("BUXGALTER AI")


def test_new_contract_not_created_unless_allowed(live):
    from datetime import date
    from services_vipiska import BankRow, Statement
    db.set_write_access(DEMO_FIRM, True, limit=1, create_contracts=False)
    s = core.get_session("w2", DEMO_FIRM)
    row = BankRow(date(2026, 10, 17), "777", "chiqim", 1_000_000.0, "TOSHKENT QURILISH MATERIALLARI MChJ", "304567890",
                  purpose="Shartnoma №777 dan 01.10.2026 bo'yicha to'lov")
    row.id = row.key()
    s.statement = Statement(rows=[row])
    run(core._analyze_vipiska(s))
    analyzed_row = s.vipiska[0]
    assert analyzed_row["contract"]["source"] == "new" and analyzed_row["selectable"]
    before = len(live.t.db["Catalog_ДоговорыКонтрагентов"])
    assert run(core.post(s, "vipiska", [row.id]))["ok"] == 1
    assert len(live.t.db["Catalog_ДоговорыКонтрагентов"]) == before  # yangi shartnoma yaratilmadi
    db.set_write_access(DEMO_FIRM, True, limit=1, create_contracts=True)  # ruxsat berilsa — yaratiladi
    row2 = BankRow(date(2026, 10, 17), "778", "chiqim", 2_000_000.0, "TOSHKENT QURILISH MATERIALLARI MChJ", "304567890",
                   purpose="Shartnoma №778 dan 02.10.2026 bo'yicha to'lov")
    row2.id = row2.key()
    s.statement = Statement(rows=[row2])
    run(core._analyze_vipiska(s))
    assert run(core.post(s, "vipiska", [row2.id]))["ok"] == 1
    assert len(live.t.db["Catalog_ДоговорыКонтрагентов"]) == before + 1


def test_undo_marks_only_our_unposted_draft_for_deletion(live):
    db.set_write_access(DEMO_FIRM, True, limit=1)
    s, ready = analyzed()
    run(core.post(s, "vipiska", [ready[0]["id"]]))
    draft = db.list_drafts(DEMO_FIRM)[0]
    undone = run(core.undo_draft(s, draft["id"], "buh"))
    assert undone["undone_at"] and undone["undone_by"] == "buh"
    assert run(live.t.get(draft["entity"], draft["ref"]))["DeletionMark"] is True
    row = next(r for r in s.vipiska if r["id"] == ready[0]["id"])
    assert not row["posted"] and row["status"] == "new"  # jadvalda yana "yangi"
    with pytest.raises(ValueError, match="allaqachon bekor"):
        run(core.undo_draft(s, draft["id"]))


@pytest.mark.parametrize("change, message", [({"Posted": True}, "O'TKAZILGAN"),
                                             ({"Комментарий": "Buxgalter o'zgartirdi"}, "izohi o'zgartirilgan")])
def test_undo_refuses_posted_or_edited_document(live, change, message):
    db.set_write_access(DEMO_FIRM, True, limit=1)
    s, ready = analyzed()
    run(core.post(s, "vipiska", [ready[0]["id"]]))
    draft = db.list_drafts(DEMO_FIRM)[0]
    run(live.t.patch(draft["entity"], draft["ref"], change))
    with pytest.raises(ValueError, match=message):
        run(core.undo_draft(s, draft["id"]))
    assert run(live.t.get(draft["entity"], draft["ref"]))["DeletionMark"] is False


def test_write_access_api_owner_and_exact_name():
    db.create_user("egasi", PASSWORD, "owner", "E")
    db.create_user("buh", PASSWORD, "accountant", "B", firm_ids=[DEMO_FIRM])
    c = TestClient(main.app, base_url="http://127.0.0.1:8000")
    c.post("/api/auth/login", json={"username": "buh", "password": PASSWORD})
    assert c.post(f"/api/firms/{DEMO_FIRM}/write-access", json={"enabled": True}).status_code == 403
    # umumiy firma sozlamasi orqali yoqib bo'lmaydi
    c.post("/api/auth/login", json={"username": "egasi", "password": PASSWORD})
    c.put(f"/api/firms/{DEMO_FIRM}", json={"onec_write_enabled": True, "onec_write_limit": 50})
    assert db.firm_settings(DEMO_FIRM)["onec_write_enabled"] is False
    r = c.post(f"/api/firms/{DEMO_FIRM}/write-access", json={"enabled": True, "confirm_name": "boshqa"})
    assert r.status_code == 400 and "firma nomini" in r.json()["detail"]
    name = db.get_firm(DEMO_FIRM)["name"]
    r = c.post(f"/api/firms/{DEMO_FIRM}/write-access", json={"enabled": True, "confirm_name": name, "limit": 1})
    assert r.status_code == 200 and r.json()["onec_write_enabled"] is True
    assert c.post(f"/api/firms/{DEMO_FIRM}/write-access", json={"enabled": True, "confirm_name": name,
                                                              "limit": 999}).status_code == 400
    assert c.post(f"/api/firms/{DEMO_FIRM}/write-access", json={"enabled": False}).json()["onec_write_enabled"] is False
    assert db.list_audit(firm_id=DEMO_FIRM)[0]["action"] == "onec_write_access"


def test_bot_offers_no_post_button_without_write_access(live):
    user = db.create_user("buh", PASSWORD, "accountant", "B", firm_ids=[DEMO_FIRM])
    s, _ready = analyzed(core.session_id(user["id"], DEMO_FIRM))

    def post_buttons():
        kb = bot.result_keyboard(core.snapshot(s), user, s)
        return [b for row in kb.inline_keyboard for b in row if (b.callback_data or "").startswith("post:")]
    assert post_buttons() == []
    db.set_write_access(DEMO_FIRM, True, limit=1)
    buttons = post_buttons()
    assert len(buttons) == 1 and "(1)" in buttons[0].text
