"""BUXGALTER AI — ma'lumotlar bazasi (SQLite, data/app.db).

Jadvallar: foydalanuvchilar (rollar bilan), firmalar (har birining o'z 1C/Didox ulanishi), foydalanuvchi↔firma
huquqlari, kirish sessiyalari, login urinishlari cheklovi, Telegram bog'lash kodlari va audit jurnali.

Maxfiy qiymatlar (1C paroli, Didox kalitlari) Fernet bilan shifrlanadi. Kalit: .env dagi APP_SECRET_KEY yoki
data/secret.key (birinchi ishga tushishda yaratiladi). Foydalanuvchi parollari — faqat scrypt xeshi.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken

import config

DB_FILE = config.DATA_DIR / "app.db"
KEY_FILE = config.DATA_DIR / "secret.key"

ROLES = ("owner", "accountant", "viewer")
ROLE_LABELS = {"owner": "Egasi", "accountant": "Buxgalter", "viewer": "Kuzatuvchi"}
SESSION_IDLE = timedelta(hours=8)
SESSION_MAX = timedelta(days=7)
LOGIN_MAX_FAILS = 5
LOGIN_MAX_FAILS_IP = 20
LOGIN_LOCK = timedelta(minutes=15)
LINK_CODE_TTL = timedelta(minutes=10)
MIN_PASSWORD = 8

FIRM_FIELDS = ("name", "inn", "onec_demo_mode", "onec_base_url", "onec_user", "onec_password", "didox_demo_mode",
               "didox_base_url", "didox_user_key", "didox_partner_token", "bank_accounts")
FIRM_SECRETS = ("onec_password", "didox_user_key", "didox_partner_token")
FIRM_BOOLS = ("onec_demo_mode", "didox_demo_mode")
# Maxfiy qiymat qaysi manzil/loginga tegishli: ular o'zgarsa va yangi qiymat berilmasa — eskisi o'chiriladi
FIRM_SECRET_BOUND_TO = {"onec_password": ("onec_base_url", "onec_user"),
                        "didox_user_key": ("didox_base_url",), "didox_partner_token": ("didox_base_url",)}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'accountant', 'viewer')),
    active INTEGER NOT NULL DEFAULT 1,
    telegram_id INTEGER UNIQUE,
    tg_firm_id INTEGER,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS firms (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    inn TEXT NOT NULL DEFAULT '',
    onec_demo_mode INTEGER NOT NULL DEFAULT 1,
    onec_base_url TEXT NOT NULL DEFAULT '',
    onec_user TEXT NOT NULL DEFAULT '',
    onec_password TEXT NOT NULL DEFAULT '',
    didox_demo_mode INTEGER NOT NULL DEFAULT 1,
    didox_base_url TEXT NOT NULL DEFAULT 'https://api-partners.didox.uz',
    didox_user_key TEXT NOT NULL DEFAULT '',
    didox_partner_token TEXT NOT NULL DEFAULT '',
    bank_accounts TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_firms (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    firm_id INTEGER NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, firm_id)
);
CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (
    key TEXT PRIMARY KEY,
    fails INTEGER NOT NULL,
    locked_until TEXT
);
CREATE TABLE IF NOT EXISTS link_codes (
    code_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_rules (
    id INTEGER PRIMARY KEY,
    firm_id INTEGER NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('counterparty', 'keyword')),
    match TEXT NOT NULL,                 -- kontragent STIR yoki kalit so'z (kichik harfda)
    name TEXT NOT NULL DEFAULT '',       -- kontragent nomi (ko'rsatish uchun)
    direction TEXT NOT NULL DEFAULT '',  -- kirim | chiqim | '' (har ikkisi)
    doc_kind TEXT NOT NULL DEFAULT 'bank' CHECK (doc_kind IN ('bank', 'ehf')),
    account TEXT NOT NULL,               -- masalan 60.10 (qarz bo'yicha hisob-kitob schyoti)
    advance_account TEXT NOT NULL DEFAULT '',  -- qarz bo'lmasa: avans schyoti (63.10 / 43.10)
    operation TEXT NOT NULL DEFAULT '',  -- ВидОперации (bo'sh — schyotdan aniqlanadi)
    contract_key TEXT NOT NULL DEFAULT '',
    cash_flow_key TEXT NOT NULL DEFAULT '',
    locked INTEGER NOT NULL DEFAULT 0,   -- 1 = qat'iy qoida (1C tarixidan ustun)
    source TEXT NOT NULL DEFAULT 'history',  -- history | manual | correction | import
    hits INTEGER NOT NULL DEFAULT 0,     -- tarixda shu schyotga olingan hujjatlar soni
    total INTEGER NOT NULL DEFAULT 0,    -- tarixdagi jami hujjatlar
    variants TEXT NOT NULL DEFAULT '[]', -- [{"account", "operation", "count"}]
    last_date TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    UNIQUE (firm_id, kind, match, direction, doc_kind)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    user_id INTEGER,
    firm_id INTEGER,
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}'
);
"""

