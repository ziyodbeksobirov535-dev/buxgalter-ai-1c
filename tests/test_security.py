"""Login, rollar, ko'p firma izolyatsiyasi va xavfsizlik himoyalari (HTTP darajasida)."""
import hashlib
import hmac
import json
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

import auth
import config
import db
import main
from tests.conftest import DEMO_FIRM, SAMPLE_READY, SAMPLE_ROWS

PASSWORD = "parol12345"


@pytest.fixture
def client():
    return TestClient(main.app, base_url="http://127.0.0.1:8000")


def login(client, username, password=PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


@pytest.fixture
def firms():
    a = db.create_firm({"name": "Alfa MChJ", "inn": "301111111", "onec_demo_mode": True})
    b = db.create_firm({"name": "Beta MChJ", "inn": "302222222", "onec_demo_mode": True})
    return a["id"], b["id"]


@pytest.fixture
def users(firms):
    alfa, _beta = firms
    return {
        "owner": db.create_user("egasi", PASSWORD, "owner", "Egasi"),
        "buh": db.create_user("buh", PASSWORD, "accountant", "Buxgalter", firm_ids=[DEMO_FIRM, alfa]),
        "kuz": db.create_user("kuz", PASSWORD, "viewer", "Kuzatuvchi", firm_ids=[DEMO_FIRM]),
    }


# ---------------------------------------------------------------- birinchi sozlash
def test_first_owner_only_from_local_machine(client, monkeypatch):
    body = {"username": "egasi", "password": PASSWORD, "full_name": "Egasi"}
    assert client.get("/api/auth/state").json() == {"needs_setup": True, "setup_allowed": False}
    assert client.post("/api/auth/setup", json=body).status_code == 403  # TestClient = "masofadan"
    monkeypatch.setattr(auth, "_LOOPBACK", auth._LOOPBACK | {"testclient"})
    r = client.post("/api/auth/setup", json=body)
    assert r.status_code == 200 and r.json()["user"]["role"] == "owner"
    assert client.get("/api/auth/me").status_code == 200  # avtomatik kirildi
    assert client.post("/api/auth/setup", json={**body, "username": "x"}).status_code == 409  # ikkinchi marta yo'q


# ---------------------------------------------------------------- login
def test_api_requires_login(client, users):
    assert client.get("/api/session").status_code == 401
    assert login(client, "buh").status_code == 200
    assert client.get("/api/session").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/session").status_code == 401


def test_session_cookie_is_httponly_and_strict(client, users):
    cookie = login(client, "buh").headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_wrong_password_lockout(client, users):
    for _ in range(db.LOGIN_MAX_FAILS):
        assert login(client, "buh", "xato-parol").status_code == 401
    r = login(client, "buh")  # to'g'ri parol ham — blok vaqtida rad etiladi
    assert r.status_code == 429 and "daqiqa" in r.json()["detail"]


def test_unknown_user_and_wrong_password_look_the_same(client, users):
    assert login(client, "yoq-odam", "x").json() == login(client, "buh", "xato-parol").json()


def test_password_change_logs_out_other_devices(client, users):
    other = TestClient(main.app, base_url="http://127.0.0.1:8000")
    login(client, "buh")
    login(other, "buh")
    r = client.post("/api/auth/password", json={"old_password": PASSWORD, "new_password": "yangi-parol-123"})
    assert r.status_code == 200
    assert client.get("/api/auth/me").status_code == 200   # shu qurilma qoladi
    assert other.get("/api/auth/me").status_code == 401    # boshqa qurilma chiqariladi


# ---------------------------------------------------------------- rollar va firmalar
def test_accountant_sees_only_assigned_firms(client, users, firms):
    alfa, beta = firms
    login(client, "buh")
    me = client.get("/api/auth/me").json()
    assert {f["id"] for f in me["firms"]} == {DEMO_FIRM, alfa}
    assert client.get("/api/session", headers={"X-Firm-Id": str(beta)}).status_code == 403
    assert client.get("/api/session", headers={"X-Firm-Id": str(alfa)}).status_code == 200


def test_owner_sees_all_firms_and_manages(client, users, firms):
    login(client, "egasi")
    assert len(client.get("/api/auth/me").json()["firms"]) == 3
    r = client.post("/api/firms", json={"name": "Gamma", "inn": "303333333"})
    assert r.status_code == 200
    assert client.post("/api/users", json={"username": "yangi", "password": PASSWORD, "role": "viewer"}).status_code == 200


def test_non_owner_cannot_manage(client, users):
    login(client, "buh")
    assert client.post("/api/firms", json={"name": "X"}).status_code == 403
    assert client.get("/api/users").status_code == 403
    assert client.get("/api/settings").status_code == 403
    assert client.get("/api/audit").status_code == 403


def test_viewer_can_analyze_but_never_post(client, users):
    login(client, "kuz")
    h = {"X-Firm-Id": str(DEMO_FIRM)}
    snap = client.post("/api/vipiska/sample", headers=h).json()
    assert snap["can_post"] is False
    ids = [r["id"] for r in snap["rows"] if r["selectable"]]
    assert client.post("/api/post", headers=h, json={"ids": ids}).status_code == 403
    assert client.post(f"/api/rows/{ids[0]}/account", headers=h, json={"account": "60.10"}).status_code == 403
    chat = client.post("/api/chat", headers=h, json={"message": "Yangi vipiskalarni 1C ga kirit"}).json()
    assert not [c for c in chat["cards"] if c["type"] == "confirm_post"]


def test_last_owner_cannot_be_removed(client, users):
    login(client, "egasi")
    r = client.put(f"/api/users/{users['owner']['id']}", json={"role": "viewer"})
    assert r.status_code == 400 and "Oxirgi" in r.json()["detail"]


def test_firms_have_separate_sessions(client, users, firms):
    alfa, _ = firms
    login(client, "buh")
    client.post("/api/vipiska/sample", headers={"X-Firm-Id": str(DEMO_FIRM)})
    assert len(client.get("/api/session", headers={"X-Firm-Id": str(DEMO_FIRM)}).json()["rows"]) == SAMPLE_ROWS
    assert client.get("/api/session", headers={"X-Firm-Id": str(alfa)}).json()["rows"] == []


def test_statement_of_another_firm_is_blocked_and_owner_firm_suggested(client, users, firms):
    alfa, _ = firms
    login(client, "buh")
    content = config.SAMPLE_VIPISKA.read_bytes()  # sarlavhada STIR 305123456 (demo firma)
    snap = client.post("/api/vipiska/upload", headers={"X-Firm-Id": str(alfa)},
                       files={"file": ("vipiska.xlsx", content)}).json()
    assert snap["kpis"]["ready"] == 0
    assert all(any("STIR 305123456" in b for b in r["blockers"]) for r in snap["rows"])
    assert [f["id"] for f in snap["owner_firms"]] == [DEMO_FIRM]


def test_audit_records_posting(client, users):
    login(client, "buh")
    h = {"X-Firm-Id": str(DEMO_FIRM)}
    snap = client.post("/api/vipiska/sample", headers=h).json()
    client.post("/api/post", headers=h, json={"ids": [r["id"] for r in snap["rows"] if r["selectable"]]})
    entry = next(a for a in db.list_audit() if a["action"] == "posted_to_1c")
    assert entry["user"] == "buh" and entry["details"]["ok"] == SAMPLE_READY


# ---------------------------------------------------------------- firma maxfiy ma'lumotlari
def test_firm_secrets_encrypted_masked_and_cleared_on_url_change(client, users, firms):
    alfa, _ = firms
    db.update_firm(alfa, {"onec_base_url": "https://1c.uzcloud.uz/a/bpuz/1234", "onec_user": "u", "onec_password": "S3cret!"})
    assert b"S3cret!" not in db.DB_FILE.read_bytes()
    login(client, "egasi")
    firm = next(f for f in client.get("/api/firms").json() if f["id"] == alfa)
    assert firm["onec_password"] == config.MASK
    db.update_firm(alfa, {"onec_base_url": "https://1c.uzcloud.uz/a/bpuz/1234/", "onec_password": config.MASK})
    assert db.firm_settings(alfa)["onec_password"] == "S3cret!"  # o'sha manzil
    db.update_firm(alfa, {"onec_base_url": "https://evil.example", "onec_password": config.MASK})
    assert db.firm_settings(alfa)["onec_password"] == ""


# ---------------------------------------------------------------- tarmoq himoyalari
def test_foreign_host_header_is_rejected(client, users):
    login(client, "buh")
    assert client.get("/api/auth/me", headers={"Host": "evil.example:8000"}).status_code == 400


def test_cross_site_post_is_rejected(client, users):
    login(client, "buh")
    r = client.post("/api/vipiska/sample", headers={"Origin": "https://evil.example", "X-Firm-Id": str(DEMO_FIRM)})
    assert r.status_code == 403


def test_force_post_requires_reason(client, users):
    login(client, "buh")
    r = client.post("/api/post", headers={"X-Firm-Id": str(DEMO_FIRM)},
                    json={"force_ids": ["x"], "force_reason": ""})
    assert r.status_code == 400


# ---------------------------------------------------------------- Telegram
def _signed_init_data(token: str, uid: int) -> str:
    fields = {"auth_date": str(int(time.time())), "query_id": "AAA", "user": json.dumps({"id": uid, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


def test_telegram_mini_app_requires_linked_account(client, users, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:TEST")
    h = {"X-Telegram-Init-Data": _signed_init_data("123:TEST", 42)}
    assert client.get("/api/auth/me", headers=h).status_code == 403  # bog'lanmagan
    code = db.create_link_code(users["buh"]["id"])
    assert db.consume_link_code(code, 42)["username"] == "buh"
    assert db.consume_link_code(code, 42) is None  # kod bir martalik
    assert client.get("/api/auth/me", headers=h).json()["user"]["username"] == "buh"
    forged = {"X-Telegram-Init-Data": _signed_init_data("999:OTHER", 42)}
    assert client.get("/api/auth/me", headers=forged).status_code == 401
