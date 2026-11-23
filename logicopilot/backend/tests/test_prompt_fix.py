"""Fix screen: Apply saves through the template update and keeps history, Revert restores,
AI Suggestion never saves, Test re-reads without touching jobs, lookups cannot be fixed."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core import prompt_fix
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.prompt_change import PromptChange
from app.models.user import SUPER_ADMIN
from tests.conftest import make_tenant, make_user
from tests.test_extraction_corrections import _edit, _setup


def _field(db):
    tenant = make_tenant(db)
    group, tdoc, fvs = _setup(db, tenant, n_jobs=3)
    mark = db.query(FieldMark).filter(FieldMark.document_id == tdoc.id).one()
    mark.extraction_prompt, mark.value_format_hint = "Return the HAWB number.", "Numeric string"
    db.commit()
    for i, fv in enumerate(fvs):
        _edit(db, fv, f"9AN567{i}")
    return tenant, group, tdoc, mark, fvs


def test_info_apply_and_revert(db_session):
    tenant, group, tdoc, mark, _ = _field(db_session)
    sa = make_user(db_session, role=SUPER_ADMIN, email="sa-fix1@example.com", full_name="Senthil")
    i = prompt_fix.info(db_session, group.id, tdoc.id, "HBL No")
    assert i["editable"] and i["prompt"] == "Return the HAWB number." and i["format_hint"] == "Numeric string"

    prompt_fix.apply(db_session, group.id, tdoc.id, "HBL No", "Return the HAWB exactly as printed.",
                     "Letters and digits", sa)
    db_session.refresh(mark)
    assert mark.extraction_prompt == "Return the HAWB exactly as printed."
    assert mark.value_format_hint == "Letters and digits"
    change = db_session.query(PromptChange).one()
    assert (change.old_prompt, change.new_prompt, change.changed_by_name) == (
        "Return the HAWB number.", "Return the HAWB exactly as printed.", "Senthil")

    prompt_fix.revert(db_session, change.id, group.id, tdoc.id, "HBL No", sa)
    db_session.refresh(mark)
    assert mark.extraction_prompt == "Return the HAWB number." and mark.value_format_hint == "Numeric string"
    assert [c.action for c in db_session.query(PromptChange).order_by(PromptChange.created_at)] == ["apply", "revert"]


def test_unchanged_or_empty_prompt_is_refused(db_session):
    _t, group, tdoc, _m, _ = _field(db_session)
    with pytest.raises(HTTPException):
        prompt_fix.apply(db_session, group.id, tdoc.id, "HBL No", "Return the HAWB number.", "Numeric string", None)
    with pytest.raises(HTTPException):
        prompt_fix.apply(db_session, group.id, tdoc.id, "HBL No", "  ", None, None)


def test_lookup_field_cannot_be_fixed(db_session):
    tenant = make_tenant(db_session)
    group, _tdoc, _ = _setup(db_session, tenant, n_jobs=1)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="ContainerTypeCode", kind="lookup"))
    db_session.commit()
    i = prompt_fix.info(db_session, group.id, None, "ContainerTypeCode")
    assert i["editable"] is False and "reference" in i["reason"]


def test_suggestion_is_shown_never_saved(db_session, monkeypatch):
    _t, group, tdoc, mark, _ = _field(db_session)
    sent = {}

    def fake(**kw):
        sent.update(kw)
        content = '{"analysis": "Letters were dropped.", "prompt": "Keep every letter.", "format_hint": "Alphanumeric"}'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr("app.core.llm.create_chat_completion_with_retry", fake)
    monkeypatch.setattr(prompt_fix, "extraction_model", lambda db: ("gpt-4o-mini", False))
    out = prompt_fix.suggest(db_session, group.id, tdoc.id, "HBL No")
    assert out["prompt"] == "Keep every letter." and out["format_hint"] == "Alphanumeric"
    assert sent["model"] == "gpt-4o-mini"
    user_text = sent["messages"][1]["content"]
    assert "95670" in user_text and "9AN5670" in user_text          # the before/after pairs went
    db_session.refresh(mark)
    assert mark.extraction_prompt == "Return the HAWB number."      # nothing saved


def test_test_rereads_and_compares_without_changing_jobs(db_session, session_factory, monkeypatch):
    _t, group, tdoc, mark, fvs = _field(db_session)
    monkeypatch.setattr(prompt_fix, "TEST_WORKERS", 1)     # one SQLite connection in tests
    monkeypatch.setattr(prompt_fix, "_doc_text", lambda jd: "HAWB 9AN5670")
    monkeypatch.setattr(prompt_fix, "extraction_model", lambda db: ("gpt-4o-mini", False))
    monkeypatch.setattr("app.core.extraction.extract_document_fields",
                        lambda text, specs, tenant_id=None: {"HBL No": "9AN5670"})
    out = prompt_fix.test(session_factory, group.id, tdoc.id, "HBL No", "Keep every letter.", None)
    assert out["total"] == 3 and out["old_correct"] == 0 and out["new_correct"] == 1
    for fv in fvs:
        db_session.refresh(fv)
    assert [fv.corrected_value for fv in fvs] == ["9AN5670", "9AN5671", "9AN5672"]


def test_same_value_is_strict():
    assert prompt_fix.same_value("9an 5670", "9AN5670")
    assert prompt_fix.same_value("1,450.00", "1450")
    assert not prompt_fix.same_value("95670", "9AN5670")
    assert prompt_fix.same_value("", None)
