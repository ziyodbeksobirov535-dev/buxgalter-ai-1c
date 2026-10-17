"""Demo rejim uchun ma'lumotlar: 1C OData bazasining "nusxasi", namuna vipiska va Didox EHF.

Barcha tashkilot nomlari, STIR va hisob raqamlari — to'qima (sinov uchun).
Demo 1C baza real 1C:Бухгалтерия для Узбекистана 3.0 OData javoblari shaklida tuzilgan
(Ref_Key, Контрагент_Key, РасшифровкаПлатежа ...), shuning uchun mantiq real bazada ham xuddi shunday ishlaydi.
"""
from __future__ import annotations

import uuid
from datetime import date

_NS = uuid.UUID("6f1c0b2a-7d4e-4c55-9a51-0c1e2b3a4d5e")


def gid(*parts: object) -> str:
    return str(uuid.uuid5(_NS, ":".join(str(p) for p in parts)))


ORG = {"name": "NAMUNA TRADE MChJ", "inn": "305123456", "account": "20208000100123456001", "mfo": "00873"}

# STIR -> (nomi, rol, MFO, hisob raqami)
COUNTERPARTIES = {
    "301234567": ("GLOBAL TEXTILE MChJ", "buyer", "00440", "20208000400111222001"),
    "302345678": ("ORIENT LOGISTICS MChJ", "supplier", "00401", "20208000500222333001"),
    "303456789": ("SAMARQAND AGRO MChJ", "buyer", "00491", "20208000600333444001"),
    "304567890": ("TOSHKENT QURILISH MATERIALLARI MChJ", "supplier", "00450", "20208000700444555001"),
    "305678901": ("FARG'ONA SAVDO MChJ", "buyer", "00525", "20208000800555666001"),
    "200999001": ("DAVLAT G'AZNACHILIGI (SOLIQ TO'LOVLARI)", "tax", "00014", "23402000300100001010"),
    "207000001": ("ATB \"NAMUNA BANK\"", "bank", "00873", "16103000900000873001"),
    "306789012": ("BUXORO OIL MChJ", "buyer", "00579", "20208000900666777001"),
    "307890123": ("ANDIJON PLAST MChJ", "supplier", "00546", "20208001000777888001"),
    "308901234": ("NAVOIY KABEL MChJ", "supplier", "00603", "20208001100888999001"),
}

ACCOUNTS = {  # 1C Hisoblar rejasi kodi -> nomi (O'zbekiston BHMS 21)
    "6010": "Mol yetkazib beruvchilar va pudratchilarga to'lanadigan schyotlar",
    "4010": "Xaridorlar va buyurtmachilardan olinadigan schyotlar",
    "6410": "Byudjetga to'lovlar bo'yicha qarz",
    "9420": "Ma'muriy xarajatlar",
    "5110": "Hisob-kitob schyoti",
}

CASH_FLOW_ITEMS = {
    "supplier": "Оплата поставщикам",
    "buyer": "Поступления от покупателей",
    "tax": "Уплата налогов и сборов",
    "bank": "Прочие расходы (комиссия банка)",
}

ROLE_PATTERN = {  # rol -> (ВидОперации chiqim, ВидОперации kirim, schyot, СтатьяДДС)
    "supplier": ("ОплатаПоставщику", "ВозвратДенежныхСредствПоставщиком", "6010", "supplier"),
    "buyer": ("ВозвратДенежныхСредствПокупателю", "ОплатаОтПокупателя", "4010", "buyer"),
    "tax": ("ПеречислениеНалога", "ПрочееПоступление", "6410", "tax"),
    "bank": ("ПрочееСписание", "ПрочееПоступление", "9420", "bank"),
}

NOMENCLATURE = {
    "Sement M400 (50 kg)": "02523001001000000",
    "Armatura A500C d12": "07214001001000000",
    "Polietilen quvur PE100 d110": "03917001001000000",
    "Kabel VVG 3x2.5": "08544001001000000",
    "Yuk tashish xizmati": "04933001001000000",
}

