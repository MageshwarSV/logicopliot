"""Deleting a page, or removing a whole document, puts what it took onto the Unclassified pile
instead of destroying it - so the operator can drag it straight back.

Deleting used to be the one irreversible action on that screen: the rendered image went with
the document directory, and the only way back was re-uploading the whole file and redoing every
placement. An operator cutting a page is usually fairly sure, not certain - a scan they think
is a duplicate, a page they think belongs to another shipment.

The part that needs the most care is what must NOT be parked. This same endpoint is the second
half of every MOVE and MERGE (the frontend copies the page elsewhere first, then calls delete
on the source), so parking by default would quietly duplicate every moved page onto the pile.
Parking is opt-in for exactly that reason, and the tests below pin both halves.
"""
import fitz

from app.models.job import Job, JobDocument, UnmatchedUploadPage
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _pdf_bytes(n_pages: int) -> bytes:
    doc = fitz.open()
    for i in range(n_pages):
        doc.new_page().insert_text((72, 72), f"page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _setup(db_session, tmp_path, pages: int):
    """A job with one document holding `pages` real, rendered pages."""
    from app.api.v1.jobs import _job_doc_dir, _render_pages

    tenant = make_tenant(db_session)
    operator = make_user(db_session, role="operator", tenant=tenant, email="op-park@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", status="ready")
    db_session.add(group)
    db_session.flush()
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                            doc_type="Invoice", order_index=0)
    db_session.add(tdoc)
    db_session.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-PARK", status="extracted",
              created_by_id=operator.id, assigned_operator_id=operator.id)
    db_session.add(job)
    db_session.flush()
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     original_name="Commercial Invoice.pdf", page_count=0)
    db_session.add(jd)
    db_session.commit()

    ddir = _job_doc_dir(jd.id)
    ddir.mkdir(parents=True, exist_ok=True)
    src = ddir / "original.pdf"
    src.write_bytes(_pdf_bytes(pages))
    jd.file_path = str(src)
    jd.page_count = _render_pages(src, ddir / "pages")
    db_session.commit()
    assert jd.page_count == pages
    return tenant, operator, job, jd


def _parked(db_session, job):
    return (
        db_session.query(UnmatchedUploadPage)
        .filter(UnmatchedUploadPage.job_id == job.id)
        .all()
    )


def test_deleting_a_page_parks_it_on_the_pile(client, db_session, tmp_path):
    tenant, operator, job, jd = _setup(db_session, tmp_path, pages=3)
    login(client, operator.email)

    resp = client.delete(
        f"/api/v1/jobs/{job.id}/documents/{jd.id}/pages/2?park=true"
    )
    assert resp.status_code == 200, resp.text

    rows = _parked(db_session, job)
    assert len(rows) == 1
    assert rows[0].page_number == 2
    assert rows[0].original_filename == "Commercial Invoice.pdf"


def test_the_parked_image_really_exists_on_disk(client, db_session, tmp_path):
    """A row pointing at a file that is not there would show as a broken thumbnail nobody can
    resolve - worse than losing the page outright, because it looks recoverable."""
    from pathlib import Path

    tenant, operator, job, jd = _setup(db_session, tmp_path, pages=2)
    login(client, operator.email)
    client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/pages/1?park=true")

    rows = _parked(db_session, job)
    assert len(rows) == 1
    assert Path(rows[0].image_path).exists()


def test_the_image_survives_the_document_directory_being_rebuilt(client, db_session, tmp_path):
    """The parked copy must not point INTO the document's own pages/ directory, which this very
    request wipes and re-renders - that is why it is copied, not referenced."""
    from pathlib import Path

    from app.api.v1.jobs import _job_doc_dir

    tenant, operator, job, jd = _setup(db_session, tmp_path, pages=3)
    login(client, operator.email)
    client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/pages/3?park=true")

    parked = Path(_parked(db_session, job)[0].image_path)
    assert parked.exists()
    assert _job_doc_dir(jd.id) not in parked.parents


def test_deleting_the_only_page_parks_it_even_though_the_whole_document_goes(
    client, db_session, tmp_path,
):
    """A one-page document taken down to zero pages is removed outright, directory and all -
    the page has to be copied off before that happens, not after."""
    from pathlib import Path

    tenant, operator, job, jd = _setup(db_session, tmp_path, pages=1)
    login(client, operator.email)

    resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/pages/1?park=true")
    assert resp.status_code == 200, resp.text

    rows = _parked(db_session, job)
    assert len(rows) == 1
    assert Path(rows[0].image_path).exists()


def test_removing_a_whole_document_parks_every_page(client, db_session, tmp_path):
    tenant, operator, job, jd = _setup(db_session, tmp_path, pages=3)
    login(client, operator.email)

    resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/file?park=true")
    assert resp.status_code == 200, resp.text

    rows = _parked(db_session, job)
    assert sorted(r.page_number for r in rows) == [1, 2, 3]


# ---- what must NOT be parked ---------------------------------------------------------------

def test_a_move_does_not_park_anything(client, db_session, tmp_path):
    """The frontend copies the page to its new slot and THEN calls this on the source. Parking
    here would leave a duplicate of a page that is alive and well somewhere else."""
    tenant, operator, job, jd = _setup(db_session, tmp_path, pages=3)
    login(client, operator.email)

    resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/pages/2")
    assert resp.status_code == 200, resp.text
    assert _parked(db_session, job) == []


def test_removing_a_whole_document_without_asking_does_not_park(client, db_session, tmp_path):
    """Same reasoning for a whole-document move/merge, and for a reupload - its replacement is
    already on the way, so the superseded copy would just be clutter."""
    tenant, operator, job, jd = _setup(db_session, tmp_path, pages=2)
    login(client, operator.email)

    resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/file")
    assert resp.status_code == 200, resp.text
    assert _parked(db_session, job) == []


def test_a_parked_page_can_be_dragged_back_onto_a_slot(client, db_session, tmp_path):
    """The whole point. The parked image has to be the shape assign_unclassified_page rebuilds
    a real JobDocument from, or 'you can always drag it back' is not true."""
    tenant, operator, job, jd = _setup(db_session, tmp_path, pages=2)
    login(client, operator.email)
    client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/pages/1?park=true")

    page_id = _parked(db_session, job)[0].id
    tdoc_id = jd.template_document_id
    resp = client.post(
        f"/api/v1/jobs/{job.id}/unclassified-pages/{page_id}/assign",
        json={"template_document_id": tdoc_id},
    )
    assert resp.status_code == 200, resp.text
    # It left the pile, and came back as a real document on the job.
    assert _parked(db_session, job) == []
    placed = [d for d in resp.json()["documents"] if d.get("file_path") or d.get("page_count")]
    assert placed, resp.text