_lock = threading.RLock()
_fernet_cache: Fernet | None = None


class DBError(ValueError):
    """Foydalanuvchiga ko'rsatiladigan xato (masalan: login band, parol qisqa)."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


@contextmanager
def tx():
    with _lock:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _chmod600(path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------- shifrlash
def _fernet() -> Fernet:
    global _fernet_cache
    if _fernet_cache is None:
        key = os.getenv("APP_SECRET_KEY", "").strip().encode()
        if not key:
            if not KEY_FILE.exists():
                KEY_FILE.write_bytes(Fernet.generate_key())
                _chmod600(KEY_FILE)
            key = KEY_FILE.read_bytes().strip()
        _fernet_cache = Fernet(key)
    return _fernet_cache


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return ""  # kalit almashgan — qiymatni qayta kiritish kerak


# ---------------------------------------------------------------- parollar
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(base64.b64decode(digest)))
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def _check_password_policy(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise DBError(f"Parol kamida {MIN_PASSWORD} belgidan iborat bo'lsin")


# ---------------------------------------------------------------- boshlash
def init() -> None:
    config.DATA_DIR.mkdir(exist_ok=True)
    with tx() as c:
        c.executescript(SCHEMA)
        # eski bazalar uchun ustun qo'shish (migratsiya)
        if "advance_account" not in [r[1] for r in c.execute("PRAGMA table_info(account_rules)")]:
            c.execute("ALTER TABLE account_rules ADD COLUMN advance_account TEXT NOT NULL DEFAULT ''")
    _chmod600(DB_FILE)
    _fernet()
    migrate_legacy_settings()


def migrate_legacy_settings() -> None:
    """Bir firmali eski versiyadan o'tish: settings.json dagi 1C/Didox sozlamalari firmaga aylanadi.
    Har doim kamida bitta demo firma bo'ladi (sinash uchun)."""
    with tx() as c:
        if c.execute("SELECT COUNT(*) FROM firms").fetchone()[0]:
            return
    create_firm({"name": "🧪 Demo firma (NAMUNA TRADE)", "inn": "305123456", "onec_demo_mode": True,
                 "didox_demo_mode": True})
    legacy = {}
    if config.SETTINGS_FILE.exists():
        try:
            legacy = json.loads(config.SETTINGS_FILE.read_text("utf-8"))
        except ValueError:
            legacy = {}
    if legacy.get("onec_base_url"):
        url = legacy["onec_base_url"]
        create_firm({
            "name": f"Firma ({url.split('//')[-1].split('/')[0]} {url.rstrip('/').split('/')[-1]})",
            "inn": legacy.get("onec_org_inn", ""), "onec_demo_mode": bool(legacy.get("onec_demo_mode", False)),
            "onec_base_url": url, "onec_user": legacy.get("onec_user", ""),
            "onec_password": legacy.get("onec_password", ""),
            "didox_demo_mode": bool(legacy.get("didox_demo_mode", True)),
            "didox_user_key": legacy.get("didox_user_key", ""),
            "didox_partner_token": legacy.get("didox_partner_token", "")})
    # firma sozlamalari endi bazada (shifrlangan) — settings.json da faqat AI sozlamalari qoladi
    if legacy:
        config.SETTINGS_FILE.write_text(json.dumps({k: v for k, v in legacy.items() if k in config.DEFAULTS},
                                                   ensure_ascii=False, indent=2), "utf-8")
        _chmod600(config.SETTINGS_FILE)


