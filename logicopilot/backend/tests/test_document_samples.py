"""Learning what a customer's documents look like from where they put them.

The classifier's hardest problem is that nothing in the WORDING of shipping
paperwork separates one document from another: every document in a consignment
quotes the others, carries the same B/L number, vessel and weights. Measured on
693 real production documents, phrase matching identified 4 of them.

What does separate them is how one customer lays them out - and the customer
states that every time they fill a slot by hand. These tests cover capturing that
and handing it back.
"""
from __future__ import annotations

from tests.conftest import make_tenant

from app.core import document_samples
from app.core.document_samples import DOCUMENT_SAMPLE_CHARS, MAX_SAMPLES_PER_SLOT
from app.models.document_sample import DocumentSample

INVOICE = (
    "JIANGSU YUNYI ELECTRIC CO.,LTD\n"
    "ADD: NO.26 HUANGSHAN ROAD, XUZHOU CHINA\n"
    "COMMERCIAL INVOICE\n"
    "INVOICE NO.: ITW2690130\n"
    "UNIT PRICE    AMOUNT    TOTAL\n"
)


def _tenant(db):
    """One tenant per test. Samples are scoped to a tenant, so they need an owner."""
    if not hasattr(db, "_sample_tenant"):
        db._sample_tenant = make_tenant(db)
    return db._sample_tenant


def _slot(db, tenant_id, name="Invoice"):
    """A template document to hang samples off, built the way the app does."""
    from app.models.template_document import TemplateDocument
    from app.models.template_group import TemplateGroup

    group = TemplateGroup(tenant_id=tenant_id, name="Sea Import")
    db.add(group)
    db.flush()
    doc = TemplateDocument(tenant_id=tenant_id, group_id=group.id, name=name,
                           doc_type="Invoice", order_index=0)
    db.add(doc)
    db.flush()
    return doc


def test_a_manual_placement_is_remembered(db_session):
    """The operator chose the slot; that is a correct label, free of charge."""
    slot = _slot(db_session, _tenant(db_session).id)
    document_samples.remember(db_session, tenant_id=_tenant(db_session).id,
                              template_document_id=slot.id, text=INVOICE)
    db_session.flush()

    stored = db_session.query(DocumentSample).all()
    assert len(stored) == 1
    assert "COMMERCIAL INVOICE" in stored[0].excerpt
    assert stored[0].template_document_id == slot.id


def test_what_was_learned_comes_back_for_that_slot(db_session):
    slot = _slot(db_session, _tenant(db_session).id)
    document_samples.remember(db_session, tenant_id=_tenant(db_session).id,
                              template_document_id=slot.id, text=INVOICE)
    db_session.flush()

    served = document_samples.for_slots(db_session, [slot.id])
    assert slot.id in served
    assert "JIANGSU YUNYI" in served[slot.id]


def test_the_same_document_twice_teaches_nothing_new(db_session):
    """A duplicate would crowd out a genuinely different supplier's layout."""
    slot = _slot(db_session, _tenant(db_session).id)
    for _ in range(3):
        document_samples.remember(db_session, tenant_id=_tenant(db_session).id,
                                  template_document_id=slot.id, text=INVOICE)
    db_session.flush()

    assert db_session.query(DocumentSample).count() == 1


def test_only_the_newest_samples_are_kept(db_session):
    """A customer who changes forwarder should stop being described by the old one."""
    slot = _slot(db_session, _tenant(db_session).id)
    for i in range(MAX_SAMPLES_PER_SLOT + 4):
        document_samples.remember(
            db_session, tenant_id=_tenant(db_session).id, template_document_id=slot.id,
            text=f"SUPPLIER {i} LIMITED\nCOMMERCIAL INVOICE\nINVOICE NO.: X{i}\n"
                 f"UNIT PRICE AMOUNT TOTAL\nSOME MORE TEXT TO PASS THE MINIMUM\n")
        db_session.flush()

    assert db_session.query(DocumentSample).count() <= MAX_SAMPLES_PER_SLOT


def test_a_near_empty_scan_is_not_learned_from(db_session):
    """A failed OCR would push a real sample out of the window for nothing."""
    slot = _slot(db_session, _tenant(db_session).id)
    document_samples.remember(db_session, tenant_id=_tenant(db_session).id,
                              template_document_id=slot.id, text="INV\n")
    db_session.flush()

    assert db_session.query(DocumentSample).count() == 0


def test_the_excerpt_is_small_enough_to_put_in_a_prompt(db_session):
    """Several of these ride along on every classification."""
    slot = _slot(db_session, _tenant(db_session).id)
    document_samples.remember(
        db_session, tenant_id=_tenant(db_session).id, template_document_id=slot.id,
        text="COMMERCIAL INVOICE\n" + "\n".join(f"line {i} of shipment data" for i in range(400)))
    db_session.flush()

    stored = db_session.query(DocumentSample).one()
    assert len(stored.excerpt) <= DOCUMENT_SAMPLE_CHARS


def test_learning_never_breaks_the_upload_that_triggered_it(db_session):
    """The operator's file is already saved; failing to learn must stay invisible."""
    document_samples.remember(db_session, tenant_id=_tenant(db_session).id,
                              template_document_id="does-not-exist", text=INVOICE)
    # No exception. Whether the row survives a flush is the database's business.


def test_nothing_learned_yet_serves_nothing(db_session):
    """A fresh customer classifies exactly as before, with no samples to show."""
    slot = _slot(db_session, _tenant(db_session).id)
    assert document_samples.for_slots(db_session, [slot.id]) == {}
    assert document_samples.for_slots(db_session, []) == {}
