"""Testlar haqiqiy data/ papkasiga (sozlamalar, sessiyalar, blok yozuvlari) tegmasligi uchun izolyatsiya."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import core  # noqa: E402
import services_1c  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(core, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(services_1c, "BLOCK_FILE", tmp_path / "onec_blocks.json")
    for key in config.DEFAULTS:
        monkeypatch.delenv(key.upper(), raising=False)
    core.SESSIONS.clear()
    monkeypatch.setattr(core, "_onec", None)
    monkeypatch.setattr(core, "_onec_sig", None)
    yield tmp_path
