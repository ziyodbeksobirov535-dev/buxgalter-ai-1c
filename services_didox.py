"""Didox EHF (elektron hisob-faktura) ↔ 1C solishtirish.

Demo rejimda demo_data.DIDOX_INVOICES ishlatiladi. Real rejimda Didox partner API dan kiruvchi
hisob-fakturalar olinadi. Didox API endpoint/maydon nomlari hamkorlik shartnomasiga qarab farq qilishi mumkin —
DIDOX_LIST_PATH va _map_invoice() ni o'zingizning hujjatlaringizga moslang.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import date

import httpx

from accounts_uz import VAT_ACCOUNT
from services_1c import E_PURCHASE, FIELDS, ODataError, OneCService
from services_vipiska import advance_key, apply_rule, finalize_row

DIDOX_LIST_PATH = "/v1/documents"  # ?owner=0 (kiruvchi) &doctype=002 (hisob-faktura)


class DidoxError(Exception):
    def __init__(self, message: str, status: int = 502):  # 502 — Didox serveri; 400 — sozlama yetishmaydi
        super().__init__(message)
        self.status = status


def _demo_invoices() -> list[dict]:
    from demo_data import COUNTERPARTIES, DIDOX_INVOICES, NOMENCLATURE
    out = []
    for inv in DIDOX_INVOICES:
        out.append({
            "id": inv["id"], "number": inv["number"], "date": inv["date"], "seller_inn": inv["seller_inn"],
            "seller_name": COUNTERPARTIES[inv["seller_inn"]][0], "total": inv["total"], "kind": inv["kind"],
            "items": [{"name": n, "ikpu": NOMENCLATURE.get(n), "qty": q, "price": p, "vat_rate": 12.0,
                       "total": round(inv["total"] / len(inv["items"]), 2)} for n, q, p in inv["items"]],
        })
    return out


VAT_DEFAULT = 12.0  # O'zbekistonda QQS stavkasi; EHF qatorida stavka ko'rsatilmasa


def _vat_rate(it: dict) -> float | None:
    """EHF qatoridagi QQS stavkasi: 12 / 0; "QQSsiz" (aylanma solig'i to'lovchisi) — 0. Noma'lum — None."""
    if it.get("without_vat") in (True, 1, "1", "true"):
        return 0.0
    for k in ("vat_rate", "vatRate", "nds_rate"):
        if it.get(k) not in (None, ""):
            try:
                return float(it[k])
            except (TypeError, ValueError):
                return None
    return None


def vat_rate_of(item: dict) -> float:
    rate = item.get("vat_rate")
    return VAT_DEFAULT if rate is None else float(rate)


def vat_code(rate: float) -> str:
    return "БезНДС" if rate <= 0 else f"НДС{int(rate) if float(rate).is_integer() else rate}"


# O'zbekiston EDO hujjat turlari (Didox, Soliq Servis)
DOC_TYPE_LABELS = {
    "002": "Elektron hisob-faktura (EHF)",
    "003": "Ishonchnoma (Доверенность)",
    "004": "Bajarilgan ishlar / xizmatlar dalolatnomasi",
    "007": "Tovar-transport yukxati (TTN)",
}


def validate_ikpu(code: str | None) -> dict:
    """MXIK (IKPU) kodini tekshirish — O'zbekistonda tovar/xizmat uchun 17 xonali bo'lishi shart."""
    raw = "".join(ch for ch in str(code or "") if ch.isdigit())
    if not raw:
        return {"code": "", "valid": False, "reason": "MXIK kodi ko'rsatilmagan"}
    if len(raw) != 17:
        return {"code": raw, "valid": False, "reason": f"MXIK 17 xonali bo'lishi kerak ({len(raw)} xona)"}
    return {"code": raw, "valid": True, "reason": "To'g'ri"}


