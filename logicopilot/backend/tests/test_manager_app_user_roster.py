"""The Usage screen's "By user" table: everyone, not only the people who finished something.

The table used to be built from one query - completed jobs, grouped by owner - so a person
appeared only if they had a completion in the window. An operator who spent the period on
jobs that are still open simply was not in the list, and neither was a quiet one, which
reads as "this user does not exist" rather than "this user finished nothing".

Two things are being held here:

  WHO IS LISTED   the roster of operators who could work the chosen shipment types, plus
                  anyone who actually owns a job in the window even if they are not on it
  THE TWO NUMBERS `jobs` (put in during the window) beside `completed` (finished during the
                  window) - different flows, so neither is a subset of the other

and, still, that `completed` goes on summing to the figure at the top of the screen, which
is the portal's own definition and the one number the app may never disagree about.
"""
from datetime import datetime, timedelta, timezone

from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.user import APP_MANAGER, OPERATOR
from app.models.user_template import UserTemplateAssignment
from tests.conftest import DEFAULT_PASSWORD, make_tenant, make_user

IST = 330


def _group(db_session, tenant, mode: str) -> TemplateGroup:
    group = TemplateGroup(tenant_id=tenant.id, name=f"grp-{mode}", mode=mode)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _job(db_session, tenant, group, *, reference, owner=None, completed_at=None,
         created_at=None, status="draft"):
    job = Job(
        tenant_id=tenant.id, group_id=group.id, reference=reference,
        status="completed" if completed_at else status,
        assigned_operator_id=owner.id if owner else None,
    )
    db_session.add(job)
    db_session.commit()
    if completed_at:
        job.completed_at = completed_at
    if created_at:
        job.created_at = created_at
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _app_login(db_session, tenant, modes, email="app@example.com"):
    user = make_user(db_session, role=APP_MANAGER, tenant=tenant, email=email)
    user.assigned_modes = modes
    db_session.add(user)
    db_session.commit()
    return user


def _login(client, email):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": DEFAULT_PASSWORD, "token_response": True},
    )
    assert response.status_code == 200, response.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


def _usage(client, headers, **params):
    query = {"tz_offset": IST, **params}
    response = client.get("/api/v1/manager-app/usage", headers=headers, params=query)
    assert response.status_code == 200, response.text
    return response.json()


def _by_name(body):
    return {u["name"]: u for u in body["users"]}


# --------------------------------------------------------------------------- who is listed

def test_an_operator_who_finished_nothing_is_still_listed(client, db_session):
    """The whole point. Quiet is a fact worth seeing, not a reason to be left out."""
    tenant = make_tenant(db_session)
    sea = _group(db_session, tenant, "Sea Import")
    busy = make_user(db_session, role=OPERATOR, tenant=tenant, email="busy@example.com",
                     full_name="Busy Operator")
    make_user(db_session, role=OPERATOR, tenant=tenant, email="quiet@example.com",
              full_name="Quiet Operator")
    _app_login(db_session, tenant, ["Sea Import"])

    now = datetime.now(timezone.utc)
    _job(db_session, tenant, sea, reference="J1", owner=busy, completed_at=now - timedelta(days=1))

    headers = _login(client, "app@example.com")
    users = _by_name(_usage(client, headers))

    assert "Quiet Operator" in users
    assert users["Quiet Operator"]["completed"] == 0
    assert users["Quiet Operator"]["jobs"] == 0
    assert users["Busy Operator"]["completed"] == 1


def test_the_roster_follows_the_shipment_type_filter(client, db_session):
    """Asking for Sea Import lists Sea Import's people. An operator assigned only to Air
    Import's templates is not one of them."""
    tenant = make_tenant(db_session)
    sea = _group(db_session, tenant, "Sea Import")
    air = _group(db_session, tenant, "Air Import")
    sea_op = make_user(db_session, role=OPERATOR, tenant=tenant, email="sea@example.com",
                       full_name="Sea Person")
    air_op = make_user(db_session, role=OPERATOR, tenant=tenant, email="air@example.com",
                       full_name="Air Person")
    db_session.add_all([
        UserTemplateAssignment(tenant_id=tenant.id, user_id=sea_op.id, group_id=sea.id),
        UserTemplateAssignment(tenant_id=tenant.id, user_id=air_op.id, group_id=air.id),
    ])
    db_session.commit()
    _app_login(db_session, tenant, ["Sea Import", "Air Import"])
    headers = _login(client, "app@example.com")

    sea_only = _by_name(_usage(client, headers, modes="Sea Import"))
    assert "Sea Person" in sea_only
    assert "Air Person" not in sea_only

    air_only = _by_name(_usage(client, headers, modes="Air Import"))
    assert "Air Person" in air_only
    assert "Sea Person" not in air_only

    both = _by_name(_usage(client, headers))
    assert {"Sea Person", "Air Person"} <= set(both)


