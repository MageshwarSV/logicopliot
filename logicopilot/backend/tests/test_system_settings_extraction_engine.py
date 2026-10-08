"""Which engine production document classification/extraction use - a live, database-backed
Super Admin setting (Settings page), defaulting to "ocr_gpt4o_mini" (today's behavior) so
nothing changes until it is deliberately flipped. Never affects the Template Wizard's own
training flow (demo_extract/test-extract/build_field_profile), which always stays on OCR
text + gpt-4o-mini regardless of this setting."""
import pytest

from app.core.system_settings import (
    VALID_EXTRACTION_ENGINES,
    InvalidExtractionEngine,
    get_extraction_engine,
    set_extraction_engine,
)
from tests.conftest import login, make_tenant, make_user


def test_defaults_to_ocr_gpt4o_mini(db_session):
    assert get_extraction_engine(db_session) == "ocr_gpt4o_mini"


def test_setting_the_vision_engine(db_session):
    set_extraction_engine(db_session, "gpt5_mini_vision")
    assert get_extraction_engine(db_session) == "gpt5_mini_vision"


def test_switching_back_to_the_old_engine(db_session):
    set_extraction_engine(db_session, "gpt5_mini_vision")
    set_extraction_engine(db_session, "ocr_gpt4o_mini")
    assert get_extraction_engine(db_session) == "ocr_gpt4o_mini"


def test_an_unrecognized_engine_is_refused_not_silently_coerced(db_session):
    with pytest.raises(InvalidExtractionEngine):
        set_extraction_engine(db_session, "some_typo")
    # Refused means untouched - still at the default.
    assert get_extraction_engine(db_session) == "ocr_gpt4o_mini"


def test_valid_engines_tuple_matches_what_the_setter_accepts(db_session):
    for engine in VALID_EXTRACTION_ENGINES:
        set_extraction_engine(db_session, engine)
        assert get_extraction_engine(db_session) == engine


def test_endpoint_requires_super_admin(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-engine1@example.com")
    login(client, admin.email)
    resp = client.post("/api/v1/system-settings/extraction-engine", json={"engine": "gpt5_mini_vision"})
    assert resp.status_code == 403


def test_endpoint_sets_a_valid_engine(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-engine1@example.com")
    login(client, admin.email)
    resp = client.post("/api/v1/system-settings/extraction-engine", json={"engine": "gpt5_mini_vision"})
    assert resp.status_code == 200
    assert resp.json()["extraction_engine"] == "gpt5_mini_vision"

    resp = client.get("/api/v1/system-settings")
    assert resp.json()["extraction_engine"] == "gpt5_mini_vision"


def test_endpoint_returns_400_for_an_unknown_engine(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-engine2@example.com")
    login(client, admin.email)
    resp = client.post("/api/v1/system-settings/extraction-engine", json={"engine": "nonsense"})
    assert resp.status_code == 400
    assert "nonsense" in resp.json()["detail"]


def test_get_system_settings_reports_the_default_before_anything_is_set(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-engine3@example.com")
    login(client, admin.email)
    resp = client.get("/api/v1/system-settings")
    assert resp.json()["extraction_engine"] == "ocr_gpt4o_mini"
