"""1C sotuvi (Реализация) → Didox chiqim hisob-faktura QORALAMASI.

Faqat qoralama: imzolash, yuborish, o'chirish yo'q — buxgalter Didox'da o'zi tekshirib imzolaydi.
Didox'ga faqat ikkita POST ruxsat: STIR+parol bilan kirish va 002 (hisob-faktura) qoralamasini yaratish.
1C dan faqat o'qiladi (GET).

Manba: Didox'ning ochiq hujjati yo'q; yo'llar va maydonlar ochiq SDK/integratsiyalardan olingan va
jonli hisobda hali tasdiqlanmagan. Birinchi jonli sinovda payload Didox javobiga qarab moslanadi.
"""
from __future__ import annotations

import hashlib
import re
import time

import httpx

from services_1c import E_CONTRACT, OneCService, ODataError

BASE_URL = 'https://api.didox.uz/'
INVOICE_DOCTYPE = '002'
E_UNITS = 'Catalog_КлассификаторЕдиницИзмерения'
SALE_ENTITY = 'Document_РеализацияТоваровУслуг'
# Didox'ga yuborilishi mumkin bo'lgan yagona yozuvchi so'rovlar
ALLOWED_POST = (re.compile(r'^v1/auth/\d{9}/password/ru$'), re.compile(r'^v1/documents/002/create$'))
TOKEN_TTL = 20 * 60

_tokens: dict[int, tuple[str, float, str]] = {}   # firm_id -> (token, muddati, parol izi)
_failed: dict[int, str] = {}                       # firm_id -> rad etilgan parol izi (qayta urinilmaydi)


class DraftError(Exception):
    def __init__(self, message: str, status: int = 502, rejected: bool = False):
        super().__init__(message)
        self.status = status
        self.rejected = rejected  # Didox aniq rad etdi (4xx) — hech narsa yaratilmagan


def _fingerprint(settings: dict) -> str:
    raw = f"{settings.get('inn')}|{settings.get('didox_password')}|{settings.get('didox_partner_token')}"
    return hashlib.sha256(raw.encode()).hexdigest()


def check_allowed(method: str, path: str) -> None:
    """Didox'ga GET dan boshqa so'rov faqat ruxsat ro'yxatidagi yo'llarga."""
    if method == 'GET':
        return
    if method == 'POST' and any(p.match(path) for p in ALLOWED_POST):
        return
    raise DraftError(f'Didox: {method} {path} so‘rovi taqiqlangan (faqat qoralama yaratish mumkin).', 403)


def _json(response: httpx.Response, what: str):
    if response.status_code in (401, 403, 422) and what == 'login':
        raise DraftError('Didox STIR yoki parolni qabul qilmadi. Parol o‘zgarmaguncha qayta urinilmaydi '
                         '(hisob bloklanmasligi uchun).', 401)
    if response.status_code in (401, 403):
        raise DraftError('Didox ruxsat bermadi (token yoki hamkor kaliti). Qayta urinilmadi.', 401)
    if not 200 <= response.status_code < 300:
        detail = ''
        try:
            body = response.json()
            detail = str(body.get('message') or body.get('error') or '')[:200] if isinstance(body, dict) else ''
        except ValueError:
            pass
        raise DraftError(f'Didox {what}: HTTP {response.status_code}' + (f' — {detail}' if detail else ''),
                         rejected=400 <= response.status_code < 500)
    try:
        return response.json()
    except ValueError:
        raise DraftError(f'Didox {what}: javob JSON emas.') from None


