"""Demo 1C bazasi bilan to'liq oqim: dublikat, tarixdan o'rganish, qoralama, majburan yozish, maxsus to'lovlar."""
import asyncio
from datetime import date

import pytest

import core
from services_1c import DemoOData, ODataError, OneCService
from services_vipiska import BankRow, Statement, analyze_statement
from tests.conftest import DEMO_FIRM


def run(coro):
    return asyncio.run(coro)


def by_number(rows):
    return {r["number"]: r for r in rows}


def test_sample_statement_classification():
    s = core.get_session("t", DEMO_FIRM)
    snap = run(core.load_sample(s))
    rows = by_number(snap["rows"])
    assert {n for n, r in rows.items() if r["status"] == "duplicate"} == {"112", "45", "118"}
    assert all(rows[n]["dup_kind"] == "exact" and not rows[n]["overridable"] for n in ("112", "45", "118"))
    assert rows["124"]["status"] == "discrepancy" and rows["124"]["overridable"]
    assert {n: rows[n]["account"] for n in ("52", "131", "53", "54")} == {
        "52": "60.10", "131": "40.10", "53": "64.10", "54": "94.20"}
    assert snap["kpis"]["learned_pct"] == 100 and snap["kpis"]["ready"] == 4


def test_post_creates_unposted_drafts_and_blocks_reupload():
    s = core.get_session("t", DEMO_FIRM)
    snap = run(core.load_sample(s))
    res = run(core.post(s, "vipiska", [r["id"] for r in snap["rows"]]))
    assert res["ok"] == 4 and res["failed"] == 4
    onec = core.get_onec(DEMO_FIRM)
    drafts = [d for e in ("Document_ПоступлениеНаРасчетныйСчет", "Document_СписаниеСРасчетногоСчета")
              for d in onec.t.db[e] if d.get("Комментарий", "").startswith("BUXGALTER AI")]
    assert len(drafts) == 4 and all(d["Posted"] is False for d in drafts)
    assert all(d.get("ВалютаДокумента_Key") and d.get("СчетБанк_Key") for d in drafts)  # tarixdan to'ldirilgan
    again = run(core.load_sample(core.get_session("t2", DEMO_FIRM)))
    assert sum(r["status"] == "duplicate" for r in again["rows"]) == 7


def test_force_requires_overridable_and_never_posts_exact_duplicates():
    s = core.get_session("t", DEMO_FIRM)
    rows = by_number(run(core.load_sample(s))["rows"])
    res = run(core.post(s, "vipiska", [], force_ids=[rows["112"]["id"]], force_reason="sinov"))
    assert res["ok"] == 0 and "Majburan yozib bo'lmaydi" in res["results"][0]["message"]
    res = run(core.post(s, "vipiska", [], force_ids=[rows["124"]["id"]], force_reason="alohida to'lov"))
    assert res["ok"] == 1
    posted = by_number(res["rows"])["124"]
    assert posted["forced"] and posted["status"] == "posted"
    comment = next(d["Комментарий"] for d in core.get_onec(DEMO_FIRM).t.db["Document_ПоступлениеНаРасчетныйСчет"]
                   if d.get("Ref_Key") == posted["posted_ref"])
    assert "MAJBURAN" in comment and "alohida to'lov" in comment


def test_in_file_duplicate_and_special_operations_are_blocked():
    onec = OneCService(DemoOData())
    rows = [BankRow(date(2026, 10, 16), "9", "chiqim", 5.0, "X", "304567890", purpose="a", id="a"),
            BankRow(date(2026, 10, 16), "9", "chiqim", 5.0, "X", "304567890", purpose="a", id="b"),
            BankRow(date(2026, 10, 16), "10", "chiqim", 7.0, "Own", "305123456", purpose="o'tkazma", id="c"),
            BankRow(date(2026, 10, 16), "11", "chiqim", 9.0, "Bank", "207000001", purpose="Oktyabr ish haqi", id="d")]
    out = {r["id"]: r for r in run(analyze_statement(Statement(rows=rows), onec))}
    assert out["a"]["selectable"] and out["b"]["dup_kind"] == "infile" and not out["b"]["overridable"]
    assert out["c"]["special"] == "transfer" and not out["c"]["selectable"]
    assert out["d"]["special"] == "salary" and not out["d"]["selectable"]


