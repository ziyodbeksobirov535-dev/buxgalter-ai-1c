"""To'lov maqsadi va soliq kodi bo'yicha schyot aniqlash (hisoblar rejasi BHMS 21)."""
from datetime import date

import pytest

from services_vipiska import BankRow, classify_purpose, rule_account

TREASURY = ("Ўзбекистон Республикаси Молия вазирлиги", "201122919")


def row(purpose, direction="chiqim", name="TEST MCHJ", inn="300000011", account_no=""):
    return BankRow(date(2026, 10, 16), "1", direction, 1000.0, name, inn, account_no=account_no, purpose=purpose)


@pytest.mark.parametrize("purpose, name_inn, account", [
    # G'aznachilik formati: ~hisob~STIR~soliq kodi
    ("~2001108601262903212160093~200000001~36 Ижтимоий соли? учун тўлов sentabr 2026", TREASURY, "65.20"),
    ("~1000218604262903111103093~200000001~46 Жисмоний шахслар даромадидан олинадиган соли? учун тўлов", TREASURY, "64.10"),
    ("101 Фу?ароларнинг жам?ариб бориладиган пенсия бадалига ажратма учун тўлов", ("Яшнобод тумани ДСИ", "200000001"), "65.20"),
    ("QQS bo'yicha sentabr uchun to'lov", TREASURY, "64.10"),
    ("Ijtimoiy soliq sentabr 2026", TREASURY, "65.20"),
    ("НДФЛ за сентябрь", TREASURY, "64.10"),
    ("INPS 0.1% sentabr", TREASURY, "65.20"),
])
def test_tax_codes(purpose, name_inn, account):
    r = row(purpose, name=name_inn[0], inn=name_inn[1])
    assert classify_purpose(r)["account"] == account


@pytest.mark.parametrize("purpose, direction, account", [
    ("Oktyabr oyi ish haqi plastik kartalarga", "chiqim", "67.10"),
    ("Перечисление зарплаты за октябрь", "chiqim", "67.10"),
    ("Ta'sischiga dividend to'lovi", "chiqim", "66.10"),
    ("Проценты по кредиту за октябрь", "chiqim", "96.10"),
    ("Kredit foizi to'lovi", "chiqim", "96.10"),
    ("Погашение кредита (основной долг)", "chiqim", "68.10"),
    ("Moliyaviy yordam qaytarish", "chiqim", "68.10"),
    ("Возвратная финансовая помощь", "kirim", "68.10"),
    ("Korporativ kartani to'ldirish (KPK)", "chiqim", "55.20"),
    ("Пополнение КПК", "chiqim", "55.20"),
    ("Humo terminal orqali tushum", "kirim", "57.10"),
    ("Эквайринг Uzcard за 15.10", "kirim", "57.10"),
    ("Inkassatsiya tushumi", "kirim", "57.10"),
])
def test_keywords(purpose, direction, account):
    assert classify_purpose(row(purpose, direction))["account"] == account


@pytest.mark.parametrize("purpose, direction", [
    ("23-sonli shartnoma bo'yicha qurilish materiallari uchun to'lov, QQS 12% bilan", "chiqim"),  # QQS bilan — soliq EMAS
    ("Оплата за товар, в т.ч. НДС 12%", "chiqim"),
    ("Shartnoma bo'yicha qarzni uzish uchun", "chiqim"),     # yetkazib beruvchiga qarz — kredit EMAS
    ("Terminal (uskuna) sotib olish uchun", "chiqim"),       # terminal xaridi — tushum EMAS
    ("14-sonli shartnoma bo'yicha sotilgan tovarlar uchun", "kirim"),
])
def test_no_false_positives(purpose, direction):
    assert classify_purpose(row(purpose, direction)) is None


def test_supplier_payment_with_vat_stays_supplier():
    assert rule_account(row("Оплата за товар, в т.ч. НДС 12%")) == "60.10"


def test_tax_code_account_learned_from_firm_history():
    """G'aznachilik: schyot — shu firmadagi shu soliq kodli ENG OXIRGI to'lovdan (barcha soliq to'lovlari bo'yicha,
    qaysi soliq organiga yozilganidan qat'i nazar); amaliyot o'zgarsa (JShDS 44.20.1 -> 44.30) yangisi olinadi."""
    import asyncio
    from services_vipiska import tax_code_history

    class Fake:
        mode = "live"

        async def tax_docs(self, entity):
            p = "~10002186042629031~200000001~{} x"
            return [{"Ref_Key": "a", "Date": "2026-08-17T10:00:00", "НазначениеПлатежа": p.format(46)},
                    {"Ref_Key": "b", "Date": "2026-09-15T10:00:00", "НазначениеПлатежа": p.format(46)},
                    {"Ref_Key": "c", "Date": "2026-10-15T10:00:00", "НазначениеПлатежа": p.format(46)},
                    {"Ref_Key": "d", "Date": "2026-10-15T10:00:00", "НазначениеПлатежа": "08102 101 Fuqarolarning pensiya"}]

        async def bank_postings(self, entity, since):
            return {"a": "k4420", "b": "k4420", "c": "k4430", "d": "k4530"}

        async def account_code(self, key):
            return {"k4420": "44.20.1", "k4430": "44.30", "k4530": "45.30.1"}.get(key)

    hist = asyncio.run(tax_code_history(Fake(), "Document_СписаниеСРасчетногоСчета", None, "46"))
    assert hist["account"] == "44.30" and "15.10.2026" in hist["reason"] and "44.20.1×2" in hist["reason"]
    assert asyncio.run(tax_code_history(Fake(), "Document_СписаниеСРасчетногоСчета", None, "101"))["account"] == "45.30.1"
    assert asyncio.run(tax_code_history(Fake(), "Document_СписаниеСРасчетногоСчета", None, "36")) is None


def test_tax_code_with_leading_payment_code():
    from services_vipiska import tax_code_of
    assert tax_code_of("08102 101 Fuqarolarning jamg'arib boriladigan pensiya") == "101"
    assert tax_code_of("~10002186042629031~201122919~46 Jismoniy shaxslar") == "46"
    assert tax_code_of("08102 ~10002286042629031411020") is None
