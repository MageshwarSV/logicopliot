"""The Manager mobile app's read-only API.

Most of this file is about two properties, because getting either wrong means one customer
seeing another customer's shipments:

  TENANT   an app login sees its own company and nothing else
  MODE     assigned_modes is a hard server-side limit; the phone's own multi-select can only
           narrow it, never widen it

The rest check that the numbers agree with the portal's Jobs list (same "completed"
definition, same dates) and that the app genuinely cannot write anything.
"""
from datetime import date, datetime, timedelta, timezone

from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.user import APP_MANAGER, OPERATOR
from tests.conftest import DEFAULT_PASSWORD, make_tenant, make_user

IST = 330  # minutes east of UTC, the app's default


def _group(db_session, tenant, mode: str) -> TemplateGroup:
    group = TemplateGroup(tenant_id=tenant.id, name=f"grp-{mode}", mode=mode)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _completed_job(db_session, tenant, group, *, when: datetime, reference="JOB", owner=None):
    """A job that the portal's Jobs list would show under "completed", finished at `when`."""
    job = Job(
        tenant_id=tenant.id, group_id=group.id, reference=reference, status="completed",
        assigned_operator_id=owner.id if owner else None,
    )
    db_session.add(job)
    db_session.commit()
    # The listener stamps completed_at with "now"; these tests need specific days, so the
    # value is set explicitly afterwards - the same thing the migration's backfill does.
    job.completed_at = when
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _app_login(db_session, tenant, modes: list[str], email="app@example.com"):
    user = make_user(db_session, role=APP_MANAGER, tenant=tenant, email=email)
    user.assigned_modes = modes
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _login(client, email: str):
    """Logs in the way the phone does: asking for tokens, then using the Bearer header."""
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": DEFAULT_PASSWORD, "token_response": True},
    )
    assert response.status_code == 200, response.text
    client.cookies.clear()  # a phone has no cookie jar
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


# --------------------------------------------------------------------------- access control


def test_an_operator_cannot_reach_the_app_api(client, db_session):
    """The app's routes are for the app's own role. An operator's perfectly valid session
    must not open them."""
    tenant = make_tenant(db_session)
    make_user(db_session, role=OPERATOR, tenant=tenant, email="op@example.com")
    headers = _login(client, "op@example.com")

    for path in ("/me", "/dashboard", "/trend", "/usage", "/reports"):
        response = client.get(f"/api/v1/manager-app{path}", headers=headers)
        assert response.status_code == 403, f"{path} let an operator in"


def test_an_app_login_with_no_modes_is_refused_with_a_useful_message(client, db_session):
    """Empty assigned_modes means "nothing", like gk2. Better to say so than to show a
    manager an empty dashboard they will read as "we did no work today"."""
    tenant = make_tenant(db_session)
    _app_login(db_session, tenant, modes=[])
    headers = _login(client, "app@example.com")

    response = client.get("/api/v1/manager-app/dashboard", headers=headers)
    assert response.status_code == 403
    assert "shipment modes" in response.json()["detail"]


def test_me_offers_only_the_modes_this_login_is_allowed(client, db_session):
    tenant = make_tenant(db_session)
    _app_login(db_session, tenant, modes=["Sea Import", "Air Import"])
    headers = _login(client, "app@example.com")

    body = client.get("/api/v1/manager-app/me", headers=headers).json()
    assert body["allowed_modes"] == ["Air Import", "Sea Import"]
    assert body["tenant_id"] == tenant.id


# --------------------------------------------------------------------------- mode isolation


def test_a_sea_import_login_never_sees_air_import(client, db_session):
    tenant = make_tenant(db_session)
    sea = _group(db_session, tenant, "Sea Import")
    air = _group(db_session, tenant, "Air Import")
    today = datetime.now(timezone.utc)
    _completed_job(db_session, tenant, sea, when=today, reference="SEA-1")
    _completed_job(db_session, tenant, air, when=today, reference="AIR-1")
    _completed_job(db_session, tenant, air, when=today, reference="AIR-2")

    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    body = client.get("/api/v1/manager-app/dashboard", headers=headers).json()
    assert body["completed"] == 1
    assert [m["mode"] for m in body["by_mode"]] == ["Sea Import"]


