"""E-DOCS login/profil tekshiruvi: faqat sun'iy HTTP javoblar."""
import asyncio
import json

import httpx
import pytest

from services_edocs import EdocsError
from services_edocs_api import check_connection

SETTINGS = {'edocs_login': 'synthetic-login', 'edocs_password': 'synthetic-password', 'inn': '000000000'}


def test_login_then_profile_only():
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        assert str(request.url).startswith('https://api.edocs.uz/v1.0.1/')
        if request.url.path.endswith('/loginpassword'):
            assert json.loads(request.content) == {'login': SETTINGS['edocs_login'], 'password': SETTINGS['edocs_password']}
            return httpx.Response(200, json={'token': 'synthetic-token'})
        assert request.headers['Authorization'] == 'Bearer synthetic-token'
        return httpx.Response(200, json={'tin': SETTINGS['inn']})
    result = asyncio.run(check_connection(SETTINGS, transport=httpx.MockTransport(handle)))
    assert result['ok'] and 'synthetic-token' not in json.dumps(result)
    assert calls == [('POST', '/v1.0.1/loginpassword'), ('GET', '/v1.0.1/profile')]


@pytest.mark.parametrize('status', [401, 403, 429, 500, 302])
def test_login_failure_is_not_retried_or_followed(status):
    count = 0
    def handle(request):
        nonlocal count
        count += 1
        return httpx.Response(status, headers={'Location': 'https://example.org/'},
                              json={'message': 'synthetic-password'})
    with pytest.raises(EdocsError) as error:
        asyncio.run(check_connection(SETTINGS, transport=httpx.MockTransport(handle)))
    assert count == 1 and 'synthetic-password' not in str(error.value)


@pytest.mark.parametrize('body', [{}, {'token': None}, [], {'token': ''}])
def test_invalid_login_body_blocks_profile(body):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=body)
    with pytest.raises(EdocsError):
        asyncio.run(check_connection(SETTINGS, transport=httpx.MockTransport(handle)))
    assert len(calls) == 1


def test_wrong_firm_blocks_success():
    def handle(request):
        return httpx.Response(200, json={'token': 'synthetic-token'} if request.method == 'POST' else {'tin': '000000001'})
    with pytest.raises(EdocsError, match='STIRi mos emas'):
        asyncio.run(check_connection(SETTINGS, transport=httpx.MockTransport(handle)))


def test_network_error_has_no_request_secrets():
    def handle(request):
        raise httpx.ConnectError('synthetic-password', request=request)
    with pytest.raises(EdocsError) as error:
        asyncio.run(check_connection(SETTINGS, transport=httpx.MockTransport(handle)))
    assert 'synthetic-password' not in str(error.value)


def test_missing_firm_never_sends_credentials():
    def handle(request):
        raise AssertionError('Network request must not happen')
    with pytest.raises(EdocsError, match='STIR'):
        asyncio.run(check_connection({**SETTINGS, 'inn': ''}, transport=httpx.MockTransport(handle)))


def invoice(fid='synthetic1', buyer='000000000'):
    return {'_id': fid, 'type': 'factura', 'status': 'signed', 'totalSumWithVat': 112,
            'data': {'buyertin': buyer, 'sellertin': '000000001', 'seller': {'name': 'Synthetic supplier'},
                     'facturadoc': {'facturano': 'TEST1', 'facturadate': '2026-01-01'},
                     'productlist': {'products': [{'name': 'Synthetic item', 'catalogcode': '00000000000000000',
                         'count': 2, 'summa': 50, 'deliverysumwithvat': 112, 'vatrate': 12, 'withoutvat': False}]}}}


def test_invoices_paginate_details_and_never_write():
    from services_edocs_api import fetch_invoices
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith('/loginpassword'):
            return httpx.Response(200, json={'token': 'synthetic-token'})
        assert request.method == 'GET'
        if path.endswith('/profile'):
            return httpx.Response(200, json={'tin': SETTINGS['inn']})
        if path.endswith('/documents'):
            assert request.url.params['io'] == 'in' and request.url.params['status'] == 'signed'
            page = int(request.url.params['page'])
            return httpx.Response(200, json={'docs': [{'_id': f'synthetic{page}'}], 'totalDocs': 2,
                'page': page, 'hasNextPage': page == 1, 'nextPage': 2 if page == 1 else None})
        return httpx.Response(200, json=invoice(path.rsplit('/', 1)[-1]))
    rows = asyncio.run(fetch_invoices(SETTINGS, transport=httpx.MockTransport(handle)))
    assert [r['id'] for r in rows] == ['synthetic1', 'synthetic2']
    assert rows[0]['items'][0]['qty'] == 2 and rows[0]['items'][0]['vat_rate'] == 12
    assert calls.count(('POST', '/v1.0.1/loginpassword')) == 1
    assert len(calls) == 6


