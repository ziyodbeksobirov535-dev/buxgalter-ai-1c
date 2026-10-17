"""Bank vipiskasi (Банковская выписка): parsing → dublikat tekshiruvi → tarixdan schyot → 1C qoralama.

Qo'llab-quvvatlanadigan formatlar:
  * Excel (.xlsx) — O'zbekiston banklari eksporti (sarlavha qatori avtomatik topiladi, UZ/RU ustun nomlari)
  * CSV (.csv) — ; yoki , ajratuvchi, UTF-8 / Windows-1251
  * 1CClientBankExchange (.txt) — 1C "Клиент-банк" almashinuv formati
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from services_1c import (ACCOUNT_CHOICES, E_BANK_IN, E_BANK_OUT, E_COUNTERPARTY, FIELDS, OPERATION_BY_ACCOUNT,
                         PURPOSE_KEYWORDS, ODataError, OneCService, fmt_account)

STATUS_LABELS = {
    "duplicate": "🟢 1C da bor (Dublikat - o'tkazib yuboriladi)",
    "new": "🟡 Yangi (Kiritishga tayyor)",
    "discrepancy": "🔴 Farq bor (Summa/Sana tafovuti)",
    "posted": "✅ 1C ga qoralama sifatida yozildi",
}


class StatementError(ValueError):
    pass


@dataclass
class BankRow:
    date: date
    number: str
    direction: str  # kirim | chiqim
    amount: float
    counterparty_name: str = ""
    counterparty_inn: str = ""
    mfo: str = ""
    account_no: str = ""
    purpose: str = ""
    id: str = ""

    def key(self) -> str:
        raw = f"{self.date}|{self.number}|{self.direction}|{self.amount:.2f}|{self.counterparty_inn}"
        return "b-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


@dataclass
class Statement:
    rows: list[BankRow]
    own_account: str = ""
    source: str = ""
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- qiymatlarni normallashtirish
def parse_amount(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = str(v).replace("\xa0", "").replace(" ", "").replace("'", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return round(float(s), 2) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def parse_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < v < 80000:  # Excel seriya raqami
        return date(1899, 12, 30) + timedelta(days=int(v))
    s = str(v).strip().split(" ")[0]
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y", "%Y.%m.%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def clean_code(v) -> str:
    """STIR / MFO / hisob raqami: faqat raqamlar (Excel float formatini ham to'g'rilaydi)."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\D", "", str(v))


def classify_header(h) -> str | None:
    s = str(h or "").strip().lower()
    if not s:
        return None
    rules = [
        ("purpose", ("maqsad", "назначен", "purpose", "izoh")),
        ("inn", ("stir", "inn", "инн")),
        ("mfo", ("mfo", "мфо", "бик")),
        ("debit", ("debet", "дебет", "chiqim", "расход", "списан")),
        ("credit", ("kredit", "кредит", "kirim", "приход", "поступ")),
        ("account", ("hisob", "счет", "счёт", "account")),
        ("name", ("kontragent", "контрагент", "наимен", "korrespondent", "корреспондент", "nomi", "получател", "плательщ")),
        ("date", ("sana", "дата", "date")),
        ("number", ("hujjat", "№", "номер", "nomer", "док", "doc")),
        ("amount", ("summa", "сумма")),
        ("direction", ("turi", "тип", "д/к", "вид")),
    ]
    for name, keys in rules:
        if any(k in s for k in keys):
            return name
    return None