class LiveDemo(DemoOData):
    mode = "live"


def test_sample_never_posts_to_live_1c(monkeypatch):
    live = OneCService(LiveDemo())
    monkeypatch.setattr(core, "get_onec", lambda *a: live)
    monkeypatch.setattr(core, "onec_fingerprint", lambda *a: "live")
    s = core.get_session("t", DEMO_FIRM)
    snap = run(core.load_sample(s))
    assert snap["is_sample"] and snap["kpis"]["ready"] == 0
    assert all(any("Test" in b for b in r["blockers"]) for r in snap["rows"])
    with pytest.raises(ODataError, match="Test|Faqat tahlil"):
        run(core.post(s, "vipiska", [r["id"] for r in snap["rows"]]))


def test_post_refused_when_1c_settings_changed_after_analysis(monkeypatch):
    s = core.get_session("t", DEMO_FIRM)
    run(core.load_sample(s))
    monkeypatch.setattr(core, "onec_fingerprint", lambda *a: "boshqa-1c")
    with pytest.raises(ODataError, match="qayta tekshirish"):
        run(core.post(s, "vipiska", [r["id"] for r in s.vipiska]))
    assert core.snapshot(s)["stale"]


def test_session_survives_restart():
    s = core.get_session("persist", DEMO_FIRM)
    run(core.load_sample(s))
    core.SESSIONS.clear()
    restored = core.get_session("persist", DEMO_FIRM)
    assert restored.statement.is_sample and len(restored.vipiska) == 8
    assert restored.statement.rows[0].date == date(2026, 10, 1)


class SlowDemo(DemoOData):
    """Haqiqiy tarmoq kabi har so'rovda boshqaruvni bo'shatadi — poyga (race) holatini ko'rsatish uchun."""
    async def query(self, *a, **kw):
        await asyncio.sleep(0.001)
        return await super().query(*a, **kw)

    async def create(self, *a, **kw):
        await asyncio.sleep(0.001)
        return await super().create(*a, **kw)


def test_parallel_posts_of_same_rows_create_each_draft_once(monkeypatch):
    slow = OneCService(SlowDemo())
    monkeypatch.setattr(core, "get_onec", lambda *a: slow)
    monkeypatch.setattr(core, "onec_fingerprint", lambda *a: "slow")
    s1, s2 = core.get_session("web", DEMO_FIRM), core.get_session("tg", DEMO_FIRM)

    async def scenario():  # bitta event loop — server'dagi kabi
        await core.load_sample(s1)
        await core.load_sample(s2)
        return await asyncio.gather(core.post(s1, "vipiska", [r["id"] for r in s1.vipiska]),
                                    core.post(s2, "vipiska", [r["id"] for r in s2.vipiska]))
    a, b = run(scenario())
    assert a["ok"] + b["ok"] == 4
    drafts = [d for e in ("Document_ПоступлениеНаРасчетныйСчет", "Document_СписаниеСРасчетногоСчета")
              for d in slow.t.db[e] if d.get("Комментарий", "").startswith("BUXGALTER AI")]
    assert len(drafts) == 4


def test_failed_analysis_keeps_previous_statement(monkeypatch):
    s = core.get_session("keep", DEMO_FIRM)
    run(core.load_sample(s))
    before = (s.statement.source, len(s.vipiska))

    async def boom(*a, **kw):
        raise ODataError("1C 404: «Catalog_Организации» OData tarkibida nashr qilinmagan")
    monkeypatch.setattr(core, "analyze_statement", boom)
    with pytest.raises(ODataError):
        run(core.load_statement(s, "boshqa.csv", "Sana;Hujjat №;Debet;Kredit;Kontragent;STIR;Maqsad\n16.10.2026;1;5;;A;301234567;x\n".encode()))
    assert (s.statement.source, len(s.vipiska)) == before  # yarim yuklangan vipiska qolmadi