# ---------------------------------------------------------------- foydalanuvchilar
def user_count() -> int:
    with tx() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def _user_dict(row: sqlite3.Row | None, firm_ids: list[int] | None = None) -> dict | None:
    if row is None:
        return None
    u = {k: row[k] for k in ("id", "username", "full_name", "role", "telegram_id", "tg_firm_id", "created_at",
                             "last_login_at")}
    u["active"] = bool(row["active"])
    u["role_label"] = ROLE_LABELS[row["role"]]
    if firm_ids is not None:
        u["firm_ids"] = firm_ids
    return u


def _firm_ids_of(c, user_id: int) -> list[int]:
    return [r[0] for r in c.execute("SELECT firm_id FROM user_firms WHERE user_id = ? ORDER BY firm_id", (user_id,))]


def get_user(user_id: int) -> dict | None:
    with tx() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user_dict(row, _firm_ids_of(c, user_id) if row else None)


def get_user_by_telegram(telegram_id: int) -> dict | None:
    with tx() as c:
        row = c.execute("SELECT * FROM users WHERE telegram_id = ? AND active = 1", (telegram_id,)).fetchone()
        return _user_dict(row, _firm_ids_of(c, row["id"]) if row else None)


def list_users() -> list[dict]:
    with tx() as c:
        return [_user_dict(r, _firm_ids_of(c, r["id"])) for r in c.execute("SELECT * FROM users ORDER BY id")]


def _active_owners(c) -> int:
    return c.execute("SELECT COUNT(*) FROM users WHERE role = 'owner' AND active = 1").fetchone()[0]


def create_user(username: str, password: str, role: str, full_name: str = "", firm_ids: list[int] = ()) -> dict:
    username = (username or "").strip()
    if not username or len(username) > 64:
        raise DBError("Login bo'sh bo'lmasin (64 belgigacha)")
    if role not in ROLES:
        raise DBError("Noto'g'ri rol")
    _check_password_policy(password)
    with tx() as c:
        if c.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise DBError("Bunday login allaqachon bor")
        cur = c.execute("INSERT INTO users (username, full_name, password_hash, role, created_at) VALUES (?,?,?,?,?)",
                        (username, (full_name or "").strip()[:120], hash_password(password), role, _iso(now())))
        uid = cur.lastrowid
        _set_user_firms(c, uid, firm_ids)
    return get_user(uid)


def _set_user_firms(c, user_id: int, firm_ids) -> None:
    c.execute("DELETE FROM user_firms WHERE user_id = ?", (user_id,))
    for fid in {int(f) for f in firm_ids or []}:
        if c.execute("SELECT 1 FROM firms WHERE id = ?", (fid,)).fetchone():
            c.execute("INSERT INTO user_firms (user_id, firm_id) VALUES (?, ?)", (user_id, fid))


def update_user(user_id: int, *, role: str | None = None, active: bool | None = None, full_name: str | None = None,
                firm_ids: list[int] | None = None, password: str | None = None) -> dict:
    with tx() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise DBError("Foydalanuvchi topilmadi")
        losing_owner = row["role"] == "owner" and row["active"] and (
            (role is not None and role != "owner") or active is False)
        if losing_owner and _active_owners(c) <= 1:
            raise DBError("Oxirgi faol \"Egasi\"ni o'chirib yoki rolini o'zgartirib bo'lmaydi")
        if role is not None:
            if role not in ROLES:
                raise DBError("Noto'g'ri rol")
            c.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        if full_name is not None:
            c.execute("UPDATE users SET full_name = ? WHERE id = ?", (full_name.strip()[:120], user_id))
        if active is not None:
            c.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))
            if not active:
                c.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
        if firm_ids is not None:
            _set_user_firms(c, user_id, firm_ids)
        if password is not None:
            _check_password_policy(password)
            c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
            c.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))  # boshqa qurilmalardan chiqarish
    return get_user(user_id)


