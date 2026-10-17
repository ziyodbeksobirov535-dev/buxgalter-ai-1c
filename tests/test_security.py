import hashlib
import hmac
import json
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

import config
import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "test-token")
    return TestClient(main.app, base_url="http://127.0.0.1:8000")


AUTH = {"Authorization": "Bearer test-token"}


def test_foreign_host_header_is_rejected(client):
    assert client.get("/api/settings", headers={**AUTH, "Host": "evil.example:8000"}).status_code == 400
    assert client.get("/api/settings", headers=AUTH).status_code == 200


def test_remote_request_without_token_is_rejected(client):
    assert client.get("/api/status").status_code == 401  # TestClient "masofadan" kelgan so'rov
    assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_secrets_are_masked_in_settings_api(client):
    client.post("/api/settings", headers=AUTH, json={"gemini_api_key": "AIza-secret"})
    assert client.get("/api/settings", headers=AUTH).json()["gemini_api_key"] == config.MASK


def test_changing_1c_url_or_user_drops_stored_password():
    config.save_settings({"onec_base_url": "https://1c.uzcloud.uz/a/bpuz/1234", "onec_user": "u",
                          "onec_password": "secret"})
    config.save_settings({"onec_base_url": "https://1c.uzcloud.uz/a/bpuz/1234/", "onec_password": config.MASK})
    assert config.get_settings()["onec_password"] == "secret"  # faqat "/" qo'shildi — o'sha manzil
    config.save_settings({"onec_base_url": "https://evil.example", "onec_password": config.MASK})
    assert config.get_settings()["onec_password"] == ""
    config.save_settings({"onec_base_url": "https://1c.uzcloud.uz/a/bpuz/1234", "onec_password": "new"})
    config.save_settings({"onec_user": "other", "onec_password": config.MASK})
    assert config.get_settings()["onec_password"] == ""


def test_force_post_requires_reason(client):
    r = client.post("/api/post", headers=AUTH, json={"mode": "vipiska", "force_ids": ["x"], "force_reason": ""})
    assert r.status_code == 400


def _signed_init_data(token: str, uid: int) -> str:
    fields = {"auth_date": str(int(time.time())), "query_id": "AAA", "user": json.dumps({"id": uid, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


def test_telegram_init_data(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:TEST")
    monkeypatch.setattr(config, "ALLOWED_TELEGRAM_IDS", {42})
    assert main.telegram_user_from_init_data(_signed_init_data("123:TEST", 42)) == 42
    with pytest.raises(main.HTTPException) as e:
        main.telegram_user_from_init_data(_signed_init_data("123:TEST", 7))
    assert e.value.status_code == 403
    with pytest.raises(main.HTTPException) as e:
        main.telegram_user_from_init_data(_signed_init_data("999:OTHER", 42))
    assert e.value.status_code == 401