# ---------------------------------------------------------------- Namuna vipiska (8 qator)
# (sana, №, yo'nalish, summa, STIR, to'lov maqsadi, demo-1C dagi holat)
SAMPLE_TRANSACTIONS = [
    (date(2026, 10, 1), "112", "kirim", 45_000_000.00, "301234567",
     "17-sonli shartnoma bo'yicha mahsulot uchun to'lov, QQS 12% bilan", "dup"),
    (date(2026, 10, 2), "45", "chiqim", 12_500_000.00, "302345678",
     "8-sonli shartnoma bo'yicha transport xizmatlari uchun to'lov, QQS bilan", "dup"),
    (date(2026, 10, 3), "118", "kirim", 28_750_000.00, "303456789",
     "05/26-sonli shartnoma bo'yicha qishloq xo'jaligi mahsulotlari uchun", "dup"),
    (date(2026, 10, 5), "124", "kirim", 19_500_000.00, "306789012",
     "11-sonli shartnoma bo'yicha moylash materiallari uchun to'lov", "diff"),
    (date(2026, 10, 14), "52", "chiqim", 67_800_000.00, "304567890",
     "23-sonli shartnoma bo'yicha qurilish materiallari uchun oldindan to'lov, QQS 12% bilan", "new"),
    (date(2026, 10, 14), "131", "kirim", 33_400_000.00, "305678901",
     "14-sonli shartnoma bo'yicha sotilgan tovarlar uchun to'lov, QQS bilan", "new"),
    (date(2026, 10, 15), "53", "chiqim", 8_450_000.00, "200999001",
     "QQS bo'yicha 2026-yil sentyabr oyi uchun to'lov (soliq)", "new"),
    (date(2026, 10, 15), "54", "chiqim", 125_000.00, "207000001",
     "Bank xizmatlari uchun komissiya, 2026-yil oktyabr", "new"),
]
DIFF_AMOUNT_IN_1C = 19_050_000.00  # 124-son: 1C da boshqa summa

# ---------------------------------------------------------------- Didox EHF (kiruvchi hisob-fakturalar)
DIDOX_INVOICES = [
    {"id": "dx-1", "number": "TQM-0912", "date": "2026-09-12", "seller_inn": "304567890", "total": 56_000_000.00,
     "kind": "Товары", "items": [("Sement M400 (50 kg)", 1000, 50_000.00)], "in_1c": "dup"},
    {"id": "dx-2", "number": "OL-0930", "date": "2026-09-30", "seller_inn": "302345678", "total": 12_500_000.00,
     "kind": "Услуги", "items": [("Yuk tashish xizmati", 1, 11_160_714.29)], "in_1c": "dup"},
    {"id": "dx-3", "number": "AP-1007", "date": "2026-10-07", "seller_inn": "307890123", "total": 23_520_000.00,
     "kind": "Товары", "items": [("Polietilen quvur PE100 d110", 300, 70_000.00)], "in_1c": "new"},
    {"id": "dx-4", "number": "NK-1010", "date": "2026-10-10", "seller_inn": "308901234", "total": 41_440_000.00,
     "kind": "Товары", "items": [("Kabel VVG 3x2.5", 2000, 18_500.00)], "in_1c": "new"},
    {"id": "dx-5", "number": "TQM-1013", "date": "2026-10-13", "seller_inn": "304567890", "total": 67_800_000.00,
     "kind": "Товары", "items": [("Armatura A500C d12", 7000, 8_649.00)], "in_1c": "diff",
     "amount_in_1c": 67_200_000.00},
]


def _dt(d: date | str, hh: int = 12) -> str:
    d = d.isoformat() if isinstance(d, date) else d
    return f"{d}T{hh:02d}:00:00"


