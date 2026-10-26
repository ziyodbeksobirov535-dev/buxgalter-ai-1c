"""Yangi kiritilgan upgrade funksiyalari uchun testlar:
- SQLite WAL rejimi va busy timeout
- SQLite sessiyalar ombori (sessions_store)
- Statik fayllar (/static) xizmati
- AI Chat streaming (/api/chat/stream SSE)
- Didox MXIK (IKPU) tekshiruvchi funksiyasi
- 1C OData batch_query
"""
import asyncio
import json
import sqlite3

import pytest
from starlette.testclient import TestClient

import db
import main
import services_1c
import services_ai
import services_didox
from tests.conftest import DEMO_FIRM


def test_sqlite_wal_and_busy_timeout():
    """SQLite WAL mode va busy timeout to'g'ri sozlanganligini tekshirish."""
    with db.tx() as c:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = c.execute("PRAGMA busy_timeout").fetchone()[0]
        fk = c.execute("PRAGMA foreign_keys").fetchone()[0]
    assert mode.lower() == "wal"
    assert timeout >= 5000
    assert fk == 1


def test_sessions_store_persistence():
    """Sessiyalarni DB da saqlash va tiklash funksiyasi."""
    test_sid = "u999_f999"
    test_data = {"test_key": "salom", "count": 42}
    db.save_session_store(test_sid, 999, test_data)

    loaded = db.load_session_store(test_sid)
    assert loaded is not None
    assert loaded["test_key"] == "salom"
    assert loaded["count"] == 42


def test_validate_ikpu():
    """MXIK (IKPU) 17 xonali kodini tekshirish."""
    # To'g'ri kod (17 xona)
    res_valid = services_didox.validate_ikpu("02523001001000000")
    assert res_valid["valid"] is True
    assert res_valid["code"] == "02523001001000000"

    # Qisqa kod
    res_short = services_didox.validate_ikpu("02523")
    assert res_short["valid"] is False

    # Bo'sh kod
    res_empty = services_didox.validate_ikpu("")
    assert res_empty["valid"] is False


def test_static_vendor_assets_served():
    """Lokal vendor fayllari (/static/vendor/...) to'g'ri uzatilayotganini tekshirish."""
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    r = client.get("/static/vendor/alpine.min.js")
    assert r.status_code == 200
    assert len(r.content) > 1000


def test_batch_query_demo():
    """DemoOData da batch_query ishlashi."""
    demo = services_1c.DemoOData()
    queries = [
        {"entity": services_1c.E_COUNTERPARTY, "top": 2},
        {"entity": services_1c.E_BANK_ACCOUNT, "top": 2},
    ]
    results = asyncio.run(demo.batch_query(queries))
    assert len(results) == 2
    assert isinstance(results[0], list)
    assert isinstance(results[1], list)


def test_ai_chat_stream_mock():
    """AI streaming generatori to'g'ri SSE formatida ma'lumot berayotganini tekshirish."""
    import core
    session = core.get_session("test_stream", DEMO_FIRM)

    async def collect_stream():
        chunks = []
        async for chunk in services_ai.chat_stream(session, "51.10 bank qoldig'i qancha", "vipiska"):
            chunks.append(chunk)
        return chunks

    events = asyncio.run(collect_stream())
    assert len(events) >= 2
    parsed = [json.loads(e.strip()) for e in events if e.strip()]
    types = [p["type"] for p in parsed]
    assert "start" in types
    assert "done" in types
