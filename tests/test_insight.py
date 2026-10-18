"""🔎 AI agentning 1C dan o'qish vositalari (demo baza) va 1C ga yozish qulfi."""
import asyncio

import core
import services_ai
import services_insight as insight
from services_1c import DemoOData, OneCService
from tests.conftest import DEMO_FIRM


def run(coro):
    return asyncio.run(coro)


def ctx():
    return services_ai.ToolContext(core.get_session("ins", DEMO_FIRM), "vipiska")


def test_overview_account_stock_documents_and_card():
    c = ctx()
    o = run(c.run("firm_overview", {}))
    assert o["bank_51_10"]["qiymat"] is not None and "debitor" in o["qarzlar"]
    a = run(c.run("account_balance", {"account": "4010"}))
    assert a["schyot"] == "40.10" and a["qatorlar"] and a["qatorlar"][0]["name"]
    s = run(c.run("stock_overview", {"source": "1c", "search": "armatura"}))
    assert s["topilgan"] >= 1 and s["tovarlar"][0]["unit"]
    d = run(c.run("list_documents", {"doc_type": "bank_kirim", "top": 3}))
    assert d["hujjat"] == "Document_ПоступлениеНаРасчетныйСчет" and d["soni"] <= 3
    card = run(c.run("counterparty_card", {"query": "301234567"}))
    assert card["kontragent"] and card["stir"] == "301234567"
    assert "xato" in run(c.run("list_documents", {"doc_type": "nimadir"}))
    assert "xato" in run(c.run("account_balance", {"account": "abc"}))


def test_raw_read_refuses_secret_and_bad_names():
    onec = OneCService(DemoOData())
    for bad in ("Catalog_Пользователи", "Catalog_Контрагенты?$filter=x", "../admin", "Catalog_Контрагенты(guid'1')"):
        try:
            run(insight.raw_query(onec, bad))
        except ValueError:
            continue
        raise AssertionError(bad)
    r = run(insight.raw_query(onec, "Catalog_Контрагенты", top=2))
    assert r["soni"] <= 2 and all("odata.metadata" not in y for y in r["yozuvlar"])


def test_compact_drops_empty_technical_and_secret_fields():
    rec = {"Ref_Key": "a", "Description": "X", "Пароль": "s", "Комментарий": "", "Owner@navigationLinkUrl": "u",
           "Дата": "0001-01-01T00:00:00", "Текст": "y" * 500}
    out = insight._compact(rec)
    assert set(out) == {"Ref_Key", "Description", "Текст"} and len(out["Текст"]) == insight.MAX_TEXT + 1


def test_agent_cannot_post_to_live_1c(monkeypatch):
    class LiveDemo(DemoOData):
        mode = "live"
    monkeypatch.setattr(core, "get_onec", lambda *a: OneCService(LiveDemo()))
    r = run(ctx().run("post_confirmed_to_1c", {}))
    assert r["holat"] == "yozish_yopiq" and "hech qanday o'zgartirish" in r["izoh"]


def test_demo_router_reaches_read_tools():
    names = lambda m: [n for n, _ in services_ai.route_intent(m)]  # noqa: E731
    assert "stock_overview" in names("skladda nima bor")
    assert names("60.10 schyot qoldig'i") == ["account_balance"]
    assert "firm_overview" in names("bank qoldig'i qancha, sverka")