def test_asking_for_a_mode_it_is_not_allowed_cannot_widen_what_it_sees(client, db_session):
    """The attack this exists to stop: the phone sends ?modes=Air Import on a Sea Import
    login. The intersection must win."""
    tenant = make_tenant(db_session)
    sea = _group(db_session, tenant, "Sea Import")
    air = _group(db_session, tenant, "Air Import")
    today = datetime.now(timezone.utc)
    _completed_job(db_session, tenant, sea, when=today, reference="SEA-1")
    _completed_job(db_session, tenant, air, when=today, reference="AIR-1")

    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    refused = client.get(
        "/api/v1/manager-app/dashboard", headers=headers, params={"modes": "Air Import"}
    )
    assert refused.status_code == 400

    # Asking for both returns only the permitted one, rather than erroring - a phone whose
    # saved preference is now partly disallowed keeps working on what it still may see.
    both = client.get(
        "/api/v1/manager-app/dashboard", headers=headers,
        params={"modes": "Sea Import,Air Import"},
    ).json()
    assert both["modes"] == ["Sea Import"]
    assert both["completed"] == 1


def test_the_phones_multi_select_narrows_within_what_is_allowed(client, db_session):
    tenant = make_tenant(db_session)
    sea = _group(db_session, tenant, "Sea Import")
    air = _group(db_session, tenant, "Air Import")
    today = datetime.now(timezone.utc)
    _completed_job(db_session, tenant, sea, when=today, reference="SEA-1")
    _completed_job(db_session, tenant, air, when=today, reference="AIR-1")

    _app_login(db_session, tenant, modes=["Sea Import", "Air Import"])
    headers = _login(client, "app@example.com")

    everything = client.get("/api/v1/manager-app/dashboard", headers=headers).json()
    assert everything["completed"] == 2

    narrowed = client.get(
        "/api/v1/manager-app/dashboard", headers=headers, params={"modes": "Air Import"}
    ).json()
    assert narrowed["completed"] == 1
    assert narrowed["modes"] == ["Air Import"]


# -------------------------------------------------------------------------- tenant isolation


def test_an_app_login_never_sees_another_companys_jobs(client, db_session):
    """The one that matters most. Two tenants, same mode, same day."""
    mine = make_tenant(db_session, name="Mine")
    theirs = make_tenant(db_session, name="Theirs")
    today = datetime.now(timezone.utc)
    _completed_job(db_session, mine, _group(db_session, mine, "Sea Import"),
                   when=today, reference="MINE-1")
    for n in range(5):
        _completed_job(db_session, theirs, _group(db_session, theirs, "Sea Import"),
                       when=today, reference=f"THEIRS-{n}")

    _app_login(db_session, mine, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    body = client.get("/api/v1/manager-app/dashboard", headers=headers).json()
    assert body["completed"] == 1

    report = client.get("/api/v1/manager-app/reports", headers=headers).json()
    assert report["total"] == 1
    assert [r["reference"] for r in report["rows"]] == ["MINE-1"]


# ------------------------------------------------------------------------------- the numbers


def test_an_unfinished_job_is_not_counted(client, db_session):
    """"Completed" is the portal's own definition, imported rather than re-implemented."""
    tenant = make_tenant(db_session)
    group = _group(db_session, tenant, "Sea Import")
    _completed_job(db_session, tenant, group, when=datetime.now(timezone.utc), reference="DONE")

    unfinished = Job(tenant_id=tenant.id, group_id=group.id, reference="WIP", status="extracted")
    db_session.add(unfinished)
    db_session.commit()

    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    assert client.get("/api/v1/manager-app/dashboard", headers=headers).json()["completed"] == 1


def test_jobs_are_counted_on_the_day_they_finished_not_the_day_they_started(client, db_session):
    """The whole reason completed_at exists."""
    tenant = make_tenant(db_session)
    group = _group(db_session, tenant, "Sea Import")
    # Finished at 09:00 IST on the 15th = 03:30 UTC on the 15th.
    finished = datetime(2026, 10, 15, 3, 30, tzinfo=timezone.utc)
    job = _completed_job(db_session, tenant, group, when=finished, reference="LATE")
    job.created_at = datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc)
    db_session.add(job)
    db_session.commit()

    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    on_finish_day = client.get(
        "/api/v1/manager-app/dashboard", headers=headers,
        params={"date": "2026-10-15", "tz_offset": IST},
    ).json()
    assert on_finish_day["completed"] == 1

    on_create_day = client.get(
        "/api/v1/manager-app/dashboard", headers=headers,
        params={"date": "2026-10-12", "tz_offset": IST},
    ).json()
    assert on_create_day["completed"] == 0


