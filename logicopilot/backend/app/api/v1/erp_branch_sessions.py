"""Branch logins: one always-on ERP user per branch - see app/core/branch_sessions.py."""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_role
from app.core.mail_crypto import encrypt_secret
from app.models.erp_branch_session import ErpBranchSession
from app.models.erp_script import ErpScript
from app.models.user import SUPER_ADMIN, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/erp-branch-sessions", tags=["erp branch sessions"])

MAX_TABS = 20


class BranchSessionIn(BaseModel):
    branch: str
    login_script_id: str
    username: str | None = None
    password: str | None = None
    max_tabs: int = 3
    enabled: bool = True
    # Stream scripts allowed to use this login; empty = all.
    script_ids: list[str] = []


class BranchSessionPatch(BaseModel):
    branch: str | None = None
    login_script_id: str | None = None
    username: str | None = None
    # None = leave the saved password alone; "" = clear it (use the login script's own).
    password: str | None = None
    max_tabs: int | None = None
    enabled: bool | None = None
    script_ids: list[str] | None = None


class BranchSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    branch: str
    login_script_id: str
    username: str | None
    has_password: bool
    max_tabs: int
    enabled: bool
    script_ids: list[str]
    status: str
    status_note: str | None
    last_check_at: datetime | None
    live_since: datetime | None


def _out(bs: ErpBranchSession) -> BranchSessionOut:
    return BranchSessionOut(
        id=bs.id, tenant_id=bs.tenant_id, branch=bs.branch, login_script_id=bs.login_script_id,
        username=bs.username, has_password=bool(bs.password_encrypted), max_tabs=bs.max_tabs,
        enabled=bs.enabled, script_ids=list(bs.script_ids or []), status=bs.status, status_note=bs.status_note,
        last_check_at=bs.last_check_at, live_since=bs.live_since,
    )


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)


def _validate(db: Session, tenant_id: str, *, branch: str, login_script_id: str, username: str | None,
              max_tabs: int, script_ids: list[str] | None = None, own_id: str | None = None) -> None:
    if not branch.strip():
        raise _bad("Enter the branch name exactly as the job's branch field carries it, e.g. Chennai.")
    if not (1 <= max_tabs <= MAX_TABS):
        raise _bad(f"Jobs at once must be between 1 and {MAX_TABS}.")
    script = db.get(ErpScript, login_script_id)
    if script is None or script.tenant_id != tenant_id:
        raise _bad("Pick a login script of this tenant.")
    if script.checkpoint_index is None:
        raise _bad(f"{script.name!r} has no checkpoint. Mark the step where the login ends "
                   "(the flag) in its recorder first - the branch login replays up to there.")
    mine = set(script_ids or [])
    for sid in mine:
        sc = db.get(ErpScript, sid)
        if sc is None or sc.tenant_id != tenant_id:
            raise _bad("Every script ticked under Used by scripts must belong to this tenant.")
    others = db.query(ErpBranchSession).filter(ErpBranchSession.tenant_id == tenant_id).all()
    for o in others:
        if o.id == own_id:
            continue
        if o.branch.strip().casefold() == branch.strip().casefold():
            # Two logins may share a branch only for DIFFERENT scripts - otherwise a job could
            # not tell which one to use. Empty = all scripts, which overlaps with anything.
            theirs = set(o.script_ids or [])
            if not mine or not theirs or (mine & theirs):
                clash = sorted(mine & theirs) if (mine and theirs) else []
                names = ", ".join((db.get(ErpScript, c).name if db.get(ErpScript, c) else c) for c in clash)
                raise _bad(f"The {o.branch} branch already has a login for "
                           + (f"the {names} script(s)." if names else "the same scripts (empty = all scripts).")
                           + " Tick different scripts on each login of the same branch.")
        if username and (o.username or "").strip().casefold() == username.strip().casefold():
            # The ERP keeps one session per user: the second login would log the first out,
            # and the two branches would knock each other off all day.
            raise _bad(f"ERP user {username!r} is already the {o.branch} branch login. Each "
                       "branch needs its own ERP user - one user can only be logged in once.")


@router.get("", response_model=list[BranchSessionOut])
def list_branch_sessions(tenant_id: str, db: Session = Depends(get_db),
                         _=Depends(require_role(SUPER_ADMIN))) -> list[BranchSessionOut]:
    rows = (db.query(ErpBranchSession).filter(ErpBranchSession.tenant_id == tenant_id)
            .order_by(ErpBranchSession.branch).all())
    return [_out(r) for r in rows]


