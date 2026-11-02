"""Yangi biznes paneli: haqiqiy Chromium, sun'iy 1C va Gemini bilan."""
import asyncio
from copy import deepcopy
import json
import socket
import threading
import time

import pytest
import uvicorn

import config,core,db,main
import services_business as business
import services_business_ai as ai
from services_edocs import browser_options
from tests.test_business import OneC,ORG,TODAY


def test_business_browser_switch_filters_chat_pdf_mobile_and_auto_refresh(monkeypatch,tmp_path):
    pw=pytest.importorskip('playwright.async_api')
    business.CACHE.clear();business.LOCKS.clear();business.SUCCESS.clear()
    db.update_firm(1,{'name':'Synthetic primary','inn':'000000000'})
    second=db.create_firm({'name':'Synthetic second','inn':'000000002'})
    private=db.create_firm({'name':'Private third','inn':'000000003'})
    db.create_user('synthetic-owner','synthetic-password','business_owner',firm_ids=[1,second['id']])
    config.save_settings({'gemini_api_key':'synthetic-key'})
    sources={}
    for fid in (1,second['id']):
        source=OneC();source.t.bal[0]['СуммаBalance']=fid*1000
        async def organization(fid=fid):return {'Ref_Key':ORG,'ИНН':db.get_firm(fid)['inn']}
        source.organization=organization
        async def query(entity,conds,top,skip,orderby,source=source):
            if entity==business.E_REGISTER_ROWS:return deepcopy(source.t.ledger)[skip:skip+top]
            return [{'Ref_Key':'synthetic-document','Организация_Key':ORG,'Date':TODAY.isoformat(),
                     'Number':'SYN-1','СуммаДокумента':112,'Posted':True,'DeletionMark':False,
                     'Контрагент_Key':'cp1'}][skip:skip+top]
        source.t.query=query
        original=source.t.get
        async def get(entity,key,original=original):
            if key=='synthetic-document':
                return {'Ref_Key':key,'Организация_Key':ORG,'Date':TODAY.isoformat(),'Number':'SYN-1',
                        'СуммаДокумента':112,'Контрагент_Key':'cp1','Товары':[{'Номенклатура_Key':'product','Количество':2,'Сумма':100,'СуммаНДС':12}]}
            return await original(entity,key)
        source.t.get=get;sources[fid]=source
    monkeypatch.setattr(core,'get_onec',lambda fid:sources[fid])
    async def generate(s,m,snapshot,history,settings,**kwargs):return 'Synthetic AI: '+snapshot['firm']['name']+' · '+str(len(history)//2+1),'Gemini synthetic'
    monkeypatch.setattr(ai,'generate',generate)
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
                ctx=await browser.new_context(viewport={'width':1440,'height':1050})
                assert (await ctx.request.post(origin+'/api/auth/login',data={'username':'synthetic-owner','password':'synthetic-password'})).ok
                page=await ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                await page.clock.install()
                await page.goto(origin+'/',wait_until='networkidle')
                await pw.expect(page.get_by_role('heading',name='Synthetic primary',exact=True)).to_be_visible()
                await pw.expect(page.locator('.metric')).to_have_count(6)
                assert 'Private third' not in await page.locator('body').inner_text()
                assert 'EHF–1C' not in await page.locator('body').inner_text()
                calls=[];page.on('request',lambda r:calls.append(r.url) if '/api/business/dashboard?' in r.url else None)
                await page.clock.fast_forward(300001)
                await pw.expect(page.get_by_role('button',name='↻ Yangilash',exact=True)).to_be_enabled()
                assert calls,'5 daqiqalik yangilanish ishga tushmadi'
                await page.screenshot(path='/tmp/buxgalter-business-overview.png',full_page=True)
                await page.get_by_role('button',name='Qarzlar',exact=False).first.click()
                await pw.expect(page.get_by_role('heading',name='Qarz va avanslar',exact=True)).to_be_visible()
                await page.get_by_label('Qarz tomoni',exact=True).select_option('receivable')
                await page.get_by_label('Qarz qidiruvi',exact=True).fill('Synthetic cp1')
                await pw.expect(page.locator('section:visible tbody tr')).to_have_count(1)
                await page.locator('section:visible tbody tr').click()
                await pw.expect(page.get_by_role('heading',name='Kontragent tafsiloti',exact=True)).to_be_visible()
                await page.get_by_role('button',name='Yopish',exact=True).click()
                await page.get_by_role('button',name='▤ Hujjatlar',exact=True).click()
                await pw.expect(page.locator('section:visible tbody tr')).to_have_count(1)
                await page.get_by_role('button',name='Ko‘rish ↗',exact=True).click()
                await pw.expect(page.get_by_role('heading',name='Hujjat tafsiloti',exact=True)).to_be_visible()
                await pw.expect(page.locator('.modal').get_by_text('Synthetic product',exact=True)).to_be_visible()
                await page.get_by_role('button',name='Yopish',exact=True).click()
                await page.get_by_role('button',name='✦ Gemini',exact=False).click()
                await page.get_by_label('Gemini savoli',exact=True).fill('How much debt?')
                await page.get_by_role('button',name='Yuborish',exact=True).click()
                await pw.expect(page.get_by_text('Synthetic AI: Synthetic primary · 1',exact=True)).to_be_visible()
                async with page.expect_download() as dl:
                    await page.get_by_role('button',name='↓ PDF hisobot',exact=True).click()
                download=await dl.value
                path=await download.path();assert path and open(path,'rb').read(5)==b'%PDF-'
                # Eski firmaning kechikkan javobi yangi firmaga qo'llanmasin.
                held=asyncio.Event();release=asyncio.Event()
                async def intercept(route):
                    if route.request.headers.get('x-firm-id')=='1':
                        held.set();await release.wait()
                        await route.fulfill(json={'firm':{'id':1,'name':'STALE'},'balances':None,'income':None,'warnings':[]})
                    else:await route.continue_()
                await page.route('**/api/business/dashboard?**',intercept)
                await page.get_by_role('button',name='↻ Yangilash',exact=True).click()
                await asyncio.wait_for(held.wait(),5)
                await page.get_by_label('Joriy firma',exact=True).select_option(str(second['id']))
                await pw.expect(page.get_by_role('heading',name='Synthetic second',exact=True)).to_be_visible()
                release.set()
                await page.wait_for_timeout(100)
                await pw.expect(page.get_by_text('Synthetic AI: Synthetic primary · 1',exact=True)).to_have_count(0)
                await page.get_by_role('button',name='◫ Umumiy holat',exact=False).click()
                await page.wait_for_function('document.querySelector("[x-data]")._x_dataStack[0].data?.firm?.id === '+str(second['id']))
                assert await page.evaluate('document.querySelector("[x-data]")._x_dataStack[0].data.firm.name')=='Synthetic second'
                await page.set_viewport_size({'width':390,'height':844})
                await page.wait_for_timeout(100)
                assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                await page.screenshot(path='/tmp/buxgalter-business-mobile.png',full_page=True)
                assert not errors,errors
            finally:await browser.close()
    try:asyncio.run(check())
    finally:server.should_exit=True;thread.join(timeout=5);sock.close()
