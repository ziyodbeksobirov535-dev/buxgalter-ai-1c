"""Sozlamalar ichidagi qoidalar: brauzer navigatsiyasi va rollar."""
import asyncio
import socket
import threading
import time

import pytest
import uvicorn

import config
import db
import main
from services_edocs import browser_options


@pytest.mark.parametrize('role', ['owner', 'accountant', 'viewer'])
def test_rules_live_inside_settings(role, tmp_path):
    pw = pytest.importorskip('playwright.async_api')
    db.create_user('synthetic', 'synthetic-password', role, firm_ids=[1])
    second = db.create_firm({'name': 'Synthetic second firm'}) if role == 'owner' else None
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(main.app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(.05)

    async def check():
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch(**browser_options())
            try:
                context = await browser.new_context(viewport={'width': 1440, 'height': 1100})
                async def assets(route):
                    url = route.request.url
                    name = 'tailwindcss.js' if 'tailwindcss' in url else 'alpine.min.js' if 'alpinejs' in url else 'lucide.min.js' if 'lucide' in url else None
                    if name:
                        await route.fulfill(content_type='application/javascript', body=(config.BASE_DIR / 'static/vendor' / name).read_bytes())
                    else:
                        await route.abort()
                await context.route('https://**/*', assets)
                await context.add_init_script("localStorage.setItem('bx_firm', '1')")
                assert (await context.request.post(origin + '/api/auth/login', data={'username': 'synthetic', 'password': 'synthetic-password'})).ok
                page = await context.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                await page.goto(origin + '/#/rules', wait_until='networkidle')
                await page.wait_for_url('**/#/settings/rules')
                nav = page.get_by_role('navigation', name='Sozlamalar bo‘limlari')
                await nav.get_by_role('button', name='Hisob qoidalari').wait_for()
                assert await page.locator('a[href="#/rules"]').count() == 0
                assert await page.get_by_text('📚 Jami qoidalar', exact=True).is_visible()
                if role != 'viewer':
                    await page.locator('select[x-model="newRule.kind"]').select_option('keyword')
                    await page.locator('input[x-model="newRule.match"]').fill('synthetic-service')
                    await page.locator('input[x-model="newRule.account"]').fill('94.20')
                    await page.get_by_role('button', name="Qo'shish", exact=True).click()
                    await page.get_by_text('🔤 «synthetic-service»', exact=True).wait_for()
                else:
                    assert not await page.get_by_text('➕ Yangi qoida', exact=True).is_visible()
                await page.reload(wait_until='networkidle')
                assert page.url.endswith('#/settings/rules')
                await page.get_by_text('📚 Jami qoidalar', exact=True).wait_for()
                if role == 'owner':
                    await nav.get_by_role('button', name='Ulanishlar', exact=False).click()
                    await page.locator('input[x-model="firmForm.edocs_login"]').wait_for(state='hidden')
                    name = page.locator('input[x-model="firmForm.name"]')
                    await nav.get_by_role('button', name='Joriy firma').click()
                    await name.fill('Unsaved synthetic name')
                    await nav.get_by_role('button', name='Ulanishlar').click()
                    await nav.get_by_role('button', name='Joriy firma').click()
                    await pw.expect(name).to_have_value('Unsaved synthetic name')
                    await page.get_by_title('Firma', exact=True).select_option(str(second['id']))
                    await page.wait_for_function('document.querySelector("[x-data]")._x_dataStack[0].firmForm.id === ' + str(second['id']))
                    await pw.expect(name).to_have_value('Synthetic second firm')
                    await nav.get_by_role('button', name='Hisob qoidalari').click()
                    await page.wait_for_timeout(250)
                    assert await nav.locator('[aria-current="page"]').count() == 1
                    await page.screenshot(path='/tmp/buxgalter-settings-rules.png', full_page=True)
                    await nav.get_by_role('button', name='Tizim holati').click()
                    await page.wait_for_timeout(250)
                    await page.screenshot(path='/tmp/buxgalter-settings-overview.png', full_page=True)
                else:
                    assert await nav.get_by_role('button', name='Foydalanuvchilar').count() == 0
                    await page.goto(origin + '/#/settings/users', wait_until='networkidle')
                    await page.wait_for_url('**/#/settings/overview')
                await nav.get_by_role('button', name='Hisob qoidalari').click()
                await page.set_viewport_size({'width': 390, 'height': 844})
                await page.wait_for_timeout(100)
                assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert not errors, errors
            finally:
                await browser.close()
    try:
        asyncio.run(check())
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
