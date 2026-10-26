"""Real 1C HTTP mijozining xatti-harakati — internetga chiqmasdan (httpx.MockTransport)."""
import asyncio
import urllib.parse

import httpx

import services_1c
from services_1c import HttpOData, OneCService

BLOCKED = ('﻿{"exception":{"descr":"Неверно указан пользователь или пароль","inner":{"descr":'
           '"Превышено допустимое количество ошибок при вводе имени и пароля.\\nВход для данного пользователя '
           'заблокирован до 18.10.2099 1:02:21"}}}')


def service(handler, user="user@x.uz", password="pw"):
    t = HttpOData("https://1c.uzcloud.uz/a/bpuz/1234", user, password)
    t.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=(user, password))
    return OneCService(t)


def counter(response):
    calls = []

    def handler(req):
        calls.append(req)
        return response()
    return handler, calls


def poke(svc, times=5):
    async def go():
        last = None
        for _ in range(times):
            last = await svc.status()
        return last
    return asyncio.run(go())


def test_uzcloud_redirect_401_is_detected_and_not_retried():
    handler, calls = counter(lambda: httpx.Response(
        301, headers={"Location": "https://1c.uzcloud.uz/availability/?url=x&mode=",
                      "WWW-Authenticate": 'Basic realm="1C:Enterprise 8.3"'}))
    status = poke(service(handler))
    assert len(calls) == 1 and "login yoki parol" in status["error"]


def test_uzcloud_redirect_404():
    handler, calls = counter(lambda: httpx.Response(
        301, headers={"Location": "https://1c.uzcloud.uz/availability/?url=x&code=404"}))
    status = poke(service(handler), times=1)
    assert "404" in status["error"]


def test_blocked_user_is_remembered_even_with_other_passwords():
    handler, calls = counter(lambda: httpx.Response(402, text=BLOCKED))
    status = poke(service(handler))
    assert len(calls) == 1 and "BLOKLANGAN" in status["error"]
    for pw in ("a", "b", "c"):
        assert "bloklangan" in poke(service(handler, password=pw), times=1)["error"]
    poke(service(handler, user="USER@X.UZ", password="d"), times=1)
    assert len(calls) == 1  # o'sha login — katta-kichik harfdan qat'i nazar
    poke(service(handler, user="odata_api", password="e"), times=1)
    assert len(calls) == 2  # boshqa login ta'sirlanmaydi
    assert services_1c.BLOCK_FILE.exists()


def test_empty_password_sends_nothing():
    handler, calls = counter(lambda: httpx.Response(200, json={}))
    status = poke(service(handler, password=""))
    assert calls == [] and "kiritilmagan" in status["error"]


def test_odata_filter_rendering():
    from datetime import datetime

    from services_1c import DT, Cast, Cond, Guid, render_filter
    f = render_filter([Cond("ИНН", "eq", "30'1"), Cond("Контрагент_Key", "eq", Guid("g")),
                       Cond("Date", "ge", DT(datetime(2026, 10, 1))), Cond("Owner", "eq", Cast("o", "Catalog_X")),
                       Cond("Posted", "eq", True), Cond("НазначениеПлатежа", "substringof", "komissiya")])
    assert f == ("ИНН eq '30''1' and Контрагент_Key eq guid'g' and Date ge datetime'2026-10-01T00:00:00' and "
                 "Owner eq cast(guid'o', 'Catalog_X') and Posted eq true and "
                 "substringof('komissiya', НазначениеПлатежа)")


def _odata_server(entity_sets: list[str]):
    """Login to'g'ri, lekin faqat berilgan obyektlar nashr qilingan 1C."""
    calls = []
    meta = "".join(f'<EntitySet Name="{e}" EntityType="StandardODATA.{e}"/>' for e in entity_sets)

    def handler(req):
        calls.append(str(req.url))
        path = httpx.URL(str(req.url)).path
        if path.endswith("$metadata"):
            return httpx.Response(200, text=f"<edmx>{meta}</edmx>")
        if path.rstrip("/").endswith("standard.odata"):
            return httpx.Response(200, json={"value": [{"name": e, "url": e} for e in entity_sets]})
        return httpx.Response(404, text="Not found")
    return handler, calls


