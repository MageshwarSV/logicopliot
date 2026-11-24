"""Screen Designer layouts - the shape, the checks, and "create from the current screen".

A layout is placement only (see app.models.screen_layout.ScreenLayout):

    {"sections": [{"id": "s1", "title": "Invoice Details", "fields": ["m:<id>", "c:<id>"]}],
     "product_card": {"fields": ["m:<id>", ...],
                      "groups": [{"id": "g1", "title": "Scheme Details", "fields": ["m:<id>"]}]}}

"m:<id>" is a FieldMark on this document, "c:<id>" a job-level CustomField of the same
template. The functions here take plain objects (anything with the attributes used), so they
are tested without a database.
"""

from __future__ import annotations

import uuid
from datetime import datetime

MAX_TITLE = 100
OTHER_TITLE = "Other"
# Never shown as a box on the product card (see JobRunPage's visibleCells).
PRODUCT_CARD_SKIP = {"Product Description Language"}


def mark_ref(mark_id: str) -> str:
    return f"m:{mark_id}"


def custom_ref(custom_field_id: str) -> str:
    return f"c:{custom_field_id}"


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _order_key(obj) -> tuple:
    created = getattr(obj, "created_at", None) or datetime.min
    order = getattr(obj, "field_order", None)
    return (order if order is not None else float("inf"), created.timestamp() if hasattr(created, "timestamp") else 0)


def header_marks(marks) -> list:
    """Marks shown in the document's own sections: one value per document."""
    return [m for m in marks
            if not getattr(m, "is_multi_value", False) and not getattr(m, "standalone_multi_value", False)]


def product_marks(marks) -> list:
    """Marks shown on each product card: one value per line (not a standalone table)."""
    return [m for m in marks
            if getattr(m, "is_multi_value", False) and not getattr(m, "standalone_multi_value", False)
            and m.label_name not in PRODUCT_CARD_SKIP]


def screen_custom_fields(custom_fields, template_document_id: str) -> list:
    """Job-level custom fields this document's tab already shows (display_document_id)."""
    return [c for c in custom_fields
            if getattr(c, "display_document_id", None) == template_document_id
            and not getattr(c, "per_row", False) and not getattr(c, "hidden_from_capture", False)]


def allowed_refs(marks, custom_fields) -> tuple[set[str], set[str]]:
    """(refs allowed in sections, refs allowed on the product card) for one document.
    Any job-level custom field of the template may be placed in a section - placing it is
    exactly how an admin moves it onto this tab."""
    section = {mark_ref(m.id) for m in header_marks(marks)}
    section |= {custom_ref(c.id) for c in custom_fields
                if not getattr(c, "per_row", False) and not getattr(c, "hidden_from_capture", False)}
    product = {mark_ref(m.id) for m in product_marks(marks)}
    return section, product


def build_from_current(marks, custom_fields, template_document_id: str) -> dict:
    """The layout the job screen shows for this document today, so the Screen Designer starts
    from exactly what operators already see.

    Sections: each field's heading (standalone_group_heading), in the order the fields are
    listed (FieldMark.created_at - the order their values arrive in, which is how today's
    screen orders sections), fields inside a section by field_order. Fields with no heading go
    in a last "Other" section, as today. Product card: the line fields by field_order, with
    their own headings as groups."""
    head = sorted(header_marks(marks), key=lambda m: getattr(m, "created_at", None) or datetime.min)
    cfs = screen_custom_fields(custom_fields, template_document_id)

    sections: list[dict] = []
    by_title: dict[str, dict] = {}
    members: dict[str, list] = {}

    def place(obj, ref: str) -> None:
        title = (getattr(obj, "standalone_group_heading", None) or "").strip() or OTHER_TITLE
        if title not in by_title:
            by_title[title] = {"id": _new_id("s"), "title": title, "fields": []}
            members[title] = []
            sections.append(by_title[title])
        members[title].append((obj, ref))

    for m in head:
        place(m, mark_ref(m.id))
    for c in cfs:
        place(c, custom_ref(c.id))
    for title, section in by_title.items():
        section["fields"] = [ref for obj, ref in sorted(members[title], key=lambda p: _order_key(p[0]))]
    # "Other" always last, as on today's screen.
    sections.sort(key=lambda s: s["title"] == OTHER_TITLE)

    plain: list[str] = []
    groups: list[dict] = []
    group_by_title: dict[str, dict] = {}
    for m in sorted(product_marks(marks), key=_order_key):
        title = (getattr(m, "standalone_group_heading", None) or "").strip()
        if not title:
            plain.append(mark_ref(m.id))
            continue
        if title not in group_by_title:
            group_by_title[title] = {"id": _new_id("g"), "title": title, "fields": []}
            groups.append(group_by_title[title])
        group_by_title[title]["fields"].append(mark_ref(m.id))

    return {"sections": sections, "product_card": {"fields": plain, "groups": groups}}