@router.post("", response_model=BranchSessionOut, status_code=status.HTTP_201_CREATED)
def create_branch_session(tenant_id: str, payload: BranchSessionIn, db: Session = Depends(get_db),
                          _=Depends(require_role(SUPER_ADMIN))) -> BranchSessionOut:
    _validate(db, tenant_id, branch=payload.branch, login_script_id=payload.login_script_id,
              username=payload.username, max_tabs=payload.max_tabs, script_ids=payload.script_ids)
    bs = ErpBranchSession(
        tenant_id=tenant_id, branch=payload.branch.strip(), login_script_id=payload.login_script_id,
        username=(payload.username or "").strip() or None,
        password_encrypted=encrypt_secret(payload.password) if payload.password else None,
        max_tabs=payload.max_tabs, enabled=payload.enabled, script_ids=payload.script_ids or None,
        status="starting" if payload.enabled else "off",
    )
    db.add(bs)
    db.commit()
    db.refresh(bs)
    return _out(bs)


@router.patch("/{bs_id}", response_model=BranchSessionOut)
def update_branch_session(bs_id: str, payload: BranchSessionPatch, db: Session = Depends(get_db),
                          _=Depends(require_role(SUPER_ADMIN))) -> BranchSessionOut:
    bs = db.get(ErpBranchSession, bs_id)
    if bs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch login not found")
    upd = payload.model_dump(exclude_unset=True)
    branch = upd.get("branch", bs.branch) or ""
    script_id = upd.get("login_script_id", bs.login_script_id)
    username = upd["username"] if "username" in upd else bs.username
    max_tabs = upd.get("max_tabs", bs.max_tabs) or bs.max_tabs
    script_ids = upd["script_ids"] if upd.get("script_ids") is not None else (bs.script_ids or [])
    _validate(db, bs.tenant_id, branch=branch, login_script_id=script_id, username=username,
              max_tabs=max_tabs, script_ids=script_ids, own_id=bs.id)
    bs.branch = branch.strip()
    bs.login_script_id = script_id
    bs.username = (username or "").strip() or None
    bs.max_tabs = max_tabs
    bs.script_ids = list(script_ids) or None
    if "password" in upd and upd["password"] is not None:
        bs.password_encrypted = encrypt_secret(upd["password"]) if upd["password"] else None
    if "enabled" in upd and upd["enabled"] is not None:
        bs.enabled = upd["enabled"]
    db.commit()
    db.refresh(bs)
    return _out(bs)


@router.delete("/{bs_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_branch_session(bs_id: str, db: Session = Depends(get_db),
                          _=Depends(require_role(SUPER_ADMIN))) -> None:
    bs = db.get(ErpBranchSession, bs_id)
    if bs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch login not found")
    db.delete(bs)
    db.commit()


# ---- per-user switch access ---------------------------------------------------------------
# A Super Admin ticks, per user (Tenants -> Users -> ERP Branch), which branch logins that user
# may turn on and off. The user then gets an "ERP Branch" button in the top bar with just those
# logins - for the times someone needs that ERP user by hand: turn the always-on login off, use
# the ERP, turn it back on. Turning it on/off is exactly the Super Admin's Turn on / Turn off.


class BranchSwitchOut(BaseModel):
    """What an assigned user sees of a branch login - never the username or password."""

    id: str
    branch: str
    enabled: bool
    status: str
    status_note: str | None
    live_since: datetime | None


class UserBranchAccessOut(BaseModel):
    user_id: str
    branch_session_ids: list[str]
    # Every branch login of the user's tenant, to tick from.
    branches: list[BranchSwitchOut]


class UserBranchAccessIn(BaseModel):
    branch_session_ids: list[str] = []


class BranchSwitchIn(BaseModel):
    enabled: bool


def _switch_out(bs: ErpBranchSession) -> BranchSwitchOut:
    return BranchSwitchOut(id=bs.id, branch=bs.branch, enabled=bs.enabled, status=bs.status,
                           status_note=bs.status_note, live_since=bs.live_since)


