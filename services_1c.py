"""1C:Бухгалтерия для Узбекистана 3.0 — OData (standard.odata) integratsiyasi.

Asosiy tamoyillar ("Zero-Error"):
  1. Bog'langan obyektlar hech qachon matn sifatida yuborilmaydi — har doim Ref_Key orqali
     (Catalog_Контрагенты, Catalog_ДоговорыКонтрагентов, Catalog_БанковскиеСчета, Catalog_Номенклатура).
  2. Har bir yozuvdan OLDIN dublikat tekshiruvi: Raqam + Sana + Summa + Kontragent_Key.
  3. Yangi hujjat rekvizitlari (ВидОперации, schyot, shartnoma, ДДС moddasi) shu kontragent bo'yicha
     buxgalterning oxirgi 3–5 ta o'tkazilgan hujjatidan o'rganiladi.
  4. Hujjatlar faqat qoralama ("Posted": false) sifatida yaratiladi — o'tkazishni inson tasdiqlaydi.

Ikki transport bor: HttpOData (real 1C) va DemoOData (xotiradagi 1C nusxasi) — mantiq bitta.
Agar sizning konfiguratsiyangizda rekvizit nomlari boshqacha bo'lsa — FIELDS lug'atini tahrirlang
va /api/1c/metadata orqali $metadata ni tekshiring.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import time
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

EMPTY_GUID = "00000000-0000-0000-0000-000000000000"

E_BANK_IN = "Document_ПоступлениеНаРасчетныйСчет"
E_BANK_OUT = "Document_СписаниеСРасчетногоСчета"
E_PURCHASE = "Document_ПоступлениеТоваровУслуг"
E_COUNTERPARTY = "Catalog_Контрагенты"
E_CONTRACT = "Catalog_ДоговорыКонтрагентов"
E_BANK_ACCOUNT = "Catalog_БанковскиеСчета"
E_NOMENCLATURE = "Catalog_Номенклатура"
E_ORG = "Catalog_Организации"
E_CASH_FLOW = "Catalog_СтатьиДвиженияДенежныхСредств"
E_ACCOUNTS = "ChartOfAccounts_Хозрасчетный"
E_REGISTER = "AccountingRegister_Хозрасчетный"  # provodkalar (ixtiyoriy: nashr qilingan bo'lsa — schyot shundan)
E_REGISTER_ROWS = "AccountingRegister_Хозрасчетный_RecordType"
BANK_ACCOUNT_CODE = "51.10"
REQUIRED_ENTITIES = [E_BANK_IN, E_BANK_OUT, E_PURCHASE, E_COUNTERPARTY, E_CONTRACT, E_BANK_ACCOUNT,
                     E_NOMENCLATURE, E_ORG, E_ACCOUNTS]
# E_CASH_FLOW ixtiyoriy: ba'zi bazalarda ДДС moddalari ishlatilmaydi (Constant_ИспользоватьСтатьиДвиженияДенежныхСредств)
META_TTL = 1800  # $metadata (bulutli bazada ~8 MB) — 30 daqiqada bir marta o'qiladi

FIELDS = {
    "inn": "ИНН",
    "counterparty": "Контрагент_Key",  # O'zbekiston konfiguratsiyasida bank hujjatlarida kompozit: Контрагент + _Type
    "counterparty_composite": "Контрагент",
    "pinfl": "ПИНФЛ",       # YaTT (yakka tadbirkor) — 14 xonali JShShIR
    "ikpu": "КодИКПУ",      # nomenklatura MXIK kodi (konfiguratsiyada bo'lmasa — nomi bo'yicha qidiriladi)
    "ikpu_alt": "КодИзКлассификатора",  # BP UZ 3.0 (uzcloud): 17 xonali MXIK shu rekvizitda
    "contract": "ДоговорКонтрагента_Key",
    "in_number": "НомерВходящегоДокумента",
    "in_date": "ДатаВходящегоДокумента",
    "amount": "СуммаДокумента",
    "operation": "ВидОперации",
    "settlement_account": "СчетУчетаРасчетовСКонтрагентом_Key",
    "cash_flow_item": "СтатьяДвиженияДенежныхСредств_Key",
    "purpose": "НазначениеПлатежа",
    "payment_lines": "РасшифровкаПлатежа",
    "advance_account": "СчетУчетаРасчетовПоАвансам_Key",  # qarz bo'lmasa — avans schyoti (63.10 / 43.10)
    "repayment": "СпособПогашенияЗадолженности",          # "Автоматически": 1C o'zi qarz/avansga taqsimlaydi
}

# Tarixdagi hujjatdan ko'chiriladigan qo'shimcha rekvizitlar (konfiguratsiyada bo'lmasa — shunchaki o'tkazib yuboriladi)
ACCOUNT_BOUND_FIELDS = ("Налог_Key", "ВидНалоговогоОбязательства", "СуммаВключаетНДС", "СтатьяДоходов_Key",
                        "СтатьяРасходов_Key")  # faqat schyot tarixdagidek qolsa
COMMON_HEADER_FIELDS = ("ВалютаДокумента_Key", "СчетБанк_Key", "СчетУчетаДенежныхСредств_Key", "Ответственный_Key",
                        "Подразделение_Key", "ПодразделениеОрганизации_Key")  # tashkilot darajasida, har doim

# Hisob-kitob schyoti -> avans schyoti (O'zbekiston BHMS 21). 1C to'lovni o'tkazishda avval kontragentning qarzini
# (40.10 / 60.10) yopadi, ortgan summani avansga (63.10 — olingan, 43.10 — berilgan) o'tkazadi.
ADVANCE_FOR = {"40.10": "63.10", "60.10": "43.10"}
REPAYMENT_AUTO = "Автоматически"
# ВидОперации: RF konfiguratsiyasi nomi -> O'zbekiston BP 3.0 nomi (qaysi biri bazada ishlatilganiga qarab tanlanadi)
OPERATION_ALIASES = {"ОплатаОтПокупателя": "ОплатаПокупателя", "ВозвратДенежныхСредствПокупателю": "ВозвратПокупателю",
                     "ВозвратДенежныхСредствПоставщиком": "ВозвратОтПоставщика",
                     "ПрочиеРасчетыСКонтрагентами": "ПрочиеРасчетыСКонтрагентами"}

# Tarixda topilmasa — standart qoidalar (O'zbekiston BHMS 21 hisoblar rejasi)
from accounts_uz import UZ_ACCOUNTS  # noqa: E402 — to'liq hisoblar rejasi (BHMS 21)

ACCOUNT_CHOICES = list(UZ_ACCOUNTS)
OPERATION_BY_ACCOUNT = {code: (a[3], a[4]) for code, a in UZ_ACCOUNTS.items()}  # (chiqimda, kirimda)
# To'lov maqsadidagi kalit so'zlar (kontragent tarixi bo'lmasa, shu so'z bo'yicha 1C tarixidan qidiriladi)
# (kalit so'z, schyot, yo'nalish). "QQS bilan" kabi iboralar soliq to'lovi EMAS — shuning uchun aniq iboralar.
PURPOSE_KEYWORDS = [
    ("komissiya", "94.20", "chiqim"), ("комиссия", "94.20", "chiqim"), ("bank xizmat", "94.20", "chiqim"),
    ("за документ", "94.20", "chiqim"),
    ("soliq", "64.10", "chiqim"), ("налог", "64.10", "chiqim"), ("qqs bo'yicha", "64.10", "chiqim"),
    ("ндс по", "64.10", "chiqim"), ("jshds", "64.10", "chiqim"), ("byudjet", "64.10", "chiqim"),
    ("бюджет", "64.10", "chiqim"),
]


ACCOUNT_RE = re.compile(r"^\s*(\d{2})\.?(\d{2})((?:\.\d+)*)\s*$")


def fmt_account(code: str | None) -> str | None:
    """1C kodini UI ko'rinishiga: '6010' -> '60.10', '4530.1' (uch bo'g'inli subschyot) -> '45.30.1'."""
    if not code:
        return None
    m = ACCOUNT_RE.match(str(code))
    return f"{m.group(1)}.{m.group(2)}{m.group(3)}" if m else code


