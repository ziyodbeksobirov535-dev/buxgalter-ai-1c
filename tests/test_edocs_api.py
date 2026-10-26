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
