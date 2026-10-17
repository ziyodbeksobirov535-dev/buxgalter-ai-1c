"""1C bilan fayl orqali ishlash: Excel eksportlarini o'qish, dublikat/shartnoma/schyot, qarz-avans va import fayli.
Barcha ma'lumotlar o'ylab topilgan (haqiqiy firma ma'lumotlari emas)."""
import asyncio
import io
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

import core
import db
import main
import services_files1c as f1c
from services_vipiska import BankRow, Statement

OWN_INN, OWN_ACC = "300000777", "20208000100000000777"


def xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


COUNTERPARTIES = xlsx([
    ["Контрагенты"], [],
    ["Наименование", "ИНН", "Полное наименование"],
    ['ООО "ALFA TEST"', 300000011, "ALFA TEST MCHJ"],
    ['"BETA TEST" MChJ', "300000021", ""],
    ["Gamma Savdo XK", "300000031", ""],
])

CONTRACTS = xlsx([
    ["Наименование", "Контрагент", "Номер", "Дата", "Вид договора"],
    ["Договор №45 от 01.02.2026", 'ООО "ALFA TEST"', "45", "01.02.2026", "С поставщиком"],
    ["Shartnoma 12", '"BETA TEST" MChJ', "12", datetime(2026, 3, 5), "С покупателем"],
])

JOURNAL = xlsx([
    ["Банковские выписки"],
    ["Дата", "Номер", "Вх. номер", "Поступление", "Списание", "Контрагент", "Вид операции", "Назначение платежа"],
    [datetime(2026, 9, 10), "0000-000101", "77", None, 5000000, 'ООО "ALFA TEST"', "Оплата поставщику",
     "Договор №45 от 01.02.2026 оплата за товар"],
    [datetime(2026, 9, 12), "0000-000102", "", 2000000, None, '"BETA TEST" MChJ', "Оплата от покупателя",
     "Shartnoma 12 bo'yicha"],
    [None, "Итого", None, 2000000, 5000000],
])

# Карточка счета 51: ikki qavatli sarlavha (Дебет/Кредит → Счет/Сумма); bitta hujjat ikki provodka (qarz + avans)
CARD_51 = xlsx([
    ["Карточка счета 51.10 за Сентябрь 2026 г."], [],
    ["Период", "Документ", "Аналитика Дт", "Аналитика Кт", "Дебет", None, "Кредит", None, "Текущее сальдо"],
    [None, None, None, None, "Счет", "Сумма", "Счет", "Сумма", None],
    [datetime(2026, 9, 10), "Списание с расчетного счета 0000-000101 от 10.09.2026 12:00:00",
     'ООО "ALFA TEST"\nДоговор №45 от 01.02.2026', "Основной счет", "6010", 5000000, "5110", None, 0],
    [datetime(2026, 9, 12), "Поступление на расчетный счет 0000-000102 от 12.09.2026 0:00:00",
     "Основной счет", '"BETA TEST" MChJ\nShartnoma 12', "5110", 1500000, "4010", None, 0],
    [datetime(2026, 9, 12), "Поступление на расчетный счет 0000-000102 от 12.09.2026 0:00:00",
     "Основной счет", '"BETA TEST" MChJ\nShartnoma 12', "5110", 500000, "6310", None, 0],
    [datetime(2026, 9, 20), "Списание с расчетного счета 0000-000103 от 20.09.2026 0:00:00",
     "Налог на доходы физических лиц", "Основной счет", "64.10", 945000, "51.10", None, 0],
    [datetime(2026, 9, 21), "Операция 0000-000004 от 21.09.2026", "Склад", "Поставщик", "1010", 1, "6010", None, 0],
    [None, "Обороты за период и сальдо на конец", None, None, None, 2000000, None, 5945000],
])

