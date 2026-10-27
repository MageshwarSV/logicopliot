"""Responses for the native Manager app (/api/v1/manager-app/*).

Separate from app/schemas/jobs.py on purpose: the app shows counts, not jobs, and it must
not start depending on the shape of an operator's job row. Every model here is read-only -
there is no Create/Update anywhere in this file, and that is deliberate (see manager_app.py).
"""
from datetime import date as Date, datetime

from pydantic import BaseModel


class AppIdentity(BaseModel):
    """What the phone needs to draw its own mode picker and name the login."""

    user_id: str
    email: str
    full_name: str | None = None
    tenant_id: str
    company_name: str | None = None
    # The modes this login is ALLOWED to see - the server's limit, not the phone's choice.
    # The picker offers exactly these and nothing else.
    allowed_modes: list[str]


class ModeCount(BaseModel):
    mode: str
    completed: int
    # Same mode, the previous comparable period. None when there is no previous period to
    # compare against rather than 0, so the app can say "no comparison" instead of "-100%".
    previous: int | None = None
    change_pct: float | None = None


class UserCount(BaseModel):
    user_id: str | None = None
    name: str
    completed: int
    # Share of the period's total, 0-100. Handy for the bar behind each row.
    share_pct: float = 0.0


class DashboardOut(BaseModel):
    """One day's picture, for the app's home screen."""

    date: Date
    completed: int
    previous_date: Date
    previous_completed: int
    change_pct: float | None = None
    by_mode: list[ModeCount]
    top_users: list[UserCount]
    # Every mode this response covers - what the phone asked for, intersected with what it is
    # allowed. Echoed back so the app can show what it is actually looking at rather than
    # assuming its own filter was honoured.
    modes: list[str]


class TrendPoint(BaseModel):
    # The local day this bucket covers (see tz_offset on the endpoints).
    date: Date
    completed: int


class TrendOut(BaseModel):
    points: list[TrendPoint]
    total: int
    modes: list[str]


class UsageOut(BaseModel):
    """The Usage screen: who completed what over a period."""

    start: Date
    end: Date
    completed: int
    previous_completed: int
    change_pct: float | None = None
    users: list[UserCount]
    by_mode: list[ModeCount]
    modes: list[str]


class ReportRow(BaseModel):
    job_id: str
    reference: str
    mode: str | None = None
    customer_name: str | None = None
    operator_name: str | None = None
    completed_at: datetime


class ReportOut(BaseModel):
    start: Date
    end: Date
    total: int
    rows: list[ReportRow]
    modes: list[str]