@pytest.mark.parametrize('failure', ['duplicate', 'count_changed', 'incomplete', 'too_many', 'bad_id', 'missing_next'])
def test_partial_pagination_is_not_returned(failure):
    from services_edocs_api import fetch_invoices
    def handle(request):
        if request.url.path.endswith('/loginpassword'):
            return httpx.Response(200, json={'token': 'synthetic-token'})
        if request.url.path.endswith('/profile'):
            return httpx.Response(200, json={'tin': SETTINGS['inn']})
        assert request.url.path.endswith('/documents'), 'Details must not be requested for a partial list'
        page = int(request.url.params['page'])
        body = {'docs': [{'_id': f'synthetic{page}'}], 'page': page, 'totalDocs': 2,
                'hasNextPage': page == 1, 'nextPage': 2 if page == 1 else None}
        if failure == 'duplicate': body['docs'][0]['_id'] = 'synthetic1'
        if failure == 'count_changed' and page == 2: body['totalDocs'] = 3
        if failure == 'incomplete': body['hasNextPage'] = False
        if failure == 'too_many': body['totalDocs'] = 1001
        if failure == 'bad_id': body['docs'][0]['_id'] = '../profile'
        if failure == 'missing_next': body.pop('nextPage')
        return httpx.Response(200, json=body)
    with pytest.raises(EdocsError):
        asyncio.run(fetch_invoices(SETTINGS, transport=httpx.MockTransport(handle)))


@pytest.mark.parametrize('problem', ['buyer', 'total', 'nan', 'vat_missing', 'status'])
def test_invoice_validation(problem):
    from services_edocs_api import map_invoice
    doc = invoice()
    if problem == 'buyer': doc['data']['buyertin'] = '000000099'
    if problem == 'total': doc['totalSumWithVat'] = 999
    if problem == 'nan': doc['data']['productlist']['products'][0]['count'] = float('nan')
    if problem == 'vat_missing': doc['data']['productlist']['products'][0].pop('vatrate')
    if problem == 'status': doc['status'] = 'rejected'
    with pytest.raises(EdocsError):
        map_invoice(doc, SETTINGS['inn'])


def test_environment_credentials_bound_to_firm(monkeypatch):
    from services_edocs_api import _credentials
    monkeypatch.setenv('EDOCS_LOGIN', 'synthetic-login')
    monkeypatch.setenv('EDOCS_PASSWORD', 'synthetic-password')
    monkeypatch.setenv('EDOCS_INN', '000000000')
    settings, expected = _credentials({'inn': '000000000'})
    assert settings['edocs_password'] == 'synthetic-password' and expected == '000000000'
    with pytest.raises(EdocsError):
        _credentials({'inn': '000000099'})
    with pytest.raises(EdocsError):
        _credentials({'inn': '000000000', 'edocs_login': 'another-login'})


def test_native_invoices_reach_existing_pipeline_without_demo(monkeypatch):
    import core
    import db
    import services_edocs_api as api
    from services_1c import OneCService, OfflineOData
    from unittest.mock import AsyncMock
    db.update_firm(1, {'edocs_enabled': True})
    monkeypatch.setattr(api, 'fetch_invoices', AsyncMock(return_value=[api.map_invoice(invoice(), SETTINGS['inn'])]))
    monkeypatch.setattr(core, 'onec_for', lambda session: OneCService(OfflineOData()))
    session = core.get_session('native-edocs', 1)
    snapshot = asyncio.run(core.load_didox(session))
    assert 'E-DOCS API' in snapshot['source']
    assert len(snapshot['rows']) == 1 and snapshot['rows'][0]['status'] == 'unchecked'
    assert not snapshot['rows'][0]['posted']