def test_empty_odata_publication_is_reported_clearly():
    handler, _ = _odata_server([])
    status = poke(service(handler), times=1)
    assert status["ok"] is False and "nashr qilinmagan" in status["error"] and "Настройка стандартного" in status["error"]


def test_missing_entity_404_names_the_entity():
    handler, _ = _odata_server(["Catalog_Контрагенты"])
    svc = service(handler)

    async def go():
        try:
            await svc.organization()
        except services_1c.ODataError as e:
            return str(e)
    assert "«Catalog_Организации» OData tarkibida nashr qilinmagan" in asyncio.run(go())


def test_payload_is_filtered_by_1c_metadata():
    meta = ('<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx"><Schema xmlns="http://schemas.microsoft.com/ado/2009/11/edm">'
            '<EntityType Name="Document_ПоступлениеНаРасчетныйСчет"><Property Name="Ref_Key"/><Property Name="Date"/>'
            '<Property Name="СуммаДокумента"/><Property Name="РасшифровкаПлатежа" Type="Collection(StandardODATA.Document_ПоступлениеНаРасчетныйСчет_РасшифровкаПлатежа_RowType)"/>'
            '</EntityType><ComplexType Name="Document_ПоступлениеНаРасчетныйСчет_РасшифровкаПлатежа_RowType">'
            '<Property Name="LineNumber"/><Property Name="СуммаПлатежа"/><Property Name="СчетУчетаРасчетовПоАвансам_Key"/>'
            '</ComplexType><EntityContainer><EntitySet Name="Document_ПоступлениеНаРасчетныйСчет"/></EntityContainer></Schema></edmx:Edmx>')
    handler, _ = counter(lambda: httpx.Response(200, text=meta))
    svc = service(handler)
    payload = {"Date": "2026-10-16T12:00:00", "СуммаДокумента": 5.0, "НетТакогоПоля": 1,
               "РасшифровкаПлатежа": [{"LineNumber": "1", "СуммаПлатежа": 5.0, "СчетУчетаРасчетовПоАвансам_Key": "k",
                                       "СпособПогашенияЗадолженности": "Автоматически"}]}
    clean, dropped = asyncio.run(svc.filter_payload("Document_ПоступлениеНаРасчетныйСчет", payload))
    assert "НетТакогоПоля" not in clean and clean["РасшифровкаПлатежа"][0]["СчетУчетаРасчетовПоАвансам_Key"] == "k"
    assert dropped == ["НетТакогоПоля", "РасшифровкаПлатежа.СпособПогашенияЗадолженности"]


# ---------------------------------------------------------------- O'zbekiston konfiguratsiyasi (uzcloud BP UZ 3.0)
def test_query_string_uses_percent20_not_plus():
    """uzcloud dagi 1C kirillcha $filter da "+" ni bo'sh joy deb o'qimaydi — %20 bo'lishi shart."""
    from services_1c import Cast, Cond, encode_query, render_filter
    q = encode_query({"$filter": render_filter([Cond("ВидОперации", "eq", "ОплатаПоставщику"),
                                                 Cond("Контрагент", "eq", Cast("g-1", "Catalog_Контрагенты"))])})
    assert "+" not in q and "%20eq%20" in q and q.startswith("$filter=")
    assert "cast(guid'g-1',%20'Catalog_" in q


COMPOSITE_META = """<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx"><edmx:DataServices>
<Schema xmlns="http://schemas.microsoft.com/ado/2009/11/edm" Namespace="StandardODATA">
<EntityType Name="Document_СписаниеСРасчетногоСчета"><Property Name="Ref_Key" Type="Edm.Guid"/>
<Property Name="Контрагент" Type="Edm.String"/><Property Name="Контрагент_Type" Type="Edm.String"/>
<Property Name="НомерВходящегоДокумента" Type="Edm.String"/><Property Name="ДатаВходящегоДокумента" Type="Edm.DateTime"/>
<Property Name="СуммаДокумента" Type="Edm.Double"/><Property Name="Date" Type="Edm.DateTime"/>
<Property Name="DeletionMark" Type="Edm.Boolean"/><Property Name="Posted" Type="Edm.Boolean"/></EntityType>
<EntityType Name="Catalog_Контрагенты"><Property Name="ИНН" Type="Edm.String"/><Property Name="ПИНФЛ" Type="Edm.String"/></EntityType>
<EntityType Name="Catalog_Номенклатура"><Property Name="Description" Type="Edm.String"/></EntityType>
<EntityContainer Name="EnterpriseV8"><EntitySet Name="Document_СписаниеСРасчетногоСчета"/></EntityContainer>
</Schema></edmx:DataServices></edmx:Edmx>"""


