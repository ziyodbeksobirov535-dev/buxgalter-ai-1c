"""E-DOCS login/parol autentifikatsiyasi va kirim/chiqim EHF o‘qishi.

Manba: doc.edocs.uz ochiq Login va asosiy JavaScript modullari.
O‘qish jonli hisobda tekshirildi; bu rasmiy hamkorlik API shartnomasi emas.
Token faqat bitta tekshiruv davomida xotirada turadi, hech qayerga qaytarilmaydi.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
import math
import os
import re

import httpx

from services_edocs import EdocsError

API_BASE = 'https://api.edocs.uz/v1.0.1/'


def _body(response: httpx.Response) -> dict:
    if response.status_code in (401, 403):
        raise EdocsError('E-DOCS kirishga ruxsat bermadi. Login/parol va hisob huquqlarini tekshiring; qayta urinish bajarilmadi.')
    if not 200 <= response.status_code < 300:
        raise EdocsError(f'E-DOCS HTTP {response.status_code} javobini berdi. Hisob tekshirilmadi.')
    try:
        body = response.json()
    except ValueError:
        raise EdocsError('E-DOCS javobi JSON emas. Ulanish yoki xizmat formatini tekshiring.') from None
    if not isinstance(body, dict):
        raise EdocsError('E-DOCS javob formati o‘zgargan. Ulanishni moslash kerak.')
    return body


def _credentials(settings):
    settings = dict(settings)
    expected = str(settings.get('inn') or '')
    if not settings.get('edocs_login') and not settings.get('edocs_password') and expected and expected == os.getenv('EDOCS_INN'):
        # Bulut bindinglari faqat aynan shu firma uchun ishlatiladi.
        settings.update(edocs_login=os.getenv('EDOCS_LOGIN'), edocs_password=os.getenv('EDOCS_PASSWORD'))
    if not settings.get('edocs_login') or not settings.get('edocs_password'):
        raise EdocsError('E-DOCS login va parolini firma sozlamalariga kiriting.')
    if not expected.isascii() or not expected.isdigit() or len(expected) not in (9, 14):
        raise EdocsError('Avval firma STIR/JShShIRini sozlamalarga kiriting.')
    return settings, expected


@asynccontextmanager
async def _session(settings, transport=None):
    settings, expected = _credentials(settings)
    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=25, follow_redirects=False,
                                     transport=transport) as client:
            response = await client.post('loginpassword', json={'login': settings['edocs_login'], 'password': settings['edocs_password']})
            token = _body(response).get('token')
            if not isinstance(token, str) or not token.strip():
                raise EdocsError('E-DOCS autentifikatsiya tokenini bermadi. Hisob tekshirilmadi.')
            client.headers['Authorization'] = f'Bearer {token}'
            profile = _body(await client.get('profile'))
            if str(profile.get('tin') or profile.get('personalNum') or '') != expected:
                raise EdocsError('E-DOCS profilidagi firma STIRi mos emas. Hujjatlar olinmadi.')
            yield client, expected
    except httpx.HTTPError:
        raise EdocsError('api.edocs.uz xizmatiga ulanish bajarilmadi. Tarmoq yoki TLS ulanishini tekshiring.') from None


async def check_connection(settings: dict, *, transport=None) -> dict:
    """Bir login urinishi va faqat profilni o'qish."""
    async with _session(settings, transport):
        pass
    return {'ok': True, 'method': 'edocs_api',
            'detail': 'E-DOCS login/paroli va firma STIRi tekshirildi. Fakturalarni olish hali tekshirilmadi.'}


def _amount(value):
    if value is None or isinstance(value, bool):
        raise ValueError('number')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('number')
    return value


STATUSES = {'signed': 'Tasdiqlangan', 'sended': 'Imzo kutilmoqda', 'received': 'Imzo kutilmoqda',
            'drafts': 'Qoralama', 'rejected': 'Rad etilgan', 'deleted': 'O‘chirilgan', 'erased': 'O‘chirilgan'}


class InvoiceBatch(list):
    def __init__(self, rows=(), warnings=()):
        super().__init__(rows)
        self.warnings = list(warnings)


