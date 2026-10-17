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

from services_1c import E_PURCHASE, FIELDS, ODataError, OneCService
from services_vipiska import apply_rule, finalize_row

DIDOX_LIST_PATH = "/v1/documents"  # ?owner=0 (kiruvchi) &doctype=002 (hisob-faktura)


class DidoxError(Exception):
    pass


def _demo_invoices() -> list[dict]:
    from demo_data import COUNTERPARTIES, DIDOX_INVOICES, NOMENCLATURE
    out = []
    for inv in DIDOX_INVOICES:
        out.append({
            "id": inv["id"], "number": inv["number"], "date": inv["date"], "seller_inn": inv["seller_inn"],
            "seller_name": COUNTERPARTIES[inv["seller_inn"]][0], "total": inv["total"], "kind": inv["kind"],
            "items": [{"name": n, "ikpu": NOMENCLATURE.get(n), "qty": q, "price": p,
                       "total": round(inv["total"] / len(inv["items"]), 2)} for n, q, p in inv["items"]],
        })
    return out


def _map_invoice(d: dict) -> dict:
    """Didox javobidagi turli maydon nomlarini yagona ko'rinishga keltiradi."""
    def pick(*keys, default=None):
        for k in keys:
            if d.get(k) not in (None, ""):
                return d[k]
        return default
    items = [{"name": it.get("name") or it.get("product_name") or "", "ikpu": it.get("catalog_code") or it.get("ikpu"),
              "qty": float(it.get("count") or it.get("quantity") or 1), "price": float(it.get("price") or 0),
              "total": float(it.get("delivery_sum_with_vat") or it.get("total") or 0)}
             for it in pick("items", "products", default=[]) or []]
    number = str(pick("doc_number", "number", "facturaNo", default=""))
    return {"id": str(pick("doc_id", "id", "_id", default=number)), "number": number,
            "date": str(pick("doc_date", "date", "facturaDate", default=""))[:10],
            "seller_inn": str(pick("partnerTin", "seller_tin", "sellerTin", default="")),
            "seller_name": str(pick("partnerCompany", "seller_name", "sellerName", default="")),
            "total": float(pick("total_sum", "total", "delivery_sum_with_vat", default=0) or 0),
            "kind": "Товары", "items": items}


async def fetch_invoices(settings: dict) -> tuple[list[dict], str]:
    if settings.get("didox_demo_mode") or not settings.get("didox_user_key"):
        return _demo_invoices(), "demo"
    headers = {"user-key": settings["didox_user_key"]}
    if settings.get("didox_partner_token"):
        headers["Partner-Authorization"] = settings["didox_partner_token"]
    url = settings["didox_base_url"].rstrip("/") + DIDOX_LIST_PATH
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(url, headers=headers, params={"owner": 0, "doctype": "002", "limit": 100, "page": 1})
    except httpx.HTTPError as e:
        raise DidoxError(f"Didox serveriga ulanib bo'lmadi: {e}") from e
    if r.status_code >= 400:
        raise DidoxError(f"Didox xatosi {r.status_code}: {r.text[:300]}")
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
    kontr = await onec.find_counterparty(inv["seller_inn"])
    row["counterparty_key"] = kontr["Ref_Key"] if kontr else None
    row["counterparty_1c_name"] = kontr.get("Description") if kontr else None
    if not kontr:
        row["blockers"].append("Yetkazib beruvchi 1C da topilmadi (STIR bo'yicha)")
    for it in inv["items"]:
        nom = await onec.find_nomenclature(it["name"], it.get("ikpu"))
        it["nomenclature_key"] = nom["Ref_Key"] if nom else None
        if not nom:
            row["blockers"].append(f"Nomenklatura topilmadi: {it['name']} (IKPU {it.get('ikpu') or '—'})")
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
    if prev and prev.get("posted"):
        row.update(posted=True, posted_ref=prev["posted_ref"], posted_number=prev["posted_number"], status="posted")
    finalize_row(row)
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
    vat_rate = (learned.get("extra") or {}).get("СтавкаНДС", "НДС12")
    lines = []
    for i, it in enumerate(row["items"], 1):
        total = it["total"]
        net = round(total / 1.12, 2)
        lines.append({"LineNumber": str(i), "Номенклатура_Key": it["nomenclature_key"], "Количество": it["qty"],
                      "Цена": round(net / it["qty"], 2) if it["qty"] else net, "Сумма": total,
                      "СтавкаНДС": vat_rate, "СуммаНДС": round(total - net, 2)})
    common = await onec.common_fields(E_PURCHASE)
    return {
        **common,
        "Date": f"{row['date']}T12:00:00", "Организация_Key": org["Ref_Key"], FIELDS["operation"]: row["operation"],
        FIELDS["counterparty"]: row["counterparty_key"], FIELDS["contract"]: contract_key,
        FIELDS["amount"]: row["amount"], FIELDS["in_number"]: row["number"],
        FIELDS["in_date"]: f"{row['date']}T00:00:00", FIELDS["settlement_account"]: acc_key,
        "СуммаВключаетНДС": True, ("Услуги" if row.get("doc_kind") == "Услуги" else "Товары"): lines,
        "Комментарий": f"BUXGALTER AI: Didox EHF №{row['number']} dan qoralama. Tekshirib o'tkazing.",
    }


async def recheck_purchase(row: dict, onec: OneCService) -> dict:
    return await onec.check_duplicate(E_PURCHASE, row["number"], date.fromisoformat(row["date"]), row["amount"],
                                      row.get("counterparty_key"))