def test_composite_counterparty_field_dedup_and_pinfl():
    import asyncio
    from datetime import date
    from services_1c import Cast, DemoOData, OneCService

    class UzLive(DemoOData):
        mode = "live"

        def __init__(self):
            super().__init__()
            self.seen = []
            for d in self.db["Document_СписаниеСРасчетногоСчета"]:  # Контрагент_Key -> kompozit
                d["Контрагент"], d["Контрагент_Type"] = d.pop("Контрагент_Key"), "StandardODATA.Catalog_Контрагенты"
            self.db["Catalog_Контрагенты"].append({"Ref_Key": "yatt-1", "Description": "YaTT TEST", "ИНН": "",
                                                   "ПИНФЛ": "30101900000011", "DeletionMark": False})

        async def query(self, entity, conds=None, **kw):
            self.seen += [c for c in conds or []]
            return await super().query(entity, conds, **kw)

        async def metadata_text(self):
            return COMPOSITE_META

    t = UzLive()
    onec = OneCService(t)
    doc = t.db["Document_СписаниеСРасчетногоСчета"][0]

    async def scenario():
        dup = await onec.check_duplicate("Document_СписаниеСРасчетногоСчета", doc["НомерВходящегоДокумента"],
                                         date.fromisoformat(doc["ДатаВходящегоДокумента"][:10]), doc["СуммаДокумента"],
                                         doc["Контрагент"])
        payload = await onec.counterparty_payload("Document_СписаниеСРасчетногоСчета", "k-1")
        yatt = await onec.find_counterparty("30101900000011")
        nom = await onec.find_nomenclature("Sement M400 (50 kg)", "123")  # КодИКПУ yo'q — nomi bo'yicha
        return dup, payload, yatt, nom
    dup, payload, yatt, nom = asyncio.run(scenario())
    assert dup["status"] == "duplicate" and dup["kind"] == "exact"
    assert any(c.field == "Контрагент" and isinstance(c.value, Cast) for c in t.seen)
    assert payload == {"Контрагент": "k-1", "Контрагент_Type": "StandardODATA.Catalog_Контрагенты"}
    assert yatt["Ref_Key"] == "yatt-1"
    assert nom is not None and not any(c.field == "КодИКПУ" for c in t.seen)
    assert OneCService.counterparty_of(doc) == doc["Контрагент"]


def test_payment_line_account_wins_over_stale_header():
    """BP UZ: sarlavhadagi СчетУчетаРасчетовСКонтрагентом standart 42.20 bo'lib qoladi, provodka qatordagi schyotda."""
    from services_1c import OneCService, fmt_account, norm_account
    doc = {"ВидОперации": "ОплатаПоставщику", "СчетУчетаРасчетовСКонтрагентом_Key": "hdr-4220",
           "РасшифровкаПлатежа": [{"СчетУчетаРасчетовСКонтрагентом_Key": "line-6010",
                                   "СчетУчетаРасчетовПоАвансам_Key": "line-4310", "ДоговорКонтрагента_Key": "c-1"}]}
    p = OneCService._extract_pattern(doc)
    assert (p["account_key"], p["advance_key"], p["contract_key"]) == ("line-6010", "line-4310", "c-1")
    tax = {"ВидОперации": "ПеречислениеНалога", "СчетУчетаРасчетовСКонтрагентом_Key": "hdr-6410"}
    assert OneCService._extract_pattern(tax)["account_key"] == "hdr-6410"  # qatorsiz — sarlavha
    assert (fmt_account("4530.1"), norm_account("45.30.1"), fmt_account("6010")) == ("45.30.1", "4530.1", "60.10")


