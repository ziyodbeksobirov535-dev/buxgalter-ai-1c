"""Real 1C HTTP mijozining xatti-harakati — internetga chiqmasdan (httpx.MockTransport)."""
import asyncio

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