OSV = xlsx([
    ["Оборотно-сальдовая ведомость по счету 40.10 за 01.09.2026 - 30.09.2026"], [],
    ["Счет", "Сальдо на начало периода", None, "Обороты за период", None, "Сальдо на конец периода", None],
    [None, "Дебет", "Кредит", "Дебет", "Кредит", "Дебет", "Кредит"],
    ["40.10", 0, 0, 0, 0, 7000000, 0],
    ["Gamma Savdo XK", 0, 0, 0, 0, 3000000, 0],
    ["Договор №7", 0, 0, 0, 0, 3000000, 0],
    ['"BETA TEST" MChJ', 0, 0, 0, 0, 4000000, 0],
    ["Итого", 0, 0, 0, 0, 7000000, 0],
])


# ---------------------------------------------------------------- fayl turini aniqlash va o'qish
@pytest.mark.parametrize("content, kind, count", [
    (COUNTERPARTIES, "counterparties", 3), (CONTRACTS, "contracts", 2), (JOURNAL, "bank_docs", 2),
    (CARD_51, "postings", 3), (OSV, "osv", 2),
])
def test_detects_export_kind(content, kind, count):
    parsed = f1c.parse_export("x.xlsx", content)
    assert (parsed["kind"], parsed["count"]) == (kind, count)


def test_card_groups_postings_per_document_and_keeps_debt_and_advance_parts():
    docs = {d["number"]: d for d in f1c.parse_export("k.xlsx", CARD_51)["records"]}
    assert docs["0000-000101"]["direction"] == "chiqim" and docs["0000-000101"]["account"] == "60.10"
    assert docs["0000-000101"]["counterparty"] == 'ООО "ALFA TEST"'
    assert docs["0000-000101"]["contract"] == "Договор №45 от 01.02.2026"
    split = docs["0000-000102"]
    assert split["amount"] == 2_000_000 and split["accounts"] == {"40.10": 1_500_000, "63.10": 500_000}
    assert split["account"] == "40.10"  # hisob-kitob schyoti asosiy, avans — qo'shimcha
    assert docs["0000-000103"]["account"] == "64.10"
    assert "0000-000004" not in docs  # bank (51) bilan bog'liq bo'lmagan provodka olinmaydi


def test_osv_skips_contract_rows_and_reads_closing_balance():
    bal = f1c.parse_export("o.xlsx", OSV)["balances"]
    assert bal["as_of"] == "2026-09-30"
    assert bal["accounts"]["40.10"] == {"gamma savdo": {"name": "Gamma Savdo XK", "dt": 3_000_000, "kt": 0},
                                        "beta test": {"name": '"BETA TEST" MChJ', "dt": 4_000_000, "kt": 0}}


def test_unknown_or_old_format_is_explained():
    with pytest.raises(f1c.ExportFileError, match="xlsx"):
        f1c.parse_export("eski.xls", b"\xd0\xcf\x11\xe0")
    with pytest.raises(f1c.ExportFileError):
        f1c.parse_export("x.xlsx", xlsx([["a", "b"], [1, 2]]))


def test_journal_and_card_merge_into_one_document_and_reupload_adds_nothing():
    store = f1c.empty_store()
    f1c.merge(store, f1c.parse_export("j.xlsx", JOURNAL), "j.xlsx")
    f1c.merge(store, f1c.parse_export("k.xlsx", CARD_51), "k.xlsx")
    again = f1c.merge(store, f1c.parse_export("k.xlsx", CARD_51), "k.xlsx")
    assert again["added"] == 0 and len(store["docs"]) == 3
    doc = next(d for d in store["docs"] if d["number"] == "0000-000101")
    assert (doc["in_number"], doc["account"], doc["operation"]) == ("77", "60.10", "ОплатаПоставщику")
    assert len(store["files"]) == 2  # bir xil fayl qayta yuklansa — ro'yxatda bitta


@pytest.mark.parametrize("a, b", [('ООО "ALFA TEST"', "Alfa Test MChJ"), ("«Gamma Savdo» XK", "GAMMA SAVDO"),
                                  ("YaTT Karimov A.", "Karimov A")])
