"""Testlar haqiqiy data/ papkasiga (baza, sozlamalar, sessiyalar, blok yozuvlari) tegmasligi uchun izolyatsiya."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import core  # noqa: E402
import db  # noqa: E402
import services_1c  # noqa: E402
import services_files1c  # noqa: E402

DEMO_FIRM = 1  # db.init() har doim birinchi bo'lib demo firmani yaratadi

# Namuna vipiska (demo_data.SAMPLE_TRANSACTIONS): 14 qator =
#   3 dublikat + 1 farqli + 1 ish haqi (vedomost kerak — bloklanadi) + 9 kiritishga tayyor
SAMPLE_ROWS = 14
SAMPLE_READY = 9
SAMPLE_NOT_POSTED = SAMPLE_ROWS - SAMPLE_READY  # 5


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(core, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(services_1c, "BLOCK_FILE", tmp_path / "onec_blocks.json")
    monkeypatch.setattr(services_files1c, "FILES_DIR", tmp_path / "onec_files")
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "app.db")
    monkeypatch.setattr(db, "KEY_FILE", tmp_path / "secret.key")
    monkeypatch.setattr(db, "_fernet_cache", None)
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    for key in config.DEFAULTS:
        monkeypatch.delenv(key.upper(), raising=False)
    core.SESSIONS.clear()
    core._services.clear()
    core._file_services.clear()
    db.init()
    yield tmp_path
