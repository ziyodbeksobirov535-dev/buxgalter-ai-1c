import csv

import pytest

from services_vipiska import (StatementError, _rows_from_table, clean_account, parse_amount, parse_csv,
                              parse_direction, parse_statement)

HEADER = ["Sana", "№", "Summa", "Turi", "Kontragent", "STIR", "To'lov maqsadi"]


@pytest.mark.parametrize("raw, expected", [
    ("1.250.000", 1_250_000.0), ("1.250.000,50", 1_250_000.5), ("1 250 000,00", 1_250_000.0),
    ("1,250,000.00", 1_250_000.0), ("1.250", 1_250.0), ("1,25", 1.25), ("-500,00", -500.0), ("(300)", -300.0),
    ("12500000", 12_500_000.0), (45000000.0, 45_000_000.0), ("", 0.0), ("abc", None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("Kredit", "kirim"), ("Debet", "chiqim"), ("K", "kirim"), ("Д", "chiqim"), ("Приход", "kirim"),
    ("Списание", "chiqim"), ("Kirim", "kirim"), ("?", None),
])
def test_parse_direction(raw, expected):
    assert parse_direction(raw) == expected


def test_kredit_is_never_read_as_chiqim():
    st = _rows_from_table([HEADER, ["01.10.2026", "1", "100", "Kredit", "A", "301234567", "x"],
                           ["01.10.2026", "2", "200", "Debet", "B", "302345678", "y"]])
    assert [(r.number, r.direction) for r in st.rows] == [("1", "kirim"), ("2", "chiqim")]


def test_skipped_rows_are_reported_not_silently_dropped():
    st = _rows_from_table([
        HEADER,
        ["01.10.2026", "1", "1.250.000", "Debet", "A", "301234567", "x"],
        ["01.10.2026", "2", "abc", "Debet", "B", "302345678", "y"],
        ["32.13.2026", "3", "5", "Debet", "C", "303456789", "z"],
        ["01.10.2026", "4", "7", "???", "D", "304567890", "w"],
        ["Начальный остаток", "", "", "", "", "", ""],
    ])
    assert [(r.number, r.amount) for r in st.rows] == [("1", 1_250_000.0)]
    assert len(st.warnings) == 3
    assert any("summa o'qilmadi" in w for w in st.warnings)
    assert any("sana o'qilmadi" in w for w in st.warnings)
    assert any("turi aniqlanmadi" in w for w in st.warnings)


def test_twenty_digit_account_stored_as_number_is_rejected_with_warning():
    assert clean_account(20208000100123456001.0) == ("", True)
    assert clean_account("20208000100123456001") == ("20208000100123456001", False)
    st = _rows_from_table([["Sana", "№", "Debet", "Kredit", "Kontragent", "STIR", "Hisob raqami", "Maqsad"],
                           ["01.10.2026", "1", "", "100", "A", "301234567", 20208000100123456001.0, "x"]])
    assert st.rows[0].account_no == ""
    assert any("SON formatida" in w for w in st.warnings)


def test_csv_parsing_does_not_mutate_global_dialect():
    before = csv.excel.delimiter
    with pytest.raises(StatementError):
        parse_csv(b"Sana\n01.10.2026\n")
    assert csv.excel.delimiter == before


EXCHANGE = (
    "1CClientBankExchange\nВерсияФормата=1.03\nРасчСчет=20208000100123456001\n"
    "СекцияДокумент=Платежное поручение\nНомер=77\nДата=16.10.2026\nСумма=5400000.00\n"
    "ПлательщикСчет=20208000100123456001\nПлательщикИНН=305123456\nПолучательСчет=20208000700444555001\n"
    "ПолучательИНН=304567890\nПолучатель1=TOSHKENT QURILISH MATERIALLARI MChJ\nНазначениеПлатежа=Оплата\n"
    "КонецДокумента\n"
    "СекцияДокумент=Платежное поручение\nНомер=140\nДата=16.10.2026\nСумма=2100000.00\n"
    "ПлательщикСчет=20208000999999999001\nПлательщикИНН=399999999\nПлательщик1=X\n"
    "ПолучательСчет=20208000100123456001\nКонецДокумента\n"
    "СекцияДокумент=Платежное поручение\nНомер=141\nДата=99.99.2026\nСумма=1.00\nКонецДокумента\nКонецФайла\n"
)


def test_1c_exchange_cp1251_directions_and_warnings():
    st = parse_statement("kb.txt", EXCHANGE.encode("cp1251"))
    assert [(r.number, r.direction, r.amount, r.counterparty_inn) for r in st.rows] == [
        ("77", "chiqim", 5_400_000.0, "304567890"), ("140", "kirim", 2_100_000.0, "399999999")]
    assert any("№141" in w for w in st.warnings)


def test_currency_account_is_flagged():
    usd = EXCHANGE.replace("20208000100123456001", "20208840100123456001")
    st = parse_statement("kb.txt", usd.encode("cp1251"))
    assert st.currency_code == "840"
    assert "valyuta" in st.warnings[0].lower()