def test_documents_without_lines_take_account_from_postings():
    """Bank komissiyasi/soliq: "Расшифровка платежа" yo'q, sarlavhada standart 42.20 — haqiqiy schyot provodkada (94.30)."""
    import asyncio
    from datetime import datetime
    from services_1c import DemoOData, OneCService
    meta = COMPOSITE_META.replace('<EntitySet Name="Document_СписаниеСРасчетногоСчета"/>',
                                  '<EntitySet Name="Document_СписаниеСРасчетногоСчета"/>'
                                  '<EntitySet Name="AccountingRegister_Хозрасчетный"/>')

    class UzLive(DemoOData):
        mode = "live"

        def __init__(self):
            super().__init__()
            acc = {a["Code"]: a["Ref_Key"] for a in self.db["ChartOfAccounts_Хозрасчетный"]}
            self.acc = acc
            self.db["Document_СписаниеСРасчетногоСчета"] = [{
                "Ref_Key": "fee-1", "Number": "1", "Date": "2026-10-01T00:00:00", "Posted": True, "DeletionMark": False,
                "ВидОперации": "КомиссияБанка", "СуммаДокумента": 100.0, "Контрагент_Key": "bank-1",
                "СчетУчетаРасчетовСКонтрагентом_Key": acc["4220"], "РасшифровкаПлатежа": []}]
            self.db["AccountingRegister_Хозрасчетный_RecordType"] = [{
                "Recorder": "fee-1", "Recorder_Type": "StandardODATA.Document_СписаниеСРасчетногоСчета",
                "AccountDr_Key": acc["9430"], "AccountCr_Key": acc["5110"], "Сумма": 100.0,
                "Period": "2026-10-01T00:00:00"}]

        async def metadata_text(self):
            return meta.replace("Контрагент\" Type", "Контрагент_Key\" Type")

        async def register_records(self, entity, ref):
            return [r for r in self.db["AccountingRegister_Хозрасчетный_RecordType"] if r["Recorder"] == ref]

    t = UzLive()
    onec = OneCService(t)

    async def scenario():
        learned = await onec.learn_history("Document_СписаниеСРасчетногоСчета", "bank-1")
        bulk = await onec.bank_postings("Document_СписаниеСРасчетногоСчета", datetime(2026, 1, 1))
        return learned, bulk
    learned, bulk = asyncio.run(scenario())
    assert learned["account_code"] == "94.30"  # sarlavhadagi 42.20 emas
    assert bulk == {"fee-1": t.acc["9430"]}


def test_dedup_without_counterparty_uses_number_date_amount():
    """Bank komissiyasi: vipiskada kontragent — firmaning o'zi, 1C da bank. Raqam + sana + summa bo'yicha aniqlanadi."""
    import asyncio
    from datetime import date
    from services_1c import DemoOData, OneCService
    onec = OneCService(DemoOData())
    doc = onec.t.db["Document_СписаниеСРасчетногоСчета"][0]
    day = date.fromisoformat(doc["ДатаВходящегоДокумента"][:10])
    dup = asyncio.run(onec.check_duplicate("Document_СписаниеСРасчетногоСчета", doc["НомерВходящегоДокумента"], day,
                                           doc["СуммаДокумента"], None))
    assert dup["status"] == "duplicate" and dup["kind"] == "exact"
    new = asyncio.run(onec.check_duplicate("Document_СписаниеСРасчетногоСчета", "999999", day, 1.0, None))
    assert new["status"] == "new"


def test_nomenclature_found_by_ikpu_in_classifier_field():
    """BP UZ 3.0: КодИКПУ yo'q, MXIK КодИзКлассификатора rekvizitida."""
    import asyncio
    from services_1c import DemoOData, OneCService
    meta = COMPOSITE_META.replace('<Property Name="Description" Type="Edm.String"/></EntityType>',
                                  '<Property Name="Description" Type="Edm.String"/>'
                                  '<Property Name="КодИзКлассификатора" Type="Edm.String"/></EntityType>')

    class UzLive(DemoOData):
        mode = "live"

        async def metadata_text(self):
            return meta
    onec = OneCService(UzLive())
    nom = asyncio.run(onec.find_nomenclature("boshqa nom", "07214001001000000"))
    assert nom and nom["Description"] == "Armatura A500C d12"


