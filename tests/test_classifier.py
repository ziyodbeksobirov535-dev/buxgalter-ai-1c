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
    """G'aznachilik: buxgalter kod 36 ni 45.10.1 (avans) ga olgan bo'lsa — standart 65.20 o'rniga shu taklif qilinadi;
    faqat soliq to'lovi hujjatlari hisoblanadi, tarix bo'lmasa — None (standart jadval ishlaydi)."""
    import asyncio
    from services_vipiska import tax_code_history

    class Fake:
        mode = "live"

        async def counterparty_docs(self, entity, kontr_key, top=100):
            return [{"Ref_Key": "a", "ВидОперации": "ПеречислениеНалога", "НазначениеПлатежа": "~10002186042629031~200000001~36 x"},
                    {"Ref_Key": "b", "ВидОперации": "ПеречислениеНалога", "НазначениеПлатежа": "~10002186042629031~200000001~36 y"},
                    {"Ref_Key": "c", "ВидОперации": "ОплатаПоставщику", "НазначениеПлатежа": "~10002186042629031~200000001~46 z"}]

        async def bank_postings(self, entity, since):
            return {"a": "k4510", "b": "k4510", "c": "k6010"}

        async def account_code(self, key):
            return {"k4510": "45.10.1", "k6010": "60.10"}.get(key)

    hist = asyncio.run(tax_code_history(Fake(), "Document_СписаниеСРасчетногоСчета", "kp", "36"))
    assert hist["account"] == "45.10.1" and (hist["hits"], hist["total"]) == (2, 2)
    assert asyncio.run(tax_code_history(Fake(), "Document_СписаниеСРасчетногоСчета", "kp", "46")) is None
