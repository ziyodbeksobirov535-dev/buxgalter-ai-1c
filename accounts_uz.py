"""O'zbekiston milliy hisoblar rejasi (21-son BHMS) — dastur ishlatadigan asosiy schyotlar.

Har bir schyot: kodi, o'zbekcha nomi, 1C dagi ruscha nomi, guruh va standart ВидОперации
(bank chiqimi / kirimi uchun). 1C dagi haqiqiy ishlatilishi baribir buxgalterning 1C tarixidan o'rganiladi —
bu ro'yxat tarix bo'lmaganda standart taxmin va tanlash uchun.
"""
from __future__ import annotations

GROUPS = [
    ("cash", "💵 Pul mablag'lari, bank, kassa, karta"),
    ("settlements", "🤝 Xaridorlar va yetkazib beruvchilar"),
    ("taxes", "🧾 Soliqlar va budjet"),
    ("staff", "👥 Ish haqi, dividend, kreditlar"),
    ("inventory", "📦 TMZ va asosiy vositalar"),
    ("pnl", "📊 Daromad va xarajatlar"),
]

# kod: (guruh, o'zbekcha nomi, 1C dagi nomi, ВидОперации chiqimda, ВидОперации kirimda)
UZ_ACCOUNTS: dict[str, tuple[str, str, str, str, str]] = {
    "50.10": ("cash", "Milliy valyutadagi kassa", "Касса в национальной валюте", "СнятиеНаличных", "ВзносНаличными"),
    "51.10": ("cash", "Hisob-kitob schyoti", "Расчетный счет", "ПереводНаДругойСчет", "ПереводСДругогоСчета"),
    "52.10": ("cash", "Valyuta schyoti", "Валютные счета", "ПереводНаДругойСчет", "ПереводСДругогоСчета"),
    "55.20": ("cash", "Korporativ plastik karta (KPK)", "Корпоративные пластиковые карты",
              "ПереводНаДругойСчет", "ПереводСДругогоСчета"),
    "57.10": ("cash", "Yo'ldagi pul o'tkazmalari (terminal, inkassatsiya)", "Переводы в пути (эквайринг)",
              "ПрочееСписание", "ПоступленияОтПродажПоПлатежнымКартам"),

    "40.10": ("settlements", "Xaridorlardan olinadigan schyotlar", "Счета к получению от покупателей",
              "ВозвратДенежныхСредствПокупателю", "ОплатаОтПокупателя"),
    "63.10": ("settlements", "Xaridorlardan olingan avanslar (bo'nak)", "Авансы полученные от покупателей",
              "ВозвратДенежныхСредствПокупателю", "ОплатаОтПокупателя"),
    "60.10": ("settlements", "Mol yetkazib beruvchilarga to'lanadigan schyotlar", "Счета к оплате поставщикам",
              "ОплатаПоставщику", "ВозвратДенежныхСредствПоставщиком"),
    "43.10": ("settlements", "Yetkazib beruvchilarga berilgan avanslar (bo'nak)", "Авансы выданные поставщикам",
              "ОплатаПоставщику", "ВозвратДенежныхСредствПоставщиком"),
    "48.90": ("settlements", "Turli debitorlar", "Прочие дебиторы", "ПрочиеРасчетыСКонтрагентами",
              "ПрочиеРасчетыСКонтрагентами"),
    "69.90": ("settlements", "Turli kreditorlar", "Прочие кредиторы", "ПрочиеРасчетыСКонтрагентами",
              "ПрочиеРасчетыСКонтрагентами"),

    "64.10": ("taxes", "Budjetga soliqlar: QQS, JShDS (kod 46), foyda, aylanma, mulk, yer",
              "Задолженность по платежам в бюджет", "ПеречислениеНалога", "ПрочееПоступление"),
    "44.10": ("taxes", "Hisobga olinadigan QQS / budjetga avans to'lovlar", "НДС к зачету / авансовые платежи в бюджет",
              "ПеречислениеНалога", "ПрочееПоступление"),
    "65.20": ("taxes", "Ijtimoiy soliq (kod 36) va pensiya badallari / INPS (kod 101)",
              "Платежи в государственные целевые фонды", "ПеречислениеНалога", "ПрочееПоступление"),
    "65.30": ("taxes", "Jamg'arib boriladigan pensiya badali — INPS (agar 1C rejasida alohida bo'lsa)",
              "Накопительные пенсионные взносы (ИНПС)", "ПеречислениеНалога", "ПрочееПоступление"),

    "67.10": ("staff", "Xodimlarga ish haqi (oylik, avans)", "Расчеты с персоналом по оплате труда",
              "ПеречислениеЗаработнойПлаты", "ПрочееПоступление"),
    "42.20": ("staff", "Hisobdor shaxslar (xizmat safari, xo'jalik)", "Авансы выданные подотчетным лицам",
              "ВыдачаПодотчетномуЛицу", "ВозвратПодотчетнымЛицом"),
    "66.10": ("staff", "Ta'sischilarga dividendlar", "Дивиденды к оплате", "ПрочееСписание", "ПрочееПоступление"),
    "68.10": ("staff", "Qisqa muddatli kreditlar, qarzlar, moliyaviy yordam", "Краткосрочные кредиты и займы",
              "РасчетыПоКредитамИЗаймам", "РасчетыПоКредитамИЗаймам"),
    "78.10": ("staff", "Uzoq muddatli kreditlar", "Долгосрочные кредиты и займы",
              "РасчетыПоКредитамИЗаймам", "РасчетыПоКредитамИЗаймам"),

    "10.10": ("inventory", "Xomashyo va materiallar (kanselyariya, xo'jalik)", "Сырье и материалы",
              "ОплатаПоставщику", "ПрочееПоступление"),
    "10.30": ("inventory", "Yoqilg'i (benzin, gaz, dizel)", "Топливо", "ОплатаПоставщику", "ПрочееПоступление"),
    "10.80": ("inventory", "Inventar va xo'jalik jihozlari", "Инвентарь и хозяйственные принадлежности",
              "ОплатаПоставщику", "ПрочееПоступление"),
    "29.10": ("inventory", "Ombordagi tovarlar (qayta sotish uchun)", "Товары на складах",
              "ОплатаПоставщику", "ПрочееПоступление"),
    "08.20": ("inventory", "Asosiy vositalar xaridi", "Приобретение основных средств",
              "ОплатаПоставщику", "ПрочееПоступление"),

    "20.10": ("pnl", "Asosiy ishlab chiqarish xarajatlari", "Основное производство", "ПрочееСписание", "ПрочееПоступление"),
    "90.10": ("pnl", "Tayyor mahsulot sotishdan daromad", "Доход от реализации готовой продукции",
              "ПрочееСписание", "ПрочееПоступление"),
    "90.20": ("pnl", "Tovar sotishdan daromad", "Доход от реализации товаров", "ПрочееСписание", "ПрочееПоступление"),
    "90.30": ("pnl", "Ish va xizmatlardan daromad", "Доход от выполнения работ и услуг",
              "ПрочееСписание", "ПрочееПоступление"),
    "94.10": ("pnl", "Sotish xarajatlari (reklama, yetkazib berish)", "Расходы по реализации",
              "ПрочееСписание", "ПрочееПоступление"),
    "94.20": ("pnl", "Ma'muriy xarajatlar (bank komissiyasi, ijara, aloqa, kommunal)", "Административные расходы",
              "ПрочееСписание", "ПрочееПоступление"),
    "94.30": ("pnl", "Boshqa operatsion xarajatlar (jarima, penya)", "Прочие операционные расходы",
              "ПрочееСписание", "ПрочееПоступление"),
    "96.10": ("pnl", "Kredit foizlari xarajati", "Расходы в виде процентов", "ПрочееСписание", "ПрочееПоступление"),
}

VAT_ACCOUNT = "44.10"  # kiruvchi EHF dagi QQS (12%) — hisobga olinadigan QQS


def title(code: str) -> str:
    acc = UZ_ACCOUNTS.get(code)
    return acc[1] if acc else ""


def operation_for(code: str, direction: str) -> str | None:
    acc = UZ_ACCOUNTS.get(code)
    if not acc:
        return None
    return acc[3] if direction == "chiqim" else acc[4]


def grouped() -> list[dict]:
    """Frontend uchun: guruhlar bo'yicha [{group, label, accounts: [{code, title, title_ru}]}]."""
    out = []
    for gid, label in GROUPS:
        items = [{"code": c, "title": a[1], "title_ru": a[2]} for c, a in UZ_ACCOUNTS.items() if a[0] == gid]
        out.append({"group": gid, "label": label, "accounts": items})
    return out
