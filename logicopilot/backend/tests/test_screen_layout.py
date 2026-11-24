"""Screen Designer layouts: built from the current screen, and checked before saving."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.screen_layout import (
    LayoutError, allowed_refs, build_from_current, clean_layout, custom_ref, mark_ref,
)

T0 = datetime(2026, 11, 24, 10, 0, 0)
DOC = "doc-inv"


def mark(mid, label, i, heading=None, order=None, multi=False, standalone=False):
    return SimpleNamespace(id=mid, label_name=label, created_at=T0 + timedelta(seconds=i),
                           standalone_group_heading=heading, field_order=order,
                           is_multi_value=multi, standalone_multi_value=standalone)


def cf(cid, label, heading=None, order=None, doc=DOC, per_row=False, hidden=False):
    return SimpleNamespace(id=cid, label_name=label, standalone_group_heading=heading,
                           field_order=order, display_document_id=doc, per_row=per_row,
                           hidden_from_capture=hidden, created_at=T0)


MARKS = [
    mark("inv", "invoice number", 0, "Invoice Details", 1),
    mark("exn", "EXPORTER NAME", 1, "Exporter Details", 1),
    mark("dat", "invoice date", 2, "Invoice Details", 2),
    mark("exa", "EXPORTER ADDRESS", 3, "Exporter Details", 2),
    mark("tot", "loose field", 4),
    mark("desc", "description", 5, None, 1, multi=True),
    mark("lang", "Product Description Language", 6, None, None, multi=True),
    mark("sch", "Scheme Code", 7, "Scheme Details", 9, multi=True),
    mark("dbk", "DBK_Sr_No", 8, "Scheme Details", 10, multi=True),
    mark("ctr", "container_number", 9, "Container", None, multi=True, standalone=True),
]
CFS = [
    cf("chc", "Customs House Code", "Exporter Details", 3),
    cf("note", "NOTE", doc=None),                 # not on this tab
    cf("combo", "Combined", per_row=True),        # per line - never in a section
    cf("hid", "Hidden", hidden=True),
]


def test_build_from_current_matches_todays_screen():
    layout = build_from_current(MARKS, CFS, DOC)
    titles = [s["title"] for s in layout["sections"]]
    assert titles == ["Invoice Details", "Exporter Details", "Other"]       # first seen; Other last
    inv, exp, other = layout["sections"]
    assert inv["fields"] == [mark_ref("inv"), mark_ref("dat")]               # by field_order
    assert exp["fields"] == [mark_ref("exn"), mark_ref("exa"), custom_ref("chc")]
    assert other["fields"] == [mark_ref("tot")]
    card = layout["product_card"]
    assert card["fields"] == [mark_ref("desc")]                              # language box skipped
    assert [g["title"] for g in card["groups"]] == ["Scheme Details"]
    assert card["groups"][0]["fields"] == [mark_ref("sch"), mark_ref("dbk")]
    # a standalone table (containers) is not a product field
    assert mark_ref("ctr") not in str(layout)


def test_built_layout_passes_its_own_check():
    layout = build_from_current(MARKS, CFS, DOC)
    section_refs, product_refs = allowed_refs(MARKS, CFS)
    clean, dropped = clean_layout(layout, section_refs, product_refs)
    assert dropped == []
    assert clean["sections"][0]["fields"] == layout["sections"][0]["fields"]


def test_any_job_level_field_may_be_placed_but_not_per_row_or_hidden():
    section_refs, product_refs = allowed_refs(MARKS, CFS)
    assert custom_ref("note") in section_refs          # moving it onto this tab is allowed
    assert custom_ref("combo") not in section_refs
    assert custom_ref("hid") not in section_refs
    assert mark_ref("desc") in product_refs and mark_ref("desc") not in section_refs


def test_deleted_field_is_dropped_not_an_error():
    section_refs, product_refs = allowed_refs(MARKS, CFS)
    clean, dropped = clean_layout(
        {"sections": [{"title": "A", "fields": [mark_ref("inv"), "m:gone"]}]},
        section_refs, product_refs)
    assert clean["sections"][0]["fields"] == [mark_ref("inv")]
    assert dropped == ["m:gone"]


@pytest.mark.parametrize("layout, message", [
    ({"sections": [{"title": "A", "fields": [mark_ref("inv")]},
                   {"title": "B", "fields": [mark_ref("inv")]}]}, "placed twice"),
    ({"sections": [{"title": "A", "fields": []}, {"title": "a", "fields": []}]}, "both called"),
    ({"sections": [{"title": "  ", "fields": []}]}, "needs a name"),
    ({"sections": [{"title": "A", "fields": [mark_ref("desc")]}]}, "product-line field"),
    ({"product_card": {"fields": [mark_ref("inv")]}}, "document field"),
])
def test_bad_layouts_are_refused(layout, message):
    section_refs, product_refs = allowed_refs(MARKS, CFS)
    with pytest.raises(LayoutError) as exc:
        clean_layout(layout, section_refs, product_refs)
    assert message in str(exc.value)
