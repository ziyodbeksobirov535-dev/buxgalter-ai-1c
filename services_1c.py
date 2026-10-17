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
REQUIRED_ENTITIES = [E_BANK_IN, E_BANK_OUT, E_PURCHASE, E_COUNTERPARTY, E_CONTRACT, E_BANK_ACCOUNT,
                     E_NOMENCLATURE, E_ORG, E_CASH_FLOW, E_ACCOUNTS]

FIELDS = {
    "inn": "ИНН",
    "counterparty": "Контрагент_Key",
    "contract": "ДоговорКонтрагента_Key",
    "in_number": "НомерВходящегоДокумента",
    "in_date": "ДатаВходящегоДокумента",
    "amount": "СуммаДокумента",
    "operation": "ВидОперации",
    "settlement_account": "СчетУчетаРасчетовСКонтрагентом_Key",
    "cash_flow_item": "СтатьяДвиженияДенежныхСредств_Key",
    "purpose": "НазначениеПлатежа",
    "payment_lines": "РасшифровкаПлатежа",
}

# Tarixdagi hujjatdan ko'chiriladigan qo'shimcha rekvizitlar (konfiguratsiyada bo'lmasa — shunchaki o'tkazib yuboriladi)
ACCOUNT_BOUND_FIELDS = ("Налог_Key", "ВидНалоговогоОбязательства", "СуммаВключаетНДС", "СтатьяДоходов_Key",
                        "СтатьяРасходов_Key")  # faqat schyot tarixdagidek qolsa
COMMON_HEADER_FIELDS = ("ВалютаДокумента_Key", "СчетБанк_Key", "СчетУчетаДенежныхСредств_Key", "Ответственный_Key",
                        "Подразделение_Key", "ПодразделениеОрганизации_Key")  # tashkilot darajasida, har doim

# Tarixda topilmasa — standart qoidalar (O'zbekiston BHMS 21 hisoblar rejasi)
ACCOUNT_CHOICES = ["60.10", "40.10", "64.10", "94.20"]
OPERATION_BY_ACCOUNT = {
    # schyot: (chiqim uchun ВидОперации, kirim uchun ВидОперации)
    "60.10": ("ОплатаПоставщику", "ВозвратДенежныхСредствПоставщиком"),
    "40.10": ("ВозвратДенежныхСредствПокупателю", "ОплатаОтПокупателя"),
    "64.10": ("ПеречислениеНалога", "ПрочееПоступление"),
    "94.20": ("ПрочееСписание", "ПрочееПоступление"),
}
# To'lov maqsadidagi kalit so'zlar (kontragent tarixi bo'lmasa, shu so'z bo'yicha 1C tarixidan qidiriladi)
# (kalit so'z, schyot, yo'nalish). "QQS bilan" kabi iboralar soliq to'lovi EMAS — shuning uchun aniq iboralar.
PURPOSE_KEYWORDS = [
    ("komissiya", "94.20", "chiqim"), ("комиссия", "94.20", "chiqim"), ("bank xizmat", "94.20", "chiqim"),
    ("soliq", "64.10", "chiqim"), ("налог", "64.10", "chiqim"), ("qqs bo'yicha", "64.10", "chiqim"),
    ("ндс по", "64.10", "chiqim"), ("jshds", "64.10", "chiqim"), ("byudjet", "64.10", "chiqim"),
    ("бюджет", "64.10", "chiqim"),
]


def fmt_account(code: str | None) -> str | None:
    """'6010' -> '60.10' (1C kodini UI ko'rinishiga)."""
    if not code:
        return None
    c = code.replace(".", "").strip()
    return f"{c[:2]}.{c[2:]}" if len(c) == 4 and c.isdigit() else code


def norm_account(code: str) -> str:
    return code.replace(".", "").strip()


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
        data = await self._request("GET", entity, params=params)
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

    async def ping(self) -> bool:
        await self._request("GET", "", params={"$format": "json"})
        return True

    async def metadata_text(self) -> str:
        return (await self._send("GET", f"{self.base}/$metadata")).text


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

    async def ping(self) -> bool:
        return True

    async def metadata_text(self) -> str:
        return " ".join(f'EntitySet Name="{e}"' for e in self.db)


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


