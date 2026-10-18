"""📦 Notion'dagi sklad bazasi: o'qish, 1C qoldiqlari bilan solishtirish.

Ikki manba:
  • jonli — firmaning Notion integratsiya tokeni va baza ID si bo'lsa, Notion API dan o'qiladi (faqat o'qish);
  • nusxa (snapshot) — data/notion_stock/<firma>.json: oxirgi jonli o'qish yoki Claude yuklagan holat.
    Jonli o'qib bo'lmasa, nusxa ko'rsatiladi (sanasi bilan).

Ikkala manba ham bir xil "oddiy qator" ko'rinishiga keltiriladi: {"Tovar": .., "Miqdor": .., "Birlik": .., ...}
— Notion ustun nomlari bilan. Shuning uchun nusxani Notion'dan qo'lda olingan jadvaldan ham yasash mumkin.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx

import config

log = logging.getLogger("buxgalter.notion")

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
TIMEOUT = 20.0
MAX_PAGES = 20  # 20 × 100 qator — sklad uchun yetarli, cheksiz aylanishdan himoya
SNAP_DIR = config.DATA_DIR / "notion_stock"
PAIR = 0.6  # miqdori aynan teng bo'lsa, nomlar shunchalik o'xshash bo'lishi yetarli (bitta tovar deb olinadi)
ALIAS_RE = re.compile(r"1C\s*(?:nomi|name|наименование)\s*[:\-–]?\s*['\"«“‘]([^'\"»”’]{2,})['\"»”’]", re.I)
SIMILAR = 0.75 # nomlar o'xshashligi (0..1): shundan yuqorisi "o'xshash tovar" deb taklif qilinadi

# Notion ustun nomlari (birinchi topilgani olinadi)
COLUMNS = {
    "qty": ("Miqdor", "Qoldiq", "Количество", "Остаток"),
    "unit": ("Birlik", "Ед. изм.", "Единица"),
    "price_in": ("Kirim narxi NDSsiz", "Kirim narxi", "Себестоимость"),
    "price_out": ("Sotuv narxi NDSsiz",),
    "price_out_vat": ("Sotuv narxi NDS bilan", "Sotuv narxi"),
    "kind": ("Turi",),
    "reserved_for": ("Band kimga",),
    "batch": ("Partiya/manba", "Partiya"),
    "note": ("Izoh", "Комментарий"),
}
TITLE_KEYS = ("Tovar", "Name", "Nomi", "Наименование")

UNITS = {"m": "m", "м": "m", "метр": "m", "metr": "m", "пм": "m", "п.м": "m",
         "dona": "dona", "шт": "dona", "штук": "dona", "штука": "dona", "pcs": "dona",
         "kg": "kg", "кг": "kg", "t": "t", "т": "t", "тн": "t", "тонна": "t", "tonna": "t",
         "juft": "juft", "пара": "juft", "пар": "juft", "to'plam": "to'plam", "компл": "to'plam",
         "комплект": "to'plam", "кмп": "to'plam"}
# lotincha harflar kirillchaga o'xshash — nomlarni solishtirishda bir xil deb olinadi
LOOKALIKE = str.maketrans({"a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "o": "о",
                           "p": "р", "t": "т", "x": "х", "y": "у", "ё": "е"})


class NotionError(Exception):
    pass


# ---------------------------------------------------------------- normallash
def unit_of(value: str) -> str:
    v = str(value or "").strip().lower().rstrip(".")
    return UNITS.get(v, v)


def name_key(name: str) -> str:
    """Solishtirish kaliti. Farq qilmaydi: katta-kichik harf, lotin/kirill o'xshash harflar, bo'shliq va tire,
    qavs ichidagi izohlar ("(OSIYO)", "(29.09 partiya)", "(А)"), "кабель" so'zi, kuchlanish ("-0,66 кВ", "10кВ"),
    izolyatsiya belgisi ("ож"/"ок"), "мм²", "*" ↔ "х". Kesim raqamlari (2,5 ≠ 25) saqlanadi."""
    s = str(name or "").lower().replace("ё", "е")
    s = re.sub(r"\s*\((а|a)\)", "а", s)  # "нг(А)" — marka belgisi (ВВГнг ≠ ВВГнг(А)), izoh emas
    s = re.sub(r"\([^)]*\)", " ", s)
    s = s.translate(LOOKALIKE).replace(",", ".").replace("*", "х")
    s = re.sub(r"(^|\s)(кабель|кабел|каб\.)(?=\s)", " ", s)
    s = re.sub(r"-?\s*\d*[.]?\d+(/\d+[.]\d+)?\s*кв\b", " ", s)          # "-0.66 кв", "10кв", "0.66/1.0кв"
    s = re.sub(r"(^|[\s-])0\.66(/1\.0)?(?=\s|$)", " ", s)                   # "-0.66" (кВ siz)
    s = re.sub(r"(?<=\d)\s*(ок|ож)\b|\b(ок|ож)\b", " ", s)
    s = re.sub(r"мм2|мм²|мм\b", " ", s)
    return re.sub(r"[\s\-_\"'«»]+", "", s)


def _digits(name: str) -> str:
    """Nomdagi raqamlar ketma-ketligi (kesim, o'lcham): "Сип 3*50+1*54,6" va "СИП 350+154,6" — bir xil."""
    s = re.sub(r"\([^)]*\)", " ", str(name or ""))
    s = re.sub(r"-?\s*\d*[.,]?\d+(/\d+[.,]\d+)?\s*[кk][вv]", " ", s.lower())
    return "".join(re.findall(r"\d", s))


def _num(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _pick(row: dict, names) -> object:
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def _title(row: dict) -> str:
    v = _pick(row, TITLE_KEYS)
    return str(v).strip() if v else ""


def _plain(prop: dict):
    """Notion API xususiyati → oddiy qiymat."""
    t = prop.get("type")
    v = prop.get(t)
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in v or [])
    if t in ("select", "status"):
        return (v or {}).get("name", "")
    if t == "multi_select":
        return ", ".join(x.get("name", "") for x in v or [])
    if t == "formula":
        return (v or {}).get((v or {}).get("type"))
    if t == "rollup":
        return (v or {}).get("number")
    if t == "date":
        return (v or {}).get("start")
    return v


def page_to_row(page: dict) -> dict:
    row = {name: _plain(p) for name, p in (page.get("properties") or {}).items()}
    title = next((name for name, p in (page.get("properties") or {}).items() if p.get("type") == "title"), None)
    if title and title not in TITLE_KEYS:
        row["Tovar"] = row[title]
    row["url"] = page.get("url", "")
    row["edited"] = page.get("last_edited_time", "")
    return row


def item_of(row: dict) -> dict | None:
    name = _title(row)
    if not name:
        return None
    qty = _num(_pick(row, COLUMNS["qty"])) or 0.0
    price = _num(_pick(row, COLUMNS["price_in"]))
    return {"name": name, "qty": round(qty, 3), "unit": unit_of(_pick(row, COLUMNS["unit"]) or ""),
            "price": price, "sum": round(qty * price, 2) if price is not None else None,
            "price_out": _num(_pick(row, COLUMNS["price_out"])),
            "price_out_vat": _num(_pick(row, COLUMNS["price_out_vat"])),
            "kind": str(_pick(row, COLUMNS["kind"]) or "Erkin"),
            "reserved_for": str(_pick(row, COLUMNS["reserved_for"]) or ""),
            "batch": str(_pick(row, COLUMNS["batch"]) or ""), "note": str(_pick(row, COLUMNS["note"]) or ""),
            "url": row.get("url", ""), "edited": row.get("edited", "")}


# ---------------------------------------------------------------- nusxa (snapshot)
def _snap_file(firm_id: int) -> Path:
    return SNAP_DIR / f"{int(firm_id)}.json"


def save_snapshot(firm_id: int, rows: list[dict], source: str) -> dict:
    data = {"fetched_at": datetime.now().isoformat(timespec="seconds"), "source": source,
            "rows": [r for r in rows if _title(r)]}
    f = _snap_file(firm_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)
    return data


def load_snapshot(firm_id: int) -> dict | None:
    f = _snap_file(firm_id)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("Notion nusxasini o'qib bo'lmadi: %s", f)
        return None


# ---------------------------------------------------------------- jonli o'qish
async def fetch_rows(token: str, database_id: str, client: httpx.AsyncClient | None = None) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": VERSION, "Content-Type": "application/json"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    rows, cursor = [], None
    try:
        for _ in range(MAX_PAGES):
            body = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
            try:
                r = await client.post(f"{API}/databases/{database_id}/query", headers=headers, json=body)
            except httpx.HTTPError as e:
                raise NotionError(f"Notion bilan aloqa yo'q: {e.__class__.__name__}") from e
            if r.status_code == 401:
                raise NotionError("Notion tokeni noto'g'ri yoki bekor qilingan (401)")
            if r.status_code == 404:
                raise NotionError("Notion bazasi topilmadi (404): baza ID sini tekshiring va bazani integratsiyaga "
                                  "ulang (bazada ••• → Connections → integratsiya nomi)")
            if r.status_code >= 400:
                raise NotionError(f"Notion xatosi ({r.status_code}): {r.text[:200]}")
            data = r.json()
            rows += [page_to_row(p) for p in data.get("results", []) if not p.get("archived")]
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
    finally:
        if own:
            await client.aclose()
    return rows


async def stock(firm: dict, client: httpx.AsyncClient | None = None) -> dict:
    """Notion skladi: jonli (token bo'lsa) yoki oxirgi nusxa. Hech biri bo'lmasa — configured=False."""
    token, db_id = (firm.get("notion_token") or "").strip(), (firm.get("notion_stock_db") or "").strip()
    warning, snap = None, None
    if token and db_id:
        try:
            snap = save_snapshot(firm["id"], await fetch_rows(token, db_id, client), "api")
            snap["live"] = True
        except NotionError as e:
            warning = str(e)
            log.warning("Notion sklad: %s", e)
    if snap is None:
        snap = load_snapshot(firm["id"])
        if snap:
            snap["live"] = False
    if snap is None:
        return {"configured": bool(token and db_id), "error": warning or "Notion sklad bazasi ulanmagan", "groups": [],
                "total": 0, "count": 0, "token": bool(token and db_id)}
    return {**build(snap["rows"]), "configured": True, "live": snap["live"], "fetched_at": snap["fetched_at"],
            "snapshot_source": snap.get("source", ""), "warning": warning, "token": bool(token and db_id)}


def build(rows: list[dict]) -> dict:
    items = [i for i in (item_of(r) for r in rows) if i]
    groups = []
    for kind in sorted({i["kind"] for i in items}, key=lambda k: (k != "Erkin", k)):
        its = sorted((i for i in items if i["kind"] == kind and abs(i["qty"]) >= 0.001),
                     key=lambda i: -(i["sum"] or 0))
        if its:
            groups.append({"title": kind, "items": its, "total": round(sum(i["sum"] or 0 for i in its), 2)})
    return {"groups": groups, "total": round(sum(g["total"] for g in groups), 2),
            "count": sum(len(g["items"]) for g in groups),
            "sold_out": sum(1 for i in items if abs(i["qty"]) < 0.001), "_items": items}


# ---------------------------------------------------------------- solishtirish
def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, name_key(a), name_key(b)).ratio()