def test_name_key_ignores_legal_form_and_quotes(a, b):
    assert f1c.name_key(a) == f1c.name_key(b)


# ---------------------------------------------------------------- fayldan tuzilgan 1C nusxasi bilan tahlil
@pytest.fixture
def firm():
    return db.create_firm({"name": "TEST FIRMA", "inn": OWN_INN, "bank_accounts": OWN_ACC})["id"]


def load(firm_id, *files):
    store = f1c.empty_store()
    for name, content in files:
        f1c.merge(store, f1c.parse_export(name, content), name)
    f1c.save_store(firm_id, store)


def statement(*rows) -> Statement:
    st = Statement(rows=list(rows), own_account=OWN_ACC, own_inn=OWN_INN, own_name="TEST FIRMA", source="t.xlsx")
    for r in st.rows:
        r.id = r.key()
    return st


def analyze(firm_id, st):
    session = core.get_session(f"t{firm_id}", firm_id)
    session.statement = st
    asyncio.run(core._analyze_vipiska(session))
    return session, {r["number"]: r for r in session.vipiska}


ALFA = ('ООО "ALFA TEST"', "300000011", "20208000100000000011", "00083")
BETA = ('"BETA TEST" MChJ', "300000021", "20208000100000000021", "00421")


def bank(d, number, direction, amount, cp, purpose="", posted=None):
    name, inn, acc, mfo = cp
    return BankRow(d, number, direction, amount, name, inn, mfo=mfo, account_no=acc, purpose=purpose, posted_date=posted)


def test_firm_without_odata_works_from_exports(firm):
    load(firm, ("c.xlsx", COUNTERPARTIES), ("d.xlsx", CONTRACTS), ("j.xlsx", JOURNAL), ("k.xlsx", CARD_51))
    assert core.get_onec(firm).mode == "file"
    session, rows = analyze(firm, statement(
        bank(date(2026, 9, 10), "77", "chiqim", 5_000_000, ALFA, "Договор №45 от 01.02.2026 оплата"),  # 1C da bor
        bank(date(2026, 9, 12), "5", "kirim", 2_000_000, BETA, "Shartnoma 12"),                       # raqamsiz mos
        bank(date(2026, 10, 2), "78", "chiqim", 3_000_000, ALFA, "Договор №45 от 01.02.2026 оплата"),  # yangi
        bank(date(2026, 10, 3), "79", "chiqim", 1_000_000, ("Yangi Firma MChJ", "300000099", "", ""), "Shartnoma №9"),
    ))
    assert rows["77"]["status"] == "duplicate" and rows["77"]["dup_kind"] == "exact"
    assert rows["5"]["status"] == "duplicate" and rows["5"]["dup_kind"] == "weak"
    new = rows["78"]
    assert new["status"] == "new" and new["account"] == "60.10" and new["account_source"] == "history"
    assert new["contract"]["source"] == "purpose" and "1C da topildi" in new["contract"]["label"]
    unknown = rows["79"]  # 1C eksportida yo'q kontragent — to'siq emas: 1C yuklashda STIR bo'yicha yaratadi
    assert unknown["status"] == "new" and not unknown["blockers"] and f1c.exportable(unknown)
    snap = core.snapshot(session)
    assert snap["files_mode"] and not snap["stale"] and snap["exportable"] == 2


def test_counterparty_found_by_name_when_export_has_no_inn(firm):
    load(firm, ("k.xlsx", CARD_51))  # kartochkada STIR yo'q — faqat nomlar
    _s, rows = analyze(firm, statement(bank(date(2026, 10, 2), "80", "chiqim", 1_000_000,
                                            ("ALFA TEST MCHJ", "300000011", "", ""), "tovar uchun")))
    assert rows["80"]["counterparty_1c_name"] == 'ООО "ALFA TEST"' and rows["80"]["account"] == "60.10"


