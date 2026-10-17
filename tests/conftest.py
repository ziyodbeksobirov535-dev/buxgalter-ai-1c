"""Testlar haqiqiy data/ papkasiga (baza, sozlamalar, sessiyalar, blok yozuvlari) tegmasligi uchun izolyatsiya."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import core  # noqa: E402
import db  # noqa: E402
import services_1c  # noqa: E402

DEMO_FIRM = 1  # db.init() har doim birinchi bo'lib demo firmani yaratadi


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(core, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(services_1c, "BLOCK_FILE", tmp_path / "onec_blocks.json")
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "app.db")
    monkeypatch.setattr(db, "KEY_FILE", tmp_path / "secret.key")
    monkeypatch.setattr(db, "_fernet_cache", None)
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    for key in config.DEFAULTS:
        monkeypatch.delenv(key.upper(), raising=False)
    core.SESSIONS.clear()
    core._services.clear()
    db.init()
    yield tmp_path