# ---------------------------------------------------------------- servis
class OneCService:
    def __init__(self, transport: HttpOData | DemoOData, org_inn: str = ""):
        self.t = transport
        self.org_inn = org_inn
        self._cache: dict[tuple, Any] = {}
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
            return {"ok": True, "mode": self.mode,
                    "label": "1C OData (Demo baza)" if self.mode == "demo" else "1C OData"}
        except ODataError as e:
            return {"ok": False, "mode": self.mode, "label": "1C OData", "error": str(e)}

    async def check_metadata(self) -> dict:
        text = await self.t.metadata_text()
        missing = [e for e in REQUIRED_ENTITIES if f'"{e}"' not in text]
        return {"ok": not missing, "missing": missing,
                "hint": "1C Konfigurator → Администрирование → Публикация на веб-сервере / "
                        "\"Настройка стандартного интерфейса OData\" orqali yetishmayotgan obyektlarni yoqing."
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
            rows = await self._q(E_COUNTERPARTY, [Cond(FIELDS["inn"], "eq", inn), Cond("DeletionMark", "eq", False)],
                                 top=2, select=["Ref_Key", "Description", FIELDS["inn"]])
            return rows[0] if rows else None
        return await self._cached(("kontr", inn), f)

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
            if ikpu:
                rows = await self._q(E_NOMENCLATURE, [Cond("КодИКПУ", "eq", ikpu), Cond("DeletionMark", "eq", False)],
                                     top=1)
                if rows:
                    return rows[0]
            rows = await self._q(E_NOMENCLATURE, [Cond("Description", "eq", name), Cond("DeletionMark", "eq", False)],
                                 top=1)
            return rows[0] if rows else None
        return await self._cached(("nom", name, ikpu), f)

    async def account_key(self, code: str) -> str | None:
        async def f():
            for c in (norm_account(code), fmt_account(norm_account(code))):
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
            rec = await self.t.get(entity, key)
            return rec.get("Description") if rec else None
        return await self._cached(("descr", entity, key), f)

    # ---- Dublikat tekshiruvi
    async def check_duplicate(self, entity: str, number: str, doc_date: date, amount: float,
                              kontr_key: str | None) -> dict:
        """Natija: status = duplicate | discrepancy | new (+ sabab, 1C dagi hujjat, farqlar).

        kind: exact  — raqam+sana+summa+kontragent to'liq mos (hech qachon qayta yozilmaydi)
              weak   — 1C dagi hujjatda kiruvchi raqam yo'q, sana+summa+kontragent mos (buxgalter tasdig'i bilan
                       "alohida to'lov" sifatida yozish mumkin)
              number — shu raqamli hujjat bor, lekin summa/sana farqli
              none   — mos hujjat yo'q"""
        if not kontr_key:
            return {"status": "new", "kind": "none", "reason": "Kontragent 1C da topilmadi — dublikat tekshiruvi faqat raqam bo'yicha",
                    "match": None, "diffs": []}
        base = [Cond("DeletionMark", "eq", False), Cond(FIELDS["counterparty"], "eq", Guid(kontr_key))]
        target_day = doc_date.isoformat()

        if number:
            cands = await self._q(entity, base + [Cond(FIELDS["in_number"], "eq", number)], top=20)
            # raqamlar yil boshida qayta boshlanadi — faqat ±31 kun ichidagilarni solishtiramiz
            near = [c for c in cands if _day(c.get(FIELDS["in_date"])) and
                    abs((date.fromisoformat(_day(c.get(FIELDS["in_date"]))) - doc_date).days) <= 31]
            for c in near:
                if _day(c.get(FIELDS["in_date"])) == target_day and _same_amount(c.get(FIELDS["amount"]), amount):
                    return {"status": "duplicate", "kind": "exact", "reason": "Raqam + Sana + Summa + Kontragent to'liq mos keldi",
                            "match": self._doc_brief(c), "diffs": []}
            if near:
                c = near[0]
                diffs = []
                if not _same_amount(c.get(FIELDS["amount"]), amount):
                    diffs.append(f"Summa: faylda {_fmt_sum(amount)}, 1C da {_fmt_sum(c.get(FIELDS['amount']))}")
                if _day(c.get(FIELDS["in_date"])) != target_day:
                    diffs.append(f"Sana: faylda {_fmt_date(target_day)}, 1C da {_fmt_date(c.get(FIELDS['in_date']))}")
                return {"status": "discrepancy", "kind": "number", "reason": "Shu raqamli hujjat 1C da bor, lekin rekvizitlar farq qiladi",
                        "match": self._doc_brief(c), "diffs": diffs}

        # Raqamsiz kiritilgan bo'lishi mumkin: Sana + Summa + Kontragent
        start = datetime.combine(doc_date, datetime.min.time())
        same_day = await self._q(entity, base + [
            Cond(FIELDS["amount"], "eq", float(amount)),
            Cond("Date", "ge", DT(start)), Cond("Date", "le", DT(start + timedelta(days=1, seconds=-1)))], top=5)
        if same_day:
            return {"status": "duplicate", "kind": "weak", "reason": "Sana + Summa + Kontragent mos keldi (1C da kiruvchi raqamsiz)",
                    "match": self._doc_brief(same_day[0]), "diffs": []}
        return {"status": "new", "kind": "none", "reason": "1C da mos hujjat topilmadi", "match": None, "diffs": []}

    @staticmethod
    def _doc_brief(d: dict) -> dict:
        return {"ref": d.get("Ref_Key"), "number": d.get("Number"), "date": _day(d.get("Date")),
                "in_number": d.get(FIELDS["in_number"]), "in_date": _day(d.get(FIELDS["in_date"])),
                "amount": d.get(FIELDS["amount"]), "posted": d.get("Posted"), "operation": d.get(FIELDS["operation"]),
                "account_key": d.get(FIELDS["settlement_account"]) or
                ((d.get(FIELDS["payment_lines"]) or [{}])[0] or {}).get(FIELDS["settlement_account"])}

    # ---- Tarixdan o'rganish
    @staticmethod
    def _extract_pattern(doc: dict) -> dict:
        line = (doc.get(FIELDS["payment_lines"]) or [{}])[0] if doc.get(FIELDS["payment_lines"]) else {}
        acc = doc.get(FIELDS["settlement_account"])
        if _is_empty_key(acc):
            acc = line.get(FIELDS["settlement_account"])
        cfi = doc.get(FIELDS["cash_flow_item"])
        if _is_empty_key(cfi):
            cfi = line.get(FIELDS["cash_flow_item"])
        contract = doc.get(FIELDS["contract"])
        if _is_empty_key(contract):
            contract = line.get(FIELDS["contract"])
        extra = {k: doc[k] for k in ACCOUNT_BOUND_FIELDS if k in doc and not _is_empty_key(doc[k])}
        goods = doc.get("Товары") or []
        if goods and goods[0].get("СтавкаНДС"):
            extra["СтавкаНДС"] = goods[0]["СтавкаНДС"]
        return {"operation": doc.get(FIELDS["operation"]), "account_key": None if _is_empty_key(acc) else acc,
                "contract_key": None if _is_empty_key(contract) else contract,
                "cash_flow_key": None if _is_empty_key(cfi) else cfi, "extra": extra}

    async def learn_history(self, entity: str, kontr_key: str | None, purpose: str = "", top: int = 5,
                            direction: str = "") -> dict | None:
        """Shu kontragent (yoki to'lov maqsadi kalit so'zi) bo'yicha oxirgi o'tkazilgan hujjatlardan rekvizitlar."""
        docs, basis = [], ""
        if kontr_key:
            docs = await self._q(entity, [Cond(FIELDS["counterparty"], "eq", Guid(kontr_key)),
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
        votes = Counter((p["operation"], p["account_key"]) for p in patterns)
        (op, acc_key), hits = votes.most_common(1)[0]
        best = next(p for p in patterns if (p["operation"], p["account_key"]) == (op, acc_key))
        code = await self.account_code(acc_key)
        samples = [{"number": d.get(FIELDS["in_number"]) or d.get("Number"), "date": _day(d.get("Date")),
                    "amount": d.get(FIELDS["amount"]), "operation": p["operation"]} for d, p in zip(docs, patterns)]
        return {
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

    # ---- Qoralama yaratish
    async def create_draft(self, entity: str, payload: dict) -> dict:
        body = {k: v for k, v in payload.items() if v is not None}
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
