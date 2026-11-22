"""Profile page: a user edits their OWN name and phone, and sets/removes their photo."""
import uuid

import pytest

from app.core.config import get_settings
from app.models.user import OPERATOR, SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _uploads_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))


def _signed_in(client, db_session, role=OPERATOR, tenant=None, email=None):
    tenant = tenant or make_tenant(db_session)
    if role == OPERATOR:
        # A Gate Keeper can only sign in while their tenant has an active Tenant Admin.
        make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email=f"ta-{uuid.uuid4().hex}@example.com")
    user = make_user(db_session, role=role, tenant=tenant, email=email or f"{role}-{uuid.uuid4().hex}@example.com")
    assert login(client, user.email).status_code == 200
    return user


def test_update_own_name_and_phone(client, db_session):
    _signed_in(client, db_session)
    r = client.patch("/api/v1/auth/me", json={"full_name": "  Senthil   Pitchai ", "phone": "+91 98400 12345"})
    assert r.status_code == 200
    assert r.json()["full_name"] == "Senthil Pitchai"
    assert r.json()["phone"] == "+91 98400 12345"
    assert client.get("/api/v1/auth/me").json()["phone"] == "+91 98400 12345"


def test_blank_name_rejected_and_phone_can_be_cleared(client, db_session):
    _signed_in(client, db_session)
    assert client.patch("/api/v1/auth/me", json={"full_name": "   "}).status_code == 422
    client.patch("/api/v1/auth/me", json={"phone": "044-2345 6789"})
    r = client.patch("/api/v1/auth/me", json={"phone": ""})
    assert r.status_code == 200 and r.json()["phone"] is None


@pytest.mark.parametrize("bad", ["12345", "abc1234567", "+91 98400 12345 6789 0", "98400-12345#"])
def test_invalid_phone_rejected(client, db_session, bad):
    _signed_in(client, db_session)
    assert client.patch("/api/v1/auth/me", json={"phone": bad}).status_code == 422


def test_email_and_role_cannot_be_changed_here(client, db_session):
    user = _signed_in(client, db_session)
    r = client.patch("/api/v1/auth/me", json={"email": "x@example.com", "role": "super_admin"})
    assert r.status_code == 200
    assert r.json()["email"] == user.email and r.json()["role"] == OPERATOR


def test_avatar_upload_serve_replace_delete(client, db_session, tmp_path):
    user = _signed_in(client, db_session)
    r = client.post("/api/v1/auth/me/avatar", files={"file": ("me.png", PNG, "image/png")})
    assert r.status_code == 200
    url = r.json()["avatar_url"]
    assert url.startswith(f"/api/v1/auth/avatar/{user.id}?v=")
    got = client.get(url)
    assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.content == PNG

    first_files = list((tmp_path / "avatars").iterdir())
    r = client.post("/api/v1/auth/me/avatar", files={"file": ("me.jpg", JPG, "image/jpeg")})
    assert r.status_code == 200 and r.json()["avatar_url"] != url
    files = list((tmp_path / "avatars").iterdir())
    assert len(files) == 1 and files[0] not in first_files  # old photo removed

    r = client.delete("/api/v1/auth/me/avatar")
    assert r.status_code == 200 and r.json()["avatar_url"] is None
    assert list((tmp_path / "avatars").iterdir()) == []


def test_avatar_rejects_non_images_and_large_files(client, db_session):
    _signed_in(client, db_session)
    fake = client.post("/api/v1/auth/me/avatar", files={"file": ("x.png", b"not an image at all", "image/png")})
    assert fake.status_code == 422
    big = client.post("/api/v1/auth/me/avatar", files={"file": ("x.png", PNG + b"\x00" * (2 * 1024 * 1024), "image/png")})
    assert big.status_code == 413


def test_avatar_not_visible_to_another_tenant(client, db_session):
    tenant_a = make_tenant(db_session, name="A")
    owner = _signed_in(client, db_session, tenant=tenant_a)
    url = client.post("/api/v1/auth/me/avatar", files={"file": ("me.png", PNG, "image/png")}).json()["avatar_url"]

    client.post("/api/v1/auth/logout")
    _signed_in(client, db_session, role=TENANT_ADMIN, tenant=tenant_a)
    assert client.get(url).status_code == 200  # same tenant

    client.post("/api/v1/auth/logout")
    _signed_in(client, db_session, tenant=make_tenant(db_session, name="B"))
    assert client.get(url).status_code == 404  # other tenant

    client.post("/api/v1/auth/logout")
    sa = make_user(db_session, role=SUPER_ADMIN, email=f"sa-{uuid.uuid4().hex}@example.com")
    login(client, sa.email)
    assert client.get(f"/api/v1/auth/avatar/{owner.id}?v=1").status_code == 200