# ---------------------------------------------------------------- parserlar
def _rows_from_table(table: list[list]) -> Statement:
    header_idx, mapping = None, {}
    for i, row in enumerate(table[:40]):
        m = {}
        for j, cell in enumerate(row):
            kind = classify_header(cell)
            if kind and kind not in m:
                m[kind] = j
        if "date" in m and ({"debit", "credit"} <= m.keys() or "amount" in m) and len(m) >= 4:
            header_idx, mapping = i, m
            break
    if header_idx is None:
        raise StatementError("Vipiska sarlavha qatori topilmadi. Kerakli ustunlar: Sana, Hujjat №, "
                             "Debet/Kredit (yoki Summa), Kontragent, STIR, To'lov maqsadi.")
    st = Statement(rows=[])
    for k in range(header_idx):  # sarlavha ustidagi qatorlardan o'z hisob raqamini topish
        text = " ".join(str(c) for c in table[k] if c is not None)
        m = re.search(r"(\d{20})", text)
        if m and not st.own_account:
            st.own_account = m.group(1)

    def cell(row, kind):
        j = mapping.get(kind)
        return row[j] if j is not None and j < len(row) else None

    for row in table[header_idx + 1:]:
        if not any(c not in (None, "") for c in row):
            continue
        first = str(row[0] or "").strip().lower()
        if first.startswith(("jami", "итого", "всего", "оборот")):
            break
        d = parse_date(cell(row, "date"))
        if not d:
            continue
        if "debit" in mapping or "credit" in mapping:
            debit, credit = parse_amount(cell(row, "debit")), parse_amount(cell(row, "credit"))
            if debit and credit:
                st.warnings.append(f"{d}: bir qatorda ham debet, ham kredit — qator o'tkazib yuborildi")
                continue
            direction, amount = ("chiqim", debit) if debit else ("kirim", credit)
        else:
            amount = parse_amount(cell(row, "amount"))
            dtext = str(cell(row, "direction") or "").lower()
            direction = "chiqim" if any(x in dtext for x in ("d", "chiq", "спис", "расх")) else "kirim"
            amount = abs(amount)
        if not amount:
            continue
        num = cell(row, "number")
        if isinstance(num, float) and num.is_integer():
            num = int(num)
        st.rows.append(BankRow(
            date=d, number=str(num or "").strip(), direction=direction, amount=amount,
            counterparty_name=str(cell(row, "name") or "").strip(), counterparty_inn=clean_code(cell(row, "inn")),
            mfo=clean_code(cell(row, "mfo")), account_no=clean_code(cell(row, "account")),
            purpose=str(cell(row, "purpose") or "").strip()))
    return st


def parse_excel(content: bytes) -> Statement:
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as e:  # noqa: BLE001
        raise StatementError(f"Excel faylni o'qib bo'lmadi ({e}). Eski .xls bo'lsa — .xlsx ga saqlang.") from e
    last_err = None
    for ws in wb.worksheets:
        table = [list(r) for r in ws.iter_rows(values_only=True)]
        try:
            return _rows_from_table(table)
        except StatementError as e:
            last_err = e
    raise last_err or StatementError("Excel faylda varaq topilmadi")


