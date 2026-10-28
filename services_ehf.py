"""E-DOCS ↔ 1C: faqat o'qish, alohida kirim/chiqim va ikki tomonlama sverka."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
import hashlib
import math
import re

from services_1c import Cond, DT, Guid, E_COUNTERPARTY, E_NOMENCLATURE, OneCService, ODataError

ENTITIES = {
    'in': ('Document_ПоступлениеТоваровУслуг', 'Document_СчетФактураПолученный'),
    'out': ('Document_РеализацияТоваровУслуг', 'Document_СчетФактураВыданный'),
}
ACTIVE = {'signed', 'sended', 'received'}
DELETED = {'deleted', 'erased'}


def number(value):
    value = re.sub(r'^(?:№\s*|N[OО]?\.?\s*(?=\d))', '', str(value or '').strip(), flags=re.I).casefold()
    value = re.sub(r'\s+', '', value)
    return re.sub(r'\d+', lambda m: str(int(m[0])), value)


def day(value):
    try:
        d = date.fromisoformat(str(value)[:10])
        return d if d.year > 1 else None
    except ValueError:
        return None


def base_row(inv):
    flow = inv.get('flow', 'in')
    row = {'id': 'e-' + hashlib.sha1(f"{flow}|{inv['id']}".encode()).hexdigest()[:12],
           'kind': 'ehf', 'flow': flow, 'source_id': inv['id'], 'roaming_id': inv.get('roaming_id', ''), 'entity': ENTITIES[flow][0],
           'direction': 'kirim' if flow == 'in' else 'chiqim', 'date': inv['date'], 'number': inv['number'],
           'amount': inv['total'], 'counterparty_name': inv.get('counterparty_name', inv.get('seller_name', '')),
           'counterparty_inn': inv.get('counterparty_inn', inv.get('seller_inn', '')),
           'ehf_status': inv.get('ehf_status', 'signed'), 'ehf_status_label': inv.get('ehf_status_label', 'Tasdiqlangan'),
           'items': inv.get('items', []), 'parse_error': inv.get('parse_error'),
           'purpose': 'EHF: ' + ', '.join(i['name'] for i in inv.get('items', []))[:300],
           'account': '60.10' if flow == 'in' else '40.10', 'account_source': 'rules',
           'account_badge': 'EHF sverka — faqat o‘qish', 'advance_account': '', 'settlement_note': '',
           'posting_note': '', 'operation': '', 'learned': None, 'blockers': [], 'diffs': [], 'comparison_warnings': [], 'match': None,
           'counterparty_key': None, 'mfo': '', 'account_no': '', 'posted': False, 'posted_ref': None,
           'posted_number': None, 'error': None, 'read_only': True, 'selectable': False, 'overridable': False}
    return row


def result(row, code, status, text, matches=(), diffs=()):
    row.update(reconcile=code, status=status, status_label=text, status_reason=text,
               diffs=list(diffs), match=matches[0] if matches else None,
               matches=list(matches), dup_kind='exact' if code == 'matched' else 'none')
    return row


async def read_onec(onec, expected, start, end):
    """To'liq va tashkilotga chegaralangan ro'yxat; xato nol deb olinmaydi."""
    datasets, errors = {}, {}
    if onec.mode != 'live':
        return datasets, {flow: 'Jonli 1C ulanmagan — tekshirilmadi.' for flow in ENTITIES}
    try:
        meta = await onec._metadata()
        org = await onec.organization()
        if not expected or not org or str(org.get('ИНН') or org.get('ПИНФЛ') or '') != expected:
            raise ODataError('1C tashkiloti STIRi tasdiqlanmadi.')
    except (ODataError, ValueError) as e:
        return datasets, {flow: str(e) for flow in ENTITIES}
    for flow, entities in ENTITIES.items():
        records, complete, available = [], True, False
        for entity in entities:
            if entity not in meta['sets']:
                continue
            available = True
            try:
                props = await onec.entity_props(entity)
                if not props or 'Организация_Key' not in props['header']:
                    raise ODataError('Hujjat uchun tashkilot chegarasi tasdiqlanmadi: ' + entity)
                conds = [Cond('Организация_Key', 'eq', Guid(org['Ref_Key'])),
                         Cond('Date', 'ge', DT(datetime.combine(start, datetime.min.time()))),
                         Cond('Date', 'le', DT(datetime.combine(end, datetime.max.time()).replace(microsecond=0)))]
                seen = set()
                for offset in range(0, 20000, 500):
                    page = await onec.t.query(entity, conds, top=500, skip=offset, orderby='Ref_Key')
                    for rec in page:
                        key = rec.get('Ref_Key')
                        if not key or key in seen:
                            raise ODataError('1C sahifalashda takroriy ID. To‘liq tekshirilmadi.')
                        seen.add(key)
                    if any(rec.get('Организация_Key') != org['Ref_Key'] for rec in page):
                        raise ODataError('1C javobidagi tashkilot mos emas.')
                    records.extend(dict(rec, _entity=entity) for rec in page)
                    if len(page) < 500: break
                else:
                    raise ODataError('1C hujjatlari o‘qish chegarasidan oshdi.')
            except ODataError as e:
                complete = False
                errors[flow] = str(e)
        if available and complete:
            try:
                keys = [OneCService.counterparty_of(r) for r in records]
                names, products = {}, {}
                for key in dict.fromkeys(k for k in keys if k):
                    names[key] = await onec.t.get(E_COUNTERPARTY, key) or {}
                for rec in records:
                    for line in (rec.get('Товары') or []) + (rec.get('Услуги') or []):
                        key = line.get('Номенклатура_Key')
                        if key and key not in products:
                            products[key] = await onec.t.get(E_NOMENCLATURE, key) or {}
                datasets[flow] = [candidate(r, names, products) for r in records]
                if any(not c['inn'] or c['amount'] is None or not c['in_date'] or not c['numbers'] for c in datasets[flow]):
                    errors[flow] = '1C hujjatlarining ayrim rekvizitlari to‘liq emas; topilmaganlik tasdiqlanmaydi.'
            except ODataError as e:
                errors[flow] = str(e)
        elif not available:
            errors[flow] = '1C da tegishli xarid/sotuv yoki hisob-faktura obyektlari nashr qilinmagan.'
    return datasets, errors


def candidate(rec, names, products=None):
    cp = names.get(OneCService.counterparty_of(rec)) or {}
    nums = [rec.get(k) for k in ('НомерВходящегоДокумента', 'НомерСчетаФактуры', 'НомерДокументаОснования', 'Number')]
    date_value = next((day(rec.get(k)) for k in ('ДатаВходящегоДокумента', 'ДатаСчетаФактуры', 'Date') if day(rec.get(k))), None)
    amount = next((rec[k] for k in ('СуммаДокумента', 'СуммаСНДС', 'ИтоговаяСумма', 'Сумма') if rec.get(k) is not None), None)
    try:
        amount = float(amount)
        if not math.isfinite(amount): amount = None
    except (ValueError, TypeError): amount = None
    items = []
    products = products or {}
    for line in (rec.get('Товары') or []) + (rec.get('Услуги') or []):
        product = products.get(line.get('Номенклатура_Key')) or {}
        qty, value, vat = line.get('Количество'), line.get('Сумма'), line.get('СуммаНДС')
        try:
            qty = float(qty)
            value = float(value) if value is not None else None
            vat = float(vat) if vat is not None else None
        except (TypeError, ValueError):
            qty, value, vat = None, None, None
        gross = (value if rec.get('СуммаВключаетНДС') is True else value + vat if value is not None and vat is not None and rec.get('СуммаВключаетНДС') is False else None)
        items.append({'name': product.get('Description') or line.get('Содержание') or '',
                      'ikpu': product.get('КодИКПУ') or product.get('КодИзКлассификатора'),
                      'qty': qty, 'total': gross, 'vat_sum': vat})
    return {'ref': rec['Ref_Key'], 'entity': rec['_entity'], 'number': rec.get('Number'),
            'in_number': nums[0], 'in_date': date_value.isoformat() if date_value else '',
            'date': str(rec.get('Date', ''))[:10], 'amount': amount, 'posted': bool(rec.get('Posted')),
            'deleted': bool(rec.get('DeletionMark')), 'inn': str(cp.get('ИНН') or cp.get('ПИНФЛ') or ''),
            'counterparty': cp.get('Description') or '',
            'external_ids': [str(rec[k]).lower() for k in ('ИдентификаторЭДО', 'ИдентификаторДокументаЭДО', 'ИдентификаторДокумента', 'ИдентификаторСчетаФактуры') if rec.get(k)], 'numbers': [number(v) for v in nums if v], 'items': items}


def matching(row, candidates):
    target, n = day(row['date']), number(row['number'])
    linked = [c for c in candidates if row.get('roaming_id') and str(row['roaming_id']).lower() in c.get('external_ids', [])]
    numbered = [c for c in candidates if n and n in c['numbers'] and day(c['in_date'])
                and abs((day(c['in_date']) - target).days) <= 366]
    same_cp = [c for c in numbered if c['inn'] and c['inn'] == row['counterparty_inn']]
    exact = [c for c in same_cp if c['in_date'] == row['date'] and c['amount'] is not None and abs(c['amount'] - row['amount']) <= .01]
    if linked:
        cands = linked
    elif exact:
        cands = exact + [c for c in same_cp if c not in exact and c['in_date'] == row['date']]
    else:
        cands = same_cp or numbered
    if not cands:
        # Raqam o'zgargan holatni mos deb yashirmaymiz — farq sifatida ko'rsatamiz.
        cands = [c for c in candidates if c['inn'] == row['counterparty_inn'] and c['in_date'] == row['date']
                 and c['amount'] is not None and abs(c['amount'] - row['amount']) <= .01]
    diffs = []
    for c in cands:
        if n not in c['numbers']: diffs.append('Faktura raqami farqli yoki 1C da tashqi raqam to‘ldirilmagan.')
        if c['in_date'] != row['date']: diffs.append('Faktura sanasi farqli: ' + c['in_date'])
        if c['amount'] is None or abs(c['amount'] - row['amount']) > .01: diffs.append('Faktura umumiy summasi farqli.')
        if not c['inn'] or c['inn'] != row['counterparty_inn']: diffs.append('Kontragent STIRi farqli yoki 1C da tasdiqlanmagan.')
    return cands, list(dict.fromkeys(diffs))


def _item_key(v):
    # «4×2×0.57» va «4x2x0,57» bir xil; bo'shliqlar ahamiyatsiz
    return re.sub(r'\s+', '', str(v or '').replace('×', 'x').replace('х', 'x').replace(',', '.')).casefold()


def _same_numbers(a, b):
    return all(x is not None and y is not None and abs(x - y) <= tol
               for x, y, tol in [(a.get('qty'), b.get('qty'), .000001), (a.get('total'), b.get('total'), .02)])


def _item_choices(item, target, remaining):
    """EHF qatoriga 1C qatorini topish: nom → nom boshi (1C dagi «-145» kabi qo'shimcha) → MXIK.
    Bir nechta nomzod bo'lsa, miqdor va summa bilan toraytiriladi."""
    key = _item_key(item['name'])
    steps = [lambda o: _item_key(o['name']) == key,
             lambda o: key and (_item_key(o['name']).startswith(key) or key.startswith(_item_key(o['name']) or '\0')),
             lambda o: item.get('ikpu') and o.get('ikpu') == item['ikpu']]
    for test in steps:
        choices = [i for i in remaining if test(target[i])]
        if len(choices) > 1:
            choices = [i for i in choices if _same_numbers(item, target[i])] or choices
        if choices:
            return choices
    return []


def compare_items(row, candidate):
    """Tasdiqlangan farq va moslashtirishdagi noaniqlik alohida qaytadi."""
    if not candidate.get('items') or not row['items']:
        return [], ['Tovar qatorlari to‘liq tekshirilmadi (1C yoki EHF tafsiloti yetishmaydi).']
    target = candidate['items']
    remaining = list(range(len(target)))
    diffs, warnings = [], []
    unresolved = False
    for item in row['items']:
        choices = _item_choices(item, target, remaining)
        if len(choices) != 1:
            warnings.append('Tovar nomi/MXIK bo‘yicha aniq moslik topilmadi: ' + item['name'])
            unresolved = True
            continue
        i = choices[0]
        remaining.remove(i)
        other = target[i]
        if item.get('ikpu') and other.get('ikpu') and item['ikpu'] != other['ikpu']:
            diffs.append('MXIK farqli: ' + item['name'])
        for key, label, tolerance in [('qty', 'Miqdor', .000001), ('total', 'Qator summasi', .02), ('vat_sum', 'QQS summasi', .02)]:
            a, b = item.get(key), other.get(key)
            if a is None or b is None:
                warnings.append(label + ' to‘liq tekshirilmadi: ' + item['name'])
            elif abs(a - b) > tolerance:
                diffs.append(label + ' farqli: ' + item['name'])
    if remaining:
        if unresolved:
            warnings.append('1C qatorlari EHF bilan to‘liq moslashtirilmadi; qo‘shimcha tovar borligi hali tasdiqlanmagan.')
        else:
            diffs.append('1C da EHFga moslashtirilmagan qo‘shimcha tovar qatorlari bor.')
    return diffs, warnings


def item_diffs(row, matches):
    candidates = [c for c in matches if c.get('items')]
    if not candidates:
        return [], ['1C da tovar qatorlari tekshirilmadi.']
    comparisons = [compare_items(row, c) for c in candidates]
    return tuple(list(dict.fromkeys(d for comparison in comparisons for d in comparison[i])) for i in (0, 1))


async def reconcile(invoices, onec, expected, date_from='', date_to=''):
    rows = [base_row(inv) for inv in invoices]
    dates = [day(r['date']) for r in rows if day(r['date'])]
    start = day(date_from) or (min(dates) if dates else None)
    end = day(date_to) or (max(dates) if dates else None)
    warnings = list(getattr(invoices, 'warnings', []))
    if start and end:
        # Kiritilgan sana va EHF sanasi turli oylarda bo'lsa ham shu yil ichida topiladi.
        datasets, errors = await read_onec(onec, expected, date(start.year, 1, 1) - timedelta(days=45), date(end.year, 12, 31) + timedelta(days=45))
    else:
        datasets, errors = {}, {flow: '1C ro‘yxatini tekshirish uchun sana davrini tanlang.' for flow in ENTITIES}
    used = set()
    for row in rows:
        st, flow = row['ehf_status'], row['flow']
        if st not in ACTIVE | DELETED:
            result(row, 'skipped', 'ignored', 'EHF holati bo‘yicha 1C sverkaga kiritilmagan')
            continue
        if flow not in datasets:
            result(row, 'unchecked', 'unchecked', errors.get(flow, '1C tekshirilmadi.'))
            continue
        matches, diffs = matching(row, datasets[flow])
        used.update((c['entity'], c['ref']) for c in matches)
        if st in DELETED:
            active = [c for c in matches if not c['deleted']]
            if active: result(row, 'deleted_in_1c', 'discrepancy', '🔴 EHF o‘chirilgan, 1C da faol hujjat bor', active, diffs)
            elif flow in errors: result(row, 'unchecked', 'unchecked', errors[flow], matches)
            else: result(row, 'deleted_absent', 'ignored', 'EHF o‘chirilgan; 1C da faol mos hujjat topilmadi', matches)
        elif not matches:
            if flow in errors:
                result(row, 'unchecked', 'unchecked', errors[flow])
            else:
                result(row, 'missing', 'new', '🟡 1C da topilmadi')
        else:
            if any(c['deleted'] for c in matches): diffs.append('1C hujjati o‘chirishga belgilangan.')
            product_diffs, product_warnings = item_diffs(row, matches)
            diffs.extend(product_diffs)
            if row.get('parse_error'): product_warnings.append(row['parse_error'])
            row['comparison_warnings'] = list(dict.fromkeys(product_warnings))
            if any(v > 1 for v in Counter(c['entity'] for c in matches).values()): diffs.append('Bir turdagi bir nechta mos hujjat: qo‘lda tekshirish kerak.')
            if st == 'signed' and any(not c['posted'] for c in matches): diffs.append('Tasdiqlangan EHF uchun 1C hujjati qoralama holatida.')
            if diffs:
                result(row, 'differences', 'discrepancy', '🔴 Rekvizitlarda farq bor', matches, diffs)
            elif product_warnings:
                result(row, 'partial', 'unchecked', '🟠 Rekvizitlar mos; tovarlar to‘liq tekshirilmadi', matches)
            else:
                result(row, 'matched', 'duplicate', '🟢 1C bilan mos', matches)
    # Teskari nazorat: EHFda umuman ko'rinmagan 1C hujjatlari.
    for flow, candidates in datasets.items():
        for c in candidates:
            if c['amount'] is None or c['deleted'] or (c['entity'], c['ref']) in used or not start <= (day(c['in_date']) or date.min) <= end:
                continue
            if any(r['flow'] == flow and r['counterparty_inn'] == c['inn'] and r['date'] == c['in_date']
                   and number(r['number']) in c['numbers'] for r in rows):
                continue
            synthetic = {'id': c['entity'] + c['ref'], 'number': c['in_number'] or c['number'] or '', 'date': c['in_date'],
                         'total': c['amount'] if c['amount'] is not None else 0, 'flow': flow, 'counterparty_inn': c['inn'], 'counterparty_name': c['counterparty']}
            r = base_row(synthetic)
            r.update(source_id=None, ehf_status='onec_only', ehf_status_label='EHF ro‘yxatida yo‘q', entity=c['entity'])
            result(r, 'onec_only', 'discrepancy', '🟠 1C da bor, yuklangan EHF ro‘yxatida topilmadi', [c])
            rows.append(r)
    warnings.extend(dict.fromkeys(errors.values()))
    return rows, {'counts': dict(Counter(r['reconcile'] for r in rows)), 'warnings': warnings,
                  'period': {'from': start.isoformat(), 'to': end.isoformat()} if start and end else None,
                  'coverage': {flow: flow in datasets for flow in ENTITIES}}