class DidoxDraftClient:
    def __init__(self, settings: dict, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self.firm_id = int(settings.get('id') or 0)
        self.token = ''
        self.http = httpx.AsyncClient(base_url=BASE_URL, timeout=30, follow_redirects=False, transport=transport)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.http.aclose()

    def _headers(self) -> dict:
        h = {'Accept': 'application/json'}
        if self.token:
            h['user-key'] = self.token
            h['api-key'] = self.token
        if self.s.get('didox_partner_token'):
            h['Partner-Authorization'] = self.s['didox_partner_token']
        return h

    async def _send(self, method: str, path: str, body=None):
        check_allowed(method, path)
        try:
            return await self.http.request(method, path, json=body, headers=self._headers())
        except httpx.HTTPError as e:
            raise DraftError(f'Didox serveriga ulanib bo‘lmadi: {type(e).__name__}') from None

    async def login(self) -> None:
        tin, password = str(self.s.get('inn') or ''), self.s.get('didox_password') or ''
        if not re.fullmatch(r'\d{9}', tin) or not password:
            raise DraftError('Didox uchun firma STIRi (9 raqam) va Didox paroli kiritilmagan '
                             '(Sozlamalar → Ulanishlar).', 400)
        fp = _fingerprint(self.s)
        cached = _tokens.get(self.firm_id)
        if cached and cached[2] == fp and cached[1] > time.time():
            self.token = cached[0]
            return
        if _failed.get(self.firm_id) == fp:
            raise DraftError('Didox oldin shu parolni rad etgan. Parolni Sozlamalarda yangilang — '
                             'shundan keyin bir marta qayta uriniladi.', 401)
        response = await self._send('POST', f'v1/auth/{tin}/password/ru', {'password': password})
        try:
            body = _json(response, 'login')
        except DraftError as e:
            if e.status == 401:
                _failed[self.firm_id] = fp
            raise
        token = _pick(body, 'token', 'userKey', 'user-key', 'user_key') if isinstance(body, dict) else ''
        if not token:
            raise DraftError('Didox kirish javobida token yo‘q.')
        self.token = token
        _tokens[self.firm_id] = (token, time.time() + TOKEN_TTL, fp)

    async def get(self, path: str, what: str):
        return _json(await self._send('GET', path), what)

    async def create_draft(self, body: dict) -> str:
        answer = _json(await self._send('POST', f'v1/documents/{INVOICE_DOCTYPE}/create', body), 'qoralama')
        doc_id = _pick(answer, '_id', 'id', 'documentId', 'doc_id') if isinstance(answer, dict) else ''
        if not doc_id and isinstance(answer, dict) and isinstance(answer.get('data'), dict):
            doc_id = _pick(answer['data'], '_id', 'id', 'documentId', 'doc_id')
        if not doc_id:
            raise DraftError('Didox qoralama ID sini qaytarmadi — Didox’da tekshiring, qayta yubormang.')
        return str(doc_id)


def _pick(d: dict, *keys) -> str:
    for k in keys:
        v = d.get(k)
        if v not in (None, ''):
            return str(v)
    return ''


def _unwrap(body):
    return body.get('data') if isinstance(body, dict) and isinstance(body.get('data'), (dict, list)) else body


def party(tin: str, profile) -> dict | None:
    p = _unwrap(profile)
    if not isinstance(p, dict):
        return None
    name = _pick(p, 'name', 'fullName', 'shortName', 'companyName', 'title')
    if not name:
        return None
    return {'tin': tin, 'name': name, 'account': _pick(p, 'account', 'accountNumber', 'bankAccount'),
            'bank_id': _pick(p, 'mfo', 'bankId', 'bankCode'), 'address': _pick(p, 'address'),
            'director': _pick(p, 'director', 'directorName', 'directorFio'),
            'accountant': _pick(p, 'accountant', 'accountantName', 'accountantFio'),
            'vat_reg_code': _pick(p, 'vatRegCode'), 'profile_tin': _pick(p, 'tin', 'inn', 'taxId')}


def _unit_key(name: str) -> str:
    return re.sub(r'[\s.]+', '', str(name or '')).casefold()


def measure_index(measures) -> dict:
    """Didox o'lchov birliklari: nom → id (masalan «м», «шт», «кг»)."""
    rows = _unwrap(measures)
    index = {}
    for m in rows if isinstance(rows, list) else []:
        if not isinstance(m, dict):
            continue
        mid = _pick(m, 'id', 'measureId', 'code')
        for k in ('name', 'nameRu', 'nameUz', 'name_ru', 'name_uz', 'shortName'):
            if m.get(k) and mid:
                index.setdefault(_unit_key(m[k]), mid)
    return index


def _party_body(p: dict) -> dict:
    return {'Name': p['name'], 'Account': p.get('account', ''), 'BankId': p.get('bank_id', ''),
            'Address': p.get('address', ''), 'Director': p.get('director', ''), 'Accountant': p.get('accountant', ''),
            'VatRegCode': p.get('vat_reg_code', ''), 'BranchCode': '', 'BranchName': ''}


def build_body(sale: dict, seller: dict | None, buyer: dict | None, measures: dict) -> tuple[dict | None, list[str], list[dict]]:
    """Qoralama tanasi, to'sqinliklar va ko'rinish uchun qatorlar. To'sqinlik bo'lsa tana None."""
    problems, preview, products = [], [], []
    if not seller:
        problems.append('Didox firmangiz profilini qaytarmadi.')
    if not re.fullmatch(r'\d{9}|\d{14}', str(sale.get('inn') or '')):
        problems.append('Xaridorning STIR/PINFL i 1C da to‘ldirilmagan.')
    elif not buyer:
        problems.append('Didox xaridorni STIR bo‘yicha topmadi.')
    if not sale.get('items'):
        problems.append('1C hujjatida tovar qatorlari yo‘q.')
    has_vat = False
    for n, it in enumerate(sale.get('items') or [], 1):
        qty, total, vat = it.get('qty'), it.get('total'), it.get('vat_sum') or 0.0
        rate = it.get('vat_rate')
        name = it.get('name') or ''
        if not qty or qty <= 0 or total is None:
            problems.append(f'{n}-qator: miqdor yoki summa yo‘q ({name}).')
            continue
        if rate is None:
            net = total - vat
            rate = round(vat / net * 100) if net else None
        if rate is None:
            problems.append(f'{n}-qator: QQS stavkasi aniqlanmadi ({name}).')
            continue
        if not it.get('ikpu'):
            problems.append(f'{n}-qator: MXIK (ИКПУ) kodi 1C da yo‘q ({name}).')
        measure = measures.get(_unit_key(it.get('unit') or ''))
        if not measure:
            problems.append(f'{n}-qator: o‘lchov birligi «{it.get("unit") or "?"}» Didox’da topilmadi ({name}).')
        net = round(total - vat, 2)
        has_vat = has_vat or rate > 0
        products.append({'OrdNo': n, 'Name': name, 'CatalogCode': it.get('ikpu') or '', 'CatalogName': '',
                         'MeasureId': measure or '', 'Count': qty, 'Summa': round(net / qty, 2),
                         'ExciseRate': 0, 'ExciseSum': 0, 'DeliverySum': net, 'VatRate': rate,
                         'VatSum': round(vat, 2), 'DeliverySumWithVat': round(total, 2), 'WithoutVat': rate == 0})
        preview.append({'name': name, 'ikpu': it.get('ikpu') or '', 'unit': it.get('unit') or '', 'qty': qty,
                        'price': round(net / qty, 2), 'vat_rate': rate, 'vat_sum': round(vat, 2), 'total': round(total, 2)})
    if problems:
        return None, problems, preview
    body = {'Version': 1, 'FacturaType': 0,
            'FacturaDoc': {'FacturaNo': sale.get('number') or '', 'FacturaDate': sale.get('date') or ''},
            'SellerTin': seller['tin'], 'BuyerTin': buyer['tin'],
            'Seller': _party_body(seller), 'Buyer': _party_body(buyer),
            'ProductList': {'HasVat': has_vat, 'HasExcise': False, 'HasMedical': False, 'HasCommittent': False,
                            'HasLgota': False, 'Tin': seller['tin'], 'Products': products},
            'HasMarking': False}
    if sale.get('contract_no'):
        body['ContractDoc'] = {'ContractNo': sale['contract_no'], 'ContractDate': sale.get('contract_date') or ''}
    return body, [], preview


async def sale_details(onec: OneCService, match: dict) -> dict:
    """1C dan (faqat GET): o'lchov birligi nomlari va shartnoma raqami/sanasi."""
    sale = {'ref': match['ref'], 'number': match.get('number') or match.get('in_number') or '',
            'date': match.get('date') or match.get('in_date') or '', 'inn': match.get('inn') or '',
            'counterparty': match.get('counterparty') or '', 'amount': match.get('amount'),
            'items': [dict(i) for i in match.get('items') or []]}
    units: dict[str, str] = {}
    for it in sale['items']:
        key = it.get('unit_key')
        if key and key not in units:
            try:
                units[key] = ((await onec.t.get(E_UNITS, key)) or {}).get('Description') or ''
            except ODataError:
                units[key] = ''
        it['unit'] = units.get(key, '') if key else ''
    if match.get('contract_key'):
        try:
            c = await onec.t.get(E_CONTRACT, match['contract_key']) or {}
            sale['contract_no'] = str(c.get('НомерДоговора') or c.get('Номер') or c.get('Description') or '')
            sale['contract_date'] = str(c.get('ДатаДоговора') or c.get('Дата') or '')[:10]
            if sale['contract_date'].startswith('0001'):
                sale['contract_date'] = ''
        except ODataError:
            pass
    return sale


def draftable(row: dict) -> str:
    """Bo'sh satr — qoralama qilish mumkin; aks holda sababi."""
    match = row.get('match') or {}
    if row.get('flow') != 'out' or row.get('reconcile') != 'onec_only':
        return 'Faqat EHF ro‘yxatida yo‘q 1C sotuvi uchun qoralama tayyorlanadi.'
    if match.get('entity') != SALE_ENTITY:
        return 'Qoralama faqat «Реализация товаров и услуг» hujjatidan tayyorlanadi.'
    if not match.get('posted'):
        return '1C hujjati o‘tkazilmagan (qoralama) — avval 1C da o‘tkazing.'
    return ''


async def prepare(row: dict, settings: dict, onec: OneCService, transport=None) -> dict:
    reason = draftable(row)
    if reason:
        raise DraftError(reason, 400)
    sale = await sale_details(onec, row['match'])
    async with DidoxDraftClient(settings, transport) as client:
        await client.login()
        own = party(str(settings['inn']), await client.get('v1/profile', 'profil'))
        if own and own.get('profile_tin') and own['profile_tin'] != str(settings['inn']):
            raise DraftError('Didox hisobi boshqa STIRga tegishli — to‘xtatildi.', 409)
        buyer = None
        if re.fullmatch(r'\d{9}|\d{14}', sale['inn']):
            try:
                buyer = party(sale['inn'], await client.get(f'v1/profile/{sale["inn"]}', 'xaridor'))
            except DraftError as e:
                if e.status == 401:
                    raise
        measures = measure_index(await client.get('v1/measures/all', 'o‘lchov birliklari'))
    body, problems, lines = build_body(sale, own, buyer, measures)
    return {'sale': {k: sale.get(k) for k in ('number', 'date', 'inn', 'counterparty', 'amount', 'contract_no', 'contract_date')},
            'buyer_name': (buyer or {}).get('name', ''), 'lines': lines, 'problems': problems,
            'ready': body is not None, 'body': body,
            'revision': hashlib.sha256(repr(body).encode()).hexdigest()[:16] if body else ''}


async def create(prepared: dict, settings: dict, transport=None) -> str:
    if not prepared.get('ready'):
        raise DraftError('Qoralamada to‘sqinliklar bor — yuborilmadi.', 400)
    async with DidoxDraftClient(settings, transport) as client:
        await client.login()
        return await client.create_draft(prepared['body'])