def _map_invoice(d: dict) -> dict:
    """Didox javobidagi turli maydon nomlarini yagona ko'rinishga keltiradi."""
    def pick(*keys, default=None):
        for k in keys:
            if d.get(k) not in (None, ""):
                return d[k]
        return default
    items = [{"name": it.get("name") or it.get("product_name") or "", "ikpu": it.get("catalog_code") or it.get("ikpu"),
              "qty": float(it.get("count") or it.get("quantity") or 1), "price": float(it.get("price") or 0),
              "total": float(it.get("delivery_sum_with_vat") or it.get("total") or 0), "vat_rate": _vat_rate(it)}
             for it in pick("items", "products", default=[]) or []]
    number = str(pick("doc_number", "number", "facturaNo", default=""))
    return {"id": str(pick("doc_id", "id", "_id", default=number)), "number": number,
            "date": str(pick("doc_date", "date", "facturaDate", default=""))[:10],
            "seller_inn": str(pick("partnerTin", "seller_tin", "sellerTin", default="")),
            "seller_name": str(pick("partnerCompany", "seller_name", "sellerName", default="")),
            "total": float(pick("total_sum", "total", "delivery_sum_with_vat", default=0) or 0),
            "kind": "Товары", "items": items}


async def fetch_invoices(settings: dict, date_from: str = "", date_to: str = "", known_ids=()) -> tuple[list[dict], str]:
    if settings.get("edocs_enabled"):
        from services_edocs import EdocsError
        from services_edocs_api import fetch_invoices as fetch_edocs
        try:
            extra = {"known_ids": known_ids} if known_ids else {}
            return await fetch_edocs(settings, date_from=date_from, date_to=date_to, **extra), "edocs"
        except EdocsError as e:
            raise DidoxError(str(e), status=400) from None
    if date_from or date_to:
        raise DidoxError("Sana bo'yicha yuklash hozir E-DOCS manbasi uchun mavjud.", status=400)
    if settings.get("didox_demo_mode"):
        return _demo_invoices(), "demo"
    if not settings.get("didox_partner_token"):
        # Kalitsiz Didox kirishni rad etadi — parolni behuda yubormaymiz, demo'ga ham yashirin o'tmaymiz
        raise DidoxError("Didox hamkor kaliti (partner token) kiritilmagan. Sozlamalar → Ulanishlar → EHF manbasi: "
                         "Didox'dan olingan kalitni kiriting yoki E-DOCS'ni tanlang.", status=400)
    if not settings.get("didox_user_key") and not settings.get("didox_password"):
        raise DidoxError("Didox paroli yoki user-key kiritilmagan (Sozlamalar → Ulanishlar).", status=400)
    from services_didox_draft import DidoxDraftClient, DraftError
    try:
        async with DidoxDraftClient(settings) as client:
            if settings.get("didox_user_key"):
                client.token = settings["didox_user_key"]
            else:
                await client.login()
            r = await client._send("GET", "v1/documents?owner=0&doctype=002&limit=100&page=1")
    except DraftError as e:
        raise DidoxError(str(e), status=400 if e.status < 500 else 502) from None
    if r.status_code >= 400:
        raise DidoxError(f"Didox xatosi {r.status_code}. Kalit/parol huquqlarini tekshiring; qayta urinilmadi.")
    data = r.json()
    docs = data.get("data") or data.get("items") or data.get("documents") or (data if isinstance(data, list) else [])
    return [_map_invoice(d) for d in docs], "live"


