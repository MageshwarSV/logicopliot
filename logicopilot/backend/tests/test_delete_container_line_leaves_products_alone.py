"""Deleting a CONTAINER must not delete, or reshuffle, any product's CTH.

The real one, from production on 2026-11-10:

    13:13:33  job JOB-8BA341: deleted product line 2 from <Bill of lading> (10 lines left)

A Bill of Lading's container rows are numbered 1, 2, 3 exactly as an invoice's product lines
are, and they count containers, not products. The delete took every per-row custom field at the
same row number along with the container - so removing container 2 deleted PRODUCT 2's CTH, and
then renumbered the rest to close the gap, sliding products 3..11's codes up one and onto the
wrong products.

What was reported was the small half: product 11 lost its CTH box. The damage underneath was
nine products carrying someone else's tariff heading, with nothing on screen to say so.

The union IS right on a document that carries product lines - a line's CTH carries no
job_document_id and would otherwise be orphaned. It is only wrong on a document whose rows are
its own standalone table.
"""
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _setup(db_session, products=4, containers=2):
    """One invoice carrying `products` product lines, one bill of lading carrying
    `containers` container rows, and a CTH per product line."""
    tenant = make_tenant(db_session)
    # An operator can only log in while their tenant has an ACTIVE tenant admin.
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta-del@example.com")
    operator = make_user(db_session, role="operator", tenant=tenant, email="op-del@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", mode="Sea Import")
    db_session.add(group)
    db_session.commit()

    invoice_t = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                                 doc_type="Invoice")
    bl_t = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of lading",
                            doc_type="Bill of lading")
    db_session.add_all([invoice_t, bl_t])
    db_session.commit()
    # container_type is its own table on the BL - that is what makes it standalone.
    db_session.add(FieldMark(tenant_id=tenant.id, document_id=bl_t.id,
                             label_name="container_type", standalone_multi_value=True,
                             x=0, y=0, width=1, height=1))
    db_session.add(FieldMark(tenant_id=tenant.id, document_id=invoice_t.id,
                             label_name="product_description",
                             x=0, y=0, width=1, height=1))
    cth = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="CTH",
                      kind="lookup", per_row=True)
    db_session.add(cth)
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-CONTAINERS",
              status="extracted", created_by_id=operator.id, assigned_operator_id=operator.id)
    db_session.add(job)
    db_session.commit()
    invoice = JobDocument(tenant_id=tenant.id, job_id=job.id, set_index=1,
                          template_document_id=invoice_t.id, file_path="/tmp/inv.pdf",
                          page_count=1, approved=True)
    bl = JobDocument(tenant_id=tenant.id, job_id=job.id, set_index=1,
                     template_document_id=bl_t.id, file_path="/tmp/bl.pdf",
                     page_count=1, approved=True)
    db_session.add_all([invoice, bl])
    db_session.commit()

    for n in range(1, products + 1):
        db_session.add(JobFieldValue(
            tenant_id=tenant.id, job_id=job.id, job_document_id=invoice.id, row_index=n,
            set_index=1, label_name="product_description", extracted_value=f"WIDGET {n}",
        ))
        # The CTH carries NO job_document_id - only (set_index, row_index).
        db_session.add(JobFieldValue(
            tenant_id=tenant.id, job_id=job.id, custom_field_id=cth.id, row_index=n,
            set_index=1, label_name="CTH", extracted_value=f"8807302{n}",
        ))
    for n in range(1, containers + 1):
        db_session.add(JobFieldValue(
            tenant_id=tenant.id, job_id=job.id, job_document_id=bl.id, row_index=n,
            set_index=1, label_name="container_type", extracted_value=f"40 HC #{n}",
        ))
    db_session.commit()
    return tenant, operator, job, invoice, bl, cth


def _cth(db_session, job, cth):
    return {
        fv.row_index: fv.extracted_value
        for fv in db_session.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cth.id)
    }


def test_deleting_a_container_leaves_every_products_cth_untouched(client, db_session):
    """The production bug. Four products, two containers; delete container 2."""
    tenant, operator, job, invoice, bl, cth = _setup(db_session)
    login(client, operator.email)
    before = _cth(db_session, job, cth)

    r = client.delete(f"/api/v1/jobs/{job.id}/documents/{bl.id}/rows/2")
    assert r.status_code == 200, r.text
    db_session.expire_all()

    assert _cth(db_session, job, cth) == before, "a container delete moved a product's CTH"
    assert sorted(before) == [1, 2, 3, 4]


def test_the_container_itself_is_still_deleted(client, db_session):
    tenant, operator, job, invoice, bl, cth = _setup(db_session)
    login(client, operator.email)

    client.delete(f"/api/v1/jobs/{job.id}/documents/{bl.id}/rows/2")
    db_session.expire_all()
    left = sorted(
        fv.row_index for fv in db_session.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id, JobFieldValue.job_document_id == bl.id)
    )
    assert left == [1]


def test_deleting_a_real_product_line_still_takes_its_cth(client, db_session):
    """The behaviour the union exists for, unchanged: a CTH carries no job_document_id, so
    scoping a product-line delete by document alone would orphan it - the line would vanish
    from the screen while still demanding a customs code."""
    tenant, operator, job, invoice, bl, cth = _setup(db_session)
    login(client, operator.email)

    r = client.delete(f"/api/v1/jobs/{job.id}/documents/{invoice.id}/rows/2")
    assert r.status_code == 200, r.text
    db_session.expire_all()

    # Row 2 gone, and 3 and 4 renumbered down to close the gap.
    assert _cth(db_session, job, cth) == {1: "88073021", 2: "88073023", 3: "88073024"}
