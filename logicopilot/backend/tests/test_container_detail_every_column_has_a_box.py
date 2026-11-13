"""Container Detail: a container number is never silently dropped, and never left without a box.

JOB-88572C: the Bill of Lading's "Container 1" card showed a container type and NO
container-number box at all. Each standalone mark is read in its own pass; the container
number's read was thrown away by the ISO 6346 check while the container type's was kept, so
the screen had nothing to type the number into and the ERP import went out with it blank.

Two guarantees pinned here:
- a container number written the way carriers print it ("FFAU 810832-9", or with the seal
  glued on) is cleaned up to its 11 characters and KEPT, rather than failing the check;
- whatever a column's read returns, every row some other column of the same table has gets
  a box for that column too - empty when nothing valid was read, so it can be filled in.
"""
from unittest.mock import patch

from app.api.v1.jobs import _is_valid_container_number, _normalize_container_number, run_extraction
from app.core.system_settings import set_extraction_engine
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def test_a_container_number_as_carriers_print_it_is_cleaned_up_and_passes():
    for printed in ("FFAU 810832-9", "FFAU-810832-9", "FFAU 8108329", "ffau8108329",
                    "FFAU8108329 SEAL SITA222875", "CONT.: FFAU 810832 9"):
        cleaned = _normalize_container_number(printed)
        assert cleaned == "FFAU8108329", printed
        assert _is_valid_container_number(cleaned)


def test_cleaning_up_never_turns_a_seal_or_a_longer_number_into_a_container():
    # A 3-letter seal, a 4-letter + 6-digit seal, the tail of a longer word, and the front of a
    # 14-character waybill number all stay invalid.
    for not_a_container in ("WHA3170485", "SITA222875", "XFFAU8108329", "OOLU2172231880", "", None):
        assert not _is_valid_container_number(_normalize_container_number(not_a_container)), \
            not_a_container


def _bl_job(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    bl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of lading", doc_type="BL")
    db_session.add(bl)
    db_session.commit()
    for label in ("container_number", "container_type"):
        db_session.add(FieldMark(tenant_id=tenant.id, document_id=bl.id, label_name=label,
                                 page_number=1, x=0.1, y=0.1, width=0.1, height=0.1,
                                 is_multi_value=True, standalone_multi_value=True,
                                 standalone_group_heading="Container"))
    db_session.commit()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-88572CLIKE", status="extracting")
    db_session.add(job)
    db_session.commit()
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=bl.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    return job


def _extract(db_session, job, container_number_read):
    """Each standalone mark is its own extract_document_rows call; answer per label."""
    reads = {
        "container_number": [{"container_number": container_number_read}],
        "container_type": [{"container_type": "40HC"}],
    }

    def rows_for(_text, specs, *_a, **_k):
        return reads[specs[0]["label"]]

    set_extraction_engine(db_session, "ocr_gpt4o_mini")
    ocr = "LOADED IN CONT.: FFAU 810832-9 SEAL SITA222875"
    with patch("app.api.v1.jobs.get_page_ocr",
               return_value={"layout_text": ocr, "text": ocr, "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", side_effect=rows_for):
        run_extraction(db_session, job)


def _cells(db_session, job):
    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.row_index.isnot(None))
            .order_by(JobFieldValue.row_index, JobFieldValue.label_name).all())
    return {(r.row_index, r.label_name): r.extracted_value for r in rows}


def test_a_spaced_and_hyphenated_container_number_is_kept_not_dropped(db_session):
    job = _bl_job(db_session)
    _extract(db_session, job, "FFAU 810832-9")
    assert _cells(db_session, job) == {
        (1, "container_number"): "FFAU8108329",
        (1, "container_type"): "40HC",
    }


def test_a_rejected_container_number_still_leaves_an_empty_box_to_fill(db_session):
    """The JOB-88572C shape: the number's read is unusable, the type's is fine. The row must
    still carry a container-number cell - empty - so the screen shows a box for it."""
    job = _bl_job(db_session)
    _extract(db_session, job, "SEAL SITA222875")
    cells = _cells(db_session, job)
    assert cells[(1, "container_type")] == "40HC"
    assert (1, "container_number") in cells, "no container-number box on the container's row"
    assert cells[(1, "container_number")] is None