def test_a_job_finished_after_midnight_local_lands_on_the_local_day(client, db_session):
    """02:00 IST on the 15th is 20:30 UTC on the 14th. Counting in UTC would file it under
    the 14th - a whole day wrong, on the screen whose entire job is which day."""
    tenant = make_tenant(db_session)
    group = _group(db_session, tenant, "Sea Import")
    _completed_job(db_session, tenant, group,
                   when=datetime(2026, 10, 14, 20, 30, tzinfo=timezone.utc), reference="NIGHT")

    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    fifteenth = client.get(
        "/api/v1/manager-app/dashboard", headers=headers,
        params={"date": "2026-10-15", "tz_offset": IST},
    ).json()
    assert fifteenth["completed"] == 1

    fourteenth = client.get(
        "/api/v1/manager-app/dashboard", headers=headers,
        params={"date": "2026-10-14", "tz_offset": IST},
    ).json()
    assert fourteenth["completed"] == 0


def test_the_dashboard_compares_against_the_previous_day(client, db_session):
    tenant = make_tenant(db_session)
    group = _group(db_session, tenant, "Sea Import")
    for n in range(4):
        _completed_job(db_session, tenant, group,
                       when=datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc), reference=f"T{n}")
    for n in range(2):
        _completed_job(db_session, tenant, group,
                       when=datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc), reference=f"Y{n}")

    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    body = client.get(
        "/api/v1/manager-app/dashboard", headers=headers,
        params={"date": "2026-10-15", "tz_offset": IST},
    ).json()
    assert body["completed"] == 4
    assert body["previous_completed"] == 2
    assert body["change_pct"] == 100.0


def test_change_is_null_rather_than_misleading_when_there_is_nothing_to_compare(client, db_session):
    """"Up 100%" from zero, and "down 100%" to zero, are both things people quote in
    meetings. None lets the app say "no comparison" instead."""
    tenant = make_tenant(db_session)
    group = _group(db_session, tenant, "Sea Import")
    _completed_job(db_session, tenant, group,
                   when=datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc), reference="ONLY")

    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    body = client.get(
        "/api/v1/manager-app/dashboard", headers=headers,
        params={"date": "2026-10-15", "tz_offset": IST},
    ).json()
    assert body["previous_completed"] == 0
    assert body["change_pct"] is None


def test_a_mode_with_no_jobs_is_still_listed_as_zero(client, db_session):
    """A missing row reads as a bug; an explicit zero reads as a quiet day."""
    tenant = make_tenant(db_session)
    sea = _group(db_session, tenant, "Sea Import")
    _group(db_session, tenant, "Air Import")
    _completed_job(db_session, tenant, sea, when=datetime.now(timezone.utc), reference="S1")

    _app_login(db_session, tenant, modes=["Sea Import", "Air Import"])
    headers = _login(client, "app@example.com")

    by_mode = {m["mode"]: m["completed"]
               for m in client.get("/api/v1/manager-app/dashboard", headers=headers).json()["by_mode"]}
    assert by_mode == {"Air Import": 0, "Sea Import": 1}


def test_top_users_ranks_by_who_the_job_is_assigned_to(client, db_session):
    tenant = make_tenant(db_session)
    group = _group(db_session, tenant, "Sea Import")
    busy = make_user(db_session, role=OPERATOR, tenant=tenant, email="busy@example.com")
    quiet = make_user(db_session, role=OPERATOR, tenant=tenant, email="quiet@example.com")
    now = datetime.now(timezone.utc)
    for n in range(3):
        _completed_job(db_session, tenant, group, when=now, reference=f"B{n}", owner=busy)
    _completed_job(db_session, tenant, group, when=now, reference="Q0", owner=quiet)

    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    top = client.get("/api/v1/manager-app/dashboard", headers=headers).json()["top_users"]
    assert [u["completed"] for u in top] == [3, 1]
    assert top[0]["name"] == busy.email or top[0]["name"] == busy.full_name


