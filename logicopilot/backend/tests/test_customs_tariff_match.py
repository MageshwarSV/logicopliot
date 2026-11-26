"""Indian Customs Tariff - phase 2: the nearest-CTH engine (AI replaced by a stub)."""
import pytest

from app.core import customs_tariff as ct
from app.core import customs_tariff_match as m
from app.models.customs_tariff import CthTariffSuggestion, CustomsTariff, CustomsTariffUpload

CODES = ["85177910", "85177920", "85177990", "85176290", "85176210",
         "84733020", "84733099", "39231090", "39239090"]


@pytest.fixture()
def tariff(db_session):
    up = CustomsTariffUpload(file_name="t.xlsx", total_codes=len(CODES))
    db_session.add(up)
    db_session.flush()
    for c in CODES:
        db_session.add(CustomsTariff(cth=c, rate="10%", unit="KGS", is_active=True, upload_id=up.id))
    db_session.add(CustomsTariff(cth="85177000", rate="10%", unit="KGS", is_active=False))
    db_session.commit()
    ct._ACTIVE.clear()
    return db_session


class FakeAI:
    """Answers by step: guesses[n] for step a, picks[n] for step b. Records each call."""

    def __init__(self, guesses=None, picks=None):
        self.guesses, self.picks, self.calls = guesses or {}, picks or {}, []

    def __call__(self, system, user, tenant_id, n_lines):
        step = "guess" if system == m._GUESS_SYSTEM else "pick"
        self.calls.append((step, user))
        table = self.guesses if step == "guess" else self.picks
        return {n: {"n": n, "cth": c, "confidence": "high"} for n, c in table.items()}


def test_document_cth_in_tariff_needs_no_ai(tariff, monkeypatch):
    ai = FakeAI()
    monkeypatch.setattr(m, "_ask", ai)
    s = m.suggest_one(tariff, doc_cth="8517.79.90", description="PCB")
    assert (s.cth, s.how) == ("85177990", "document") and ai.calls == []


def test_foreign_code_narrows_to_its_subheading(tariff, monkeypatch):
    ai = FakeAI(picks={1: "85177910"})
    monkeypatch.setattr(m, "_ask", ai)
    s = m.suggest_one(tariff, doc_cth="8517.79.0000", part_code="X1", description="Printed circuit board for router")
    assert (s.cth, s.how) == ("85177910", "ai")
    assert [c[0] for c in ai.calls] == ["pick"]                    # no guess step needed
    assert "85177920" in ai.calls[0][1] and "85176290" not in ai.calls[0][1]   # only 851779xx shown


def test_no_document_cth_guesses_then_picks(tariff, monkeypatch):
    ai = FakeAI(guesses={1: "85176299"}, picks={1: "85176290"})
    monkeypatch.setattr(m, "_ask", ai)
    s = m.suggest_one(tariff, part_code="R-55", description="Wireless access point")
    assert (s.cth, s.how) == ("85176290", "ai")
    assert [c[0] for c in ai.calls] == ["guess", "pick"]


def test_answer_outside_the_list_is_never_used(tariff, monkeypatch):
    monkeypatch.setattr(m, "_ask", FakeAI(picks={1: "99999999"}))
    s = m.suggest_one(tariff, doc_cth="39231000", description="Plastic box")
    assert s.cth == "39231090" and s.how == "fallback"            # the 'Other' code of 3923


def test_ai_down_still_gives_an_allowed_code(tariff, monkeypatch):
    monkeypatch.setattr(m, "_ask", lambda *a: {})
    s = m.suggest_one(tariff, doc_cth="847330", description="Laptop part")
    assert s.cth in CODES and s.how == "fallback" and s.cth == "84733099"


def test_inactive_code_is_not_used(tariff, monkeypatch):
    monkeypatch.setattr(m, "_ask", FakeAI(picks={1: "85177000"}))
    s = m.suggest_one(tariff, doc_cth="85177000", description="Parts")
    assert s.cth != "85177000" and s.cth in CODES


def test_remembered_per_tenant(tariff, monkeypatch):
    ai = FakeAI(picks={1: "85177920"})
    monkeypatch.setattr(m, "_ask", ai)
    m.suggest_one(tariff, doc_cth="851779", description="Antenna", tenant_id="t1")
    assert tariff.query(CthTariffSuggestion).count() == 1
    s = m.suggest_one(tariff, doc_cth="851779", description=" antenna ", tenant_id="t1")
    assert (s.cth, s.how) == ("85177920", "ai") and len(ai.calls) == 1        # no second call
    m.suggest_one(tariff, doc_cth="851779", description="Antenna", tenant_id="t2")
    assert len(ai.calls) == 2                                                # other tenant: asked


def test_batch_one_call_per_step_and_nothing_to_go_on(tariff, monkeypatch):
    ai = FakeAI(guesses={1: "39239000"}, picks={1: "85177990", 2: "39239090"})
    monkeypatch.setattr(m, "_ask", ai)
    lines = [m.Line("a", "851779", "", "Board"), m.Line("b", "", "", "Plastic crate"),
             m.Line("c", "85176210", "", ""), m.Line("d")]
    out = m.suggest_many(tariff, lines, None, remember=False)
    assert out["a"].cth == "85177990" and out["b"].cth == "39239090"
    assert out["c"].how == "document" and out["d"].cth == "" and out["d"].note
    assert [c[0] for c in ai.calls] == ["guess", "pick"]


def test_no_tariff_loaded(db_session):
    ct._ACTIVE.clear()
    s = m.suggest_one(db_session, doc_cth="85177990")
    assert s.cth == "" and "not been uploaded" in s.note


def test_residual():
    assert m.residual(["85177910", "85177920", "85177990"]) == "85177990"
    assert m.residual(["01012100"]) == "01012100"
    assert m.residual([]) == ""
