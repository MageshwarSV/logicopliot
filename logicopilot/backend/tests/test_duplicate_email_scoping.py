"""A held pre-alert is only shown to someone who would have been shown the JOB.

Found live on 2026-11-14: an Air Import operator, whose Jobs list correctly hid every Sea
Import job, opened Duplicate Emails and saw a Sea Import pre-alert - and the sidebar badge
counted it. The table had only ever been filtered by tenant, so every mode went to everyone.

The gate is `_visible_duplicates`, and it reads the same two sources the Jobs list reads
(`user.assigned_modes`, then UserTemplateAssignment) so the two screens cannot drift apart.
Hiding a row from the list is not enough on its own, so Delete and "Process as new job" are
tested against the same gate here: a row nobody can see must also be a row nobody can act on
by id.
"""
from app.models.duplicate_email import DuplicateEmail
from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.user_template import UserTemplateAssignment
from tests.conftest import login, make_tenant, make_user


def _tenant(db_session, name="Test Tenant"):
    """A tenant WITH an active tenant admin.

    An operator/GK2/manager cannot log in to a tenant that has no active Tenant Admin left
    (see auth.login) - the whole account is refused, by design. A tenant built without one
    therefore fails at the login line with a 401 that looks nothing like a scoping problem.
    """
    tenant = make_tenant(db_session, name=name)
    make_user(db_session, role="tenant_admin", tenant=tenant,
              email=f"admin-{tenant.id[:8]}@example.com")
    return tenant