def seed_1c() -> dict[str, list[dict]]:
    """Demo 1C bazasi — OData entity set nomlari bo'yicha."""
    db: dict[str, list[dict]] = {k: [] for k in (
        "Catalog_Организации", "Catalog_Контрагенты", "Catalog_ДоговорыКонтрагентов", "Catalog_БанковскиеСчета",
        "Catalog_Номенклатура", "Catalog_СтатьиДвиженияДенежныхСредств", "ChartOfAccounts_Хозрасчетный",
        "Document_ПоступлениеНаРасчетныйСчет", "Document_СписаниеСРасчетногоСчета", "Document_ПоступлениеТоваровУслуг")}

    org_key = gid("org", ORG["inn"])
    db["Catalog_Организации"].append({"Ref_Key": org_key, "Description": ORG["name"], "ИНН": ORG["inn"],
                                      "DeletionMark": False})
    org_acc_key = gid("bank", ORG["account"])
    db["Catalog_БанковскиеСчета"].append({
        "Ref_Key": org_acc_key, "Owner": org_key, "Owner_Type": "StandardODATA.Catalog_Организации",
        "НомерСчета": ORG["account"], "Description": "Asosiy hisob (ATB NAMUNA BANK)", "DeletionMark": False})

    for code, name in ACCOUNTS.items():
        db["ChartOfAccounts_Хозрасчетный"].append({"Ref_Key": gid("acc", code), "Code": code, "Description": name})
    for role, name in CASH_FLOW_ITEMS.items():
        db["Catalog_СтатьиДвиженияДенежныхСредств"].append({"Ref_Key": gid("cfi", role), "Description": name,
                                                            "DeletionMark": False})
    for name, ikpu in NOMENCLATURE.items():
        db["Catalog_Номенклатура"].append({"Ref_Key": gid("nom", name), "Description": name, "КодИКПУ": ikpu,
                                           "DeletionMark": False})

    for inn, (name, role, mfo, acc) in COUNTERPARTIES.items():
        k = gid("kontr", inn)
        db["Catalog_Контрагенты"].append({"Ref_Key": k, "Description": name, "НаименованиеПолное": name,
                                          "ИНН": inn, "DeletionMark": False})
        db["Catalog_БанковскиеСчета"].append({
            "Ref_Key": gid("bank", acc), "Owner": k, "Owner_Type": "StandardODATA.Catalog_Контрагенты",
            "НомерСчета": acc, "Description": f"{name} ({mfo})", "DeletionMark": False})
        kind = {"supplier": "СПоставщиком", "buyer": "СПокупателем"}.get(role, "Прочее")
        db["Catalog_ДоговорыКонтрагентов"].append({
            "Ref_Key": gid("contract", inn), "Owner_Key": k, "Description": f"Asosiy shartnoma ({name})",
            "ВидДоговора": kind, "DeletionMark": False})

    counter = {"n": 100}

    def bank_doc(inn: str, direction: str, d: date | str, number: str, amount: float, purpose: str,
                 posted: bool = True) -> None:
        name, role, _mfo, acc = COUNTERPARTIES[inn]
        op_out, op_in, acc_code, cfi_role = ROLE_PATTERN[role]
        entity = "Document_СписаниеСРасчетногоСчета" if direction == "chiqim" else "Document_ПоступлениеНаРасчетныйСчет"
        counter["n"] += 1
        acc_key, cfi_key, contract_key = gid("acc", acc_code), gid("cfi", cfi_role), gid("contract", inn)
        db[entity].append({
            "Ref_Key": gid("doc", entity, number, d, inn), "Number": f"NT00-{counter['n']:06d}", "Date": _dt(d, 10),
            "Posted": posted, "DeletionMark": False, "Организация_Key": org_key, "СчетОрганизации_Key": org_acc_key,
            "ВидОперации": op_out if direction == "chiqim" else op_in,
            "Контрагент_Key": gid("kontr", inn), "ДоговорКонтрагента_Key": contract_key,
            "СчетКонтрагента_Key": gid("bank", acc), "СуммаДокумента": amount,
            "НомерВходящегоДокумента": number, "ДатаВходящегоДокумента": _dt(d, 0),
            "НазначениеПлатежа": purpose, "СчетУчетаРасчетовСКонтрагентом_Key": acc_key,
            "СтатьяДвиженияДенежныхСредств_Key": cfi_key,
            "РасшифровкаПлатежа": [{"LineNumber": "1", "ДоговорКонтрагента_Key": contract_key, "СуммаПлатежа": amount,
                                    "СчетУчетаРасчетовСКонтрагентом_Key": acc_key,
                                    "СтатьяДвиженияДенежныхСредств_Key": cfi_key}],
            "Комментарий": "Buxgalter tomonidan kiritilgan",
        })

    # --- Buxgalterning oldingi yozuvlari (tarixdan o'rganish uchun) — avgust-sentyabr 2026
    history = [
        ("301234567", "kirim", [("2026-08-11", "87", 38_000_000), ("2026-09-04", "96", 41_200_000)]),
        ("302345678", "chiqim", [("2026-08-20", "31", 11_000_000), ("2026-09-18", "39", 12_500_000)]),
        ("303456789", "kirim", [("2026-08-27", "91", 22_100_000), ("2026-09-22", "104", 26_300_000)]),
        ("304567890", "chiqim", [("2026-08-05", "27", 54_300_000), ("2026-09-02", "34", 61_000_000),
                                 ("2026-09-25", "41", 48_900_000)]),
        ("305678901", "kirim", [("2026-08-14", "89", 29_000_000), ("2026-09-11", "99", 31_700_000),
                                ("2026-09-29", "107", 27_450_000)]),
        ("200999001", "chiqim", [("2026-08-15", "29", 7_980_000), ("2026-09-15", "37", 8_210_000)]),
        ("207000001", "chiqim", [("2026-08-31", "33", 118_000), ("2026-09-30", "43", 121_500)]),
        ("306789012", "kirim", [("2026-09-09", "98", 17_800_000)]),
    ]
    purposes = {
        "supplier": "Shartnoma bo'yicha to'lov, QQS bilan", "buyer": "Shartnoma bo'yicha tovar uchun to'lov",
        "tax": "QQS bo'yicha to'lov (soliq)", "bank": "Bank xizmatlari uchun komissiya",
    }
    for inn, direction, docs in history:
        for d, number, amount in docs:
            bank_doc(inn, direction, d, number, float(amount), purposes[COUNTERPARTIES[inn][1]])

    # --- Vipiskaning 1C da allaqachon bor qatorlari (dublikat) va farqli qatori
    for d, number, direction, amount, inn, purpose, state in SAMPLE_TRANSACTIONS:
        if state == "dup":
            bank_doc(inn, direction, d, number, amount, purpose)
        elif state == "diff":
            bank_doc(inn, direction, d, number, DIFF_AMOUNT_IN_1C, purpose)

    # --- Kiruvchi hisob-fakturalar (ПоступлениеТоваровУслуг): tarix + Didox dublikatlari
    def purchase_doc(inn: str, d: str, number: str, amount: float, kind: str, item: str) -> None:
        counter["n"] += 1
        qty = 1 if kind == "Услуги" else 100
        net = round(amount / 1.12, 2)
        db["Document_ПоступлениеТоваровУслуг"].append({
            "Ref_Key": gid("purchase", number, inn), "Number": f"NT00-{counter['n']:06d}", "Date": _dt(d, 11),
            "Posted": True, "DeletionMark": False, "Организация_Key": org_key, "ВидОперации": kind,
            "Контрагент_Key": gid("kontr", inn), "ДоговорКонтрагента_Key": gid("contract", inn),
            "СуммаДокумента": amount, "НомерВходящегоДокумента": number, "ДатаВходящегоДокумента": _dt(d, 0),
            "СчетУчетаРасчетовСКонтрагентом_Key": gid("acc", "6010"), "СуммаВключаетНДС": True,
            "Товары": [{"LineNumber": "1", "Номенклатура_Key": gid("nom", item), "Количество": qty,
                        "Цена": round(net / qty, 2), "Сумма": amount, "СтавкаНДС": "НДС12",
                        "СуммаНДС": round(amount - net, 2)}],
        })

    for inn, d, number, amount, kind, item in [
        ("304567890", "2026-08-03", "TQM-0803", 54_300_000.0, "Товары", "Sement M400 (50 kg)"),
        ("307890123", "2026-09-08", "AP-0908", 19_600_000.0, "Товары", "Polietilen quvur PE100 d110"),
        ("307890123", "2026-09-26", "AP-0926", 21_840_000.0, "Товары", "Polietilen quvur PE100 d110"),
        ("308901234", "2026-09-16", "NK-0916", 36_960_000.0, "Товары", "Kabel VVG 3x2.5"),
        ("302345678", "2026-08-31", "OL-0831", 11_000_000.0, "Услуги", "Yuk tashish xizmati"),
    ]:
        purchase_doc(inn, d, number, amount, kind, item)
    for inv in DIDOX_INVOICES:
        if inv["in_1c"] == "dup":
            purchase_doc(inv["seller_inn"], inv["date"], inv["number"], inv["total"], inv["kind"], inv["items"][0][0])
        elif inv["in_1c"] == "diff":
            purchase_doc(inv["seller_inn"], inv["date"], inv["number"], inv["amount_in_1c"], inv["kind"],
                         inv["items"][0][0])
    return db