def map_invoice(doc, expected, flow=None):
    """EHF yo'nalishi pul oqimi emas: xarid — kirim, sotuv — chiqim."""
    try:
        data = doc['data']
        buyer, seller = str(data['buyertin']), str(data['sellertin'])
        flow = flow or ('in' if buyer == expected else 'out' if seller == expected else None)
        if doc['type'] != 'factura' or flow not in ('in', 'out') or (buyer if flow == 'in' else seller) != expected:
            raise ValueError('firm/type')
        status = doc['status']
        if status not in STATUSES:
            raise ValueError('status')
        fid = str(doc['_id'])
        if not re.fullmatch(r'[A-Za-z0-9_-]+', fid):
            raise ValueError('id')
        number = str(data['facturadoc']['facturano'])
        day = date.fromisoformat(data['facturadoc']['facturadate'][:10]).isoformat()
        cp = seller if flow == 'in' else buyer
        if not number or not cp.isdigit() or len(cp) not in (9, 14):
            raise ValueError('number/counterparty')
        total = _amount(doc['totalSumWithVat'])
        items, parse_error = [], None
        try:
            for item in data['productlist']['products']:
                rate = 0.0 if item.get('withoutvat') is True else _amount(item['vatrate'])
                if not 0 <= rate <= 100 or not item['name']:
                    raise ValueError('vat/name')
                items.append({'name': str(item['name']), 'ikpu': item.get('catalogcode'),
                              'qty': _amount(item['count']), 'price': _amount(item['summa']),
                              'total': _amount(item['deliverysumwithvat']), 'vat_rate': rate,
                              'vat_sum': _amount(item['vatsum']) if item.get('vatsum') is not None else None})
            if not items or abs(sum(i['total'] for i in items) - total) > max(.02, len(items) * .01):
                raise ValueError('items total')
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            items = []
            parse_error = 'Tovar qatorlari yoki ularning summasi to‘liq tekshirilmadi.'
        return {'id': fid, 'number': number, 'date': day, 'seller_inn': seller,
                'seller_name': str(data['seller']['name']), 'buyer_inn': buyer,
                'buyer_name': str(data.get('buyer', {}).get('name') or ''),
                'counterparty_inn': cp, 'counterparty_name': str(data['seller']['name'] if flow == 'in' else data['buyer']['name']),
                'flow': flow, 'roaming_id': doc.get('roumingId') or data.get('facturaid') or '', 'ehf_status': status, 'ehf_status_label': STATUSES[status],
                'parse_error': parse_error, 'total': total, 'kind': 'Товары', 'items': items}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise EdocsError('E-DOCS fakturasi sarlavhasi yoki firma STIRi mos emas. Qisman import bajarilmadi.') from None