def _access_out(db: Session, user: User) -> UserBranchAccessOut:
    from app.models.erp_branch_session_user import ErpBranchSessionUser

    branches = (db.query(ErpBranchSession).filter(ErpBranchSession.tenant_id == user.tenant_id)
                .order_by(ErpBranchSession.branch).all()) if user.tenant_id else []
    ids = [r.branch_session_id for r in db.query(ErpBranchSessionUser)
           .filter(ErpBranchSessionUser.user_id == user.id).all()]
    return UserBranchAccessOut(user_id=user.id, branch_session_ids=ids,
                               branches=[_switch_out(b) for b in branches])


@router.get("/user-access/{user_id}", response_model=UserBranchAccessOut)
def get_user_branch_access(user_id: str, db: Session = Depends(get_db),
                           _=Depends(require_role(SUPER_ADMIN))) -> UserBranchAccessOut:
    """The branch logins of this user's tenant, and which of them the user may switch."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _access_out(db, user)


@router.put("/user-access/{user_id}", response_model=UserBranchAccessOut)
def set_user_branch_access(user_id: str, payload: UserBranchAccessIn, db: Session = Depends(get_db),
                           _=Depends(require_role(SUPER_ADMIN))) -> UserBranchAccessOut:
    """Replace the set of branch logins this user may turn on and off (empty = none)."""
    from app.models.erp_branch_session_user import ErpBranchSessionUser

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    wanted = set(payload.branch_session_ids or [])
    for bs_id in wanted:
        bs = db.get(ErpBranchSession, bs_id)
        if bs is None or bs.tenant_id != user.tenant_id:
            raise _bad("Every ticked branch login must belong to this user's own company.")
    rows = db.query(ErpBranchSessionUser).filter(ErpBranchSessionUser.user_id == user.id).all()
    have = {r.branch_session_id: r for r in rows}
    for bs_id, row in have.items():
        if bs_id not in wanted:
            db.delete(row)
    for bs_id in wanted - set(have):
        db.add(ErpBranchSessionUser(tenant_id=user.tenant_id, user_id=user.id, branch_session_id=bs_id))
    db.commit()
    return _access_out(db, user)


@router.get("/mine", response_model=list[BranchSwitchOut])
def my_branch_switches(db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)) -> list[BranchSwitchOut]:
    """The branch logins the signed-in user may turn on and off - drives the top-bar button
    (shown only when this is not empty)."""
    from app.models.erp_branch_session_user import ErpBranchSessionUser

    rows = (db.query(ErpBranchSession)
            .join(ErpBranchSessionUser, ErpBranchSessionUser.branch_session_id == ErpBranchSession.id)
            .filter(ErpBranchSessionUser.user_id == user.id)
            .order_by(ErpBranchSession.branch).all())
    return [_switch_out(b) for b in rows]


@router.post("/{bs_id}/switch", response_model=BranchSwitchOut)
def switch_branch_session(bs_id: str, payload: BranchSwitchIn, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)) -> BranchSwitchOut:
    """Turn one branch login on or off - a Super Admin, or a user it was assigned to. The
    supervisor (app/core/branch_sessions.py) stops or starts its keeper within seconds."""
    from app.models.erp_branch_session_user import ErpBranchSessionUser

    bs = db.get(ErpBranchSession, bs_id)
    if bs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch login not found")
    if user.role != SUPER_ADMIN:
        allowed = db.query(ErpBranchSessionUser).filter(
            ErpBranchSessionUser.user_id == user.id,
            ErpBranchSessionUser.branch_session_id == bs.id).first()
        if allowed is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="You are not allowed to turn this ERP branch login on or off.")
    if bs.enabled != payload.enabled:
        bs.enabled = payload.enabled
        db.commit()
        db.refresh(bs)
        logger.info("branch login %s (%s) turned %s by %s", bs.id, bs.branch,
                    "ON" if bs.enabled else "OFF", user.email)
    return _switch_out(bs)


@router.post("/{bs_id}/relogin", response_model=BranchSessionOut)
def relogin_branch_session(bs_id: str, db: Session = Depends(get_db),
                           _=Depends(require_role(SUPER_ADMIN))) -> BranchSessionOut:
    """Check the login now, and log in again if it has dropped."""
    from app.core.branch_sessions import request_relogin

    bs = db.get(ErpBranchSession, bs_id)
    if bs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch login not found")
    request_relogin(bs.id)
    return _out(bs)
