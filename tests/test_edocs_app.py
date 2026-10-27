"""BUXGALTER AI ichidagi E-DOCS o'qish oqimi; tashqi xizmatlar soxta."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import core
import db
import main
import services_ai
import services_edocs_api
from services_1c import ODataError
from tests.test_edocs_api import invoice, SETTINGS


def test_app_load_period_read_only_reload_and_post_guard(monkeypatch):
    firm = db.create_firm({'name': 'Synthetic EHF', 'inn': '000000000', 'edocs_enabled': True})
    fetch = AsyncMock(return_value=[services_edocs_api.map_invoice(invoice(), SETTINGS['inn'])])
    monkeypatch.setattr(services_edocs_api, 'fetch_invoices', fetch)
    db.create_user('synthetic-owner', 'synthetic-password', 'owner')
    with TestClient(main.app, base_url='http://127.0.0.1:8000') as client:
        client.post('/api/auth/login', json={'username': 'synthetic-owner', 'password': 'synthetic-password'})
        h = {'X-Firm-Id': str(firm['id'])}
        r = client.post('/api/didox/load?date_from=2026-01-01&date_to=2026-01-31', headers=h)
        assert r.status_code == 200, r.text
        snap = r.json()
        assert snap['read_only'] and not snap['can_post'] and snap['loaded_at']
        assert snap['offline'] and snap['rows'][0]['status'] == 'unchecked'
        assert snap['rows'][0]['selectable'] is False
        assert 'E-DOCS' in snap['source']
        fetch.assert_awaited_once()
        assert fetch.call_args.kwargs == {'date_from': '2026-01-01', 'date_to': '2026-01-31'}
        reload = client.get('/api/session?mode=didox', headers=h).json()
        assert reload['loaded_at'] == snap['loaded_at'] and reload['read_only']
        post = client.post('/api/post', headers=h, json={'mode': 'didox', 'ids': [snap['rows'][0]['id']]})
        assert post.status_code == 502 and 'faqat' in post.json()['detail']
        # Sozlama o'chirilsa ham yuklangan haqiqiy E-DOCS qatorlari yozilmaydi.
        db.update_firm(firm['id'], {'edocs_enabled': False})
        again = client.post('/api/post', headers=h, json={'mode': 'didox', 'ids': [snap['rows'][0]['id']]})
        assert again.status_code == 502


def test_failed_reload_keeps_previous_rows(monkeypatch):
    db.update_firm(1, {'edocs_enabled': True})
    s = core.get_session('edocs-keep', 1)
    s.didox = [{'id': 'previous-row'}]
    monkeypatch.setattr(services_edocs_api, 'fetch_invoices', AsyncMock(side_effect=services_edocs_api.EdocsError('Unavailable')))
    from services_didox import DidoxError
    with pytest.raises(DidoxError):
        asyncio.run(core.load_didox(s))
    assert s.didox == [{'id': 'previous-row'}]


def test_agent_never_offers_edocs_post_card():
    db.update_firm(1, {'edocs_enabled': True})
    ctx = services_ai.ToolContext(core.get_session('read-only-agent', 1), 'didox')
    result = asyncio.run(ctx.run('post_confirmed_to_1c', {'mode': 'didox'}))
    assert result['holat'] == 'yozish_yopiq' and not ctx.cards


def test_source_history_survives_period_switch_and_restart(monkeypatch):
    db.update_firm(1, {'edocs_enabled': True})
    s = core.get_session('synthetic-ehf-history', 1)
    january = services_edocs_api.map_invoice(invoice(), SETTINGS['inn'])
    february = dict(january, id='synthetic-february', date='2026-02-10')
    fetch = AsyncMock(side_effect=[[january], [february], []])
    monkeypatch.setattr(services_edocs_api, 'fetch_invoices', fetch)
    asyncio.run(core.load_didox(s, '2026-01-01', '2026-01-31'))
    assert not core.snapshot(s, 'didox')['outdated']
    asyncio.run(core.load_didox(s, '2026-02-01', '2026-02-28'))
    restored = core._load_session(s.sid, s.firm_id)
    assert len(restored.ehf_history) == 2
    asyncio.run(core.load_didox(restored, '2026-01-01', '2026-01-31'))
    assert fetch.call_args.kwargs['known_ids'] == [{'id': january['id'], 'flow': 'in'}]
    # 404 yoki bo'sh javob ham oldingi IDni keyingi tekshiruvdan yo'qotmaydi.
    assert len(restored.ehf_history) == 2
