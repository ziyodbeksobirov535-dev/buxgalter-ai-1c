"""BUXGALTER AI — sozlamalar (.env + data/settings.json).

Ustuvorlik: standart qiymatlar  <  .env / muhit o'zgaruvchilari  <  UI orqali saqlangan settings.json
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"
SAMPLE_VIPISKA = BASE_DIR / "sample_bank_vipiska.xlsx"

load_dotenv(BASE_DIR / ".env")

# UI orqali o'zgartiriladigan UMUMIY sozlamalar (AI). 1C va Didox ulanishlari har bir firma uchun alohida —
# ular data/app.db da shifrlangan holda saqlanadi (db.py).
DEFAULTS: dict[str, object] = {
    "ai_provider": "gemini",  # gemini | openai | claude | mock
    "gemini_api_key": "",
    "gemini_model": "gemini-3.8-flash",
    "openai_api_key": "",
    "openai_model": "gpt-4.1-mini",
    "anthropic_api_key": "",
    "anthropic_model": "claude-sonnet-5-5",
}
SECRET_KEYS = {"gemini_api_key", "openai_api_key", "anthropic_api_key"}
BOOL_KEYS: set[str] = set()
MASK = "••••••••"

# Faqat .env orqali (UI dan o'zgartirilmaydi — xavfsizlik uchun)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
HOST = os.getenv("HOST", "127.0.0.1")
# Qaysi Host nomlari bilan kelgan so'rovlar qabul qilinadi (DNS rebinding hujumidan himoya).
# WEBAPP_URL domeni avtomatik qo'shiladi; qo'shimchalarini vergul bilan: ALLOWED_HOSTS=192.168.1.10,buh.local
_extra_hosts = {h.strip() for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()}
_webapp_host = urlparse(WEBAPP_URL).hostname if WEBAPP_URL else None
ALLOWED_HOSTS = sorted({"127.0.0.1", "localhost", *_extra_hosts} | ({_webapp_host} if _webapp_host else set()))
PORT = int(os.getenv("PORT", "8000"))

_lock = threading.Lock()


def _to_bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "ha", "on"}


def get_settings() -> dict:
    s = dict(DEFAULTS)
    for key in DEFAULTS:
        env_val = os.getenv(key.upper())
        if env_val not in (None, ""):
            s[key] = env_val
    if SETTINGS_FILE.exists():
        try:
            s.update({k: v for k, v in json.loads(SETTINGS_FILE.read_text("utf-8")).items() if k in DEFAULTS})
        except (json.JSONDecodeError, OSError):
            pass
    for key in BOOL_KEYS:
        s[key] = _to_bool(s[key])
    return s


def save_settings(updates: dict) -> dict:
    with _lock:
        stored = {}
        if SETTINGS_FILE.exists():
            try:
                stored = json.loads(SETTINGS_FILE.read_text("utf-8"))
            except json.JSONDecodeError:
                stored = {}
        for key, value in updates.items():
            if key not in DEFAULTS:
                continue
            if key in SECRET_KEYS and (value is None or value == MASK):
                continue  # maskalangan qiymat qaytdi — eski kalitni saqlab qolamiz
            stored[key] = _to_bool(value) if key in BOOL_KEYS else (str(value).strip() if value is not None else "")
        stored = {k: v for k, v in stored.items() if k in DEFAULTS}  # eski (firma) kalitlari qayta yozilmasin
        SETTINGS_FILE.write_text(json.dumps(stored, ensure_ascii=False, indent=2), "utf-8")
        try:
            os.chmod(SETTINGS_FILE, 0o600)
        except OSError:
            pass
    return get_settings()


def public_settings() -> dict:
    s = get_settings()
    return {k: (MASK if k in SECRET_KEYS and v else v) for k, v in s.items()}