def test_an_unassigned_job_is_still_counted_under_one_named_row(client, db_session):
    """The per-user rows must add up to the total above them, or the screen contradicts
    itself."""
    tenant = make_tenant(db_session)
    group = _group(db_session, tenant, "Sea Import")
    owner = make_user(db_session, role=OPERATOR, tenant=tenant, email="someone@example.com")
    now = datetime.now(timezone.utc)
    _completed_job(db_session, tenant, group, when=now, reference="A", owner=owner)
    _completed_job(db_session, tenant, group, when=now, reference="B", owner=None)

    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    body = client.get("/api/v1/manager-app/dashboard", headers=headers).json()
    assert body["completed"] == 2
    assert sum(u["completed"] for u in body["top_users"]) == 2
    assert "Unassigned" in [u["name"] for u in body["top_users"]]


# ------------------------------------------------------------------------------------ trend


def test_the_trend_returns_a_point_for_every_day_including_empty_ones(client, db_session):
    tenant = make_tenant(db_session)
    group = _group(db_session, tenant, "Sea Import")
    _completed_job(db_session, tenant, group,
                   when=datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc), reference="X")

    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    body = client.get(
        "/api/v1/manager-app/trend", headers=headers,
        params={"days": 7, "end": "2026-10-15", "tz_offset": IST},
    ).json()
    assert len(body["points"]) == 7
    assert body["points"][-1]["date"] == "2026-10-15"
    assert body["points"][-1]["completed"] == 1
    assert body["total"] == 1
    assert all(p["completed"] == 0 for p in body["points"][:-1])


# ------------------------------------------------------------------------------------ usage


def test_usage_compares_against_an_equal_length_period_before_it(client, db_session):
    """Not "last month" - comparing a 3-day window against a 31-day one produces a number
    that is simply wrong, and it is the kind that gets repeated."""
    tenant = make_tenant(db_session)
    group = _group(db_session, tenant, "Sea Import")
    for day, count in ((13, 1), (14, 1), (15, 4)):
        for n in range(count):
            _completed_job(db_session, tenant, group,
                           when=datetime(2026, 10, day, 6, 0, tzinfo=timezone.utc),
                           reference=f"D{day}-{n}")

    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    body = client.get(
        "/api/v1/manager-app/usage", headers=headers,
        params={"start": "2026-10-15", "end": "2026-10-15", "tz_offset": IST},
    ).json()
    assert body["completed"] == 4          # the 15th
    assert body["previous_completed"] == 1  # the 14th, one day, not the whole month


def test_usage_rejects_a_backwards_date_range(client, db_session):
    tenant = make_tenant(db_session)
    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    response = client.get(
        "/api/v1/manager-app/usage", headers=headers,
        params={"start": "2026-10-15", "end": "2026-10-01"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------------- reports


def test_reports_list_finished_jobs_newest_first(client, db_session):
    tenant = make_tenant(db_session)
    group = _group(db_session, tenant, "Sea Import")
    for day in (13, 15, 14):
        _completed_job(db_session, tenant, group,
                       when=datetime(2026, 10, day, 6, 0, tzinfo=timezone.utc),
                       reference=f"JOB-{day}")

    _app_login(db_session, tenant, modes=["Sea Import"])
    headers = _login(client, "app@example.com")

    body = client.get(
        "/api/v1/manager-app/reports", headers=headers,
        params={"start": "2026-10-01", "end": "2026-10-31", "tz_offset": IST},
    ).json()
    assert body["total"] == 3
    assert [r["reference"] for r in body["rows"]] == ["JOB-15", "JOB-14", "JOB-13"]


# -------------------------------------------------------------------------------- read-only


def test_the_app_api_exposes_no_way_to_write_anything(client, db_session):
    """Structural, not behavioural: every route on this router is a GET. A manager watches;
    they never act. If a POST is ever added here, this fails and whoever added it has to say
    why out loud."""
    from app.api.v1.manager_app import router

    offenders = [
        (sorted(r.methods), r.path)
        for r in router.routes
        if set(getattr(r, "methods", set())) - {"GET", "HEAD", "OPTIONS"}
    ]
    assert offenders == [], f"the Manager app API must be read-only, found: {offenders}"