def build_sample_xlsx(path) -> None:
    """Namunaviy bank vipiskasi (O'zbekiston banklari Excel eksporti ko'rinishida)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Vipiska"
    ws["A1"] = "ATB \"NAMUNA BANK\" — Hisob raqamidan ko'chirma (vipiska)"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"Mijoz: {ORG['name']}   STIR: {ORG['inn']}"
    ws["A3"] = f"Hisob raqami: {ORG['account']}   MFO: {ORG['mfo']}   Valyuta: UZS"
    ws["A4"] = "Davr: 01.10.2026 — 15.10.2026"
    headers = ["Sana", "Hujjat №", "Debet (Chiqim)", "Kredit (Kirim)", "Kontragent nomi", "Kontragent STIR",
               "MFO", "Hisob raqami", "To'lov maqsadi"]
    ws.append([])
    ws.append(headers)
    hdr_row = ws.max_row
    thin = Side(style="thin", color="BBBBBB")
    for col, _ in enumerate(headers, 1):
        c = ws.cell(row=hdr_row, column=col)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = Border(top=thin, bottom=thin, left=thin, right=thin)
    total_d = total_k = 0.0
    for d, number, direction, amount, inn, purpose, _state in SAMPLE_TRANSACTIONS:
        name, _role, mfo, acc = COUNTERPARTIES[inn]
        debit, credit = (amount, None) if direction == "chiqim" else (None, amount)
        total_d += debit or 0
        total_k += credit or 0
        ws.append([d, number, debit, credit, name, inn, mfo, acc, purpose])
        r = ws.max_row
        ws.cell(row=r, column=1).number_format = "DD.MM.YYYY"
        for col in (3, 4):
            ws.cell(row=r, column=col).number_format = "#,##0.00"
        for col in (6, 7, 8):
            ws.cell(row=r, column=col).number_format = "@"
    ws.append(["Jami", None, total_d, total_k])
    r = ws.max_row
    for col in (1, 3, 4):
        ws.cell(row=r, column=col).font = Font(bold=True)
    for col in (3, 4):
        ws.cell(row=r, column=col).number_format = "#,##0.00"
    for col, width in zip("ABCDEFGHI", (12, 10, 18, 18, 40, 14, 8, 24, 70)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = ws.cell(row=hdr_row + 1, column=1)
    wb.save(path)