def test_duplicate_found_even_if_1c_used_another_counterparty():
    """Soliq: vipiskada G'aznachilik, 1C da tuman DSI — raqam + sana + summa mos bo'lsa "1C da bor" (ikki marta emas)."""
    import asyncio
    from datetime import date
    from services_1c import DemoOData, OneCService
    onec = OneCService(DemoOData())
    doc = next(d for d in onec.t.db["Document_СписаниеСРасчетногоСчета"] if d["НомерВходящегоДокумента"])
    other = next(c["Ref_Key"] for c in onec.t.db["Catalog_Контрагенты"] if c["Ref_Key"] != doc["Контрагент_Key"])
    dup = asyncio.run(onec.check_duplicate("Document_СписаниеСРасчетногоСчета", doc["НомерВходящегоДокумента"],
                                           date.fromisoformat(doc["ДатаВходящегоДокумента"][:10]), doc["СуммаДокумента"], other))
    assert dup["status"] == "duplicate" and dup["kind"] == "other_cp" and "boshqa kontragentga" in dup["reason"]
    new = asyncio.run(onec.check_duplicate("Document_СписаниеСРасчетногоСчета", doc["НомерВходящегоДокумента"],
                                           date.fromisoformat(doc["ДатаВходящегоДокумента"][:10]), 1.0, other))
    assert new["status"] == "new"


def test_bank_fee_uses_counterparty_from_1c_fee_history_even_if_marked_deleted():
    import asyncio
    from services_1c import DemoOData, OneCService

    class UzLive(DemoOData):
        mode = "live"

        def __init__(self):
            super().__init__()
            self.db["Catalog_Контрагенты"].append({"Ref_Key": "self-cp", "Description": "TEST FIRMA (o'zi)", "ИНН": "300000777",
                                                   "DeletionMark": True})
            self.db["Document_СписаниеСРасчетногоСчета"].append({"Ref_Key": "fee-1", "Date": "2026-10-12T00:00:00",
                                                                 "Posted": True, "DeletionMark": False,
                                                                 "ВидОперации": "КомиссияБанка", "Контрагент_Key": "self-cp"})

        async def metadata_text(self):
            return ""
    onec = OneCService(UzLive())
    assert asyncio.run(onec.find_counterparty("300000777")) is None  # o'chirishga belgilangan — oddiy qidiruvda yo'q
    assert asyncio.run(onec.fee_counterparty())["Ref_Key"] == "self-cp"


