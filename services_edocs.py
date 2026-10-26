"""E-DOCS brauzer adapteri. Sayt profili jonli DOM tekshirilgach sozlanadi.

Login va reklama yopishdan tashqari faqat navigatsiya va JSON eksport yuklash.
Parol, cookie, trace va screenshot diskka yozilmaydi.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import os
import shutil
from datetime import date
from urllib.parse import urlsplit

import config

os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(config.DATA_DIR / 'playwright-browsers'))
ORIGIN = 'https://doc.edocs.uz'
PROFILE_FILE = config.DATA_DIR / 'edocs-profile.json'
_LOCK = asyncio.Lock()


def browser_options():
    # Muhitda tayyor Chromium bo'lsa, qo'shimcha yuklash talab qilinmaydi.
    executable = shutil.which('chromium')
    return {"executable_path": executable} if executable else {}


class EdocsError(Exception):
    pass


def load_profile():
    try:
        p = json.loads(PROFILE_FILE.read_text('utf-8'))
    except (OSError, ValueError):
        raise EdocsError('E-DOCS sayt profili hali sozlanmagan. Jonli sahifa bilan moslash kerak.') from None
    required = ('login_url', 'incoming_url', 'username', 'password', 'submit', 'authenticated',
                'firm_inn', 'export_json')
    if not isinstance(p, dict) or any(not isinstance(p.get(k), str) or not p[k].strip() for k in required):
        raise EdocsError('E-DOCS profilida majburiy manzil yoki selektor yetishmaydi.')
    for key in ('login_url', 'incoming_url'):
        u = urlsplit(p[key])
        if f'{u.scheme}://{u.netloc}' != ORIGIN or u.username or u.password:
            raise EdocsError('E-DOCS manzili faqat https://doc.edocs.uz domenida bo‘lsin.')
    if '/out/' in p['incoming_url']:
        raise EdocsError('Kiruvchi hujjatlar manzili kerak, chiquvchi emas.')
    return p


async def close_ad(page, selector):
    """Ixtiyoriy reklama tugmasini paydo bo'lguncha kutish."""
    if not selector:
        return
    from playwright.async_api import TimeoutError as BrowserTimeout
    button = page.locator(selector)
    try:
        await button.wait_for(state='visible', timeout=8000)
    except BrowserTimeout:
        return
    await button.click(timeout=10000)
    await button.wait_for(state='hidden', timeout=10000)


def parse_export(payload, expected_inn):
    """Moslashtirilgan JSON eksport shartnomasi; noma'lum formatni taxmin qilmaydi."""
    try:
        data = json.loads(payload)
        if not expected_inn or str(data['buyer_inn']) != expected_inn:
            raise ValueError('buyer')
        docs = data['invoices']
        if not isinstance(docs, list):
            raise ValueError('invoices')
        seen = set()
        for doc in docs:
            for key in ('id', 'number', 'date', 'seller_inn', 'seller_name', 'total', 'items'):
                if key not in doc:
                    raise ValueError(key)
            date.fromisoformat(doc['date'])
            doc['total'] = float(doc['total'])
            if not math.isfinite(doc['total']):
                raise ValueError('total')
            if not doc['id'] or doc['id'] in seen or not isinstance(doc['items'], list):
                raise ValueError('id/items')
            seen.add(doc['id'])
            for item in doc['items']:
                for key in ('name', 'qty', 'price', 'total'):
                    if key not in item:
                        raise ValueError(key)
                for key in ('qty', 'price', 'total'):
                    item[key] = float(item[key])
                    if not math.isfinite(item[key]):
                        raise ValueError(key)
            doc.setdefault('kind', 'Товары')
        return docs
    except (ValueError, TypeError, KeyError, AttributeError):
        raise EdocsError('E-DOCS eksport formati yoki xaridor STIRi mos emas. Import bajarilmadi.') from None


async def browse(page, settings, profile, check_only=False):
    """Testlarda ham ishlatiladigan bitta login urinishli ish oqimi."""
    await page.goto(profile['login_url'], wait_until='domcontentloaded')
    await page.locator(profile['username']).fill(settings['edocs_login'])
    await page.locator(profile['password']).fill(settings['edocs_password'])
    await page.locator(profile['submit']).click()
    await close_ad(page, profile.get('ad_close'))
    await page.locator(profile['authenticated']).wait_for(state='visible', timeout=20000)
    await page.goto(profile['incoming_url'], wait_until='domcontentloaded')
    await close_ad(page, profile.get('ad_close'))
    inn = re.sub(r'\D', '', await page.locator(profile['firm_inn']).inner_text())
    if inn != settings.get('inn'):
        raise EdocsError('E-DOCS dagi tanlangan firma STIRi mos emas. Davom etilmadi.')
    if check_only:
        return []
    async with page.expect_download(timeout=30000) as pending:
        await page.locator(profile['export_json']).click()
    download = await pending.value
    path = await download.path()
    if not path or await download.failure():
        raise EdocsError('E-DOCS eksportini yuklab bo‘lmadi.')
    # Brauzer konteksti yopilganda vaqtinchalik eksport ham o'chadi.
    from pathlib import Path
    if Path(path).stat().st_size > 20 * 1024 * 1024:
        raise EdocsError('E-DOCS eksporti 20 MB chegarasidan katta.')
    return parse_export(Path(path).read_bytes(), settings['inn'])


async def fetch_invoices(settings, check_only=False):
    if not settings.get('edocs_login') or not settings.get('edocs_password'):
        raise EdocsError('E-DOCS login va parolini firma sozlamalariga kiriting.')
    profile = load_profile()
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise EdocsError('E-DOCS uchun requirements-edocs.txt va Playwright Chromium o‘rnatilsin.') from None
    async with _LOCK:
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(**browser_options())
                try:
                    context = await browser.new_context(accept_downloads=True)
                    page = await context.new_page()
                    page.set_default_timeout(15000)
                    result = await browse(page, settings, profile, check_only)
                    await context.close()
                    return result
                finally:
                    await browser.close()
        except EdocsError:
            raise
        except Exception:
            # Brauzer xatolarida DOM yoki login tafsilotlari bo'lishi mumkin.
            raise EdocsError('E-DOCS bosqichi bajarilmadi. Login, reklama yoki sayt profilini tekshiring; avtomatik qayta urinish yo‘q.') from None