def test_live_odata_failure_falls_back_to_exports(firm, monkeypatch):
    from services_1c import ODataError
    db.update_firm(firm, {"onec_demo_mode": False, "onec_base_url": "https://1c.example.uz/odata", "onec_user": "u",
                          "onec_password": "p"})
    load(firm, ("c.xlsx", COUNTERPARTIES), ("j.xlsx", JOURNAL))

    async def broken(*a, **kw):
        raise ODataError("OData: obyektlar nashr qilinmagan")
    monkeypatch.setattr(core.get_onec(firm).t, "query", broken)
    session, rows = analyze(firm, statement(bank(date(2026, 9, 10), "77", "chiqim", 5_000_000, ALFA)))
    assert rows["77"]["status"] == "duplicate"
    assert session.analyzed_with["vipiska"].startswith(core.FILES)
    with pytest.raises(ODataError, match="import fayli"):  # fayl rejimida 1C ga to'g'ridan-to'g'ri yozilmaydi
        asyncio.run(core.post(session, "vipiska", [r["id"] for r in session.vipiska]))


def test_new_exports_make_analysis_stale(firm):
    load(firm, ("c.xlsx", COUNTERPARTIES))
    session, _ = analyze(firm, statement(bank(date(2026, 9, 10), "77", "chiqim", 5_000_000, ALFA)))
    assert not core.snapshot(session)["stale"]
    load(firm, ("c.xlsx", COUNTERPARTIES), ("j.xlsx", JOURNAL))
    assert core.snapshot(session)["stale"]


def test_osv_balance_splits_payment_into_debt_and_advance(firm):
    load(firm, ("c.xlsx", COUNTERPARTIES), ("o.xlsx", OSV))
    _s, rows = analyze(firm, statement(
        bank(date(2026, 10, 1), "1", "kirim", 3_000_000, BETA),  # qarz 4 mln: to'liq yopadi
        bank(date(2026, 10, 2), "2", "kirim", 1_500_000, BETA),  # qolgan qarz 1 mln: 1 mln + 0.5 mln avans
        bank(date(2026, 9, 29), "3", "kirim", 700_000, BETA),    # vedomost sanasigacha — qoldiqda bor
    ))
    assert rows["1"]["balance_split"] == {"account": "40.10", "as_of": "2026-09-30", "debt": 4_000_000,
                                          "close": 3_000_000, "advance": 0, "found": True}
    assert rows["2"]["balance_split"]["close"] == 1_000_000 and rows["2"]["balance_split"]["advance"] == 500_000
    assert "63.10" in rows["2"]["settlement_note"] and "500 000.00" in rows["2"]["settlement_note"]
    assert "balance_split" not in rows["3"]


# ---------------------------------------------------------------- 1C ga import fayli
def test_client_bank_file_format():
    rows = [
        {"kind": "bank", "id": "a", "date": "2026-10-02", "posted_date": "2026-10-03", "number": "78",
         "direction": "chiqim", "amount": 3_000_000.0, "counterparty_name": 'ООО "ALFA TEST"',
         "counterparty_inn": "300000011", "account_no": "20208000100000000011", "mfo": "00083",
         "purpose": "Шартнома №45 бўйича тўлов\nқисман"},
        {"kind": "bank", "id": "b", "date": "2026-10-01", "number": "5", "direction": "kirim", "amount": 1_250_000.5,
         "counterparty_name": "BETA", "counterparty_inn": "300000021", "account_no": "", "mfo": "",
         "purpose": "oplata"},
    ]
    raw = f1c.build_client_bank(rows, OWN_ACC, OWN_INN, "TEST FIRMA", created=datetime(2026, 10, 18, 9, 0))
    text = raw.decode("cp1251")
    lines = text.split("\r\n")
    assert lines[0] == "1CClientBankExchange" and "КонецФайла" in lines
    assert "ДатаНачала=01.10.2026" in lines and "ДатаКонца=03.10.2026" in lines
    out = text[text.index("Номер=78"):]
    out = out[:out.index("КонецДокумента")]
    assert "ПлательщикСчет=" + OWN_ACC in out and "ПолучательИНН=300000011" in out
    assert "ДатаСписано=03.10.2026" in out and "Сумма=3000000.00" in out
    assert "НазначениеПлатежа=Шартнома N45 бўйича тўлов кисман" in out  # ғ/қ/ҳ → cp1251, bitta qator
    inc = text[text.index("Номер=5"):]
    assert "ПолучательСчет=" + OWN_ACC in inc and "ДатаПоступило=01.10.2026" in inc and "Сумма=1250000.50" in inc
    assert "ВсегоПоступило=1250000.50" in lines and "ВсегоСписано=3000000.00" in lines


