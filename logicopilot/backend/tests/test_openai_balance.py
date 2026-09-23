"""Manually-entered OpenAI account balance + expiry (Spend Analytics > OpenAI Account
Balance) - there is no OpenAI API, not even the Admin key, that returns this, so a Super
Admin types it in; the app just stores and displays it, plus a live USD->INR conversion."""
from datetime import date
from unittest.mock import patch

import pytest

from app.core.system_settings import (
    InvalidOpenAIBalance,
    get_system_settings,
    set_openai_balance,
)
from tests.conftest import login, make_tenant, make_user


def test_balance_is_none_by_default(db_session):
    row = get_system_settings(db_session)
    assert row.openai_balance_usd is None
    assert row.openai_balance_expiry is None


def test_setting_a_balance_and_expiry(db_session):
    set_openai_balance(db_session, 250.75, date(2027, 3, 1))
    row = get_system_settings(db_session)
    assert row.openai_balance_usd == 250.75
    assert row.openai_balance_expiry == date(2027, 3, 1)


def test_setting_balance_without_an_expiry_is_allowed(db_session):
    set_openai_balance(db_session, 100.0, None)
    row = get_system_settings(db_session)
    assert row.openai_balance_usd == 100.0
    assert row.openai_balance_expiry is None


def test_clearing_the_balance_with_none(db_session):
    set_openai_balance(db_session, 100.0, date(2027, 1, 1))
    set_openai_balance(db_session, None, None)
    row = get_system_settings(db_session)
    assert row.openai_balance_usd is None
    assert row.openai_balance_expiry is None


def test_negative_balance_is_refused(db_session):
    with pytest.raises(InvalidOpenAIBalance):
        set_openai_balance(db_session, -5.0, None)
    # Refused means untouched.
    assert get_system_settings(db_session).openai_balance_usd is None


def test_balance_endpoint_requires_super_admin(client, db_session):
    tenant = make_tenant(db_session)
    make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-balance@example.com")
    login(client, "ta-balance@example.com")
    resp = client.post("/api/v1/system-settings/openai-balance", json={"balance_usd": 100.0})
    assert resp.status_code == 403


def test_balance_endpoint_sets_and_is_reflected_in_settings(client, db_session):
    make_user(db_session, role="super_admin", email="sa-balance1@example.com")
    login(client, "sa-balance1@example.com")

    resp = client.post("/api/v1/system-settings/openai-balance",
                       json={"balance_usd": 342.5, "expiry_date": "2027-06-30"})
    assert resp.status_code == 200
    assert resp.json() == {"openai_balance_usd": 342.5, "openai_balance_expiry": "2027-06-30"}

    resp = client.get("/api/v1/system-settings")
    assert resp.json()["openai_balance_usd"] == 342.5
    assert resp.json()["openai_balance_expiry"] == "2027-06-30"


def test_balance_endpoint_returns_400_for_negative_balance(client, db_session):
    make_user(db_session, role="super_admin", email="sa-balance2@example.com")
    login(client, "sa-balance2@example.com")
    resp = client.post("/api/v1/system-settings/openai-balance", json={"balance_usd": -1.0})
    assert resp.status_code == 400


def test_balance_endpoint_with_no_body_fields_clears_it(client, db_session):
    make_user(db_session, role="super_admin", email="sa-balance3@example.com")
    login(client, "sa-balance3@example.com")
    client.post("/api/v1/system-settings/openai-balance", json={"balance_usd": 50.0})
    resp = client.post("/api/v1/system-settings/openai-balance", json={})
    assert resp.status_code == 200
    assert resp.json() == {"openai_balance_usd": None, "openai_balance_expiry": None}


def test_usd_inr_rate_endpoint_requires_super_admin(client, db_session):
    tenant = make_tenant(db_session)
    make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-rate@example.com")
    login(client, "ta-rate@example.com")
    resp = client.get("/api/v1/system-settings/usd-inr-rate")
    assert resp.status_code == 403


def test_usd_inr_rate_endpoint_returns_the_live_rate(client, db_session):
    make_user(db_session, role="super_admin", email="sa-rate1@example.com")
    login(client, "sa-rate1@example.com")
    with patch("app.api.v1.system_settings.fetch_usd_to_inr_rate", return_value=(95.96, "2026-09-15")):
        resp = client.get("/api/v1/system-settings/usd-inr-rate")
    assert resp.status_code == 200
    assert resp.json() == {"rate": 95.96, "as_of": "2026-09-15"}


def test_usd_inr_rate_endpoint_returns_502_when_unreachable(client, db_session):
    from app.core.currency import CurrencyRateError

    make_user(db_session, role="super_admin", email="sa-rate2@example.com")
    login(client, "sa-rate2@example.com")
    with patch("app.api.v1.system_settings.fetch_usd_to_inr_rate",
               side_effect=CurrencyRateError("unreachable")):
        resp = client.get("/api/v1/system-settings/usd-inr-rate")
    assert resp.status_code == 502