async def analyze_invoice(inv: dict, onec: OneCService, prev: dict | None = None, rules=None) -> dict:
    d = date.fromisoformat(inv["date"])
    rid = "e-" + hashlib.sha1(f"{inv['id']}|{inv['number']}".encode()).hexdigest()[:10]
    row = {"id": rid, "kind": "ehf", "entity": E_PURCHASE, "date": inv["date"], "number": inv["number"],
           "direction": "chiqim", "amount": inv["total"], "counterparty_name": inv["seller_name"],
           "counterparty_inn": inv["seller_inn"], "mfo": "", "account_no": "",
           "purpose": f"EHF: {', '.join(i['name'] for i in inv['items'])}"[:300], "items": inv["items"],
           "doc_kind": inv.get("kind", "Товары"), "blockers": [], "posted": False, "posted_ref": None,
           "posted_number": None, "error": None, "learned": None}
    readonly = onec.mode in ("offline", "file")  # 1C ga ulanmagan: xaridlar 1C bilan solishtirib bo'lmaydi
    kontr = await onec.find_counterparty(inv["seller_inn"]) or await onec.find_counterparty_by_name(inv["seller_name"])
    row["counterparty_key"] = kontr["Ref_Key"] if kontr else None
    row["counterparty_1c_name"] = kontr.get("Description") if kontr else None
    if not kontr and not readonly:
        row["blockers"].append("Yetkazib beruvchi 1C da topilmadi (STIR bo'yicha)")
    for it in inv["items"]:
        nom = None if readonly else await onec.find_nomenclature(it["name"], it.get("ikpu"))
        it["nomenclature_key"] = nom["Ref_Key"] if nom else None
        if not nom and not readonly:
            row["blockers"].append(f"Nomenklatura topilmadi: {it['name']} (IKPU {it.get('ikpu') or '—'})")
    if readonly:
        dup = {"status": "unchecked", "kind": "none", "match": None, "diffs": [],
               "reason": "1C ga ulanmagan — xarid hujjatlari (Поступление товаров и услуг) bilan solishtirilmadi"}
    else:
        dup = await onec.check_duplicate(E_PURCHASE, inv["number"], d, inv["total"], row["counterparty_key"])
    row.update(status=dup["status"], status_reason=dup["reason"], match=dup["match"], diffs=dup["diffs"],
               dup_kind=dup.get("kind"))
    if dup["status"] != "duplicate" or dup.get("kind") == "weak":
        row["learned"] = await onec.learn_history(E_PURCHASE, row["counterparty_key"])
    learned = row["learned"]
    match_code = await onec.account_code((dup["match"] or {}).get("account_key"))
    locked = rules.locked(inv["seller_inn"], "", "ehf", "") if rules else None
    stored = rules.learned(inv["seller_inn"], "", "ehf", "") if rules else None
    row["operation"] = (learned or {}).get("operation") or row["doc_kind"]
    if dup["status"] == "duplicate" and match_code:
        row["account"], row["account_source"] = match_code, "onec"
    elif prev and prev.get("account_source") == "manual" and not (locked and locked.rule["account"] == prev["account"]):
        row["account"], row["account_source"] = prev["account"], "manual"
    elif locked:
        apply_rule(row, locked)
    elif learned and learned.get("account_code"):
        row["account"], row["account_source"] = learned["account_code"], "history"
    elif stored:
        apply_rule(row, stored)
    else:
        row["account"], row["account_source"] = "60.10", "rules"
    # Provodka: Dt tovar/xarajat (29.10 / 94.20) + Dt hisobga olinadigan QQS (44.10) — Kt yetkazib beruvchi (60.10)
    extra = (learned or {}).get("extra") or {}
    row["item_account"] = (await onec.account_code(extra.get("item_account_key"))
                           or ("94.20" if row["doc_kind"] == "Услуги" else "29.10"))
    has_vat = any(vat_rate_of(it) > 0 for it in inv["items"])
    row["vat_account"] = (await onec.account_code(extra.get("vat_account_key")) or VAT_ACCOUNT) if has_vat else ""
    row["posting_note"] = (f"Dt {row['item_account']}" + (f" + Dt {row['vat_account']} (QQS 12%)" if row["vat_account"] else "")
                           + " — Kt {acc}")
    if prev and prev.get("posted"):
        row.update(posted=True, posted_ref=prev["posted_ref"], posted_number=prev["posted_number"], status="posted")
    finalize_row(row)
    row["posting_note"] = row["posting_note"].replace("{acc}", row["account"])
    return row


