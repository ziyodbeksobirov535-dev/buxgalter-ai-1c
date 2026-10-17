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


def test_unknown_contract_is_created_on_posting():
    """Yangi shartnoma bo'yicha birinchi to'lov: ko'rib chiqishda yaratilmaydi, 1C ga yozishda yaratiladi."""
    import asyncio
    import core
    from tests.conftest import DEMO_FIRM
    csv = ("Sana;Hujjat №;Debet;Kredit;Kontragent;STIR;To'lov maqsadi\n"
           "16.10.2026;901;5 000,00;;TOSHKENT QURILISH MATERIALLARI MChJ;304567890;Договор 999 от 01.10.2026 за товар\n").encode()
    s = core.get_session("c2", DEMO_FIRM)

    async def scenario():
        snap = await core.load_statement(s, "v.csv", csv)
        r = snap["rows"][0]
        assert r["contract"]["source"] == "new" and r["selectable"] and "№999" in r["contract"]["label"]
        onec = core.get_onec(DEMO_FIRM)
        before = len(onec.t.db["Catalog_ДоговорыКонтрагентов"])
        preview = await core.preview_payload(s, "vipiska", r["id"])
        assert len(onec.t.db["Catalog_ДоговорыКонтрагентов"]) == before  # ko'rib chiqish hech narsa yaratmaydi
        assert "ДоговорКонтрагента_Key" not in preview["payload"]
        res = await core.post(s, "vipiska", [r["id"]])
        assert res["ok"] == 1 and "yangi shartnoma" in res["results"][0]["message"]
        new = onec.t.db["Catalog_ДоговорыКонтрагентов"][-1]
        assert len(onec.t.db["Catalog_ДоговорыКонтрагентов"]) == before + 1
        assert new["Номер"] == "999" and new["Дата"] == "2026-10-01T00:00:00" and new["ВидДоговора"] == "СПоставщиком"
        draft = onec.t.db["Document_СписаниеСРасчетногоСчета"][-1]
        assert draft["ДоговорКонтрагента_Key"] == new["Ref_Key"]
        # keyingi vipiskada shu shartnoma endi "topildi" — ikkinchi marta yaratilmaydi
        again = await core.load_statement(core.get_session("c3", DEMO_FIRM), "v2.csv", csv.replace(b"901", b"902"))
        assert again["rows"][0]["contract"]["source"] == "purpose"
    asyncio.run(scenario())


def test_didox_vat_split_44_10():
    import asyncio
    import core
    from tests.conftest import DEMO_FIRM
    s = core.get_session("vat", DEMO_FIRM)
    snap = asyncio.run(core.load_didox(s))
    new = next(r for r in snap["rows"] if r["status"] == "new")
    assert (new["item_account"], new["vat_account"], new["account"]) == ("29.10", "44.10", "60.10")
    assert new["posting_note"] == "Dt 29.10 + Dt 44.10 (QQS 12%) — Kt 60.10"
    payload = asyncio.run(core.preview_payload(s, "didox", new["id"]))["payload"]
    line = payload["Товары"][0]
    assert line["СчетУчета_Key"] and line["СчетУчетаНДС_Key"]