def _pair_same_qty(rows: list[dict]) -> None:
    """Nomi boshqacha yozilgan, lekin yetishmayotgan miqdori aynan teng va nomi o'xshash Notion qatorini 1C qatoriga
    qo'shadi ("СИП 4 4х50" 1C da 1200 ↔ "СИП 4Х50" Notion'da 1200). Belgilanadi: paired=True ("nomi boshqa")."""
    for r in sorted((r for r in rows if r["state"] in ("only_1c", "diff")), key=lambda r: -abs(r["diff"])):
        need = round(r["qty_1c"] - (r["qty_notion"] or 0), 3)
        if abs(need) < 0.001:
            continue
        cands = [n for n in rows if n["state"] == "only_notion" and abs(n["qty_notion"] - need) < 0.001
                 and (not n["unit"] or not r["unit"] or n["unit"] == r["unit"])]
        if not cands:
            continue
        best = max(cands, key=lambda n: _ratio(r["name"], n["name"]))
        if _ratio(r["name"], best["name"]) < PAIR:
            continue
        empty = not r["qty_notion"]
        r.update(qty_notion=round((r["qty_notion"] or 0) + best["qty_notion"], 3), diff=0.0, state="ok", paired=True,
                 notion_name=best["name"] if empty else f"{r['notion_name']} + {best['name']}",
                 url=best["url"] if empty or not r["url"] else r["url"],
                 note=best["note"] if empty or not r["note"] else r["note"])
        rows.remove(best)


