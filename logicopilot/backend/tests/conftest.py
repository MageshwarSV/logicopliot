import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.deps import get_db
from app.core.security import hash_password
from app.db.base import Base
from app.main import app
from app.models.tenant import Tenant
from app.models.user import User


@pytest.fixture()
def test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


@pytest.fixture()
def db_session(session_factory):
    session = session_factory()
    yield session
    session.close()


@pytest.fixture()
def client(session_factory):
    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        # Mirrors the real frontend's axios client, which always sends this header
        # (see main.py's CSRF-mitigation middleware for mutating requests).
        test_client.headers.update({"X-Requested-With": "XMLHttpRequest"})
        yield test_client
    app.dependency_overrides.clear()


DEFAULT_PASSWORD = "TestPass123"


def make_tenant(db_session, name="Test Tenant", **kwargs) -> Tenant:
    tenant = Tenant(name=name, **kwargs)
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


def make_user(
    db_session,
    *,
    role: str,
    tenant: Tenant | None = None,
    email: str | None = None,
    password: str = DEFAULT_PASSWORD,
    full_name: str = "Test User",
    is_active: bool = True,
) -> User:
    email = email or f"{role}-{id(object())}@example.com"
    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        role=role,
        tenant_id=tenant.id if tenant else None,
        is_active=is_active,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def login(client: TestClient, email: str, password: str = DEFAULT_PASSWORD):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return response
