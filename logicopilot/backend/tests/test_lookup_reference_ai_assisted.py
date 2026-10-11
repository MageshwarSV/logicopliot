"""lookup_reference_ai_assisted (app/core/reference_cache.py) - the second step of a multi-key
fuzzy lookup, reached only after an exact match has already failed (see
_resolve_target_value's own two-step call in jobs.py). Every key except the last is matched
exactly in SQL first; the closest-ranked candidates on the last key are then handed to the AI
(mocked here - see test_resolve_fuzzy_candidate_with_ai.py for that function's own tests)."""
from unittest.mock import patch

from app.core.reference_cache import lookup_reference_ai_assisted, remember_reference
from app.models.custom_field import CustomField
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_field(db_session, label_name="Shipment Port Code"):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    cf = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name=label_name,
        kind="hardcoded", is_target_value=True, fuzzy_match=True,
        lookup_key_label="Shipment Country,Shipment City",
    )
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    return cf


def test_only_candidates_sharing_the_exact_first_key_are_ever_offered_to_the_ai(db_session):
    cf = _make_field(db_session)
    remember_reference(db_session, cf.id, ["CHINA", "Pudong/Shanghai"], "CNPDG")
    remember_reference(db_session, cf.id, ["CHINA", "Shanghai Pt"], "CNSGP")
    remember_reference(db_session, cf.id, ["CANADA", "Shanghai"], "CAXYZ")  # wrong country
    db_session.commit()

    with patch("app.core.llm.resolve_fuzzy_candidate_with_ai", return_value="CNSGP") as mocked:
        result = lookup_reference_ai_assisted(db_session, cf.id, ["CHINA", "Shanghai"])

    assert result == "CNSGP"
    offered = {code for _, code in mocked.call_args.args[1]}
    assert offered == {"CNPDG", "CNSGP"}  # never the Canadian row


def test_returns_none_when_no_row_shares_the_exact_first_key(db_session):
    cf = _make_field(db_session)
    remember_reference(db_session, cf.id, ["CANADA", "Vancouver"], "CAVAN")
    db_session.commit()

    with patch("app.core.llm.resolve_fuzzy_candidate_with_ai") as mocked:
        result = lookup_reference_ai_assisted(db_session, cf.id, ["CHINA", "Shanghai"])

    assert result is None
    mocked.assert_not_called()


def test_returns_none_when_the_ai_decides_nothing_genuinely_matches(db_session):
    cf = _make_field(db_session)
    remember_reference(db_session, cf.id, ["CHINA", "Shanghai"], "CNSHA")
    db_session.commit()

    with patch("app.core.llm.resolve_fuzzy_candidate_with_ai", return_value=""):
        assert lookup_reference_ai_assisted(db_session, cf.id, ["CHINA", "Atlantis"]) is None


def test_a_single_key_returns_none_without_querying_or_calling_the_ai(db_session):
    """Nothing to pre-filter on with only one key - lookup_reference's own fuzzy=True path
    handles that case directly instead (a freight forwarder's name, the original use)."""
    cf = _make_field(db_session, label_name="Freight Forwarder")
    remember_reference(db_session, cf.id, ["KUEHNE + NAGEL"], "SOME_CODE")
    db_session.commit()

    with patch("app.core.llm.resolve_fuzzy_candidate_with_ai") as mocked:
        assert lookup_reference_ai_assisted(db_session, cf.id, ["KUEHNE + NAGEL"]) is None
    mocked.assert_not_called()


def test_candidate_list_is_capped_even_with_many_rows_sharing_the_first_key(db_session):
    from app.core.reference_cache import AI_ASSISTED_CANDIDATE_LIMIT

    cf = _make_field(db_session)
    for i in range(AI_ASSISTED_CANDIDATE_LIMIT + 10):
        remember_reference(db_session, cf.id, ["CHINA", f"Port {i}"], f"CN{i:03d}")
    db_session.commit()

    with patch("app.core.llm.resolve_fuzzy_candidate_with_ai", return_value="") as mocked:
        lookup_reference_ai_assisted(db_session, cf.id, ["CHINA", "Port 1"])

    assert len(mocked.call_args.args[1]) == AI_ASSISTED_CANDIDATE_LIMIT
