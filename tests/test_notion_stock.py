"""📓 Notion sklad: o'qish (jonli API / nusxa), 1C bilan solishtirish (o'ylab topilgan ma'lumot)."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

import core
import db
import main
import services_notion as sn
from tests.conftest import DEMO_FIRM

PASSWORD = "parol12345"
DB_ID = "0123456789abcdef0123456789abcdef"


def page(name, qty, unit="m", price=1000.0, kind="Erkin", note=""):
    t = lambda v: {"type": "rich_text", "rich_text": [{"plain_text": v}] if v else []}  # noqa: E731
    return {"url": f"https://notion.so/{name}", "last_edited_time": "2026-10-18T10:00:00.000Z", "properties": {
        "Tovar": {"type": "title", "title": [{"plain_text": name}]},
        "Miqdor": {"type": "number", "number": qty},
        "Birlik": {"type": "select", "select": {"name": unit}},
        "Kirim narxi NDSsiz": {"type": "number", "number": price},
        "Sotuv narxi NDS bilan": {"type": "number", "number": price * 1.3},
        "Turi": {"type": "select", "select": {"name": kind}},
        "Izoh": t(note), "Band kimga": t(""), "Partiya/manba": t("")}}


def notion_client(pages, status=200):
    calls = []

    def handler(req):
        calls.append(req)
        assert req.headers["Authorization"] == "Bearer tok" and req.headers["Notion-Version"]
        if status != 200:
            return httpx.Response(status, json={"message": "x"})
        import json
        body = json.loads(req.content)
        start = int(body.get("start_cursor") or 0)
        chunk = pages[start:start + 2]  # 2 tadan — sahifalashni tekshirish
        more = start + 2 < len(pages)
        return httpx.Response(200, json={"results": chunk, "has_more": more, "next_cursor": str(start + 2) if more else None})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def firm(**kw):
    return {"id": DEMO_FIRM, "notion_token": "tok", "notion_stock_db": DB_ID, **kw}


def test_live_read_pages_groups_and_saves_snapshot():
    c, calls = notion_client([page("ВВГнг(А) 3х2,5", 120), page("Хомут 300", 3, "juft", 500), page("Eski", 0),
                              page("Kabel zaxira", 50, kind="Zaxira")])
    s = asyncio.run(sn.stock(firm(), c))
    assert len(calls) == 2 and s["live"] and s["count"] == 3 and s["sold_out"] == 1
    assert [g["title"] for g in s["groups"]] == ["Erkin", "Zaxira"]
    assert s["groups"][0]["items"][0] == {**s["groups"][0]["items"][0], "name": "ВВГнг(А) 3х2,5", "qty": 120.0,
                                          "sum": 120000.0, "unit": "m"}
    assert sn.load_snapshot(DEMO_FIRM)["source"] == "api"


def test_falls_back_to_snapshot_with_warning_when_api_fails():
    sn.save_snapshot(DEMO_FIRM, [{"Tovar": "A", "Miqdor": 5, "Birlik": "dona", "Kirim narxi NDSsiz": 10}], "claude")
    c, _ = notion_client([], status=401)
    s = asyncio.run(sn.stock(firm(), c))
    assert not s["live"] and s["count"] == 1 and "401" in s["warning"] and s["token"] is True
    assert asyncio.run(sn.stock({"id": DEMO_FIRM}))["token"] is False  # tokensiz — faqat nusxa
    assert asyncio.run(sn.stock({"id": 999}))["error"] == "Notion sklad bazasi ulanmagan"


def test_name_key_ignores_case_lookalikes_spaces_and_batch_note():
    assert sn.name_key("ППГнг (А) - HF 3х2,5 (ALFA partiya)") == sn.name_key("ппгнг(a)-hf 3x2.5")
    assert sn.name_key("АВВГ 3x2,5 (ож)-0,66 кВ") == sn.name_key("АВВГ 3x2,5 (ож)-0,66 (OSIYO)")
    assert sn.name_key("Кабель АВВГ 4х240мм") == sn.name_key("АВВГ 4*240")
    assert sn.name_key("СИП 4 4х16 0,66/1,0") == sn.name_key("СИП 4 4х16")
    assert sn.name_key("ВВГнг(А) 5х4") != sn.name_key("ВВГнг 5х4")  # нг(А) — boshqa marka
    assert sn.name_key("АВВГ 3х25") != sn.name_key("АВВГ 3х2,5")    # kesim raqami
    assert sn.name_key("U/UTP Cat 5e") != sn.name_key("U/UTP Cat 6")
    assert sn.unit_of("шт") == "dona" and sn.unit_of("м") == "m" and sn.unit_of("кг") == "kg"


def test_compare_states():
    onec = {"groups": [{"items": [{"name": "Kabel A", "unit": "м", "qty": 100}, {"name": "Kabel B", "unit": "м", "qty": 50},
                                  {"name": "Faqat 1C", "unit": "шт", "qty": 2}, {"name": "Tugagan", "unit": "шт", "qty": 0}]}]}
    notion = sn.build([{"Tovar": "kabel a", "Miqdor": 100, "Birlik": "m"}, {"Tovar": "Kabel B", "Miqdor": 30, "Birlik": "m"},
                       {"Tovar": "Kabel B (X partiya)", "Miqdor": 5, "Birlik": "m"},
                       {"Tovar": "Faqat Notion", "Miqdor": 7, "Birlik": "dona"}, {"Tovar": "Tugagan", "Miqdor": 0}])
    r = sn.compare(onec, notion)
    by = {x["name"]: x for x in r["rows"]}
    assert by["Kabel A"]["state"] == "ok"
    assert by["Kabel B"]["state"] == "diff" and by["Kabel B"]["diff"] == -15.0  # 30 + 5 (partiya) − 50
    assert by["Faqat 1C"]["state"] == "only_1c" and by["Faqat Notion"]["state"] == "only_notion"
    assert "Tugagan" not in by and r["rows"][0]["state"] == "diff"
    assert r["counts"] == {"diff": 1, "only_1c": 1, "only_notion": 1, "ok": 1}


def test_firm_settings_notion_db_link_and_secret_token():
    f = db.update_firm(DEMO_FIRM, {"notion_stock_db": f"https://www.notion.so/ws/Sklad-{DB_ID}?v=1", "notion_token": "ntn_x"})
    assert f["notion_stock_db"] == "01234567-89ab-cdef-0123-456789abcdef" and f["notion_token"] != "ntn_x"
    assert db.firm_settings(DEMO_FIRM)["notion_token"] == "ntn_x"
    with pytest.raises(db.DBError, match="Notion"):
        db.update_firm(DEMO_FIRM, {"notion_stock_db": "sklad"})


def test_stock_endpoint_sources():
    db.create_user("u1", PASSWORD, "viewer", "U", firm_ids=[DEMO_FIRM])
    c = TestClient(main.app, base_url="http://127.0.0.1:8000")
    c.post("/api/auth/login", json={"username": "u1", "password": PASSWORD})
    h = {"X-Firm-Id": str(DEMO_FIRM)}
    assert c.get("/api/stock?source=notion", headers=h).json()["error"]  # ulanmagan
    sn.save_snapshot(DEMO_FIRM, [{"Tovar": "Armatura A500C d12", "Miqdor": 18.5, "Birlik": "t", "Kirim narxi NDSsiz": 1}], "claude")
    n = c.get("/api/stock?source=notion", headers=h).json()
    assert n["count"] == 1 and "_items" not in n and n["live"] is False
    cmp = c.get("/api/stock?source=compare", headers=h).json()
    assert cmp["source"] == "compare" and cmp["counts"]["only_1c"] >= 1
    assert c.get("/api/stock?source=x", headers=h).status_code == 400
    assert c.get("/api/stock", headers=h).json()["source"] == "1c"


def test_similar_hint_only_when_digits_match():
    onec = {"groups": [{"items": [{"name": "Сип 3*50+1*54,6 nex", "unit": "м", "qty": 10},
                                  {"name": "ПУГНП 2х1,5", "unit": "м", "qty": 5}]}]}
    notion = sn.build([{"Tovar": "Кабел СИП 350+154,6", "Miqdor": 7, "Birlik": "m"},
                       {"Tovar": "ПУГНП 2Х2,5", "Miqdor": 3, "Birlik": "m"}])
    by = {r["name"]: r for r in sn.compare(onec, notion)["rows"]}
    assert by["Сип 3*50+1*54,6 nex"]["similar"]["name"] == "Кабел СИП 350+154,6"
    assert by["Сип 3*50+1*54,6 nex"]["similar"]["same_qty"] is False
    assert "similar" not in by["ПУГНП 2х1,5"]  # 1,5 ≠ 2,5