def test_draft_mirrors_latest_real_document_of_same_operation():
    """Komissiya: 1C da qatorsiz, shartnomasiz, sarlavha 42.20 (provodka 94.30 ni 1C sozlamasidan oladi) — qoralama
    ham xuddi shunday; soliq: soliq turi (Налог_Key) va davri (o'tgan oy) namunadan."""
    import asyncio
    from services_1c import DemoOData, OneCService
    from services_vipiska import mirror_template
    onec = OneCService(DemoOData())
    onec.t.db["Document_СписаниеСРасчетногоСчета"].append({
        "Ref_Key": "fee-t", "Date": "2026-10-12T00:00:00", "Posted": True, "DeletionMark": False,
        "ВидОперации": "КомиссияБанка", "Контрагент_Key": "self", "ДоговорКонтрагента_Key": "00000000-0000-0000-0000-000000000000",
        "СчетУчетаРасчетовСКонтрагентом_Key": "acc-4220", "СпособПогашенияЗадолженности": "НеПогашать",
        "СтатусВыписки": "Обработан", "КратностьДокумента": "1", "СчетКонтрагента_Key": "self-acc", "РасшифровкаПлатежа": []})
    payload = {"ДоговорКонтрагента_Key": "ctr", "СчетУчетаРасчетовСКонтрагентом_Key": "acc-9430",
               "РасшифровкаПлатежа": [{"СчетУчетаРасчетовСКонтрагентом_Key": "acc-9430"}], "КратностьДокумента": 1}
    row = {"entity": "Document_СписаниеСРасчетногоСчета", "counterparty_key": "self", "date": "2026-10-16"}
    asyncio.run(mirror_template(row, payload, onec, "КомиссияБанка", "acc-9430"))
    assert "РасшифровкаПлатежа" not in payload and payload["ДоговорКонтрагента_Key"] is None
    assert payload["СчетУчетаРасчетовСКонтрагентом_Key"] == "acc-4220" and payload["СтатусВыписки"] == "Обработан"
    assert payload["КратностьДокумента"] == "1" and payload["СчетКонтрагента_Key"] == "self-acc"

    onec.t.db["Document_СписаниеСРасчетногоСчета"].append({
        "Ref_Key": "tax-t", "Date": "2026-10-15T00:00:00", "Posted": True, "DeletionMark": False,
        "ВидОперации": "ПеречислениеНалога", "Контрагент_Key": "gni", "Налог_Key": "ndfl", "СубконтоДт1": "Налог",
        "РасшифровкаПлатежа": []})
    payload = {"РасшифровкаПлатежа": [{}]}
    row = {"entity": "Document_СписаниеСРасчетногоСчета", "counterparty_key": "gni", "date": "2026-11-15",
           "tax_template": {"template_ref": "tax-t", "tax_key": "ndfl"}}
    asyncio.run(mirror_template(row, payload, onec, "ПеречислениеНалога", None))
    assert payload["Налог_Key"] == "ndfl" and payload["НалоговыйПериод"] == "2026-10-01T00:00:00"
    assert payload["СубконтоДт1"] == "Налог" and "РасшифровкаПлатежа" not in payload


def test_write_403_shows_1c_text_and_does_not_block_reads(monkeypatch):
    """Login to'g'ri, lekin 1C yozishga ruxsat bermadi (403): 1C matni ko'rsatiladi, o'qish to'xtamaydi."""
    import services_1c
    monkeypatch.setattr(services_1c, "WRITES_ENABLED", True)  # HTTP xatosini sinash uchun qulf ochiladi
    import pytest
    from services_1c import ODataError

    def handler(req):
        if req.method == "POST":
            return httpx.Response(403, json={"odata.error": {"code": "", "message": {"lang": "ru", "value": "Недостаточно прав для добавления"}}})
        return httpx.Response(200, json={"value": [{"Ref_Key": "x"}]})
    svc = service(handler)
    with pytest.raises(ODataError, match="yozishga .* ruxsat bermadi .*Недостаточно прав"):
        asyncio.run(svc.t.create("Document_СписаниеСРасчетногоСчета", {"a": 1}))
    assert svc.t.auth_error is None
    assert asyncio.run(svc.t.query("Catalog_Контрагенты", top=1)) == [{"Ref_Key": "x"}]  # o'qish ishlayveradi


def test_write_403_from_hosting_nginx_is_explained(monkeypatch):
    """So'rov 1C gacha yetmay hosting proksi-serveri (nginx) yozishni yopsa — HTML emas, tushunarli sabab."""
    import services_1c
    monkeypatch.setattr(services_1c, "WRITES_ENABLED", True)  # HTTP xatosini sinash uchun qulf ochiladi
    import pytest
    from services_1c import ODataError
    html = "<html><head><title>403 Forbidden</title></head><body><center><h1>403 Forbidden</h1></center><hr><center>nginx</center></body></html>"

    def handler(req):
        if req.method == "POST":
            return httpx.Response(403, text=html, headers={"content-type": "text/html"})
        return httpx.Response(200, json={"value": []})
    svc = service(handler)
    with pytest.raises(ODataError, match="Hosting serveri \\(nginx\\).*1C gacha") as e:
        asyncio.run(svc.t.create("Document_СписаниеСРасчетногоСчета", {"a": 1}))
    assert "<html" not in str(e.value) and svc.t.auth_error is None


