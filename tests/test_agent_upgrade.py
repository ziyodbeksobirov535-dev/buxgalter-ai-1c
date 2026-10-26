import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import core
import services_ai as ai
import services_insight as insight
from services_1c import ODataError, render_filter


def test_failed_balances_are_not_zero():
    onec = SimpleNamespace(
        find_counterparty=AsyncMock(return_value={'Ref_Key': 'fake', 'Description': 'Synthetic'}),
        account_balance=AsyncMock(side_effect=ODataError('unavailable')),
        counterparty_cond=AsyncMock(side_effect=ODataError('unavailable')))
    result = asyncio.run(insight.counterparty_card(onec, '000000000'))
    assert result['qoldiqlar'] != "qarz/avans yo'q"
    assert not result['toliq'] and result['ogohlantirishlar']


def test_live_documents_scope_org():
    transport = SimpleNamespace(query=AsyncMock(return_value=[]))
    onec = SimpleNamespace(mode='live', org_inn='000000000', t=transport,
        entity_props=AsyncMock(return_value={'header': {'Организация_Key'}}),
        organization=AsyncMock(return_value={'Ref_Key': '11111111-1111-1111-1111-111111111111'}))
    asyncio.run(insight.documents(onec, 'bank_kirim'))
    condition = render_filter(transport.query.call_args.args[1])
    assert 'Организация_Key eq guid' in condition


def test_chat_history_survives_reload():
    s = core.Session('upgrade', 1, chat=[{'role': 'user', 'text': 'salom'}])
    core.save_session(s)
    assert core._load_session('upgrade', 1).chat == s.chat


def test_stream_uses_selected_provider(monkeypatch):
    reply = AsyncMock(return_value={'reply': 'provider answer', 'cards': [], 'tools': [], 'refresh': [], 'engine': 'openai'})
    monkeypatch.setattr(ai, 'chat', reply)
    async def collect():
        return [json.loads(e) async for e in ai.chat_stream(core.Session('s', 1), 'salom')]
    events = asyncio.run(collect())
    assert events[-1]['engine'] == 'openai'
    assert events[-1]['reply'] == 'provider answer'
    reply.assert_awaited_once()


def test_compaction_keeps_valid_json_and_totals():
    result = ai.compact_result({'total': 80, 'rows': [{'text': 'x' * 3000}] * 80})
    assert json.loads(json.dumps(result))['total'] == 80
    assert result['rows'][-1]['qisqartirilgan']


def test_openai_executes_tool_and_returns_answer(monkeypatch):
    import config
    responses = [
        {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'call1', 'type': 'function', 'function': {'name': 'firm_overview', 'arguments': '{}'}}]}}]},
        {'choices': [{'message': {'role': 'assistant', 'content': 'Verified answer'}}]}]
    requests = []
    class Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kw):
            requests.append(kw['json'])
            body = responses.pop(0)
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)
    monkeypatch.setattr(ai.httpx, 'AsyncClient', Client)
    ctx = SimpleNamespace(session=core.Session('s', 1), run=AsyncMock(return_value={'balance': 123}))
    settings = {**config.DEFAULTS, 'openai_api_key': 'synthetic'}
    assert asyncio.run(ai._provider_tools(ctx, 'openai', settings, 'balance')) == 'Verified answer'
    ctx.run.assert_awaited_once_with('firm_overview', {})
    assert requests[-1]['messages'][-1]['role'] == 'tool'
