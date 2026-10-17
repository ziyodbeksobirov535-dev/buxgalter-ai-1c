"""AI: Demo AI marshrutlash va Gemini model zaxirasi (Google'ga ulanmasdan, soxta mijoz bilan)."""
import asyncio
import types as pytypes

import config
import core
import services_ai
from tests.conftest import DEMO_FIRM, SAMPLE_READY


def test_demo_ai_post_request_shows_confirmation_card_but_posts_nothing():
    s = core.get_session("ai", DEMO_FIRM)
    res = asyncio.run(services_ai.chat(s, "Yangi vipiskalarni 1C ga kirit", "vipiska"))
    assert res["engine"] == "mock"
    assert [c["type"] for c in res["cards"]] == ["confirm_post"] and res["cards"][0]["count"] == SAMPLE_READY
    assert not any(r["posted"] for r in s.vipiska)


def test_gemini_retired_model_falls_back(monkeypatch):
    from google.genai import errors
    used = []

    class FakeModels:
        async def generate_content(self, model, contents, config):
            used.append(model)
            if model == "gemini-retired":
                raise errors.ClientError(404, {"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}})
            return pytypes.SimpleNamespace(function_calls=None, text="Salom")

    class FakeClient:
        def __init__(self, api_key):
            self.aio = pytypes.SimpleNamespace(models=FakeModels())

    import google.genai
    monkeypatch.setattr(google.genai, "Client", FakeClient)
    config.save_settings({"ai_provider": "gemini", "gemini_api_key": "k", "gemini_model": "gemini-retired"})
    res = asyncio.run(services_ai.chat(core.get_session("ai", DEMO_FIRM), "salom", "vipiska"))
    assert used == ["gemini-retired", services_ai.GEMINI_FALLBACK_MODEL]
    assert res["engine"] == "gemini" and "Salom" in res["reply"] and "yopilgan" in res["reply"]
