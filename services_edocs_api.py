"""E-DOCS saytining ochiq mijozida ishlatiladigan login/parol autentifikatsiyasi.

Manba: doc.edocs.uz ochiq Login va asosiy JavaScript modullari.
Bu rasmiy hamkorlik API shartnomasi emas; jonli hisob bilan hali tasdiqlanmagan.
Token faqat bitta tekshiruv davomida xotirada turadi, hech qayerga qaytarilmaydi.
"""
from __future__ import annotations

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


async def check_connection(settings: dict, *, transport=None) -> dict:
    """Bir login urinishi va faqat profilni o'qish; hujjatlar o'zgartirilmaydi."""
    login, password = settings.get('edocs_login'), settings.get('edocs_password')
    if not login or not password:
        raise EdocsError('E-DOCS login va parolini firma sozlamalariga kiriting.')
    expected = str(settings.get('inn') or '')
    if not expected.isascii() or not expected.isdigit() or len(expected) not in (9, 14):
        raise EdocsError('Avval firma STIR/JShShIRini sozlamalarga kiriting.')
    try:
        # TLS tekshiruvi va muhitning mavjud ishonch sozlamalari saqlanadi.
        async with httpx.AsyncClient(base_url=API_BASE, timeout=25, follow_redirects=False,
                                     transport=transport) as client:
            login_response = await client.post('loginpassword', json={'login': login, 'password': password})
            token = _body(login_response).get('token')
            if not isinstance(token, str) or not token.strip():
                raise EdocsError('E-DOCS autentifikatsiya tokenini bermadi. Hisob tekshirilmadi.')
            profile = _body(await client.get('profile', headers={'Authorization': f'Bearer {token}'}))
            actual = str(profile.get('tin') or profile.get('personalNum') or '')
            if actual != expected:
                raise EdocsError('E-DOCS profilidagi firma STIRi mos emas. Hujjatlar olinmadi.')
    except httpx.HTTPError:
        # Xatolardagi so'rov tafsilotlari parol yoki tokenni oshkor qilmasin.
        raise EdocsError('api.edocs.uz xizmatiga ulanish bajarilmadi. Muhit tarmoq ruxsati va TLS ulanishini tekshiring.') from None
    return {'ok': True, 'method': 'edocs_api',
            'detail': 'E-DOCS login/paroli va firma STIRi tekshirildi. Fakturalarni olish hali tekshirilmadi.'}


def main() -> int:
    """Bulutdagi maxfiy bindinglar orqali qiymatlarni chiqarmasdan tekshirish."""
    import asyncio
    import os
    settings = {'edocs_login': os.getenv('EDOCS_LOGIN', ''),
                'edocs_password': os.getenv('EDOCS_PASSWORD', ''),
                'inn': os.getenv('EDOCS_INN', '')}
    try:
        result = asyncio.run(check_connection(settings))
    except EdocsError as error:
        print(str(error))
        return 1
    print(result['detail'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
