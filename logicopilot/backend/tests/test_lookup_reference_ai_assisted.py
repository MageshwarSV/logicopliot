"""lookup_reference_ai_assisted (app/core/reference_cache.py) - the second step of a multi-key
fuzzy lookup, reached only after an exact match has already failed (see
_resolve_target_value's own two-step call in jobs.py). Every key except the last is matched
exactly in SQL first; the model is then consulted in up to two ways - both mocked here, see
their own test files for how each one individually behaves:

1. normalize_place_name_with_ai (test_normalize_place_name_with_ai.py) - a pure translation
   step with no candidate list, tried first. Needed because a GENUINE rename ("Peking" ->
   "Beijing") shares almost no characters with its current name, so step 2's string-similarity
   ranking would never put the right row in front of the model at all.
2. resolve_fuzzy_candidate_with_ai (test_resolve_fuzzy_candidate_with_ai.py) - ranks by string
   similarity and asks the model to pick from a short list, for OCR noise / minor spelling
   variants the normalize step isn't meant to catch.
"""
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


# Every test mocks normalize_place_name_with_ai to "" (no rename found) unless it is
# specifically what's under test, so the other tests exercise step 2 (rank + pick) in
# isolation, deterministically.
_NO_RENAME = patch("app.core.llm.normalize_place_name_with_ai", return_value="")


def test_only_candidates_sharing_the_exact_first_key_are_ever_offered_to_the_ai(db_session):
    cf = _make_field(db_session)
    remember_reference(db_session, cf.id, ["CHINA", "Pudong/Shanghai"], "CNPDG")
    remember_reference(db_session, cf.id, ["CHINA", "Shanghai Pt"], "CNSGP")
    remember_reference(db_session, cf.id, ["CANADA", "Shanghai"], "CAXYZ")  # wrong country
    db_session.commit()

    with _NO_RENAME, \
         patch("app.core.llm.resolve_fuzzy_candidate_with_ai", return_value="CNSGP") as mocked:
        result = lookup_reference_ai_assisted(db_session, cf.id, ["CHINA", "Shanghai"])

    assert result == "CNSGP"
    offered = {code for _, code in mocked.call_args.args[1]}
    assert offered == {"CNPDG", "CNSGP"}  # never the Canadian row


def test_returns_none_when_no_row_shares_the_exact_first_key(db_session):
    cf = _make_field(db_session)
    remember_reference(db_session, cf.id, ["CANADA", "Vancouver"], "CAVAN")
    db_session.commit()

    with _NO_RENAME, patch("app.core.llm.resolve_fuzzy_candidate_with_ai") as mocked:
        result = lookup_reference_ai_assisted(db_session, cf.id, ["CHINA", "Shanghai"])

    assert result is None
    mocked.assert_not_called()


def test_returns_none_when_the_ai_decides_nothing_genuinely_matches(db_session):
    cf = _make_field(db_session)
    remember_reference(db_session, cf.id, ["CHINA", "Shanghai"], "CNSHA")
    db_session.commit()

    with _NO_RENAME, patch("app.core.llm.resolve_fuzzy_candidate_with_ai", return_value=""):
        assert lookup_reference_ai_assisted(db_session, cf.id, ["CHINA", "Atlantis"]) is None


def test_a_single_key_returns_none_without_querying_or_calling_the_ai(db_session):
    """Nothing to pre-filter on with only one key - lookup_reference's own fuzzy=True path
    handles that case directly instead (a freight forwarder's name, the original use)."""
    cf = _make_field(db_session, label_name="Freight Forwarder")
    remember_reference(db_session, cf.id, ["KUEHNE + NAGEL"], "SOME_CODE")
    db_session.commit()

    with patch("app.core.llm.normalize_place_name_with_ai") as norm_mocked, \
         patch("app.core.llm.resolve_fuzzy_candidate_with_ai") as pick_mocked:
        assert lookup_reference_ai_assisted(db_session, cf.id, ["KUEHNE + NAGEL"]) is None
    norm_mocked.assert_not_called()
    pick_mocked.assert_not_called()


def test_candidate_list_is_capped_even_with_many_rows_sharing_the_first_key(db_session):
    from app.core.reference_cache import AI_ASSISTED_CANDIDATE_LIMIT

    cf = _make_field(db_session)
    for i in range(AI_ASSISTED_CANDIDATE_LIMIT + 10):
        remember_reference(db_session, cf.id, ["CHINA", f"Port {i}"], f"CN{i:03d}")
    db_session.commit()

    with _NO_RENAME, \
         patch("app.core.llm.resolve_fuzzy_candidate_with_ai", return_value="") as mocked:
        lookup_reference_ai_assisted(db_session, cf.id, ["CHINA", "Port 1"])

    assert len(mocked.call_args.args[1]) == AI_ASSISTED_CANDIDATE_LIMIT


# ---- the normalize-first step: catches a genuine rename, not just spelling noise ----------


def test_a_genuine_rename_resolves_via_normalization_with_no_candidate_list_needed(db_session):
    """"Saigon" and "Ho Chi Minh City" share almost no characters - string-similarity ranking
    would never put the right row in the candidate list at all. The normalize step is what
    actually catches this, via an exact match on the TRANSLATED name - no pick-from-a-list
    call needed once that lands."""
    cf = _make_field(db_session)
    remember_reference(db_session, cf.id, ["VIETNAM", "Ho Chi Minh City"], "VNSGN")
    remember_reference(db_session, cf.id, ["VIETNAM", "Saigon Petro Oil Terminal"], "VNOSP")
    db_session.commit()

    with patch("app.core.llm.normalize_place_name_with_ai", return_value="Ho Chi Minh City") as norm_mocked, \
         patch("app.core.llm.resolve_fuzzy_candidate_with_ai") as pick_mocked:
        result = lookup_reference_ai_assisted(db_session, cf.id, ["VIETNAM", "Saigon"])

    assert result == "VNSGN"
    norm_mocked.assert_called_once_with("Saigon", field_description="a value")
    pick_mocked.assert_not_called()  # the exact hit on the normalized name was enough


def test_a_rename_that_still_does_not_exactly_match_falls_through_to_rank_and_pick(db_session):
    """The normalized name does not land on the whole table - only ONE of the two steps needs
    to find the answer, and whichever one does wins."""
    cf = _make_field(db_session)
    remember_reference(db_session, cf.id, ["VIETNAM", "Ho Chi Minh, VICT"], "VNVIC")
    db_session.commit()

    with patch("app.core.llm.normalize_place_name_with_ai", return_value="Ho Chi Minh City"), \
         patch("app.core.llm.resolve_fuzzy_candidate_with_ai", return_value="VNVIC") as pick_mocked:
        result = lookup_reference_ai_assisted(db_session, cf.id, ["VIETNAM", "Saigon"])

    assert result == "VNVIC"
    pick_mocked.assert_called_once()
    # The candidate list and the query text both reflect the NORMALIZED name, not the raw
    # "Saigon" - much closer to the sheet's own spelling than the original ever was.
    assert pick_mocked.call_args.args[0] == "HO CHI MINH CITY"
