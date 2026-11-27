"""The Combined Description builder offers product-line fields only - never a container table
(standalone mark), a lookup keyed on one, or the hidden Product Description Language."""
import uuid

from app.api.v1.jobs import _available_composite_labels
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def test_only_product_line_fields_are_offered(db_session):
    t = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:5]}")
    g = TemplateGroup(tenant_id=t.id, name="Sea import", status="approved")
    db_session.add(g)
    db_session.flush()
    inv = TemplateDocument(tenant_id=t.id, group_id=g.id, name="Invoice", doc_type="INVOICE")
    bl = TemplateDocument(tenant_id=t.id, group_id=g.id, name="BL", doc_type="BL")
    db_session.add_all([inv, bl])
    db_session.flush()
    box = dict(tenant_id=t.id, x=0, y=0, width=1, height=1, is_multi_value=True)
    db_session.add_all([
        FieldMark(document_id=inv.id, label_name="product_description", **box),
        FieldMark(document_id=inv.id, label_name="item_material_code", **box),
        FieldMark(document_id=inv.id, label_name="Product Description Language", **box),
        FieldMark(document_id=bl.id, label_name="container_type", standalone_multi_value=True, **box),
        CustomField(tenant_id=t.id, group_id=g.id, label_name="ContainerTypeCode", kind="lookup", per_row=True,
                    lookup_key_label="container_type"),
        CustomField(tenant_id=t.id, group_id=g.id, label_name="CTH", kind="lookup", per_row=True,
                    lookup_key_label="Customer Part code"),
        CustomField(tenant_id=t.id, group_id=g.id, label_name="Combined Description", kind="composite", per_row=True),
    ])
    db_session.commit()
    got = _available_composite_labels(db_session, g.id, exclude_label="Combined Description")
    assert got == ["CTH", "item_material_code", "product_description"]