def test_hard_write_lock_sends_nothing_to_1c(monkeypatch):
    """Egasi qarori: haqiqiy 1C ga hech qanday yozuv yuborilmaydi — so'rov umuman jo'natilmaydi; o'qish ishlaydi."""
    import pytest
    import services_1c
    from services_1c import ODataError
    assert services_1c.WRITES_ENABLED is False
    sent = []

    def handler(req):
        sent.append(req.method)
        return httpx.Response(200, json={"value": []})
    svc = service(handler)
    for call in (svc.t.create("Document_СписаниеСРасчетногоСчета", {"a": 1}),
                 svc.t.patch("Document_СписаниеСРасчетногоСчета", "x", {"DeletionMark": True}),
                 svc.t.create("Catalog_Контрагенты", {"a": 1})):
        with pytest.raises(ODataError, match="dastur darajasida yopiq"):
            asyncio.run(call)
    assert sent == []
    asyncio.run(svc.t.query("Catalog_Контрагенты", top=1))
    assert sent == ["GET"]


def test_when_opened_only_bank_and_purchase_documents_allowed(monkeypatch):
    import pytest
    import services_1c
    from services_1c import ODataError
    monkeypatch.setattr(services_1c, "WRITES_ENABLED", True)
    assert services_1c.WRITE_ALLOWLIST == {"Document_ПоступлениеНаРасчетныйСчет", "Document_СписаниеСРасчетногоСчета",
                                           "Document_ПоступлениеТоваровУслуг"}
    sent = []

    def handler(req):
        sent.append(urllib.parse.unquote(req.url.path).rsplit("/", 1)[-1])
        return httpx.Response(201, json={"Ref_Key": "r", "Number": "1"})
    svc = service(handler)
    for entity in ("Catalog_Контрагенты", "Document_РеализацияТоваровУслуг", "Document_ПриходныйКассовыйОрдер"):
        with pytest.raises(ODataError, match="faqat bank vipiskasi va kirim fakturalari"):
            asyncio.run(svc.t.create(entity, {"a": 1}))
    asyncio.run(svc.t.create("Document_ПоступлениеТоваровУслуг", {"a": 1}))
    assert sent == ["Document_ПоступлениеТоваровУслуг"]


def test_cache_expires_so_changes_in_1c_are_seen(monkeypatch):
    """1C da o'zgargan qoldiq ko'pi bilan CACHE_TTL dan keyin dastur va agentga ko'rinadi."""
    import services_1c
    clock = [1000.0]
    monkeypatch.setattr(services_1c.time, "monotonic", lambda: clock[0])
    calls = []

    async def factory():
        calls.append(1)
        return len(calls)
    svc = service(lambda req: httpx.Response(200, json={"value": []}))
    assert asyncio.run(svc._cached(("balance", "51.10"), factory)) == 1
    assert asyncio.run(svc._cached(("balance", "51.10"), factory)) == 1   # bir tahlil ichida — keshdan
    clock[0] += services_1c.CACHE_TTL + 1
    assert asyncio.run(svc._cached(("balance", "51.10"), factory)) == 2   # muddati o'tdi — 1C dan qayta
    assert asyncio.run(svc._cached(("acc_by_code", "51.10"), factory)) == 3
    clock[0] += services_1c.CACHE_TTL + 1
    assert asyncio.run(svc._cached(("acc_by_code", "51.10"), factory)) == 3  # hisoblar rejasi — uzoqroq saqlanadi


def test_batch_query_sends_only_get_requests():
    """batch_query 1C ga POST ($batch) yubormaydi — yozish qulfini chetlab o'tmasin."""
    sent = []

    def handler(req):
        sent.append(req.method)
        return httpx.Response(200, json={"value": [{"Ref_Key": "x"}]})
    svc = service(handler)
    res = asyncio.run(svc.t.batch_query([{"entity": "Catalog_Контрагенты", "top": 1},
                                         {"entity": "Catalog_Банки", "top": 1}]))
    assert res == [[{"Ref_Key": "x"}], [{"Ref_Key": "x"}]] and set(sent) == {"GET"}