async def fetch_invoices(settings, *, transport=None, date_from='', date_to='', max_documents=10000, direction='both', statuses=None, known_ids=()):
    """Kirim/chiqim va barcha saytda mavjud holatlar; qisman yuklash yashirilmaydi."""
    if direction not in ('in', 'out', 'both'):
        raise EdocsError('EHF yo‘nalishi in, out yoki both bo‘lsin.')
    if statuses is not None and (not statuses or any(st not in STATUSES for st in statuses)):
        raise EdocsError('EHF holati noto‘g‘ri.')
    for day in (date_from, date_to):
        if day:
            try:
                date.fromisoformat(day)
            except ValueError:
                raise EdocsError('Davr YYYY-MM-DD formatida bo‘lsin.') from None
    if date_from and date_to and date_from > date_to:
        raise EdocsError('Davr boshi oxiridan keyin bo‘lmasin.')
    if not isinstance(max_documents, int) or not 1 <= max_documents <= 10000:
        raise EdocsError('Faktura chegarasi 1–10000 oralig‘ida bo‘lsin.')
    buckets = [('in', 'received'), ('in', 'signed'), ('in', 'rejected'),
               ('out', 'drafts'), ('out', 'sended'), ('out', 'signed'), ('out', 'rejected'), ('out', 'deleted')]
    buckets = [(io, st) for io, st in buckets if (direction == 'both' or io == direction)
               and (statuses is None or st in statuses or (st == 'received' and 'sended' in statuses))]
    warnings = []
    if direction in ('in', 'both') and (statuses is None or 'deleted' in statuses):
        warnings.append('E-DOCS kiruvchi o‘chirilganlar ro‘yxatini alohida bermaydi (ERROR_WITH_FILTER). Avval yuklanganlar ID bo‘yicha qayta tekshiriladi; boshlang‘ich qamrov to‘liq tasdiqlanmagan.')
    async with _session(settings, transport) as (client, expected):
        all_ids, seen = [], set()
        for io, status in buckets:
            params = {'type': 'factura', 'io': io, 'status': status, 'sort': 'updatedAt', 'order': -1, 'limit': 50}
            if date_from: params['dateMin'] = date_from
            if date_to: params['dateMax'] = date_to
            ids, total = [], None
            for page in range(1, 202):
                body = _body(await client.get('documents', params={**params, 'page': page}))
                rows, count = body.get('docs'), body.get('totalDocs')
                if not isinstance(rows, list) or type(count) is not int or count < 0 or body.get('page') != page:
                    raise EdocsError('E-DOCS sahifalash formati o‘zgargan. Import to‘xtatildi.')
                if len(all_ids) + count > max_documents:
                    raise EdocsError('Fakturalar chegaradan ko‘p. Kichikroq sana davrini tanlang.')
                if total is not None and total != count:
                    raise EdocsError('Yuklash vaqtida hujjatlar ro‘yxati o‘zgardi. Qayta tekshirish kerak.')
                total = count
                for row in rows:
                    fid = row.get('_id') if isinstance(row, dict) else None
                    if not isinstance(fid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', fid) or (io, fid) in seen:
                        raise EdocsError('E-DOCS ro‘yxatida noto‘g‘ri yoki takroriy ID. Import to‘xtatildi.')
                    seen.add((io, fid))
                    ids.append(fid)
                if body.get('hasNextPage') is False: break
                if body.get('hasNextPage') is not True or not rows or body.get('nextPage') != page + 1:
                    raise EdocsError('E-DOCS sahifalari to‘liq olinmadi. Import to‘xtatildi.')
            else:
                raise EdocsError('E-DOCS sahifalar chegarasi oshdi. Import to‘xtatildi.')
            if len(ids) != total:
                raise EdocsError('E-DOCS hujjatlari soni mos emas. Qisman natija saqlanmadi.')
            all_ids.extend((io, fid) for fid in ids)
        if statuses is None:
            for previous in known_ids:
                io, fid = previous.get('flow'), previous.get('id')
                if io not in ('in', 'out') or (direction != 'both' and io != direction): continue
                if not isinstance(fid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', fid):
                    raise EdocsError('Oldingi EHF IDsi noto‘g‘ri.')
                if (io, fid) not in seen:
                    seen.add((io, fid))
                    all_ids.append((io, fid))
        if len(all_ids) > max_documents:
            raise EdocsError('Oldingi EHF bilan birga hujjatlar chegaradan ko‘p. Davrni kichraytiring.')
        invoices = []
        previous_keys = {(p.get('flow'), p.get('id')) for p in known_ids}
        for io, fid in all_ids:
            response = await client.get('documents/factura/' + fid)
            if response.status_code == 404 and (io, fid) in previous_keys:
                message = 'Avval yuklangan ayrim EHF tafsilotlari manbada ochilmadi; 1C teskari nazoratini ko‘ring.'
                if message not in warnings: warnings.append(message)
                continue
            detail = _body(response)
            if detail.get('_id') != fid:
                raise EdocsError('E-DOCS hujjat IDsi mos emas. Import to‘xtatildi.')
            mapped = map_invoice(detail, expected, io)
            if (not date_from or mapped['date'] >= date_from) and (not date_to or mapped['date'] <= date_to):
                invoices.append(mapped)
        return InvoiceBatch(invoices, warnings)


def main() -> int:
    """Bulutdagi maxfiy bindinglar orqali qiymatlarni chiqarmasdan tekshirish."""
    import asyncio
    import argparse
    parser = argparse.ArgumentParser(description='E-DOCS o‘qish sinovi')
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--direction', choices=['in', 'out', 'both'], default='both')
    parser.add_argument('--from-date', default='')
    parser.add_argument('--to-date', default='')
    args = parser.parse_args()
    settings = {'edocs_login': os.getenv('EDOCS_LOGIN', ''),
                'edocs_password': os.getenv('EDOCS_PASSWORD', ''),
                'inn': os.getenv('EDOCS_INN', '')}
    try:
        if args.fetch:
            invoices = asyncio.run(fetch_invoices(settings, date_from=args.from_date, date_to=args.to_date, direction=args.direction))
            print(f'E-DOCS: {len(invoices)} ta kirim/chiqim faktura holatlari bilan olindi.')
            for warning in invoices.warnings: print('Ogohlantirish:', warning)
            return 0
        result = asyncio.run(check_connection(settings))
    except EdocsError as error:
        print(str(error))
        return 1
    print(result['detail'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
