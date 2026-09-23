import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import TenantScope, get_db, get_tenant_scope, require_role, scoped_query
from app.models.user import TENANT_ADMIN, User
from app.models.user_type import UserType
from app.schemas.user_type import UserTypeCreate, UserTypeOut, UserTypeUpdate

router = APIRouter(prefix="/user-types", tags=["user-types"])

logger = logging.getLogger(__name__)


@router.get("", response_model=list[UserTypeOut])
def list_user_types(
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    _: User = Depends(require_role(TENANT_ADMIN)),
) -> list[UserTypeOut]:
    return scoped_query(db, UserType, scope).order_by(UserType.created_at).all()


@router.post("", response_model=UserTypeOut, status_code=status.HTTP_201_CREATED)
def create_user_type(
    payload: UserTypeCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role(TENANT_ADMIN)),
) -> UserTypeOut:
    clash = (
        db.query(UserType)
        .filter(UserType.tenant_id == actor.tenant_id, UserType.name.ilike(payload.name))
        .first()
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'A user type named "{payload.name}" already exists.',
        )
    # Manager's whole design is that it can never be given write access — a custom type
    # built on manager inherits that same lock, same as the Masters page keeping Manager's
    # own Write checkbox permanently disabled.
    write_enabled = False if payload.base_role == "manager" else payload.write_enabled
    user_type = UserType(
        tenant_id=actor.tenant_id,
        name=payload.name,
        base_role=payload.base_role,
        write_enabled=write_enabled,
    )
    db.add(user_type)
    db.commit()
    db.refresh(user_type)
    return user_type


@router.patch("/{type_id}", response_model=UserTypeOut)
def update_user_type(
    type_id: str,
    payload: UserTypeUpdate,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    _: User = Depends(require_role(TENANT_ADMIN)),
) -> UserTypeOut:
    user_type = scoped_query(db, UserType, scope).filter(UserType.id == type_id).first()
    if user_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User type not found")
    user_type.write_enabled = False if user_type.base_role == "manager" else payload.write_enabled
    db.add(user_type)
    db.commit()
    db.refresh(user_type)
    return user_type


@router.delete("/{type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_type(
    type_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    _: User = Depends(require_role(TENANT_ADMIN)),
) -> None:
    user_type = scoped_query(db, UserType, scope).filter(UserType.id == type_id).first()
    if user_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User type not found")
    in_use = db.query(User).filter(User.user_type_id == user_type.id).count()
    if in_use:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{in_use} user(s) are still set to this type — move or delete them first.",
        )
    db.delete(user_type)
    db.commit()