def compare(onec: dict, notion: dict) -> dict:
    """1C va Notion qoldiqlari tovar nomi bo'yicha: mos / farq / faqat 1C da / faqat Notion'da."""
    a: dict[str, dict] = {}
    for g in onec.get("groups", []):
        for it in g["items"]:
            x = a.setdefault(name_key(it["name"]), {"name": it["name"], "unit": unit_of(it.get("unit")), "qty": 0.0})
            x["qty"] = round(x["qty"] + (it.get("qty") or 0), 3)
    b: dict[str, dict] = {}
    for it in notion.get("_items") or [i for g in notion.get("groups", []) for i in g["items"]]:
        alias = ALIAS_RE.search(it.get("note") or "")  # Izohda "1C nomi: '…'" — 1C dagi nomi bilan bog'lash
        key = name_key(alias.group(1)) if alias else name_key(it["name"])
        x = b.setdefault(key, {"name": it["name"], "unit": it["unit"], "qty": 0.0, "url": it["url"],
                               "note": it["note"]})
        x["qty"] = round(x["qty"] + it["qty"], 3)
    rows = []
    for k in a.keys() | b.keys():
        x, y = a.get(k), b.get(k)
        q1, q2 = (x or {}).get("qty", 0.0), (y or {}).get("qty", 0.0)
        if abs(q1) < 0.001 and abs(q2) < 0.001:
            continue
        diff = round(q2 - q1, 3)
        state = ("only_notion" if not x else "only_1c" if not y else "ok" if abs(diff) < 0.001 else "diff")
        unit_mismatch = bool(x and y and x["unit"] and y["unit"] and x["unit"] != y["unit"])
        rows.append({"name": (x or y)["name"], "notion_name": (y or {}).get("name", ""), "unit": (y or x)["unit"],
                     "qty_1c": q1 if x else None, "qty_notion": q2 if y else None, "diff": diff, "state": state,
                     "unit_mismatch": unit_mismatch, "url": (y or {}).get("url", ""), "note": (y or {}).get("note", "")})
    _pair_same_qty(rows)
    # nomi sal boshqacha yozilgan bo'lishi mumkin — birlashtirmaymiz, faqat "o'xshash" deb ko'rsatamiz
    lonely = [r for r in rows if r["state"] == "only_notion"]
    for r in rows:
        if r["state"] != "only_1c" or not lonely:
            continue
        k = name_key(r["name"])
        same_digits = [n for n in lonely if _digits(n["name"]) == _digits(r["name"])]  # 2,5 va 1,5 — boshqa tovar
        if not same_digits:
            continue
        best = max(same_digits, key=lambda n: difflib.SequenceMatcher(None, k, name_key(n["name"])).ratio())
        ratio = difflib.SequenceMatcher(None, k, name_key(best["name"])).ratio()
        if ratio >= SIMILAR:
            r["similar"] = {"name": best["name"], "qty": best["qty_notion"], "url": best["url"],
                            "same_qty": abs((best["qty_notion"] or 0) - r["qty_1c"]) < 0.001}
    order = {"diff": 0, "only_1c": 1, "only_notion": 2, "ok": 3}
    rows.sort(key=lambda r: (order[r["state"]], -abs(r["diff"]), r["name"].lower()))
    counts = {s: sum(r["state"] == s for r in rows) for s in order}
    return {"rows": rows, "counts": counts}


# ---------------------------------------------------------------- CLI: Claude/qo'lda nusxa yuklash
if __name__ == "__main__":  # python services_notion.py <firma_id> <rows.json>
    firm_id, path = int(sys.argv[1]), Path(sys.argv[2])
    raw = json.loads(path.read_text(encoding="utf-8"))
    saved = save_snapshot(firm_id, raw["results"] if isinstance(raw, dict) else raw, "claude")
    print(f"{len(saved['rows'])} qator saqlandi → {_snap_file(firm_id)}")