def _group(db_session, tenant, *, name, mode):
    group = TemplateGroup(tenant_id=tenant.id, name=name, status="approved", mode=mode)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _job(db_session, tenant, group, *, reference, assigned_operator_id=None):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracted",
              assigned_operator_id=assigned_operator_id)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _duplicate(db_session, tenant, group, *, bl, message_id, original=None):
    row = DuplicateEmail(
        message_id=message_id, tenant_id=tenant.id, group_id=group.id,
        sender="cargora@4slogistics.com", subject=f"PRE ALERT BL NO : {bl}",
        bl_number=bl, status="pending",
        original_job_id=original.id if original else None,
        original_reference=original.reference if original else None,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _operator(db_session, tenant, *, email, modes=None):
    user = make_user(db_session, role="operator", tenant=tenant, email=email)
    user.assigned_modes = modes
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _two_modes(db_session):
    """One tenant, one Sea Import duplicate and one Air Import duplicate."""
    tenant = _tenant(db_session)
    sea = _group(db_session, tenant, name="Sea import", mode="Sea Import")
    air = _group(db_session, tenant, name="Air Import", mode="Air Import")
    sea_dup = _duplicate(db_session, tenant, sea, bl="1078279555", message_id="<sea@x>")
    air_dup = _duplicate(db_session, tenant, air, bl="6840355813", message_id="<air@x>")
    return tenant, sea, air, sea_dup, air_dup


def _bls(resp):
    assert resp.status_code == 200, resp.text
    return {r["bl_number"] for r in resp.json()}


# ---- the bug that was reported -------------------------------------------------------------

def test_air_import_operator_does_not_see_a_sea_import_duplicate(client, db_session):
    tenant, _sea, _air, _sea_dup, _air_dup = _two_modes(db_session)
    air_op = _operator(db_session, tenant, email="vinoth@example.com", modes=["Air Import"])
    login(client, air_op.email)

    assert _bls(client.get("/api/v1/duplicate-emails")) == {"6840355813"}


def test_sea_import_operator_still_sees_their_own(client, db_session):
    tenant, _sea, _air, _sea_dup, _air_dup = _two_modes(db_session)
    sea_op = _operator(db_session, tenant, email="importdocs@example.com",
                       modes=["Sea Import"])
    login(client, sea_op.email)

    assert _bls(client.get("/api/v1/duplicate-emails")) == {"1078279555"}


def test_an_operator_on_both_modes_sees_both(client, db_session):
    tenant, _sea, _air, _sea_dup, _air_dup = _two_modes(db_session)
    both = _operator(db_session, tenant, email="both@example.com",
                     modes=["Sea Import", "Air Import"])
    login(client, both.email)

    assert _bls(client.get("/api/v1/duplicate-emails")) == {"1078279555", "6840355813"}


# ---- hiding the row is not enough: it must not be actionable by id -------------------------

def test_air_import_operator_cannot_delete_a_sea_import_duplicate(client, db_session):
    tenant, _sea, _air, sea_dup, _air_dup = _two_modes(db_session)
    air_op = _operator(db_session, tenant, email="vinoth@example.com", modes=["Air Import"])
    login(client, air_op.email)

    resp = client.delete(f"/api/v1/duplicate-emails/{sea_dup.id}")
    assert resp.status_code == 404, resp.text
    db_session.refresh(sea_dup)
    assert sea_dup.status == "pending"


def test_air_import_operator_cannot_process_a_sea_import_duplicate(client, db_session):
    tenant, _sea, _air, sea_dup, _air_dup = _two_modes(db_session)
    air_op = _operator(db_session, tenant, email="vinoth@example.com", modes=["Air Import"])
    login(client, air_op.email)

    resp = client.post(f"/api/v1/duplicate-emails/{sea_dup.id}/process")
    # 404, not the 409 "the copy of this email is gone" that an ALLOWED row with no raw_path
    # would give - the row must be refused before its contents are ever considered.
    assert resp.status_code == 404, resp.text
    db_session.refresh(sea_dup)
    assert sea_dup.status == "pending"


def test_the_operator_who_owns_the_mode_can_still_delete_it(client, db_session):
    tenant, _sea, _air, sea_dup, _air_dup = _two_modes(db_session)
    sea_op = _operator(db_session, tenant, email="importdocs@example.com",
                       modes=["Sea Import"])
    login(client, sea_op.email)

    resp = client.delete(f"/api/v1/duplicate-emails/{sea_dup.id}")
    assert resp.status_code == 200, resp.text
    db_session.refresh(sea_dup)
    assert sea_dup.status == "deleted"


# ---- the other roles, each keeping the rule the Jobs list already gives them ---------------

def test_tenant_admin_sees_every_mode(client, db_session):
    tenant, _sea, _air, _sea_dup, _air_dup = _two_modes(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta@example.com")
    login(client, admin.email)

    assert _bls(client.get("/api/v1/duplicate-emails")) == {"1078279555", "6840355813"}


def test_an_operator_with_no_modes_but_explicit_templates_is_held_to_them(client, db_session):
    tenant, sea, _air, _sea_dup, _air_dup = _two_modes(db_session)
    op = _operator(db_session, tenant, email="templated@example.com", modes=None)
    db_session.add(UserTemplateAssignment(tenant_id=tenant.id, user_id=op.id, group_id=sea.id))
    db_session.commit()
    login(client, op.email)

    assert _bls(client.get("/api/v1/duplicate-emails")) == {"1078279555"}


def test_an_operator_with_neither_sees_the_whole_tenant(client, db_session):
    # Unchanged from before the gate existed, and deliberately: this is the same operator who
    # already sees every UNOWNED job, and a held mail is owned by nobody.
    tenant, _sea, _air, _sea_dup, _air_dup = _two_modes(db_session)
    op = _operator(db_session, tenant, email="unscoped@example.com", modes=None)
    login(client, op.email)

    assert _bls(client.get("/api/v1/duplicate-emails")) == {"1078279555", "6840355813"}


def test_gk2_with_no_modes_sees_nothing(client, db_session):
    # The opposite of the operator above, on purpose - assigned_modes is GK2's real access
    # gate, and none set means none, exactly as _jobs_visible_query already has it.
    tenant, _sea, _air, _sea_dup, _air_dup = _two_modes(db_session)
    gk2 = make_user(db_session, role="gk2", tenant=tenant, email="gk2none@example.com")
    gk2.assigned_modes = None
    db_session.add(gk2)
    db_session.commit()
    login(client, gk2.email)

    assert _bls(client.get("/api/v1/duplicate-emails")) == set()


def test_gk2_is_held_to_its_modes(client, db_session):
    tenant, _sea, _air, _sea_dup, _air_dup = _two_modes(db_session)
    gk2 = make_user(db_session, role="gk2", tenant=tenant, email="gk2sea@example.com")
    gk2.assigned_modes = ["Sea Import"]
    db_session.add(gk2)
    db_session.commit()
    login(client, gk2.email)

    assert _bls(client.get("/api/v1/duplicate-emails")) == {"1078279555"}


def test_gk2_restricted_to_one_operator_sees_only_that_operators_shipments(client, db_session):
    tenant = _tenant(db_session)
    sea = _group(db_session, tenant, name="Sea import", mode="Sea Import")
    mine = _operator(db_session, tenant, email="mine@example.com", modes=["Sea Import"])
    theirs = _operator(db_session, tenant, email="theirs@example.com", modes=["Sea Import"])
    _duplicate(db_session, tenant, sea, bl="AAA111", message_id="<a@x>",
               original=_job(db_session, tenant, sea, reference="JOB-A",
                             assigned_operator_id=mine.id))
    _duplicate(db_session, tenant, sea, bl="BBB222", message_id="<b@x>",
               original=_job(db_session, tenant, sea, reference="JOB-B",
                             assigned_operator_id=theirs.id))

    gk2 = make_user(db_session, role="gk2", tenant=tenant, email="gk2one@example.com")
    gk2.assigned_modes = ["Sea Import"]
    gk2.assigned_operator_ids = [mine.id]
    db_session.add(gk2)
    db_session.commit()
    login(client, gk2.email)

    assert _bls(client.get("/api/v1/duplicate-emails")) == {"AAA111"}


# ---- tenants, which were already separated and must stay that way --------------------------

def test_another_tenants_duplicate_is_never_visible(client, db_session):
    tenant, _sea, _air, _sea_dup, _air_dup = _two_modes(db_session)
    other = _tenant(db_session, name="Other Tenant")
    other_sea = _group(db_session, other, name="Sea import", mode="Sea Import")
    _duplicate(db_session, other, other_sea, bl="ZZZ999", message_id="<z@x>")

    sea_op = _operator(db_session, tenant, email="importdocs@example.com",
                       modes=["Sea Import"])
    login(client, sea_op.email)

    assert _bls(client.get("/api/v1/duplicate-emails")) == {"1078279555"}
