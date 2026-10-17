"""must_change_password: forced on creation and on an admin's password reset, cleared only by
the account owner's own self-service change-password call."""
from app.models.user import OPERATOR, SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import DEFAULT_PASSWORD, login, make_tenant, make_user


def test_new_user_must_change_password_on_creation(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    login(client, "ta@example.com")

    response = client.post(
        "/api/v1/users",
        json={
            "email": "new-op@example.com",
            "password": "OpPass1234",
            "full_name": "New Operator",
            "role": OPERATOR,
        },
    )
    assert response.status_code == 201
    assert response.json()["must_change_password"] is True


def test_tenant_admin_created_by_super_admin_must_change_password(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    tenant = make_tenant(db_session, name="Tenant A")
    login(client, "sa@example.com")

    response = client.post(
        "/api/v1/users",
        json={
            "email": "ta@example.com",
            "password": "TaPass1234",
            "full_name": "New Tenant Admin",
            "role": TENANT_ADMIN,
            "tenant_id": tenant.id,
        },
    )
    assert response.status_code == 201
    assert response.json()["must_change_password"] is True


def test_existing_user_unaffected_by_column_default(client, db_session):
    """An account that existed before this feature (and never had its password reset since)
    is never retroactively forced to change it - see the migration's backfill."""
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    response = login(client, "sa@example.com")
    assert response.json()["user"]["must_change_password"] is False


def test_admin_resetting_password_forces_change_again(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op@example.com")
    assert op.must_change_password is False
    login(client, "ta@example.com")

    response = client.patch(f"/api/v1/users/{op.id}", json={"password": "NewTempPass1"})
    assert response.status_code == 200
    assert response.json()["must_change_password"] is True


def test_editing_other_fields_does_not_force_password_change(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op@example.com")
    login(client, "ta@example.com")

    response = client.patch(f"/api/v1/users/{op.id}", json={"full_name": "Renamed"})
    assert response.status_code == 200
    assert response.json()["must_change_password"] is False


def test_change_password_requires_auth(client, db_session):
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "BrandNewPass1"},
    )
    assert response.status_code == 401


def test_change_password_wrong_current_password_rejected(client, db_session):
    user = make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    assert user.must_change_password is False
    login(client, "sa@example.com")

    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "totally-wrong", "new_password": "BrandNewPass1"},
    )
    assert response.status_code == 401


def test_change_password_too_short_rejected(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")

    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "short"},
    )
    assert response.status_code == 422


def test_change_password_success_clears_flag_and_new_password_works(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    login(client, "ta@example.com")
    create_response = client.post(
        "/api/v1/users",
        json={
            "email": "op@example.com",
            "password": "TempPass1234",
            "full_name": "New Operator",
            "role": OPERATOR,
        },
    )
    assert create_response.json()["must_change_password"] is True

    client.post("/api/v1/auth/logout")
    login_response = login(client, "op@example.com", password="TempPass1234")
    assert login_response.status_code == 200
    assert login_response.json()["user"]["must_change_password"] is True

    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "TempPass1234", "new_password": "MyOwnPassword1"},
    )
    assert response.status_code == 200
    assert response.json()["must_change_password"] is False

    # The old (temporary) password no longer works; the new one does, and the flag stays clear.
    client.post("/api/v1/auth/logout")
    assert login(client, "op@example.com", password="TempPass1234").status_code == 401
    relogin = login(client, "op@example.com", password="MyOwnPassword1")
    assert relogin.status_code == 200
    assert relogin.json()["user"]["must_change_password"] is False