def _decode(content: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def parse_csv(content: bytes) -> Statement:
    text = _decode(content)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    return _rows_from_table([list(r) for r in csv.reader(io.StringIO(text), dialect)])


def parse_1c_exchange(content: bytes) -> Statement:
    text = _decode(content)
    if "1CClientBankExchange" not in text[:200]:
        raise StatementError("Fayl 1CClientBankExchange formatida emas")
    st = Statement(rows=[])
    doc: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "=" not in line and line != "КонецДокумента":
            continue
        if line.startswith("РасчСчет=") and not st.own_account:
            st.own_account = clean_code(line.split("=", 1)[1])
        elif line.startswith("СекцияДокумент="):
            doc = {}
        elif line == "КонецДокумента" and doc is not None:
            st.rows.append(_exchange_doc(doc, st.own_account))
            doc = None
        elif doc is not None:
            k, v = line.split("=", 1)
            doc[k.strip()] = v.strip()
    st.rows = [r for r in st.rows if r]
    if not st.rows:
        raise StatementError("1CClientBankExchange faylida hujjat topilmadi")
    return st


def _exchange_doc(d: dict, own: str) -> BankRow | None:
    dt = parse_date(d.get("Дата"))
    if not dt:
        return None
    payer_acc = clean_code(d.get("ПлательщикСчет") or d.get("ПлательщикРасчСчет"))
    if own and payer_acc == own:
        out = True
    elif own and clean_code(d.get("ПолучательСчет") or d.get("ПолучательРасчСчет")) == own:
        out = False
    else:
        out = bool(d.get("ДатаСписано")) and not d.get("ДатаПоступило")
    side = "Получатель" if out else "Плательщик"
    return BankRow(
        date=dt, number=d.get("Номер", ""), direction="chiqim" if out else "kirim", amount=parse_amount(d.get("Сумма")),
        counterparty_name=d.get(f"{side}1") or d.get(side, ""), counterparty_inn=clean_code(d.get(f"{side}ИНН")),
        mfo=clean_code(d.get(f"{side}МФО") or d.get(f"{side}БИК")),
        account_no=clean_code(d.get(f"{side}Счет") or d.get(f"{side}РасчСчет")),
        purpose=d.get("НазначениеПлатежа", ""))


def parse_statement(filename: str, content: bytes) -> Statement:
    name = (filename or "").lower()
    if len(content) > 10 * 1024 * 1024:
        raise StatementError("Fayl juda katta (10 MB dan oshmasin)")
    if name.endswith((".xlsx", ".xlsm")) or content[:2] == b"PK":
        st = parse_excel(content)
    elif name.endswith(".txt") or b"1CClientBankExchange" in content[:200]:
        st = parse_1c_exchange(content)
    elif name.endswith(".csv"):
        st = parse_csv(content)
    elif name.endswith(".xls"):
        raise StatementError("Eski .xls format qo'llab-quvvatlanmaydi — faylni Excel'da .xlsx sifatida saqlang")
    else:
        raise StatementError("Format noma'lum. .xlsx, .csv yoki 1CClientBankExchange .txt yuklang")
    if not st.rows:
        raise StatementError("Vipiskada birorta ham tranzaksiya topilmadi")
    seen: dict[str, int] = {}
    for r in st.rows:
        base = r.key()
        seen[base] = seen.get(base, 0) + 1
        r.id = base if seen[base] == 1 else f"{base}-{seen[base]}"
    st.source = filename
    return st


# ---------------------------------------------------------------- tahlil
def rule_account(direction: str, purpose: str) -> str:
    low = (purpose or "").lower()
    for kw, acc, kw_dir in PURPOSE_KEYWORDS:
        if kw in low and kw_dir == direction:
            return acc
    return "60.10" if direction == "chiqim" else "40.10"


def operation_for(account: str, direction: str) -> str:
    ops = OPERATION_BY_ACCOUNT.get(account)
    if not ops:
        return "ПрочееСписание" if direction == "chiqim" else "ПрочееПоступление"
    return ops[0] if direction == "chiqim" else ops[1]


async def analyze_bank_row(r: BankRow, onec: OneCService, prev: dict | None = None,
                           in_file_duplicate: bool = False) -> dict:
    entity = E_BANK_OUT if r.direction == "chiqim" else E_BANK_IN
    row = {**asdict(r), "date": r.date.isoformat(), "kind": "bank", "entity": entity, "blockers": [],
           "posted": False, "posted_ref": None, "posted_number": None, "error": None, "learned": None}
    kontr = await onec.find_counterparty(r.counterparty_inn)
    row["counterparty_key"] = kontr["Ref_Key"] if kontr else None
    row["counterparty_1c_name"] = kontr.get("Description") if kontr else None
    if not kontr:
        row["blockers"].append("Kontragent 1C da topilmadi (STIR bo'yicha) — avval katalogga qo'shing")

    if in_file_duplicate:
        dup = {"status": "duplicate", "reason": "Shu tranzaksiya faylda ikki marta uchraydi", "match": None, "diffs": []}
    else:
        dup = await onec.check_duplicate(entity, r.number, r.date, r.amount, row["counterparty_key"])
    row.update(status=dup["status"], status_reason=dup["reason"], match=dup["match"], diffs=dup["diffs"])

    if dup["status"] != "duplicate":
        row["learned"] = await onec.learn_history(entity, row["counterparty_key"], r.purpose, direction=r.direction)
    learned = row["learned"]
    match_code = await onec.account_code((dup["match"] or {}).get("account_key"))
    if dup["status"] == "duplicate" and match_code:
        row["account"], row["account_source"] = match_code, "onec"
        row["operation"] = dup["match"].get("operation") or operation_for(match_code, r.direction)
    elif prev and prev.get("account_source") == "manual":
        row["account"], row["account_source"] = prev["account"], "manual"
        row["operation"] = operation_for(row["account"], r.direction)
    elif learned and learned.get("account_code"):
        row["account"], row["account_source"], row["operation"] = learned["account_code"], "history", learned["operation"]
    else:
        row["account"] = rule_account(r.direction, r.purpose)
        row["account_source"] = "rules"
        row["operation"] = operation_for(row["account"], r.direction)
    if prev and prev.get("posted"):
        row.update(posted=True, posted_ref=prev["posted_ref"], posted_number=prev["posted_number"], status="posted")
    finalize_row(row)
    return row


def finalize_row(row: dict) -> None:
    row["status_label"] = STATUS_LABELS.get(row["status"], row["status"])
    row["selectable"] = row["status"] == "new" and not row["blockers"] and not row["posted"]
    if row["account_source"] == "history":
        row["account_badge"] = f"🧠 Oldingi 1C tarixidan o'rganildi ({row['account']})"
    elif row["account_source"] == "manual":
        row["account_badge"] = "✍️ Buxgalter tanladi"
    elif row["account_source"] == "onec":
        row["account_badge"] = "📄 1C dagi mavjud hujjatdan"
    else:
        row["account_badge"] = "🤖 AI taxmini (1C tarixi yo'q)"


async def analyze_statement(st: Statement, onec: OneCService, previous: list[dict] | None = None) -> list[dict]:
    prev = {p["id"]: p for p in previous or []}
    seen: set[tuple] = set()
    flags = []
    for r in st.rows:
        sig = (r.date, r.number, r.direction, round(r.amount, 2), r.counterparty_inn)
        flags.append(sig in seen)
        seen.add(sig)
    return list(await asyncio.gather(*(analyze_bank_row(r, onec, prev.get(r.id), f)
                                       for r, f in zip(st.rows, flags))))


def kpis(rows: list[dict]) -> dict:
    ready = [r for r in rows if r["selectable"]]
    cands = [r for r in rows if r["status"] in ("new", "posted")]
    learned = [r for r in cands if r["account_source"] == "history"]
    return {
        "total": len(rows), "ready": len(ready), "duplicates": sum(r["status"] == "duplicate" for r in rows),
        "discrepancies": sum(r["status"] == "discrepancy" for r in rows), "posted": sum(r["posted"] for r in rows),
        "blocked": sum(1 for r in rows if r["status"] == "new" and r["blockers"]),
        "learned": len(learned), "learned_base": len(cands),
        "learned_pct": round(100 * len(learned) / len(cands)) if cands else 0,
        "ready_sum": round(sum(r["amount"] for r in ready), 2),
    }


def cash_flow(rows: list[dict]) -> dict:
    days: dict[str, dict] = {}
    for r in rows:
        d = days.setdefault(r["date"], {"date": r["date"], "kirim": 0.0, "chiqim": 0.0})
        d[r["direction"]] += r["amount"]
    out = sorted(days.values(), key=lambda x: x["date"])
    for d in out:
        d["net"] = round(d["kirim"] - d["chiqim"], 2)
    tk = round(sum(d["kirim"] for d in out), 2)
    tc = round(sum(d["chiqim"] for d in out), 2)
    by_cp: dict[str, float] = {}
    for r in rows:
        by_cp[r["counterparty_name"]] = by_cp.get(r["counterparty_name"], 0) + (r["amount"] if r["direction"] == "kirim" else -r["amount"])
    top = sorted(by_cp.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
    return {"days": out, "total_kirim": tk, "total_chiqim": tc, "net": round(tk - tc, 2),
            "top_counterparties": [{"name": n, "net": round(v, 2)} for n, v in top]}


# ---------------------------------------------------------------- qoralama (draft) payload
async def build_bank_payload(row: dict, onec: OneCService, own_account: str = "") -> dict:
    if not row.get("counterparty_key"):
        raise ODataError("Kontragent Ref_Key topilmadi")
    org = await onec.organization()
    if not org:
        raise ODataError("1C da tashkilot (Catalog_Организации) topilmadi")
    org_acc = await onec.find_org_bank_account(own_account or None)
    if not org_acc:
        raise ODataError("Tashkilotning bank hisobi (Catalog_БанковскиеСчета) topilmadi")
    learned = row.get("learned") or {}
    acc_key = learned.get("account_key") if learned.get("account_code") == row["account"] else None
    acc_key = acc_key or await onec.account_key(row["account"])
    if not acc_key:
        raise ODataError(f"{row['account']} schyoti 1C hisoblar rejasida topilmadi")
    contract_key = learned.get("contract_key")
    if not contract_key:
        contract = await onec.find_contract(row["counterparty_key"], row["direction"])
        contract_key = contract["Ref_Key"] if contract else None
    kontr_bank = await onec.find_bank_account(row["counterparty_key"], E_COUNTERPARTY, row.get("account_no") or None)
    cfi = learned.get("cash_flow_key") if learned.get("account_code") == row["account"] else None
    payload = {
        "Date": f"{row['date']}T12:00:00",
        "Организация_Key": org["Ref_Key"],
        "СчетОрганизации_Key": org_acc["Ref_Key"],
        FIELDS["operation"]: row["operation"],
        FIELDS["counterparty"]: row["counterparty_key"],
        FIELDS["contract"]: contract_key,
        "СчетКонтрагента_Key": kontr_bank["Ref_Key"] if kontr_bank else None,
        FIELDS["amount"]: row["amount"],
        FIELDS["in_number"]: row["number"],
        FIELDS["in_date"]: f"{row['date']}T00:00:00",
        FIELDS["purpose"]: row["purpose"],
        FIELDS["settlement_account"]: acc_key,
        FIELDS["cash_flow_item"]: cfi,
        FIELDS["payment_lines"]: [{
            "LineNumber": "1", FIELDS["contract"]: contract_key, "СуммаПлатежа": row["amount"],
            FIELDS["settlement_account"]: acc_key, FIELDS["cash_flow_item"]: cfi,
        }],
        "Комментарий": f"BUXGALTER AI: vipiskadan qoralama (№{row['number']}, {row['date']}). Tekshirib o'tkazing.",
    }
    if learned.get("account_code") == row["account"]:
        for k, v in (learned.get("extra") or {}).items():
            payload.setdefault(k, v)
    payload[FIELDS["payment_lines"]][0] = {k: v for k, v in payload[FIELDS["payment_lines"]][0].items() if v is not None}
    return payload


async def recheck_bank(row: dict, onec: OneCService) -> dict:
    return await onec.check_duplicate(row["entity"], row["number"], date.fromisoformat(row["date"]), row["amount"],
                                      row.get("counterparty_key"))


def set_account(row: dict, account: str) -> None:
    acc = fmt_account(account.replace(".", "")) or account
    if acc not in ACCOUNT_CHOICES and not (row.get("learned") or {}).get("account_code") == acc:
        raise ValueError(f"Noma'lum schyot: {account}")
    learned = row.get("learned") or {}
    row["account"] = acc
    if learned.get("account_code") == acc:
        row["account_source"], row["operation"] = "history", learned["operation"]
    else:
        row["account_source"] = "manual"
        if row["kind"] == "bank":
            row["operation"] = operation_for(acc, row["direction"])
    finalize_row(row)


async def post_rows(rows: list[dict], ids: list[str], onec: OneCService, builders: dict) -> list[dict]:
    """Tanlangan qatorlarni 1C ga QORALAMA sifatida yozadi. Har biridan oldin dublikat qayta tekshiriladi.

    builders: {"bank": (payload_builder, rechecker), "ehf": (...)}
    """
    results = []
    by_id = {r["id"]: r for r in rows}
    onec.invalidate()
    for rid in dict.fromkeys(ids):
        row = by_id.get(rid)
        if not row:
            results.append({"id": rid, "ok": False, "message": "Qator topilmadi"})
            continue
        if row["posted"]:
            results.append({"id": rid, "ok": False, "message": "Allaqachon 1C ga yozilgan"})
            continue
        if not row["selectable"]:
            why = row["blockers"][0] if row["blockers"] else row["status_label"]
            results.append({"id": rid, "ok": False, "message": f"Bloklandi: {why}"})
            continue
        build, recheck = builders[row["kind"]]
        try:
            fresh = await recheck(row, onec)
            if fresh["status"] != "new":
                row.update(status=fresh["status"], status_reason=fresh["reason"], match=fresh["match"],
                           diffs=fresh["diffs"])
                finalize_row(row)
                results.append({"id": rid, "ok": False, "message": f"Bloklandi: {row['status_label']}"})
                continue
            payload = await build(row, onec)
            created = await onec.create_draft(row["entity"], payload)
        except ODataError as e:
            row["error"] = str(e)
            results.append({"id": rid, "ok": False, "message": str(e)})
            continue
        row.update(posted=True, posted_ref=created["ref"], posted_number=created["number"], status="posted",
                   error=None)
        finalize_row(row)
        results.append({"id": rid, "ok": True, "message": f"Qoralama yaratildi: {created['number']}",
                        "number": created["number"]})
    return results