def test_export_endpoint_only_new_rows_and_marks_them(firm):
    load(firm, ("c.xlsx", COUNTERPARTIES), ("j.xlsx", JOURNAL))
    user = db.create_user("buh", "parol12345", "accountant", "B", firm_ids=[firm])
    db.create_user("kuz", "parol12345", "viewer", "K", firm_ids=[firm])
    session = core.get_session(core.session_id(user["id"], firm), firm)
    session.statement = statement(bank(date(2026, 9, 10), "77", "chiqim", 5_000_000, ALFA),
                                  bank(date(2026, 10, 2), "78", "chiqim", 3_000_000, ALFA))
    asyncio.run(core._analyze_vipiska(session))
    c = TestClient(main.app, base_url="http://127.0.0.1:8000")
    h = {"X-Firm-Id": str(firm)}
    c.post("/api/auth/login", json={"username": "kuz", "password": "parol12345"})
    assert c.post("/api/vipiska/export-1c", headers=h, json={}).status_code == 403  # kuzatuvchi
    c.post("/api/auth/login", json={"username": "buh", "password": "parol12345"})
    dup_id = next(r["id"] for r in session.vipiska if r["number"] == "77")
    assert c.post("/api/vipiska/export-1c", headers=h, json={"ids": [dup_id]}).status_code == 400  # 1C da bor
    assert c.post("/api/vipiska/export-1c", headers=h, json={"expected_firm_id": 1}).status_code == 409
    r = c.post("/api/vipiska/export-1c", headers=h, json={})
    assert r.status_code == 200 and r.headers["x-rows"] == "1"
    text = r.content.decode("cp1251")
    assert "Номер=78" in text and "Номер=77" not in text
    assert c.post("/api/vipiska/export-1c", headers=h, json={}).status_code == 400  # ikkinchi marta — yangi yo'q
    assert db.list_audit(firm_id=firm)[0]["action"] == "onec_import_file"


def test_upload_endpoint_learns_rules_and_reports_bad_files(firm):
    db.create_user("buh", "parol12345", "accountant", "B", firm_ids=[firm])
    c = TestClient(main.app, base_url="http://127.0.0.1:8000")
    c.post("/api/auth/login", json={"username": "buh", "password": "parol12345"})
    files = [("files", ("kontragent.xlsx", COUNTERPARTIES)), ("files", ("kartochka.xlsx", CARD_51)),
             ("files", ("eski.xls", b"\xd0\xcf\x11\xe0"))]
    res = c.post("/api/1c-files/upload", headers={"X-Firm-Id": str(firm)}, files=files).json()
    assert [r["ok"] for r in res["results"]] == [True, True, False]
    assert res["summary"]["counts"]["docs"] == 3 and res["learned"]["added"] >= 1
    rules = {(r["match"], r["direction"]): r["account"] for r in db.list_rules(firm)}
    assert rules[("300000011", "chiqim")] == "60.10" and rules[("300000021", "kirim")] == "40.10"
    assert c.delete("/api/1c-files", headers={"X-Firm-Id": str(firm)}).json() == {"ok": True}
    assert c.get("/api/1c-files", headers={"X-Firm-Id": str(firm)}).json()["loaded"] is False