def test_an_operator_with_no_template_assignments_belongs_to_every_mode(client, db_session):
    """UserTemplateAssignment's own rule: no rows means every template in the tenant. An
    operator nobody has restricted must not vanish from every filter instead of appearing
    in all of them."""
    tenant = make_tenant(db_session)
    _group(db_session, tenant, "Sea Import")
    make_user(db_session, role=OPERATOR, tenant=tenant, email="free@example.com",
              full_name="Unrestricted Operator")
    _app_login(db_session, tenant, ["Sea Import", "Air Import"])
    headers = _login(client, "app@example.com")

    assert "Unrestricted Operator" in _by_name(_usage(client, headers, modes="Sea Import"))
    assert "Unrestricted Operator" in _by_name(_usage(client, headers, modes="Air Import"))


def test_a_deactivated_operator_is_off_the_roster_but_keeps_the_work_they_did(client, db_session):
    """Someone who has left should not pad the list - but dropping their completions would
    make the column stop adding up to the total, which is the one thing it may never do."""
    tenant = make_tenant(db_session)
    sea = _group(db_session, tenant, "Sea Import")
    gone = make_user(db_session, role=OPERATOR, tenant=tenant, email="gone@example.com",
                     full_name="Departed Operator")
    _app_login(db_session, tenant, ["Sea Import"])
    now = datetime.now(timezone.utc)
    _job(db_session, tenant, sea, reference="J1", owner=gone, completed_at=now - timedelta(days=1))
    gone.is_active = False
    db_session.add(gone)
    db_session.commit()

    headers = _login(client, "app@example.com")
    body = _usage(client, headers)
    users = _by_name(body)

    assert users["Departed Operator"]["completed"] == 1
    assert sum(u["completed"] for u in body["users"]) == body["completed"]


def test_another_tenants_operators_are_never_on_the_roster(client, db_session):
    """The roster is a new query over the users table, so it gets the same tenant check
    every other query in this file has."""
    tenant = make_tenant(db_session)
    other = make_tenant(db_session, name="Other Co")
    _group(db_session, tenant, "Sea Import")
    make_user(db_session, role=OPERATOR, tenant=other, email="theirs@example.com",
              full_name="Their Operator")
    _app_login(db_session, tenant, ["Sea Import"])

    headers = _login(client, "app@example.com")
    assert "Their Operator" not in _by_name(_usage(client, headers))


# ------------------------------------------------------------------------- the two numbers

def test_jobs_counts_what_was_put_in_and_completed_what_was_finished(client, db_session):
    """Intake and output, side by side."""
    tenant = make_tenant(db_session)
    sea = _group(db_session, tenant, "Sea Import")
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op@example.com",
                   full_name="An Operator")
    _app_login(db_session, tenant, ["Sea Import"])

    now = datetime.now(timezone.utc)
    # Three put in this period; one of them finished.
    _job(db_session, tenant, sea, reference="J1", owner=op, created_at=now - timedelta(days=2),
         completed_at=now - timedelta(days=1))
    _job(db_session, tenant, sea, reference="J2", owner=op, created_at=now - timedelta(days=2))
    _job(db_session, tenant, sea, reference="J3", owner=op, created_at=now - timedelta(days=2))

    headers = _login(client, "app@example.com")
    # An explicit window: the default is "this month", so a job created two days ago lands
    # outside it whenever this runs on the 1st or 2nd, and the test would pass or fail by
    # the calendar rather than by the code.
    row = _by_name(_usage(client, headers, start="2020-01-01"))["An Operator"]
    assert row["jobs"] == 3
    assert row["completed"] == 1


