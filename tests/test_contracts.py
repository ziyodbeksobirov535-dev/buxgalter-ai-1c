"""To'lov maqsadidan shartnoma raqami va sanasini ajratib olish (O'zbek / rus yozilish usullari)."""
import pytest

from services_vipiska import extract_contract


@pytest.mark.parametrize("purpose, number, date", [
    ("00098 оплата за кабель в ассортименте согл дог№50 от 10.09.2026", "50", "2026-09-10"),
    ("Перечисляется оплата сог-но дог№271 от 22.09.2026г. за кабель.", "271", "2026-09-22"),
    ("00098Предоплата 100% за кабель с-но дог №432 от 15.10.2026 г.", "432", "2026-10-15"),
    ("11.10.2026 yildagi 398 shartnomaga asosan mahsulot olish uchun", "398", "2026-10-11"),
    ("00111 15.10.2026yil Shartnoma N_434 uchun to?lov", "434", "2026-10-15"),
    ("11.09.2026 yildagi 240 sonli shartnomaga asosan  Кабель olish uchun", "240", "2026-09-11"),
    ("0009816 10 2026й №440 шартномага асосан кабел сотиб олиш учун", "440", None),
    ("сог дог №437 от 16.10.2026 за Кабель в ассортименте", "437", "2026-10-16"),
    ("П/О, 11.10.2026ЙИЛДАГИ  №399 СОНЛИ ШАРТНОМАГА АСОСАН КАБЕЛЬ", "399", "2026-10-11"),
    ("00111 Договор 439 от 16.10.2026 год. Кабель в ассортименте.", "439", "2026-10-16"),
    ("17-sonli shartnoma bo'yicha mahsulot uchun to'lov", "17", None),
    ("05/26-sonli shartnoma bo'yicha qishloq xo'jaligi mahsulotlari", "05/26", None),
    ("Shartnoma № A-12/3 dan 01.02.2026 bo'yicha", "A-12/3", "2026-02-01"),
])
def test_contract_reference_is_found(purpose, number, date):
    ref = extract_contract(purpose)
    assert ref is not None and ref["number"] == number and ref["date"] == date


@pytest.mark.parametrize("purpose", [
    "~2001108601262903212160093~200835711~36 Ижтимоий солиқ учун тўлов sentabr 2026",
    "За документ №172 S=100 000 000,00 Тариф",
    "Bank xizmatlari uchun komissiya",
    "",
])
def test_no_false_contract(purpose):
    assert extract_contract(purpose) is None


def test_contract_from_purpose_is_matched_in_1c():
    import asyncio
    import core
    from tests.conftest import DEMO_FIRM
    s = core.get_session("c", DEMO_FIRM)
    snap = asyncio.run(core.load_sample(s))
    rows = {r["number"]: r for r in snap["rows"]}
    assert rows["52"]["contract"]["source"] == "purpose" and "№23" in rows["52"]["contract"]["label"]   # "23-sonli shartnoma"
    assert rows["131"]["contract"]["source"] == "purpose" and "№14" in rows["131"]["contract"]["label"]
    assert rows["53"]["contract"]["source"] in ("history", "default")  # soliq: raqam yo'q — tarixdan
    payload = asyncio.run(core.preview_payload(s, "vipiska", rows["52"]["id"]))["payload"]
    assert payload["ДоговорКонтрагента_Key"] == rows["52"]["contract"]["key"]


def test_unknown_contract_number_blocks_row():
    import asyncio
    import core
    from tests.conftest import DEMO_FIRM
    csv = ("Sana;Hujjat №;Debet;Kredit;Kontragent;STIR;To'lov maqsadi\n"
           "16.10.2026;901;5 000,00;;TOSHKENT QURILISH MATERIALLARI MChJ;304567890;Договор 999 от 01.10.2026 за товар\n").encode()
    snap = asyncio.run(core.load_statement(core.get_session("c2", DEMO_FIRM), "v.csv", csv))
    r = snap["rows"][0]
    assert r["contract"]["source"] == "missing" and not r["selectable"]
    assert any("№999" in b for b in r["blockers"])