def norm_account(code: str) -> str:
    """UI ko'rinishidan 1C kodiga: '60.10' -> '6010', '45.30.1' -> '4530.1'."""
    m = ACCOUNT_RE.match(str(code))
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else str(code).strip()


# ---------------------------------------------------------------- filter qiymat turlari
@dataclass(frozen=True)
class Guid:
    value: str


@dataclass(frozen=True)
class DT:
    value: datetime


@dataclass(frozen=True)
class Cast:
    """Kompozit tipli rekvizit (masalan Catalog_БанковскиеСчета.Owner)."""
    guid: str
    entity: str


@dataclass(frozen=True)
class Cond:
    field: str
    op: str  # eq | ne | ge | le | gt | lt | substringof
    value: Any


def _render_value(v: Any) -> str:
    if isinstance(v, Guid):
        return f"guid'{v.value}'"
    if isinstance(v, DT):
        return f"datetime'{v.value.strftime('%Y-%m-%dT%H:%M:%S')}'"
    if isinstance(v, Cast):
        return f"cast(guid'{v.guid}', '{v.entity}')"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(round(float(v), 2)) if isinstance(v, float) else str(v)
    return "'" + str(v).replace("'", "''") + "'"


def encode_query(params: dict[str, str]) -> str:
    """OData so'rov qatori: bo'sh joy %20 bilan (httpx "+" qo'yadi — bulutli 1C (uzcloud) kirillcha rekvizitli
    $filter da "+" ni bo'sh joy deb o'qimaydi va xato qaytaradi)."""
    safe = "',()$"
    return "&".join(f"{k}={urllib.parse.quote(v, safe=safe)}" for k, v in params.items())


def render_filter(conds: list[Cond]) -> str:
    parts = []
    for c in conds:
        if c.op == "substringof":
            parts.append(f"substringof({_render_value(c.value)}, {c.field})")
        else:
            parts.append(f"{c.field} {c.op} {_render_value(c.value)}")
    return " and ".join(parts)


class ODataError(Exception):
    pass


# ---------------------------------------------------------------- bloklanish xotirasi
# 1C bloklangan foydalanuvchi bilan har yangi urinish blok muddatini uzaytiradi. Shuning uchun blok tugash vaqti
# (URL + login bo'yicha) faylda saqlanadi va shu vaqtgacha BOSHQA parol kiritilsa ham 1C ga so'rov yuborilmaydi.
BLOCK_FILE = Path(__file__).resolve().parent / "data" / "onec_blocks.json"
DEFAULT_BLOCK = timedelta(minutes=60)


def _block_key(base: str, user: str) -> str:
    return f"{base}|{(user or '').strip().lower()}"


