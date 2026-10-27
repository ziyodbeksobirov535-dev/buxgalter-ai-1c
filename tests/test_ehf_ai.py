"""Tanlangan EHF izohi: tashqi Google/1C chaqiruvlarisiz sinovlar."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import config
import core
import db
import main
import services_ehf_ai as ai
from services_ehf import base_row, result, candidate
from tests.test_ehf_reconciliation import invoice, document


def row_with_detail():
    row = base_row(invoice())
    doc = candidate(document(), {'synthetic-cp': {'ИНН': '000000001'}},
                    {'synthetic-product': {'Description': 'Synthetic product'}})
    result(row, 'differences', 'discrepancy', 'Synthetic difference', [doc], ['Synthetic quantity difference'])
    return row


def session():
    s = core.Session('synthetic-ehf-ai', 1)
    s.didox = [row_with_detail()]
    s.didox_source = 'E-DOCS API'
    return s


def test_selected_context_uses_allowlist_and_limits_with_explicit_counts():
    s = session(); row = s.didox[0]
    row.update(password='synthetic-secret', unrelated='not for AI')
    row['items'] *= 150
    row['matches'][0]['items'] *= 150
    row['matches'][0]['token'] = 'synthetic-token'
    context = ai.context_for(s, row)
    text = json.dumps(context)
    assert 'synthetic-secret' not in text and 'synthetic-token' not in text and 'unrelated' not in text
    assert context['ehf']['tovarlar']['jami'] == 150
    assert context['ehf']['tovarlar']['berilgan'] == 100
    assert context['onec_hujjatlar'][0]['tovarlar']['qatorlar'][0]['qty'] == 2
    assert context['onec_hujjatlar'][0]['tovarlar']['berilgan'] == 100


def test_onec_only_context_does_not_invent_source_ehf_values():
    s = session(); s.didox[0].update(ehf_status='onec_only', source_id=None)
    context = ai.context_for(s, s.didox[0])
    assert context['ehf'] is None and context['onec_hujjatlar'][0]['amount'] == 112


def test_gemini_gets_selected_details_and_followup_but_no_global_history_or_tools(monkeypatch):
    s = session(); other = deepcopy(s.didox[0]); other['id'] = 'other-row'; other['number'] = 'OTHER'
    s.didox.append(other); s.chat = [{'role': 'user', 'text': 'PRIVATE GLOBAL CHAT'}]
    config.save_settings({'gemini_api_key': 'synthetic-key', 'ai_provider': 'openai'})
    calls = []
    async def generate(context, history, message, settings):
        calls.append((context, deepcopy(history), message))
        return 'Synthetic explanation', 'synthetic-model', ''
    monkeypatch.setattr(ai, '_generate', generate)
    before = deepcopy(s.didox)
    async def check():
        revision = ai.state(s, s.didox[0])['revision']
        first = await ai.ask(s, before[0]['id'], 'What differs?', revision)
        assert first['engine'] == 'gemini'
        await ai.ask(s, before[0]['id'], 'What about VAT?', revision)
        assert calls[1][1][0]['text'] == 'What differs?'
        assert ai.state(s, other)['messages'] == []
    asyncio.run(check())
    assert s.didox == before and s.chat == [{'role': 'user', 'text': 'PRIVATE GLOBAL CHAT'}]
    assert 'OTHER' not in json.dumps(calls) and 'PRIVATE GLOBAL CHAT' not in json.dumps(calls)
    assert calls[0][0]['ehf']['tovarlar']['qatorlar'][0]['qty'] == 2
    assert calls[0][0]['onec_hujjatlar'][0]['tovarlar']['qatorlar'][0]['vat_sum'] == 12
    core.save_session(s)
    restored = core._load_session(s.sid, 1)
    assert len(ai.state(restored, restored.didox[0])['messages']) == 4
    restored.didox[0]['amount'] += 1
    assert ai.state(restored, restored.didox[0])['messages'] == []


def test_missing_key_and_provider_error_are_honest_and_do_not_expose_errors(monkeypatch, caplog):
    s = session(); row = s.didox[0]
    response = asyncio.run(ai.ask(s, row['id'], 'Explain', ai.state(s, row)['revision']))
    assert response['engine'] == 'local' and 'bu Gemini javobi emas' in response['reply']
    assert 'API kalitini' in response['ai']['notice']
    config.save_settings({'gemini_api_key': 'synthetic-key'})
    monkeypatch.setattr(ai, '_generate', AsyncMock(side_effect=RuntimeError('PRIVATE_PROVIDER_SECRET')))
    response = asyncio.run(ai.ask(s, row['id'], 'Explain again', ai.state(s, row)['revision']))
    assert response['engine'] == 'local' and 'javob bermadi' in response['ai']['notice']
    assert 'PRIVATE_PROVIDER_SECRET' not in json.dumps(response) + caplog.text


def test_changed_context_rejected_before_and_during_generation(monkeypatch):
    s = session(); row = s.didox[0]; revision = ai.state(s, row)['revision']
    config.save_settings({'gemini_api_key': 'synthetic-key'})
    gen = AsyncMock(return_value=('Explanation', 'synthetic', ''))
    monkeypatch.setattr(ai, '_generate', gen)
    row['amount'] += 1
    with pytest.raises(ai.ContextChanged):
        asyncio.run(ai.ask(s, row['id'], 'Explain', revision))
    gen.assert_not_awaited()
    async def changed(*args):
        s.analyzed_with['didox_loaded_at'] = '2026-01-11T00:00:00Z'
        return 'Stale answer', 'synthetic', ''
    monkeypatch.setattr(ai, '_generate', changed)
    with pytest.raises(ai.ContextChanged):
        asyncio.run(ai.ask(s, row['id'], 'Explain', ai.state(s, row)['revision']))
    assert not s.ehf_chats


def test_sdk_uses_no_tools_and_retired_model_fallback(monkeypatch):
    import google.genai
    from google.genai import errors
    calls = []
    class FakeAio:
        def __init__(self): self.models = self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def generate_content(self, *, model, contents, config):
            calls.append((model, contents, config))
            assert not config.tools and not config.cached_content
            if model == 'retired-synthetic':
                raise errors.ClientError(404, {'error': {'code': 404, 'message': 'not found'}})
            return SimpleNamespace(text='Synthetic answer')
    class FakeClient:
        def __init__(self, **kwargs): self.aio = FakeAio()
    monkeypatch.setattr(google.genai, 'Client', FakeClient)
    s = session()
    result = asyncio.run(ai._generate(ai.context_for(s, s.didox[0]), [], 'Explain VAT',
                                     {'gemini_api_key': 'synthetic-key', 'gemini_model': 'retired-synthetic'}))
    assert result[0] == 'Synthetic answer' and result[1] == 'gemini-flash-latest' and result[2]
    assert len(calls) == 2 and calls[-1][1][-1].parts[0].text == 'Explain VAT'
    assert 'Synthetic product' in calls[0][1][0].parts[0].text


def test_endpoint_auth_firm_scope_viewer_access_and_stale_revision(monkeypatch):
    user = db.create_user('synthetic-viewer', 'synthetic-password', 'viewer', firm_ids=[1])
    forbidden = db.create_firm({'name': 'Synthetic other firm', 'inn': '000000002'})
    s = core.get_session(core.session_id(user['id'], 1), 1)
    s.didox = session().didox; s.didox_source = 'E-DOCS API'
    row = s.didox[0]; endpoint = '/api/ehf/' + row['id'] + '/chat'
    payload = {'message': 'Explain', 'revision': ai.state(s, row)['revision']}
    with TestClient(main.app, base_url='http://127.0.0.1:8000') as client:
        assert client.post(endpoint, json=payload).status_code == 401
        client.post('/api/auth/login', json={'username': 'synthetic-viewer', 'password': 'synthetic-password'})
        assert client.post(endpoint, json=payload, headers={'X-Firm-Id': str(forbidden['id'])}).status_code == 403
        assert client.post('/api/ehf/unknown/chat', json=payload).status_code == 404
        assert client.post(endpoint, json={**payload, 'message': '   '}).status_code == 400
        assert client.post(endpoint, json={**payload, 'message': 'x' * 4001}).status_code == 422
        assert client.post(endpoint, json=payload).status_code == 200
        preview = client.get('/api/rows/' + row['id'] + '/inspect?mode=didox').json()
        assert len(preview['ai']['messages']) == 2 and preview['payload'] is None
        row['amount'] += 1
        assert client.post(endpoint, json=payload).status_code == 409
