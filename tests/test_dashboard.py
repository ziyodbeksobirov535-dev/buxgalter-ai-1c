"""🏠 Bosh sahifa va 📦 Sklad: qoldiqlar, sverka, aylanma, qarzlar, bugungi ishlar (demo baza, o'ylab topilgan ma'lumot)."""
import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient

import core
import db
import main
import services_dashboard as dash
from demo_data import DEMO_BANK_BALANCE, DEMO_STOCK
from services_1c import DemoOData, OneCService
from services_vipiska import BankRow, Statement, parse_statement
from tests.conftest import DEMO_FIRM

PASSWORD = "parol12345"


def run(coro):
    return asyncio.run(coro)


def client(role="accountant"):
    db.create_user("u1", PASSWORD, role, "U", firm_ids=[DEMO_FIRM])
    c = TestClient(main.app, base_url="http://127.0.0.1:8000")
    c.post("/api/auth/login", json={"username": "u1", "password": PASSWORD})
    return c, {"X-Firm-Id": str(DEMO_FIRM)}


def test_opening_balance_from_bank_export_and_1c_exchange():
    from tests.test_parser import BANK_FORMAT, EXCHANGE
    from services_vipiska import _rows_from_table
    st = _rows_from_table(BANK_FORMAT)
    assert st.opening_balance == 1000.0
    assert st.closing_balance == round(1000.0 + 6269303.56 - 945000 - 100000000 - 100000, 2)
    ex = EXCHANGE.replace("РасчСчет=20208000100123456001\n", "РасчСчет=20208000100123456001\nСекцияРасчСчет\n"
                          "НачальныйОстаток=10000000.00\nКонецРасчСчет\n")
    st2 = parse_statement("kb.txt", ex.encode("cp1251"))
    assert st2.opening_balance == 10_000_000 and st2.closing_balance == 10_000_000 - 5_400_000 + 2_100_000


@pytest.mark.parametrize("closing, onec, pending, state", [
    (100.0, 100.0, 0.0, "ok"), (130.0, 100.0, 30.0, "pending"), (130.0, 100.0, 0.0, "mismatch")])
def test_sverka_states(closing, onec, pending, state):
    s = dash.sverka({"closing": closing, "pending_net": pending, "pending_count": 1, "last_date": "2026-10-16"}, onec)
    assert s["state"] == state and s["diff"] == round(closing - onec, 2)
    assert dash.sverka({"closing": None, "pending_net": 0}, 100.0) is None


def test_dashboard_demo_balance_month_debts_todo():
    session = core.get_session("d1", DEMO_FIRM)
    run(core.load_sample(session))
    onec = OneCService(DemoOData())
    d = run(dash.build(session, onec, {"ok": True, "mode": "demo"}, today=date(2026, 10, 18)))
    assert d["balance"]["value"] == DEMO_BANK_BALANCE and d["balance"]["source"] == "1c"
    assert d["month"]["from"] == "2026-10-01" and d["month"]["kirim"] > 0
    assert len(d["weeks"]) == dash.WEEKS and any(w["kirim"] for w in d["weeks"])
    assert d["debts"]["debitor"]["top"][0] == {"amount": 45_000_000.0, "name": "GLOBAL TEXTILE MChJ", "inn": "301234567"}
    assert d["debts"]["kreditor"]["total"] == 95_520_000.0
    assert d["todo"]["bank_ready"] == 9 and d["todo"]["bank_duplicates"] == 3


def test_dashboard_falls_back_to_statement_when_1c_offline():
    session = core.Session("d2", DEMO_FIRM)
    session.statement = Statement(rows=[BankRow(date(2026, 10, 2), "1", "kirim", 500.0, "A", "300000011"),
                                        BankRow(date(2026, 10, 3), "2", "chiqim", 200.0, "B", "300000021")],
                                  opening_balance=1000.0)
    from services_1c import OfflineOData
    d = run(dash.build(session, OneCService(OfflineOData("x")), {"ok": False}, today=date(2026, 10, 18)))
    assert d["balance"] == {"value": 1300.0, "source": "statement", "statement": d["balance"]["statement"],
                            "sverka": None, "onec": None}
    assert d["flow_source"] == "statement" and d["month"]["net"] == 300.0 and d["debts"] is None


def test_stock_groups_items_with_units_and_ikpu():
    s = run(dash.stock(OneCService(DemoOData())))
    assert s["count"] == len(DEMO_STOCK) and s["groups"][0]["account"] == "29.10"
    arm = next(i for i in s["groups"][0]["items"] if i["name"].startswith("Armatura"))
    assert arm["unit"] == "tonna" and arm["ikpu"] == "07214001001000000" and arm["price"] == round(166_500_000 / 18.5, 2)


def test_dashboard_and_stock_endpoints():
    c, h = client(role="viewer")  # ko'rish — kuzatuvchiga ham ochiq
    d = c.get("/api/dashboard", headers=h).json()
    assert d["balance"]["value"] == DEMO_BANK_BALANCE and d["firms"][0]["source"] == "demo"
    assert c.get("/api/stock", headers=h).json()["count"] == len(DEMO_STOCK)


def test_browser_page_url_is_rejected_as_1c_address():
    with pytest.raises(db.DBError, match="brauzerdagi sahifa"):
        db.update_firm(DEMO_FIRM, {"onec_base_url": "https://1c.uzcloud.uz/a/adm/#e1cib/list/X"})
    from services_1c import HttpOData
    h = HttpOData("https://1c.uzcloud.uz/a/adm/#e1cib/list/X", "u", "p")
    assert h.auth_error and "so'rov yuborilmadi" in h.auth_error


def test_balances_endpoint_selected_account_full_list():
    c, h = client(role="viewer")
    r = c.get("/api/balances?account=63.10", headers=h).json()
    assert r["account"] == "63.10" and r["total_kt"] == 8_000_000.0 and r["rows"][0]["amount"] == -8_000_000.0
    assert {x["account"] for x in r["choices"]} >= {"40.10", "63.10", "60.10", "43.10"}
    assert c.get("/api/balances?account=4310", headers=h).json()["total_dt"] == 15_000_000.0  # nuqtasiz ham
    assert c.get("/api/balances?account=abc", headers=h).status_code == 400


def test_onec_balance_is_dated_by_last_entered_bank_document():
    """1C dagi 51.10 — bugungi emas, 1C ga oxirgi kiritilgan bank hujjati kunidagi holat (1C orqada qolishi mumkin)."""
    session = core.Session("d3", DEMO_FIRM)
    session.statement = Statement(rows=[BankRow(date(2026, 10, 16), "1", "kirim", 500.0, "A", "300000011")],
                                  opening_balance=1000.0)
    d = run(dash.build(session, OneCService(DemoOData()), {"ok": True}, today=date(2026, 10, 18)))
    o = d["balance"]["onec"]
    assert o["value"] == DEMO_BANK_BALANCE and o["last_entry"] == "2026-10-05" and o["days_behind"] == 13
    assert o["statement_newer"] is True  # vipiskada 1C dan keyingi to'lovlar bor
