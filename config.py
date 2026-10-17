"""BUXGALTER AI — sozlamalar (.env + data/settings.json).

Ustuvorlik: standart qiymatlar  <  .env / muhit o'zgaruvchilari  <  UI orqali saqlangan settings.json
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"
SAMPLE_VIPISKA = BASE_DIR / "sample_bank_vipiska.xlsx"

load_dotenv(BASE_DIR / ".env")

# UI orqali o'zgartiriladigan sozlamalar
DEFAULTS: dict[str, object] = {
    "ai_provider": "gemini",  # gemini | openai | claude | mock
    "gemini_api_key": "",
    "gemini_model": "gemini-2.5-flash",
    "openai_api_key": "",
    "openai_model": "gpt-4.1-mini",
    "anthropic_api_key": "",
    "anthropic_model": "claude-sonnet-5-5",
    "onec_demo_mode": True,
    "onec_base_url": "",  # masalan: http://server/buh_uz  (…/odata/standard.odata avtomatik qo'shiladi)
    "onec_user": "",
    "onec_password": "",
    "onec_org_inn": "",  # bir nechta tashkilot bo'lsa — qaysi STIR bo'yicha ishlash
    "didox_demo_mode": True,
    "didox_base_url": "https://api-partners.didox.uz",
    "didox_user_key": "",
    "didox_partner_token": "",
}
SECRET_KEYS = {"gemini_api_key", "openai_api_key", "anthropic_api_key", "onec_password",
               "didox_user_key", "didox_partner_token"}
BOOL_KEYS = {"onec_demo_mode", "didox_demo_mode"}
MASK = "••••••••"

# Faqat .env orqali (UI dan o'zgartirilmaydi — xavfsizlik uchun)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
APP_ACCESS_TOKEN = os.getenv("APP_ACCESS_TOKEN", "").strip()
ALLOWED_TELEGRAM_IDS = {int(x) for x in os.getenv("ALLOWED_TELEGRAM_IDS", "").replace(" ", "").split(",") if x.isdigit()}
HOST = os.getenv("HOST", "127.0.0.1")
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
        SETTINGS_FILE.write_text(json.dumps(stored, ensure_ascii=False, indent=2), "utf-8")
        try:
            os.chmod(SETTINGS_FILE, 0o600)
        except OSError:
            pass
    return get_settings()


def public_settings() -> dict:
    s = get_settings()
    return {k: (MASK if k in SECRET_KEYS and v else v) for k, v in s.items()}