class LayoutError(ValueError):
    """A layout that cannot be saved - the message says why, in words for the admin."""


def _title(value, what: str) -> str:
    title = str(value or "").strip()
    if not title:
        raise LayoutError(f"Every {what} needs a name.")
    if len(title) > MAX_TITLE:
        raise LayoutError(f"{what.capitalize()} name is too long (max {MAX_TITLE} characters): {title[:40]}...")
    return title


def clean_layout(layout, section_refs: set[str], product_refs: set[str]) -> tuple[dict, list[str]]:
    """Checks a layout sent by the Screen Designer and returns (clean layout, dropped refs).

    - sections and product-card groups need a non-empty name, unique within their list;
    - a field may appear only once in the whole layout;
    - a ref to a field that no longer exists (deleted in the wizard since) is dropped, not an
      error - otherwise one deleted field would block every later save - and reported back;
    - a ref of the wrong kind (a line field in a section, a document field on the product
      card) is an error: it would never show where the admin put it."""
    if not isinstance(layout, dict):
        raise LayoutError("Layout must be an object.")
    seen: set[str] = set()
    dropped: list[str] = []

    def take(refs, allowed: set[str], other: set[str], where: str) -> list[str]:
        out: list[str] = []
        for ref in refs or []:
            ref = str(ref)
            if ref in seen:
                raise LayoutError("A field is placed twice - each field can be in one place only.")
            if ref not in allowed:
                if ref in other:
                    raise LayoutError(
                        "A product-line field cannot go in a section, and a document field "
                        "cannot go on the product card." if where == "section" else
                        "A document field cannot go on the product card, and a product-line "
                        "field cannot go in a section.")
                dropped.append(ref)
                continue
            seen.add(ref)
            out.append(ref)
        return out

    sections_in = layout.get("sections") or []
    if not isinstance(sections_in, list):
        raise LayoutError("sections must be a list.")
    sections: list[dict] = []
    titles: set[str] = set()
    for s in sections_in:
        if not isinstance(s, dict):
            raise LayoutError("Each section must be an object.")
        title = _title(s.get("title"), "section")
        if title.lower() in titles:
            raise LayoutError(f"Two sections are both called '{title}'.")
        titles.add(title.lower())
        sections.append({"id": str(s.get("id") or _new_id("s")), "title": title,
                         "fields": take(s.get("fields"), section_refs, product_refs, "section")})

    card_in = layout.get("product_card") or {}
    if not isinstance(card_in, dict):
        raise LayoutError("product_card must be an object.")
    card_fields = take(card_in.get("fields"), product_refs, section_refs, "card")
    groups: list[dict] = []
    group_titles: set[str] = set()
    for g in card_in.get("groups") or []:
        if not isinstance(g, dict):
            raise LayoutError("Each product-card group must be an object.")
        title = _title(g.get("title"), "group")
        if title.lower() in group_titles:
            raise LayoutError(f"Two product-card groups are both called '{title}'.")
        group_titles.add(title.lower())
        groups.append({"id": str(g.get("id") or _new_id("g")), "title": title,
                       "fields": take(g.get("fields"), product_refs, section_refs, "card")})

    return {"sections": sections, "product_card": {"fields": card_fields, "groups": groups}}, dropped