def test_completed_may_exceed_jobs_when_a_backlog_is_cleared(client, db_session):
    """Deliberate, and the reason the two are not nested: a job created before the window and
    finished inside it counts as output here and as intake in the period it arrived."""
    tenant = make_tenant(db_session)
    sea = _group(db_session, tenant, "Sea Import")
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op@example.com",
                   full_name="An Operator")
    _app_login(db_session, tenant, ["Sea Import"])

    now = datetime.now(timezone.utc)
    _job(db_session, tenant, sea, reference="OLD", owner=op,
         created_at=now - timedelta(days=400), completed_at=now - timedelta(days=1))

    headers = _login(client, "app@example.com")
    # From the 1st of last month: wide enough to hold the completion, nowhere near wide
    # enough to reach the job's creation 400 days ago.
    body = _usage(client, headers, start=str((datetime.now(timezone.utc).date()
                                              - timedelta(days=40)).replace(day=1)))
    row = _by_name(body)["An Operator"]
    assert row["completed"] == 1
    assert row["jobs"] == 0
    # And the headline is still the portal's definition, unmoved by any of this.
    assert body["completed"] == 1


def test_the_completed_column_still_adds_up_to_the_total(client, db_session):
    """The invariant the old table had, kept through the rewrite - including the ownerless
    jobs that have no person to file them under."""
    tenant = make_tenant(db_session)
    sea = _group(db_session, tenant, "Sea Import")
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op@example.com",
                   full_name="An Operator")
    _app_login(db_session, tenant, ["Sea Import"])

    now = datetime.now(timezone.utc)
    _job(db_session, tenant, sea, reference="J1", owner=op, completed_at=now - timedelta(days=1))
    _job(db_session, tenant, sea, reference="J2", owner=None, completed_at=now - timedelta(days=1))

    headers = _login(client, "app@example.com")
    body = _usage(client, headers)

    assert body["completed"] == 2
    assert sum(u["completed"] for u in body["users"]) == 2
    assert "Unassigned" in _by_name(body)


def test_the_busiest_person_is_first_and_the_idle_ones_are_last(client, db_session):
    tenant = make_tenant(db_session)
    sea = _group(db_session, tenant, "Sea Import")
    top = make_user(db_session, role=OPERATOR, tenant=tenant, email="top@example.com",
                    full_name="Top Operator")
    mid = make_user(db_session, role=OPERATOR, tenant=tenant, email="mid@example.com",
                    full_name="Mid Operator")
    make_user(db_session, role=OPERATOR, tenant=tenant, email="idle@example.com",
              full_name="Idle Operator")
    _app_login(db_session, tenant, ["Sea Import"])

    now = datetime.now(timezone.utc)
    for i in range(2):
        _job(db_session, tenant, sea, reference=f"T{i}", owner=top,
             completed_at=now - timedelta(days=1))
    _job(db_session, tenant, sea, reference="M0", owner=mid, completed_at=now - timedelta(days=1))

    headers = _login(client, "app@example.com")
    names = [u["name"] for u in _usage(client, headers)["users"]]
    assert names[0] == "Top Operator"
    assert names[1] == "Mid Operator"
    assert names[-1] == "Idle Operator"


def test_the_dashboards_top_users_still_shows_only_people_who_finished_something(client, db_session):
    """The roster belongs on the Usage screen. A home-screen "top 3" padded with names that
    completed nothing would say the opposite of what it means."""
    tenant = make_tenant(db_session)
    sea = _group(db_session, tenant, "Sea Import")
    busy = make_user(db_session, role=OPERATOR, tenant=tenant, email="busy@example.com",
                     full_name="Busy Operator")
    make_user(db_session, role=OPERATOR, tenant=tenant, email="quiet@example.com",
              full_name="Quiet Operator")
    _app_login(db_session, tenant, ["Sea Import"])

    now = datetime.now(timezone.utc)
    _job(db_session, tenant, sea, reference="J1", owner=busy, completed_at=now)

    headers = _login(client, "app@example.com")
    response = client.get("/api/v1/manager-app/dashboard", headers=headers,
                          params={"tz_offset": IST})
    assert response.status_code == 200, response.text
    names = [u["name"] for u in response.json()["top_users"]]
    assert "Quiet Operator" not in names
