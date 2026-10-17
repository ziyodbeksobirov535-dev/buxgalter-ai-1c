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