def authenticate(username: str, password: str) -> dict | None:
    with tx() as c:
        row = c.execute("SELECT * FROM users WHERE username = ?", ((username or "").strip(),)).fetchone()
    if not row:
        verify_password(password or "", hash_password("vaqt-tenglashtirish"))  # login bor-yo'qligini sezdirmaslik
        return None
    if not verify_password(password or "", row["password_hash"]) or not row["active"]:
        return None
    with tx() as c:
        c.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_iso(now()), row["id"]))
    return get_user(row["id"])


# ---------------------------------------------------------------- login urinishlarini cheklash
def login_locked_for(key: str) -> int:
    """Necha soniya bloklangan (0 — bloklanmagan)."""
    with tx() as c:
        row = c.execute("SELECT locked_until FROM login_failures WHERE key = ?", (key,)).fetchone()
    until = _parse(row["locked_until"]) if row else None
    return max(0, int((until - now()).total_seconds())) if until else 0


def register_login_failure(key: str, limit: int) -> None:
    with tx() as c:
        row = c.execute("SELECT fails, locked_until FROM login_failures WHERE key = ?", (key,)).fetchone()
        fails = 1
        if row:
            expired = row["locked_until"] and _parse(row["locked_until"]) <= now()
            fails = 1 if expired else row["fails"] + 1
        locked = _iso(now() + LOGIN_LOCK) if fails >= limit else None
        c.execute("INSERT INTO login_failures (key, fails, locked_until) VALUES (?, ?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET fails = excluded.fails, locked_until = excluded.locked_until",
                  (key, fails, locked))


