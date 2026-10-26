import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import config
import db
import services_edocs as edocs


def sample():
    return {'buyer_inn': '000000000', 'invoices': [
        {'id': 'synthetic-1', 'number': 'TEST-1', 'date': '2026-01-01',
         'seller_inn': '000000001', 'seller_name': 'Synthetic supplier', 'total': 112,
         'items': [{'name': 'Synthetic item', 'qty': 1, 'price': 100, 'total': 112, 'vat_rate': 12}]}]}


def test_edocs_password_encrypted_and_masked():
    firm = db.create_firm({'name': 'Synthetic', 'edocs_login': 'test-user', 'edocs_password': 'test-password'})
    assert firm['edocs_password'] == config.MASK
    assert db.firm_settings(firm['id'])['edocs_password'] == 'test-password'
    with db.tx() as c:
        assert c.execute('SELECT edocs_password FROM firms WHERE id=?', (firm['id'],)).fetchone()[0] != 'test-password'
    db.update_firm(firm['id'], {'edocs_login': 'another-user', 'edocs_password': config.MASK})
    assert db.firm_settings(firm['id'])['edocs_password'] == ''


def test_export_rejects_wrong_firm_and_unknown_schema():
    assert len(edocs.parse_export(json.dumps(sample()), '000000000')) == 1
    for payload in (json.dumps(sample()), '[]', '{}', 'invalid'):
        with pytest.raises(edocs.EdocsError):
            edocs.parse_export(payload, '000000099')


def test_missing_credentials_stop_before_browser():
    with pytest.raises(edocs.EdocsError, match='login'):
        asyncio.run(edocs.fetch_invoices({}))


def test_edocs_selected_never_falls_back_to_demo(monkeypatch):
    import services_didox
    monkeypatch.setattr(edocs, 'fetch_invoices', AsyncMock(side_effect=edocs.EdocsError('not configured')))
    with pytest.raises(services_didox.DidoxError, match='not configured'):
        asyncio.run(services_didox.fetch_invoices({'edocs_enabled': True, 'didox_demo_mode': True}))


def test_browser_login_ad_and_export():
    pw = pytest.importorskip('playwright.async_api')
    profile = {'login_url': edocs.ORIGIN + '/login', 'incoming_url': edocs.ORIGIN + '/incoming',
               'username': '#login', 'password': '#password', 'submit': '#submit',
               'authenticated': '#authenticated', 'firm_inn': '#inn', 'export_json': '#export', 'ad_close': '#ad'}
    login_html = '''<input id="login"><input id="password" type="password">
    <button id="submit" onclick="location.href='/authenticated'">Login</button>'''
    authenticated_html = '''<div id="authenticated">Ready</div><button id="ad" style="display:none" onclick="this.remove()">×</button>
    <script>setTimeout(()=>document.querySelector('#ad').style.display='block',50)</script>'''
    export_html = '<div id="inn">000000000</div><button id="ad" onclick="this.remove()">×</button><a id="export" href="/export.json" download>Export</a>'
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            key = self.path.strip('/')
            bodies = {'login': login_html, 'authenticated': authenticated_html, 'incoming': export_html,
                      'export.json': json.dumps(sample())}
            body = bodies.get(key, '').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json' if key == 'export.json' else 'text/html; charset=utf-8')
            if key == 'export.json':
                self.send_header('Content-Disposition', 'attachment; filename=sample.json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = 'http://127.0.0.1:' + str(server.server_port)
    profile.update(login_url=origin + '/login', incoming_url=origin + '/incoming')
    async def run():
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch(**edocs.browser_options())
            try:
                context = await browser.new_context(accept_downloads=True)
                page = await context.new_page()
                result = await edocs.browse(page, {'edocs_login': 'synthetic', 'edocs_password': 'synthetic',
                                                  'inn': '000000000'}, profile)
                assert result[0]['id'] == 'synthetic-1'
                assert await page.locator('#ad').count() == 0
                await context.close()
            finally:
                await browser.close()
    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()


def test_profile_rejects_external_and_outgoing_urls(tmp_path, monkeypatch):
    path = tmp_path / 'profile.json'
    monkeypatch.setattr(edocs, 'PROFILE_FILE', path)
    profile = {k: '#synthetic' for k in ('username', 'password', 'submit', 'authenticated', 'firm_inn', 'export_json')}
    for incoming in ('https://example.org/incoming', edocs.ORIGIN + '/list/all/out/sended'):
        path.write_text(json.dumps({**profile, 'login_url': edocs.ORIGIN + '/login', 'incoming_url': incoming}))
        with pytest.raises(edocs.EdocsError):
            edocs.load_profile()


def test_absent_ad_does_not_block():
    pytest.importorskip('playwright.async_api')
    from playwright.async_api import TimeoutError
    button = AsyncMock()
    button.wait_for.side_effect = TimeoutError('not visible')
    from types import SimpleNamespace
    page = SimpleNamespace(locator=lambda selector: button)
    asyncio.run(edocs.close_ad(page, '#ad'))
    button.click.assert_not_awaited()


def test_connection_check_owner_only(monkeypatch):
    import main
    from fastapi.testclient import TestClient
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(edocs, 'fetch_invoices', fetch)
    firm = db.create_firm({'name': 'Synthetic'})
    url = f"/api/firms/{firm['id']}/edocs/check"
    with TestClient(main.app, base_url='http://127.0.0.1:8000') as client:
        assert client.post(url).status_code == 401
        db.create_user('viewer-test', 'synthetic-password', 'viewer')
        client.post('/api/auth/login', json={'username': 'viewer-test', 'password': 'synthetic-password'})
        assert client.post(url).status_code == 403
        fetch.assert_not_awaited()
        db.create_user('owner-test', 'synthetic-password', 'owner')
        client.post('/api/auth/login', json={'username': 'owner-test', 'password': 'synthetic-password'})
        assert client.post(url).json()['ok'] is True
        fetch.assert_awaited_once()