async def analyze_invoices(invoices: list[dict], onec: OneCService, previous: list[dict] | None = None,
                           rules=None) -> list[dict]:
    prev = {p["id"]: p for p in previous or []}

    async def one(inv):
        rid = "e-" + hashlib.sha1(f"{inv['id']}|{inv['number']}".encode()).hexdigest()[:10]
        return await analyze_invoice(inv, onec, prev.get(rid), rules)
    return list(await asyncio.gather(*(one(i) for i in invoices)))


async def build_purchase_payload(row: dict, onec: OneCService) -> dict:
    org = await onec.organization()
    if not org or not row.get("counterparty_key"):
        raise ODataError("Tashkilot yoki kontragent Ref_Key topilmadi")
    learned = row.get("learned") or {}
    acc_key = learned.get("account_key") if learned.get("account_code") == row["account"] else None
    acc_key = acc_key or await onec.account_key(row["account"])
    if not acc_key:
        raise ODataError(f"{row['account']} schyoti 1C da topilmadi")
    rule = row.get("rule") or {}
    contract_key = learned.get("contract_key") or (rule.get("contract_key") if rule.get("account") == row["account"]
                                                   else None) or None
    if not contract_key:
        c = await onec.find_contract(row["counterparty_key"], "chiqim")
        contract_key = c["Ref_Key"] if c else None
    learned_rate = (learned.get("extra") or {}).get("СтавкаНДС")
    item_key = await onec.account_key(row["item_account"]) if row.get("item_account") else None
    vat_key = await onec.account_key(row["vat_account"]) if row.get("vat_account") else None
    item_field = "СчетЗатрат_Key" if row.get("doc_kind") == "Услуги" else "СчетУчета_Key"
    lines = []
    for i, it in enumerate(row["items"], 1):
        total, rate = it["total"], vat_rate_of(it)
        net = round(total / (1 + rate / 100), 2)
        code = vat_code(rate)
        if rate == VAT_DEFAULT and learned_rate:  # 1C dagi aniq qiymat nomi (masalan "НДС12") tarixdan
            code = learned_rate
        lines.append({k: v for k, v in {
            "LineNumber": str(i), "Номенклатура_Key": it["nomenclature_key"], "Количество": it["qty"],
            "Цена": round(net / it["qty"], 2) if it["qty"] else net, "Сумма": total,
            "СтавкаНДС": code, "СуммаНДС": round(total - net, 2),
            item_field: item_key, "СчетУчетаНДС_Key": vat_key if rate > 0 else None}.items() if v is not None})
    common = await onec.common_fields(E_PURCHASE)
    return {
        **common,
        "Date": f"{row['date']}T12:00:00", "Организация_Key": org["Ref_Key"], FIELDS["operation"]: row["operation"],
        **await onec.counterparty_payload(E_PURCHASE, row["counterparty_key"]), FIELDS["contract"]: contract_key,
        FIELDS["amount"]: row["amount"], FIELDS["in_number"]: row["number"],
        FIELDS["in_date"]: f"{row['date']}T00:00:00", FIELDS["settlement_account"]: acc_key,
        "СуммаВключаетНДС": True,
        FIELDS["advance_account"]: await advance_key(row, onec),
        "СпособЗачетаАвансов": "Автоматически" if row.get("advance_account") else None,  # berilgan avans hisobga olinsin
        ("Услуги" if row.get("doc_kind") == "Услуги" else "Товары"): lines,
        "Комментарий": f"BUXGALTER AI: Didox EHF №{row['number']} dan qoralama. Tekshirib o'tkazing.",
    }


async def recheck_purchase(row: dict, onec: OneCService) -> dict:
    return await onec.check_duplicate(E_PURCHASE, row["number"], date.fromisoformat(row["date"]), row["amount"],
                                      row.get("counterparty_key"))
