"""Didox chiqim faktura qoralamasi: haqiqiy Didox/1C chaqirilmaydi (soxta transport va sun'iy ma'lumot)."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import core
import db
import main
import services_didox_draft as dd
from services_ehf import vat_rate

SELLER, BUYER = '000000001', '000000002'
SALE = {'ref': 'sale-1', 'entity': dd.SALE_ENTITY, 'posted': True, 'number': 'S-7', 'date': '2026-01-10',
        'inn': BUYER, 'counterparty': 'Synthetic buyer', 'amount': 112.0, 'contract_key': 'contract-1',
        'items': [{'name': 'Synthetic cable', 'ikpu': '00000000000000001', 'qty': 2, 'total': 112.0,
                   'vat_sum': 12.0, 'vat_rate': 12.0, 'unit_key': 'unit-1'}]}
SETTINGS = {'id': 7, 'inn': SELLER, 'didox_password': 'synthetic-password', 'didox_drafts_enabled': True,
            'didox_partner_token': 'synthetic-partner'}


@pytest.fixture(autouse=True)
def clean_tokens():
    dd._tokens.clear(); dd._failed.clear()


def row():
    return {'id': 'r-1', 'flow': 'out', 'reconcile': 'onec_only', 'match': dict(SALE)}


class FakeOneC:
    def __init__(self):
        async def get(entity, key):
            return {dd.E_UNITS: {'Description': 'м'}, dd.E_CONTRACT: {'Номер': 'C-1', 'Дата': '2025-12-01T00:00:00'}}[entity]
        self.t = SimpleNamespace(get=get)


def didox(calls, login_status=200):
    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path, request.headers.get('user-key')))
        assert request.headers.get('Partner-Authorization') == 'synthetic-partner'
        path = request.url.path
        if path == f'/v1/auth/{SELLER}/password/ru':
            assert json.loads(request.content) == {'password': 'synthetic-password'}
            return httpx.Response(login_status, json={'token': 'synthetic-token'} if login_status == 200 else {'message': 'bad'})
        if path == '/v1/profile':
            return httpx.Response(200, json={'name': 'Synthetic seller', 'tin': SELLER, 'account': '20208000000000000001', 'mfo': '00001'})
        if path == f'/v1/profile/{BUYER}':
            return httpx.Response(200, json={'name': 'Synthetic buyer LLC'})
        if path == '/v1/measures/all':
            return httpx.Response(200, json=[{'id': '1', 'name': 'шт'}, {'id': '2', 'name': 'м'}])
        if path == '/v1/documents/002/create':
            body = json.loads(request.content)
            assert body['SellerTin'] == SELLER and body['BuyerTin'] == BUYER
            return httpx.Response(200, json={'_id': 'draft-1'})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_only_login_and_invoice_draft_posts_are_allowed():
    dd.check_allowed('GET', 'v1/documents/abc')
    dd.check_allowed('POST', f'v1/auth/{SELLER}/password/ru')
    dd.check_allowed('POST', 'v1/documents/002/create')
    for method, path in [('POST', 'v1/documents/abc/sign'), ('POST', 'v1/documents/abc/delete/draft'),
                         ('POST', 'v1/documents/abc/reject'), ('DELETE', 'v1/documents/abc'),
                         ('POST', 'v1/documents/075/create'), ('PUT', 'v1/profile/update')]:
        with pytest.raises(dd.DraftError):
            dd.check_allowed(method, path)


def test_vat_rate_from_1c_enum():
    assert vat_rate('НДС12') == 12 and vat_rate('БезНДС') == 0 and vat_rate('НДС0') == 0 and vat_rate('') is None


def test_prepare_builds_body_from_1c_sale_and_create_posts_once():
    calls = []
    prepared = asyncio.run(dd.prepare(row(), SETTINGS, FakeOneC(), didox(calls)))
    assert prepared['ready'] and not prepared['problems']
    body = prepared['body']
    product = body['ProductList']['Products'][0]
    assert product['Count'] == 2 and product['Summa'] == 50 and product['DeliverySum'] == 100
    assert product['VatSum'] == 12 and product['DeliverySumWithVat'] == 112 and product['MeasureId'] == '2'
    assert body['ProductList']['HasVat'] and body['ContractDoc'] == {'ContractNo': 'C-1', 'ContractDate': '2025-12-01'}
    assert body['FacturaDoc'] == {'FacturaNo': 'S-7', 'FacturaDate': '2026-01-10'}
    doc_id = asyncio.run(dd.create(prepared, SETTINGS, didox(calls)))
    assert doc_id == 'draft-1'
    posts = [c for c in calls if c[0] == 'POST']
    # token keshlangan: ikkinchi bosqichda qayta login yo'q
    assert [p[1] for p in posts] == [f'/v1/auth/{SELLER}/password/ru', '/v1/documents/002/create']
    assert all(c[2] == 'synthetic-token' for c in calls if c[1] != f'/v1/auth/{SELLER}/password/ru')


def test_missing_ikpu_and_unknown_unit_block_the_draft():
    sale = dict(SALE, items=[dict(SALE['items'][0], ikpu='', unit='рулон')])
    body, problems, lines = dd.build_body(sale, {'tin': SELLER, 'name': 'S'}, {'tin': BUYER, 'name': 'B'}, {'м': '2'})
    assert body is None and len(problems) == 2 and lines


def test_rejected_password_is_not_retried():
    calls = []
    for _ in range(2):
        with pytest.raises(dd.DraftError):
            asyncio.run(dd.prepare(row(), SETTINGS, FakeOneC(), didox(calls, login_status=422)))
    assert len([c for c in calls if c[0] == 'POST']) == 1


def test_only_posted_1c_sale_without_ehf_is_draftable():
    assert dd.draftable(row()) == ''
    assert dd.draftable(dict(row(), reconcile='matched'))
    assert dd.draftable(dict(row(), match=dict(SALE, posted=False)))
    assert dd.draftable(dict(row(), match=dict(SALE, entity='Document_СчетФактураВыданный')))


def _client_with_row(role='owner', enabled=True):
    user = db.create_user('synthetic-' + role, 'synthetic-password', role, firm_ids=[1])
    db.update_firm(1, {'inn': SELLER, 'didox_drafts_enabled': enabled, 'didox_password': 'synthetic-password'})
    s = core.get_session(core.session_id(user['id'], 1), 1)
    s.didox = [row()]
    client = TestClient(main.app, base_url='http://127.0.0.1:8000')
    client.post('/api/auth/login', json={'username': 'synthetic-' + role, 'password': 'synthetic-password'})
    return client


def test_endpoint_flow_blocks_duplicates_and_marks_unknown(monkeypatch):
    prepared = {'ready': True, 'body': {'x': 1}, 'revision': 'a' * 16, 'sale': {'number': 'S-7'}, 'lines': [], 'problems': []}
    async def fake_prepare(*a, **k): return dict(prepared)
    outcomes = iter(['draft-1'])
    async def fake_create(*a, **k): return next(outcomes)
    monkeypatch.setattr(dd, 'prepare', fake_prepare)
    monkeypatch.setattr(dd, 'create', fake_create)
    with _client_with_row() as client:
        r = client.post('/api/didox/drafts/r-1/prepare')
        assert r.status_code == 200 and 'body' not in r.json()
        assert client.post('/api/didox/drafts/r-1/create', json={'revision': 'b' * 16, 'confirm': True}).status_code == 409
        r = client.post('/api/didox/drafts/r-1/create', json={'revision': 'a' * 16, 'confirm': True})
        assert r.json()['doc_id'] == 'draft-1' and r.json()['draft']['status'] == 'created'
        assert client.post('/api/didox/drafts/r-1/prepare').json()['existing']['doc_id'] == 'draft-1'
    # javob noma'lum (tarmoq xatosi) — band qilingan holat qoladi, qayta yuborilmaydi
    db.update_firm(1, {'inn': SELLER})
    async def broken(*a, **k): raise dd.DraftError('timeout')
    monkeypatch.setattr(dd, 'create', broken)
    other = dict(row(), id='r-2', match=dict(SALE, ref='sale-2'))
    with TestClient(main.app, base_url='http://127.0.0.1:8000') as client:
        client.post('/api/auth/login', json={'username': 'synthetic-owner', 'password': 'synthetic-password'})
        s = next(v for v in core.SESSIONS.values() if v.firm_id == 1)
        s.didox = [other]
        client.post('/api/didox/drafts/r-2/prepare')
        assert client.post('/api/didox/drafts/r-2/create', json={'revision': 'a' * 16, 'confirm': True}).status_code == 502
        assert db.didox_draft(1, 'sale-2')['status'] == 'unknown'


def test_endpoint_requires_setting_and_post_role():
    with _client_with_row(enabled=False) as client:
        assert client.post('/api/didox/drafts/r-1/prepare').status_code == 403
    with _client_with_row(role='viewer') as client:
        assert client.post('/api/didox/drafts/r-1/prepare').status_code == 403


def test_without_partner_token_no_login_is_attempted():
    calls = []
    with pytest.raises(dd.DraftError, match='hamkor kaliti'):
        asyncio.run(dd.prepare(row(), dict(SETTINGS, didox_partner_token=''), FakeOneC(), didox(calls)))
    assert calls == []
