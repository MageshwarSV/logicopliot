from dataclasses import dataclass
from typing import Generator, Type, TypeVar

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Query, Session

from app.core.security import TokenType, decode_token
from app.db.session import SessionLocal
from app.models.user import ADMIN, GK2, OPERATOR, SUPER_ADMIN, User

ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"

ModelT = TypeVar("ModelT")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    if payload.get("type") != TokenType.ACCESS.value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    return user


def require_role(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return dependency


def require_write_access(*roles: str):
    """Same role check as require_role, for use on WRITE endpoints only. Additionally: a
    Tenant Admin can lock their operator/gk2 users to read-only on the "Masters" page
    (Tenant.role_write_enabled) — the same restriction Manager always has, just made
    optional and per-tenant for these two roles instead of fixed. A missing tenant, a
    missing key, or a null column all mean read-and-write, so this is a no-op for every
    tenant that existed before the setting did. Every other role passed in `roles` is
    unaffected — only OPERATOR/GK2 are ever checked against the flag."""
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        if user.role in (OPERATOR, GK2):
            write_enabled = True
            if user.user_type_id is not None and user.user_type is not None:
                # A custom type's own Write rule replaces the tenant-wide default for its
                # base role — that granularity is the whole point of creating one instead of
                # using Gate Keeper 1/2 directly.
                write_enabled = user.user_type.write_enabled
            elif user.tenant is not None and user.tenant.role_write_enabled:
                write_enabled = user.tenant.role_write_enabled.get(user.role, True)
            if not write_enabled:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Your account is set to read-only by your Tenant Admin.",
                )
        return user

    return dependency


@dataclass
class TenantScope:
    tenant_id: str | None
    is_super_admin: bool


def get_tenant_scope(
    user: User = Depends(get_current_user),
    tenant_id: str | None = None,
) -> TenantScope:
    """Super admins and admins are unrestricted (optionally narrowed via ?tenant_id=, which
    is how the jobs list's tenant-filter dropdown works); everyone else is forced to their
    own tenant regardless of what's passed in.

    is_super_admin is deliberately not renamed to something broader: it drives query SCOPE
    only (which tenant's rows a query may touch). It grants no endpoint by itself — an admin
    reaches exactly the routes that name ADMIN in their own require_role(...), same as any
    other role. See app/models/user.py for why admin has no tenant_id to fall back on.
    """
    if user.role in (SUPER_ADMIN, ADMIN):
        return TenantScope(tenant_id=tenant_id, is_super_admin=True)
    return TenantScope(tenant_id=user.tenant_id, is_super_admin=False)


def scoped_query(db: Session, model: Type[ModelT], scope: TenantScope) -> Query:
    """The single tenant-filtering choke point — every tenant-scoped route must go through
    this instead of hand-written `.filter(tenant_id==...)` so isolation can't be forgotten."""
    query = db.query(model)
    if scope.tenant_id is not None:
        query = query.filter(model.tenant_id == scope.tenant_id)
    return query
