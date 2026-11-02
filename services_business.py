"""Firma egasi uchun tashkilotga chegaralangan, ketma-ket 1C o'qishi."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import math
import re
import time as clock
import uuid
from urllib.parse import quote
from zoneinfo import ZoneInfo

import core
import db
from services_1c import (Cond, DT, Guid, ODataError, OfflineOData, E_ACCOUNTS, E_COUNTERPARTY, E_NOMENCLATURE,
                         E_REGISTER, E_REGISTER_ROWS, OneCService, encode_query, render_filter)
from services_insight import DOC_TYPES

TZ = ZoneInfo('Asia/Tashkent')
LOCKS = {}
CACHE = {}
SUCCESS = {}
REFS = {}
REF_TTL = 1800
# Kam o'zgaradigan kataloglar: har 5 daqiqalik yangilanishda yuzlab GET qayta yuborilmasin.
STATIC = {E_ACCOUNTS, E_COUNTERPARTY, E_NOMENCLATURE, 'Catalog_КлассификаторЕдиницИзмерения'}
PAGE = 500
AGE_WINDOW = 180
LIMIT = 20000


def period(start='', end=''):
    today = datetime.now(TZ).date()
    try:
        a, b = date.fromisoformat(start) if start else today.replace(day=1), date.fromisoformat(end) if end else today
    except (ValueError, TypeError):
        raise ValueError('Sana YYYY-MM-DD formatida bo‘lsin') from None
    # Davr oxiri kelajakda bo'lsa (masalan butun yil tanlansa) — bugungi kungacha olinadi.
    b = min(b, today)
    if a > b or a.year < 2000:
        raise ValueError('Davr boshi oxiridan keyin yoki kelajakda bo‘lmasin')
    return a, b


def amount(value):
    if value is None or isinstance(value, bool): raise ValueError('1C summa/miqdor rekviziti olinmagan')
    try:n = float(value)
    except (TypeError,ValueError,OverflowError):raise ValueError('1C sonli rekviziti noto‘g‘ri') from None
    if not math.isfinite(n): raise ValueError('1C sonli rekviziti noto‘g‘ri')
    return n


def explain(error):
    # Tarmoq xatosida ulanish URLi/loginini egasi paneliga chiqarish shart emas.
    return '1C ma’lumoti olinmadi. Buxgalter ulanish va OData ruxsatlarini tekshirsin.' if isinstance(error,ODataError) else str(error)


def onec_for(firm_id):
    service=core.get_onec(firm_id)
    if service.mode=='demo' and not db.firm_settings(firm_id)['onec_demo_mode']:
        return OneCService(OfflineOData('Jonli 1C ulanishi sozlanmagan'))
    return service


def shared_refs(firm_id):
    key = (firm_id, core.onec_fingerprint(firm_id))
    hit = REFS.get(key)
    if not hit or clock.monotonic() - hit[0] > REF_TTL:
        if len(REFS) > 20: REFS.clear()
        hit = REFS[key] = (clock.monotonic(), {})
    return hit[1]


class Reader:
    def __init__(self, onec, expected, shared=None):
        self.onec, self.expected, self.org, self.meta = onec, str(expected), None, None
        self.refs, self.shared = {}, {} if shared is None else shared

    async def ready(self):
        if self.onec.mode == 'demo': return
        if self.onec.mode != 'live': raise ValueError('Jonli 1C ulanishi sozlanmagan')
        self.meta = await self.onec._metadata()
        self.org = await self.onec.organization()
        if not self.org or not self.expected or str(self.org.get('ИНН') or self.org.get('ПИНФЛ') or '') != self.expected:
            raise ValueError('1C tashkiloti STIRi tasdiqlanmadi')
        try: uuid.UUID(self.org.get('Ref_Key') or '')
        except (ValueError,TypeError): raise ValueError('1C tashkiloti GUIDi tasdiqlanmadi') from None

    def check_org(self, rows):
        if self.onec.mode == 'live' and any(r.get('Организация_Key') != self.org['Ref_Key'] for r in rows):
            raise ValueError('1C javobidagi tashkilot chegarasi tasdiqlanmadi')

    async def ref(self, entity, key):
        if not key: return {}
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',str(key)): raise ValueError('1C identifikatori noto‘g‘ri')
        pair = (entity, key)
        if pair in self.shared: return self.shared[pair]
        if pair not in self.refs:
            self.refs[pair] = await self.onec.t.get(entity, key) or {}
            if entity in STATIC and self.refs[pair]: self.shared[pair] = self.refs[pair]
        return self.refs[pair]

    async def code(self, key):
        rec = await self.ref(E_ACCOUNTS, key)
        value = re.sub(r'\D', '', str(rec.get('Code') or ''))
        if not value: raise ValueError('1C schyot kodi aniqlanmadi')
        return value

    async def rows(self, entity, conds, order='Ref_Key'):
        if self.onec.mode == 'live':
            if entity not in self.meta['sets']: raise ValueError('1C obyekti nashr qilinmagan: ' + entity)
            props = await self.onec.entity_props(entity)
            if not props or 'Организация_Key' not in props['header']:
                raise ValueError('Obyektning tashkilot rekviziti tasdiqlanmadi: ' + entity)
            conds = [*conds, Cond('Организация_Key', 'eq', Guid(self.org['Ref_Key']))]
        result, seen = [], set()
        for skip in range(0, LIMIT, PAGE):
            chunk = await self.onec.t.query(entity, conds, top=PAGE, skip=skip, orderby=order)
            self.check_org(chunk)
            for row in chunk:
                signature = repr(sorted(row.items()))
                if signature in seen: raise ValueError('1C sahifalashida takroriy yozuv; natija to‘liq emas')
                seen.add(signature)
            result.extend(chunk)
            if len(chunk) < PAGE: return result
        raise ValueError('1C o‘qish chegarasi oshdi; davrni kichraytiring')

    async def balances(self, at):
        if self.onec.mode == 'demo':
            result = []
            for code, rows in self.onec.t.db.get('_balances', {}).items():
                result.extend(dict(r, _code=code.replace('.', '')) for r in rows)
            return result
        if E_REGISTER not in self.meta['sets']: raise ValueError('1C qoldiq registri nashr qilinmagan')
        from services_1c import render_filter
        path = quote(f"{E_REGISTER}/Balance(Period=datetime'{at:%Y-%m-%dT%H:%M:%S}')", safe="(),='/")
        result, seen = [], set()
        for skip in range(0, LIMIT, PAGE):
            params = {'$format': 'json', '$filter': render_filter([Cond('Организация_Key', 'eq', Guid(self.org['Ref_Key']))]),
                      '$top': str(PAGE), '$skip': str(skip), '$orderby': 'Account_Key,ExtDimension1'}
            body = await self.onec.t._request('GET', path + '?' + encode_query(params))
            chunk = body.get('value')
            if not isinstance(chunk, list): raise ValueError('1C qoldiq javobi noto‘g‘ri')
            self.check_org(chunk)
            for row in chunk:
                signature = repr(sorted(row.items()))
                if signature in seen: raise ValueError('1C qoldiqlari sahifalashda takrorlandi')
                seen.add(signature)
                row['_code'] = await self.code(row.get('Account_Key'))
            result.extend(chunk)
            if len(chunk) < PAGE: return result
        raise ValueError('1C qoldiqlari to‘liq olinmadi — o‘qish chegarasi')

    async def increases(self, account_key, column, start, end):
        # Субконто bilan provodkalar: qarzni oshirgan tomon (40 — Dr, 60 — Cr) bo'yicha.
        path = quote(f"{E_REGISTER}/RecordsWithExtDimensions(StartPeriod=datetime'{start:%Y-%m-%dT%H:%M:%S}',"
                     f"EndPeriod=datetime'{end:%Y-%m-%dT%H:%M:%S}')", safe="(),='/")
        conds = [Cond('Account' + column + '_Key', 'eq', Guid(account_key)), Cond('Организация_Key', 'eq', Guid(self.org['Ref_Key']))]
        result, seen = [], set()
        for skip in range(0, LIMIT, PAGE):
            params = {'$format': 'json', '$filter': render_filter(conds), '$top': str(PAGE), '$skip': str(skip),
                      '$orderby': 'Period,Recorder,LineNumber'}
            body = await self.onec.t._request('GET', path + '?' + encode_query(params))
            chunk = body.get('value')
            if not isinstance(chunk, list): raise ValueError('1C provodkalar javobi noto‘g‘ri')
            self.check_org(chunk)
            for row in chunk:
                signature = repr(sorted(row.items()))
                if signature in seen: raise ValueError('1C provodkalari sahifalashda takrorlandi')
                seen.add(signature)
                if row.get('Account' + column + '_Key') != account_key: raise ValueError('1C provodka filtri tasdiqlanmadi')
                if row.get('Active') is False: continue
                cp = next((row.get(f'ExtDimension{column}{i}') for i in (1, 2, 3)
                           if str(row.get(f'ExtDimension{column}{i}_Type') or '').split('.')[-1] == E_COUNTERPARTY), None)
                try: d = date.fromisoformat(str(row.get('Period'))[:10])
                except ValueError: raise ValueError('Provodka sanasi aniqlanmadi') from None
                if cp: result.append((cp, d, amount(row.get('Сумма'))))
            if len(chunk) < PAGE: return result
        raise ValueError('1C provodkalari to‘liq olinmadi — o‘qish chegarasi')

    async def ledger(self, start, end):
        if self.onec.mode == 'demo':
            # Faqat demo transportining maxsus sun'iy provodkalari.
            from demo_data import BUSINESS_POSTINGS
            return deepcopy([r for r in BUSINESS_POSTINGS if start.isoformat() <= r['date'] <= end.isoformat()])
        # Dt/Kt juftliklari bo'yicha tayyor aylanma: $select faqat schyotlar — 1C субконто bo'yicha bo'lmaydi,
        # natija bir necha o'nlab qator va bitta so'rovda to'liq keladi (provodkalarni sahifalab o'qish shart emas).
        path = quote(f"{E_REGISTER}/DrCrTurnovers(StartPeriod=datetime'{datetime.combine(start, time.min):%Y-%m-%dT%H:%M:%S}',"
                     f"EndPeriod=datetime'{datetime.combine(end, time(23, 59, 59)):%Y-%m-%dT%H:%M:%S}')", safe="(),='/")
        params = {'$format': 'json', '$filter': render_filter([Cond('Организация_Key', 'eq', Guid(self.org['Ref_Key']))]),
                  '$select': 'AccountDr_Key,AccountCr_Key,СуммаTurnover,Организация_Key'}
        body = await self.onec.t._request('GET', path + '?' + encode_query(params))
        records = body.get('value')
        if not isinstance(records, list): raise ValueError('1C aylanma javobi noto‘g‘ri')
        if len(records) > LIMIT: raise ValueError('1C o‘qish chegarasi oshdi; davrni kichraytiring')
        self.check_org(records)
        result = []
        for r in records:
            result.append({'dt': await self.code(r.get('AccountDr_Key')), 'kt': await self.code(r.get('AccountCr_Key')),
                           'amount': amount(r.get('СуммаTurnover'))})
        return result

    async def document(self, kind, key):
        entity = DOC_TYPES.get(kind)
        if not entity or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', key): raise ValueError('Hujjat turi yoki ID noto‘g‘ri')
        # GETdan oldin ham obyektning tashkilot chegarasi tekshiriladi.
        if self.onec.mode == 'live':
            props = await self.onec.entity_props(entity)
            if not props or 'Организация_Key' not in props['header']: raise ValueError('Tashkilot rekviziti yo‘q')
        rec = await self.ref(entity, key)
        if not rec: raise KeyError(key)
        self.check_org([rec])
        if rec.get('Ref_Key')!=key:raise ValueError('1C hujjat identifikatori mos emas')
        return await self.brief(kind, rec, detailed=True)

    async def brief(self, kind, rec, detailed=False):
        cp = await self.ref(E_COUNTERPARTY, OneCService.counterparty_of(rec))
        out = {'id': rec['Ref_Key'], 'kind': kind, 'number': rec.get('Number') or '',
               'date': str(rec.get('Date') or '')[:10], 'amount': None, 'posted': rec.get('Posted'),
               'deleted': rec.get('DeletionMark'), 'counterparty': cp.get('Description') or '',
               'inn': cp.get('ИНН') or cp.get('ПИНФЛ') or '', 'source_number': rec.get('НомерВходящегоДокумента') or ''}
        if rec.get('СуммаДокумента') is not None: out['amount'] = amount(rec['СуммаДокумента'])
        if detailed:
            out['items'] = []
            for line in (rec.get('Товары') or []) + (rec.get('Услуги') or []):
                p = await self.ref(E_NOMENCLATURE, line.get('Номенклатура_Key'))
                out['items'].append({'name': p.get('Description') or line.get('Содержание') or '',
                     'ikpu': p.get('КодИКПУ') or p.get('КодИзКлассификатора'),
                     **{k: amount(line[v]) if line.get(v) is not None else None for k,v in
                        [('qty','Количество'),('amount','Сумма'),('vat','СуммаНДС')]}})
            out['purpose'] = str(rec.get('НазначениеПлатежа') or '')[:1000]
        return out


def income(ledger):
    values = {'revenue': 0., 'cost': 0., 'expenses': 0., 'other_income': 0.}
    for row in ledger:
        dt, kt, value = row['dt'], row['kt'], amount(row['amount'])
        # Oy/yil yopilishining 99xx bilan o'tkazmalari operatsion natijani yo'qotmasin.
        if dt.startswith('99') or kt.startswith('99'): continue
        for code, sign in ((dt, -1), (kt, 1)):
            if code.startswith('90'): values['revenue'] += sign * value
            elif code.startswith(('93', '95')): values['other_income'] += sign * value
            elif code.startswith('91'): values['cost'] -= sign * value
            elif code.startswith(('94', '96', '98')): values['expenses'] -= sign * value
    values['profit'] = values['revenue'] + values['other_income'] - values['cost'] - values['expenses']
    return {k: round(v, 2) for k,v in values.items()}


def fifo_split(balance, moves, end, limit):
    # Qoldiqni eng yangi oshirishlardan eskisiga qarab yopamiz: to'lovlar eng eski qarzni yopadi deb olinadi.
    remaining, old, oldest = balance, 0., None
    for d, value in sorted(moves, reverse=True):
        if remaining <= .005: break
        if value < 0: remaining -= value; continue
        take = min(value, remaining)
        remaining -= take
        oldest = d
        if (end - d).days >= limit: old += take
    beyond = remaining > .005
    if beyond: old += remaining
    return old, (AGE_WINDOW if beyond else (end - oldest).days if oldest else None), beyond


async def fifo_age(reader, debt, accounts, end, limits):
    # Hujjat kesimi yo'q hisob-kitoblarda qarz yoshi kontragent provodkalaridan FIFO bilan topiladi.
    todo = [e for e in debt.values() if e['side'] in ('receivable', 'payable') and e['unknown_amount'] > .005]
    if not todo or reader.onec.mode != 'live': return
    start = datetime.combine(end - timedelta(days=AGE_WINDOW), time.min)
    moves = {}
    for side, column in (('receivable', 'Dr'), ('payable', 'Cr')):
        if not any(e['side'] == side for e in todo): continue
        for key in sorted(k for k in accounts.get(side, ()) if k):
            for cp, d, value in await reader.increases(key, column, start, datetime.combine(end, time(23, 59, 59))):
                moves.setdefault((side, cp), []).append((d, value))
    for e in todo:
        old, days, beyond = fifo_split(e['unknown_amount'], moves.get((e['side'], e['counterparty_id']), []), end,
                                       limits['debt_age_days'])
        e['old_amount'] += old
        e['oldest_days'] = max(e['oldest_days'] or 0, days) if days is not None else e['oldest_days']
        e['age_beyond'] = beyond
        e['age_method'] = 'fifo'
        e['unknown_amount'] = 0.


async def balance_summary(reader, rows, end, limits):
    cash, bank, stock, debt, fifo = 0., 0., {}, {}, {}
    warnings = []
    for r in rows:
        code, value = r['_code'], amount(r.get('СуммаBalance'))
        if code.startswith('50'): cash += value
        elif code.startswith(('51','52','55')): bank += value
        dims = [(str(r.get('ExtDimension'+str(i)+'_Type') or '').split('.')[-1], r.get('ExtDimension'+str(i))) for i in (1,2,3)]
        if code.startswith(('10', '28', '29')):
            key = next((k for t,k in dims if t == E_NOMENCLATURE), None)
            if not key:
                warnings.append('Ayrim ombor qoldiqlarida tovar kesimi aniqlanmadi.'); continue
            item = stock.setdefault((code,key), {'code': code, 'id': key, 'amount':0., 'qty':0.})
            item['amount'] += value
            qty = r.get('КоличествоBalance')
            if qty is None: item['qty'] = None
            elif item['qty'] is not None: item['qty'] += amount(qty)
        side = next((s for prefix,s in [('40','receivable'),('60','payable'),('43','advance_paid'),('63','advance_received')] if code.startswith(prefix)), None)
        if not side: continue
        cp = next((k for t,k in dims if t == E_COUNTERPARTY), None)
        sign = -1 if side in ('payable','advance_received') else 1
        if abs(value) <= .005: continue
        reversed_balance = value * sign < 0
        if reversed_balance:
            side = 'unclassified_credit' if value < 0 else 'unclassified_debit'
            sign = -1 if value < 0 else 1
        if not cp:
            warnings.append('Ayrim qarz qoldiqlarida kontragent kesimi aniqlanmadi.'); continue
        entry = debt.setdefault((side,cp,code if reversed_balance else ''),
            {'id':cp+'-'+code if reversed_balance else cp, 'counterparty_id':cp, 'account':code,
             'side':side, 'amount':0., 'old_amount':0., 'oldest_days':None, 'unknown_amount':0.})
        entry['amount'] += value * sign
        if not reversed_balance: fifo.setdefault(side, set()).add(r.get('Account_Key'))
        doc = next(((t,k) for t,k in dims if t in DOC_TYPES.values()), None)
        days = None
        if doc:
            rec = await reader.ref(*doc)
            if rec: reader.check_org([rec])
            try:
                dt = date.fromisoformat(str(rec.get('Date'))[:10])
                if dt <= end: days = (end-dt).days
            except ValueError: pass
        if days is None: entry['unknown_amount'] += value * sign
        else:
            entry['oldest_days'] = max(entry['oldest_days'] or 0, days)
            if days >= limits['debt_age_days']: entry['old_amount'] += value * sign
    try: await fifo_age(reader, debt, fifo, end, limits)
    except (ODataError, ValueError) as error:
        warnings.append('Qarz yoshi FIFO bo‘yicha hisoblanmadi: ' + explain(error))
    for item in stock.values():
        rec = await reader.ref(E_NOMENCLATURE, item['id'])
        item.update(name=rec.get('Description') or 'Nomi olinmagan', ikpu=rec.get('КодИКПУ') or rec.get('КодИзКлассификатора') or '')
        unit=await reader.ref('Catalog_КлассификаторЕдиницИзмерения',rec.get('ЕдиницаИзмерения_Key'))
        item['unit']=unit.get('Description') or rec.get('ЕдиницаИзмерения') or ''
        item['amount'] = round(item['amount'],2)
    for item in debt.values():
        cp = await reader.ref(E_COUNTERPARTY, item['counterparty_id'])
        item.update(name=cp.get('Description') or 'Nomi olinmagan', inn=cp.get('ИНН') or cp.get('ПИНФЛ') or '')
        for key in ('amount','old_amount','unknown_amount'): item[key] = round(item[key],2)
        item['reasons'] = []
        if item['side'] in ('receivable','payable'):
            if item['old_amount'] > .005: item['reasons'].append('Qarz yoshi ' + str(int(limits['debt_age_days'])) + ' kunga yetgan')
            if item['amount'] > limits['debt_large_amount']: item['reasons'].append('Yirik qarz chegarasidan oshgan')
    return {'bank':round(bank,2), 'cash':round(cash,2),
            'stock': sorted(stock.values(),key=lambda r:-r['amount']),
            'stock_total':None if any('ombor' in w for w in warnings) else round(sum(x['amount'] for x in stock.values()),2),
            'debts_complete': not any('qarz' in w for w in warnings),
            'debts': sorted(debt.values(),key=lambda r:-r['amount']), 'warnings':list(dict.fromkeys(warnings))}


async def build(session, start='', end='', refresh=False):
    a,b = period(start,end)
    settings = db.firm_settings(session.firm_id)
    # Kesh faqat shu firma va shu ulanish uchun; har bir HTTP so'rovda kirish qayta tekshiriladi.
    key = (session.firm_id, core.onec_fingerprint(session.firm_id), a.isoformat(), b.isoformat(),
           settings['debt_age_days'],settings['debt_large_amount'])
    async with LOCKS.setdefault(session.firm_id, asyncio.Lock()):
        hit = CACHE.get(key)
        if not refresh and hit and clock.monotonic()-hit[0] < 60: return deepcopy(hit[1])
        onec = onec_for(session.firm_id)
        reader = Reader(onec, settings['inn'], shared_refs(session.firm_id))
        out = {'firm': {'id':session.firm_id,'name':settings['name'],'inn':settings['inn']},
               'period':{'from':a.isoformat(),'to':b.isoformat()}, 'as_of':b.isoformat(), 'source':onec.mode,
               'limits':{k:settings[k] for k in db.DEBT_LIMITS}, 'warnings':[], 'balances':None, 'income':None,
               'updated_at':SUCCESS.get(key), 'attempted_at':datetime.now(timezone.utc).isoformat(), 'partial':True}
        try:
            await reader.ready()
        except (ODataError,ValueError) as error:
            out['warnings'].append(explain(error))
            return out
        try:
            data = await reader.balances(datetime.combine(b,time(23,59,59)))
            out['balances'] = await balance_summary(reader,data,b,out['limits'])
            out['warnings'].extend(out['balances'].pop('warnings'))
        except (ODataError,ValueError) as error:
            out['warnings'].append('Qoldiqlar: '+explain(error))
        try:
            out['income'] = income(await reader.ledger(a,b))
        except (ODataError,ValueError) as error:
            out['warnings'].append('Foyda/zarar: '+explain(error))
        if out['balances'] is not None and out['income'] is not None and not out['warnings']:
            out['updated_at'] = out['attempted_at']
            if len(SUCCESS)>100:SUCCESS.clear()
            SUCCESS[key]=out['updated_at']
        out['partial'] = bool(out['warnings']) or out['balances'] is None or out['income'] is None
        # Natija har safar manba va davrga bog'langan; eski to'liq natija xatoda yashirin qaytarilmaydi.
        if len(CACHE) > 100: CACHE.clear()
        CACHE[key] = clock.monotonic(),deepcopy(out)
        return out


async def documents(session, kind, start='', end='', page=1, search=''):
    if kind not in DOC_TYPES: raise ValueError('Hujjat turi noto‘g‘ri')
    a,b = period(start,end)
    async with LOCKS.setdefault(session.firm_id, asyncio.Lock()):
        reader = Reader(onec_for(session.firm_id), db.firm_settings(session.firm_id)['inn'], shared_refs(session.firm_id))
        await reader.ready()
        rows = await reader.rows(DOC_TYPES[kind], [Cond('Date','ge',DT(datetime.combine(a,time.min))),
                 Cond('Date','le',DT(datetime.combine(b,time(23,59,59))))])
        for r in rows:
            try:d=date.fromisoformat(str(r.get('Date'))[:10])
            except ValueError:raise ValueError('Hujjat sanasi olinmagan') from None
            if not a<=d<=b or not r.get('Ref_Key'):raise ValueError('Hujjat davri yoki identifikatori mos emas')
        result = [await reader.brief(kind,r) for r in rows]
        q = search.casefold()
        result = [r for r in result if not q or any(q in str(r[k]).casefold() for k in ('number','counterparty','inn','source_number'))]
        result.sort(key=lambda r:(r['date'],r['number']),reverse=True)
        return {'rows':result[(page-1)*50:page*50], 'count':len(result), 'page':page}


async def document(session, kind, key):
    async with LOCKS.setdefault(session.firm_id, asyncio.Lock()):
        r = Reader(onec_for(session.firm_id), db.firm_settings(session.firm_id)['inn'], shared_refs(session.firm_id))
        await r.ready()
        return await r.document(kind,key)