def _load_blocks() -> dict:
    try:
        return json.loads(BLOCK_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def blocked_until(base: str, user: str) -> datetime | None:
    raw = _load_blocks().get(_block_key(base, user))
    try:
        until = datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None
    return until if until and until > datetime.now() else None


def remember_block(base: str, user: str, text: str) -> datetime:
    m = re.search(r"до\s+(\d{2}\.\d{2}\.\d{4})\s+(\d{1,2}:\d{2}(?::\d{2})?)", text)
    until = None
    if m:
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                until = datetime.strptime(f"{m.group(1)} {m.group(2)}", fmt)
                break
            except ValueError:
                continue
    until = (until or datetime.now() + DEFAULT_BLOCK) + timedelta(minutes=1)
    blocks = {k: v for k, v in _load_blocks().items() if v > datetime.now().isoformat()}
    blocks[_block_key(base, user)] = until.isoformat(timespec="seconds")
    BLOCK_FILE.parent.mkdir(exist_ok=True)
    BLOCK_FILE.write_text(json.dumps(blocks, ensure_ascii=False, indent=2), "utf-8")
    return until


# ---------------------------------------------------------------- transportlar
class HttpOData:
    mode = "live"

    def __init__(self, base_url: str, user: str, password: str, timeout: float = 30.0):
        base = base_url.rstrip("/")
        if not base.endswith("standard.odata"):
            base += "/odata/standard.odata"
        self.base = base
        self.client = httpx.AsyncClient(auth=(user, password) if user else None, timeout=timeout,
                                        headers={"Accept": "application/json"})
        # Login xato bo'lsa — sozlamalar o'zgarmaguncha 1C ga QAYTA urinilmaydi.
        # Aks holda har sahifa yangilanishi yangi xato urinish bo'lib, 1C foydalanuvchini bloklaydi.
        self.user = user
        self.auth_error: str | None = None
        until = blocked_until(self.base, user)
        if until:
            self.auth_error = (f"🔒 Bu 1C foydalanuvchisi {until:%d.%m.%Y %H:%M} gacha bloklangan. Har yangi urinish "
                               "blokni uzaytirgani uchun shu vaqtgacha 1C ga so'rov yuborilmaydi (parol o'zgarsa ham). "
                               "Blokni darhol olib tashlash uchun uzcloud qo'llab-quvvatlash xizmatiga murojaat qiling.")
        elif "#" in base_url or "/a/adm/" in base_url or "e1cib" in base_url:
            self.auth_error = ("1C manzili noto'g'ri: bu brauzerdagi sahifa (yoki boshqaruv bazasi /a/adm/), OData emas. "
                               "⚙️ Joriy firma → bazaning manzili, masalan https://1c.uzcloud.uz/a/bpuz/1234 "
                               "(1C ga so'rov yuborilmadi)")
        elif not user or not password:
            self.auth_error = ("1C login yoki parol kiritilmagan — ⚙️ Sozlamalarda 1C bazasidagi foydalanuvchi nomi "
                               "va parolini kiriting (1C ga so'rov yuborilmadi)")

    @staticmethod
    def _effective_status(r: httpx.Response) -> int:
        """Bulutli 1C (masalan uzcloud) nginx'i 401/404 ni /availability/ sahifasiga 301 bilan yo'naltiradi."""
        if not r.is_redirect:
            return r.status_code
        if r.headers.get("www-authenticate"):
            return 401
        m = re.search(r"[?&]code=(\d{3})", r.headers.get("location", ""))
        return int(m.group(1)) if m else r.status_code

    @staticmethod
    def _error_text(r: httpx.Response) -> str:
        """OData xatosi yoki 1C platforma istisnosi ({"exception": {"descr", "inner": ...}}) matnini olish."""
        try:
            data = json.loads(r.text.lstrip("\ufeff"))
        except ValueError:
            return r.text[:300]
        if isinstance(data, dict) and "odata.error" in data:
            return str(data["odata.error"].get("message", {}).get("value", ""))
        parts, e = [], (data.get("exception") if isinstance(data, dict) else None)
        while isinstance(e, dict):
            if e.get("descr"):
                parts.append(" ".join(str(e["descr"]).split()))
            e = e.get("inner")
        return " | ".join(parts) or r.text[:300]

    def _raise_for(self, r: httpx.Response, status: int) -> None:
        text = "" if r.is_redirect else self._error_text(r)
        low = text.lower()
        if "заблокирован" in low:
            blocked = next((p for p in text.split(" | ") if "заблокирован" in p.lower()), text)
            remember_block(self.base, self.user, blocked)
            self.auth_error = (f"🔒 1C foydalanuvchisi BLOKLANGAN (xato urinishlar ko'p): {blocked} — blok muddati "
                               "tugashini kuting yoki uzcloud qo'llab-quvvatlash xizmatidan blokni olib tashlashni "
                               "so'rang. Dastur sozlamalar o'zgarmaguncha 1C ga qayta urinmaydi.")
            raise ODataError(self.auth_error)
        if status in (401, 402, 403) or "пароль" in low or ("пользовател" in low and "неверно" in low):
            self.auth_error = ("1C: login yoki parol noto'g'ri, yoki foydalanuvchiga OData huquqi berilmagan. "
                               "Eslatma: uzcloud portali emas, 1C BAZASIDAGI foydalanuvchi nomi kerak. "
                               "Dastur sozlamalar o'zgarmaguncha 1C ga qayta urinmaydi (bloklanishdan himoya).")
            raise ODataError(self.auth_error)
        if status == 404:
            entity = urllib.parse.unquote(r.request.url.path).rstrip("/").rsplit("/", 1)[-1].split("(")[0]
            if entity.startswith(("Catalog_", "Document_", "ChartOfAccounts_", "AccumulationRegister_",
                                  "AccountingRegister_", "InformationRegister_")):
                raise ODataError(f"1C 404: «{entity}» OData tarkibida nashr qilinmagan — 1C da \"Настройка "
                                 "стандартного интерфейса OData\" orqali shu obyektni nashrga qo'shing")
            raise ODataError("1C 404: manzil topilmadi — baza manzilini yoki OData nashr qilinganini tekshiring")
        if r.is_redirect:
            raise ODataError(f"1C {status}: kutilmagan yo'naltirish → {r.headers.get('location', '')[:200]}")
        raise ODataError(f"1C OData xatosi {status}: {text[:500]}")

    async def _send(self, method: str, url: str, **kw) -> httpx.Response:
        if self.auth_error:
            raise ODataError(self.auth_error)
        try:
            r = await self.client.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise ODataError(f"1C serveriga ulanib bo'lmadi: {e}") from e
        status = self._effective_status(r)
        if status >= 300:
            self._raise_for(r, status)
        return r

    async def _request(self, method: str, path: str, **kw) -> Any:
        r = await self._send(method, f"{self.base}/{path}", **kw)
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            raise ODataError("1C JSON o'rniga boshqa javob qaytardi — manzil OData'ga "
                             "(…/odata/standard.odata) ishora qilayotganini tekshiring") from None

    async def query(self, entity: str, conds: list[Cond] | None = None, top: int | None = None,
                    orderby: str | None = None, select: list[str] | None = None, skip: int | None = None) -> list[dict]:
        params = {"$format": "json"}
        if conds:
            params["$filter"] = render_filter(conds)
        if top:
            params["$top"] = str(top)
        if skip:
            params["$skip"] = str(skip)
        if orderby:
            params["$orderby"] = orderby
        if select:
            params["$select"] = ",".join(select)
        data = await self._request("GET", entity + "?" + encode_query(params))
        return data.get("value", [])

    async def get(self, entity: str, key: str) -> dict | None:
        try:
            return await self._request("GET", f"{entity}(guid'{key}')", params={"$format": "json"})
        except ODataError as e:
            if "404" in str(e):
                return None
            raise

    async def create(self, entity: str, payload: dict) -> dict:
        return await self._request("POST", entity, params={"$format": "json"}, json=payload)

    async def patch(self, entity: str, key: str, payload: dict) -> dict:
        """Mavjud obyektni o'zgartirish — faqat dastur yaratgan qoralamani "o'chirishga belgilash" uchun."""
        return await self._request("PATCH", f"{entity}(guid'{key}')", params={"$format": "json"}, json=payload)

    async def ping(self) -> bool:
        await self._request("GET", "", params={"$format": "json"})
        return True

    async def metadata_text(self) -> str:
        return (await self._send("GET", f"{self.base}/$metadata")).text

    async def balance_rows(self, account_key: str, code: str, at: datetime) -> list[dict]:
        """Hisob qoldig'i subkonto bo'yicha (AccountingRegister_Хозрасчетный/Balance) — faqat o'qish.
        Condition parametri uzcloud da ishlamaydi, shuning uchun $filter."""
        path = urllib.parse.quote(f"{E_REGISTER}/Balance(Period=datetime'{at:%Y-%m-%dT%H:%M:%S}')", safe="(),='/")
        data = await self._request("GET", path + "?" + encode_query({"$format": "json",
                                                                    "$filter": f"Account_Key eq guid'{account_key}'"}))
        return data.get("value", [])

    async def register_records(self, entity: str, ref: str) -> list[dict]:
        """Hujjatning buxgalteriya provodkalari (AccountingRegister_Хозрасчетный, Recorder bo'yicha) — faqat o'qish."""
        path = f"{E_REGISTER}(Recorder=guid'{ref}', Recorder_Type='StandardODATA.{entity}')"
        data = await self._request("GET", urllib.parse.quote(path, safe="(),='") + "?$format=json")
        return data.get("RecordSet") or []


class OfflineOData:
    """1C ga ulanib bo'lmaganda — ko'rish rejimi: hech qanday so'rov yuborilmaydi, hech narsa topilmaydi."""
    mode = "offline"

    def __init__(self, reason: str = ""):
        self.reason = reason

    async def query(self, *a, **kw) -> list[dict]:
        return []

    async def get(self, *a, **kw) -> dict | None:
        return None

    async def create(self, *a, **kw) -> dict:
        raise ODataError("Ko'rish rejimi: 1C ga ulanmagan — yozib bo'lmaydi")

    async def patch(self, *a, **kw) -> dict:
        raise ODataError("Ko'rish rejimi: 1C ga ulanmagan — yozib bo'lmaydi")

    async def balance_rows(self, *a, **kw) -> list[dict]:
        return []

    async def ping(self) -> bool:
        raise ODataError(self.reason or "1C ga ulanmagan")

    async def metadata_text(self) -> str:
        return ""


class DemoOData:
    """Xotiradagi 1C bazasi — real OData filter semantikasini takrorlaydi."""
    mode = "demo"

    def __init__(self):
        from demo_data import seed_1c
        self.db = seed_1c()
        self._n = 900

    @staticmethod
    def _match(rec: dict, c: Cond) -> bool:
        cur = rec.get(c.field)
        v = c.value
        if c.op == "substringof":
            return str(v).lower() in str(cur or "").lower()
        if isinstance(v, Cast):
            v = v.guid
        elif isinstance(v, Guid):
            v = v.value
        elif isinstance(v, DT):
            v = v.value.strftime("%Y-%m-%dT%H:%M:%S")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if cur is None:
                return False
            cur, v = round(float(cur), 2), round(float(v), 2)
        ops = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b, "ge": lambda a, b: a >= b,
               "le": lambda a, b: a <= b, "gt": lambda a, b: a > b, "lt": lambda a, b: a < b}
        try:
            return ops[c.op](cur, v)
        except TypeError:
            return False

    async def query(self, entity, conds=None, top=None, orderby=None, select=None, skip=None) -> list[dict]:
        rows = [r for r in self.db.get(entity, []) if all(self._match(r, c) for c in conds or [])]
        if orderby:
            field, _, direction = orderby.partition(" ")
            rows.sort(key=lambda r: str(r.get(field, "")), reverse=direction.lower() == "desc")
        if skip:
            rows = rows[skip:]
        if top:
            rows = rows[:top]
        return copy.deepcopy(rows)

    async def get(self, entity, key) -> dict | None:
        for r in self.db.get(entity, []):
            if r.get("Ref_Key") == key:
                return copy.deepcopy(r)
        return None

    async def create(self, entity, payload) -> dict:
        import uuid
        self._n += 1
        rec = copy.deepcopy(payload)
        rec["Ref_Key"] = str(uuid.uuid4())
        rec["Number"] = f"NT00-{self._n:06d}"
        rec.setdefault("DeletionMark", False)
        self.db.setdefault(entity, []).append(rec)
        return copy.deepcopy(rec)

    async def patch(self, entity, key, payload) -> dict:
        for r in self.db.get(entity, []):
            if r.get("Ref_Key") == key:
                r.update(copy.deepcopy(payload))
                return copy.deepcopy(r)
        raise ODataError("Hujjat topilmadi")

    async def ping(self) -> bool:
        return True

    async def balance_rows(self, account_key: str, code: str, at: datetime) -> list[dict]:
        """Demo / fayl nusxasi: tayyor qoldiqlar (db["_balances"][schyot])."""
        return copy.deepcopy(self.db.get("_balances", {}).get(code, []))

    async def metadata_text(self) -> str:
        return " ".join(f'EntitySet Name="{e}"' for e in self.db if not e.startswith("_"))


