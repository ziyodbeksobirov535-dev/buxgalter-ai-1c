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
    """Demo firma "haqiqiy 1C" sifatida (xotiradagi baza, lekin mode=live — barcha himoyalar ishlaydi).
    Yozish mexanizmini sinash uchun dastur darajasidagi qulf shu testlarda ochiladi (haqiqiy dasturda — yopiq)."""
    import services_1c
    monkeypatch.setattr(services_1c, "WRITES_ENABLED", True)
    monkeypatch.setattr(services_1c, "WRITE_ALLOWLIST", services_1c.WRITE_ALLOWLIST | {
        services_1c.E_COUNTERPARTY, services_1c.E_CONTRACT, services_1c.E_BANK_ACCOUNT})
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


# ---------------------------------------------------------------- ➕ yangi kontragent (vipiskadan)
def new_cp_session(live, sid="cp1"):
    from datetime import date
    from services_vipiska import BankRow, Statement
    live.t.db.setdefault("Catalog_Банки", []).append({"Ref_Key": "bank-444", "Code": "00444", "Description": "TEST BANK",
                                                      "DeletionMark": False})
    s = core.get_session(sid, DEMO_FIRM)
    row = BankRow(date(2026, 10, 17), "501", "kirim", 2_500_000.0, "YANGI XARIDOR MChJ", "300000099", mfo="00444",
                  account_no="20208000100000000099", purpose="Shartnoma №55 dan 01.10.2026 bo'yicha to'lov")
    row.id = row.key()
    s.statement = Statement(rows=[row])
    run(core._analyze_vipiska(s))
    return s, row.id


def test_create_counterparty_with_bank_account_and_contract(live):
    s, rid = new_cp_session(live)
    r = s.vipiska[0]
    assert r["missing_counterparty"] and not r["selectable"]
    pre = run(core.counterparty_prefill(s, rid))
    assert (pre["inn"], pre["mfo"], pre["bank"]["name"], pre["contract_number"]) == ("300000099", "00444", "TEST BANK", "55")
    with pytest.raises(ODataError, match="ruxsat berilmagan"):  # yozish yoqilmagan
        run(core.create_counterparty(s, rid, pre))
    db.set_write_access(DEMO_FIRM, True, limit=1)
    created = run(core.create_counterparty(s, rid, {**pre, "folder": ""}, by="buh"))
    cp = next(c for c in live.t.db["Catalog_Контрагенты"] if c["Ref_Key"] == created["counterparty"]["ref"])
    assert cp["ИНН"] == "300000099" and cp["Комментарий"].startswith("BUXGALTER AI") and cp["ЮридическоеФизическоеЛицо"] == "ЮридическоеЛицо"
    acc = next(a for a in live.t.db["Catalog_БанковскиеСчета"] if a.get("Owner") == cp["Ref_Key"])
    assert acc["НомерСчета"] == "20208000100000000099" and cp["ОсновнойБанковскийСчет_Key"] == acc["Ref_Key"]
    ctr = next(c for c in live.t.db["Catalog_ДоговорыКонтрагентов"] if c["Owner_Key"] == cp["Ref_Key"])
    assert ctr["Номер"] == "55" and ctr["ВидДоговора"] == "СПокупателем"
    assert {d["entity"] for d in db.list_drafts(DEMO_FIRM)} == {"Catalog_Контрагенты", "Catalog_БанковскиеСчета",
                                                               "Catalog_ДоговорыКонтрагентов"}
    row = s.vipiska[0]  # qayta tekshirildi: to'siq yo'q, shartnoma topildi
    assert not row["blockers"] and row["selectable"] and row["contract"]["source"] == "purpose"
    with pytest.raises(ValueError, match="bor"):  # takror yaratilmaydi
        run(core.create_counterparty(s, rid, pre))


def test_existing_even_deleted_counterparty_is_not_duplicated(live):
    db.set_write_access(DEMO_FIRM, True, limit=1)
    s, rid = new_cp_session(live, "cp2")
    live.t.db["Catalog_Контрагенты"].append({"Ref_Key": "old", "Description": "ESKI NOM", "ИНН": "300000099", "DeletionMark": True})
    pre = run(core.counterparty_prefill(s, rid))
    assert pre["existing"] == [{"name": "ESKI NOM", "deleted": True}]
    with pytest.raises(ValueError, match="o'chirishga belgilangan"):
        run(core.create_counterparty(s, rid, pre))


def test_undo_counterparty_cascades_and_refuses_when_used(live):
    db.set_write_access(DEMO_FIRM, True, limit=1)
    s, rid = new_cp_session(live, "cp3")
    created = run(core.create_counterparty(s, rid, run(core.counterparty_prefill(s, rid))))
    ref = created["counterparty"]["ref"]
    cp_draft = next(d for d in db.list_drafts(DEMO_FIRM) if d["entity"] == "Catalog_Контрагенты")
    live.t.db["Document_ПоступлениеНаРасчетныйСчет"].append({"Ref_Key": "used", "Контрагент_Key": ref, "Posted": True,
                                                             "DeletionMark": False})
    with pytest.raises(ValueError, match="o'tkazilgan hujjatlarda"):
        run(core.undo_draft(s, cp_draft["id"]))
    live.t.db["Document_ПоступлениеНаРасчетныйСчет"].pop()
    run(core.undo_draft(s, cp_draft["id"]))
    assert all(d["undone_at"] for d in db.list_drafts(DEMO_FIRM))  # bank hisobi va shartnoma ham
    for entity in ("Catalog_Контрагенты", "Catalog_БанковскиеСчета", "Catalog_ДоговорыКонтрагентов"):
        assert all(x["DeletionMark"] for x in live.t.db[entity] if ref in (x.get("Ref_Key"), x.get("Owner"), x.get("Owner_Key")))