def reset_login_failures(key: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM login_failures WHERE key = ?", (key,))


# ---------------------------------------------------------------- web sessiyalar
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_web_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with tx() as c:
        c.execute("INSERT INTO web_sessions (token_hash, user_id, created_at, last_seen) VALUES (?,?,?,?)",
                  (_token_hash(token), user_id, _iso(now()), _iso(now())))
        c.execute("DELETE FROM web_sessions WHERE last_seen < ? OR created_at < ?",
                  (_iso(now() - SESSION_IDLE), _iso(now() - SESSION_MAX)))
    return token


def user_from_web_session(token: str) -> dict | None:
    if not token:
        return None
    th = _token_hash(token)
    with tx() as c:
        row = c.execute("SELECT * FROM web_sessions WHERE token_hash = ?", (th,)).fetchone()
        if not row:
            return None
        if _parse(row["last_seen"]) < now() - SESSION_IDLE or _parse(row["created_at"]) < now() - SESSION_MAX:
            c.execute("DELETE FROM web_sessions WHERE token_hash = ?", (th,))
            return None
        if _parse(row["last_seen"]) < now() - timedelta(minutes=1):
            c.execute("UPDATE web_sessions SET last_seen = ? WHERE token_hash = ?", (_iso(now()), th))
        user_id = row["user_id"]
    user = get_user(user_id)
    return user if user and user["active"] else None


def delete_web_session(token: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM web_sessions WHERE token_hash = ?", (_token_hash(token or ""),))


# ---------------------------------------------------------------- Telegram bog'lash
def create_link_code(user_id: int) -> str:
    code = f"{secrets.randbelow(10 ** 6):06d}"
    with tx() as c:
        c.execute("DELETE FROM link_codes WHERE user_id = ? OR expires_at < ?", (user_id, _iso(now())))
        c.execute("INSERT INTO link_codes (code_hash, user_id, expires_at) VALUES (?,?,?)",
                  (_token_hash(code), user_id, _iso(now() + LINK_CODE_TTL)))
    return code


def consume_link_code(code: str, telegram_id: int) -> dict | None:
    code = "".join(ch for ch in str(code) if ch.isdigit())
    with tx() as c:
        row = c.execute("SELECT * FROM link_codes WHERE code_hash = ?", (_token_hash(code),)).fetchone()
        if not row or _parse(row["expires_at"]) < now():
            return None
        c.execute("DELETE FROM link_codes WHERE code_hash = ?", (_token_hash(code),))
        c.execute("UPDATE users SET telegram_id = NULL WHERE telegram_id = ?", (telegram_id,))
        c.execute("UPDATE users SET telegram_id = ? WHERE id = ?", (telegram_id, row["user_id"]))
        user_id = row["user_id"]
    return get_user(user_id)


def unlink_telegram(user_id: int) -> None:
    with tx() as c:
        c.execute("UPDATE users SET telegram_id = NULL, tg_firm_id = NULL WHERE id = ?", (user_id,))


def set_telegram_firm(user_id: int, firm_id: int) -> None:
    with tx() as c:
        c.execute("UPDATE users SET tg_firm_id = ? WHERE id = ?", (firm_id, user_id))


# ---------------------------------------------------------------- firmalar
def _norm(v) -> str:
    return str(v or "").strip().rstrip("/").lower()


def _clean_firm_value(key: str, value):
    if key in FIRM_BOOLS:
        return int(bool(value) and str(value).lower() not in ("0", "false", "no", "off"))
    value = str(value if value is not None else "").strip()
    if key == "inn":
        value = "".join(ch for ch in value if ch.isdigit())
    if key == "bank_accounts":
        value = ",".join(sorted({"".join(ch for ch in a if ch.isdigit()) for a in value.replace(";", ",").split(",")}
                                - {""}))
    return value


def create_firm(data: dict) -> dict:
    name = str(data.get("name") or "").strip()
    if not name:
        raise DBError("Firma nomi bo'sh bo'lmasin")
    values = {k: _clean_firm_value(k, data[k]) for k in FIRM_FIELDS if k in data}
    for k in FIRM_SECRETS:
        if values.get(k) == config.MASK:
            values.pop(k)
        elif k in values:
            values[k] = encrypt(values[k])
    cols = ", ".join(values) + ", created_at"
    with tx() as c:
        cur = c.execute(f"INSERT INTO firms ({cols}) VALUES ({', '.join('?' * (len(values) + 1))})",
                        (*values.values(), _iso(now())))
        fid = cur.lastrowid
    return get_firm(fid)


def update_firm(firm_id: int, data: dict) -> dict:
    before = firm_settings(firm_id)
    if before is None:
        raise DBError("Firma topilmadi")
    values = {}
    for k in FIRM_FIELDS:
        if k not in data:
            continue
        if k in FIRM_SECRETS:
            if data[k] in (None, config.MASK):
                continue
            values[k] = encrypt(str(data[k]).strip())
        else:
            values[k] = _clean_firm_value(k, data[k])
    if "name" in values and not values["name"]:
        raise DBError("Firma nomi bo'sh bo'lmasin")
    for secret, deps in FIRM_SECRET_BOUND_TO.items():
        changed = any(k in data and _norm(data[k]) != _norm(before[k]) for k in deps)
        if changed and data.get(secret) in (None, config.MASK):
            values[secret] = ""  # manzil/login o'zgardi — eski parol yangi manzilga yuborilmasin
    if values:
        with tx() as c:
            c.execute(f"UPDATE firms SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
                      (*values.values(), firm_id))
    return get_firm(firm_id)


def deactivate_firm(firm_id: int) -> None:
    with tx() as c:
        c.execute("UPDATE firms SET active = 0 WHERE id = ?", (firm_id,))


def _firm_public(row: sqlite3.Row) -> dict:
    f = {k: row[k] for k in ("id", "name", "inn", "onec_base_url", "onec_user", "didox_base_url", "created_at")}
    for k in FIRM_BOOLS:
        f[k] = bool(row[k])
    for k in FIRM_SECRETS:
        f[k] = config.MASK if row[k] else ""
    f["bank_accounts"] = row["bank_accounts"]
    f["active"] = bool(row["active"])
    return f


def get_firm(firm_id: int) -> dict | None:
    with tx() as c:
        row = c.execute("SELECT * FROM firms WHERE id = ?", (firm_id,)).fetchone()
    return _firm_public(row) if row else None


def firm_settings(firm_id: int) -> dict | None:
    """Servislar uchun: shifrlanmagan qiymatlar, eski sozlama nomlari bilan (onec_org_inn = firma STIR'i)."""
    with tx() as c:
        row = c.execute("SELECT * FROM firms WHERE id = ?", (firm_id,)).fetchone()
    if not row:
        return None
    s = {k: row[k] for k in FIRM_FIELDS}
    for k in FIRM_SECRETS:
        s[k] = decrypt(row[k])
    for k in FIRM_BOOLS:
        s[k] = bool(row[k])
    s["id"] = row["id"]
    s["active"] = bool(row["active"])
    s["onec_org_inn"] = row["inn"]
    return s


def list_firms(user: dict | None = None, include_inactive: bool = False) -> list[dict]:
    with tx() as c:
        rows = c.execute("SELECT * FROM firms ORDER BY id").fetchall()
    firms = [_firm_public(r) for r in rows if include_inactive or r["active"]]
    if user and user["role"] != "owner":
        allowed = set(user.get("firm_ids") or [])
        firms = [f for f in firms if f["id"] in allowed]
    return firms


def user_can_access(user: dict, firm_id: int) -> bool:
    firm = get_firm(firm_id)
    if not firm or not firm["active"]:
        return False
    return user["role"] == "owner" or firm_id in (user.get("firm_ids") or [])


def find_firms_by_owner_data(inn: str = "", account: str = "") -> list[dict]:
    """Vipiskadagi STIR yoki hisob raqami bo'yicha qaysi firmaga tegishli ekanini topish."""
    out = []
    for f in list_firms():
        accounts = set(filter(None, f["bank_accounts"].split(",")))
        if (inn and f["inn"] == inn) or (account and account in accounts):
            out.append(f)
    return out


# ---------------------------------------------------------------- audit jurnali
def audit(user_id: int | None, firm_id: int | None, action: str, **details) -> None:
    with tx() as c:
        c.execute("INSERT INTO audit_log (ts, user_id, firm_id, action, details) VALUES (?,?,?,?,?)",
                  (_iso(now()), user_id, firm_id, action, json.dumps(details, ensure_ascii=False, default=str)))


def list_audit(limit: int = 100, firm_id: int | None = None) -> list[dict]:
    q = ("SELECT a.*, u.username, f.name AS firm_name FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
         "LEFT JOIN firms f ON f.id = a.firm_id")
    args: tuple = ()
    if firm_id:
        q += " WHERE a.firm_id = ?"
        args = (firm_id,)
    q += " ORDER BY a.id DESC LIMIT ?"
    with tx() as c:
        rows = c.execute(q, (*args, max(1, min(limit, 500)))).fetchall()
    return [{"id": r["id"], "ts": r["ts"], "user": r["username"], "firm": r["firm_name"], "action": r["action"],
             "details": json.loads(r["details"] or "{}")} for r in rows]


# ---------------------------------------------------------------- hisob qoidalari (qaysi kontragent qaysi schyotga)
RULE_FIELDS = ("kind", "match", "name", "direction", "doc_kind", "account", "advance_account", "operation", "contract_key",
               "cash_flow_key", "locked", "source", "hits", "total", "variants", "last_date")


def _rule_dict(r: sqlite3.Row) -> dict:
    d = {k: r[k] for k in ("id", "firm_id", *RULE_FIELDS, "updated_at", "updated_by")}
    d["locked"] = bool(d["locked"])
    d["variants"] = json.loads(d["variants"] or "[]")
    d["confidence"] = round(100 * d["hits"] / d["total"]) if d["total"] else None
    d["conflict"] = len(d["variants"]) > 1
    return d


def _norm_rule(data: dict) -> dict:
    kind = data.get("kind", "counterparty")
    if kind not in ("counterparty", "keyword"):
        raise DBError("Qoida turi: counterparty yoki keyword")
    match = str(data.get("match") or "").strip()
    match = "".join(ch for ch in match if ch.isdigit()) if kind == "counterparty" else match.lower()
    if kind == "counterparty" and len(match) != 9:
        raise DBError("Kontragent STIR'i 9 xonali bo'lsin")
    if kind == "keyword" and len(match) < 3:
        raise DBError("Kalit so'z kamida 3 belgi bo'lsin")
    direction = data.get("direction", "") or ""
    if direction not in ("", "kirim", "chiqim"):
        raise DBError("Yo'nalish: kirim, chiqim yoki bo'sh")
    doc_kind = data.get("doc_kind", "bank") or "bank"
    if doc_kind not in ("bank", "ehf"):
        raise DBError("Hujjat turi: bank yoki ehf")
    account = str(data.get("account") or "").replace(" ", "")
    digits = account.replace(".", "")
    if not (len(digits) == 4 and digits.isdigit()):
        raise DBError(f"Schyot formati: 60.10 ko'rinishida (berilgan: {account or '—'})")
    adv = str(data.get("advance_account") or "").replace(" ", "").replace(".", "")
    if adv and not (len(adv) == 4 and adv.isdigit()):
        raise DBError(f"Avans schyoti formati: 63.10 ko'rinishida (berilgan: {data.get('advance_account')})")
    return {"kind": kind, "match": match, "direction": direction, "doc_kind": doc_kind,
            "account": f"{digits[:2]}.{digits[2:]}", "advance_account": f"{adv[:2]}.{adv[2:]}" if adv else ""}


def list_rules(firm_id: int) -> list[dict]:
    with tx() as c:
        rows = c.execute("SELECT * FROM account_rules WHERE firm_id = ? ORDER BY kind, name, match, direction",
                         (firm_id,)).fetchall()
    return [_rule_dict(r) for r in rows]


def get_rule(rule_id: int) -> dict | None:
    with tx() as c:
        r = c.execute("SELECT * FROM account_rules WHERE id = ?", (rule_id,)).fetchone()
    return _rule_dict(r) if r else None


def save_rule(firm_id: int, data: dict, by: str, *, locked: bool, source: str) -> dict:
    """Buxgalter qoidasi (qo'lda, tuzatishdan yoki Excel'dan). Bir xil kalitdagi qoida bo'lsa — yangilanadi."""
    key = _norm_rule(data)
    values = {**key, "name": str(data.get("name") or "")[:200], "operation": str(data.get("operation") or "")[:100],
              "contract_key": str(data.get("contract_key") or ""), "cash_flow_key": str(data.get("cash_flow_key") or ""),
              "locked": int(locked), "source": source, "updated_at": _iso(now()), "updated_by": by}
    with tx() as c:
        existing = c.execute("SELECT * FROM account_rules WHERE firm_id = ? AND kind = ? AND match = ? AND "
                             "direction = ? AND doc_kind = ?",
                             (firm_id, key["kind"], key["match"], key["direction"], key["doc_kind"])).fetchone()
        if existing:
            if not values["name"]:
                values["name"] = existing["name"]
            if existing["account"] == values["account"] and not values["contract_key"]:
                values["contract_key"], values["cash_flow_key"] = existing["contract_key"], existing["cash_flow_key"]
            c.execute(f"UPDATE account_rules SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
                      (*values.values(), existing["id"]))
            rule_id = existing["id"]
        else:
            cur = c.execute(f"INSERT INTO account_rules (firm_id, {', '.join(values)}) VALUES "
                            f"(?, {', '.join('?' * len(values))})", (firm_id, *values.values()))
            rule_id = cur.lastrowid
    return get_rule(rule_id)


def update_rule(rule_id: int, firm_id: int, data: dict, by: str) -> dict:
    rule = get_rule(rule_id)
    if not rule or rule["firm_id"] != firm_id:
        raise DBError("Qoida topilmadi")
    merged = {**rule, **{k: v for k, v in data.items()
                         if k in ("account", "advance_account", "operation", "direction", "name")}}
    norm = _norm_rule(merged)
    account, advance = norm["account"], norm["advance_account"]
    locked = bool(data.get("locked", rule["locked"]))
    changed = (account != rule["account"] or advance != rule["advance_account"]
               or str(merged.get("operation") or "") != rule["operation"])
    source = "manual" if changed or (locked and not rule["locked"]) else rule["source"]  # buxgalter tasdiqladi
    with tx() as c:
        c.execute("UPDATE account_rules SET advance_account = ? WHERE id = ?", (advance, rule_id))
        c.execute("UPDATE account_rules SET account = ?, operation = ?, name = ?, locked = ?, source = ?, "
                  "contract_key = CASE WHEN account = ? THEN contract_key ELSE '' END, "
                  "cash_flow_key = CASE WHEN account = ? THEN cash_flow_key ELSE '' END, "
                  "updated_at = ?, updated_by = ? WHERE id = ?",
                  (account, str(merged.get("operation") or "")[:100], str(merged.get("name") or "")[:200],
                   int(locked), source, account, account, _iso(now()), by, rule_id))
    return get_rule(rule_id)


def delete_rule(rule_id: int, firm_id: int) -> None:
    with tx() as c:
        c.execute("DELETE FROM account_rules WHERE id = ? AND firm_id = ?", (rule_id, firm_id))


def upsert_learned_rules(firm_id: int, learned: list[dict]) -> dict:
    """1C tarixidan o'rganilgan qoidalar. Qat'iy (buxgalter) qoidalarning schyoti o'zgartirilmaydi —
    faqat statistika yangilanadi. Natija: nechta qo'shildi / yangilandi / qat'iy qoldi."""
    stats = {"added": 0, "updated": 0, "kept_locked": 0}
    with tx() as c:
        for r in learned:
            row = c.execute("SELECT id, locked FROM account_rules WHERE firm_id = ? AND kind = ? AND match = ? AND "
                            "direction = ? AND doc_kind = ?",
                            (firm_id, r["kind"], r["match"], r["direction"], r["doc_kind"])).fetchone()
            common = (r["hits"], r["total"], json.dumps(r["variants"], ensure_ascii=False), r["last_date"], _iso(now()))
            if row and row["locked"]:
                c.execute("UPDATE account_rules SET hits = ?, total = ?, variants = ?, last_date = ?, updated_at = ? "
                          "WHERE id = ?", (*common, row["id"]))
                stats["kept_locked"] += 1
            elif row:
                c.execute("UPDATE account_rules SET name = ?, account = ?, advance_account = ?, operation = ?, "
                          "contract_key = ?, cash_flow_key = ?, source = 'history', hits = ?, total = ?, variants = ?, "
                          "last_date = ?, updated_at = ?, updated_by = '1C tarixi' WHERE id = ?",
                          (r["name"], r["account"], r.get("advance_account", ""), r["operation"], r["contract_key"],
                           r["cash_flow_key"], *common, row["id"]))
                stats["updated"] += 1
            else:
                c.execute("INSERT INTO account_rules (firm_id, kind, match, name, direction, doc_kind, account, "
                          "advance_account, operation, contract_key, cash_flow_key, locked, source, hits, total, variants, "
                          "last_date, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,'history',?,?,?,?,?,'1C tarixi')",
                          (firm_id, r["kind"], r["match"], r["name"], r["direction"], r["doc_kind"], r["account"],
                           r.get("advance_account", ""), r["operation"], r["contract_key"], r["cash_flow_key"], *common))
                stats["added"] += 1
    return stats
