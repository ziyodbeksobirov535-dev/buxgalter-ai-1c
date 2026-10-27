"""EHF filtrlari haqiqiy brauzerda, faqat sun'iy mahalliy ma'lumotlar bilan."""
import asyncio
import socket
import threading
import time

import pytest
import uvicorn

import config,core,db,main
from services_edocs import browser_options
from services_ehf import base_row,result


def test_browser_ehf_filters(tmp_path):
    pw=pytest.importorskip('playwright.async_api')
    db.update_firm(1,{'edocs_enabled':True})
    user=db.create_user('synthetic','synthetic-password','owner')
    session=core.get_session(core.session_id(user['id'],1),1)
    data=[('in','sended','EHF-IN','2026-01-10','missing','new'),('out','signed','EHF-OUT','2026-01-20','matched','duplicate'),('out','deleted','EHF-DEL','2026-01-25','deleted_in_1c','discrepancy')]
    for flow,st,num,dt,code,status in data:
        row=base_row({'id':num,'number':num,'date':dt,'total':112,'flow':flow,'ehf_status':st,'ehf_status_label':st,'counterparty_name':'Synthetic '+flow,'counterparty_inn':'000000001' if flow=='in' else '000000002','items':[]})
        result(row,code,status,code)
        session.didox.append(row)
    session.didox_source='E-DOCS API';session.ehf_summary={'counts':{'missing':1,'matched':1,'deleted_in_1c':1},'warnings':[]}
    sock=socket.socket();sock.bind(('127.0.0.1',0));origin=f'http://127.0.0.1:{sock.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(main.app,log_level='error',lifespan='off'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    for _ in range(50):
        if server.started:break
        time.sleep(.05)
    async def check():
        async with pw.async_playwright() as p:
            browser=await p.chromium.launch(**browser_options())
            try:
                ctx=await browser.new_context(viewport={'width':1440,'height':1100})
                async def assets(route):
                    u=route.request.url
                    n='tailwindcss.js' if 'tailwindcss' in u else 'alpine.min.js' if 'alpinejs' in u else 'lucide.min.js' if 'lucide' in u else None
                    if n:await route.fulfill(content_type='application/javascript',body=(config.BASE_DIR/'static/vendor'/n).read_bytes())
                    else:await route.abort()
                await ctx.route('https://**/*',assets)
                assert (await ctx.request.post(origin+'/api/auth/login',data={'username':'synthetic','password':'synthetic-password'})).ok
                page=await ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto(origin+'/#/didox',wait_until='networkidle')
                async def count(n):await pw.expect(page.locator('tbody tr:visible')).to_have_count(n)
                await count(3)
                await page.get_by_title('EHF holati',exact=True).select_option('sended');await count(1)
                assert await page.locator('tbody tr:visible').filter(has_text='EHF-IN').count()==1
                await page.get_by_role('button',name='✕ Tozalash',exact=True).click();await count(3)
                await page.get_by_title("Holat bo'yicha",exact=True).select_option('deleted_in_1c');await count(1)
                assert await page.locator('tbody tr:visible').filter(has_text='EHF-DEL').count()==1
                await page.get_by_role('button',name='✕ Tozalash',exact=True).click()
                await page.get_by_title('Jadval sanasi — dan',exact=True).fill('2026-01-15');await count(2)
                await page.get_by_role('button',name='✕ Tozalash',exact=True).click()
                await page.get_by_placeholder('Qidirish: kontragent, STIR, №, maqsad',exact=True).fill('000000001');await count(1)
                await page.get_by_role('button',name='✕ Tozalash',exact=True).click()
                await page.screenshot(path='/tmp/buxgalter-ehf-control.png',full_page=True)
                await page.set_viewport_size({'width':390,'height':844})
                await page.wait_for_timeout(100)
                assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert not errors,errors
            finally:await browser.close()
    try:asyncio.run(check())
    finally:server.should_exit=True;thread.join(timeout=5);sock.close()
