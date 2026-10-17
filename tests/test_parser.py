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


# ---------------------------------------------------------------- "Выписка лицевых счетов" (bank eksporti) formati
BANK_FORMAT = [
    [None, None, None, None, "Выписка лицевых счетов"],
    [None, "№ счёта 20208000100000000777", None, None, "Ответисполнитель Test"],
    [None, 'Наименование счёта ООО "TEST FIRMA"'],
    [None, "Остаток:", "Начало дня", 1000.0],
    ["№\nпп", "Дата\nдокумента", "Дата\nобработки", "№ док", "Наименование счёта", "ИНН", "№ счёта", "МФО",
     "Обороты по\nдебету", "Обороты по\nкредиту", "Назначение платежа"],
    [1, "15.10.2026", "16.10.2026", "16", "Ўзбекистон Республикаси Молия вазирлиги", "201122919",
     "23402000300100001010", "00014", 945000, 0, "~1000000000000000000000000~1"],
    [2, "16.10.2026", "16.10.2026", "101", '"ALFA TEST" MCHJ', "300000011", "20208000100000000011", "00083",
     100000000, 0, "00098 оплата за кабель"],
    [3, "16.10.2026", "16.10.2026", "1001", 'ООО "BETA TEST"', "300000021", "20208000100000000021", "00421",
     0, 6269303.56, "Перечисляется оплата"],
    [4, "16.10.2026", "16.10.2026", "90000001", 'ООО "TEST FIRMA"', "300000777", "16401000100000000777", "01158",
     100000, 0, "За документ №101 S=100 000 00"],
    [5, None, None, None, "Итого за весь период", None, None, None, 101045000, 6269303.56, "Входящее сальдо: 1 000"],
    [None, "Выписка лицевых счетов за 16.10.2026 по 18.10.2026"],
]


def test_bank_export_format_columns():
    st = _rows_from_table(BANK_FORMAT)
    assert [r.number for r in st.rows] == ["16", "101", "1001", "90000001"]  # "№ док", tartib raqami emas
    assert st.rows[1].counterparty_name == '"ALFA TEST" MCHJ'           # "Наименование счёта" — nomi
    assert st.rows[1].account_no == "20208000100000000011"                   # "№ счёта" — hisob raqami
    assert st.own_account == "20208000100000000777" and st.own_name == 'ООО "TEST FIRMA"'
    assert st.warnings == []  # "Итого" va sarlavha qatorlari — ogohlantirishsiz


def test_tax_and_bank_fee_defaults():
    from services_vipiska import BankRow, rule_account, special_kind
    from datetime import date
    tax = BankRow(date(2026, 10, 15), "16", "chiqim", 945000.0, "Ўзбекистон Республикаси Молия вазирлиги",
                  "201122919", purpose="~1000000000000000000000000~1")
    dsi = BankRow(date(2026, 10, 15), "18", "chiqim", 7875.0, "Яшнобод тумани ДСИ", "200000001", purpose="101 ...")
    fee = BankRow(date(2026, 10, 16), "90000001", "chiqim", 100000.0, 'ООО "TEST FIRMA"', "300000777",
                  account_no="16401000100000000777", purpose="За документ №101 S=100 000 00")
    assert rule_account(tax) == "64.10"                 # kodsiz G'aznachilik to'lovi — standart 64.10
    assert rule_account(dsi) == "65.20"                 # "101 … pensiya badali" — 65.20
    assert special_kind(fee, "300000777")[0] == "bank_fee" and rule_account(fee) == "94.20"
    own = BankRow(date(2026, 10, 16), "7", "chiqim", 5.0, "O'zimiz", "300000777", account_no="20208000999999999001",
                  purpose="O'z hisobimizga o'tkazma")
    assert special_kind(own, "300000777")[0] == "transfer"


def test_processing_date_is_kept_separately():
    st = _rows_from_table(BANK_FORMAT)
    tax = st.rows[0]  # hujjat 15.10, bank o'tkazgan 16.10
    assert str(tax.date) == "2026-10-15" and str(tax.posted_date) == "2026-10-16"
    only_posted = _rows_from_table([["Дата проводки", "№ док", "Дебет", "Кредит", "Наименование", "ИНН", "Назначение"],
                                    ["16.10.2026", "5", 10, 0, "A", "301234567", "x"]])
    assert str(only_posted.rows[0].date) == "2026-10-16"  # yagona sana ustuni bo'lsa ham o'qiladi