# ---------------------------------------------------------------- yordamchilar
def _day(value: str | None) -> str:
    return (value or "")[:10]


def _same_amount(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.01
    except (TypeError, ValueError):
        return False


def _fmt_sum(v: Any) -> str:
    try:
        return f"{float(v):,.2f}".replace(",", " ")
    except (TypeError, ValueError):
        return str(v)


def _fmt_date(iso: str) -> str:
    d = _day(iso)
    return f"{d[8:10]}.{d[5:7]}.{d[:4]}" if len(d) == 10 else iso


def _is_empty_key(v: Any) -> bool:
    return not v or v == EMPTY_GUID


def _has_lines(doc: dict) -> bool:
    """"Расшифровка платежа" qatorida schyot ko'rsatilganmi (bo'lsa — schyot o'sha qatordan olinadi)."""
    lines = doc.get(FIELDS["payment_lines"]) or []
    return bool(lines) and not _is_empty_key((lines[0] or {}).get(FIELDS["settlement_account"]))


# ---------------------------------------------------------------- servis
class OneCService:
    def __init__(self, transport: HttpOData | DemoOData, org_inn: str = ""):
        self.t = transport
        self.org_inn = org_inn
        self._cache: dict[tuple, Any] = {}
        self._meta: tuple[float, dict] | None = None  # invalidate() da tozalanmaydi — katta fayl
        self._sem = asyncio.Semaphore(6)

    @property
    def mode(self) -> str:
        return self.t.mode

    async def _q(self, *a, **kw) -> list[dict]:
        async with self._sem:
            return await self.t.query(*a, **kw)

    async def _cached(self, key: tuple, factory):
        if key not in self._cache:
            self._cache[key] = await factory()
        return self._cache[key]

    def invalidate(self) -> None:
        self._cache.clear()

    # ---- holat
    async def status(self) -> dict:
        try:
            await self.t.ping()
            if self.mode == "live":
                meta = await self._cached(("metadata",), self.check_metadata)
                if meta["published"] == 0:
                    return {"ok": False, "mode": self.mode, "label": "1C OData: obyektlar nashr qilinmagan",
                            "error": "1C ga ulanildi (login to'g'ri), lekin OData tarkibida birorta ham obyekt "
                                     "nashr qilinmagan. " + meta["hint"]}
                if meta["missing"]:
                    return {"ok": True, "mode": self.mode, "label": f"1C OData ({len(meta['missing'])} obyekt yetishmaydi)",
                            "warning": "Nashr qilinmagan: " + ", ".join(meta["missing"]), "missing": meta["missing"]}
            label = {"demo": "1C OData (Demo baza)", "file": "1C eksport fayllari"}.get(self.mode, "1C OData")
            return {"ok": True, "mode": self.mode, "label": label}
        except ODataError as e:
            return {"ok": False, "mode": self.mode, "label": "1C OData", "error": str(e)}

    def drop_metadata(self) -> None:
        self._meta = None
        self._cache.pop(("metadata",), None)

    async def _metadata(self) -> dict:
        """$metadata: nashr qilingan obyektlar va har birining rekvizitlari (bir marta o'qib, xotirada)."""
        if self._meta and time.monotonic() - self._meta[0] < META_TTL:
            return self._meta[1]
        text = await self.t.metadata_text()
        meta = {"sets": set(re.findall(r'EntitySet Name="([^"]+)"', text)), "types": {}}
        if self.mode == "live" and text:
            import xml.etree.ElementTree as ET
            try:
                root = ET.fromstring(text)
            except ET.ParseError:
                root = None
            if root is not None:
                types = {el.get("Name"): el for el in root.iter() if el.tag.endswith(("EntityType", "ComplexType"))}
                for name, et in types.items():
                    header, tables = set(), {}
                    for prop in et:
                        if not prop.tag.endswith("Property"):
                            continue
                        pname, typ = prop.get("Name"), prop.get("Type") or ""
                        header.add(pname)
                        if typ.startswith("Collection("):
                            row_type = types.get(typ[len("Collection("):-1].split(".")[-1])
                            tables[pname] = {p.get("Name") for p in (row_type if row_type is not None else [])
                                             if p.tag.endswith("Property")}
                    meta["types"][name] = {"header": header, "tables": tables}
        if meta["sets"]:  # hali nashr qilinmagan bo'lsa — keyingi safar qayta o'qiladi
            self._meta = (time.monotonic(), meta)
        return meta

    async def check_metadata(self) -> dict:
        sets = (await self._metadata())["sets"]
        missing = [e for e in REQUIRED_ENTITIES if e not in sets]
        return {"ok": not missing, "missing": missing, "published": len(sets),
                "hint": ("1C da (administrator huquqi bilan): Администрирование → Общие настройки → Интеграция с "
                         "другими программами → \"Настройка стандартного интерфейса OData\" (yoki Все функции → "
                         "Обработки → \"Настройка стандартного интерфейса OData\") → kerakli obyektlarni belgilab "
                         "\"Сохранить\". Bulutli 1C da buni uzcloud qo'llab-quvvatlashi ham qila oladi.")
                if missing else ""}

    # ---- Ref_Key resolution
    async def organization(self) -> dict | None:
        async def f():
            conds = [Cond("DeletionMark", "eq", False)]
            if self.org_inn:
                conds.append(Cond(FIELDS["inn"], "eq", self.org_inn))
            rows = await self._q(E_ORG, conds, top=1)
            return rows[0] if rows else None
        return await self._cached(("org", self.org_inn), f)

    async def find_counterparty(self, inn: str) -> dict | None:
        inn = (inn or "").strip()
        if not inn:
            return None

        async def f():
            field = FIELDS["inn"]
            if len(inn) == 14:  # yakka tadbirkor — JShShIR (PINFL)
                props = await self.entity_props(E_COUNTERPARTY)
                if props is None or FIELDS["pinfl"] in props["header"]:
                    field = FIELDS["pinfl"]
            rows = await self._q(E_COUNTERPARTY, [Cond(field, "eq", inn), Cond("DeletionMark", "eq", False)],
                                 top=2, select=["Ref_Key", "Description", FIELDS["inn"]])
            if not rows and field != FIELDS["inn"]:
                rows = await self._q(E_COUNTERPARTY, [Cond(FIELDS["inn"], "eq", inn), Cond("DeletionMark", "eq", False)],
                                     top=2, select=["Ref_Key", "Description", FIELDS["inn"]])
            return rows[0] if rows else None
        return await self._cached(("kontr", inn), f)

    # ---- kontragent rekviziti: oddiy (Контрагент_Key) yoki kompozit (Контрагент + Контрагент_Type)
    async def counterparty_field(self, entity: str) -> str:
        props = await self.entity_props(entity)
        if props and FIELDS["counterparty"] not in props["header"] and FIELDS["counterparty_composite"] in props["header"]:
            return FIELDS["counterparty_composite"]
        return FIELDS["counterparty"]

    async def counterparty_cond(self, entity: str, kontr_key: str) -> Cond:
        field = await self.counterparty_field(entity)
        if field == FIELDS["counterparty_composite"]:
            return Cond(field, "eq", Cast(kontr_key, E_COUNTERPARTY))
        return Cond(field, "eq", Guid(kontr_key))

    async def counterparty_payload(self, entity: str, kontr_key: str) -> dict:
        field = await self.counterparty_field(entity)
        if field == FIELDS["counterparty_composite"]:
            return {field: kontr_key, f"{field}_Type": f"StandardODATA.{E_COUNTERPARTY}"}
        return {field: kontr_key}

    @staticmethod
    def counterparty_of(doc: dict) -> str | None:
        v = doc.get(FIELDS["counterparty"])
        if _is_empty_key(v) and str(doc.get(FIELDS["counterparty_composite"] + "_Type") or "").endswith(E_COUNTERPARTY):
            v = doc.get(FIELDS["counterparty_composite"])
        return None if _is_empty_key(v) else v

    async def find_counterparty_by_name(self, name: str) -> dict | None:
        """Faqat fayl rejimida: eksportda STIR bo'lmasa — nomi bo'yicha (tashkiliy shakl va qo'shtirnoqsiz)."""
        if self.mode != "file" or not name:
            return None
        from services_files1c import name_key
        key = name_key(name)
        if not key:
            return None
        rows = await self._q(E_COUNTERPARTY, [Cond("_name_key", "eq", key), Cond("DeletionMark", "eq", False)], top=2)
        return rows[0] if len(rows) == 1 else None

    async def find_contract(self, kontr_key: str, direction: str) -> dict | None:
        async def f():
            rows = await self._q(E_CONTRACT, [Cond("Owner_Key", "eq", Guid(kontr_key)),
                                              Cond("DeletionMark", "eq", False)], top=10)
            want = "СПоставщиком" if direction == "chiqim" else "СПокупателем"
            for r in rows:
                if r.get("ВидДоговора") == want:
                    return r
            return rows[0] if rows else None
        return await self._cached(("contract", kontr_key, direction), f)

    async def find_contract_by_ref(self, kontr_key: str, number: str, on_date: str | None = None) -> dict | None:
        """To'lov maqsadidagi shartnoma raqami bo'yicha aynan shu kontragentning shartnomasi.
        Raqam "Номер" rekvizitidan, bo'lmasa nomidan ("Shartnoma №398 …") qidiriladi; bir nechta bo'lsa — sanasi mosi."""
        def norm(v: str) -> str:
            return re.sub(r"[\s№#]|^n_?", "", str(v or "").lower()).lstrip("0") or "0"

        async def f():
            rows = await self._q(E_CONTRACT, [Cond("Owner_Key", "eq", Guid(kontr_key)),
                                              Cond("DeletionMark", "eq", False)], top=200)
            want = norm(number)
            hits = [r for r in rows if norm(r.get("Номер")) == want or
                    re.search(r"(?:№|N_?|\b)\s*0*" + re.escape(number.lstrip("0") or "0") + r"(?![\w/])",
                              str(r.get("Description") or ""), re.IGNORECASE)]
            if on_date:
                dated = [r for r in hits if str(r.get("Дата") or "")[:10] == on_date]
                hits = dated or hits
            return hits[0] if hits else None
        return await self._cached(("contract_ref", kontr_key, number, on_date), f)

    async def create_contract(self, kontr_key: str, number: str, on_date: str | None, direction: str) -> dict:
        """Yangi shartnoma (to'lov maqsadidagi raqam 1C da yo'q — yangi shartnoma bo'yicha birinchi to'lov)."""
        org = await self.organization()
        when = f" от {on_date[8:10]}.{on_date[5:7]}.{on_date[:4]}" if on_date else ""
        body = {"Owner_Key": kontr_key, "Description": f"№{number}{when}", "Номер": number,
                "Дата": f"{on_date}T00:00:00" if on_date else None,
                "ВидДоговора": "СПоставщиком" if direction == "chiqim" else "СПокупателем",
                "Организация_Key": org["Ref_Key"] if org else None, "DeletionMark": False,
                "Комментарий": "BUXGALTER AI: to'lov maqsadidagi shartnoma bo'yicha yaratildi"}
        body, _dropped = await self.filter_payload(E_CONTRACT, {k: v for k, v in body.items() if v is not None})
        created = await self.t.create(E_CONTRACT, body)
        self.invalidate()
        return created

    async def find_bank_account(self, owner_key: str, owner_entity: str, account_no: str | None) -> dict | None:
        async def f():
            conds = [Cond("Owner", "eq", Cast(owner_key, owner_entity)), Cond("DeletionMark", "eq", False)]
            if account_no:
                conds.append(Cond("НомерСчета", "eq", account_no))
            rows = await self._q(E_BANK_ACCOUNT, conds, top=1)
            return rows[0] if rows else None
        return await self._cached(("bankacc", owner_key, account_no), f)

    async def find_org_bank_account(self, account_no: str | None) -> dict | None:
        org = await self.organization()
        if not org:
            return None
        # Hisob raqami berilgan bo'lsa — FAQAT aniq mos hisob. Boshqa hisobga "jimgina" yozib yubormaslik uchun.
        return await self.find_bank_account(org["Ref_Key"], E_ORG, account_no or None)

    async def find_nomenclature(self, name: str, ikpu: str | None = None) -> dict | None:
        async def f():
            props = await self.entity_props(E_NOMENCLATURE)
            # MXIK kodi: КодИКПУ yoki (BP UZ 3.0) КодИзКлассификатора — konfiguratsiyada borini ishlatamiz
            fields = [FIELDS["ikpu"]] if props is None else \
                [f for f in (FIELDS["ikpu"], FIELDS["ikpu_alt"]) if f in props["header"]]
            for field in fields if ikpu else []:
                rows = await self._q(E_NOMENCLATURE, [Cond(field, "eq", ikpu), Cond("DeletionMark", "eq", False)], top=1)
                if rows:
                    return rows[0]
            rows = await self._q(E_NOMENCLATURE, [Cond("Description", "eq", name), Cond("DeletionMark", "eq", False)],
                                 top=1)
            return rows[0] if rows else None
        return await self._cached(("nom", name, ikpu), f)

    async def account_key(self, code: str) -> str | None:
        async def f():
            for c in dict.fromkeys((norm_account(code), fmt_account(norm_account(code)), code)):
                rows = await self._q(E_ACCOUNTS, [Cond("Code", "eq", c)], top=1)
                if rows:
                    return rows[0]["Ref_Key"]
            return None
        return await self._cached(("acc_by_code", norm_account(code)), f)

    async def account_code(self, key: str | None) -> str | None:
        if _is_empty_key(key):
            return None

        async def f():
            rec = await self.t.get(E_ACCOUNTS, key)
            return fmt_account(rec.get("Code")) if rec else None
        return await self._cached(("acc_by_key", key), f)

    async def ref_description(self, entity: str, key: str | None) -> str | None:
        if _is_empty_key(key):
            return None

        async def f():
            try:
                rec = await self.t.get(entity, key)
            except ODataError:  # obyekt nashr qilinmagan (masalan, ДДС moddalari) — nomi ko'rsatilmaydi, xolos
                return None
            return rec.get("Description") if rec else None
        return await self._cached(("descr", entity, key), f)

    # ---- Dublikat tekshiruvi
    async def check_duplicate(self, entity: str, number: str, doc_date: date, amount: float,
                              kontr_key: str | None, posted: date | None = None) -> dict:
        """Natija: status = duplicate | discrepancy | new (+ sabab, 1C dagi hujjat, farqlar).

        kind: exact  — raqam+sana+summa+kontragent to'liq mos (hech qachon qayta yozilmaydi)
              weak   — 1C dagi hujjatda kiruvchi raqam yo'q, sana+summa+kontragent mos (buxgalter tasdig'i bilan
                       "alohida to'lov" sifatida yozish mumkin)
              number — shu raqamli hujjat bor, lekin summa/sana farqli
              none   — mos hujjat yo'q"""
        if not kontr_key:
            # Kontragentsiz (masalan, bank komissiyasi: vipiskada firmaning o'zi) — kiruvchi raqam + sana + summa
            if number:
                for c in await self._q(entity, [Cond("DeletionMark", "eq", False), Cond(FIELDS["in_number"], "eq", number),
                                                Cond(FIELDS["amount"], "eq", float(amount))], top=20):
                    if _day(c.get(FIELDS["in_date"])) == doc_date.isoformat():
                        return {"status": "duplicate", "kind": "exact",
                                "reason": "Raqam + Sana + Summa mos keldi (kontragent 1C da topilmagan)",
                                "match": await self._brief(entity, c), "diffs": []}
            return {"status": "new", "kind": "none", "reason": "Kontragent 1C da topilmadi — dublikat raqam, sana va summa "
                    "bo'yicha tekshirildi, mos hujjat yo'q", "match": None, "diffs": []}
        base = [Cond("DeletionMark", "eq", False), await self.counterparty_cond(entity, kontr_key)]
        target_day = doc_date.isoformat()

        if number:
            cands = await self._q(entity, base + [Cond(FIELDS["in_number"], "eq", number)], top=20)
            # raqamlar yil boshida qayta boshlanadi — faqat ±31 kun ichidagilarni solishtiramiz
            near = [c for c in cands if _day(c.get(FIELDS["in_date"])) and
                    abs((date.fromisoformat(_day(c.get(FIELDS["in_date"]))) - doc_date).days) <= 31]
            for c in near:
                if _day(c.get(FIELDS["in_date"])) == target_day and _same_amount(c.get(FIELDS["amount"]), amount):
                    return {"status": "duplicate", "kind": "exact", "reason": "Raqam + Sana + Summa + Kontragent to'liq mos keldi",
                            "match": await self._brief(entity, c), "diffs": []}
            if near:
                c = near[0]
                diffs = []
                if not _same_amount(c.get(FIELDS["amount"]), amount):
                    diffs.append(f"Summa: faylda {_fmt_sum(amount)}, 1C da {_fmt_sum(c.get(FIELDS['amount']))}")
                if _day(c.get(FIELDS["in_date"])) != target_day:
                    diffs.append(f"Sana: faylda {_fmt_date(target_day)}, 1C da {_fmt_date(c.get(FIELDS['in_date']))}")
                return {"status": "discrepancy", "kind": "number", "reason": "Shu raqamli hujjat 1C da bor, lekin rekvizitlar farq qiladi",
                        "match": await self._brief(entity, c), "diffs": diffs}

        # Raqamsiz kiritilgan bo'lishi mumkin: Sana + Summa + Kontragent
        start = datetime.combine(posted or doc_date, datetime.min.time())  # 1C hujjat sanasi = bank o'tkazgan kun
        same_day = await self._q(entity, base + [
            Cond(FIELDS["amount"], "eq", float(amount)),
            Cond("Date", "ge", DT(start)), Cond("Date", "le", DT(start + timedelta(days=1, seconds=-1)))], top=5)
        if same_day:
            return {"status": "duplicate", "kind": "weak", "reason": "Sana + Summa + Kontragent mos keldi (1C da kiruvchi raqamsiz)",
                    "match": await self._brief(entity, same_day[0]), "diffs": []}
        # Boshqa kontragentga yozilgan bo'lishi mumkin (masalan, soliq: vipiskada G'aznachilik, 1C da tuman DSI):
        # kiruvchi raqam + sana + summa mos — kontragentdan qat'i nazar "1C da bor" (ikki marta kiritilmasin)
        if number:
            for c in await self._q(entity, [Cond("DeletionMark", "eq", False), Cond(FIELDS["in_number"], "eq", number),
                                            Cond(FIELDS["amount"], "eq", float(amount))], top=20):
                if _day(c.get(FIELDS["in_date"])) == target_day or _day(c.get("Date")) == (posted or doc_date).isoformat():
                    name = await self.ref_description(E_COUNTERPARTY, self.counterparty_of(c)) or "—"
                    return {"status": "duplicate", "kind": "other_cp",
                            "reason": f"Raqam + Sana + Summa mos keldi, lekin 1C da boshqa kontragentga yozilgan: {name}",
                            "match": await self._brief(entity, c), "diffs": [f"Kontragent: 1C da {name}"]}
        return {"status": "new", "kind": "none", "reason": "1C da mos hujjat topilmadi", "match": None, "diffs": []}

    @staticmethod
    def _doc_brief(d: dict) -> dict:
        return {"ref": d.get("Ref_Key"), "number": d.get("Number"), "date": _day(d.get("Date")),
                "in_number": d.get(FIELDS["in_number"]), "in_date": _day(d.get(FIELDS["in_date"])),
                "amount": d.get(FIELDS["amount"]), "posted": d.get("Posted"), "operation": d.get(FIELDS["operation"]),
                "account_key": OneCService._extract_pattern(d)["account_key"]}

    async def _brief(self, entity: str, d: dict) -> dict:
        brief = self._doc_brief(d)
        if not _has_lines(d) and (posted := await self.posted_accounts(entity, d)):
            brief["account_key"] = posted[0]
        return brief

    # ---- provodkalar: qatorsiz hujjatlarda (komissiya, soliq) sarlavhadagi schyot standart qiymatda qoladi
    async def _register_on(self) -> bool:
        if self.mode != "live":
            return False
        try:
            return E_REGISTER in (await self._metadata())["sets"]
        except ODataError:
            return False

    async def posted_accounts(self, entity: str, doc: dict) -> list[str]:
        """Bank hujjatining 51.10 ga qarshi schyot(lar)i 1C provodkasidan (katta summa birinchi). Bo'lmasa — []."""
        ref = doc.get("Ref_Key")
        if not ref or entity not in (E_BANK_IN, E_BANK_OUT) or not await self._register_on():
            return []

        async def f():
            bank = await self.account_key(BANK_ACCOUNT_CODE)
            try:
                records = await self.t.register_records(entity, ref)
            except ODataError:
                return []
            parts: Counter = Counter()
            for r in records:
                dr, cr = r.get("AccountDr_Key"), r.get("AccountCr_Key")
                corr = dr if (entity == E_BANK_OUT and cr == bank) else cr if (entity == E_BANK_IN and dr == bank) else None
                if not _is_empty_key(corr):
                    parts[corr] += float(r.get("Сумма") or 0)
            return [k for k, _v in parts.most_common()]
        return await self._cached(("posted", ref), f)

    async def counterparty_docs(self, entity: str, kontr_key: str, top: int = 100) -> list[dict]:
        """Kontragentning oxirgi o'tkazilgan hujjatlari (keshlangan) — soliq kodi bo'yicha o'rganish uchun."""
        async def f():
            return await self._q(entity, [await self.counterparty_cond(entity, kontr_key), Cond("Posted", "eq", True),
                                          Cond("DeletionMark", "eq", False)], top=top, orderby="Date desc")
        return await self._cached(("cp_docs", entity, kontr_key, top), f)

    async def bank_postings(self, entity: str, since: datetime) -> dict[str, str]:
        """Ommaviy: davr ichidagi barcha bank hujjatlari -> asosiy qarshi schyot (bitta sahifalangan so'rov)."""
        if entity not in (E_BANK_IN, E_BANK_OUT) or not await self._register_on():
            return {}
        return await self._cached(("bank_postings", entity, since.date().isoformat()),
                                  lambda: self._bank_postings(entity, since))

    async def _bank_postings(self, entity: str, since: datetime) -> dict[str, str]:
        bank = await self.account_key(BANK_ACCOUNT_CODE)
        if not bank:
            return {}
        side, other = ("AccountCr_Key", "AccountDr_Key") if entity == E_BANK_OUT else ("AccountDr_Key", "AccountCr_Key")
        sums: dict[str, Counter] = {}
        skip = 0
        while skip < 50000:
            page = await self._q(E_REGISTER_ROWS, [Cond(side, "eq", Guid(bank)), Cond("Period", "ge", DT(since))],
                                 top=1000, skip=skip or None, orderby="Period,Recorder,LineNumber",
                                 select=["Recorder", "Recorder_Type", other, "Сумма"])
            for r in page:
                if str(r.get("Recorder_Type") or "").endswith(entity) and not _is_empty_key(r.get(other)):
                    sums.setdefault(r["Recorder"], Counter())[r[other]] += float(r.get("Сумма") or 0)
            if len(page) < 1000:
                break
            skip += 1000
        return {ref: c.most_common(1)[0][0] for ref, c in sums.items()}

    # ---- qoldiqlar, aylanma, nomlar (Bosh sahifa va Sklad uchun; faqat o'qish)
    async def account_balance(self, code: str, at: datetime | None = None) -> list[dict]:
        """Schyot qoldig'i subkonto bo'yicha: [{"dim": ref, "dim_type": "Catalog_…", "sum": Dt−Kt, "qty": miqdor}]."""
        at = at or datetime.combine(date.today(), datetime.max.time()).replace(microsecond=0)
        key = await self.account_key(code) if self.mode == "live" else None
        if self.mode == "live" and not (key and await self._register_on()):
            return []

        async def f():
            rows = await self.t.balance_rows(key or "", code, at)
            out = []
            for r in rows:
                out.append({"dim": r.get("ExtDimension1"), "dim_type": str(r.get("ExtDimension1_Type") or "").split(".")[-1],
                            "sum": round(float(r.get("СуммаBalance") or 0), 2),
                            "qty": round(float(r.get("КоличествоBalance") or 0), 3)})
            return out
        return await self._cached(("balance", code, at.isoformat()), f)

    async def fetch_all(self, entity: str, conds: list[Cond] | None = None, select: list[str] | None = None,
                        orderby: str | None = None, limit: int = 20000, page: int = 500) -> list[dict]:
        """Sahifalab o'qish — tartib barqaror bo'lishi shart (bulutli 1C $orderby siz sahifalarni takrorlaydi)."""
        order = f"{orderby},Ref_Key" if orderby else "Ref_Key"
        out: list[dict] = []
        while len(out) < limit:
            chunk = await self._q(entity, conds or [], top=page, skip=len(out) or None, orderby=order, select=select)
            out.extend(chunk)
            if len(chunk) < page:
                break
        return out[:limit]

    async def bank_flows(self, since: date, until: date | None = None) -> list[dict]:
        """O'tkazilgan bank hujjatlari bo'yicha kunlik kirim/chiqim: [{"date", "kirim", "chiqim"}]."""
        until = until or date.today()
        days: dict[str, dict] = {}
        for entity, side in ((E_BANK_IN, "kirim"), (E_BANK_OUT, "chiqim")):
            docs = await self.fetch_all(entity, [Cond("Posted", "eq", True), Cond("DeletionMark", "eq", False),
                                                 Cond("Date", "ge", DT(datetime.combine(since, datetime.min.time()))),
                                                 Cond("Date", "le", DT(datetime.combine(until, datetime.max.time()).replace(microsecond=0)))],
                                        select=["Ref_Key", "Date", FIELDS["amount"]])
            for d in docs:
                day = _day(d.get("Date"))
                rec = days.setdefault(day, {"date": day, "kirim": 0.0, "chiqim": 0.0})
                rec[side] = round(rec[side] + float(d.get(FIELDS["amount"]) or 0), 2)
        return sorted(days.values(), key=lambda x: x["date"])

    async def known_operations(self, entity: str) -> set[str]:
        """Shu bazada haqiqatan ishlatilgan ВидОперации qiymatlari (konfiguratsiyalar orasida nomlar farq qiladi:
        RF — ОплатаОтПокупателя, O'zbekiston BP 3.0 — ОплатаПокупателя)."""
        async def f():
            try:
                docs = await self.fetch_all(entity, [Cond("DeletionMark", "eq", False)],
                                            select=["Ref_Key", FIELDS["operation"]], limit=3000)
            except ODataError:
                return set()
            return {d.get(FIELDS["operation"]) for d in docs if d.get(FIELDS["operation"])}
        return await self._cached(("operations", entity), f)

    async def valid_operation(self, entity: str, op: str | None, special: str | None = None,
                              learned: str | None = None) -> str | None:
        """ВидОперации ni shu baza qabul qiladigan qiymatga keltirish (1C noma'lum qiymatli hujjatni rad etadi)."""
        known = await self.known_operations(entity)
        if not known or (op in known and not (special == "bank_fee" and "КомиссияБанка" in known)):
            return op
        if special == "bank_fee" and "КомиссияБанка" in known:
            return "КомиссияБанка"
        alias = OPERATION_ALIASES.get(op or "")
        if alias in known:
            return alias
        if learned and OPERATION_ALIASES.get(learned) == op:  # tarixdagi nom shu operatsiyaning boshqa yozilishi
            return learned
        return op  # boshqa operatsiyaga almashtirilmaydi (masalan, ish haqi "прочее" ga aylanmasin)

    async def fee_counterparty(self) -> dict | None:
        """Bank komissiyasi uchun 1C da buxgalter ishlatgan kontragent (oxirgi "КомиссияБанка" hujjatidan) —
        u o'chirishga belgilangan bo'lsa ham (vipiskada kontragent — firmaning o'zi)."""
        async def f():
            if "КомиссияБанка" not in await self.known_operations(E_BANK_OUT):
                return None
            docs = await self._q(E_BANK_OUT, [Cond("Posted", "eq", True), Cond(FIELDS["operation"], "eq", "КомиссияБанка")],
                                 top=1, orderby="Date desc")
            key = self.counterparty_of(docs[0]) if docs else None
            return await self.t.get(E_COUNTERPARTY, key) if key else None
        return await self._cached(("fee_counterparty",), f)

    async def last_bank_date(self) -> date | None:
        """1C ga oxirgi kiritilgan (o'tkazilgan) bank hujjati sanasi — 51.10 qoldig'i shu kungacha to'liq."""
        async def f():
            days = []
            for entity in (E_BANK_IN, E_BANK_OUT):
                rows = await self._q(entity, [Cond("Posted", "eq", True), Cond("DeletionMark", "eq", False)],
                                     top=1, orderby="Date desc", select=["Ref_Key", "Date"])
                days += [_day(r.get("Date")) for r in rows if r.get("Date")]
            return date.fromisoformat(max(days)) if days else None
        return await self._cached(("last_bank_date",), f)

    async def describe(self, entity: str, keys: list[str], fields: list[str] | None = None) -> dict[str, dict]:
        """Bir nechta obyektning nomi (va qo'shimcha rekvizitlari) — keshlangan. Ko'p bo'lsa (jonli 1C) — butun
        katalog bitta sahifalangan so'rov bilan (yuzlab alohida GET o'rniga)."""
        uniq = [k for k in dict.fromkeys(keys) if not _is_empty_key(k)]
        if self.mode == "live" and len(uniq) > 15:
            props = await self.entity_props(entity)
            cols = ["Ref_Key", "Description", *[f for f in fields or [] if not props or f in props["header"]]]

            async def whole():
                return {r["Ref_Key"]: r for r in await self.fetch_all(entity, select=cols, limit=50000)}
            catalog = await self._cached(("catalog", entity, tuple(cols)), whole)
            return {k: {x: (catalog.get(k) or {}).get(x) for x in ["Description", *(fields or [])]} for k in uniq}
        return await self._describe_each(entity, uniq, fields)

    async def _describe_each(self, entity: str, keys: list[str], fields: list[str] | None = None) -> dict[str, dict]:
        async def one(k):
            async def f():
                try:
                    rec = await self.t.get(entity, k)
                except ODataError:
                    rec = None
                return {x: (rec or {}).get(x) for x in ["Description", *(fields or [])]}
            return k, await self._cached(("describe", entity, k), f)
        return dict(await asyncio.gather(*(one(k) for k in keys)))

    # ---- Tarixdan o'rganish
    @staticmethod
    def _extract_pattern(doc: dict) -> dict:
        """Hujjatdan rekvizitlar. "Расшифровка платежа" qatori bo'lsa — schyot/shartnoma/avans o'sha qatordan:
        O'zbekiston konfiguratsiyasida sarlavhadagi СчетУчетаРасчетовСКонтрагентом standart qiymatda qoladi
        (masalan 42.20), provodka esa qatordagi schyot bo'yicha tuziladi. Qatorsiz hujjatda (soliq) — sarlavha."""
        line = (doc.get(FIELDS["payment_lines"]) or [{}])[0] if doc.get(FIELDS["payment_lines"]) else {}

        def pick(field):
            v = line.get(field)
            return doc.get(field) if _is_empty_key(v) else v
        acc = pick(FIELDS["settlement_account"])
        cfi = pick(FIELDS["cash_flow_item"])
        contract = pick(FIELDS["contract"])
        extra = {k: doc[k] for k in ACCOUNT_BOUND_FIELDS if k in doc and not _is_empty_key(doc[k])}
        goods = doc.get("Товары") or doc.get("Услуги") or []
        if goods and goods[0].get("СтавкаНДС"):
            extra["СтавкаНДС"] = goods[0]["СтавкаНДС"]
        if goods:  # xarid: tovar/xarajat schyoti va QQS schyoti (Dt 29.10 + Dt 44.10 — Kt 60.10)
            item_acc = goods[0].get("СчетУчета_Key") or goods[0].get("СчетЗатрат_Key")
            if not _is_empty_key(item_acc):
                extra["item_account_key"] = item_acc
            if not _is_empty_key(goods[0].get("СчетУчетаНДС_Key")):
                extra["vat_account_key"] = goods[0]["СчетУчетаНДС_Key"]
        adv = pick(FIELDS["advance_account"])
        repayment = line.get(FIELDS["repayment"]) or doc.get(FIELDS["repayment"]) or ""
        return {"operation": doc.get(FIELDS["operation"]), "account_key": None if _is_empty_key(acc) else acc,
                "contract_key": None if _is_empty_key(contract) else contract,
                "cash_flow_key": None if _is_empty_key(cfi) else cfi, "extra": extra,
                "advance_key": None if _is_empty_key(adv) else adv, "repayment": repayment}

    async def learn_history(self, entity: str, kontr_key: str | None, purpose: str = "", top: int = 5,
                            direction: str = "") -> dict | None:
        """Shu kontragent (yoki to'lov maqsadi kalit so'zi) bo'yicha oxirgi o'tkazilgan hujjatlardan rekvizitlar."""
        docs, basis = [], ""
        if kontr_key:
            docs = await self._q(entity, [await self.counterparty_cond(entity, kontr_key),
                                          Cond("Posted", "eq", True), Cond("DeletionMark", "eq", False)],
                                 top=top, orderby="Date desc")
            basis = "kontragent"
        if not docs and purpose and entity in (E_BANK_IN, E_BANK_OUT):
            low = purpose.lower()
            for kw, _acc, kw_dir in PURPOSE_KEYWORDS:
                if kw in low and (not direction or kw_dir == direction):
                    docs = await self._q(entity, [Cond(FIELDS["purpose"], "substringof", kw),
                                                  Cond("Posted", "eq", True), Cond("DeletionMark", "eq", False)],
                                         top=top, orderby="Date desc")
                    if docs:
                        basis = f"to'lov maqsadi (\"{kw}\")"
                        break
        if not docs:
            return None
        patterns = [self._extract_pattern(d) for d in docs]
        for d, p in zip(docs, patterns):  # qatorsiz hujjat: schyot — 1C provodkasidan
            if not _has_lines(d) and (posted := await self.posted_accounts(entity, d)):
                p["account_key"] = posted[0]
        votes = Counter((p["operation"], p["account_key"]) for p in patterns)
        (op, acc_key), hits = votes.most_common(1)[0]
        best = next(p for p in patterns if (p["operation"], p["account_key"]) == (op, acc_key))
        code = await self.account_code(acc_key)
        adv_keys = Counter(p["advance_key"] for p in patterns
                           if p["advance_key"] and (p["operation"], p["account_key"]) == (op, acc_key))
        adv_code = await self.account_code(adv_keys.most_common(1)[0][0]) if adv_keys else None
        samples = [{"number": d.get(FIELDS["in_number"]) or d.get("Number"), "date": _day(d.get("Date")),
                    "amount": d.get(FIELDS["amount"]), "operation": p["operation"]} for d, p in zip(docs, patterns)]
        return {
            "advance_key": adv_keys.most_common(1)[0][0] if adv_keys else None, "advance_code": adv_code,
            "repayment": best["repayment"],
            "source": "history", "basis": basis, "operation": op, "account_key": acc_key, "account_code": code,
            "contract_key": best["contract_key"] if basis == "kontragent" else None,
            "cash_flow_key": best["cash_flow_key"], "extra": best["extra"],
            "confidence": f"{hits}/{len(docs)}", "samples": samples,
            "badge": f"🧠 Oldingi 1C tarixidan o'rganildi ({code})" if code else "🧠 Oldingi 1C tarixidan o'rganildi",
        }

    async def common_fields(self, entity: str) -> dict:
        """Shu turdagi eng so'nggi o'tkazilgan hujjatdan tashkilot darajasidagi rekvizitlar (valyuta, pul schyoti,
        mas'ul shaxs, bo'linma) — qoralama 1C da kamroq qo'lda to'ldirilishi uchun."""
        async def f():
            docs = await self._q(entity, [Cond("Posted", "eq", True), Cond("DeletionMark", "eq", False)],
                                 top=1, orderby="Date desc")
            doc = docs[0] if docs else {}
            return {k: doc[k] for k in COMMON_HEADER_FIELDS if k in doc and not _is_empty_key(doc[k])}
        return dict(await self._cached(("common", entity), f))

    async def entity_props(self, entity: str) -> dict | None:
        """$metadata bo'yicha hujjat rekvizitlari: {"header": {...}, "tables": {jadval: {...}}}.
        Demo yoki $metadata o'qilmasa — None (filtrlanmaydi)."""
        if self.mode != "live":
            return None
        try:
            return (await self._metadata())["types"].get(entity)
        except ODataError:
            return None

    async def filter_payload(self, entity: str, payload: dict) -> tuple[dict, list[str]]:
        """Konfiguratsiyada yo'q rekvizitlarni olib tashlash (aks holda 1C butun hujjatni rad etadi)."""
        props = await self.entity_props(entity)
        if not props:
            return payload, []
        out, dropped = {}, []
        for k, v in payload.items():
            if k not in props["header"]:
                dropped.append(k)
            elif isinstance(v, list) and k in props["tables"]:
                cols = props["tables"][k]
                rows = []
                for row in v:
                    dropped += [f"{k}.{c}" for c in row if c not in cols]
                    rows.append({c: x for c, x in row.items() if c in cols})
                out[k] = rows
            else:
                out[k] = v
        return out, sorted(set(dropped))

    # ---- Qoralama yaratish
    async def create_draft(self, entity: str, payload: dict) -> dict:
        body = {k: v for k, v in payload.items() if v is not None}
        body, _dropped = await self.filter_payload(entity, body)
        body["Posted"] = False  # HECH QACHON avtomatik o'tkazilmaydi
        body["DeletionMark"] = False
        created = await self.t.create(entity, body)
        self.invalidate()
        return {"ref": created.get("Ref_Key"), "number": created.get("Number"), "posted": created.get("Posted", False)}


def build_service(settings: dict) -> OneCService:
    if settings.get("onec_demo_mode") or not settings.get("onec_base_url"):
        return OneCService(DemoOData(), "")
    return OneCService(HttpOData(settings["onec_base_url"], settings.get("onec_user", ""),
                                 settings.get("onec_password", "")), settings.get("onec_org_inn", ""))
